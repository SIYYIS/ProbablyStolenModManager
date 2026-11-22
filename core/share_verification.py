"""Share only ordered mod IDs, source hints and evidence about installed files."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .paths import APP_ID, GamePaths


MAX_CODE_BYTES = 512 * 1024
MAX_ORDER = 4096
MAX_SCAN_ENTRIES = 4096
MAX_SCAN_BYTES = 64 * 1024**2  # per source
MAX_MANIFEST_BYTES = 256 * 1024
MAX_MOD_ENTRIES = 4096
MAX_MOD_BYTES = 512 * 1024**2
MAX_TOTAL_ENTRIES = 16_384
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_DEPTH = 32
MAX_RELATIVE_BYTES = 4096
_CHUNK = 1024**2
_WORKSHOP_ID = re.compile(r"[0-9]{1,20}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_HASH_DOMAIN = b"PSMM2\x00mod-tree-sha256\x00"


class ShareError(ValueError):
    """The manifest or the evidence needed to verify it was not usable."""


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512 and bool(value.strip()) and "\0" not in value


def _validate_order(name: str, order: list[str]) -> None:
    if (not isinstance(name, str) or not 0 < len(name.strip()) <= 128 or len(order) > MAX_ORDER
            or not all(map(_valid_id, order)) or len(set(order)) != len(order)):
        raise ShareError("Invalid share name or mod order")
    name.encode("utf-8")
    for mid in order:
        mid.encode("utf-8")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ShareError("Duplicate field in manifest")
    return result


def read_share(code: str) -> dict:
    """Decode either wire format with independent encoded and inflated size limits."""
    if len(code) > MAX_CODE_BYTES:
        raise ShareError("Share code is too large")
    prefix, _, text = code.strip().partition(":")
    if prefix not in ("PSMM1", "PSMM2") or not text:
        raise ShareError("Unsupported share format")
    version = 2 if prefix == "PSMM2" else 1
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(base64.b64decode(text, altchars=b"-_", validate=True), MAX_CODE_BYTES + 1)
        if len(raw) > MAX_CODE_BYTES or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ShareError("Invalid or oversized compressed manifest")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys if version == 2 else None)
        if not isinstance(payload, dict) or payload.get("v") != version:
            raise ShareError("Share version does not match its prefix")
        if version == 2 and (set(payload) != {"v", "appid", "name", "mods"}
                             or type(payload["v"]) is not int or payload["appid"] != APP_ID):
            raise ShareError("Invalid verified manifest or wrong AppID")
        mods = payload["mods"]
        if not isinstance(mods, list) or len(mods) > MAX_ORDER or not all(isinstance(item, dict) for item in mods):
            raise ShareError("Invalid mod list")
        _validate_order(payload["name"], [item["id"] for item in mods])
        rows = []
        for item in mods:
            hint = item.get("workshop_id")
            valid_hint = isinstance(hint, str) and bool(_WORKSHOP_ID.fullmatch(hint))
            row = {"id": item["id"], "workshop_id": hint if valid_hint else None}
            if version == 2:
                if (set(item) != {"id", "workshop_id", "source", "fingerprint"}
                        or (hint is not None and not valid_hint) or item["source"] not in ("local", "workshop")
                        or not isinstance(item["fingerprint"], str) or not _DIGEST.fullmatch(item["fingerprint"])):
                    raise ShareError("Invalid verified mod row")
                row.update(source=item["source"], fingerprint=item["fingerprint"])
            rows.append(row)
        return {**({"appid": APP_ID} if version == 2 else {}), "v": version, "name": payload["name"], "mods": rows}
    except (ValueError, TypeError, KeyError, RecursionError, zlib.error) as exc:
        raise ShareError("Invalid share code") from exc


@dataclass
class _Budget:
    entries: int
    size: int

    def spend(self, *, entries: int = 0, size: int = 0) -> None:
        if entries > self.entries or size > self.size:
            raise ShareError("Filesystem verification limit exceeded")
        self.entries -= entries
        self.size -= size


@dataclass(frozen=True)
class _Stamp:
    mode: int
    device: int
    inode: int
    size: int
    modified: int
    changed: int

    @classmethod
    def from_stat(cls, info: os.stat_result) -> _Stamp:
        return cls.checked(stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)

    @classmethod
    def checked(cls, mode: int, device: int, inode: int, size: int, modified: int, changed: int, links: int) -> _Stamp:
        if mode not in (stat.S_IFDIR, stat.S_IFREG) or not inode or (mode == stat.S_IFREG and links != 1):
            raise ShareError("Link or unsupported filesystem object")
        return cls(mode, device, inode, size, modified, changed)

    @property
    def identity(self) -> tuple[int, int, int]:
        return self.mode, self.device, self.inode


def _stat(path: Path) -> _Stamp | None:
    try:
        if os.name == "nt":
            handle = _open_windows(path, read=False)
            try:
                return _windows_stamp(handle)
            finally:
                _close_handle(handle)
        return _Stamp.from_stat(path.lstat())
    except FileNotFoundError:
        return None


class _Snapshot:
    """Track objects observed in order, including absent manifests and root paths."""

    def __init__(self, root: Path):
        self.root = root
        self.ancestors: dict[Path, tuple[int, int, int]] = {}
        self.entries: dict[Path, _Stamp | None] = {}
        for parent in reversed(root.parents):
            stamp = _stat(parent)
            if stamp is None or stamp.mode != stat.S_IFDIR:
                raise ShareError("Parent directory is unavailable")
            self.ancestors[parent] = stamp.identity

    def watch(self, path: Path) -> _Stamp | None:
        stamp = _stat(path)
        if self.entries.setdefault(path, stamp) != stamp:
            raise ShareError("Filesystem changed during verification")
        return stamp

    def _check_ancestor(self, path: Path) -> None:
        stamp = _stat(path)
        if stamp is None or stamp.identity != self.ancestors[path]:
            raise ShareError("Parent directory changed during verification")

    def check_path(self, path: Path) -> None:
        for parent in (*reversed(path.parents), path):
            if parent in self.ancestors:
                self._check_ancestor(parent)
            else:
                self.watch(parent)

    def check(self) -> None:
        for path in self.ancestors:
            self._check_ancestor(path)
        for path in self.entries:
            self.watch(path)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _BasicInfo(ctypes.Structure):
        _fields_ = [(field, ctypes.c_int64) for field in ("created", "accessed", "modified", "changed")] + [
            ("attributes", wintypes.DWORD),
        ]

    class _FileInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD)] + [
            (field, wintypes.FILETIME) for field in ("created", "accessed", "modified")
        ] + [(field, wintypes.DWORD) for field in ("volume", "size_high", "size_low", "links", "id_high", "id_low")]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel.CreateFileW
    _create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _get_basic_info = _kernel.GetFileInformationByHandleEx
    _get_basic_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(_BasicInfo), wintypes.DWORD]
    _get_basic_info.restype = wintypes.BOOL
    _get_file_info = _kernel.GetFileInformationByHandle
    _get_file_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _get_file_info.restype = wintypes.BOOL

    def _open_windows(path: Path, *, read: bool) -> int:
        access, sharing = (0x80000000, 1) if read else (0x80, 7)  # READ or READ_ATTRIBUTES
        nofollow = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
        directories = 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS allows metadata handles for directories
        handle = _create_file("\\\\?\\" + str(path), access, sharing, None, 3, nofollow | directories, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def _windows_stamp(handle: int) -> _Stamp:
        basic, info = _BasicInfo(), _FileInfo()
        if not _get_basic_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)) or not _get_file_info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (basic.attributes | info.attributes) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ShareError("Reparse point")
        mode = stat.S_IFDIR if basic.attributes & stat.FILE_ATTRIBUTE_DIRECTORY else stat.S_IFREG
        return _Stamp.checked(mode, info.volume, (info.id_high << 32) | info.id_low,
                              (info.size_high << 32) | info.size_low, basic.modified, basic.changed, info.links)


def _fd_stamp(fd: int) -> _Stamp:
    if os.name == "nt":
        return _windows_stamp(msvcrt.get_osfhandle(fd))
    return _Stamp.from_stat(os.fstat(fd))


def _open_fd(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    handle = _open_windows(path, read=True)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _close_handle(handle)
        raise


@contextmanager
def _checked_file(snapshot: _Snapshot, path: Path):
    snapshot.check_path(path)
    expected = snapshot.entries[path]
    if expected is None or expected.mode != stat.S_IFREG:
        raise ShareError("Expected an ordinary file")
    with os.fdopen(_open_fd(path), "rb") as stream:
        if _fd_stamp(stream.fileno()) != expected:
            raise ShareError("File was replaced before reading")
        snapshot.check_path(path)
        yield stream
        if _fd_stamp(stream.fileno()) != expected:
            raise ShareError("File was changed while reading")
        snapshot.check_path(path)


def _children(snapshot: _Snapshot, path: Path, *budgets: _Budget) -> list[Path]:
    snapshot.check_path(path)
    children = []
    with os.scandir(path) as entries:
        for entry in entries:
            for budget in budgets:
                budget.spend(entries=1)
            children.append(path / entry.name)
    snapshot.check_path(path)
    return sorted(children)  # Same candidate precedence as the installed-mod scanner.


@dataclass(frozen=True)
class _Candidate:
    folder: Path
    name: str
    workshop_id: str | None
    valid_manifest: bool


def _scan(root: Path | None, source: str) -> tuple[dict[str, _Candidate], _Snapshot | None]:
    if root is None:
        return {}, None
    root = root.absolute()
    if ".." in root.parts or str(root).startswith(("\\\\", "//")):
        raise ShareError("Unsupported mod root")
    snapshot = _Snapshot(root)
    root_stamp = snapshot.watch(root)
    if root_stamp is None:
        return {}, snapshot
    if root_stamp.mode != stat.S_IFDIR:
        raise ShareError("Mod root is not a directory")

    catalog = {}
    budget = _Budget(MAX_SCAN_ENTRIES, MAX_SCAN_BYTES)
    for folder in _children(snapshot, root, budget):
        if source == "local" and folder.name.startswith("."):
            continue
        folder_stamp = snapshot.watch(folder)
        if folder_stamp is None:
            raise ShareError("Candidate vanished during discovery")
        if folder_stamp.mode != stat.S_IFDIR:
            continue
        path = folder / "manifest.xml"
        stamp = snapshot.watch(path)
        if stamp is None or stamp.mode == stat.S_IFDIR:
            continue
        if stamp.size > MAX_MANIFEST_BYTES:
            raise ShareError("Mod manifest is too large")
        budget.spend(size=stamp.size)
        with _checked_file(snapshot, path) as stream:
            raw = stream.read(stamp.size + 1)
            if len(raw) != stamp.size:
                raise ShareError("Mod manifest changed during discovery")
        text = raw.decode("utf-8-sig", errors="replace")
        if "<!DOCTYPE" in text:
            raise ShareError("Unsupported mod manifest declaration")
        try:
            xml = ET.fromstring(text)
        except ET.ParseError:
            mid, name, valid = folder.name, folder.name, False
        else:
            # Match scanner.py's ID lookup and folder-name fallback, not the folder label alone.
            mid = (xml.findtext("ID") or "").strip() or folder.name
            name = (xml.findtext("Name") or "").strip() or mid
            valid = True
        match = re.fullmatch(r"([0-9]{7,12})" if source == "workshop" else r"ws_([0-9]{7,12})", folder.name)
        catalog[mid] = _Candidate(folder, name[:512], match[1] if match else None, valid)
    return catalog, snapshot


def _fingerprint(folder: Path, total: _Budget) -> tuple[str, _Snapshot]:
    """Hash sorted, framed relative UTF-8 paths and full file bytes, not local metadata."""
    snapshot = _Snapshot(folder)
    root = snapshot.watch(folder)
    if root is None or root.mode != stat.S_IFDIR:
        raise ShareError("Mod folder is unavailable")
    budget = _Budget(MAX_MOD_ENTRIES, MAX_MOD_BYTES)
    nodes = []

    def walk(directory: Path, depth: int) -> None:
        for path in _children(snapshot, directory, budget, total):
            relative = path.relative_to(folder).as_posix().encode("utf-8")
            if len(relative) > MAX_RELATIVE_BYTES or depth > MAX_DEPTH:
                raise ShareError("Mod path exceeds verification limits")
            stamp = snapshot.watch(path)
            if stamp is None or stamp.device != root.device:
                raise ShareError("Mod tree changed or spans filesystems")
            nodes.append((relative, path, stamp))
            if stamp.mode == stat.S_IFDIR:
                walk(path, depth + 1)

    walk(folder, 1)
    digest = hashlib.sha256(_HASH_DOMAIN)
    for relative, path, stamp in sorted(nodes):
        is_dir = stamp.mode == stat.S_IFDIR
        digest.update((b"D" if is_dir else b"F") + len(relative).to_bytes(4, "big") + relative)
        if not is_dir:
            budget.spend(size=stamp.size)
            total.spend(size=stamp.size)
            digest.update(stamp.size.to_bytes(8, "big"))
            with _checked_file(snapshot, path) as stream:
                remaining = stamp.size
                while remaining:
                    chunk = stream.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise ShareError("Mod file shrank while hashing")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise ShareError("Mod file grew while hashing")
    return digest.hexdigest(), snapshot


@dataclass(frozen=True)
class ShareEvidence:
    source: str | None = None
    name: str | None = None
    workshop_id: str | None = None
    fingerprint: str | None = None
    missing: bool = False


_UNSAFE = (OSError, ValueError, RecursionError)


def inspect_share(paths: GamePaths, order: list[str], *, fingerprint: bool = True) -> dict[str, ShareEvidence]:
    """Observe the effective copies; uncertain discovery is not absence or a fallback."""
    if not order:
        return {}
    catalogs = {}
    guards = {}
    for source, root in (("local", paths.local_mods_dir), ("workshop", paths.workshop_dir)):
        try:
            catalogs[source], guards[source] = _scan(root, source)
        except _UNSAFE:
            catalogs[source] = None

    evidence = {}
    trees = {}
    total = _Budget(MAX_TOTAL_ENTRIES, MAX_TOTAL_BYTES)
    local, workshop = catalogs["local"], catalogs["workshop"]
    for mid in order:
        row = ShareEvidence()
        if local is not None:
            w = workshop.get(mid) if workshop is not None else None
            candidate = local.get(mid) or w
            if candidate is not None:
                source = "local" if mid in local else "workshop"
                row = ShareEvidence(source, candidate.name, w.workshop_id if w else candidate.workshop_id)
                if fingerprint and candidate.valid_manifest:
                    try:
                        value, trees[mid] = _fingerprint(candidate.folder, total)
                        row = replace(row, fingerprint=value)
                    except _UNSAFE:
                        pass
            elif workshop is not None:
                row = ShareEvidence(missing=True)
        evidence[mid] = row

    for mid, snapshot in trees.items():
        try:
            snapshot.check()
        except _UNSAFE:
            evidence[mid] = replace(evidence[mid], fingerprint=None)
    for source, snapshot in guards.items():
        try:
            if snapshot is not None:
                snapshot.check()
        except _UNSAFE:
            catalogs[source] = None
    if catalogs["local"] is None:
        return dict.fromkeys(order, ShareEvidence())
    if catalogs["workshop"] is None:
        for mid, row in evidence.items():
            evidence[mid] = (replace(row, workshop_id=local[mid].workshop_id) if row.source == "local"
                             else ShareEvidence())
    return evidence


def create_verified_code(paths: GamePaths, name: str, order: list[str]) -> str:
    """Export an exact, complete PSMM2 manifest, or refuse to produce a code."""
    try:
        _validate_order(name, order)
        mods = []
        for mid, row in inspect_share(paths, order).items():
            if row.fingerprint is None:
                raise ShareError("Could not fingerprint the effective mod")
            mods.append({"id": mid, "source": row.source, "workshop_id": row.workshop_id,
                         "fingerprint": row.fingerprint})
        payload = {"v": 2, "appid": APP_ID, "name": name, "mods": mods}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_CODE_BYTES:
            raise ShareError("Verified manifest is too large")
        code = "PSMM2:" + base64.urlsafe_b64encode(zlib.compress(raw, level=9)).decode("ascii")
        if len(code) > MAX_CODE_BYTES:
            raise ShareError("Verified share code is too large")
        return code
    except UnicodeError as exc:
        raise ShareError("Invalid UTF-8 share manifest") from exc
