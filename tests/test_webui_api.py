"""Local API integration tests. All file writes stay in temporary fixtures."""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import socket
import tempfile
import threading
import unittest
import zlib
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn

from core.file_ops import write_lock
from core.settings import AppSettings
from core.workshop import WorkshopItem, WorkshopPage
from webui.server import create_app, frontend_dist, run


@dataclass
class Response:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.text)


class TestWebUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data_dir = self.root / "manager"
        self.game = self.root / "Game"
        self.save_dir = self.root / "Save"
        self.workshop = self.root / "Workshop"
        self.config = self.save_dir / "ModConfigs" / "child.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"enabled": true, "count": 3, "speed": 1.5}', encoding="utf-8")
        self.workshop.mkdir()
        for mod_id, prereq in [("base", ""), ("child", "<Prerequisites><Mod>base</Mod></Prerequisites>")]:
            folder = self.game / "Mods" / mod_id
            folder.mkdir(parents=True)
            (folder / "manifest.xml").write_text(f"<Manifest><ID>{mod_id}</ID>{prereq}</Manifest>", encoding="utf-8")
        (self.game / "Probably Stolen.exe").touch()
        self.loot = self.game / "Mods" / "child" / "LootTables" / "drop.json"
        self.loot.parent.mkdir()
        self.loot.write_text('{"patches": []}', encoding="utf-8")
        self.save = self.save_dir / "SaveFile.es3"
        self.original = b'{"modOrder":{"value":["child","base"],"__type":"list"},"volume":2}'
        self.save.write_bytes(self.original)
        self.settings_path = self.data_dir / "settings.json"
        self.settings = AppSettings(game_dir=str(self.game), save_dir=str(self.save_dir),
                                    workshop_dir=str(self.workshop), steam_api_key="fixture-secret",
                                    steam_username="fixture-user")
        self.settings.save(self.settings_path)
        self.assets = self.root / "frontend" / "dist"
        self.assets.mkdir(parents=True)
        (self.assets / "index.html").write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
        (self.assets / "asset.js").write_text("console.log('fixture');", encoding="utf-8")
        for target, value in {
            "core.paths._default_steam_dirs": [],
            "core.paths.default_save_data_dir": self.save_dir,
            "core.es3_manager.is_game_running": False,
            "webui.operations.is_game_running": False,
            "webui.server.is_game_running": False,
            "webui.server.launch_game": (False, "Unit tests never launch Steam."),
            "core.test_session.launch_game": (False, "Unit tests never launch Steam."),
            "core.test_session.default_save_data_dir": self.save_dir,
        }.items():
            replacement = patch(target, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        self.port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            create_app(self.data_dir, assets_dir=self.assets), access_log=False,
            log_config=None, proxy_headers=False, ws="none", timeout_graceful_shutdown=2,
        ))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()

        def stop_server():
            server.should_exit = True
            worker.join(4)
            self.assertFalse(worker.is_alive(), "Fixture server did not exit")

        self.addCleanup(stop_server)
        for _ in range(100):
            if server.started:
                break
            if not worker.is_alive():
                self.fail("Fixture server exited before startup")
            await asyncio.sleep(0.02)
        else:
            self.fail("Fixture server did not start")

    def _http(self, method, path, payload, headers, params):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        if params:
            path += "?" + urlencode(params)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"X-PSMM-Request": "1"}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        try:
            connection.request(method, path, body=data, headers=request_headers)
            response = connection.getresponse()
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()},
                            response.read())
        finally:
            connection.close()

    async def test_workshop_inventory_tracks_physical_copies_and_rejects_duplicate_download(self):
        for root, directory, manifest in (
            (self.workshop, "180019991", "<Manifest><ID>shared</ID><Name>Steam copy</Name></Manifest>"),
            (self.game / "Mods", "ws_180019992", "<Manifest><ID>shared</ID><Name>Local copy</Name></Manifest>"),
            (self.workshop, "180019993", "<Manifest><ID>"),
            (self.workshop, "180019994", None),
            (self.game / "Mods", "180019995", "<Manifest><ID>unrelated</ID></Manifest>"),
        ):
            folder = root / directory
            folder.mkdir()
            if manifest is not None:
                (folder / "manifest.xml").write_text(manifest, encoding="utf-8")

        response = await self.request("GET", "/api/state")
        inventory = {row["workshop_id"]: row for row in response.json()["workshop_downloads"]}
        self.assertEqual({key: row["name"] for key, row in inventory.items()},
                         {"180019991": "Steam copy", "180019992": "Local copy"})

        folder = self.workshop / "180019994"
        (folder / "manifest.xml").write_text("<Manifest><ID>arrived</ID></Manifest>", encoding="utf-8")
        response = await self.request("POST", "/api/refresh", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("180019994", {row["workshop_id"] for row in response.json()["workshop_downloads"]})

        with patch("webui.operations.find_downloader_executable") as find:
            for item_id in ("180019991", "180019992"):
                response = await self.request("POST", "/api/workshop/download",
                                              json={"id": item_id, "confirm_login": True})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("已下载", response.text)
            find.assert_not_called()

    async def request(self, method, path, *, json=None, headers=None, params=None):
        return await asyncio.wait_for(
            asyncio.to_thread(self._http, method, path, json, headers, params), timeout=5,
        )

    async def config_document(self, label):
        files = (await self.request("GET", "/api/config/files?mod_id=child")).json()["files"]
        file_id = next(file["id"] for file in files if label in file["label"])
        doc = (await self.request("GET", "/api/config/file", params={"mod_id": "child", "id": file_id})).json()
        return file_id, doc

    async def test_loopback_origin_request_header_static_and_docs(self):
        self.assertIn("<title>fixture</title>", (await self.request("GET", "/")).text)
        self.assertIn("console.log", (await self.request("GET", "/asset.js")).text)
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual((await self.request("GET", path)).status_code, 404)
        for scheme_host in ("127.0.0.1:5173", "localhost:5173"):
            # Vite preserves the original Host rather than rewriting it to :8765.
            allowed = await self.request("POST", "/api/refresh", json={}, headers={
                "Host": scheme_host, "Origin": "http://" + scheme_host, "Sec-Fetch-Site": "same-origin",
            })
            self.assertEqual(allowed.status_code, 200, allowed.text)
            self.assertEqual(allowed.headers["cache-control"], "no-store")
        for headers in (
            {"Host": "outside.invalid", "X-Forwarded-Host": "127.0.0.1:8765"},
            {"Host": "127.0.0.1:8765@outside.invalid"}, {"Host": "localhost:0"},
            {"Origin": "http://127.0.0.1:5173"}, {"Origin": "http://localhost:8765"},
            {"Origin": "null"}, {"X-PSMM-Request": ""}, {"Sec-Fetch-Site": "same-site"},
        ):
            with self.subTest(headers=headers):
                response = await self.request("GET", "/api/state", headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertNotIn("access-control-allow-origin", response.headers)
        response = await self.request("GET", "/", headers={"Host": "outside.invalid"})
        self.assertEqual(response.status_code, 403)
        response = await self.request("OPTIONS", "/api/settings", headers={"Origin": "https://outside.invalid"})
        self.assertEqual(response.status_code, 403)
        response = await self.request("GET", "/api/unknown")
        self.assertEqual(response.status_code, 404)

    async def test_settings_secret_preserve_clear_and_validation(self):
        response = await self.request("GET", "/api/settings")
        public = response.json()
        self.assertNotIn("fixture-secret", response.text)
        self.assertNotIn("steam_api_key", public)
        self.assertTrue(public.pop("has_steam_api_key"))
        public["game_version"] = "1.0.1"
        self.assertEqual((await self.request("PUT", "/api/settings", json=public)).status_code, 200)
        self.assertEqual(AppSettings.load(self.settings_path).steam_api_key, "fixture-secret")
        invalid = await self.request("PUT", "/api/settings", json={**public, "steam_api_key": {"secret": "other-secret"}})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("other-secret", invalid.text)
        with patch("webui.server.query_workshop_via_api", side_effect=RuntimeError("URL?key=fixture-secret")), \
                patch("webui.server.browse_workshop_public_page", side_effect=TimeoutError):
            response = await self.request("GET", "/api/workshop/search")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("fixture-secret", response.text)
        self.assertEqual((await self.request("PUT", "/api/settings", json={**public, "steam_api_key": ""})).status_code, 200)
        self.assertFalse((await self.request("GET", "/api/settings")).json()["has_steam_api_key"])

    async def test_undocumented_config_display_defaults_off_and_persists(self):
        legacy = json.loads(self.settings_path.read_text(encoding="utf-8"))
        legacy.pop("show_undocumented_config", None)
        self.settings_path.write_text(json.dumps(legacy), encoding="utf-8")
        public = (await self.request("GET", "/api/settings")).json()
        self.assertIs(public.pop("show_undocumented_config"), False)
        public.pop("has_steam_api_key")
        self.assertEqual((await self.request("PUT", "/api/settings", json={**public, "show_undocumented_config": "true"})).status_code, 422)
        self.assertEqual((await self.request("PUT", "/api/settings", json={**public, "show_undocumented_config": True})).status_code, 200)
        self.assertEqual((await self.request("PUT", "/api/settings", json=public)).status_code, 200)
        self.assertIs(AppSettings.load(self.settings_path).show_undocumented_config, True)
        self.assertIs((await self.request("GET", "/api/settings")).json()["show_undocumented_config"], True)
        self.assertEqual((await self.request("PUT", "/api/settings", json={**public, "show_undocumented_config": False})).status_code, 200)
        self.assertIs(AppSettings.load(self.settings_path).show_undocumented_config, False)

    async def test_public_search_falls_back_without_disclosing_key(self):
        cover = "https://images.steamusercontent.com/ugc/123456/ABC/"
        item = WorkshopItem("1234567890", "Fixture", preview_url=cover)
        with patch("webui.server.query_workshop_via_api") as backup, \
                patch("webui.server.browse_workshop_public_page", return_value=WorkshopPage([item], 65, True)) as browse:
            response = await self.request("GET", "/api/workshop/search?q=fixture&page=2")
        backup.assert_not_called()
        browse.assert_called_once_with(search_text="fixture", required_tag="", page=2, sort_by='trend')
        self.assertEqual(response.json()["source"], "steam_public")
        self.assertEqual((response.json()['page'], response.json()['total'], response.json()['has_next']), (2, 65, True))
        cached_url = response.json()["items"][0]["preview_url"]
        self.assertRegex(cached_url, r"^/api/workshop/cover\?id=1234567890&v=[a-f0-9]{64}$")
        self.assertNotIn(cover, response.text)
        self.assertNotIn("fixture-secret", response.text)

        with patch("webui.server.browse_workshop_public_page", side_effect=TimeoutError), \
                patch("webui.server.query_workshop_via_api", side_effect=TimeoutError), \
                patch("webui.server.fetch_workshop_details_by_ids", return_value=[item]) as fetch:
            failed = await self.request("GET", "/api/workshop/search?q=fixture")
            lookup = await self.request("GET", "/api/workshop/lookup?ids=1234567890")
            for _ in range(2):
                covers = await self.request("GET", "/api/workshop/covers?ids=1234567890")
                self.assertEqual(covers.json(), {"covers": {item.published_file_id: cached_url}})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(lookup.json()["source"], "steam_details")
        self.assertEqual(fetch.call_count, 1)  # The cover batch reuses known metadata.

    async def test_removed_translation_routes_leave_workshop_text_unchanged(self):
        item = WorkshopItem('1234567890', 'First Mod', description='Original description')
        with patch('webui.server.fetch_workshop_details_by_ids', return_value=[item]):
            lookup = await self.request('GET', '/api/workshop/lookup?ids=1234567890&translate=true')
        self.assertEqual(lookup.json()['items'][0]['title'], 'First Mod')
        self.assertEqual(lookup.json()['items'][0]['description'], 'Original description')
        self.assertNotIn('translation_notice', lookup.json())
        for path in ('/api/localization', '/api/localization/providers', '/api/localization/preview'):
            self.assertEqual((await self.request('GET', path)).status_code, 404)

    async def test_search_api_is_a_fallback_and_pagination_can_keep_its_source(self):
        item = WorkshopItem('1234567890', 'Fixture')
        with patch('webui.server.browse_workshop_public_page', side_effect=TimeoutError) as public, \
                patch('webui.server.query_workshop_via_api', return_value=([item], 61)) as backup:
            first = await self.request('GET', '/api/workshop/search?q=fixture')
            second = await self.request('GET', '/api/workshop/search?q=fixture&page=2&source=steam_api')
        self.assertEqual(first.json()['source'], 'steam_api')
        self.assertEqual((second.json()['page'], second.json()['has_next']), (2, True))
        self.assertEqual(public.call_count, 1)
        self.assertEqual(backup.call_args.kwargs['page'], 2)

    async def test_local_cover_route_only_serves_known_raster_files(self):
        path = self.loot.parent.parent / "cover.png"
        path.write_bytes(b"<script>not an image</script>")
        self.assertEqual((await self.request("GET", "/api/mods/cover?id=child")).status_code, 404)
        png = b"\x89PNG\r\n\x1a\nfixture"
        path.write_bytes(png)
        state = (await self.request("GET", "/api/state")).json()
        self.assertTrue(next(m for m in state["mods"] if m["id"] == "child")["local_cover"])
        image = await self.request("GET", "/api/mods/cover?id=child")
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertEqual(image.content, png)
        self.assertEqual((await self.request("GET", "/api/mods/cover?id=../cover.png")).status_code, 404)

    async def test_config_metadata_is_read_only_and_unknown_keys_are_not_guessed(self):
        entries = [{"key": "enabled", "value": False, "displayName": "测试标签", "comment": "作者注释"},
                   {"key": "mystery", "value": 42}]
        self.config.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        folder = self.loot.parent.parent
        (folder / "README.md").write_text("作者手册 <script>not HTML</script>", encoding="utf-8")
        result = (await self.request("GET", "/api/config/files?mod_id=child")).json()
        self.assertEqual(result["readme"], {"name": "README.md", "text": "作者手册 <script>not HTML</script>"})
        self.assertEqual({file["filename"] for file in result["files"]}, {"child.json", "drop.json"})
        self.assertIn((await self.request("POST", "/api/config/ai-chat", json={})).status_code, {404, 405})
        file_id, form = await self.config_document("ModConfig")
        self.assertEqual(form["entries"][0]["description"], "作者注释")
        self.assertEqual(form["entries"][0]["help_source"], "配置文件")
        self.assertEqual(form["entries"][1]["description"], "")
        response = await self.request("PUT", "/api/config/file", json={
            "mod_id": "child", "id": file_id, "revision": form["revision"], "values": {"enabled": True},
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(self.config.read_bytes()), {"entries": [{**entries[0], "value": True}, entries[1]]})

    async def test_workshop_open_requires_a_real_mapping_from_scan(self):
        with patch("webui.management_routes.open_workshop_page_in_steam") as launch:
            self.assertEqual((await self.request("POST", "/api/mods/open", json={"id": "child", "workshop": True})).status_code, 404)
            launch.assert_not_called()
            item = self.workshop / "180019999"
            item.mkdir()
            (item / "manifest.xml").write_text("<Manifest><ID>named.id</ID><Name>中文名</Name></Manifest>", encoding="utf-8")
            self.assertEqual((await self.request("POST", "/api/mods/open", json={"id": "named.id", "workshop": True})).status_code, 200)
            launch.assert_called_once_with("180019999")

    async def test_order_preview_diagnostics_and_exact_save_revision(self):
        state = (await self.request("GET", "/api/state")).json()
        self.assertTrue(state["game_ready"] and state["save_ready"])
        self.assertTrue(next(mod for mod in state["mods"] if mod["id"] == "child")["configurable"])
        order = {"order": state["order"]}
        preview = await self.request("POST", "/api/order/preview", json=order)
        self.assertEqual(preview.json(), {"order": ["base", "child"], "warnings": []})
        diag = await self.request("POST", "/api/order/diagnose", json=order)
        self.assertEqual(diag.json()["issues"][0]["severity"], "warning")
        edit = {"order": ["base"], "revision": state["revision"]}
        self.save.write_bytes(self.original.replace(b'"volume":2', b'"volume":9'))
        conflict = await self.request("POST", "/api/order", json=edit)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual((await self.request("GET", "/api/backups")).json()["backups"], [])
        edit["revision"] = (await self.request("POST", "/api/refresh", json={})).json()["revision"]
        with write_lock(self.save_dir):
            locked = await self.request("POST", "/api/order", json=edit)
        self.assertEqual(locked.status_code, 409)
        response = await self.request("POST", "/api/order", json=edit)
        self.assertEqual(response.status_code, 200, response.text)
        live = json.loads(self.save.read_bytes())
        self.assertEqual(live, {"modOrder": {"value": ["base"], "__type": "list"}, "volume": 9})
        names = (await self.request("GET", "/api/backups")).json()["backups"]
        self.assertEqual(len(names), 1)
        self.assertIn(b'"volume":9', (self.data_dir / "backups" / names[0]).read_bytes())
        for bad in (1, "true", False):
            self.assertEqual((await self.request("POST", "/api/backups/restore", json={"name": names[0], "confirm": bad})).status_code, 422)
        self.assertEqual((await self.request("POST", "/api/backups/restore", json={"name": "../SaveFile.es3", "confirm": True})).status_code, 404)
        self.assertEqual((await self.request("POST", "/api/backups/restore", json={"name": names[0], "confirm": True})).status_code, 200)
        self.assertEqual(json.loads(self.save.read_bytes())["modOrder"]["value"], ["child", "base"])

    async def test_full_profile_needs_opt_in_and_launch_follows_apply(self):
        self.save.write_bytes(b"opaque-es3-original")
        saved = await self.request("POST", "/api/profiles/save", json={"name": "snapshot", "note": "fixture"})
        self.assertEqual(saved.status_code, 200, saved.text)
        state = (await self.request("GET", "/api/state")).json()
        self.assertEqual(state["profiles"], [{"name": "snapshot", "save_note": "fixture", "mode": "full_file"}])
        self.save.write_bytes(self.original)
        with patch("webui.server.launch_game", return_value=(True, "launched")) as launcher:
            for override in ({}, {"full_restore": False}, {"full_restore": 1}, {"full_restore": "true"}):
                denied = await self.request("POST", "/api/profiles/apply", json={"name": "snapshot", "launch": True, **override})
                self.assertIn(denied.status_code, (409, 422))
                self.assertEqual(self.save.read_bytes(), self.original)
            launcher.assert_not_called()
            preview = (await self.request("GET", "/api/state")).json()
            self.assertTrue(preview["save_activity"]["recent_external_change"])
            denied = await self.request("POST", "/api/profiles/apply", json={"name": "snapshot", "full_restore": True, "launch": True, "save_revision": state["revision"]})
            self.assertEqual(denied.status_code, 409)
            self.assertEqual(self.save.read_bytes(), self.original)
            response = await self.request("POST", "/api/profiles/apply", json={"name": "snapshot", "full_restore": True, "launch": True, "save_revision": preview["revision"]})
            self.assertEqual(response.status_code, 200, response.text)
            launcher.assert_called_once_with(self.game)
        self.assertEqual(self.save.read_bytes(), b"opaque-es3-original")
        activity = (await self.request("GET", "/api/state")).json()
        self.assertFalse(activity["save_activity"]["recent_external_change"])
        self.assertEqual(activity["profiles"][0]["activity"]["launch_requests"], 1)
        self.assertEqual(AppSettings.load(self.settings_path).last_profile, "snapshot")
        self.assertEqual((await self.request("POST", "/api/profiles/delete", json={"name": "snapshot"})).status_code, 200)
        self.assertEqual(AppSettings.load(self.settings_path).last_profile, "")

    async def test_config_discovery_stale_revisions_and_backup(self):
        file_id, form = await self.config_document("ModConfig")
        self.assertEqual(form["mode"], "form")
        self.assertEqual({entry["key"]: entry["value_type"] for entry in form["entries"]},
                         {"enabled": "bool", "count": "int", "speed": "float"})
        edit = {"id": file_id, "mod_id": "child", "revision": form["revision"], "values": {"count": 5, "speed": 2}}
        self.assertEqual((await self.request("PUT", "/api/config/file", json={**edit, "id": "../SaveFile.es3"})).status_code, 404)
        self.assertEqual((await self.request("PUT", "/api/config/file", json={**edit, "mod_id": "base"})).status_code, 404)
        self.assertEqual((await self.request("PUT", "/api/config/file", json={**edit, "values": {"count": True}})).status_code, 422)
        self.assertEqual(self.save.read_bytes(), self.original)
        original_config = self.config.read_bytes() + b"\n"
        self.config.write_bytes(original_config)
        response = await self.request("PUT", "/api/config/file", json=edit)
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.data_dir / "config_backups").exists())
        edit["revision"] = (await self.config_document("ModConfig"))[1]["revision"]
        response = await self.request("PUT", "/api/config/file", json=edit)
        self.assertEqual(response.status_code, 200, response.text)
        doc = (await self.config_document("ModConfig"))[1]
        self.assertEqual(doc["revision"], response.json()["revision"])
        self.assertEqual([(entry["value"], entry["value_type"]) for entry in doc["entries"]],
                         [(True, "bool"), (5, "int"), (2.0, "float")])
        backup, = (self.data_dir / "config_backups").glob("*.bak")
        self.assertEqual(backup.read_bytes(), original_config)

    async def test_raw_config_json_validation_and_save(self):
        file_id, doc = await self.config_document("drop.json")
        self.assertEqual(doc["mode"], "raw")
        edit = {"mod_id": "child", "id": file_id, "revision": doc["revision"]}
        for text in ("{not json", '{"invalid": NaN}'):
            self.assertEqual((await self.request("PUT", "/api/config/file", json={**edit, "text": text})).status_code, 422)
        self.assertEqual(self.loot.read_text(encoding="utf-8"), '{"patches": []}')
        response = await self.request("PUT", "/api/config/file", json={**edit, "text": '{"patches": [{"table":"a"}]}'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(self.loot.read_bytes()), {"patches": [{"table": "a"}]})

    async def test_share_round_trip_and_input_bound(self):
        payload = {"name": "fixture", "order": ["child", "base"]}
        exported = await self.request("POST", "/api/share/export", json=payload)
        self.assertEqual(exported.status_code, 200, exported.text)
        imported = await self.request("POST", "/api/share/import", json=exported.json())
        self.assertEqual(imported.json(), payload)
        large = base64.urlsafe_b64encode(zlib.compress(b" " * (600 * 1024))).decode("ascii")
        rejected = await self.request("POST", "/api/share/import", json={"code": "PSMM1:" + large})
        self.assertEqual(rejected.status_code, 422)

    async def test_verified_share_matches_content_not_version_and_is_read_only(self):
        payload = {"name": "transfer fixture", "order": ["base", "child"], "verify": True}
        exported = await self.request('POST', '/api/share/export', json=payload)
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.json()['code'].startswith('PSMM2:'))
        before = await self.request('POST', '/api/share/preview', json=exported.json())
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual([row['status'] for row in before.json()['verification']], ['match', 'match'])
        self.loot.write_text('{"patches": [{"table":"same-version-changed"}]}', encoding='utf-8')
        (self.game / 'Mods' / 'base' / 'manifest.xml').unlink()
        after = await self.request('POST', '/api/share/preview', json=exported.json())
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual([row['status'] for row in after.json()['verification']], ['missing', 'different'])
        self.assertEqual(self.save.read_bytes(), self.original)
        self.assertNotIn(str(self.root), after.text)

    async def test_dependency_preview_and_write_recheck_after_manifest_changes(self):
        preview = await self.request('POST', '/api/order/dependencies', json={'order': ['child']})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()['ready'])
        self.assertEqual(preview.json()['order'], ['base', 'child'])
        self.assertEqual([row['id'] for row in preview.json()['enable']], ['base'])
        self.assertEqual(self.save.read_bytes(), self.original)
        revision = (await self.request('GET', '/api/state')).json()['revision']
        child = self.game / 'Mods' / 'child' / 'manifest.xml'
        prior = child.read_bytes()
        child.write_text('<Manifest><ID>child</ID><Prerequisites><Mod>new.dep</Mod></Prerequisites></Manifest>', encoding='utf-8')
        blocked = await self.request('POST', '/api/order/dependencies', json={'order': ['child']})
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertFalse(blocked.json()['ready'])
        self.assertEqual(blocked.json()['missing'][0]['id'], 'new.dep')
        save = {'order': ['base', 'child'], 'revision': revision, 'check_dependencies': True}
        rejected = await self.request('POST', '/api/order', json=save)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(self.save.read_bytes(), self.original)
        child.write_bytes(prior)
        written = await self.request('POST', '/api/order', json=save)
        self.assertEqual(written.status_code, 200, written.text)
        self.assertEqual(json.loads(self.save.read_bytes())['modOrder']['value'], ['base', 'child'])

    async def test_workshop_confirmation_and_interfering_write(self):
        item = WorkshopItem("1234567890", "Fixture", tags=["UI"])
        with patch("webui.server.fetch_workshop_details_by_ids", return_value=[item]) as lookup:
            response = await self.request("GET", "/api/workshop/lookup?ids=1234567890&translate=false")
            lookup.assert_called_once_with([item.published_file_id])
        self.assertEqual(response.json()["items"][0]["url"], item.url)
        payload = {"id": item.published_file_id, "confirm_login": False}
        entered, release = threading.Event(), threading.Event()

        def download(*_args, **kwargs):
            self.assertNotIn("password", kwargs)
            self.assertEqual(kwargs["steam_username"], "fixture-user")
            entered.set()
            if not release.wait(3):
                return False, "fixture timed out"
            return True, "downloaded"

        with patch("webui.operations.find_downloader_executable", return_value=("steamcmd", self.root / "steamcmd.exe")), \
                patch("webui.operations.download_workshop_item_manual", side_effect=download) as mocked:
            response = await self.request("POST", "/api/workshop/download", json=payload)
            self.assertEqual(response.status_code, 409)
            mocked.assert_not_called()
            task = asyncio.create_task(self.request("POST", "/api/workshop/download", json={**payload, "confirm_login": True}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                conflict = await self.request("POST", "/api/profiles/save", json={"name": "conflict"})
                self.assertEqual(conflict.status_code, 409, conflict.text)
            finally:
                release.set()
                result = await task
            self.assertEqual(result.json(), {"message": "downloaded"})
            mocked.assert_called_once()
        with patch("webui.server.open_workshop_page_in_steam") as opener:
            bad = await self.request("POST", "/api/workshop/open", json={"id": "steam://other"})
            self.assertEqual(bad.status_code, 422)
            good = await self.request("POST", "/api/workshop/open", json={"id": item.published_file_id})
            self.assertEqual(good.status_code, 200)
            opener.assert_called_once_with(item.published_file_id)


class TestRunner(unittest.TestCase):
    def test_runner_uses_loopback_and_frozen_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("webui.server.sys._MEIPASS", str(root), create=True):
                self.assertEqual(frontend_dist(), root / "frontend" / "dist")
            with patch("webui.server.uvicorn.Server") as server, patch("webui.server.webbrowser.open") as browser:
                run(root, port=8871, open_browser=False)
                config = server.call_args.args[0]
                self.assertEqual((config.host, config.port), ("127.0.0.1", 8871))
                self.assertFalse(config.proxy_headers)
                server.return_value.run.assert_called_once()
                browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
