from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import test_session as session
from core.paths import GAME_EXE_NAME
from core.scanner import scan_installed_mods
from game_fixture import make_game_fixture


class IsolatedTestSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = make_game_fixture(Path(tmp.name))
        self.root = self.paths.save_data_dir
        self.slot = self.root / 'save_0.es3'
        self.slot.write_bytes(b'official slot data')
        (self.root / 'Player.log').write_bytes(b'original log')
        (self.paths.game_dir / GAME_EXE_NAME).touch()
        self.global_bytes = b'{"modOrder": ["alpha", "beta"], "modTrustMac": "opaque"}'
        self.paths.save_file.write_bytes(self.global_bytes)
        for name, value in {'default_save_data_dir': self.root, 'is_game_running': False, 'launch_game': (True, 'test fixture')}.items():
            replacement = patch.object(session, name, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.mods = scan_installed_mods(self.paths)

    def elapsed(self):
        state = session._read(self.root)
        state['wait_until'] = 0
        session._write(self.root, state)

    def launch(self):
        session.launch_round(self.root, self.paths, self.mods)
        self.elapsed()

    def test_vanilla_recovery_preserves_original_bytes(self):
        session.start(self.paths, 'vanilla', self.mods)
        self.assertFalse(self.slot.exists())
        self.assertFalse(self.paths.mod_configs_dir.exists())
        self.assertEqual(json.loads(self.paths.save_file.read_bytes()), {'modOrder': [], 'modTrustMac': 'opaque'})
        self.launch()
        self.slot.write_bytes(b'test data')
        result = session.finish(self.root)
        self.assertTrue(Path(result['archive']).is_dir())
        self.assertFalse(session.active_session(self.root))
        self.assertEqual(self.slot.read_bytes(), b'official slot data')
        self.assertEqual(self.paths.save_file.read_bytes(), self.global_bytes)
        self.assertEqual((self.root / 'Player.log').read_bytes(), b'original log')

    def test_interrupted_directory_swap_is_recoverable(self):
        real_rename = session.os.rename

        def interrupt(src, dest):
            if Path(dest) == self.root:
                raise KeyboardInterrupt
            real_rename(src, dest)

        with patch.object(session.os, 'rename', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            session.start(self.paths, 'vanilla', self.mods)
        self.assertFalse(self.root.exists())
        self.assertFalse(session.status(self.root, self.mods)['ready'])
        session.finish(self.root)
        self.assertEqual(self.slot.read_bytes(), b'official slot data')

    def test_parts_may_not_reproduce_a_combination(self):
        session.start(self.paths, 'bisect', self.mods)
        self.launch()
        self.assertEqual(session.record(self.root, True, self.mods)['order'], ['alpha'])
        self.launch()
        self.assertEqual(session.record(self.root, False, self.mods)['order'], ['beta'])
        self.launch()
        self.assertEqual(session.record(self.root, False, self.mods)['phase'], 'interaction')
        session.finish(self.root)

    def test_prerequisites_are_never_excluded(self):
        self.mods['alpha'].manifest.prerequisites = ['beta']
        self.assertEqual(session.dependency_order(['alpha'], ['alpha', 'beta'], self.mods), ['alpha', 'beta'])
        self.mods['beta'].manifest.prerequisites = ['alpha']
        session.start(self.paths, 'bisect', self.mods)
        self.launch()
        self.assertEqual(session.record(self.root, True, self.mods)['phase'], 'dependency_group')
        session.finish(self.root)


if __name__ == '__main__':
    unittest.main()
