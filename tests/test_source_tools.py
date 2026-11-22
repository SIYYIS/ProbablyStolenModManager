"""Source integration tests. All local paths are temporary, Steam/downloads mocked."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs
from unittest.mock import Mock, patch

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from core.paths import APP_ID, GamePaths
from core.third_party import SourceError, SourceStore, latest_github_release, source_link
from core.workshop_collections import CollectionEntry, CollectionError, parse_collection_id, resolve_collection
from webui.server import LoopbackGuard
from webui.source_routes import source_router


class SteamCollectionTests(unittest.TestCase):
    def test_parse_canonical_inputs(self):
        self.assertEqual(parse_collection_id(' 000123 '), '123')
        self.assertEqual(parse_collection_id('https://steamcommunity.com/sharedfiles/filedetails/?id=123&l=english'), '123')
        for source in ('https://steamcommunity.com.evil.test/sharedfiles/filedetails/?id=123',
                       'https://public@steamcommunity.com/sharedfiles/filedetails/?id=123',
                       'https://steamcommunity.com/sharedfiles/filedetails/?id=123&id=456',
                       'https://steamcommunity.com/browse/?id=123', '0', '18446744073709551616'):
            with self.subTest(source=source), self.assertRaises(CollectionError):
                parse_collection_id(source)

    def test_post_nested_sorted_dedup_filtered(self):
        def open_(req, timeout):
            self.assertEqual(req.host, 'api.steampowered.com')
            self.assertEqual(req.get_method(), 'POST')
            arguments = parse_qs(req.data.decode())
            ids = [v[0] for k, v in arguments.items() if k.startswith('publishedfileids[')]
            if 'GetCollectionDetails' in req.full_url:
                self.assertEqual(arguments['collectioncount'], [str(len(ids))])
                sequence = {'1': [('2', 0, 5), ('3', 2, 1), ('5', 0, 6), ('6', 0, 7), ('7', 12, 8)],
                            '3': [('1', 2, 2), ('2', 0, 3), ('4', 0, 1)]}
                rows = [{'result': 1, 'publishedfileid': i, 'children': [
                    {'publishedfileid': c, 'filetype': t, 'sortorder': n} for c, t, n in sequence[i]]} for i in ids]
                key = 'collectiondetails'
            else:
                self.assertEqual(arguments['itemcount'], [str(len(ids))])
                key = 'publishedfiledetails'
                rows = [{'result': 9 if i == '6' else 1, 'publishedfileid': i, 'title': f'mod {i}',
                         'creator_app_id': 766, 'consumer_app_id': '440' if i == '5' else int(APP_ID)} for i in ids]
            return io.BytesIO(json.dumps({'response': {key: rows}}).encode())
        with patch('core.workshop_collections.urllib.request.build_opener', return_value=SimpleNamespace(open=open_)):
            id_, title, rows = resolve_collection('1')
        self.assertEqual((id_, title), ('1', 'mod 1'))
        self.assertEqual([(row.id, row.state) for row in rows],
                         [('4', 'verified'), ('2', 'verified'), ('5', 'wrong_game'), ('6', 'unavailable'), ('7', 'unsupported')])

    def test_wrong_root_and_non_collection_rejected(self):
        with patch('core.workshop_collections._details', return_value={'1': {'result': 1, 'consumer_app_id': 440}}), \
             patch('core.workshop_collections._steam_rows') as collection:
            with self.assertRaises(CollectionError):
                resolve_collection('1')
            collection.assert_not_called()
        with patch('core.workshop_collections._details', return_value={'1': {'result': 1, 'consumer_app_id': APP_ID}}), \
             patch('core.workshop_collections._steam_rows', return_value={'1': {'result': 9}}):
            with self.assertRaises(CollectionError) as problem:
                resolve_collection('1')
            self.assertEqual(problem.exception.status, 404)


class SourceCardTests(unittest.TestCase):
    def test_allowlisted_canonical_sources(self):
        self.assertEqual(source_link('https://github.com/Owner/Repository/releases/latest?x=secret').url,
                         'https://github.com/owner/repository')
        self.assertEqual(source_link('https://www.nexusmods.com/somegame/mods/21?tab=files').platform, 'nexus')
        for text in ('http://github.com/owner/repo', 'https://github.com.evil.test/owner/repo',
                     'https://github.com@evil.test/owner/repo', 'https://github.com:443/owner/repo',
                     'https://github.com/owner/..', 'https://github.com/owner/%2e%2e', 'file:///tmp/test'):
            with self.subTest(text=text), self.assertRaises(SourceError):
                source_link(text)

    def test_latest_fixed_host_ignores_asset_and_redirect_urls(self):
        def open_(req, timeout):
            self.assertEqual(req.full_url, 'https://api.github.com/repos/owner/repo/releases/latest')
            self.assertIsNone(req.get_header('Authorization'))
            return io.BytesIO(b'{"draft":false,"prerelease":false,"tag_name":"v2","published_at":"2026-02-01T12:00:00Z","html_url":"https://untrusted.test"}')
        with patch('core.third_party.urllib.request.build_opener', return_value=SimpleNamespace(open=open_)) as opener:
            self.assertEqual(latest_github_release(source_link('https://github.com/owner/repo')),
                             {'tag': 'v2', 'published_at': '2026-02-01T12:00:00Z'})
            self.assertIsNone(opener.call_args.args[0].redirect_request(None, None, 302, None, {}, 'https://untrusted.test'))

    def test_missing_release_and_nexus_no_fetch(self):
        mocked = Mock(side_effect=HTTPError('https://api.github.com', 404, 'Not Found', {}, None))
        with patch('core.third_party.urllib.request.build_opener', return_value=SimpleNamespace(open=mocked)):
            self.assertIsNone(latest_github_release(source_link('https://github.com/owner/repo')))
            with self.assertRaises(SourceError):
                latest_github_release(source_link('https://www.nexusmods.com/game/mods/21'))
        self.assertEqual(mocked.call_count, 1)

    def test_persistence_and_stale_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            row = {'name': '测试模组', 'url': 'https://github.com/owner/repo'}
            self.assertEqual(store.read(), [])
            saved = store.write([row], [])
            self.assertEqual(SourceStore(Path(tmp)).read(), saved)
            with self.assertRaises(SourceError):
                store.write([], [])
            self.assertEqual(store.read(), saved)
            self.assertEqual(store.write([], [row]), [])


class GuardedSourceRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.mods, self.workshop = root / 'game' / 'Mods', root / 'workshop'
        self.mods.mkdir(parents=True)
        self.workshop.mkdir()
        self.paths = GamePaths(game_dir=self.mods.parent, local_mods_dir=self.mods, workshop_dir=self.workshop)
        self.settings = SimpleNamespace(steam_username='', downloader_path='')
        self.ops = SimpleNamespace(paths=lambda _: self.paths, settings=lambda: self.settings,
                                   data_dir=root / 'manager', download=Mock(side_effect=self.fake_download))
        app = FastAPI()
        app.add_middleware(LoopbackGuard)
        workspace = APIRouter(prefix='/api')
        workspace.include_router(source_router(self.ops))
        app.include_router(workspace)
        self.client = TestClient(app, base_url='http://127.0.0.1', headers={'X-PSMM-Request': '1'})
        self.addCleanup(self.client.close)
        runner = patch('webui.source_routes.is_game_running', return_value=False)
        runner.start()
        self.addCleanup(runner.stop)

    @staticmethod
    def mod(folder):
        folder.mkdir()
        (folder / 'manifest.xml').write_text('<Mod><ID>fixture</ID></Mod>', encoding='utf-8')

    def fake_download(self, paths, settings, item_id, confirm_login):
        self.assertNotEqual(paths.local_mods_dir, self.mods)
        self.mod(paths.local_mods_dir / f'ws_{item_id}')
        return {'message': 'staged'}

    def preview(self):
        rows = [CollectionEntry(str(i), f'mod {i}', 'wrong_game' if i == 5 else 'verified') for i in range(1, 6)]
        with patch('webui.source_routes.resolve_collection', return_value=('99', 'fixture', rows)):
            response = self.client.post('/api/workshop/collections/preview', json={'collection': '99'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def download(self, preview, id_='1', **changes):
        body = {'preview_token': preview['preview_token'], 'id': id_, 'confirm': True, **changes}
        return self.client.post('/api/workshop/collections/download', json=body)

    def test_no_download_before_confirmation_and_preserves_existing(self):
        self.mod(self.workshop / '2')
        self.mod(self.mods / 'ws_3')
        (self.mods / 'ws_4').mkdir()
        preview = self.preview()
        self.assertEqual([m['state'] for m in preview['members']], ['missing', 'installed', 'installed', 'blocked', 'wrong_game'])
        self.assertEqual(self.download(preview, confirm=False).status_code, 422)
        self.assertEqual(self.download(preview, confirm='true').status_code, 422)
        self.assertEqual(self.download(preview, id_='5').status_code, 422)
        self.assertEqual(self.download(preview, id_='3').status_code, 422)
        self.ops.download.assert_not_called()
        self.assertEqual(self.download(preview).json()['status'], 'installed')
        self.assertEqual(self.download(preview).json()['status'], 'skipped')
        self.ops.download.assert_called_once()
        self.assertTrue((self.mods / 'ws_1' / 'manifest.xml').exists())
        self.assertFalse(list(self.mods.glob('.psmm-collection-*')))

    def test_new_arrival_during_download_is_not_overwritten(self):
        def arrive(*args):
            self.fake_download(*args)
            self.mod(self.mods / 'ws_1')
            (self.mods / 'ws_1' / 'personal.txt').write_text('keep', encoding='utf-8')
        self.ops.download.side_effect = arrive
        self.assertEqual(self.download(self.preview()).json()['status'], 'skipped')
        self.assertEqual((self.mods / 'ws_1' / 'personal.txt').read_text(), 'keep')
        self.assertFalse(list(self.mods.glob('.psmm-collection-*')))

    def test_stale_path_and_login_gate(self):
        self.settings.steam_username = 'fake-user'
        preview = self.preview()
        self.assertTrue(preview['require_login'])
        self.assertEqual(self.download(preview).status_code, 409)
        self.settings.downloader_path = 'changed.exe'
        self.assertEqual(self.download(preview, confirm_login=True).status_code, 409)
        self.ops.download.assert_not_called()

    def test_sources_guard_and_disk_round_trip(self):
        bare = TestClient(self.client.app, base_url='http://127.0.0.1')
        with bare:
            self.assertEqual(bare.get('/api/sources').status_code, 403)
        row = {'name': '来源一', 'url': 'https://github.com/owner/repo'}
        response = self.client.put('/api/sources', json={'links': [row], 'expected': []})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['links'][0]['origin'], 'user')
        self.assertEqual(self.client.get('/api/sources').json(), response.json())
        self.assertEqual(self.client.put('/api/sources', json={'links': [], 'expected': []}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
