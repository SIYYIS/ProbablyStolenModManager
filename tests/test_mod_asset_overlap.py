import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.paths import GamePaths
from core.scanner import detect_asset_conflicts, scan_installed_mods
from core.workshop import browse_workshop_public, open_workshop_page_in_steam, trusted_workshop_preview


class AssetOverlapTest(unittest.TestCase):
    def test_reports_names_paths_and_actual_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for address, mod_id, name, suffix in (("alpha", "brewing", "酿酒示例", "png"),
                                                  ("ws_3832840208", "tobacco", "香烟示例", "jpg")):
                folder = root / address
                assets = folder / "Textures" / "Items"
                assets.mkdir(parents=True)
                (folder / "manifest.xml").write_text(f"<Manifest><ID>{mod_id}</ID><Name>{name}</Name></Manifest>", encoding="utf-8")
                (assets / "same.png").write_bytes(b"identical bytes")
                (assets / f"diff.{suffix}").write_bytes(suffix.encode())
                (assets / "unknown.png").write_bytes(b"unreadable fixture")
                localization = folder / "Localization"
                localization.mkdir()
                (localization / "en.csv").write_text(f"key,value\nshared,Same text\ndifferent,{name}", encoding="utf-8")
            mods = scan_installed_mods(GamePaths(local_mods_dir=root))
            self.assertEqual(mods["tobacco"].workshop_id, "3832840208")
            from core.scanner import _asset_digest
            with patch("core.scanner._asset_digest", side_effect=lambda p: None if p.stem == "unknown" else _asset_digest(p)):
                issues = detect_asset_conflicts(["tobacco", "brewing"], mods)
            assets = {issue.asset.key: issue.asset for issue in issues}
            self.assertEqual(assets["same"].comparison, "same")
            self.assertEqual(assets["diff"].comparison, "different")
            self.assertEqual(assets["unknown"].comparison, "unknown")
            self.assertEqual(assets["en / shared"].comparison, "same")
            self.assertEqual(assets["en / different"].comparison, "different")
            self.assertEqual([(o.name, o.mod_id, o.path) for o in assets["diff"].owners], [
                ("香烟示例", "tobacco", "Textures/Items/diff.jpg"), ("酿酒示例", "brewing", "Textures/Items/diff.png"),
            ])
            self.assertEqual(detect_asset_conflicts(["brewing"], mods), [])

    def test_only_steam_image_cdn_is_accepted(self):
        allowed = "https://images.steamusercontent.com/ugc/123/ABC/?imw=128"
        self.assertEqual(trusted_workshop_preview(allowed), allowed)
        for url in (allowed.replace("https:", "http:"), allowed.replace(".com/", ".com.evil.test/"),
                    allowed.replace(".com/", ".com@localhost/"), "file:///C:/secret.png", None):
            self.assertEqual(trusted_workshop_preview(url), "")

    def test_public_search_does_not_treat_an_error_page_as_empty(self):
        with patch("core.workshop._steam_open") as opener:
            opener.return_value.__enter__.return_value.read.return_value = b"<title>Access Denied</title>"
            with self.assertRaises(ValueError):
                browse_workshop_public("missing")
            opener.return_value.__enter__.return_value.read.return_value = b'<div class="workshopBrowseItems">No items matching</div>'
            self.assertEqual(browse_workshop_public("missing"), [])

    def test_workshop_open_falls_back_to_the_exact_https_page(self):
        with patch("core.workshop.platform.system", return_value="Windows"), \
                patch("core.workshop.os.startfile", side_effect=[OSError("no handler"), None], create=True) as start:
            self.assertTrue(open_workshop_page_in_steam("180019999"))
            self.assertEqual([call.args[0] for call in start.call_args_list], [
                "steam://url/CommunityFilePage/180019999",
                "https://steamcommunity.com/sharedfiles/filedetails/?id=180019999",
            ])
