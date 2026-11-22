import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

from core import diagnostics
from diagnostic_fixture import FIXTURE_DIGEST, fixture_app


API = "/api/diagnostics"
GUARD = {"X-PSMM-Request": "1"}


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="psmm-test-diagnostics-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        app, ops, self.marker = fixture_app(self.root)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.paths = ops.paths(ops.settings())
        self.log = self.paths.save_data_dir / "Player.log"
        self.config = self.paths.mod_configs_dir / "fixture.json"

    def test_readonly_actual_formats_and_evidence(self):
        before = self.log.read_bytes()
        data = self.client.get(API + "/log", headers=GUARD).json()
        self.assertEqual(data["log"]["status"], "ok")
        self.assertEqual(data["enabled"], {"ids": ["fixture.current-only"], "status": "ok"})
        audited = {r["mod_id"]: r["status"] for r in data["observed"] if r["mod_id"]}
        self.assertEqual(audited, {"fixture.disabled": "not_running_reported", "fixture.window": "not_running_reported",
                                   "fixture.widget": "running_reported"})
        attributed = [r for r in data["events"] if r["mod_id"]]
        self.assertEqual([(r["mod_id"], r["evidence"]) for r in attributed], [("fixture.window", "audit_id")])
        self.assertIn("MissingMethodException", attributed[0]["message"])
        hook = next(r for r in data["events"] if r["evidence"] == "hook_owner")
        self.assertEqual(hook["reported"], "Fixture Widget")
        harmony = next(r for r in data["events"] if r["kind"] == "patch")
        self.assertEqual(harmony["evidence"], "unattributed")
        self.assertIsNone(harmony["mod_id"])
        self.assertIn("FixtureWidget.Patches", harmony["stack"][0])
        self.assertNotIn("FixtureOwner", json.dumps(data))
        self.assertEqual(self.log.read_bytes(), before)

    def test_api_guard_limits_and_default_excludes_configs(self):
        for path, method in (("/log", "GET"), ("/dev", "PUT"), ("/support/download", "POST")):
            self.assertEqual(self.client.request(method, API + path).status_code, 403)
            self.assertEqual(self.client.request(method, API + path, headers={**GUARD, "Origin": "http://example.invalid"}).status_code, 403)
        with patch.object(diagnostics, "_configs", side_effect=AssertionError("opt-in required")):
            preview = self.client.post(API + "/support/preview", headers=GUARD, json={}).json()
            zip_response = self.client.post(API + "/support/download", headers=GUARD, json={"revision": preview["revision"]})
        self.assertEqual(zip_response.status_code, 200)
        self.assertEqual(zip_response.headers["cache-control"], "no-store")
        with ZipFile(io.BytesIO(zip_response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"enabled-mods.json", "report.json", "Player.log"})
            self.assertNotIn(b"never-export-this", archive.read("enabled-mods.json"))
            log = archive.read("Player.log")
            self.assertIn(b"MissingMethodException", log)
            for secret in (b"sample-secret-in-player-log", b"fake-pass", b"dont-share", b"FixtureOwner", b"76561190000000000"):
                self.assertNotIn(secret, log)
        self.assertEqual(self.client.post(API + "/support/preview", headers=GUARD, json={"include_configs": "true"}).status_code, 422)

    def test_opt_in_scrubbed_preview_matches_zip_and_requires_refresh(self):
        opts = {"include_configs": True}
        self.config.write_text(r'{"Key":"steamAuthTicket", "Value":"ticket-123", "nested":{"accessToken":"abc123",'
                               r'"path":"/home/fixture-user/private folder/tmp", "extraCustomers":4}}', encoding="utf-8")
        (self.config.parent / "oversized.json").write_bytes(b"a" * (diagnostics.CONFIG_LIMIT + 1))
        (self.config.parent / "binary.evil").write_bytes(b"not-json")
        preview = self.client.post(API + "/support/preview", headers=GUARD, json=opts).json()
        self.assertEqual(preview["skipped"], 2)
        packed = next(f for f in preview["files"] if f["name"] == "ModConfigs/fixture.json")
        cleaned = json.loads(packed["text"])
        self.assertEqual(cleaned["Value"], "[redacted]")
        self.assertEqual(cleaned["nested"], {"accessToken": "[redacted]", "path": "[redacted path]", "extraCustomers": 4})
        response = self.client.post(API + "/support/download", headers=GUARD, json={**opts, "revision": preview["revision"]})
        with ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read(packed["name"]).decode("utf-8"), packed["text"])
        self.assertEqual(self.client.post(API + "/support/download", headers=GUARD, json={"revision": preview["revision"]}).status_code, 409)
        self.config.write_text('{"extraCustomers": 5}')
        self.assertEqual(self.client.post(API + "/support/download", headers=GUARD, json={**opts, "revision": preview["revision"]}).status_code, 409)

    def test_log_bounds_and_missing_or_invalid_inputs(self):
        self.log.write_bytes(b"x" * diagnostics.LOG_LIMIT + b"\nSystem.ArgumentException: fixture\n")
        report = diagnostics.analyze(self.paths)
        self.assertTrue(report["tail_line_numbers"])
        self.assertEqual(report["events"][0]["line"], 1)
        snap = diagnostics.support_snapshot(self.paths)
        self.assertLessEqual(len(snap.files["Player.log"]), diagnostics.LOG_LIMIT)
        self.paths.save_file.write_text('{"modOrder":[false]}')
        self.log.unlink()
        report = diagnostics.analyze(self.paths)
        self.assertEqual(report["log"]["status"], "missing")
        self.assertEqual(report["enabled"]["status"], "unreadable")
        self.assertFalse(report["events"])

    def test_dev_fail_closed_for_unknown_installation_running_and_stale_state(self):
        endpoint = API + "/dev"
        self.assertFalse(self.client.get(endpoint, headers=GUARD).json()["supported"])
        self.assertFalse(self.marker.exists())
        with patch.object(diagnostics, "_verified_game", return_value=True):
            for running in (True, None):
                with patch.object(diagnostics, "_game_running", return_value=running):
                    state = self.client.get(endpoint, headers=GUARD).json()
                    self.assertFalse(state["editable"])
                    self.assertEqual(self.client.put(endpoint, headers=GUARD, json={"enabled": True, "revision": state["revision"]}).status_code, 409)
            with patch.object(diagnostics, "_game_running", return_value=False):
                state = self.client.get(endpoint, headers=GUARD).json()
                self.marker.touch()
                self.assertEqual(self.client.put(endpoint, headers=GUARD, json={"enabled": True, "revision": state["revision"]}).status_code, 409)

    @unittest.skipUnless(os.name == "nt", "the verified build and marker writer are Windows-only")
    def test_dev_create_and_remove_only_empty_file_and_preserve_unsafe_markers(self):
        endpoint = API + "/dev"
        self.assertEqual(hashlib.sha256(b"fixture-managed-assembly").hexdigest(), FIXTURE_DIGEST)
        with patch.object(diagnostics, "_DEV_SHA256", FIXTURE_DIGEST), patch.object(diagnostics, "_game_running", return_value=False):
            state = self.client.get(endpoint, headers=GUARD).json()
            self.assertTrue(state["editable"], state)
            enabled = self.client.put(endpoint, headers=GUARD, json={"enabled": True, "revision": state["revision"]})
            self.assertEqual(enabled.status_code, 200, enabled.text)
            self.assertEqual(self.marker.read_bytes(), b"")
            disabled = self.client.put(endpoint, headers=GUARD, json={"enabled": False, "revision": enabled.json()["revision"]})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertFalse(self.marker.exists())
            self.marker.write_bytes(b"do not replace")
            self.assertEqual(self.client.get(endpoint, headers=GUARD).json()["marker"], "nonempty")
            self.assertEqual(self.client.put(endpoint, headers=GUARD, json={"enabled": False, "revision": "0" * 64}).status_code, 409)
            self.assertEqual(self.marker.read_bytes(), b"do not replace")
            self.marker.unlink()
            target = self.root / "outside-marker"
            target.touch()
            os.link(target, self.marker)
            self.assertFalse(self.client.get(endpoint, headers=GUARD).json()["editable"])
            self.assertEqual(self.client.put(endpoint, headers=GUARD, json={"enabled": False, "revision": "0" * 64}).status_code, 409)
            self.assertTrue(self.marker.exists())
            self.marker.unlink()
            self.marker.with_name("1dev.txt").touch()
            self.assertEqual(self.client.get(endpoint, headers=GUARD).json()["marker"], "full")

    def test_links_are_not_followed_and_multiple_audit_blocks_keep_last(self):
        target = self.root / "other.json"
        target.write_text('{"unrelatedData":true}')
        self.config.unlink()
        os.link(target, self.config)
        snap = diagnostics.support_snapshot(self.paths, include_configs=True)
        self.assertNotIn("ModConfigs/fixture.json", snap.files)
        report = diagnostics.parse_log(self.log.read_text(encoding="utf-8") +
            "\n[ModLoader] audit: game 0.46D, trust list: fixture\n"
            "[ModLoader] audit: fixture.new v1 source=Local workshop=0 owner=0 running=True trust=Match caps=none hash=x")
        self.assertEqual([r["mod_id"] for r in report["observed"] if r["mod_id"]], ["fixture.new"])


if __name__ == "__main__":
    unittest.main()
