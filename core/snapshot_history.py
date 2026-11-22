"""Canonical slot snapshots, explicit profile links, and a view of existing backups."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .es3_manager import ProfileManager
from .file_ops import atomic_write, content_digest, is_link, staged_directory, write_lock
from .paths import GamePaths, is_game_running
from .save_slots import MAX_SIZE, SlotError, list_backups, list_slots, parse_slot, slot_path, slot_revision

_HEX = re.compile(r"[a-f0-9]{64}\Z")
_SNAPSHOT = re.compile(r"[a-f0-9]{32}\Z")
_EDIT = re.compile(r"(0|[1-9][0-9]{0,9}):(backup-[a-z0-9_]{8})\Z", re.ASCII)
_GLOBAL = re.compile(r"SaveFile_[a-zA-Z0-9_-]{1,230}\.es3\.bak\Z", re.ASCII)
_REASONS = {"manual", "pre_switch", "pre_restore"}
_MAX_CONFIG_TOTAL = 128 * 1024 * 1024
_MAX_CONFIG_ENTRIES = 4096
_MAX_DIFF = 100


class SnapshotError(ValueError):
    pass


def _plain(path: Path) -> Path:
    path = path.absolute()
    if str(path).startswith(("\\\\", "//")) or any(is_link(p) for p in (path, *path.parents)):
        raise SnapshotError("不支持网络路径、符号链接或目录联接。")
    return Path(os.path.abspath(path))


def _read(path: Path, limit: int = MAX_SIZE) -> bytes:
    path = _plain(path)
    if not path.is_file():
        raise SnapshotError("备份源或目标文件不存在，或不是普通文件。")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SnapshotError("文件或配置备份超过大小限制。")
    return data


def _json(path: Path) -> dict:
    try:
        data = json.loads(_read(path, 16_384))
        if isinstance(data, dict):
            return data
    except (ValueError, UnicodeError, RecursionError):
        pass
    raise SnapshotError("备份或关联的元数据无法读取。")


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _iso(timestamp: float | None = None) -> str:
    return (datetime.now(timezone.utc) if timestamp is None else
            datetime.fromtimestamp(timestamp, timezone.utc)).isoformat()


def _slot_id(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise SnapshotError("无效档位。")
    return value


def _check_stopped() -> None:
    if is_game_running():
        raise SnapshotError("请先关闭游戏，再创建快照、关联档位或覆盖存档。")


def _slot_fields(data: bytes, slot_id: int) -> dict[str, int] | None:
    try:
        slot = parse_slot(data, slot_id)
        return {"day": slot.day, "cash": slot.values["cash"], "favor": slot.values["favor"]}
    except SlotError:
        return None


def _mod_order(data: bytes) -> list[str] | None:
    try:
        value = json.loads(data.decode("utf-8-sig"))
        order = value.get("modOrder") if isinstance(value, dict) else None
        if isinstance(order, dict):
            order = order.get("value")
        if isinstance(order, list) and len(order) <= 4096 and all(
            isinstance(item, str) and len(item) <= 512 for item in order
        ):
            return order
    except (ValueError, UnicodeError, RecursionError):
        pass
    return None


def _differences(before: set[str], after: set[str], changed: list[str]) -> dict:
    result = {"added": sorted(after - before), "removed": sorted(before - after), "changed": sorted(changed)}
    return {"counts": {k: len(v) for k, v in result.items()}, **{k: v[:_MAX_DIFF] for k, v in result.items()}}


def _config_tree(root: Path, copy_to: Path | None = None) -> dict[str, str]:
    root = _plain(root)
    if not root.is_dir():
        raise SnapshotError("未找到 ModConfigs 目录。")
    if copy_to is not None:
        copy_to.mkdir()
    pending = [root]
    entries: dict[str, str] = {}
    remaining = _MAX_CONFIG_TOTAL
    while pending:
        directory = pending.pop()
        for node in _plain(directory).iterdir():
            if len(entries) >= _MAX_CONFIG_ENTRIES:
                raise SnapshotError("配置备份目录条目过多。")
            node = _plain(node)
            relative = node.relative_to(root)
            mode = node.stat().st_mode
            target = copy_to / relative if copy_to is not None else None
            if stat.S_ISDIR(mode):
                entries[relative.as_posix() + "/"] = "directory"
                if target is not None:
                    target.mkdir()
                pending.append(node)
            elif stat.S_ISREG(mode):
                data = _read(node, min(MAX_SIZE, remaining))
                remaining -= len(data)
                entries[relative.as_posix()] = content_digest(data)
                if target is not None:
                    atomic_write(target, data)
            else:
                raise SnapshotError("配置目录含有不支持的文件类型。")
    return entries


@dataclass(frozen=True)
class _Entry:
    id: str
    kind: str
    path: Path
    date: str
    reason: str
    slot_id: int | None = None
    profile: str | None = None
    fields: dict | None = None
    digest: str | None = None

    def summary(self) -> dict:
        return {"id": self.id, "kind": self.kind, "date": self.date, "reason": self.reason,
                "slot_id": self.slot_id, "profile": self.profile, "fields": self.fields}


class SnapshotHistory:
    def __init__(self, data_dir: Path, profiles: ProfileManager, paths: GamePaths) -> None:
        self.root = _plain(data_dir / "snapshot_history")
        self.profiles = profiles
        self.directory = _plain(paths.save_data_dir) if paths.save_data_dir is not None else None
        self.game_dir = paths.game_dir
        self.scope = (content_digest(os.path.normcase(str(self.directory)).encode("utf-8"))
                      if self.directory is not None else None)

    def _store(self) -> Path:
        root = _plain(self.root)
        for active in (self.directory, self.game_dir):
            if active is not None and root.is_relative_to(_plain(active)):
                raise SnapshotError("快照和导出目录必须在游戏及存档目录之外。")
        return root

    def _directory(self, scope: str | None = None) -> Path:
        if self.directory is None or not _plain(self.directory).is_dir():
            raise SnapshotError("未找到游戏存档目录。")
        if scope is not None and scope != self.scope:
            raise SnapshotError("存档目录已变更，请刷新后重试。")
        return self.directory

    def _profile(self, name: str):
        name = name.strip()
        if not name or len(name) > 128:
            raise SnapshotError("无效预设名称。")
        for profile in self.profiles.list_profiles():
            if profile.name == name:
                return profile
        raise SnapshotError("未找到指定预设，请刷新后重试。")

    def _link_path(self, name: str) -> Path:
        return _plain(self._store() / "links" / f"{content_digest(name.encode('utf-8'))}.json")

    def _link(self, name: str) -> dict | None:
        path = self._link_path(name)
        if not path.exists():
            return None
        link = _json(path)
        scope = link.get("scope")
        if (link.get("name") != name or not isinstance(link.get("created_at"), str)
                or not isinstance(scope, str) or not _HEX.fullmatch(scope)):
            raise SnapshotError("预设关联记录异常，请重新关联。")
        _slot_id(link.get("slot_id"))
        return link

    def set_link(self, name: str, slot_id: int | None, scope: str | None) -> None:
        with write_lock(_plain(self.profiles.profiles_dir)):
            profile = self._profile(name)
            path = self._link_path(profile.name)
            if slot_id is None:
                path.unlink(missing_ok=True)
                return
            directory = self._directory(scope)
            with write_lock(directory):
                _check_stopped()
                self._read_slot(_slot_id(slot_id))
                atomic_write(path, _json_bytes({"name": profile.name, "created_at": profile.created_at,
                                                "scope": self.scope, "slot_id": slot_id}))

    def _read_slot(self, slot_id: int) -> bytes:
        data = _read(slot_path(self._directory(), _slot_id(slot_id)))
        if not data or not data.strip(b"\0"):
            raise SnapshotError("档位为空或全零，未创建快照。")
        return data

    def _capture(self, slot_id: int, data: bytes, reason: str, profile: str | None = None) -> _Entry:
        key = uuid4().hex
        folder = _plain(self._store() / "slots" / self.scope / key)
        entry = _Entry(f"slot:{key}", "slot", folder / f"save_{slot_id}.es3", _iso(), reason,
                       slot_id, profile, _slot_fields(data, slot_id), content_digest(data))
        with staged_directory(folder) as (pending, commit):
            pending.mkdir()
            atomic_write(pending / entry.path.name, data)
            atomic_write(pending / "snapshot.json", _json_bytes({**entry.summary(), "digest": entry.digest}))
            _check_stopped()
            if self._read_slot(slot_id) != data:
                raise SnapshotError("存档在备份期间已变更，请刷新后重试。")
            commit()
        return entry

    def capture(self, slot_id: int, scope: str) -> dict:
        with write_lock(self._directory(scope)):
            _check_stopped()
            return self._capture(slot_id, self._read_slot(slot_id), "manual").summary()

    def backup_link(self, name: str) -> dict | None:
        name = name.strip()
        if not self._link(name):
            return None
        with write_lock(_plain(self.profiles.profiles_dir)):
            link = self._link(name)
            if link is None:
                return None
            if link["created_at"] != self._profile(name).created_at:
                raise SnapshotError("该预设已重新创建，请重新关联档位。")
            directory = self._directory(link["scope"])
            with write_lock(directory):
                _check_stopped()
                slot_id = link["slot_id"]
                return self._capture(slot_id, self._read_slot(slot_id), "pre_switch", name).summary()

    def _stored(self, key: str) -> _Entry:
        self._directory()
        folder = _plain(self._store() / "slots" / self.scope / key)
        meta = _json(folder / "snapshot.json")
        slot_id = _slot_id(meta.get("slot_id"))
        reason, date, profile, fields, digest = (meta.get(k) for k in ("reason", "date", "profile", "fields", "digest"))
        if (not isinstance(reason, str) or reason not in _REASONS or not isinstance(date, str)
                or not isinstance(digest, str) or not _HEX.fullmatch(digest)
                or not (profile is None or isinstance(profile, str) and len(profile) <= 128)
                or not (fields is None or isinstance(fields, dict) and fields.keys() == {"day", "cash", "favor"}
                        and all(type(v) is int for v in fields.values()))):
            raise SnapshotError("快照元数据异常。")
        try:
            if datetime.fromisoformat(date).utcoffset() is None:
                raise ValueError
        except ValueError:
            raise SnapshotError("快照时间格式异常。") from None
        return _Entry(f"slot:{key}", "slot", _plain(slot_path(folder, slot_id)), date, reason, slot_id,
                       profile, fields, digest)

    def _entry(self, identifier: str) -> _Entry:
        prefix, _, key = identifier.partition(":")
        if prefix == "slot" and _SNAPSHOT.fullmatch(key):
            return self._stored(key)
        if prefix == "edit" and (match := _EDIT.fullmatch(key)):
            self._directory()
            slot_id = _slot_id(int(match[1]))
            path = _plain(self.root.parent / "slot_backups" / self.scope / match[1] / match[2] / "original.es3")
            if path.is_file():
                return _Entry(identifier, "slot", path, _iso(path.stat().st_mtime), "slot_edit", slot_id)
        if prefix in ("global", "configs") and _GLOBAL.fullmatch(key):
            backup = _plain(self.profiles.backups_dir / key)
            path = backup if prefix == "global" else _plain(backup.with_name(backup.name + ".ModConfigs"))
            if backup.is_file() and (path.is_file() if prefix == "global" else path.is_dir()):
                return _Entry(identifier, prefix, path, _iso(backup.stat().st_mtime), "existing")
        raise SnapshotError("未找到指定备份。")

    def index(self, offset: int, limit: int, kind: str | None = None) -> dict:
        slots = list_slots(self.directory) if self.directory is not None else []
        profiles = []
        for profile in self.profiles.list_profiles():
            try:
                link = self._link(profile.name)
            except SnapshotError:
                profiles.append({"name": profile.name, "slot_id": None, "state": "invalid"})
                continue
            if link is not None:
                state = ("profile_changed" if link["created_at"] != profile.created_at else
                         "other_directory" if link["scope"] != self.scope else
                         "linked" if link["slot_id"] in slots else "missing_slot")
            else:
                state = "unlinked"
            profiles.append({"name": profile.name, "slot_id": link["slot_id"] if link else None, "state": state})
        ids = []
        if self.scope is not None and kind in (None, "slot"):
            folder = _plain(self._store() / "slots" / self.scope)
            if folder.is_dir():
                ids += [f"slot:{p.name}" for p in folder.iterdir() if _SNAPSHOT.fullmatch(p.name)]
            legacy = _plain(self.root.parent / "slot_backups" / self.scope)
            if legacy.is_dir():
                for child in legacy.iterdir():
                    if _EDIT.fullmatch(f"{child.name}:backup-00000000") and not is_link(child):
                        ids += [f"edit:{child.name}:{name}" for name in list_backups(legacy, int(child.name))]
        if kind in (None, "global", "configs"):
            _plain(self.profiles.backups_dir)
            for path in self.profiles.list_backups():
                if kind in (None, "global"):
                    ids.append(f"global:{path.name}")
                if kind in (None, "configs"):
                    ids.append(f"configs:{path.name}")
        entries = []
        for identifier in ids:
            try:
                entries.append(self._entry(identifier).summary())
            except (OSError, SnapshotError):
                continue
        entries.sort(key=lambda entry: (entry["date"], entry["id"]), reverse=True)
        return {"directory": str(self.directory) if self.directory else None, "scope": self.scope, "slots": slots,
                "profiles": profiles, "entries": entries[offset:offset+limit], "total": len(entries),
                "game_running": is_game_running()}

    def _verified_bytes(self, entry: _Entry) -> bytes:
        data = _read(entry.path)
        if entry.digest is not None and entry.digest != content_digest(data):
            raise SnapshotError("快照校验失败，文件已变更。")
        return data

    def _replace_slot(self, slot_id: int, data: bytes, expected: bytes) -> Path:
        path = _plain(slot_path(self._directory(), slot_id))
        fd, name = tempfile.mkstemp(prefix=".psmm-restore-", suffix=".tmp", dir=path.parent)
        pending = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self._read_slot(slot_id) != expected:
                raise SnapshotError("已保存恢复前快照；档位已变更，未覆盖。")
            _check_stopped()
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        return path

    def inspect(self, identifier: str) -> dict:
        entry = self._entry(identifier)
        result = {**entry.summary(), "source_name": entry.path.name, "target_path": None, "target_revision": None,
                  "current_fields": None, "identical": None, "diff": None, "game_running": is_game_running()}
        if entry.kind == "configs":
            saved = _config_tree(entry.path)
            result["source_revision"] = content_digest(_json_bytes(saved))
            try:
                if self.directory is not None and (live := _plain(self.directory / "ModConfigs")).exists():
                    current = _config_tree(live)
                    result["identical"] = current == saved
                    result["diff"] = _differences(set(current), set(saved),
                                                   [p for p in current.keys() & saved.keys() if current[p] != saved[p]])
            except (OSError, SnapshotError):
                result["diff_error"] = "无法读取当前模组配置，仍可导出备份。"
            return result
        saved = self._verified_bytes(entry)
        result["source_revision"] = content_digest(saved)
        if entry.kind == "slot":
            result["fields"] = _slot_fields(saved, entry.slot_id)
            live = slot_path(self._directory(), entry.slot_id)
        else:
            live = self.directory / "SaveFile.es3" if self.directory is not None else None
        if live is not None:
            result["target_path"] = str(live)
        try:
            current = _read(live) if live is not None and _plain(live).exists() else None
        except (OSError, SnapshotError):
            result["diff_error"] = "无法读取当前文件，仍可导出备份。"
            current = None
        if current is not None:
            result["identical"] = current == saved
            if entry.kind == "slot":
                result["current_fields"] = _slot_fields(current, entry.slot_id)
                if result["current_fields"] is not None and result["fields"] is not None:
                    result["target_revision"] = slot_revision(self.directory, entry.slot_id,
                                                              parse_slot(current, entry.slot_id))
            else:
                before, after = _mod_order(current), _mod_order(saved)
                if before is not None and after is not None:
                    common = set(before) & set(after)
                    old = [mid for mid in before if mid in common]
                    new = [mid for mid in after if mid in common]
                    result["diff"] = {**_differences(set(before), set(after), []), "order_changed": old != new}
        return result

    def restore(self, identifier: str, source_revision: str, *, target_revision: str | None = None,
                replace: bool = False, confirm: bool = False) -> dict:
        entry = self._entry(identifier)
        if not replace:
            if confirm or target_revision is not None:
                raise SnapshotError("导出副本不接受覆盖档位的参数。")
            folder = _plain(self._store() / "exports" / uuid4().hex)
            name = ("ModConfigs" if entry.kind == "configs" else "SaveFile.es3" if entry.kind == "global"
                    else f"save_{entry.slot_id}.es3")
            with staged_directory(folder) as (pending, commit):
                pending.mkdir()
                if entry.kind == "configs":
                    digest = content_digest(_json_bytes(_config_tree(entry.path, pending / name)))
                else:
                    data = self._verified_bytes(entry)
                    digest = content_digest(data)
                    atomic_write(pending / name, data)
                if source_revision != digest:
                    raise SnapshotError("备份已变更，请重新检查差异后再导出。")
                commit()
            return {"message": "已导出独立副本，未修改游戏存档。", "path": str(folder / name)}

        if entry.kind != "slot" or confirm is not True or target_revision is None:
            raise SnapshotError("仅支持明确确认后的同档位覆盖；请先检查差异。")
        directory = self._directory()
        with write_lock(directory):
            _check_stopped()
            data = self._verified_bytes(entry)
            if content_digest(data) != source_revision:
                raise SnapshotError("备份已变更，请重新检查差异后再恢复。")
            try:
                parse_slot(data, entry.slot_id)
                original = parse_slot(self._read_slot(entry.slot_id), entry.slot_id)
            except SlotError as exc:
                raise SnapshotError("备份或当前档位未通过格式验证，只能导出副本。") from exc
            if target_revision != slot_revision(directory, entry.slot_id, original):
                raise SnapshotError("档位已变更，请重新检查差异后再恢复。")
            if original.data == data:
                raise SnapshotError("备份与当前档位相同，无需覆盖。")
            backup = self._capture(entry.slot_id, original.data, "pre_restore")
            path = self._replace_slot(entry.slot_id, data, original.data)
            return {"message": "档位已恢复，恢复前版本已备份。", "path": str(path), "backup": backup.id}


def backup_linked_slot(ops, profile_name: str, *, paths: GamePaths | None = None) -> dict | None:
    """Before apply (outside profile/save locks), snapshot this exact profile's linked slot.

    No link means None. Return a timeline summary on success. On a broken link,
    unsafe source, running game or write failure, propagate SnapshotError/OSError;
    the caller must abort the switch. Never changes the slot or its index.
    CLI may supply already resolved paths and an adapter with data_dir/profiles.
    """
    resolved = paths if paths is not None else ops.paths(ops.settings())
    return SnapshotHistory(ops.data_dir, ops.profiles, resolved).backup_link(profile_name)
