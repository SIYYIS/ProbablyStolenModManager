import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.game_review import read_game_review
from core.manifest import check_game_version_compatibility, version_entry_status
from core.paths import GAME_EXE_NAME, GamePaths
from core.settings import AppSettings


class GameReviewTests(TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.game = self.root / 'Game'
        self.managed = self.game / 'Probably Stolen_Data' / 'Managed'
        self.managed.mkdir(parents=True)
        self.exe = self.game / GAME_EXE_NAME
        self.exe.touch()
        os.utime(self.exe, (1_700_000_000, 1_700_000_000))
        self.log = self.root / 'Player.log'
        self.paths = GamePaths(game_dir=self.game, save_data_dir=self.root)

    def write_log(self, managed=None):
        path = (managed or self.managed).as_posix()
        self.log.write_text(
            f"Mono path[0] = '{path}'\n"
            "[ModLoader] audit: game 0.45B, trust list: previous\n"
            "[ModLoader] audit: fixture.review v0.19.4 source=Local workshop=0 owner=0 running=True trust=Updated caps=none hash=old\n"
            "[ModLoader] audit: game 0.46D, trust list: fixture\n"
            "[ModLoader] audit: fixture.review v0.19.4 source=Workshop workshop=180019999 owner=0 running=True trust=Match caps=files, steam hash=new\n",
            encoding='utf-8',
        )

    def test_current_install_and_latest_audit_block(self):
        self.write_log()
        review = read_game_review(self.paths)
        self.assertEqual(review.version, '0.46D')
        self.assertIsNotNone(review.logged_at)
        self.assertEqual(review.trust, {('fixture.review', '0.19.4', 'workshop', '180019999'): 'Match'})

    def test_other_install_or_new_binary_does_not_supply_version_or_trust(self):
        self.write_log(self.root / 'other' / self.managed.name)
        self.assertFalse(read_game_review(self.paths).version)
        self.write_log()
        dll = self.managed / 'Assembly-CSharp.dll'
        dll.touch()
        future = self.log.stat().st_mtime + 60
        os.utime(dll, (future, future))
        self.assertFalse(read_game_review(self.paths).version)
        self.assertEqual(read_game_review(self.paths).trust, {})

    def test_legacy_and_current_versions_get_separate_verdicts(self):
        self.assertEqual(version_entry_status('0.46D', '0.46D'), 'match')
        self.assertEqual(version_entry_status('0.46', '0.46D'), 'match')
        self.assertEqual(version_entry_status('0.45D', '0.46D'), 'mismatch')
        self.assertEqual(version_entry_status('1.0.0', '0.46D'), 'mismatch')
        self.assertEqual(version_entry_status('0.46D', ''), 'unknown')
        self.assertEqual(check_game_version_compatibility(['0.46D', '1.0.0'], '0.46D'), 'compatible')

    def test_old_hardcoded_default_is_not_a_manual_override(self):
        settings = self.root / 'settings.json'
        settings.write_text(json.dumps({'game_version': '1.0.0.14'}), encoding='utf-8')
        self.assertEqual(AppSettings.load(settings).game_version, '')
        AppSettings(game_version='1.0.0.14', game_version_manual=True).save(settings)
        self.assertEqual(AppSettings.load(settings).game_version, '1.0.0.14')
