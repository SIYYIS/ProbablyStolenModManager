import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.es3_manager import ProfileManager
from core.file_ops import write_lock
from core.paths import GamePaths
from core.save_slots import parse_slot, slot_revision, write_slot
from core.snapshot_history import SnapshotError, SnapshotHistory, backup_linked_slot
from webui.operations import Operations
from webui.snapshot_routes import snapshot_router


def slot(slot_id, cash=22, favor=365, day=106):
    return json.dumps({
        "playerStore": {"__type": "PlayerStore,Assembly-CSharp", "value": {
            "playerCash": cash, "wildFavor": favor, "saveSlotId": slot_id,
        }},
        "storeStation": {"__type": "StoreStation,Assembly-CSharp", "value": {"dayCounter": day}},
    }).encode("utf-8")


class SnapshotHistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="psmm-history-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data_dir = self.root / "manager"
        self.saves = self.root / "saves"
        self.saves.mkdir()
        self.original = self.saves / "save_0.es3"
        self.original.write_bytes(slot(0))
        (self.saves / "save_1.es3").write_bytes(slot(1))
        (self.saves / "save_0_day101.es3").write_bytes(slot(0, day=101))
        self.index = self.saves / "saves_index.es3"
        self.index.write_bytes(b'{"slot":1}')
        self.global_save = self.saves / "SaveFile.es3"
        self.global_save.write_bytes(b'{"modOrder":{"value":["a","b","c"]}}')
        self.configs = self.saves / "ModConfigs"
        self.configs.mkdir()
        self.paths = GamePaths(save_data_dir=self.saves, save_file=self.global_save, mod_configs_dir=self.configs)
        self.profiles = ProfileManager(self.data_dir)
        self.history = SnapshotHistory(self.data_dir, self.profiles, self.paths)
        for name in ("core.snapshot_history.is_game_running", "core.es3_manager.is_game_running", "core.save_slots.is_game_running"):
            running = patch(name, return_value=False)
            running.start()
            self.addCleanup(running.stop)

    def test_explicit_profile_link_snapshots_canonical_slot_only(self):
        self.assertTrue(self.profiles.save_profile("预设 A", self.paths)[0])
        adapter = SimpleNamespace(data_dir=self.data_dir, profiles=self.profiles)
        self.assertIsNone(backup_linked_slot(adapter, "预设 A", paths=self.paths))
        with self.assertRaisesRegex(SnapshotError, "存档目录已变更"):
            self.history.set_link("预设 A", 0, "f" * 64)
        self.history.set_link("预设 A", 0, self.history.scope)
        entry = backup_linked_slot(adapter, "预设 A", paths=self.paths)
        self.assertEqual(entry["reason"], "pre_switch")
        self.assertEqual(entry["profile"], "预设 A")
        self.assertEqual(entry["slot_id"], 0)
        self.assertEqual(entry["fields"], {"day": 106, "cash": 22, "favor": 365})
        self.assertEqual(self.history.index(0, 20)["slots"], [0, 1])
        self.assertEqual(self.index.read_bytes(), b'{"slot":1}')
        (self.saves / "save_0_day101.es3").unlink()
        self.original.unlink()
        with self.assertRaises(SnapshotError):
            backup_linked_slot(adapter, "预设 A", paths=self.paths)
        self.assertEqual(self.history.index(0, 20)["profiles"][0]["state"], "missing_slot")
        self.history.set_link("预设 A", None, self.history.scope)
        self.assertIsNone(backup_linked_slot(adapter, "预设 A", paths=self.paths))

    def test_switch_hook_refuses_an_identically_named_slot_in_a_different_directory(self):
        self.assertTrue(self.profiles.save_profile("A", self.paths)[0])
        self.history.set_link("A", 0, self.history.scope)
        another = self.root / "other-saves"
        another.mkdir()
        other_file = another / "save_0.es3"
        other_file.write_bytes(slot(0))
        paths = GamePaths(save_data_dir=another)
        other_history = SnapshotHistory(self.data_dir, self.profiles, paths)
        adapter = SimpleNamespace(data_dir=self.data_dir, profiles=self.profiles)
        self.assertEqual(other_history.index(0, 10)["profiles"][0]["state"], "other_directory")
        with self.assertRaisesRegex(SnapshotError, "目录已变更"):
            backup_linked_slot(adapter, "A", paths=paths)
        self.assertEqual(other_file.read_bytes(), slot(0))

    def test_export_then_guarded_restore_has_rollback_snapshot(self):
        entry = self.history.capture(0, self.history.scope)
        edited = slot(0, cash=99, day=120)
        self.original.write_bytes(edited)
        details = self.history.inspect(entry["id"])
        self.assertEqual(details["current_fields"], {"day": 120, "cash": 99, "favor": 365})
        exported = self.history.restore(entry["id"], details["source_revision"])
        export_path = Path(exported["path"])
        self.assertFalse(export_path.is_relative_to(self.saves))
        self.assertEqual(export_path.read_bytes(), slot(0))
        self.assertEqual(self.original.read_bytes(), edited)
        target = details["target_revision"]
        with self.assertRaisesRegex(SnapshotError, "明确确认"):
            self.history.restore(entry["id"], details["source_revision"], target_revision=target, replace=True)
        with patch("core.snapshot_history.is_game_running", return_value=True), self.assertRaisesRegex(SnapshotError, "关闭游戏"):
            self.history.restore(entry["id"], details["source_revision"], target_revision=target, replace=True, confirm=True)
        result = self.history.restore(entry["id"], details["source_revision"], target_revision=target, replace=True, confirm=True)
        self.assertEqual(self.original.read_bytes(), slot(0))
        self.assertEqual((self.saves / "save_1.es3").read_bytes(), slot(1))
        self.assertEqual(self.index.read_bytes(), b'{"slot":1}')
        self.assertEqual(self.history.inspect(result["backup"])["fields"]["cash"], 99)

    def test_stale_preview_tampering_and_inflight_game_start(self):
        entry = self.history.capture(0, self.history.scope)
        self.original.write_bytes(slot(0, cash=99))
        details = self.history.inspect(entry["id"])
        self.original.write_bytes(slot(0, cash=45))
        with self.assertRaisesRegex(SnapshotError, "档位已变更"):
            self.history.restore(entry["id"], details["source_revision"], target_revision=details["target_revision"],
                                 replace=True, confirm=True)
        details = self.history.inspect(entry["id"])
        with patch("core.snapshot_history.is_game_running", side_effect=[False, False, True]):
            with self.assertRaisesRegex(SnapshotError, "关闭游戏"):
                self.history.restore(entry["id"], details["source_revision"], target_revision=details["target_revision"],
                                     replace=True, confirm=True)
        self.assertEqual(self.original.read_bytes(), slot(0, cash=45))
        self.assertFalse(list(self.saves.glob(".psmm-restore-*.tmp")))
        payload, = (self.history.root / "slots" / self.history.scope / entry["id"].split(":")[1]).glob("save_*.es3")
        payload.write_bytes(slot(0, cash=23))
        with self.assertRaisesRegex(SnapshotError, "校验失败"):
            self.history.restore(entry["id"], details["source_revision"])
        with write_lock(self.saves), self.assertRaises(OSError):
            self.history.capture(0, self.history.scope)

    def test_unparsed_slots_offer_copy_but_no_live_replace(self):
        self.original.write_bytes(b"opaque es3")
        entry = self.history.capture(0, self.history.scope)
        self.original.write_bytes(slot(0))
        details = self.history.inspect(entry["id"])
        self.assertIsNone(details["fields"])
        self.assertIsNone(details["target_revision"])
        result = self.history.restore(entry["id"], details["source_revision"])
        self.assertEqual(Path(result["path"]).read_bytes(), b"opaque es3")
        with self.assertRaisesRegex(SnapshotError, "格式验证"):
            self.history.restore(entry["id"], details["source_revision"], target_revision="f" * 64,
                                 replace=True, confirm=True)

    def test_timeline_projects_existing_backups_and_config_file_changes(self):
        cfg = self.configs / "known.json"
        cfg.write_bytes(b'{"limit":1}')
        (self.configs / "only-in-backup.json").touch()
        original_global = self.global_save.read_bytes()
        self.assertTrue(self.profiles.save_profile("A", self.paths)[0])
        backup = self.profiles.backup_save_file(self.global_save)
        self.global_save.write_bytes(b'{"modOrder":["b","a","new"]}')
        self.assertTrue(self.profiles.apply_profile("A", self.paths)[0])
        self.global_save.write_bytes(original_global)
        cfg.write_bytes(b'{"limit":2}')
        (self.configs / "only-in-backup.json").unlink()
        (self.configs / "current.json").touch()
        legacy = self.data_dir / "slot_backups" / self.history.scope
        write_slot(self.saves, legacy, 0, slot_revision(self.saves, 0, parse_slot(slot(0), 0)), changes={"cash": 123})
        timeline = self.history.index(0, 100)
        self.assertEqual({entry["kind"] for entry in timeline["entries"]}, {"slot", "global", "configs"})
        self.assertEqual(self.history.index(1, 1)["entries"], timeline["entries"][1:2])
        config, = self.history.index(0, 10, "configs")["entries"]
        config_details = self.history.inspect(config["id"])
        self.assertEqual(config_details["diff"]["added"], ["only-in-backup.json"])
        self.assertEqual(config_details["diff"]["removed"], ["current.json"])
        self.assertEqual(config_details["diff"]["changed"], ["known.json"])
        copy = Path(self.history.restore(config["id"], config_details["source_revision"])["path"])
        self.assertEqual((copy / "known.json").read_bytes(), b'{"limit":1}')
        self.assertEqual(cfg.read_bytes(), b'{"limit":2}')
        pre_switch = self.history.inspect(config["id"].replace("configs:", "global:", 1))
        self.assertTrue(pre_switch["diff"]["order_changed"])
        self.assertEqual(pre_switch["diff"]["added"], ["new"])
        self.assertEqual(pre_switch["diff"]["removed"], ["c"])
        self.assertTrue(self.history.inspect(f"global:{backup.name}")["identical"])

    def test_routes_require_exact_ids_and_default_to_export(self):
        ops = Operations(self.data_dir)
        app = FastAPI()
        app.include_router(snapshot_router(ops), prefix="/api")
        with TestClient(app) as client, patch.object(ops, "paths", return_value=self.paths):
            endpoint = "/api/snapshots"
            scope = client.get(endpoint).json()["scope"]
            self.assertEqual(client.post(endpoint, json={"slot_id": True, "scope": scope}).status_code, 422)
            self.assertEqual(client.get(endpoint + "/inspect", params={"id": "global:../../SaveFile.es3"}).status_code, 409)
            created = client.post(endpoint, json={"slot_id": 0, "scope": scope})
            self.assertEqual(created.status_code, 200)
            identifier = created.json()["entry"]["id"]
            detail = client.get(endpoint + "/inspect", params={"id": identifier}).json()
            result = client.post(endpoint + "/restore", json={"id": identifier, "source_revision": detail["source_revision"]})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(Path(result.json()["path"]).read_bytes(), self.original.read_bytes())
            self.assertEqual(client.post(endpoint + "/restore", json={"id": identifier, "source_revision": "0" * 64}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
