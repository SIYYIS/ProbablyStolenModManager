import json
import tempfile
import unittest
from pathlib import Path

from core.paths import GamePaths
from core.loot_merge import compare_loot, generate_loot, install_loot
from core.mod_file_health import scan_health, isolate_file, restore_file, list_isolated
from core.maintenance_files import MaintenanceError


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        self.mods = root / 'Mods'
        self.mods.mkdir()
        self.workshop = root / 'Workshop'
        self.workshop.mkdir()
        self.save = root / 'SaveFile.es3'
        self.save.write_bytes(b'untouched')
        self.paths = GamePaths(game_dir=root, local_mods_dir=self.mods, workshop_dir=self.workshop, save_file=self.save)
        self.addCleanup(self.assertEqual, self.save.read_bytes(), b'untouched')

    def mod(self, name, payload=None, *, workshop=False):
        path = (self.workshop if workshop else self.mods) / name
        path.mkdir()
        (path / 'manifest.xml').write_text(
            f'<Mod><ID>{name}</ID><Name>{name}</Name><Assemblies><Assembly>main.dll</Assembly></Assemblies>'
            '<Capabilities /></Mod>', encoding='utf-8')
        (path / 'main.dll').write_bytes(b'fixture only, never loaded')
        if payload is not None:
            (path / 'LootTables').mkdir()
            (path / 'LootTables' / 'content.json').write_text(json.dumps(payload), encoding='utf-8')
        return path

    def test_comparison_and_add_only_choice(self):
        a = self.mod('a', {'patches': [{'table': 'junkTable', 'add': [{'id': 'shared', 'weight': 2}, {'id': 'a_only', 'weight': 4}]}]})
        self.mod('b', {'patches': [{'table': 'junkTable', 'add': [{'id': 'shared', 'weight': 9}, {'id': 'b_only', 'weight': 6}]}]})
        carrier = self.mod('carrier')
        order = ['a', 'b', 'carrier']
        report = compare_loot(self.paths, order)
        table = report['targets'][0]
        self.assertEqual(table['conflicts'][0]['entry'], 'shared')
        self.assertEqual([(x['mod_id'], x['weight']) for x in table['conflicts'][0]['options']], [('a', 2), ('b', 9)])
        result = generate_loot(report, 'table:junkTable', {'shared': 'a'})
        self.assertEqual(result['patch'], {'patches': [{'table': 'junkTable', 'add': [{'id': 'shared', 'weight': 2}]}]})
        self.assertEqual(result['draft_order'], order)
        self.assertFalse((carrier / 'LootTables').exists())
        self.assertTrue((a / 'LootTables' / 'content.json').is_file())

    def test_clear_and_parse_failures_block(self):
        self.mod('a', {'patches': [{'table': 'junkTable', 'clear': True, 'add': [{'id': 'it', 'weight': 2}]}]})
        b = self.mod('b', {'patches': [{'table': 'junkTable', 'add': [{'id': 'it', 'weight': 9}]}]})
        order = ['a', 'b']
        report = compare_loot(self.paths, order)
        self.assertTrue(report['targets'][0]['blocked'])
        with self.assertRaises(MaintenanceError):
            generate_loot(report, 'table:junkTable', {'it': 'a'})
        (b / 'LootTables' / 'content.json').write_text('{"patches":[],"patches":[]}', encoding='utf-8')
        self.assertTrue(compare_loot(self.paths, order)['errors'])

    def test_health_quarantines_and_restores_exactly_without_replacing(self):
        a = self.mod('a')
        (a / 'archive.zip').write_bytes(b'zip fixture')
        (a / 'extra.dll').write_bytes(b'extra fixture')
        items = {x['path']: x for x in scan_health(self.paths, 'local', 'a')['issues']}
        self.assertEqual(set(items), {'archive.zip', 'extra.dll'})
        self.assertNotEqual(items['archive.zip']['severity'], items['extra.dll']['severity'])
        record = isolate_file(self.paths, 'a', items['extra.dll']['id'])
        self.assertEqual(len(list_isolated(self.paths)), 1)
        self.assertFalse((a / 'extra.dll').exists())
        (a / 'extra.dll').write_bytes(b'newer')
        with self.assertRaises(MaintenanceError):
            restore_file(self.paths, record['id'])
        self.assertEqual((a / 'extra.dll').read_bytes(), b'newer')
        (a / 'extra.dll').unlink()
        restore_file(self.paths, record['id'])
        self.assertEqual((a / 'extra.dll').read_bytes(), b'extra fixture')
        self.assertEqual(list_isolated(self.paths), [])

    def test_workshop_is_read_only_and_stale_local_file_is_not_moved(self):
        for source in ('workshop', 'local'):
            a = self.mod('a', workshop=source == 'workshop')
            (a / 'archive.zip').write_bytes(b'old')
        source_item = scan_health(self.paths, 'workshop', 'a')['issues'][0]
        self.assertFalse(source_item['can_isolate'])
        with self.assertRaises(MaintenanceError):
            isolate_file(self.paths, 'a', source_item['id'])
        item = scan_health(self.paths, 'local', 'a')['issues'][0]
        (self.mods / 'a' / 'archive.zip').write_bytes(b'changed')
        with self.assertRaises(MaintenanceError):
            isolate_file(self.paths, 'a', item['id'])
        self.assertEqual((self.workshop / 'a' / 'archive.zip').read_bytes(), b'old')

    def test_install_revalidates_data_and_only_creates_new_json(self):
        self.mod('a', {'patches': [{'table': 'junkTable', 'add': [{'id': 'item', 'weight': 2}]}]})
        b = self.mod('b', {'patches': [{'table': 'junkTable', 'add': [{'id': 'item', 'weight': 3}]}]})
        c = self.mod('carrier')
        order = ['a', 'b', 'carrier']
        report = compare_loot(self.paths, order)
        body = {'item': 'a'}
        file = b / 'LootTables' / 'content.json'
        original = file.read_bytes()
        file.write_bytes(original + b' ')
        with self.assertRaises(MaintenanceError):
            install_loot(self.paths, order, report['revision'], 'table:junkTable', body)
        self.assertFalse((c / 'LootTables').exists())
        file.write_bytes(original)
        output = install_loot(self.paths, order, report['revision'], 'table:junkTable', body)
        installed = (self.mods / output['relative_path']).read_bytes()
        self.assertEqual(json.loads(installed)['patches'][0]['add'], [{'id': 'item', 'weight': 2}])
        with self.assertRaises(MaintenanceError):
            install_loot(self.paths, order, report['revision'], 'table:junkTable', {'item': 'b'})
        self.assertEqual((self.mods / output['relative_path']).read_bytes(), installed)
        self.assertEqual(file.read_bytes(), original)

    def test_declarations_groups_and_aliases_are_compared_but_not_generated(self):
        a = self.mod('a', {'tables': [{'id': 'custom', 'entries': [{'id': 'item', 'weight': 1}]}],
            'groups': [{'id': 'test_group', 'entries': [{'id': 'custom', 'weight': 6}]}],
            'patches': [{'table': 'junkTable', 'add': [{'id': 'item', 'weight': 2}]}]})
        self.mod('b', {'patches': [{'table': 'junkTable', 'add': [{'id': 'item', 'weight': 3}]},
            {'table': 'JunkTable', 'remove': ['item']}, {'group': 'test_group', 'setWeight': [{'id': 'custom', 'weight': 0}]}]})
        nested = a / 'LootTables' / 'Ignored'
        nested.mkdir()
        (nested / 'invalid.json').write_bytes(b'not part of the load')
        report = compare_loot(self.paths, ['a', 'b'])
        self.assertFalse(report['errors'])
        self.assertTrue(all(t['blocked'] for t in report['targets']))
        table = next(t for t in report['targets'] if t['key'] == 'group:test_group')
        self.assertEqual([e['operation'] for e in table['edits']], ['register', 'initial', 'setWeight'])
        with self.assertRaises(MaintenanceError):
            generate_loot(report, 'table:junkTable', {'item': 'a'})

    def test_link_and_manifest_change_cannot_redirect_restore(self):
        a = self.mod('a')
        (a / 'extra.dll').write_bytes(b'original')
        issue = scan_health(self.paths, 'local', 'a')['issues'][0]
        record = isolate_file(self.paths, 'a', issue['id'])
        (a / 'manifest.xml').write_bytes((a / 'manifest.xml').read_bytes() + b' ')
        with self.assertRaises(MaintenanceError):
            restore_file(self.paths, record['id'])
        self.assertEqual(len(list_isolated(self.paths)), 1)
        self.assertFalse((a / 'extra.dll').exists())

    def test_dll_at_relative_path_not_basename_and_link_not_movable(self):
        a = self.mod('a')
        (a / 'Sub').mkdir()
        (a / 'Sub' / 'main.dll').write_bytes(b'other DLL with same basename')
        link_target = self.workshop / 'unchanged.zip'
        link_target.write_bytes(b'outside')
        import os
        os.link(link_target, a / 'another.zip')
        issues = {item['path']: item for item in scan_health(self.paths, 'local', 'a')['issues']}
        self.assertIn('Sub/main.dll', issues)
        self.assertNotIn('main.dll', issues)
        self.assertFalse(issues['another.zip']['can_isolate'])
        self.assertEqual(link_target.read_bytes(), b'outside')

    def test_routes_require_confirmation_and_known_process_state(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from webui.maintenance_routes import maintenance_router

        a = self.mod('a')
        (a / 'scratch.psd').write_bytes(b'art fixture')
        app = FastAPI()
        app.include_router(maintenance_router(SimpleNamespace(settings=lambda: {}, paths=lambda _: self.paths)), prefix='/api')
        with TestClient(app) as client:
            route = '/api/maintenance/health'
            issue = client.get(f'{route}/scan', params={'source': 'local', 'folder': 'a'}).json()['issues'][0]
            self.assertEqual(client.get(f'{route}/scan', params={'source': 'local', 'folder': '..'}).status_code, 409)
            self.assertEqual(client.get(f'{route}/catalogue').json()['copies'][0]['mod_id'], 'a')
            payload = {'folder': 'a', 'issue_id': issue['id']}
            self.assertEqual(client.post(f'{route}/isolate', json={**payload, 'confirm': False}).status_code, 409)
            for output in ('failed', '"Probably Stolen.exe","12","Console","1","34 K"\n'):
                with patch('webui.maintenance_routes.subprocess.check_output', return_value=output):
                    self.assertEqual(client.post(f'{route}/isolate', json={**payload, 'confirm': True}).status_code, 409)
                    self.assertTrue((a / 'scratch.psd').is_file())
            with patch('webui.maintenance_routes.subprocess.check_output', return_value='"explorer.exe","12","Console","1","34 K"\n'):
                response = client.post(f'{route}/isolate', json={**payload, 'confirm': True})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse((a / 'scratch.psd').is_file())
                record_id = response.json()['id']
                self.assertEqual(client.post(f'{route}/restore', json={'record_id': record_id, 'confirm': True}).status_code, 200)
                self.assertEqual((a / 'scratch.psd').read_bytes(), b'art fixture')

    def test_loot_routes_compare_preview_and_publish_without_saving(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from webui.maintenance_routes import maintenance_router

        for name, weight in [('a', 6), ('b', 8)]:
            self.mod(name, {'patches': [{'table': 'junkTable', 'add': [{'id': 'item', 'weight': weight}]}]})
        carrier = self.mod('carrier')
        order = ['a', 'b', 'carrier']
        router = maintenance_router(SimpleNamespace(settings=lambda: None, paths=lambda _: self.paths))
        app = FastAPI()
        app.include_router(router, prefix='/api')
        with TestClient(app) as client:
            route = '/api/maintenance/loot'
            response = client.post(f'{route}/compare', json={'order': order})
            self.assertEqual(response.status_code, 200)
            body = {'order': order, 'target': 'table:junkTable', 'choices': {'item': 'b'},
                    'revision': response.json()['revision']}
            self.assertEqual(client.post(f'{route}/generate', json={**body, 'revision': 'a' * 64}).status_code, 409)
            preview = client.post(f'{route}/generate', json=body)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['draft_order'], order)
            self.assertFalse((carrier / 'LootTables').exists())
            self.assertEqual(client.post(f'{route}/install', json={**body, 'confirm': False}).status_code, 409)
            with patch('webui.maintenance_routes.subprocess.check_output', return_value='"explorer.exe","12","Console","1","34 K"\n'):
                installed = client.post(f'{route}/install', json={**body, 'confirm': True})
            self.assertEqual(installed.status_code, 200, installed.text)
            self.assertEqual(installed.json()['patch'], preview.json()['patch'])
