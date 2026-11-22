import asyncio
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.paths import APP_ID, GamePaths
from core.scanner import scan_installed_mods
from core.workshop import WorkshopItem
from core.workshop_versions import (
    VersionError, change_version_lock, clone_workshop, parse_acf, read_public_update_times, workshop_versions,
)
from webui.operations import Operations
from webui.server import create_app


class WorkshopVersionTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.workshop = root / "SteamLibrary" / "steamapps" / "workshop"
        self.content = self.workshop / "content" / APP_ID
        self.game = root / "Probably Stolen"
        self.mods = self.game / "Mods"
        self.mods.mkdir(parents=True)
        self.save = root / "SaveFile.es3"
        self.save_bytes = b'{"modOrder":{"value":["Exact.ID"]},"modTrustMac":{"value":"preserve"}}'
        self.save.write_bytes(self.save_bytes)
        self.paths = GamePaths(self.game, self.mods, self.content, root, self.save)
        self.id = "1234567890"
        self.other = "1111111111"
        self.source = self.content / self.id
        self.make_mod(self.source, "Exact.ID")
        self.make_mod(self.content / self.other, "No.Record")
        self.acf = self.workshop / f"appworkshop_{APP_ID}.acf"
        self.acf.write_text(f'''
            "AppWorkshop" {{
                "appid" "{APP_ID}" // Steam's app id
                "WorkshopItemsInstalled" {{
                    "{self.id}" {{ "Size" "4000" "timeupdated" "1700000000" "manifest" "8400000000000000" }}
                }}
                "WorkshopItemDetails" {{
                    "{self.id}" {{"timeupdated" "1700000050" "latest_timeupdated" "1700000100"}}
                    "{self.other}" {{"timeupdated" "1700000100"}}
                }}
            }}''', encoding="utf-8-sig")
        running = patch("core.workshop_versions.is_game_running", return_value=False)
        self.running = running.start()
        self.addCleanup(running.stop)

    def tearDown(self):
        self.assertEqual(self.save.read_bytes(), self.save_bytes)

    @staticmethod
    def make_mod(folder, mod_id):
        folder.mkdir(parents=True)
        (folder / "manifest.xml").write_text(
            f"<Manifest><ID>{mod_id}</ID><Name>{mod_id} name</Name><ModVersion>2.1</ModVersion></Manifest>",
            encoding="utf-8",
        )
        (folder / "Payload.bin").write_bytes(b"\x00\x01\xfe")

    def rows(self, online=False):
        return {row["id"]: row for row in workshop_versions(self.paths, online=online)["rows"]}

    def switch(self, action):
        return change_version_lock(self.paths, self.id, action, "Exact.ID")

    def test_real_acf_shape_uses_the_installed_timestamp_not_cached_latest(self):
        with patch("core.workshop_versions.fetch_workshop_details_by_ids", return_value=[
            WorkshopItem(self.id, "A", time_updated=1700000100),
            WorkshopItem(self.other, "B", time_updated=1700000100),
        ]) as steam:
            self.assertEqual(self.rows()[self.id]["public_state"], "unchecked")
            steam.assert_not_called()
            rows = self.rows(online=True)
        self.assertEqual(rows[self.id]["installed_time"], 1700000000)
        self.assertEqual(rows[self.id]["steam_manifest"], "8400000000000000")
        self.assertEqual(rows[self.id]["status"], "update")
        self.assertEqual(rows[self.other]["status"], "unknown")
        self.assertEqual(rows[self.other]["status_reason"], "missing_installed_time")

    def test_equal_older_missing_and_failed_public_lookup(self):
        for public, expected, state, reason in (
            (1700000000, "current", "available", None),
            (1699999999, "unknown", "available", "older_public_time"),
            (0, "unknown", "missing_time", None),
        ):
            with self.subTest(public=public), patch("core.workshop_versions.fetch_workshop_details_by_ids",
                    return_value=[WorkshopItem(self.id, "A", time_updated=public)]):
                rows = self.rows(online=True)
                self.assertEqual(rows[self.id]["status"], expected)
                self.assertEqual(rows[self.id]["public_state"], state)
                self.assertEqual(rows[self.id]["status_reason"], reason)
                self.assertEqual(rows[self.other]["public_state"], "unavailable")
        with patch("core.workshop_versions.fetch_workshop_details_by_ids", side_effect=TimeoutError):
            self.assertEqual(self.rows(online=True)[self.id]["public_state"], "failed")
            self.assertTrue(workshop_versions(self.paths, online=True)["notes"])

    def test_failed_batch_does_not_hide_other_batches(self):
        with patch("core.workshop_versions._VERSION_BATCH_SIZE", 1), \
                patch("core.workshop_versions.fetch_workshop_details_by_ids", side_effect=[
                    TimeoutError(), [WorkshopItem(self.id, "A", time_updated=1700000100)],
                ]):
            rows = self.rows(online=True)
        self.assertEqual(rows[self.other]["public_state"], "failed")
        self.assertEqual(rows[self.id]["status"], "update")

    def test_parse_nested_tree_escaped_strings_and_structural_failures(self):
        self.assertEqual(parse_acf(r'"AppWorkshop" {"nest" {k "a\\b\"c"}}')["appworkshop"]["nest"]["k"], 'a\\b"c')
        for bad in ('"a" {', '"a" "b" }', '"a" "b" "A" "c"', '"a" "b', '"a" {"b"}'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_acf(bad)
        self.acf.write_text('"AppWorkshop" {"appid" "42"}', encoding="utf-8")
        self.assertIsNone(self.rows()[self.id]["installed_time"])

    def test_lock_unlock_restore_preserves_bytes_and_switches_resolution(self):
        acf_data = self.acf.read_bytes()
        manifest = (self.source / "manifest.xml").read_bytes()
        clone = self.switch("lock")
        self.assertEqual((clone / "manifest.xml").read_bytes(), manifest)
        self.assertEqual(scan_installed_mods(self.paths)["Exact.ID"].folder, clone)
        locked = self.rows()[self.id]
        self.assertEqual((locked["lock_state"], locked["locked_time"], locked["locked_version"]), ("locked", 1700000000, "2.1"))
        self.assertEqual(set(path.name for path in clone.iterdir()), {"manifest.xml", "Payload.bin"})
        (clone / "user-change.txt").write_bytes(b"preserve local changes")
        (self.source / "Payload.bin").write_bytes(b"new Steam content")
        isolated = self.switch("unlock")
        self.assertFalse(clone.exists())
        self.assertFalse(isolated.is_relative_to(self.mods))
        self.assertEqual(scan_installed_mods(self.paths)["Exact.ID"].folder, self.source)
        self.assertEqual(self.rows()[self.id]["lock_state"], "quarantined")
        self.assertEqual(self.switch("restore"), clone)
        self.assertEqual((clone / "Payload.bin").read_bytes(), b"\x00\x01\xfe")
        self.assertEqual((clone / "user-change.txt").read_bytes(), b"preserve local changes")
        self.assertEqual((self.source / "Payload.bin").read_bytes(), b"new Steam content")
        self.assertEqual(self.acf.read_bytes(), acf_data)

    def test_never_overwrite_other_local_id_including_case_variants(self):
        other = self.mods / "unrelated-folder-name"
        self.make_mod(other, "exact.id")
        self.assertTrue(self.rows()[self.id]["local_conflict"])
        with self.assertRaisesRegex(VersionError, "相同 <ID>"):
            self.switch("lock")
        other.rename(self.game / "moved")
        self.switch("lock")
        quarantine = self.switch("unlock")
        (self.game / "moved").rename(other)
        with self.assertRaisesRegex(VersionError, "相同 <ID>"):
            self.switch("restore")
        self.assertTrue(quarantine.is_dir())
        self.assertTrue(other.is_dir())

    def test_shared_clone_prepare_is_staged_and_not_owned_by_locking(self):
        mod = scan_installed_mods(self.paths)["Exact.ID"]

        def prepare(staged):
            self.assertFalse(staged.is_relative_to(self.mods))
            self.assertFalse(staged.is_relative_to(self.content))
            now = scan_installed_mods(self.paths)["Exact.ID"]
            self.assertEqual(now.source, "workshop")
            (staged / "zh-CN.csv").write_bytes(b"added translation")

        clone = clone_workshop(self.paths, mod, prepare=prepare)
        self.assertTrue((clone / "zh-CN.csv").exists())
        self.assertFalse((mod.folder / "zh-CN.csv").exists())
        self.assertEqual(self.rows()[self.id]["lock_state"], "none")
        with self.assertRaises(VersionError):
            self.switch("unlock")
        self.assertTrue(clone.is_dir())

    def test_failed_prepare_id_mutation_or_game_start_never_publishes(self):
        mod = scan_installed_mods(self.paths)["Exact.ID"]

        def bad_callback(staged):
            (staged / "extra").touch()
            raise RuntimeError("translation failed")

        with self.assertRaisesRegex(RuntimeError, "translation failed"):
            clone_workshop(self.paths, mod, prepare=bad_callback)
        with self.assertRaisesRegex(VersionError, "保留原样"):
            clone_workshop(self.paths, mod, prepare=lambda staged: (staged / "manifest.xml").write_text(
                "<Manifest><ID>Different</ID></Manifest>", encoding="utf-8"))
        with self.assertRaisesRegex(VersionError, "退出游戏"):
            clone_workshop(self.paths, mod, prepare=lambda _: setattr(self.running, "return_value", True))
        self.assertEqual(scan_installed_mods(self.paths)["Exact.ID"].source, "workshop")
        self.assertEqual(list(self.mods.glob("psmm-workshop-*")), [])

    def test_refuse_source_changed_during_clone_or_other_id(self):
        mod = scan_installed_mods(self.paths)["Exact.ID"]
        with self.assertRaisesRegex(VersionError, "Steam 更新"):
            clone_workshop(self.paths, mod, prepare=lambda _: (self.source / "Payload.bin").write_bytes(b"updated"))
        self.assertFalse(list(self.mods.glob("psmm-workshop-*")))
        with self.assertRaisesRegex(VersionError, "条目已变更"):
            change_version_lock(self.paths, self.id, "lock", "Different")

    def test_replaced_lock_directory_is_not_moved(self):
        clone = self.switch("lock")
        clone.rename(self.game / "existing-clone")
        self.make_mod(clone, "Exact.ID")
        with self.assertRaisesRegex(VersionError, "已被替换"):
            self.switch("unlock")
        self.assertTrue(clone.is_dir())

    def test_source_and_destination_junctions_or_symlinks_are_rejected(self):
        mod = scan_installed_mods(self.paths)["Exact.ID"]

        def link(link_path, target):
            if os.name != "nt":
                link_path.symlink_to(target, target_is_directory=True)
                return link_path.unlink
            escaped_link = str(link_path).replace("'", "''")
            escaped_target = str(target).replace("'", "''")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"New-Item -ItemType Junction -Path '{escaped_link}' -Target '{escaped_target}' | Out-Null"],
                           check=True, capture_output=True)
            return link_path.rmdir

        remove = link(self.source / "linked", self.game)
        try:
            with self.assertRaisesRegex(VersionError, "链接或重解析点"):
                clone_workshop(self.paths, mod)
        finally:
            remove()
        self.mods.rename(self.game / "real-mods")
        remove = link(self.mods, self.game / "real-mods")
        try:
            with self.assertRaisesRegex(VersionError, "链接或重解析点"):
                clone_workshop(self.paths, mod)
        finally:
            remove()

    def test_workshop_overlap_is_rejected_before_write(self):
        dangerous = GamePaths(self.game, self.mods, self.game.parent)
        with self.assertRaisesRegex(VersionError, "重叠"):
            clone_workshop(dangerous, scan_installed_mods(self.paths)["Exact.ID"])
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_unsubscribed_mod_is_still_visible_and_can_be_restored(self):
        self.switch("lock")
        self.source.rename(self.workshop / "unsubscribed")
        self.assertFalse(self.rows()[self.id]["installed"])
        self.assertEqual(self.rows()[self.id]["action"], "unlock")
        self.switch("unlock")
        self.assertEqual(self.rows()[self.id]["action"], "restore")
        self.assertTrue(self.switch("restore").is_dir())

    def test_http_workflow_is_loopback_guarded_and_requires_confirmation(self):
        app = create_app(self.game.parent / "manager")
        url = "/api/workshop/versions"

        async def request(path, *, data=None, marker=True):
            sent = []
            content = b"" if data is None else json.dumps(data).encode()
            headers = [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")]
            if marker:
                headers.append((b"x-psmm-request", b"1"))

            async def receive():
                return {"type": "http.request", "body": content}

            async def send(event):
                sent.append(event)

            await app({"type": "http", "method": "GET" if data is None else "POST", "scheme": "http",
                       "path": path, "query_string": b"", "headers": headers}, receive, send)
            return sent[0]["status"], json.loads(sent[-1]["body"])

        async def flow():
            data = {"id": self.id, "mod_id": "Exact.ID", "action": "lock", "confirm": False}
            self.assertEqual((await request(url, marker=False))[0], 403)
            self.assertEqual((await request(url))[0], 200)
            self.assertEqual((await request(f"{url}/change", data=data))[0], 409)
            self.assertEqual(self.rows()[self.id]["lock_state"], "none")
            for action, state in (("lock", "locked"), ("unlock", "quarantined"), ("restore", "locked")):
                status, result = await request(f"{url}/change", data=dict(data, action=action, confirm=True))
                self.assertEqual(status, 200, result)
                _, overview = await request(url)
                row = next(row for row in overview["rows"] if row["id"] == self.id)
                self.assertEqual(row["lock_state"], state)
                self.assertTrue(Path(result["path"]).is_dir())

        with patch.object(Operations, "paths", return_value=self.paths):
            asyncio.run(flow())

    def test_mod_state_retains_only_successful_public_dates_across_restarts(self):
        data_dir = self.game.parent / "manager"
        self.make_mod(self.mods / "Local", "Local.Only")

        async def read(app, path, query=b""):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(event):
                sent.append(event)

            await app({"type": "http", "method": "GET", "scheme": "http", "path": path, "query_string": query,
                       "headers": [(b"host", b"127.0.0.1"), (b"x-psmm-request", b"1")]}, receive, send)
            self.assertEqual(sent[0]["status"], 200, sent)
            return json.loads(sent[-1]["body"])

        async def state_times(app):
            result = await read(app, "/api/state")
            return {mod["id"]: mod["workshop_public_time"] for mod in result["mods"]}

        async def flow():
            app = create_app(data_dir)
            url = "/api/workshop/versions"
            with patch("core.workshop_versions.fetch_workshop_details_by_ids", return_value=[
                WorkshopItem(self.id, "A", time_updated=1700000050),
                WorkshopItem(self.other, "B", time_updated=0),
            ]) as steam:
                await read(app, url)
                self.assertEqual(await state_times(app), {"Exact.ID": None, "No.Record": None, "Local.Only": None})
                steam.assert_not_called()
                await read(app, url, b"online=true")
                steam.assert_called_once()
            self.assertEqual(read_public_update_times(data_dir), {self.id: 1700000050})
            self.switch("lock")
            app = create_app(data_dir)
            expected = {"Exact.ID": 1700000050, "No.Record": None, "Local.Only": None}
            self.assertEqual(await state_times(app), expected)
            with patch("core.workshop_versions.fetch_workshop_details_by_ids", side_effect=TimeoutError):
                failed = await read(app, url, b"online=true")
            self.assertTrue(all(row["public_time"] is None and row["status"] == "unknown" for row in failed["rows"]))
            self.assertEqual(await state_times(app), expected)
            with patch("core.workshop_versions.fetch_workshop_details_by_ids", return_value=[
                WorkshopItem(self.id, "A", time_updated=0),
                WorkshopItem(self.other, "B", time_updated=1700000100),
            ]):
                await read(app, url, b"online=true")
            self.assertEqual(await state_times(app), expected | {"No.Record": 1700000100})

        with patch.object(Operations, "paths", return_value=self.paths):
            asyncio.run(flow())
