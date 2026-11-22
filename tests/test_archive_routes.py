import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from core.paths import GamePaths
from webui.archive_routes import archive_router
from webui.operations import Operations
from webui.server import LoopbackGuard


class ArchiveEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mods = self.root / 'Mods'
        self.workshop = self.root / 'Workshop'
        self.workshop.mkdir()
        self.save = self.root / 'SaveFile.es3'
        self.save.write_bytes(b'fixture save untouched')
        self.data_dir = self.root / 'manager'
        self.ops = Operations(self.data_dir)
        self.ops.paths = lambda *_: GamePaths(local_mods_dir=self.mods, workshop_dir=self.workshop, save_file=self.save)
        app = FastAPI()
        app.add_middleware(LoopbackGuard)

        async def local():
            with self.ops.local():
                yield

        workspace = APIRouter(prefix='/api', dependencies=[Depends(local)])
        workspace.include_router(archive_router(self.ops))
        app.include_router(workspace)
        self.client = TestClient(app, base_url='http://127.0.0.1', headers={'X-PSMM-Request': '1'})
        self.addCleanup(self.client.close)
        proc = patch('core.archive_install.psutil.process_iter', return_value=[])
        proc.start()
        self.addCleanup(proc.stop)

    def post(self, path, body, expected=200):
        response = self.client.post('/api/archive/' + path, json=body)
        self.assertEqual(response.status_code, expected, response.text if response.status_code != expected else '')
        return response

    def zip(self, files):
        stream = io.BytesIO()
        with ZipFile(stream, 'w', ZIP_STORED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return stream.getvalue()

    def start(self, buf, name='pack.zip'):
        return self.post('start', {'filename': name, 'size': len(buf)}).json()

    def upload(self, buf, name='pack.zip', preview_status=200):
        job = self.start(buf, name)
        for offset in range(0, len(buf), job['chunk_size']):
            data = base64.b64encode(buf[offset:offset + job['chunk_size']]).decode()
            self.post('chunk', {'job': job['job'], 'offset': offset, 'data': data})
        return self.post('preview', {'job': job['job']}, preview_status).json()

    def confirm(self, preview, expected=200):
        return self.post('confirm', {'job': preview['job'], 'token': preview['token']}, expected)

    def test_guard_lock_and_chunk_order(self):
        no_guard = TestClient(self.client.app, base_url='http://127.0.0.1')
        self.assertEqual(no_guard.post('/api/archive/start', json={'filename': 'pack.zip', 'size': 4}).status_code, 403)
        self.assertEqual(self.client.post('/api/archive/start', headers={'Origin': 'https://example.invalid'},
                                         json={'filename': 'pack.zip', 'size': 4}).status_code, 403)
        with self.ops.local():
            self.post('start', {'filename': 'pack.zip', 'size': 4}, 409)
        job = self.start(b'ABCD')['job']
        self.post('chunk', {'job': job, 'offset': 1, 'data': 'QQ=='}, 409)
        self.post('chunk', {'job': job, 'offset': 0, 'data': 'QQ=='})
        self.post('preview', {'job': job}, 409)
        self.post('cancel', {'job': job})
        self.post('chunk', {'job': job, 'offset': 1, 'data': 'QQ=='}, 404)

    def test_multichunk_install_rejects_wrong_confirmation_and_no_replay(self):
        archive = self.zip({'wrapper/Demo/manifest.xml': '<Mod><ID>Demo</ID></Mod>',
                            'wrapper/Demo/Textures/data.bin': b'X' * (2 * 1024**2 + 5)})
        preview = self.upload(archive)
        self.assertEqual(preview['kind'], 'install')
        self.assertFalse(self.mods.exists())
        self.confirm({**preview, 'token': '0' * 32}, 409)
        current = self.post('preview', {'job': preview['job']}).json()
        self.confirm(preview, 409)
        self.assertFalse(self.mods.exists())
        self.confirm(current)
        self.confirm(current, 404)
        self.assertEqual((self.mods / 'Demo/Textures/data.bin').stat().st_size, 2 * 1024**2 + 5)
        self.assertEqual(self.save.read_bytes(), b'fixture save untouched')

    def test_fresh_local_collision_blocks_entire_plan(self):
        preview = self.upload(self.zip({f'{n}/manifest.xml': f'<Mod><ID>{n}</ID></Mod>' for n in ('One', 'Two')}))
        self.mods.mkdir()
        (self.mods / 'Two').mkdir()
        (self.mods / 'Two/user.txt').write_text('keep')
        self.confirm(preview, 409)
        self.assertFalse((self.mods / 'One').exists())
        self.assertEqual((self.mods / 'Two/user.txt').read_text(), 'keep')
        self.post('cancel', {'job': preview['job']})

    def test_portable_round_trip_prefers_local_and_restores_order_without_trust(self):
        self.mods.mkdir()
        local = self.mods / 'custom-folder'
        local.mkdir()
        (local / 'manifest.xml').write_text('<Mod><ID>First</ID><Name>Local</Name></Mod>')
        for ident in ('First', 'Second'):
            path = self.workshop / ident
            path.mkdir()
            (path / 'manifest.xml').write_text(f'<Mod><ID>{ident}</ID><Name>Workshop</Name></Mod>')
        pdir = self.ops.profiles.profiles_dir / 'Preset'
        (pdir / 'ModConfigs').mkdir(parents=True)
        (pdir / 'ModConfigs/First.json').write_text('{"distance":12}')
        (pdir / 'profile_meta.json').write_text(json.dumps({'name': 'Preset', 'mode': 'json_keys', 'active_mods': ['Second', 'First'],
                                                            'has_isolated_configs': True, 'save_note': 'Two mods', 'created_at': '', 'updated_at': ''}))
        (pdir / 'mod_keys.json').write_text('{"modTrust":"PRIVATE"}')
        (pdir / 'SaveFile.es3.snapshot').write_text('Full save data')
        info = self.post('export/preview', {'name': 'Preset', 'include_configs': True}).json()
        self.assertEqual(info['order'], ['Second', 'First'])
        self.assertEqual(info['config_files'], ['First.json'])
        self.post('export/confirm', {'job': info['job'], 'token': '0' * 32}, 409)
        download = self.post('export/confirm', {k: info[k] for k in ('job', 'token')})
        self.assertEqual(download.headers['content-type'], 'application/zip')
        with ZipFile(io.BytesIO(download.content)) as pack:
            names = pack.namelist()
            self.assertIn(b'<Name>Local</Name>', pack.read('mods/First/manifest.xml'))
            self.assertNotIn('mod_keys.json', names)
            self.assertNotIn('SaveFile.es3.snapshot', names)
            self.assertNotIn(b'PRIVATE', pack.read('psmm-portable.json'))
        # Migration target is another temporary game directory, not the source directory.
        self.mods = self.root / 'NewMachineMods'
        import_info = self.upload(download.content)
        self.assertEqual(import_info['kind'], 'import')
        self.assertTrue(import_info['blockers'])  # Existing profile name cannot be overwritten.
        renamed = self.post('preview', {'job': import_info['job'], 'name': 'Restored'}).json()
        self.assertFalse(renamed['blockers'])
        self.assertTrue(renamed['rows'][0]['workshop'])
        self.assertFalse(self.mods.exists())
        self.confirm(renamed)
        restored = self.ops.profiles.profiles_dir / 'Restored'
        self.assertEqual(json.loads((restored / 'mod_keys.json').read_text()), {'modOrder': {
            '__type': 'System.Collections.Generic.List`1[[System.String, mscorlib]],mscorlib', 'value': ['Second', 'First']}})
        self.assertEqual((restored / 'ModConfigs/First.json').read_text(), '{"distance":12}')
        self.assertEqual((self.mods / 'First/manifest.xml').read_text(), '<Mod><ID>First</ID><Name>Local</Name></Mod>')
        self.assertEqual(self.save.read_bytes(), b'fixture save untouched')
        self.assertEqual(set(p.name for p in self.ops.profiles.list_profiles()), {'Preset', 'Restored'})
        # Modify one payload while keeping its old checksum: ZIP CRC alone is insufficient.
        with ZipFile(io.BytesIO(download.content)) as pack:
            altered = {n: pack.read(n) for n in pack.namelist()}
        altered['mods/First/manifest.xml'] = b'<Mod><ID>First</ID><Name>Changed</Name></Mod>'
        failure = self.upload(self.zip(altered), preview_status=422)
        self.assertIn('mods/First/manifest.xml', failure['detail'])
