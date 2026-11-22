import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.workshop import WorkshopItem
from core.workshop_cover import WorkshopCoverCache, _download, _SteamRedirects
from webui.server import create_app


ITEM_ID = "180010001"
FIRST = "https://images.steamusercontent.com/ugc/fixture/one/"
SECOND = "https://images.steamusercontent.com/ugc/fixture/two/"
PNG = b"\x89PNG\r\n\x1a\nfixture"
WEBP = b"RIFF\x05\x00\x00\x00WEBPfixture"


class WorkshopCoverCacheTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name) / "manager_data"

    def client(self):
        return TestClient(create_app(self.data), base_url="http://127.0.0.1",
                          headers={"X-PSMM-Request": "1"})

    def test_download_once_and_reuse_after_restart_without_steam(self):
        with self.client() as app, patch("webui.server.fetch_workshop_details_by_ids",
                                         return_value=[WorkshopItem(ITEM_ID, "Item", preview_url=FIRST)]) as steam:
            url = app.get(f"/api/workshop/covers?ids={ITEM_ID}").json()["covers"][ITEM_ID]
            self.assertTrue(url.startswith("/api/workshop/cover?"), url)
            with patch("core.workshop_cover._download", return_value=(PNG, "image/png")) as download:
                for _ in range(2):
                    picture = app.get(url)
                    self.assertEqual(picture.content, PNG)
                    self.assertIn("private", picture.headers["cache-control"])
                download.assert_called_once_with(FIRST)
            steam.assert_called_once_with([ITEM_ID])

        with self.client() as restarted, patch("webui.server.fetch_workshop_details_by_ids", side_effect=TimeoutError) as offline, \
                patch("core.workshop_cover._download", side_effect=TimeoutError) as download:
            result = restarted.get(f"/api/workshop/covers?ids={ITEM_ID},180010002&cached_only=true")
            self.assertEqual(result.json(), {"covers": {ITEM_ID: url}})
            self.assertEqual(restarted.get(f"/api/workshop/covers?ids={ITEM_ID}").json()["covers"][ITEM_ID], url)
            self.assertEqual(restarted.get(url).content, PNG)
            self.assertEqual(restarted.get(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
            offline.assert_not_called()
            download.assert_not_called()

    def test_new_workshop_cover_replaces_disk_only_after_success(self):
        with self.client() as app, patch("webui.server.fetch_workshop_details_by_ids",
                                         side_effect=[[WorkshopItem(ITEM_ID, "Item", preview_url=FIRST)],
                                                      [WorkshopItem(ITEM_ID, "Item", preview_url=SECOND)]]), \
                patch("core.workshop_cover._download", side_effect=[(PNG, "image/png"), None]):
            first = app.get(f"/api/workshop/lookup?ids={ITEM_ID}&translate=false").json()["items"][0]["preview_url"]
            self.assertEqual(app.get(first).content, PNG)
            second = app.get(f"/api/workshop/lookup?ids={ITEM_ID}&translate=false").json()["items"][0]["preview_url"]
            self.assertNotEqual(first, second)
            fallback = app.get(second)
            self.assertEqual(fallback.content, PNG)
            self.assertEqual(fallback.headers["cache-control"], "no-store")

        with self.client() as restarted, patch("webui.server.fetch_workshop_details_by_ids",
                                               return_value=[WorkshopItem(ITEM_ID, "Item", preview_url=SECOND)]), \
                patch("core.workshop_cover._download", return_value=(WEBP, "image/webp")) as download:
            self.assertEqual(restarted.get(first).content, PNG)
            self.assertEqual(restarted.get(f"/api/workshop/lookup?ids={ITEM_ID}&translate=false").json()["items"][0]["preview_url"], second)
            update = restarted.get(second)
            self.assertEqual(update.content, WEBP)
            self.assertEqual(update.headers["content-type"], "image/webp")
            download.assert_called_once_with(SECOND)
        with self.client() as restarted:
            self.assertEqual(restarted.get(f"/api/workshop/covers?ids={ITEM_ID}").json()["covers"][ITEM_ID], second)
            self.assertEqual(restarted.get(second).content, WEBP)

    def test_concurrent_reads_download_once_and_cache_evicts_oldest(self):
        cache = WorkshopCoverCache(self.data / "workshop_covers")
        one = cache.note(ITEM_ID, FIRST).split("&v=")[1]
        with patch("core.workshop_cover._download", return_value=(PNG, "image/png")) as download, \
                patch("core.workshop_cover._MAX_FILES", 1):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: cache.get(ITEM_ID, one), range(4)))
            self.assertEqual(results, [(PNG, "image/png", True)] * 4)
            download.assert_called_once_with(FIRST)
            two = cache.note("180010002", SECOND).split("&v=")[1]
            self.assertEqual(cache.get("180010002", two), (PNG, "image/png", True))
            self.assertEqual([p.stem for p in cache.folder.glob("*.cover")], ["180010002"])

    def test_downloader_rejects_untrusted_locations_and_oversized_or_non_images(self):
        with self.assertRaises(HTTPError):
            _SteamRedirects().redirect_request(Request(FIRST), None, 302, "", {}, "https://example.test/ugc/image.png")
        with patch("core.workshop_cover.build_opener") as opener:
            self.assertIsNone(_download("https://example.test/ugc/image.png"))
            opener.assert_not_called()
            for data, length, expected in ((PNG, str(len(PNG)), (PNG, "image/png")),
                                           (b"<html>", None, None),
                                           (PNG, "99999999", None),
                                           (PNG, "99", None),
                                           (PNG + b"x" * 32, None, None)):
                with self.subTest(length=length, expected=expected), patch("core.workshop_cover._MAX_IMAGE", 32):
                    stream = BytesIO(data)
                    stream.headers = {} if length is None else {"Content-Length": length}
                    opener.return_value.open.return_value = stream
                    self.assertEqual(_download(FIRST), expected)
