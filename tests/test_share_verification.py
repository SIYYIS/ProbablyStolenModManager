from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
import zlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from core import share_verification as share
from core.es3_manager import ProfileManager
from core.paths import APP_ID, GamePaths
from core.scanner import scan_installed_mods
from core.settings import AppSettings
from webui.operations import Operations, import_share
from webui.server import create_app


def wire(raw: bytes, *, prefix: str = "PSMM2", tail: bytes = b"") -> str:
    return prefix + ":" + base64.urlsafe_b64encode(zlib.compress(raw) + tail).decode("ascii")


class TestShareVerification(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def machine(self, label):
        root = self.root / label
        paths = GamePaths(local_mods_dir=root / "Mods", workshop_dir=root / "Workshop",
                          save_file=root / "SaveFile.es3", mod_configs_dir=root / "ModConfigs")
        for folder in (paths.local_mods_dir, paths.workshop_dir, paths.mod_configs_dir):
            folder.mkdir(parents=True)
        ops = Operations(root / "manager")
        ops.settings = lambda: AppSettings(steam_api_key="private-api-key", steam_username="private-user")
        ops.paths = lambda _: paths
        return paths, ops

    @staticmethod
    def mod(parent, folder, mid, content=b"original", *, reverse=False):
        target = parent / folder
        files = {
            target / "manifest.xml": f"<Mod><ID>{mid}</ID><Name>{mid} title</Name><ModVersion>1</ModVersion></Mod>".encode(),
            target / "data" / "example.bin": content,
        }
        for path in reversed(files) if reverse else files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[path])
        return target

    def test_deterministic_ordered_code_does_not_read_or_encode_player_state(self):
        first, sender = self.machine("first")
        second, receiver = self.machine("second")
        order = ["b", "a"]
        for paths, folder, reverse in ((first, "original", False), (second, "renamed", True)):
            paths.save_file.write_bytes(b'{"modTrustMac": "private-trust-token"}')
            (paths.mod_configs_dir / "a.json").write_text('"private-config"', encoding="utf-8")
            mod = self.mod(paths.local_mods_dir, folder, "a", reverse=reverse)
            self.mod(paths.workshop_dir, "123456789", "b", reverse=reverse)
            if reverse:
                os.utime(mod / "data" / "example.bin", (1000000000, 1000000000))
        with patch.object(share, "_open_fd", wraps=share._open_fd) as opened:
            code = sender.export_share("Example", order, verified=True)["code"]
        self.assertTrue(opened.called)
        self.assertTrue(all(path.parts[-1] in ("manifest.xml", "example.bin") for (path,), _ in opened.call_args_list))
        self.assertEqual(receiver.export_share("Example", order, verified=True), {"code": code})
        raw = zlib.decompress(base64.urlsafe_b64decode(code.removeprefix("PSMM2:")))
        payload = json.loads(raw)
        self.assertEqual(set(payload), {"v", "appid", "name", "mods"})
        self.assertEqual(payload["appid"], APP_ID)
        self.assertEqual([row["id"] for row in payload["mods"]], order)
        self.assertEqual([row["source"] for row in payload["mods"]], ["workshop", "local"])
        self.assertTrue(all(set(row) == {"id", "source", "workshop_id", "fingerprint"} for row in payload["mods"]))
        self.assertTrue(all(len(row["fingerprint"]) == 64 for row in payload["mods"]))
        self.assertNotIn(b"private-", raw)
        self.assertNotIn(b"example.bin", raw)
        self.assertNotIn(b"original", raw)
        self.assertEqual(import_share(code), {"name": "Example", "order": order})

    def test_preview_distinguishes_all_outcomes_and_keeps_the_effective_override(self):
        source, sender = self.machine("source")
        target, receiver = self.machine("target")
        mods = [("same", "123456780"), ("different", "123456781"), ("absent", "123456782"), ("unsafe", "123456783")]
        for mid, fid in mods:
            self.mod(source.workshop_dir, fid, mid)
        self.mod(target.local_mods_dir, "renamed", "same")
        self.mod(target.workshop_dir, "123456781", "different")  # Matching alternate isn't the effective copy.
        self.mod(target.local_mods_dir, "override", "different", b"modified")
        unsafe = self.mod(target.workshop_dir, "123456783", "unsafe")
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        os.link(outside, unsafe / "linked")
        order = [mid for mid, _ in mods]
        code = sender.export_share("Example", order, verified=True)["code"]
        preview = receiver.preview_share(code)
        self.assertEqual(preview["format"], "PSMM2")
        self.assertEqual(preview["version"], 2)
        self.assertEqual(preview["order"], order)
        self.assertEqual(preview["workshop"], dict(mods))
        self.assertEqual(preview["missing"], [{"id": "absent", "workshop_id": "123456782"}])
        self.assertEqual(preview["verification"], [
            {"id": mid, "name": (f"{mid} title" if actual else mid), "status": status,
             "expected_source": "workshop", "actual_source": actual, "workshop_id": fid}
            for (mid, fid), (status, actual) in zip(mods, [
                ("match", "local"), ("different", "local"), ("missing", None), ("unverifiable", "workshop"),
            ])
        ])
        with self.assertRaises(HTTPException) as failure:
            receiver.export_share("Example", ["unsafe"], verified=True)
        self.assertEqual(failure.exception.status_code, 409)

    def test_sharing_preserves_source_selection_and_workshop_hint(self):
        paths, ops = self.machine("machine")
        self.mod(paths.local_mods_dir, "a", "mod", b"first")
        winner = self.mod(paths.local_mods_dir, "z", "mod", b"last")
        self.mod(paths.local_mods_dir, ".ignored", "mod", b"ignored")
        self.mod(paths.workshop_dir, "123456789", "mod")
        self.assertEqual(scan_installed_mods(paths)["mod"].folder, winner)
        observed = share.inspect_share(paths, ["mod"])["mod"]
        payload = share.read_share(ops.export_share("Example", ["mod"], verified=True)["code"])
        self.assertEqual(payload["mods"], [
            {"id": "mod", "source": "local", "workshop_id": "123456789", "fingerprint": observed.fingerprint},
        ])

    def test_legacy_code_and_defaults_still_work_without_a_fingerprint(self):
        paths, ops = self.machine("machine")
        self.mod(paths.workshop_dir, "123456789", "installed")
        order = ["installed", "other"]
        code = ops.export_share("Example", order)["code"]
        self.assertEqual(code, ProfileManager.export_share_code("Example", order, {"installed": "123456789"}))
        self.assertEqual(import_share(code), {"name": "Example", "order": order})
        preview = ops.preview_share(code)
        self.assertEqual(preview["version"], 1)
        self.assertEqual(preview["format"], "PSMM1")
        self.assertEqual(preview["workshop"], {"installed": "123456789"})
        self.assertEqual(preview["missing"], [{"id": "other", "workshop_id": None}])
        self.assertEqual([row["status"] for row in preview["verification"]], ["unverifiable", "missing"])

    def test_rejects_invalid_versions_fields_and_bounded_wire_data_before_scanning(self):
        _, ops = self.machine("machine")
        entry = {"id": "mod", "source": "local", "workshop_id": None, "fingerprint": "a" * 64}
        header = {"v": 2, "name": "Example", "appid": APP_ID}
        payload = header | {"mods": [entry]}
        malformed = [
            payload | {"appid": "1"}, payload | {"v": 2.0}, payload | {"config": {}},
            header | {"mods": [entry, entry]}, header | {"mods": [entry | {"source": "path"}]},
            header | {"mods": [entry | {"workshop_id": "https://example.invalid"}]},
            header | {"mods": [entry | {"fingerprint": "bad"}]}, header | {"mods": [entry | {"path": "C:/private"}]},
            header | {"mods": [{key: value for key, value in entry.items() if key != "fingerprint"}]},
            header | {"mods": [entry | {"id": "\ud800"}]},
        ]
        raw = json.dumps(payload).encode()
        cases = [wire(json.dumps(case).encode()) for case in malformed] + [
            wire(raw, prefix="PSMM1"), wire(raw, tail=zlib.compress(b"{}")),
            wire(raw.replace(b'"v": 2', b'"v": 2, "v": 2')), wire(b" " * (share.MAX_CODE_BYTES + 1)),
            "PSMM2:" + "A" * share.MAX_CODE_BYTES, "PSMM2:not a valid code", wire(raw)[:-8],
        ]
        with patch.object(ops, "paths", side_effect=AssertionError("invalid code must not trigger discovery")):
            for index, code in enumerate(cases):
                with self.subTest(index=index), self.assertRaises(HTTPException) as failure:
                    ops.preview_share(code)
                self.assertEqual(failure.exception.status_code, 422)

    def test_linked_candidate_cannot_be_discarded_in_favor_of_workshop(self):
        paths, ops = self.machine("machine")
        self.mod(paths.workshop_dir, "123456789", "mod")
        code = ops.export_share("Example", ["mod"], verified=True)["code"]
        outside = self.mod(self.root, "outside", "mod")
        link = paths.local_mods_dir / "alias"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        with patch.object(share, "_open_fd", wraps=share._open_fd) as opened:
            preview = ops.preview_share(code)
        self.assertFalse(any(path.is_relative_to(link) for (path,), _ in opened.call_args_list))
        self.assertEqual(preview["verification"][0]["status"], "unverifiable")
        self.assertEqual(preview["missing"], [])

    def test_tree_limits_and_discovery_limits_fail_closed(self):
        paths, ops = self.machine("machine")
        first = self.mod(paths.local_mods_dir, "a", "a")
        self.mod(paths.local_mods_dir, "b", "b")
        code = ops.export_share("Example", ["a", "b"], verified=True)["code"]
        total = sum(path.stat().st_size for path in first.rglob("*") if path.is_file())
        for setting, limit in [("MAX_MOD_ENTRIES", 2), ("MAX_MOD_BYTES", total - 1), ("MAX_DEPTH", 1),
                               ("MAX_SCAN_ENTRIES", 1), ("MAX_MANIFEST_BYTES", 4), ("MAX_SCAN_BYTES", 4)]:
            with self.subTest(setting=setting), patch.object(share, setting, limit):
                self.assertEqual([row["status"] for row in ops.preview_share(code)["verification"]], ["unverifiable"] * 2)
                with self.assertRaises(HTTPException):
                    ops.export_share("Example", ["a", "b"], verified=True)
        with patch.object(share, "MAX_TOTAL_BYTES", total * 2 - 1):
            self.assertEqual([row["status"] for row in ops.preview_share(code)["verification"]], ["match", "unverifiable"])

    def test_mutation_after_read_with_same_size_and_restored_mtime_is_rejected(self):
        paths, ops = self.machine("machine")
        mod = self.mod(paths.local_mods_dir, "a", "a")
        target = mod / "data" / "example.bin"
        code = ops.export_share("Example", ["a"], verified=True)["code"]
        original = share._checked_file

        @contextmanager
        def mutate_after_read(snapshot, path):
            with original(snapshot, path) as stream:
                yield stream
            if path == target:
                stamp = path.stat()
                path.write_bytes(b"mutated!")
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

        with patch.object(share, "_checked_file", mutate_after_read):
            self.assertEqual(ops.preview_share(code)["verification"][0]["status"], "unverifiable")

    def test_new_override_during_hash_does_not_certify_previous_winner(self):
        paths, ops = self.machine("machine")
        workshop = self.mod(paths.workshop_dir, "123456789", "mod")
        code = ops.export_share("Example", ["mod"], verified=True)["code"]
        original = share._fingerprint

        def add_override(folder, budget):
            result = original(folder, budget)
            if folder == workshop:
                self.mod(paths.local_mods_dir, "override", "mod", b"new effective copy")
            return result

        with patch.object(share, "_fingerprint", add_override):
            self.assertEqual(ops.preview_share(code)["verification"][0]["status"], "unverifiable")

    def test_http_verified_export_import_and_preview_default_to_legacy_only_on_export(self):
        paths, _ = self.machine("http")
        folder = self.mod(paths.local_mods_dir, "a", "mod")
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "index.html").write_text("<!doctype html>", encoding="utf-8")
        payload = {"name": "Example", "order": ["mod"]}
        with patch.object(Operations, "paths", return_value=paths), TestClient(
            create_app(self.root / "http-manager", assets_dir=assets),
            base_url="http://127.0.0.1", headers={"X-PSMM-Request": "1"},
        ) as client:
            legacy = client.post("/api/share/export", json=payload)
            self.assertEqual(legacy.status_code, 200)
            self.assertTrue(legacy.json()["code"].startswith("PSMM1:"))
            verified = client.post("/api/share/export", json={**payload, "verify": True})
            self.assertEqual(verified.status_code, 200)
            code = verified.json()
            self.assertTrue(code["code"].startswith("PSMM2:"))
            self.assertEqual(client.post("/api/share/import", json=code).json(), payload)
            (folder / "data" / "example.bin").write_bytes(b"different bytes")
            preview = client.post("/api/share/preview", json=code)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.json()["format"], "PSMM2")
            self.assertEqual(preview.json()["verification"][0]["status"], "different")


if __name__ == "__main__":
    unittest.main()
