"""Read Steam version evidence and manage reversible local Workshop copies."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .file_ops import atomic_write, copy_plain_tree, is_link, write_lock
from .manifest import parse_manifest
from .paths import APP_ID, GamePaths, is_game_running
from .scanner import InstalledMod
from .workshop import fetch_workshop_details_by_ids


class VersionError(OSError):
    """An operation could not safely proceed."""


_ITEM = re.compile(r"[1-9][0-9]{0,19}\Z")
_VERSION_BATCH_SIZE = 20
_TOKEN = re.compile(r'\s+|//[^\r\n]*|"(?:[^"\\]|\\[^\r\n])*"|[{}]|[^\s"{}]+')
_STATE = ".psmm-workshop-versions"
_PUBLIC_TIMES_FILE = "workshop_update_times.json"


def parse_acf(text: str) -> dict:
    """Parse the Valve KeyValues structure used by appworkshop_*.acf."""
    root: dict = {}
    stack = [root]
    key = None
    pos = 0
    text = text.removeprefix("\ufeff")
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ValueError("ACF 包含不完整的文本项。")
        token, pos = match[0], match.end()
        if token.isspace() or token.startswith("//"):
            continue
        if token == "}":
            if key is not None or len(stack) == 1:
                raise ValueError("ACF 键值层级不完整。")
            stack.pop()
            continue
        if token == "{":
            if key is None or len(stack) >= 16:
                raise ValueError("ACF 键值层级无效。")
            node: dict = {}
            stack[-1][key] = node
            stack.append(node)
            key = None
            continue
        if token.startswith('"'):
            token = re.sub(r'\\(["\\])', r'\1', token[1:-1])
        if key is None:
            key = token.casefold()
            if key in stack[-1]:
                raise ValueError("ACF 包含重复键。")
        else:
            stack[-1][key], key = token, None
    if len(stack) != 1 or key is not None:
        raise ValueError("ACF 未写完整。")
    return root


def _timestamp(value: object) -> int | None:
    if not re.fullmatch(r"[1-9][0-9]{0,9}", str(value)):
        return None
    timestamp = int(str(value))
    return timestamp if timestamp <= 4_294_967_295 else None


def read_public_update_times(data_dir: Path) -> dict[str, int]:
    """Read display-only Steam timestamps, never local installation dates."""
    path = data_dir / _PUBLIC_TIMES_FILE
    try:
        if is_link(data_dir) or is_link(path):
            return {}
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, dict):
        return {}
    return {item_id: timestamp for item_id, value in entries.items()
            if _ITEM.fullmatch(item_id) and (timestamp := _timestamp(value)) is not None}


def remember_public_update_times(data_dir: Path, rows: list[dict]) -> None:
    updates = {row["id"]: row["public_time"] for row in rows if row["public_time"] is not None}
    if not updates:
        return
    if is_link(data_dir):
        raise OSError("不能在链接目录中保存工坊更新时间。")
    with write_lock(data_dir):
        previous = read_public_update_times(data_dir)
        if any(previous.get(item_id) != timestamp for item_id, timestamp in updates.items()):
            atomic_write(data_dir / _PUBLIC_TIMES_FILE, json.dumps(previous | updates).encode("utf-8"))


def _plain_path(path: Path) -> Path:
    if ".." in path.parts:
        raise VersionError("不支持带有上级跳转的路径。")
    path = path.absolute()
    if str(path).startswith(("\\\\", "//")):
        raise VersionError("不支持网络路径。")
    for part in (*reversed(path.parents), path):
        if is_link(part):
            raise VersionError(f"拒绝访问链接或重解析点：{part}")
    return path


def _directory(path: Path) -> bool:
    _plain_path(path)
    if path.exists() and not path.is_dir():
        raise VersionError(f"不是普通目录：{path}")
    return path.is_dir()


def _paths(paths: GamePaths) -> tuple[Path, Path]:
    if paths.game_dir is None or paths.local_mods_dir is None:
        raise VersionError("未找到游戏目录。")
    game = _plain_path(paths.game_dir)
    mods = _plain_path(paths.local_mods_dir)
    store = _plain_path(game / _STATE)
    if not game.is_dir() or mods != game / "Mods":
        raise VersionError("本地模组目录必须是游戏目录内的 Mods。")
    if paths.workshop_dir is not None:
        workshop = _plain_path(paths.workshop_dir)
        if any(p.is_relative_to(workshop) or workshop.is_relative_to(p) for p in (mods, store)):
            raise VersionError("本地模组目录或隔离目录与工坊目录重叠。")
    _directory(mods)
    _directory(store)
    return mods, store


def _read(path: Path, limit: int) -> bytes:
    _plain_path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise VersionError(f"文件过大：{path.name}")
    return data


def _installed_acf(workshop: Path | None) -> tuple[dict[str, tuple[int | None, str]], str]:
    if workshop is None or workshop.name != APP_ID or workshop.parent.name.casefold() != "content":
        return {}, "当前工坊目录无法与 Steam 库的 ACF 对应。"
    acf = workshop.parent.parent / f"appworkshop_{APP_ID}.acf"
    try:
        app = parse_acf(_read(acf, 8 * 1024 * 1024).decode("utf-8-sig")).get("appworkshop")
        if not isinstance(app, dict) or app.get("appid") != APP_ID:
            raise ValueError("不匹配的 AppID")
        entries = app.get("workshopitemsinstalled")
        if not isinstance(entries, dict):
            raise ValueError("缺少已安装条目")
        return {
            item_id: (_timestamp(entry.get("timeupdated")), str(entry.get("manifest", "")))
            for item_id, entry in entries.items() if _ITEM.fullmatch(item_id) and isinstance(entry, dict)
        }, ""
    except (OSError, ValueError):
        return {}, "未能读取此 Steam 库的已安装 ACF 记录。"


def _mod(folder: Path, source: str) -> InstalledMod | None:
    path = folder / "manifest.xml"
    try:
        content = _read(path, 512 * 1024)
    except FileNotFoundError:
        return None
    try:
        root = ET.fromstring(content.decode("utf-8-sig"))
        ids = root.findall("ID")
        mod_id = (ids[0].text or "").strip() if len(ids) == 1 else ""
    except (ValueError, ET.ParseError):
        mod_id = ""
    if not mod_id:
        raise VersionError(f"manifest.xml 缺少唯一有效的 <ID>：{folder.name}")
    manifest = parse_manifest(path)
    if manifest.parse_error or manifest.mod_id != mod_id:
        raise VersionError(f"manifest.xml 无法确认：{folder.name}")
    return InstalledMod(manifest, folder, source, folder.name if source == "workshop" else None)


def _workshop_mod(paths: GamePaths, item_id: str) -> InstalledMod:
    if not _ITEM.fullmatch(item_id) or paths.workshop_dir is None:
        raise VersionError("无效的工坊条目或未找到工坊目录。")
    folder = _plain_path(paths.workshop_dir / item_id)
    mod = _mod(folder, "workshop") if folder.is_dir() else None
    if mod is None:
        raise VersionError("找不到工坊模组的 manifest.xml。")
    return mod


def _local_ids(mods: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if mods.is_dir():
        for folder in mods.iterdir():
            if folder.name.startswith("."):
                continue
            if is_link(folder):
                raise VersionError(f"Mods 中有链接或重解析点，无法检查 ID 冲突：{folder.name}")
            if folder.is_dir():
                mod = _mod(folder, "local")
                if mod is not None:
                    result.setdefault(mod.mod_id.casefold(), []).append(folder)
    return result


def _stopped() -> None:
    if is_game_running():
        raise VersionError("请退出游戏后再切换本地模组版本。")


def _rename_new(source: Path, target: Path) -> None:
    """An atomic move that never replaces an existing destination directory."""
    _plain_path(source)
    _plain_path(target)
    if os.name == "nt":
        os.rename(source, target)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        status = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        status = libc.renamex_np(os.fsencode(source), os.fsencode(target), 4)
    else:
        raise VersionError("此系统不支持无覆盖的目录切换。")
    if status:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _tree_state(root: Path) -> dict[Path, tuple]:
    """Reject links and detect an ordinary Steam update in progress during the clone."""
    pending = [root]
    result = {}
    while pending:
        path = pending.pop()
        if is_link(path):
            raise VersionError(f"模组含有链接或重解析点：{path.name}")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode):
            raise VersionError(f"模组含有不支持的文件类型：{path.name}")
        result[path.relative_to(root)] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return result


def _record(store: Path, item_id: str) -> dict | None:
    try:
        data = json.loads(_read(store / f"{item_id}.json", 8192))
        if (not isinstance(data, dict) or data.get("id") != item_id or not isinstance(data.get("token"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", data["token"]) or not isinstance(data.get("mod_id"), str)
                or not data["mod_id"] or not isinstance(data.get("dev"), int) or not isinstance(data.get("ino"), int)):
            raise ValueError
        return data
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError) as exc:
        raise VersionError(f"工坊条目 {item_id} 的锁定记录无效。") from exc


def _name(item_id: str, token: str) -> str:
    return f"psmm-workshop-{item_id}-{token}"


def _record_state(mods: Path, store: Path, record: dict | None) -> tuple[str, Path | None]:
    if record is None:
        return "none", None
    name = _name(record["id"], record["token"])
    found = []
    for state, path in (("locked", mods / name), ("quarantined", store / name)):
        if _directory(path):
            info = path.stat()
            if (info.st_dev, info.st_ino) != (record["dev"], record["ino"]):
                raise VersionError(f"锁定副本的目录已被替换，已拒绝移动：{path.name}")
            found.append((state, path))
    if len(found) > 1:
        raise VersionError("同时存在锁定副本和隔离副本，请先人工检查目录。")
    return found[0] if found else ("none", None)


def _discard_stage(scratch: Path, store: Path) -> None:
    try:
        _plain_path(scratch)
        if (scratch.parent == store and scratch.resolve().parent == store.resolve()
                and scratch.name.startswith(".staging-")):
            shutil.rmtree(scratch, ignore_errors=True)
    except OSError:
        pass


def _clone(paths: GamePaths, installed_mod: InstalledMod, *, prepare: Callable[[Path], None] | None,
           managed: bool) -> Path:
    _stopped()
    mods, store = _paths(paths)
    item_id = installed_mod.workshop_id or ""
    if installed_mod.source != "workshop":
        raise VersionError("只能从已安装的工坊模组创建本地副本。")
    live = _workshop_mod(paths, item_id)
    if live.folder != _plain_path(installed_mod.folder) or live.mod_id != installed_mod.mod_id:
        raise VersionError("工坊模组来源或 <ID> 已变更，请重新读取。")
    with write_lock(mods):
        _stopped()
        if _local_ids(mods).get(live.mod_id.casefold()):
            raise VersionError("Mods 已有相同 <ID> 的本地模组，未覆盖。")
        if managed and _record_state(mods, store, _record(store, item_id))[0] != "none":
            raise VersionError("已存在可切换的锁定副本。")
        store.mkdir(exist_ok=True)
        token = uuid4().hex
        dest = mods / _name(item_id, token)
        scratch = Path(tempfile.mkdtemp(prefix=".staging-", dir=store))
        staged = scratch / "mod"
        try:
            evidence = _installed_acf(paths.workshop_dir)[0].get(item_id)
            original = _tree_state(live.folder)
            copy_plain_tree(live.folder, staged)
            if prepare is not None:
                prepare(staged)
            _plain_path(staged)
            _tree_state(staged)
            candidate = _mod(staged, "local")
            if candidate is None or candidate.mod_id != live.mod_id:
                raise VersionError("暂存副本未保留原样的模组 <ID>，未安装。")
            if (_tree_state(live.folder) != original
                    or _installed_acf(paths.workshop_dir)[0].get(item_id) != evidence):
                raise VersionError("复制期间 Steam 更新了工坊文件，请稍后重试。")
            _stopped()
            if _local_ids(mods).get(live.mod_id.casefold()):
                raise VersionError("复制期间出现相同 <ID> 的本地模组，未覆盖。")
            if managed:
                info = staged.stat()
                atomic_write(store / f"{item_id}.json", json.dumps({
                    "id": item_id, "token": token, "mod_id": live.mod_id,
                    "name": live.display_name, "version": live.manifest.mod_version,
                    "time": evidence[0] if evidence is not None else None,
                    "dev": info.st_dev, "ino": info.st_ino,
                }, ensure_ascii=False).encode("utf-8"))
            _stopped()
            _rename_new(staged, dest)
            return dest
        finally:
            _discard_stage(scratch, store)


def clone_workshop(paths: GamePaths, installed_mod: InstalledMod, *,
                   prepare: Callable[[Path], None] | None = None) -> Path:
    """Publish a same-ID local override. A trusted callback can edit only the staged copy.

    A derived copy (e.g. a translation) is not owned by version locking and cannot be
    removed by its unlock action. Neither kind of copy changes game save or trust data.
    """
    return _clone(paths, installed_mod, prepare=prepare, managed=False)


def change_version_lock(paths: GamePaths, item_id: str, action: str, mod_id: str) -> Path:
    _stopped()
    if not _ITEM.fullmatch(item_id):
        raise VersionError("无效的工坊 ID。")
    if action == "lock":
        mod = _workshop_mod(paths, item_id)
        if mod.mod_id != mod_id:
            raise VersionError("工坊条目已变更，请重新读取。")
        return _clone(paths, mod, prepare=None, managed=True)
    mods, store = _paths(paths)
    with write_lock(mods):
        record = _record(store, item_id)
        state, path = _record_state(mods, store, record)
        if record is None or path is None or record["mod_id"] != mod_id:
            raise VersionError("找不到与当前条目匹配的锁定副本。")
        _tree_state(path)
        candidate = _mod(path, "local")
        if candidate is None or candidate.mod_id != mod_id:
            raise VersionError("副本 <ID> 已改变，已拒绝切换。")
        if action == "unlock" and state == "locked":
            target = store / path.name
        elif action == "restore" and state == "quarantined":
            if _local_ids(mods).get(mod_id.casefold()):
                raise VersionError("Mods 已有相同 <ID> 的本地模组，未恢复。")
            target = mods / path.name
        else:
            raise VersionError("锁定状态已变更，请重新读取。")
        _stopped()
        _rename_new(path, target)
        return target


def workshop_versions(paths: GamePaths, *, online: bool = False) -> dict:
    workshop = _plain_path(paths.workshop_dir) if paths.workshop_dir is not None else None
    acf, acf_note = _installed_acf(workshop)
    notes = [acf_note] if acf_note else []
    rows: dict[str, dict] = {}
    ignored = 0
    if workshop is not None and _directory(workshop):
        for folder in sorted(workshop.iterdir()):
            if not _ITEM.fullmatch(folder.name):
                continue
            try:
                mod = _mod(folder, "workshop") if _directory(folder) else None
            except OSError:
                ignored += 1
                continue
            if mod is None:
                continue
            installed, manifest = acf.get(folder.name, (None, ""))
            rows[folder.name] = {"id": folder.name, "name": mod.display_name, "mod_id": mod.mod_id,
                                 "version": mod.manifest.mod_version, "installed_time": installed,
                                 "steam_manifest": manifest, "installed": True}
    if ignored:
        notes.append(f"已跳过 {ignored} 个无法安全读取的工坊目录。")

    records: dict[str, tuple[dict, str, Path]] = {}
    local_blocked = False
    local_ids = {}
    try:
        mods, store = _paths(paths)
        if store.is_dir():
            for file in store.glob("*.json"):
                if not _ITEM.fullmatch(file.stem):
                    continue
                try:
                    record = _record(store, file.stem)
                    state, path = _record_state(mods, store, record)
                except OSError as exc:
                    notes.append(str(exc))
                    if file.stem in rows:
                        rows[file.stem]["record_error"] = True
                    continue
                if record is not None and path is not None:
                    records[file.stem] = (record, state, path)
        local_ids = _local_ids(mods)
    except OSError as exc:
        notes.append(str(exc))
        local_blocked = True

    for item_id, (record, _, _) in records.items():
        rows.setdefault(item_id, {"id": item_id, "name": record.get("name") or record["mod_id"],
                                  "mod_id": record["mod_id"], "version": "", "installed_time": None,
                                  "steam_manifest": "", "installed": False})

    remote = {}
    failed = set()
    if online:
        item_ids = list(rows)
        for start in range(0, len(item_ids), _VERSION_BATCH_SIZE):
            batch = item_ids[start:start + _VERSION_BATCH_SIZE]
            try:
                allowed = set(batch)
                remote.update({item.published_file_id: _timestamp(item.time_updated)
                               for item in fetch_workshop_details_by_ids(batch) if item.published_file_id in allowed})
            except (OSError, ValueError, TypeError, AttributeError):
                failed.update(batch)
        if failed:
            notes.append("部分 Steam 查询未完成；可稍后重试。")

    for item_id, row in rows.items():
        public = remote.get(item_id)
        installed_time = row["installed_time"]
        if installed_time is None or public is None or public < installed_time:
            status = "unknown"
        else:
            status = "update" if public > installed_time else "current"
        public_state = ("unchecked" if not online else "failed" if item_id in failed else
                        "unavailable" if item_id not in remote else "missing_time" if public is None else "available")
        status_reason = ("not_installed" if not row["installed"] else "missing_installed_time") if installed_time is None else (
            "older_public_time" if public is not None and public < installed_time else None)
        record, state, path = records.get(item_id, ({}, "none", None))
        mod_id = row["mod_id"]
        collision = bool([folder for folder in local_ids.get(mod_id.casefold(), []) if folder != path])
        action = {"locked": "unlock", "quarantined": "restore", "none": "lock"}[state]
        lock_mod_id = record.get("mod_id", mod_id)
        collision = collision or (action == "restore" and bool(local_ids.get(lock_mod_id.casefold())))
        if row.get("record_error") or (action != "unlock" and (local_blocked or collision)):
            action = None
        locked_time = _timestamp(record.get("time"))
        row.update(public_time=public, public_state=public_state, status_reason=status_reason,
                   status=status, lock_state=state, local_conflict=collision,
                   locked_time=locked_time, locked_version=record.get("version"), lock_mod_id=lock_mod_id,
                   local_path=str(path) if path is not None else None, action=action)

    return {"rows": sorted(rows.values(), key=lambda row: (row["name"].casefold(), row["id"])),
            "notes": notes, "game_running": is_game_running(), "online": online}
