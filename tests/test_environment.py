"""Isolated preview fixtures. No real settings, game data, or binaries are modified."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import environment as env
from core.paths import APP_ID, GAME_EXE_NAME, GAME_FOLDER_NAME
from webui.environment_routes import environment_router
from webui.server import LoopbackGuard


class TestEnvironment(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.steam = self.root / "Steam"
        self.library = self.root / "Library"
        self.game = self.library / "steamapps/common" / GAME_FOLDER_NAME
        self.workshop = self.library / "steamapps/workshop/content" / APP_ID
        self.save = self.root / "Save"
        self.tools = self.root / "tools"
        for directory in (self.game / "Mods", self.workshop, self.save / "ModConfigs"):
            directory.mkdir(parents=True)
        self.file(self.steam / "steam.exe")
        self.file(self.game / GAME_EXE_NAME)
        self.file(self.save / "SaveFile.es3")
        self.file(self.save / "saves_index.es3")
        self.file(self.tools / "steamcmd.exe")
        self.file(self.game / f"{GAME_FOLDER_NAME}_Data/Managed/0Harmony.dll")
        vdf = self.steam / "steamapps/libraryfolders.vdf"
        self.file(vdf)
        escaped_library = str(self.library).replace("\\", "\\\\")
        vdf.write_text(f'"libraryfolders" {{ "1" {{ "path" "{escaped_library}" }} }}', encoding="utf-8")

        for replacement in (
            patch.object(env, "_steam_candidates", return_value=[self.steam]),
            patch.object(env, "_default_save", return_value=self.save),
            patch.object(env, "_downloader_candidates", return_value=[self.tools / "steamcmd.exe"]),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    @staticmethod
    def file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    def test_automatic_inventory_resolves_steam_library_and_tools(self):
        result = env.preview_environment()
        self.assertEqual(result.downloader_kind, "steamcmd")
        for key, path in {
            "game_dir": self.game, "game_exe": self.game / GAME_EXE_NAME,
            "steam_exe": self.steam / "steam.exe", "downloader_path": self.tools / "steamcmd.exe",
            "local_mods_dir": self.game / "Mods", "workshop_dir": self.workshop,
            "save_dir": self.save, "save_file": self.save / "SaveFile.es3",
            "saves_index": self.save / "saves_index.es3", "mod_configs_dir": self.save / "ModConfigs",
        }.items():
            with self.subTest(key=key):
                self.assertEqual(result.paths[key], env.PathPreview(str(path), "auto", "found"))
        markers = {row.id: row for row in result.markers}
        self.assertEqual(markers["harmony"].status, "found")
        self.assertTrue(all(markers[key].status == "missing" for key in ("bepinex", "melonloader", "doorstop")))

    def test_custom_paths_do_not_fall_back_and_game_requires_executable(self):
        unverified_game = self.root / "Other Game"
        unverified_game.mkdir()
        self.file(unverified_game / "UnityCrashHandler64.exe")
        missing = self.root / "Missing"
        unknown = self.tools / "anything.exe"
        self.file(unknown)
        result = env.preview_environment(game_dir=f'"{unverified_game}"', save_dir=str(missing),
                                         workshop_dir=str(missing), downloader_path=str(unknown))
        self.assertEqual(result.paths["game_dir"].reason, "game_exe_unverified")
        self.assertEqual(result.paths["game_exe"].path, str(unverified_game / GAME_EXE_NAME))
        for key in ("game_dir", "game_exe", "save_dir", "save_file", "workshop_dir"):
            self.assertEqual((result.paths[key].mode, result.paths[key].status), ("custom", "missing"))
        self.assertEqual(result.paths["downloader_path"].reason, "unrecognized_downloader")
        self.assertIsNone(result.downloader_kind)

    def test_markers_need_specific_files_not_directory_or_generic_proxy_names(self):
        (self.game / "BepInEx/core").mkdir(parents=True)
        (self.game / "MelonLoader/net6").mkdir(parents=True)
        for generic in ("Harmony.dll", "winhttp.dll", "version.dll"):
            self.file(self.game / generic)
        first = env.preview_environment(game_dir=str(self.game))
        self.assertTrue(all(row.status == "missing" for row in first.markers if row.id != "harmony"))

        files = {
            "bepinex": self.game / "BepInEx/core/BepInEx.Unity.Mono.Preloader.dll",
            "melonloader": self.game / "MelonLoader/net6/MelonLoader.dll",
            "doorstop": self.game / "doorstop_config.ini",
        }
        for path in files.values():
            self.file(path)
        second = env.preview_environment(game_dir=str(self.game))
        for row in second.markers:
            if row.id in files:
                self.assertEqual((row.status, row.evidence), ("found", [str(files[row.id])]))

    def test_unc_and_unsafe_syntax_are_rejected_before_filesystem_access(self):
        with patch.object(env, "_node_type", side_effect=AssertionError("filesystem probe")):
            for raw, reason in (
                (r"\\remote\library\Game", "local_only"), ("//remote/library/Game", "local_only"),
                (r"\\?\C:\Game", "local_only"), (r"\\.\C:\Game", "local_only"),
                (r"C:Game", "invalid_path"), (str(self.root / ".." / "Game"), "invalid_path"),
            ):
                with self.subTest(raw=raw):
                    result = env._LocalProbe().check(raw, "dir", "custom")
                    self.assertEqual((result.status, result.reason), ("skipped", reason))

    @unittest.skipUnless(os.name == "nt", "Windows drive syntax")
    def test_mapped_dos_devices_and_reserved_names_never_reach_filesystem(self):
        with patch.object(env, "_node_type", side_effect=AssertionError("filesystem probe")), \
             patch.object(env, "_drive_target", return_value=r"\Device\Mup\server\share") as mapping:
            probe = env._LocalProbe()
            self.assertEqual(probe.check(r"Z:\Game", "dir").reason, "local_only")
            for raw in (r"C:\Game\NUL.dll", r"C:\Game:stream", r"C:\Game.\game.exe"):
                self.assertEqual(probe.check(raw, "file").reason, "invalid_path")
            mapping.assert_called_once_with("Z:")

    def test_blocked_ancestor_prevents_deeper_access(self):
        blocked = self.root / "Potential junction"
        scanned = []
        real = env._node_type

        def attributes(path):
            scanned.append(path)
            return None if path == blocked else real(path)

        with patch.object(env, "_node_type", side_effect=attributes):
            result = env._LocalProbe().check(blocked / "game.exe", "file", "custom")
        self.assertEqual((result.status, result.reason), ("skipped", "link_or_special"))
        self.assertEqual(scanned[-1], blocked)

    def test_post_contract_and_loopback_guard(self):
        app = FastAPI()
        app.add_middleware(LoopbackGuard)
        app.include_router(environment_router(), prefix="/api")
        draft = {"game_dir": str(self.game), "workshop_dir": "", "save_dir": "", "downloader_path": ""}

        async def post(payload, *, marker=True, origin=None):
            body = json.dumps(payload).encode("utf-8")
            headers = [(b"host", b"127.0.0.1:8765"), (b"content-type", b"application/json")]
            if marker:
                headers.append((b"x-psmm-request", b"1"))
            if origin:
                headers.append((b"origin", origin))
            scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
                     "path": "/api/environment/preview", "headers": headers, "query_string": b""}
            events = []

            async def receive():
                return {"type": "http.request", "body": body}

            async def send(event):
                events.append(event)

            await app(scope, receive, send)
            return events[0]["status"], json.loads(b"".join(event.get("body", b"") for event in events))

        status, data = asyncio.run(post(draft))
        self.assertEqual(status, 200)
        self.assertEqual(data["paths"]["game_dir"]["mode"], "custom")
        self.assertEqual(data["paths"]["workshop_dir"]["path"], str(self.workshop))
        self.assertEqual(data["downloader_kind"], "steamcmd")
        self.assertEqual(asyncio.run(post(draft, marker=False))[0], 403)
        self.assertEqual(asyncio.run(post(draft, origin=b"https://example.invalid"))[0], 403)
        for invalid in ({**draft, "extra": "value"}, {**draft, "save_dir": 3}, {**draft, "game_dir": "a\0b"}, {}):
            self.assertEqual(asyncio.run(post(invalid))[0], 422)
        self.assertEqual((self.save / "SaveFile.es3").read_bytes(), b"fixture")
        self.assertEqual({p.name for p in self.save.iterdir()}, {"ModConfigs", "SaveFile.es3", "saves_index.es3"})

    def test_desktop_picker_checks_request_before_showing_native_dialog(self):
        app = FastAPI()
        app.add_middleware(LoopbackGuard)
        picker = Mock(side_effect=[str(self.game), None])
        app.include_router(environment_router(picker), prefix='/api')
        with TestClient(app, base_url='http://127.0.0.1') as client:
            url = '/api/environment/pick'
            headers = {'X-PSMM-Request': '1'}
            self.assertEqual(client.post(url, json={'field': 'game_dir'}).status_code, 403)
            self.assertEqual(client.post(url, json={'field': 'game_dir', 'path': str(self.game)}, headers=headers).status_code, 422)
            picker.assert_not_called()
            self.assertEqual(client.post(url, json={'field': 'game_dir'}, headers=headers).json(), {'path': str(self.game)})
            self.assertEqual(client.post(url, json={'field': 'downloader_path'}, headers=headers).json(), {'path': None})
            self.assertEqual([call.args for call in picker.call_args_list], [(False,), (True,)])


if __name__ == "__main__":
    unittest.main()
