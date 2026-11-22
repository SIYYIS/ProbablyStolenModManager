"""Unit and integration tests for Probably Stolen Mod Manager."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_inspector import (
    discover_editable_files,
    load_mod_config_entries,
    parse_edited_value,
    save_mod_config_entries,
    save_raw_json_file,
)
from core.es3_manager import ProfileManager, inspect_save_file
from core.file_ops import atomic_write
from core.manifest import check_game_version_compatibility, parse_manifest, version_entry_status
from core.paths import GamePaths, is_game_running, parse_vdf_library_paths
from core.scanner import scan_installed_mods, topological_sort_mods, validate_active_mods
from core.workshop import (
    download_workshop_item_manual,
    parse_workshop_ids_from_text,
    translate_category_zh,
)


class TestManifestAndVersions(unittest.TestCase):
    def test_version_matching_rules(self) -> None:
        self.assertEqual(version_entry_status("1.0.0", "1.0.0.14"), "match")
        self.assertEqual(version_entry_status("1.0.*", "1.0.0.14"), "match")
        self.assertEqual(version_entry_status("1.0.0.5 - 1.0.1", "1.0.0.14"), "match")
        self.assertEqual(version_entry_status("1.0.0.13", "1.0.0.14"), "mismatch")
        self.assertEqual(version_entry_status("0.46D", "1.0.0.14"), "legacy")
        self.assertEqual(version_entry_status("1.0 - *", "1.0.0.14"), "invalid")
        self.assertEqual(version_entry_status("*", "1.0.0.14"), "invalid")

        self.assertEqual(check_game_version_compatibility([], "1.0.0.14"), "unknown")
        self.assertEqual(check_game_version_compatibility(["1.0.0"], "1.0.0.14"), "compatible")
        self.assertEqual(check_game_version_compatibility(["0.46"], "1.0.0.14"), "legacy")
        self.assertEqual(check_game_version_compatibility(["1.0.0.1"], "1.0.0.14"), "incompatible")

    def test_parse_manifest_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mf = Path(tmp) / "manifest.xml"
            mf.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Manifest>
  <ID>bank_of_anti</ID>
  <Name>安缇银行</Name>
  <Author>ANTI</Author>
  <ModVersion>1.0.1</ModVersion>
  <Description>Online Bank &amp; Casino</Description>
  <GameVersions><Version>1.0.0</Version></GameVersions>
  <Prerequisites><Mod>ezapi</Mod></Prerequisites>
  <Assemblies><Assembly>BankOfAnti.dll</Assembly></Assemblies>
  <Capabilities>
    <Capability>network</Capability>
    <Capability>steam-auth</Capability>
  </Capabilities>
</Manifest>
""",
                encoding="utf-8",
            )
            parsed = parse_manifest(mf)
            self.assertEqual(parsed.mod_id, "bank_of_anti")
            self.assertEqual(parsed.name, "安缇银行")
            self.assertEqual(parsed.prerequisites, ["ezapi"])
            self.assertEqual(parsed.capabilities, ["network", "steam-auth"])
            self.assertFalse(parsed.has_deprecated_capabilities())

    def test_windows_game_process_check_matches_exact_image_name(self) -> None:
        output = '"Not Probably Stolen.exe","1","Console","1","1 K"\r\n"Probably Stolen.exe","2","Console","1","1 K"\r\n'
        with patch("core.paths.platform.system", return_value="Windows"), patch(
            "core.paths.subprocess.check_output", return_value=output
        ):
            self.assertTrue(is_game_running())
        with patch("core.paths.platform.system", return_value="Windows"), patch(
            "core.paths.subprocess.check_output", return_value=output.splitlines()[0]
        ):
            self.assertFalse(is_game_running())


class TestScannerAndSorting(unittest.TestCase):
    def _write_mod(self, root: Path, folder_name: str, mod_id: str, prereqs: list[str] | None = None, loot: dict | None = None) -> Path:
        mdir = root / folder_name
        mdir.mkdir(parents=True, exist_ok=True)
        pre_xml = "".join(f"<Mod>{p}</Mod>" for p in (prereqs or []))
        (mdir / "manifest.xml").write_text(
            f"""<?xml version="1.0" encoding="utf-8"?>
<Manifest>
  <ID>{mod_id}</ID>
  <Name>{mod_id}</Name>
  <ModVersion>1.0</ModVersion>
  <GameVersions><Version>1.0.0</Version></GameVersions>
  <Prerequisites>{pre_xml}</Prerequisites>
  <Assemblies><Assembly>{mod_id}.dll</Assembly></Assemblies>
  <Capabilities />
</Manifest>
""",
            encoding="utf-8",
        )
        if loot is not None:
            ldir = mdir / "LootTables"
            ldir.mkdir()
            (ldir / "patch.json").write_text(json.dumps(loot), encoding="utf-8")
        return mdir

    def test_local_overrides_workshop_and_topological_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            local_dir = t / "Game" / "Mods"
            ws_dir = t / "Workshop"
            self._write_mod(ws_dir, "3839325008", "ezapi", loot={"patches": [{"table": "junkTable"}]})
            self._write_mod(local_dir, "EZapi_Dev", "ezapi")  # overrides workshop
            self._write_mod(ws_dir, "9999999999", "child_mod", prereqs=["ezapi"], loot={"patches": [{"table": "junkTable"}]})
            self._write_mod(local_dir, "helper", "helper_mod")

            paths = GamePaths(local_mods_dir=local_dir, workshop_dir=ws_dir)
            mods = scan_installed_mods(paths)
            self.assertEqual(set(mods.keys()), {"ezapi", "child_mod", "helper_mod"})
            self.assertTrue(mods["ezapi"].overridden_workshop)
            self.assertEqual(mods["ezapi"].source, "local")

            # child_mod before ezapi -> topological sort should fix order
            sorted_ids, warns = topological_sort_mods(["child_mod", "helper_mod", "ezapi"], mods)
            self.assertEqual(warns, [])
            self.assertLess(sorted_ids.index("ezapi"), sorted_ids.index("child_mod"))

            issues = validate_active_mods(["child_mod", "ezapi"], mods, "1.0.0.14")
            self.assertTrue(any("加载顺序有误" in i.message for i in issues))


class TestES3ProfileManager(unittest.TestCase):
    def test_profile_save_partial_merge_backup_and_share_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("core.es3_manager.is_game_running", return_value=False):
            t = Path(tmp)
            save_dir = t / "LocalLow"
            save_dir.mkdir()
            save_file = save_dir / "SaveFile.es3"
            cfg_dir = save_dir / "ModConfigs"
            cfg_dir.mkdir()
            (cfg_dir / "ezapi.json").write_text(json.dumps({"speed": 2.0}), encoding="utf-8")

            es3_a = {
                "audioVolume": {"__type": "float", "value": 0.5},
                "modOrder": {"__type": "System.Collections.Generic.List`1[[System.String]]", "value": ["ezapi", "mod_a"]},
                "modTrust": {"__type": "string", "value": "TRUST_A"},
                "modTrustMac": {"__type": "string", "value": "MAC_A"},
            }
            save_file.write_text("\ufeff" + json.dumps(es3_a), encoding="utf-8")

            paths = GamePaths(save_data_dir=save_dir, save_file=save_file, mod_configs_dir=cfg_dir)
            mgr = ProfileManager(t / "manager_data")

            ok, _ = mgr.save_profile("档A", paths, save_note="槽位1")
            self.assertTrue(ok)

            # User later changes volume to 0.9 and switches to mod_b
            es3_b = {
                "audioVolume": {"__type": "float", "value": 0.9},
                "modOrder": {"__type": "System.Collections.Generic.List`1[[System.String]]", "value": ["mod_b"]},
                "modTrust": {"__type": "string", "value": "TRUST_B"},
                "modTrustMac": {"__type": "string", "value": "MAC_B"},
            }
            save_file.write_text(json.dumps(es3_b), encoding="utf-8")
            (cfg_dir / "ezapi.json").write_text(json.dumps({"speed": 1.0}), encoding="utf-8")

            # Apply profile A -> should restore modOrder/modTrust/modTrustMac + ModConfigs, while keeping audioVolume 0.9!
            ok2, _ = mgr.apply_profile("档A", paths)
            self.assertTrue(ok2)

            state = inspect_save_file(save_file)
            self.assertTrue(state.is_json)
            self.assertEqual(state.active_mods, ["ezapi", "mod_a"])
            self.assertEqual(state.raw_data["audioVolume"]["value"], 0.9)
            self.assertEqual(state.raw_data["modTrustMac"]["value"], "MAC_A")
            self.assertEqual(json.loads((cfg_dir / "ezapi.json").read_text())["speed"], 2.0)

            # Test 0-byte corruption detection & backup recovery
            save_file.write_bytes(b"\x00" * 64)
            corrupt_state = inspect_save_file(save_file)
            self.assertTrue(corrupt_state.is_corrupted_zero)
            backups = mgr.list_backups()
            self.assertGreaterEqual(len(backups), 1)
            ok3, _ = mgr.restore_backup(backups[0], paths)
            self.assertTrue(ok3)
            self.assertEqual(inspect_save_file(save_file).active_mods, ["mod_b"])

            # Share code round-trip
            code = mgr.export_share_code("档A", ["ezapi", "mod_a"], {"ezapi": "111"})
            decoded = mgr.decode_share_code(code)
            self.assertEqual(decoded["name"], "档A")
            self.assertEqual(decoded["mods"][0]["workshop_id"], "111")

    def test_full_file_restore_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("core.es3_manager.is_game_running", return_value=False):
            t = Path(tmp)
            save_file = t / "SaveFile.es3"
            save_file.write_bytes(b"opaque-save-v1")
            paths = GamePaths(save_file=save_file)
            mgr = ProfileManager(t / "manager_data")

            ok, msg = mgr.save_profile("opaque", paths)
            self.assertTrue(ok, msg)
            save_file.write_bytes(b"opaque-live-v2")

            ok, msg = mgr.apply_profile("opaque", paths)
            self.assertFalse(ok)
            self.assertIn("明确确认", msg)
            self.assertEqual(save_file.read_bytes(), b"opaque-live-v2")

            ok, msg = mgr.apply_profile("opaque", paths, allow_full_restore=True)
            self.assertTrue(ok, msg)
            self.assertEqual(save_file.read_bytes(), b"opaque-save-v1")

    def test_profile_config_switch_replaces_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("core.es3_manager.is_game_running", return_value=False):
            t = Path(tmp)
            save_file = t / "SaveFile.es3"
            cfg_dir = t / "ModConfigs"
            cfg_dir.mkdir()
            save_file.write_text(json.dumps({"modOrder": ["a"]}), encoding="utf-8")
            (cfg_dir / "a.json").write_text('{"v": 1}', encoding="utf-8")
            paths = GamePaths(save_file=save_file, mod_configs_dir=cfg_dir)
            mgr = ProfileManager(t / "manager_data")
            self.assertTrue(mgr.save_profile("A", paths)[0])

            save_file.write_text(json.dumps({"modOrder": ["b"]}), encoding="utf-8")
            (cfg_dir / "b.json").write_text('{"v": 2}', encoding="utf-8")
            self.assertTrue(mgr.save_profile("B", paths)[0])
            self.assertTrue(mgr.apply_profile("A", paths)[0])
            self.assertTrue((cfg_dir / "a.json").is_file())
            self.assertFalse((cfg_dir / "b.json").exists())

    def test_profile_switch_rolls_back_configs_when_save_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("core.es3_manager.is_game_running", return_value=False):
            t = Path(tmp)
            save_file = t / "SaveFile.es3"
            cfg_dir = t / "ModConfigs"
            cfg_dir.mkdir()
            save_file.write_text(json.dumps({"modOrder": ["a"]}), encoding="utf-8")
            (cfg_dir / "a.json").write_text('{"v": 1}', encoding="utf-8")
            paths = GamePaths(save_file=save_file, mod_configs_dir=cfg_dir)
            mgr = ProfileManager(t / "manager_data")
            self.assertTrue(mgr.save_profile("A", paths)[0])

            save_file.write_text(json.dumps({"modOrder": ["b"]}), encoding="utf-8")
            (cfg_dir / "a.json").unlink()
            (cfg_dir / "b.json").write_text('{"v": 2}', encoding="utf-8")
            def fail_live_save(path: Path, data: bytes) -> None:
                if path == save_file:
                    raise OSError("simulated disk failure")
                atomic_write(path, data)

            with patch("core.es3_manager.atomic_write", side_effect=fail_live_save):
                ok, _ = mgr.apply_profile("A", paths)
            self.assertFalse(ok)
            self.assertEqual(json.loads(save_file.read_text(encoding="utf-8"))["modOrder"], ["b"])
            self.assertFalse((cfg_dir / "a.json").exists())
            self.assertTrue((cfg_dir / "b.json").is_file())

    def test_recovery_after_process_exit_keeps_save_and_configs_in_sync(self) -> None:
        script = """
import os
import sys
from pathlib import Path
from core.file_ops import atomic_write, content_digest, staged_directory

root = Path(sys.argv[1])
save = root / 'SaveFile.es3'
configs = root / 'ModConfigs'
before, after = save.read_bytes(), b'{"modOrder":["new"]}'
with staged_directory(configs, save_transition=(content_digest(before), content_digest(after))) as (pending, commit):
    pending.mkdir()
    (pending / 'new.json').write_text('{}')
    commit()
    if sys.argv[2] == 'committed':
        atomic_write(save, after)
    os._exit(42)
"""
        for phase in ("uncommitted", "committed", "external_change"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                save = root / "SaveFile.es3"
                save.write_bytes(b'{"modOrder":["old"]}')
                configs = root / "ModConfigs"
                configs.mkdir()
                (configs / "old.json").write_text("{}")
                proc = subprocess.run([sys.executable, "-B", "-c", script, tmp, phase], cwd=ROOT)
                self.assertEqual(proc.returncode, 42)
                mgr = ProfileManager(root / "manager")
                paths = GamePaths(save_file=save, mod_configs_dir=configs)
                if phase == "external_change":
                    save.write_text('{"modOrder":["external"]}')
                with patch("core.es3_manager.is_game_running", return_value=False):
                    ok, msg = mgr.recover_pending_switch(paths)
                if phase == "external_change":
                    self.assertFalse(ok)
                    self.assertIn("被改动", msg)
                    self.assertEqual(inspect_save_file(save).active_mods, ["external"])
                    self.assertTrue(list(root.glob(".psmm-txn-*/previous/old.json")))
                else:
                    self.assertTrue(ok, msg)
                    name = "new" if phase == "committed" else "old"
                    self.assertEqual(inspect_save_file(save).active_mods, [name])
                    self.assertEqual([p.name for p in configs.iterdir()], [f"{name}.json"])
                    self.assertFalse(list(root.glob(".psmm-txn-*/transaction.json")))

    def test_json_profile_will_not_overwrite_unreadable_live_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("core.es3_manager.is_game_running", return_value=False):
            root = Path(tmp)
            save_file = root / "SaveFile.es3"
            save_file.write_text('{"modOrder": ["a"]}')
            paths = GamePaths(save_file=save_file)
            mgr = ProfileManager(root / "manager")
            self.assertTrue(mgr.save_profile("A", paths)[0])
            save_file.write_bytes(b"opaque-live")
            ok, _ = mgr.apply_profile("A", paths, allow_full_restore=True)
            self.assertFalse(ok)
            self.assertEqual(save_file.read_bytes(), b"opaque-live")

    def test_invalid_profile_names_cannot_escape_profile_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ProfileManager(Path(tmp) / "manager_data")
            for name in (".", "..", "", "CON", "name."):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        mgr._profile_path(name)

    def test_backup_names_are_unique_within_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            save_file = t / "SaveFile.es3"
            save_file.write_text("{}", encoding="utf-8")
            mgr = ProfileManager(t / "manager_data")
            first = mgr.backup_save_file(save_file, "test")
            second = mgr.backup_save_file(save_file, "test")
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(len(mgr.list_backups()), 2)


class TestConfigAndWorkshopHelpers(unittest.TestCase):
    def test_config_inspector_flat_and_loot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cfg_dir = t / "ModConfigs"
            mod_dir = t / "Mods" / "my_mod"
            (mod_dir / "LootTables").mkdir(parents=True)
            cfg_dir.mkdir()

            cfg_file = cfg_dir / "my_mod.json"
            cfg_file.write_text(json.dumps({"fast": False, "rate": 1.5, "mode": "Neon"}), encoding="utf-8")
            loot_file = mod_dir / "LootTables" / "drop.json"
            loot_file.write_text(json.dumps({"tables": []}), encoding="utf-8")

            files = discover_editable_files("my_mod", mod_dir, cfg_dir)
            self.assertEqual(len(files), 2)

            entries, raw_obj, fmt = load_mod_config_entries(cfg_file)
            self.assertEqual(fmt, "flat_dict")
            self.assertEqual({e.key: e.value_type for e in entries}, {"fast": "bool", "rate": "float", "mode": "str"})

            save_mod_config_entries(
                cfg_file,
                {"fast": parse_edited_value("true", "bool"), "rate": parse_edited_value("2.5", "float")},
                fmt,
                raw_obj,
                backup_dir=t / "cfg_bak",
            )
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertTrue(saved["fast"])
            self.assertEqual(saved["rate"], 2.5)

            save_raw_json_file(loot_file, '{"tables": [{"id": "t1"}]}', backup_dir=t / "cfg_bak")
            self.assertEqual(json.loads(loot_file.read_text())["tables"][0]["id"], "t1")

    def test_vdf_and_workshop_helpers(self) -> None:
        vdf = '"libraryfolders"\n{\n  "0"\n  {\n    "path" "C:\\\\Program Files (x86)\\\\Steam"\n  }\n  "1"\n  {\n    "path" "D:\\\\SteamLibrary"\n  }\n}'
        libs = parse_vdf_library_paths(vdf)
        self.assertEqual(len(libs), 2)
        self.assertEqual(translate_category_zh("Quality of Life"), "体验优化 (QoL)")
        ids = parse_workshop_ids_from_text(
            "https://steamcommunity.com/sharedfiles/filedetails/?id=3839325008, 3841509159"
        )
        self.assertEqual(ids, ["3839325008", "3841509159"])

    @unittest.skipUnless(os.name == "nt", "Windows uses a separate interactive console")
    def test_workshop_download_uses_fresh_staging_and_external_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            target = t / "Mods"
            staging_root = t / "downloads" / "1234567"
            staging_root.mkdir(parents=True)
            (staging_root / "manifest.xml").write_text("stale", encoding="utf-8")

            def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertNotIn("-password", cmd)
                self.assertNotIn("input", kwargs)
                self.assertEqual(kwargs.get("creationflags"), subprocess.CREATE_NEW_CONSOLE)
                stage = Path(cmd[cmd.index("-dir") + 1])
                self.assertFalse((stage / "manifest.xml").exists())
                (stage / "manifest.xml").write_text(
                    "<Manifest><ID>fresh</ID></Manifest>", encoding="utf-8"
                )
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("core.workshop.subprocess.run", side_effect=fake_run) as run, patch(
                "core.workshop.is_game_running", return_value=False
            ):
                ok, msg = download_workshop_item_manual(
                    "1234567", target, "depotdownloader", Path("DepotDownloader.exe"),
                    steam_username="user", staging_dir=staging_root,
                )
            self.assertTrue(ok, msg)
            self.assertTrue((target / "ws_1234567" / "manifest.xml").is_file())
            run.assert_called_once()

    def test_workshop_failed_download_does_not_replace_installed_mod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            target = t / "Mods"
            installed = target / "ws_1234567"
            installed.mkdir(parents=True)
            (installed / "manifest.xml").write_text("old", encoding="utf-8")
            stage_root = t / "downloads"

            def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                stage = Path(cmd[cmd.index("-dir") + 1])
                (stage / "manifest.xml").write_text("new", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 1, "", "failed")

            with patch("core.workshop.subprocess.run", side_effect=fake_run):
                ok, _ = download_workshop_item_manual(
                    "1234567", target, "depotdownloader", Path("DepotDownloader.exe"),
                    staging_dir=stage_root,
                )
            self.assertFalse(ok)
            self.assertEqual((installed / "manifest.xml").read_text(encoding="utf-8"), "old")


class TestCLISmoke(unittest.TestCase):
    def test_cli_scan_save_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_dir = t / "Game"
            mods_dir = game_dir / "Mods" / "demo"
            mods_dir.mkdir(parents=True)
            (mods_dir / "manifest.xml").write_text(
                "<Manifest><ID>demo</ID><Name>Demo</Name><GameVersions><Version>1.0.0</Version></GameVersions></Manifest>",
                encoding="utf-8",
            )
            save_dir = t / "Save"
            save_dir.mkdir()
            workshop_dir = t / "Workshop"
            workshop_dir.mkdir()
            (save_dir / "SaveFile.es3").write_text(
                json.dumps({"modOrder": {"__type": "list", "value": ["demo"]}}),
                encoding="utf-8",
            )

            base_cmd = [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-c",
                "import runpy,sys; from unittest.mock import patch; sys.argv.pop(0); "
                "patch('core.paths.is_game_running', return_value=False).start(); "
                "patch('core.es3_manager.is_game_running', return_value=False).start(); "
                "patch('core.paths.launch_game', side_effect=AssertionError('Test must not launch Steam')).start(); "
                "runpy.run_path(sys.argv[0], run_name='__main__')",
                str(ROOT / "main.py"),
                "--data-dir",
                str(t / "mgr"),
                "--game-dir",
                str(game_dir),
                "--save-dir",
                str(save_dir),
                "--workshop-dir",
                str(workshop_dir),
            ]
            out_scan = subprocess.check_output(base_cmd + ["scan"], text=True, encoding="utf-8")
            self.assertIn('"installed_count": 1', out_scan)

            out_save = subprocess.check_output(
                base_cmd + ["save-profile", "p1"], text=True, encoding="utf-8"
            )
            self.assertIn("已保存方案", out_save)

            out_list = subprocess.check_output(
                base_cmd + ["list-profiles"], text=True, encoding="utf-8"
            )
            self.assertIn("p1", out_list)
            save_file = save_dir / "SaveFile.es3"
            save_file.write_text('{"modOrder": [], "volume": 2}', encoding="utf-8")
            subprocess.check_output(base_cmd + ["apply-profile", "p1"], encoding="utf-8")
            result = inspect_save_file(save_file)
            self.assertEqual(result.active_mods, ["demo"])
            self.assertEqual(result.raw_data["volume"], 2)


if __name__ == "__main__":
    unittest.main()
