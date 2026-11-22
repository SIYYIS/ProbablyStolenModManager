import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

from webui.desktop import DesktopStartError, local_service


class DesktopServiceTests(unittest.TestCase):
    def test_serves_assets_on_loopback_and_stops_when_closed(self):
        with TemporaryDirectory(prefix="psmm-test-desktop-") as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>workbench</p>", encoding="utf-8")
            with patch("webui.desktop.frontend_dist", return_value=root):
                with local_service(root / "manager") as first, local_service(root / "other") as second:
                    self.assertNotEqual(first, second)
                    self.assertTrue(first.startswith("http://127.0.0.1:"))
                    opener = build_opener(ProxyHandler({}))
                    with opener.open(first, timeout=4) as response:
                        self.assertEqual(response.read(), b"<p>workbench</p>")
                        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            port = int(first.rsplit(":", maxsplit=1)[1].rstrip("/"))
            with socket.socket() as client:
                client.settimeout(1)
                self.assertNotEqual(client.connect_ex(("127.0.0.1", port)), 0)

    def test_missing_assets_report_a_startup_error(self):
        with TemporaryDirectory(prefix="psmm-test-desktop-") as tmp:
            root = Path(tmp)
            with patch("webui.desktop.frontend_dist", return_value=root):
                with self.assertRaises(DesktopStartError), local_service(root / "manager"):
                    self.fail("Started desktop server without the frontend")
