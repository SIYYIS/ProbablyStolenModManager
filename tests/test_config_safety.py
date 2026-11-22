"""Focused safety tests for config and loot JSON writes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_inspector import discover_editable_files, save_mod_config_entries, save_raw_json_file
from core.file_ops import atomic_write


class TestConfigSafety(unittest.TestCase):
    def test_mod_id_cannot_select_a_config_outside_mod_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs = root / "ModConfigs"
            configs.mkdir()
            (root / "other.json").write_text("{}", encoding="utf-8")
            (configs / "normal.json").write_text("{}", encoding="utf-8")
            self.assertEqual(discover_editable_files("../other", None, configs), [])
            self.assertEqual(len(discover_editable_files("normal", None, configs)), 1)

    def test_config_save_uses_atomic_write_and_preserves_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "mod.json"
            backup_dir = root / "backups"
            original = {"enabled": False, "label": "原有"}
            target.write_text(json.dumps(original), encoding="utf-8")

            with patch("core.config_inspector.atomic_write", wraps=atomic_write) as write:
                result = save_mod_config_entries(
                    target, {"enabled": True}, "flat_dict", original, backup_dir
                )

            self.assertEqual(result, target)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"enabled": True, "label": "原有"},
            )
            write.assert_called_once()
            self.assertEqual(len(list(backup_dir.glob("mod_*.json.bak"))), 1)

    def test_raw_save_uses_unique_backups_for_same_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "loot.json"
            backup_dir = root / "backups"
            target.write_text('{"revision": 0}', encoding="utf-8")
            fixed_time = datetime(2026, 11, 21, 12, 34, 56, 789012)

            with patch("core.config_inspector.datetime") as clock:
                clock.now.return_value = fixed_time
                save_raw_json_file(target, '{"revision": 1}', backup_dir)
                save_raw_json_file(target, '{"revision": 2}', backup_dir)

            backups = sorted(backup_dir.glob("loot_*.json.bak"))
            self.assertEqual(len(backups), 2)
            self.assertNotEqual(backups[0].name, backups[1].name)
            self.assertTrue(all("20261121_123456_789012_" in p.name for p in backups))
            self.assertCountEqual(
                [json.loads(p.read_text(encoding="utf-8"))["revision"] for p in backups],
                [0, 1],
            )
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"revision": 2})

    def test_both_save_apis_reject_link_targets_before_backup_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "linked.json"
            target.write_text('{"keep": true}', encoding="utf-8")
            backup_dir = root / "backups"

            for save in (
                lambda: save_mod_config_entries(
                    target, {"keep": False}, "flat_dict", {"keep": True}, backup_dir
                ),
                lambda: save_raw_json_file(target, '{"keep": false}', backup_dir),
            ):
                with self.subTest(save=save), patch(
                    "core.config_inspector.is_link", return_value=True
                ), patch("core.config_inspector.atomic_write") as write:
                    with self.assertRaisesRegex(OSError, "linked file"):
                        save()
                    write.assert_not_called()

            self.assertEqual(target.read_text(encoding="utf-8"), '{"keep": true}')
            self.assertFalse(backup_dir.exists())


if __name__ == "__main__":
    unittest.main()
