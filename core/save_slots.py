"""Read and patch known fields in Probably Stolen's ES3 slots."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .file_ops import atomic_write, content_digest, is_link, write_lock
from .paths import is_game_running
from .save_fields import FIELDS, REPUTATION_FIELDS, FactionReputation, SlotValue

MAX_SIZE = 64 * 1024 * 1024
_SLOT = re.compile(r"save_(0|[1-9][0-9]{0,9})\.es3\Z", re.ASCII)
_BACKUP = re.compile(r"backup-[a-z0-9_]{8}\Z", re.ASCII)
_INT = re.compile(rb"-?(?:0|[1-9][0-9]{0,9})\Z")
_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_TOKEN = re.compile(rb'"[^"\\]*(?:\\[\s\S][^"\\]*)*"|["{}\[\]]')
_WS = re.compile(rb"[ \t\r\n]*")


class SlotError(ValueError):
    pass


def _ws(data: bytes, i: int) -> int:
    return _WS.match(data, i).end()


def _end(data: bytes, i: int) -> int:
    if data[i:i+1] == b'"':
        token = _TOKEN.match(data, i)
        if token and token.end() > i + 1:
            return token.end()
    if data[i:i+1] in (b"{", b"["):
        stack = [b"}" if data[i:i+1] == b"{" else b"]"]
        for token in _TOKEN.finditer(data, i+1):
            piece = token[0]
            if piece == b'"':
                break
            if piece in (b"{", b"["):
                if len(stack) == 256:
                    break
                stack.append(b"}" if piece == b"{" else b"]")
            elif piece in (b"}", b"]"):
                if piece != stack.pop():
                    break
                if not stack:
                    return token.end()
    elif data[i:i+1] not in (b"", b'"'):
        match = re.match(rb"[^,}\] \t\r\n]+", data[i:i+80])
        if match:
            return i + match.end()
    raise SlotError("无法识别存档结构，已禁止写入。")


def _members(data: bytes, begin: int, end: int) -> dict[bytes, tuple[int, int]]:
    if data[begin:begin+1] != b"{":
        raise SlotError("存档缺少预期的对象，已禁止写入。")
    result = {}
    i = _ws(data, begin+1)
    if data[i:i+1] == b"}" and i+1 == end:
        return result
    while i < end:
        key = _TOKEN.match(data, i) if data[i:i+1] == b'"' else None
        if key is None or key.end() == i+1 or b"\\" in key[0]:
            break  # Only schema keys here; nested ES3 dictionary keys are opaque.
        key_end = key.end()
        colon = _ws(data, key_end)
        if data[colon:colon+1] != b":" or key_end == i:
            break
        start = _ws(data, colon+1)
        stop = _end(data, start)
        name = key[0][1:-1]
        if name in result:
            raise SlotError("存档包含重复字段，已禁止写入。")
        result[name] = (start, stop)
        i = _ws(data, stop)
        if data[i:i+1] == b"}" and i+1 == end:
            return result
        if data[i:i+1] != b",":
            break
        i = _ws(data, i+1)
    raise SlotError("无法识别存档结构，已禁止写入。")


def _reputations(data: bytes, span: tuple[int, int]) -> dict[str, tuple[FactionReputation, tuple[int, int]]]:
    begin, end = span
    if data[begin:begin+1] != b"[":
        raise SlotError("阵营声望列表格式不受支持。")
    result = {}
    i = _ws(data, begin+1)
    if i == end-1 and data[i:i+1] == b"]":
        return result
    while i < end-1 and len(result) < 128:
        stop = _end(data, i)
        members = _members(data, i, stop)
        a, b = members[b"factionId"]
        faction_id = json.loads(data[a:b])
        if (not isinstance(faction_id, str) or not 0 < len(faction_id) <= 128
                or not faction_id.isprintable() or faction_id in result):
            raise SlotError("阵营 ID 重复或无效。")
        a, b = amount_span = members[b"amount"]
        if not _NUMBER.fullmatch(data[a:b]) or not isfinite(amount := float(data[a:b])):
            raise SlotError("阵营声望不是有限数值。")
        label = faction_id
        if b"factionDisplay" in members:
            a, b = members[b"factionDisplay"]
            display = json.loads(data[a:b])
            if isinstance(display, str) and 0 < len(display) <= 128 and display.isprintable() and display.strip():
                label = display
        result[faction_id] = (FactionReputation(faction_id, label, amount), amount_span)
        i = _ws(data, stop)
        if i == end-1 and data[i:i+1] == b"]":
            return result
        if data[i:i+1] != b",":
            break
        i = _ws(data, i+1)
    raise SlotError("阵营声望列表格式不受支持。")


@dataclass(frozen=True)
class Slot:
    data: bytes
    fields: dict[str, tuple[int, int]]
    values: dict[str, SlotValue]
    day: int
    read_only_reputations: tuple[FactionReputation, ...] = ()
    reputation_issue: str | None = None

    def patch(self, changes: dict[str, SlotValue]) -> bytes:
        for key, value in changes.items():
            if key not in self.fields:
                raise SlotError("此档位没有该可编辑项目，请重新读取。")
            if not FIELDS[key].accepts(value):
                raise SlotError(f"「{FIELDS[key].label}」的数值或类型超出允许范围。")
        parts = []
        cursor = 0
        for (start, end), value in sorted((self.fields[k], v) for k, v in changes.items()):
            encoded = b"true" if value is True else b"false" if value is False else str(value).encode("ascii")
            parts.extend((self.data[cursor:start], encoded))
            cursor = end
        return b"".join([*parts, self.data[cursor:]])


def parse_slot(data: bytes, slot_id: int) -> Slot:
    root_start = _ws(data, 3 if data.startswith(b"\xef\xbb\xbf") else 0)
    root_end = len(data.rstrip())

    def record(key: bytes, kind: bytes) -> dict[bytes, tuple[int, int]]:
        envelope = _members(data, *records[key])
        a, b = envelope[b"__type"]
        if data[a:b] != b'"' + kind + b',Assembly-CSharp"':
            raise KeyError(key)
        return _members(data, *envelope[b"value"])

    def integer(span: tuple[int, int]) -> int:
        a, b = span
        if not _INT.fullmatch(data[a:b]):
            raise SlotError("存档数值格式不受支持，已禁止写入。")
        value = int(data[a:b])
        if not -(2**31) <= value < 2**31:
            raise SlotError("存档数值超出游戏支持的范围，已禁止写入。")
        return value

    try:
        records = _members(data, root_start, root_end)
        player = record(b"playerStore", b"PlayerStore")
        station = record(b"storeStation", b"StoreStation")
        day = integer(station[b"dayCounter"])
        if integer(player[b"saveSlotId"]) != slot_id or day < 0:
            raise SlotError("存档内容与档位不一致，已禁止写入。")
    except KeyError:
        raise SlotError("缺少已验证的存档字段，已禁止写入。") from None
    fields: dict[str, tuple[int, int]] = {}
    values: dict[str, SlotValue] = {}
    reputations = {}
    reputation_issue = None
    if b"storeReputations" in player:
        try:
            reputations = _reputations(data, player[b"storeReputations"])
        except (KeyError, ValueError, RecursionError):
            reputation_issue = "这个档位的阵营声望列表无法安全识别，未开放编辑。"
    objects = {b"playerStore": player, b"storeStation": station}
    nested: dict[tuple[int, int], dict[bytes, tuple[int, int]]] = {}
    for key, field in FIELDS.items():
        if field.faction_id is not None:
            if field.faction_id in reputations:
                reputation, fields[key] = reputations[field.faction_id]
                values[key] = reputation.amount
            continue
        try:
            members = objects[field.record]
            for name in field.path[:-1]:
                span = members[name]
                if span not in nested:
                    nested[span] = _members(data, *span)
                members = nested[span]
            span = members[field.path[-1]]
            if field.kind == "boolean":
                a, b = span
                if data[a:b] not in (b"true", b"false"):
                    raise SlotError("存档开关格式不受支持。")
                value = data[a:b] == b"true"
            else:
                value = integer(span)
        except (KeyError, SlotError):
            if key in {"cash", "favor"}:
                raise SlotError("缺少已验证的存档字段，已禁止写入。") from None
            # Optional fields may be absent or have a different format in another game version.
            continue
        fields[key] = span
        values[key] = value
    return Slot(data, fields, values, day,
                tuple(rep for faction_id, (rep, _) in reputations.items() if faction_id not in REPUTATION_FIELDS),
                reputation_issue)


def _check_path(path: Path) -> None:
    if str(path).startswith(("\\\\", "//")) or any(is_link(p) for p in (path, *path.parents)):
        raise SlotError("不支持网络目录或链接路径。")


def list_slots(directory: Path) -> list[int]:
    _check_path(directory)
    if not directory.is_dir():
        return []
    return sorted(int(m[1]) for p in directory.iterdir() if (m := _SLOT.fullmatch(p.name))
                  and int(m[1]) <= 2_147_483_647 and not is_link(p) and p.is_file())


def _read(path: Path) -> bytes:
    _check_path(path)
    if not path.is_file():
        raise SlotError("未找到该档位文件。")
    with path.open("rb") as source:
        data = source.read(MAX_SIZE + 1)
    if not data or len(data) > MAX_SIZE:
        raise SlotError("存档为空或超过 64 MB，未读取。")
    return data


def slot_path(directory: Path, slot_id: int) -> Path:
    if not 0 <= slot_id <= 2_147_483_647:
        raise SlotError("无效档位。")
    return directory / f"save_{slot_id}.es3"


def read_slot(directory: Path, slot_id: int) -> Slot:
    return parse_slot(_read(slot_path(directory, slot_id)), slot_id)


def slot_revision(directory: Path, slot_id: int, slot: Slot) -> str:
    # Bind the revision to the target path as well as its contents.
    return content_digest(str(slot_path(directory.absolute(), slot_id)).encode("utf-8") + b"\0" + slot.data) or ""


def list_backups(backup_root: Path, slot_id: int) -> list[str]:
    folder = backup_root / str(slot_id)
    _check_path(folder)
    if not folder.is_dir():
        return []
    files = [(p.stat().st_mtime_ns, p.name) for p in folder.iterdir()
             if _BACKUP.fullmatch(p.name) and not is_link(p) and p.is_dir()
             and not is_link(p / "original.es3") and (p / "original.es3").is_file()]
    return [name for _, name in sorted(files, reverse=True)[:40]]


def write_slot(directory: Path, backup_root: Path, slot_id: int, revision: str,
               *, changes: dict[str, SlotValue] | None = None, restore: str | None = None) -> str:
    path = slot_path(directory, slot_id)
    _check_path(path)
    if not path.is_file():
        raise SlotError("未找到该档位文件。")
    if is_game_running():
        raise SlotError("请关闭游戏后再保存或恢复档位。")
    folder = backup_root / str(slot_id)
    _check_path(folder)
    with write_lock(directory):
        original = read_slot(directory, slot_id)
        if revision != slot_revision(directory, slot_id, original):
            raise SlotError("档位已变更，请重新读取后再操作。")
        if restore is not None:
            if not _BACKUP.fullmatch(restore) or changes is not None:
                raise SlotError("无效备份。")
            candidate = _read(folder / restore / "original.es3")
        else:
            if not changes:
                raise SlotError("没有要保存的修改。")
            candidate = original.patch(changes)
        parse_slot(candidate, slot_id)
        if candidate == original.data:
            raise SlotError("档位没有变化。")
        folder.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=folder))
        atomic_write(backup / "original.es3", original.data)
        if is_game_running() or _read(path) != original.data:
            raise SlotError("备份完成，但游戏已启动或档位已变更，未写入档位。")
        atomic_write(path, candidate)
        return backup.name
