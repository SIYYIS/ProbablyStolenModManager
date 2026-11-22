"""Host the workbench in a Windows window and scope its service to that window."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import uvicorn

from .server import create_app, frontend_dist


WINDOW_TITLE = "深空当铺 · 模组工作台"


class DesktopStartError(RuntimeError):
    pass


@contextmanager
def local_service(data_dir: Path, *, pick_path: Callable[[bool], str | None] | None = None) -> Iterator[str]:
    assets = frontend_dist()
    if not (assets / "index.html").is_file():
        raise DesktopStartError("找不到工作台界面文件，请重新构建或重新下载程序。")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Keep the allocated port reserved until Uvicorn takes over the socket.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            create_app(data_dir, assets_dir=assets, pick_path=pick_path), host="127.0.0.1", port=port,
            proxy_headers=False, access_log=False, log_config=None, ws="none",
            timeout_graceful_shutdown=5,
        ))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.025)
            if not server.started or not worker.is_alive():
                raise DesktopStartError("本机工作台服务启动失败，请重新打开程序。")
            yield f"http://127.0.0.1:{port}/"
        finally:
            server.should_exit = True
            worker.join(timeout=7)


def run_desktop(data_dir: Path) -> None:
    import webview

    # The UI is served over loopback; it does not need access to file:// URLs.
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = True
    dialog_lock = threading.Lock()

    def pick_path(file: bool) -> str | None:
        if not dialog_lock.acquire(blocking=False):
            raise RuntimeError('File picker already open')
        try:
            options = {'file_types': ('Executable (*.exe)',)} if file else {}
            result = window.create_file_dialog(webview.FileDialog.OPEN if file else webview.FileDialog.FOLDER, **options)
            return result[0] if result else None
        finally:
            dialog_lock.release()

    with local_service(data_dir, pick_path=pick_path) as url:
        window = webview.create_window(
            WINDOW_TITLE, url, width=1280, height=860, min_size=(760, 540),
            background_color="#e9f3ff",
        )
        unsupported = threading.Event()
        timed_out = threading.Event()

        def require_edge(renderer: str) -> bool:
            if renderer == "edgechromium":
                return True
            unsupported.set()
            return False

        def watch_startup() -> None:
            deadline = time.monotonic() + 20
            while not window.events.closed.is_set():
                if window.events.loaded.wait(0.2):
                    return
                if time.monotonic() >= deadline:
                    if not window.events.closed.is_set():
                        timed_out.set()
                        window.destroy()
                    return

        window.events.initialized += require_edge
        window.events.shown += watch_startup
        icon = Path(__file__).resolve().parents[1] / "frontend" / "public" / "app_icon.ico"
        webview.start(gui="edgechromium", private_mode=True, http_server=False,
                      icon=str(icon) if not getattr(sys, "frozen", False) and icon.is_file() else None)
        if unsupported.is_set():
            raise DesktopStartError("此程序需要 Microsoft Edge WebView2 Runtime。安装后请重新打开程序。")
        if timed_out.is_set():
            raise DesktopStartError("工作台窗口加载超时。请重新打开程序；若仍失败，请检查或修复 Microsoft Edge WebView2 Runtime。")


def show_start_error(exc: Exception) -> None:
    from ctypes import windll

    message = str(exc) if isinstance(exc, DesktopStartError) else (
        "无法打开模组工作台。请检查 manager_data 目录是否可写，以及 Microsoft Edge WebView2 Runtime 是否可用。"
    )
    logging.getLogger(__name__).exception("Could not start the desktop workbench")
    windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
