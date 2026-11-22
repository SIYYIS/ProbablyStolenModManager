"""Bounded disk cache for Steam Workshop cover images."""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from http.client import HTTPException
from pathlib import Path
from threading import BoundedSemaphore, Lock
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .file_ops import atomic_write, is_link
from .workshop import trusted_workshop_preview

_ID = re.compile(r"[0-9]{1,20}")
_VERSION = re.compile(rb"[a-f0-9]{64}")
_MAX_IMAGE = 4 * 1024 * 1024
_MAX_BYTES = 128 * 1024 * 1024
_MAX_FILES = 1024
_MAX_KNOWN = 4096
_RETRY_DELAY = 60
_DOWNLOAD_TIMEOUT = 5


def _image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return ""


class _SteamRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_workshop_preview(newurl):
            raise HTTPError(req.full_url, code, "Unsupported Workshop cover redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> tuple[bytes, str] | None:
    if not trusted_workshop_preview(url):
        return None
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/png,image/jpeg,image/webp,image/gif"})
    deadline = time.monotonic() + _DOWNLOAD_TIMEOUT
    with build_opener(_SteamRedirects()).open(request, timeout=_DOWNLOAD_TIMEOUT) as response:
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 12
                                   or int(length) > _MAX_IMAGE):
            return None
        data = bytearray()
        while time.monotonic() < deadline:
            chunk = response.read1(min(65536, _MAX_IMAGE + 1 - len(data)))
            if not chunk:
                if length is not None and len(data) != int(length):
                    return None
                media = _image_type(data)
                return (bytes(data), media) if media else None
            data.extend(chunk)
            if len(data) > _MAX_IMAGE:
                break
    return None


class WorkshopCoverCache:
    def __init__(self, folder: Path):
        self.folder = folder
        # Source URLs come from Workshop metadata, never from image request parameters.
        self._sources: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._failures: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._state = Lock()
        self._disk = Lock()
        self._items = tuple(Lock() for _ in range(64))
        self._downloads = BoundedSemaphore(6)

    @staticmethod
    def _route(item_id: str, version: str) -> str:
        return f"/api/workshop/cover?id={item_id}&v={version}"

    def note(self, item_id: str, preview: str) -> str:
        preview = trusted_workshop_preview(preview)
        if not _ID.fullmatch(item_id):
            return ""
        if not preview:
            return self.link(item_id)
        version = hashlib.sha256(preview.encode("utf-8")).hexdigest()
        with self._state:
            self._sources[item_id] = version, preview
            self._sources.move_to_end(item_id)
            if len(self._sources) > _MAX_KNOWN:
                self._sources.popitem(last=False)
        return self._route(item_id, version)

    def link(self, item_id: str) -> str:
        if not _ID.fullmatch(item_id):
            return ""
        with self._state:
            source = self._sources.get(item_id)
        if source:
            return self._route(item_id, source[0])
        cached = self._read(item_id, header_only=True)
        return self._route(item_id, cached[0]) if cached else ""

    def _path(self, item_id: str) -> Path:
        path = self.folder / (item_id + ".cover")
        if any(is_link(p) for p in (path, self.folder, *self.folder.parents)):
            raise OSError("Linked Workshop cache path")
        return path

    def _read(self, item_id: str, *, header_only: bool = False) -> tuple[str, bytes, str] | None:
        try:
            path = self._path(item_id)
            if not path.is_file() or not 64 < path.stat().st_size <= 64 + _MAX_IMAGE:
                return None
            with path.open("rb") as stream:
                version = stream.read(64)
                picture = stream.read(12 if header_only else _MAX_IMAGE + 1)
            media = _image_type(picture)
            if _VERSION.fullmatch(version) and media and len(picture) <= _MAX_IMAGE:
                return version.decode("ascii"), picture, media
        except OSError:
            pass
        return None

    def _write(self, item_id: str, version: str, picture: bytes) -> None:
        with self._disk:
            path = self._path(item_id)
            self.folder.mkdir(parents=True, exist_ok=True)
            atomic_write(path, version.encode("ascii") + picture)
            files = []
            for entry in self.folder.glob("*.cover"):
                try:
                    if _ID.fullmatch(entry.stem) and not is_link(entry) and entry.is_file():
                        info = entry.stat()
                        files.append((info.st_mtime_ns, info.st_size, entry))
                except OSError:
                    continue
            total = sum(size for _, size, _ in files)
            count = len(files)
            for _, size, entry in sorted(files):
                if total <= _MAX_BYTES and count <= _MAX_FILES:
                    break
                if entry != path:
                    try:
                        entry.unlink(missing_ok=True)
                    except OSError:
                        continue
                    total -= size
                    count -= 1

    def get(self, item_id: str, version: str) -> tuple[bytes, str, bool] | None:
        if not _ID.fullmatch(item_id) or not version.isascii() or not _VERSION.fullmatch(version.encode("ascii")):
            return None
        with self._items[int(item_id) % len(self._items)]:
            cached = self._read(item_id)
            if cached and cached[0] == version:
                return cached[1], cached[2], True
            stale = (cached[1], cached[2], False) if cached else None
            key = item_id, version
            with self._state:
                source = self._sources.get(item_id)
                failed = self._failures.get(key, 0) > time.monotonic()
            if not source or source[0] != version or failed:
                return stale
            with self._downloads:
                try:
                    result = _download(source[1])
                except (OSError, HTTPException, ValueError):
                    result = None
            if result is None:
                with self._state:
                    self._failures[key] = time.monotonic() + _RETRY_DELAY
                    self._failures.move_to_end(key)
                    if len(self._failures) > _MAX_KNOWN:
                        self._failures.popitem(last=False)
                return stale
            picture, media = result
            try:
                self._write(item_id, version, picture)
            except OSError:
                pass  # The current cover can still be shown if the cache cannot be written.
            return picture, media, True
