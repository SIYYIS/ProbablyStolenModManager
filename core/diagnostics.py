"""Bounded, read-only diagnostics and the verified Windows dev.txt switch."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .paths import GAME_EXE_NAME, GamePaths

LOG_LIMIT = 768 * 1024
SAVE_LIMIT = 2 * 1024 * 1024
CONFIG_LIMIT = 48 * 1024
CONFIG_TOTAL = 192 * 1024
CONFIG_COUNT = 48
ENTRY_SCAN_LIMIT = 512
REPORT_LIMIT = 200
_DEV_BUILD = "0.46D / Steam 26525552"
# Decompiled DevTools.IsEnabled / MarkerPath in this exact installed managed assembly.
_DEV_SHA256 = "725cb999d019dff1ff31bed353c7f9bb8d81a7605a15de1f80f5a3a6cf7b1d05"
_ASSEMBLY = Path(f"{Path(GAME_EXE_NAME).stem}_Data/Managed/Assembly-CSharp.dll")
_SECRET_KEY = re.compile(
    r"api[_ -]?key|access[_ -]?key|private[_ -]?key|client[_ -]?secret|secret|token|password|passwd|"
    r"passphrase|pwd|authorization|credential|cookie|session[_ -]?(?:id|key)|steam[_ -]?auth|"
    r"connection[_ -]?string|webhook|proxy[_ -]?url|user[_ -]?name|e[_ -]?mail",
    re.I,
)
_SECRET_ASSIGN = re.compile(
    r"(?im)(?<![\w.-])(?P<key>[\w.-]*(?:api[_ -]?key|access[_ -]?key|private[_ -]?key|client[_ -]?secret|"
    r"secret|token|password|passwd|passphrase|pwd|authorization|credential|cookie|"
    r"session[_ -]?(?:id|key)|steam[_ -]?auth|connection[_ -]?string|webhook|proxy[_ -]?url|user[_ -]?name|"
    r"e[_ -]?mail)[\w.-]*[\"']?[^\S\r\n]*[:=][^\S\r\n]*).*?$"
)
_PEM = re.compile(r"-----BEGIN [^\r\n-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^\r\n-]*PRIVATE KEY-----|\Z)", re.I)
_AUTH = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I)
_TOKEN = re.compile(r"\b(?:sk-(?:proj-)?[\w-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b")
_URL = re.compile(r"\b(?:https?|socks5h?)://[^\s<>\"']+", re.I)
_WIN_PATH = re.compile(r"(?i)(?:file:/{2,3})?(?:(?<!\w)[A-Z]:[\\/]|\\\\(?:\?\\|[^\\\s]))[^\r\n<>\"'|]*")
_POSIX_PATH = re.compile(r"(?<![\w/])(?:file://[^\r\n<>\"']*|"
                         r"/(?:home|Users|var|tmp|mnt|Volumes|opt)/[^\r\n<>\"']*|"
                         r"/(?:[^\s<>\"'/()]+/)+[^\s<>\"'()]*)", re.I)
_STEAM_ID = re.compile(r"\b7656119\d{10}\b")


class DiagnosticsError(Exception):
    """A user-facing error which must not contain private paths or raw file data."""


def scrub(text: str) -> str:
    text = _PEM.sub("[redacted private key]", text)
    text = _SECRET_ASSIGN.sub(r"\g<key>[redacted]", text)
    text = _AUTH.sub("[redacted authorization]", text)
    text = _TOKEN.sub("[redacted token]", text)
    text = _URL.sub("[redacted URL]", text)
    text = _WIN_PATH.sub("[redacted path]", text)
    text = _POSIX_PATH.sub("[redacted path]", text)
    return _STEAM_ID.sub("[redacted Steam ID]", text)


def _plain(path: Path, *, directory: bool = False) -> os.stat_result | None:
    path = path.absolute()
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise DiagnosticsError("拒绝访问链接、重解析点或其下的文件。")
        check = stat.S_ISDIR if item != path or directory else stat.S_ISREG
        if not check(info.st_mode) or (item == path and not directory and info.st_nlink != 1):
            raise DiagnosticsError("目标不是普通文件或目录。")
    return info


def _open_checked(path: Path, before: os.stat_result):
    # Compare the opened object before reading; a changed path is never trusted.
    stream = path.open("rb")
    try:
        opened = os.fstat(stream.fileno())
        still = _plain(path)
        if still is None or not os.path.samestat(before, opened) or not os.path.samestat(still, opened):
            raise DiagnosticsError("文件在读取时发生变化，请重试。")
    except BaseException:
        stream.close()
        raise
    return stream


def _small_file(path: Path, limit: int) -> bytes | None:
    info = _plain(path)
    if info is None:
        return None
    if info.st_size > limit:
        raise DiagnosticsError("文件超过读取上限。")
    with _open_checked(path, info) as stream:
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise DiagnosticsError("文件过大或读取时发生变化。")
    return data


@dataclass(frozen=True)
class LogSnapshot:
    status: str
    text: str = ""
    modified_at: str | None = None
    tail_only: bool = False


def read_player_log(paths: GamePaths) -> LogSnapshot:
    if paths.save_data_dir is None:
        return LogSnapshot("missing")
    path = paths.save_data_dir / "Player.log"
    try:
        info = _plain(path)
        if info is None:
            return LogSnapshot("missing")
        with _open_checked(path, info) as stream:
            start = max(0, info.st_size - LOG_LIMIT)
            if start:
                stream.seek(start - 1)
                boundary = stream.read(1) == b"\n"
            else:
                boundary = True
            data = stream.read(min(LOG_LIMIT, info.st_size))
        if not boundary:
            data = data.partition(b"\n")[2]
        if start:
            # A tail can start inside a multi-line credential or error record.
            end_pem = re.search(rb"-----END [^\r\n-]*PRIVATE KEY-----", data, re.I)
            if end_pem and b"-----BEGIN " not in data[:end_pem.start()]:
                data = data[end_pem.end():].partition(b"\n")[2]
        return LogSnapshot("ok", data.decode("utf-8-sig", errors="replace"),
                           datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(), bool(start))
    except (DiagnosticsError, OSError, ValueError):
        return LogSnapshot("unreadable")


def _enabled(paths: GamePaths) -> dict:
    if paths.save_file is None:
        return {"status": "missing", "ids": []}
    try:
        raw = _small_file(paths.save_file, SAVE_LIMIT)
        if raw is None:
            return {"status": "missing", "ids": []}
        order = json.loads(raw).get("modOrder")
        if isinstance(order, dict):
            order = order.get("value")
        if (not isinstance(order, list) or len(order) > 4096 or
                any(not isinstance(mid, str) or not mid.strip() or len(mid) > 512 for mid in order)):
            raise ValueError
        ids = [scrub(mid).encode("utf-8", errors="replace").decode("utf-8") for mid in order]
        return {"status": "ok", "ids": ids}
    except (DiagnosticsError, OSError, ValueError, AttributeError, RecursionError):
        return {"status": "unreadable", "ids": []}


_AUDIT = re.compile(
    r'^\[ModLoader\] audit: (.+?) v\S* source=(?:Local|Workshop) workshop=\S+ owner=\S+ '
    r'running=(True|False) trust=\S+ caps=.*? hash=\S*(?: .*)?$'
)
_AUDIT_ERROR = re.compile(r' error="(.*)"$')
_OBSERVED = re.compile(r"^\[ModLoader\]\s+(Initialized|Init failed|Registered \(disabled, [^)]+\)): (.+) v\S*$")
_HOOK = re.compile(r"^\[ModHook\] '.*?' handler \S+ from '(.+?)' threw:\s*")
_TAGGED_ERROR = re.compile(r"^\[([^\]]+)\]\s*:?\s*(?:Error|Exception|Failed to patch)\b", re.I)
_ERROR = re.compile(r"\b(?:\w*Exception(?=\s*:|\s*$)|failed to patch|crash!!!)\b|crash!!!", re.I)
_STACK = re.compile(r"^\s*(?:at |\(wrapper |--- End of |Rethrow as |\S*Exception\s*:)")
_FRAMEWORK_TAGS = {"modloader", "modhook", "harmony", "harmonyx", "devtools", "unity", "steam"}


def parse_log(text: str, *, tail_only: bool = False) -> dict:
    """Only use identity emitted by the game; neither a stack nor proximity is ownership."""
    audit: dict[str, dict] = {}
    loader: dict[str, dict] = {}
    events: list[dict] = []
    pending: dict | None = None
    errors = 0

    def emit(line: int, content: str, kind: str, evidence: str, reported: str | None = None,
             mod_id: str | None = None) -> dict:
        nonlocal errors
        errors += 1
        event = {"line": line, "kind": kind, "evidence": evidence,
                 "reported": scrub(reported)[:512] if reported else None,
                 "mod_id": scrub(mod_id)[:512] if mod_id else None, "message": scrub(content)[:1800], "stack": []}
        events.append(event)
        if len(events) > REPORT_LIMIT:
            events.pop(0)
        return event

    # Parse raw in memory; only return scrubbed text. In particular, generic log tags
    # are not manifest IDs, and ModHook can report an assembly instead of a mod name.
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("[ModLoader] audit: game "):
            audit.clear()
            pending = None
            continue
        match = _AUDIT.fullmatch(line)
        if match:
            mid, running = match.groups()
            status = "running_reported" if running == "True" else "not_running_reported"
            audit[mid] = {"mod_id": mid, "reported": mid, "status": status, "line": number}
            error = _AUDIT_ERROR.search(line)
            if error:
                emit(number, error[1], "loader", "audit_id", mid, mid)
            pending = None
            continue
        match = _OBSERVED.fullmatch(line)
        if match:
            label, name = match.groups()
            status = {"Initialized": "initialized", "Init failed": "init_failed"}.get(label, "registered_disabled")
            loader[name] = {"mod_id": None, "reported": name, "status": status, "line": number}
            pending = emit(number, line, "loader", "loader_name", name) if status == "init_failed" else None
            continue
        hook = _HOOK.match(line)
        if hook:
            pending = emit(number, line, "hook", "hook_owner", hook[1])
            continue
        tag = _TAGGED_ERROR.match(line)
        if tag and tag[1].casefold() not in _FRAMEWORK_TAGS:
            pending = emit(number, line, "patch" if "failed to patch" in line.lower() else "exception", "log_tag", tag[1])
            continue
        if pending and _STACK.match(raw):
            if pending["evidence"] not in {"hook_owner", "log_tag"} and _ERROR.search(line) and not pending["stack"]:
                pending = None
            else:
                if len(pending["stack"]) < 8:
                    pending["stack"].append(scrub(line)[:1200])
                continue
        if _ERROR.search(line) or line.startswith("[ModHook]") and "failed:" in line.lower():
            patch = "harmony" in line.casefold() or "failed to patch" in line.casefold()
            pending = emit(number, line, "patch" if patch else "exception", "unattributed")
        else:
            pending = None

    observed = sorted((*audit.values(), *loader.values()), key=lambda row: row["line"], reverse=True)
    for row in observed[:REPORT_LIMIT]:
        row["reported"] = scrub(row["reported"])[:512]
        if row["mod_id"] is not None:
            row["mod_id"] = scrub(row["mod_id"])[:512]
    return {"events": events[::-1], "event_count": errors, "observed": observed[:REPORT_LIMIT],
            "observed_count": len(observed), "tail_line_numbers": tail_only}


def analyze(paths: GamePaths) -> dict:
    log = read_player_log(paths)
    return {"log": {"status": log.status, "modified_at": log.modified_at, "tail_only": log.tail_only},
            "enabled": _enabled(paths), **parse_log(log.text, tail_only=log.tail_only)}


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _clean_json(value):
    if isinstance(value, dict):
        sensitive_entry = any(
            k.casefold() in {"key", "name", "id"} and isinstance(v, str) and _SECRET_KEY.search(v)
            for k, v in value.items()
        )
        return {scrub(k): "[redacted]" if _SECRET_KEY.search(k) or sensitive_entry and k.casefold() in
                {"value", "default", "defaultvalue", "currentvalue"} else _clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_json(v) for v in value]
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, int) and not isinstance(value, bool) and _STEAM_ID.fullmatch(str(value)):
        return "[redacted Steam ID]"
    return value


def _configs(root: Path | None) -> tuple[dict[str, bytes], str, int]:
    if root is None:
        return {}, "missing", 0
    entries: dict[str, bytes] = {}
    try:
        if _plain(root, directory=True) is None:
            return entries, "missing", 0
        with os.scandir(root) as children:
            names = sorted(item.name for item in islice(children, ENTRY_SCAN_LIMIT + 1))
        if len(names) > ENTRY_SCAN_LIMIT:
            return entries, "too_many", 0
    except (DiagnosticsError, OSError):
        return entries, "unreadable", 0
    skipped, size = 0, 0
    for name in names:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,124}\.json", name, re.I) or scrub(name) != name
                or len(entries) >= CONFIG_COUNT or size >= CONFIG_TOTAL):
            skipped += 1
            continue
        try:
            raw = _small_file(root / name, CONFIG_LIMIT)
            value = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
            if not isinstance(value, (dict, list)):
                raise ValueError
            cleaned = _json_bytes(_clean_json(value))
            if len(cleaned) > CONFIG_LIMIT or size + len(cleaned) > CONFIG_TOTAL:
                raise ValueError
        except (DiagnosticsError, OSError, ValueError, RecursionError):
            skipped += 1
            continue
        entries[f"ModConfigs/{name}"] = cleaned
        size += len(cleaned)
    return entries, "ok", skipped


@dataclass(frozen=True)
class SupportSnapshot:
    files: dict[str, bytes]
    revision: str
    log_status: str
    tail_only: bool
    enabled_status: str
    config_status: str
    skipped: int

    def preview(self) -> dict:
        return {"revision": self.revision, "log_status": self.log_status, "tail_only": self.tail_only,
                "enabled_status": self.enabled_status, "config_status": self.config_status, "skipped": self.skipped,
                "files": [{"name": name, "bytes": len(data),
                           "text": data.decode("utf-8") if name.startswith("ModConfigs/") else None}
                          for name, data in self.files.items()]}

    def archive(self) -> bytes:
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        return output.getvalue()


def support_snapshot(paths: GamePaths, *, include_configs: bool = False) -> SupportSnapshot:
    log, enabled = read_player_log(paths), _enabled(paths)
    configs, config_status, skipped = _configs(paths.mod_configs_dir) if include_configs else ({}, "excluded", 0)
    files = {"enabled-mods.json": _json_bytes(enabled)}
    tail_only = log.tail_only
    if log.status == "ok":
        clean = scrub(log.text).encode("utf-8")
        # Decoding malformed UTF-8 and replacing short secrets can grow the output.
        if len(clean) > LOG_LIMIT:
            clean = clean[-LOG_LIMIT:].partition(b"\n")[2]
            tail_only = True
        files["Player.log"] = clean
    files.update(configs)
    files["report.json"] = _json_bytes({"redaction": "best effort; review before sharing", "log": log.status,
                                        "log_tail_only": tail_only, "log_modified_at": log.modified_at,
                                        "configs": config_status, "configs_skipped": skipped})
    digest = hashlib.sha256(b"with-configs\0" if include_configs else b"without-configs\0")
    for name, data in files.items():
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return SupportSnapshot(files, digest.hexdigest(), log.status, tail_only, enabled["status"], config_status, skipped)


def _verified_game(game_dir: Path) -> bool:
    if os.name != "nt" or _plain(game_dir / GAME_EXE_NAME) is None:
        return False
    data = _small_file(game_dir / _ASSEMBLY, 32 * 1024 * 1024)
    return data is not None and hashlib.sha256(data).hexdigest() == _DEV_SHA256


def _game_running() -> bool | None:
    # Unlike the general launcher check, uncertainty here must not permit a write.
    if os.name != "nt":
        return None
    try:
        output = subprocess.check_output(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "tasklist.exe"), "/FO", "CSV", "/NH"],
            text=True, errors="replace", stderr=subprocess.DEVNULL, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = [r for r in csv.reader(io.StringIO(output), strict=True) if len(r) >= 5 and r[1].isdigit()]
        return any(r[0].casefold() == GAME_EXE_NAME.casefold() for r in rows) if rows else None
    except (OSError, subprocess.SubprocessError, KeyError, csv.Error):
        return None


def dev_status(game_dir: Path | None) -> dict:
    result = {"supported": False, "build": None, "marker": "unknown", "game_running": None,
              "editable": False, "revision": None, "reason": "此游戏安装版本的 dev.txt 支持尚未核验。"}
    if game_dir is None:
        return {**result, "reason": "未定位游戏目录。"}
    try:
        if not _verified_game(game_dir):
            return result
        result.update(supported=True, build=_DEV_BUILD)
        if _plain(game_dir / "1dev.txt") is not None:
            return {**result, "marker": "full", "reason": "检测到独立的 1dev.txt 标记，本开关不会更改它。"}
        marker = _plain(game_dir / "dev.txt")
        if marker is not None and marker.st_size:
            return {**result, "marker": "nonempty", "reason": "dev.txt 不是空文件，程序不会替换或删除它。"}
        running = _game_running()
        marker_state = "empty" if marker is not None else "absent"
        revision = hashlib.sha256(f"{game_dir.absolute()}\0{marker_state}\0{marker}".encode("utf-8")).hexdigest()
        reason = "请先关闭游戏。" if running else "无法确认游戏已退出。" if running is None else None
        return {**result, "marker": marker_state, "game_running": running, "editable": running is False,
                "revision": revision, "reason": reason}
    except (DiagnosticsError, OSError):
        return {**result, "reason": "拒绝访问链接、非普通文件或无法读取的安装目录。"}


def _remove_empty_marker(path: Path) -> None:
    # Open the reparse point itself and deny concurrent writes/deletes while checking.
    # Disposition is set on that handle, never on a subsequently resolved path.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    info = _plain(path)
    if info is None or info.st_size != 0:
        raise DiagnosticsError("dev.txt 已变化，请重新读取。")
    handle = open_file(str(path), 0x10000 | 0x80, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    try:
        # BY_HANDLE_FILE_INFORMATION consists of thirteen DWORDs (FILETIMEs are two DWORDs).
        buffer = (wintypes.DWORD * 13)()
        get_info = kernel.GetFileInformationByHandle
        get_info.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
        if not get_info(handle, ctypes.byref(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        if buffer[0] & (0x400 | 0x10) or buffer[8] or buffer[9] or buffer[10] != 1:
            raise DiagnosticsError("拒绝删除非空、链接或非普通 dev.txt。")
        if _game_running() is not False:
            raise DiagnosticsError("请先关闭游戏，再修改开发者模式。")
        disposition = kernel.SetFileInformationByHandle
        disposition.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        delete = wintypes.BOOLEAN(True)
        if not disposition(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def set_dev_marker(game_dir: Path | None, *, enabled: bool, revision: str) -> dict:
    status = dev_status(game_dir)
    if game_dir is None or not status["editable"]:
        raise DiagnosticsError(status["reason"] or "当前无法修改开发者模式。")
    if revision != status["revision"]:
        raise DiagnosticsError("开发者模式状态已变化，请重新读取。")
    if enabled == (status["marker"] == "empty"):
        return status
    marker = game_dir.absolute() / "dev.txt"
    try:
        if _plain(game_dir / "1dev.txt") is not None or _game_running() is not False:
            raise DiagnosticsError("游戏或开发者标记状态已变化，请重新读取。")
        if enabled:
            if _plain(marker) is not None:
                raise DiagnosticsError("dev.txt 已存在，程序不会覆盖。")
            with marker.open("xb"):
                pass
        else:
            _remove_empty_marker(marker)
    except OSError:
        raise DiagnosticsError("无法修改 dev.txt，请检查目录权限和文件占用。") from None
    return dev_status(game_dir)
