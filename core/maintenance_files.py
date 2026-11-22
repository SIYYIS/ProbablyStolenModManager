"""Bounded, non-following reads and non-replacing moves for maintenance tools."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .paths import GamePaths


class MaintenanceError(ValueError):
    pass


MAX_MANIFEST = 256 * 1024
MOD_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def clean_part(part: str) -> bool:
    return (0 < len(part) <= 255 and part not in {".", ".."} and
            not part.endswith((".", " ")) and not RESERVED.match(part.split(".", 1)[0].rstrip(" ")) and
            not any(ord(c) < 32 or c in '<>:"/\\|?*' for c in part))


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def directory(path: Path) -> Path:
    """Validate every ancestor; don't resolve a configured junction into a different root."""
    path = path.absolute()
    if ".." in path.parts:
        raise MaintenanceError("维护工具不接受含有 .. 的目录。")
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise MaintenanceError("目录包含链接、联接点或不是普通文件夹。")
    return path


def entries(path: Path, limit: int):
    directory(path)
    with os.scandir(path) as listing:
        for index, entry in enumerate(listing):
            if index >= limit:
                raise MaintenanceError("扫描达到文件数量上限，无法安全继续。")
            yield Path(entry.path), entry.stat(follow_symlinks=False)


def regular(path: Path) -> os.stat_result:
    directory(path.parent)
    info = path.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MaintenanceError("只处理非链接、非共享的普通文件。")
    return info


def signature(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def identity(path: Path) -> tuple[int, int]:
    info = directory(path).stat()
    return info.st_dev, info.st_ino


def read_small(path: Path, limit: int) -> bytes:
    before = regular(path)
    if before.st_size > limit:
        raise MaintenanceError("文件超过扫描大小上限。")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or signature(before) != signature(regular(path)):
        raise MaintenanceError("读取期间文件发生变动，请重新扫描。")
    return raw


def digest_file(path: Path, limit: int) -> tuple[str, tuple[int, ...]]:
    before = regular(path)
    if before.st_size > limit:
        raise MaintenanceError("文件太大，不能安全验证内容。")
    h = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > limit:
                raise MaintenanceError("文件读取超过大小上限。")
            h.update(block)
    mark = signature(before)
    if count != before.st_size or mark != signature(regular(path)):
        raise MaintenanceError("读取期间文件发生变动，请重新扫描。")
    return h.hexdigest(), mark


def digest_value(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def strict_json(raw: bytes):
    def pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("JSON 含重复键。")
            obj[key] = value
        return obj

    def invalid(_):
        raise ValueError("JSON 含非有限数值。")

    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)


def move_new(source: Path, dest: Path):
    """On Windows Path.rename is an atomic same-volume move that fails on EEXIST."""
    if os.name != "nt":
        raise MaintenanceError("维护工具的非覆盖移动目前只支持 Windows。")
    regular(source)
    directory(dest.parent)
    try:
        os.rename(source, dest)
    except FileExistsError:
        raise MaintenanceError("目标文件已存在，未覆盖任何文件。") from None


def mods_root(paths: GamePaths, source: str) -> Path | None:
    if source == "local":
        return paths.local_mods_dir
    if source == "workshop":
        return paths.workshop_dir
    raise MaintenanceError("未知模组来源。")


def writable_mods(paths: GamePaths) -> Path:
    if not paths.game_dir or not paths.local_mods_dir:
        raise MaintenanceError("尚未配置游戏的本地 Mods 目录。")
    local = paths.local_mods_dir.absolute()
    if local != paths.game_dir.absolute() / "Mods":
        raise MaintenanceError("只能修改游戏目录直属 Mods 下的本地副本。")
    return directory(local)


def writable_area(paths: GamePaths, candidate: Path):
    """A misconfigured Steam/save path must not turn it into a write target."""
    candidate = candidate.absolute()
    for anchor in (paths.workshop_dir, paths.save_data_dir, paths.mod_configs_dir):
        if anchor:
            anchor = anchor.absolute()
            if candidate.is_relative_to(anchor) or anchor.is_relative_to(candidate):
                raise MaintenanceError("维护目录与工坊或存档目录重叠，已阻止写入。")
    if paths.save_file and paths.save_file.absolute().is_relative_to(candidate):
        raise MaintenanceError("维护目录内包含配置的存档，已阻止写入。")


@dataclass(frozen=True)
class Copy:
    source: str
    root: Path
    id: str
    name: str
    manifest: ET.Element | None
    revision: str
    error: str = ""


def read_copy(source: str, folder: Path) -> Copy:
    path = folder / "manifest.xml"
    try:
        raw = read_small(path, MAX_MANIFEST)
        text = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise MaintenanceError("不读取含 DTD 或实体声明的清单。")
        root = ET.fromstring(text)
        ids = root.findall("ID")
        mod_id = (ids[0].text or "").strip() if len(ids) == 1 and not len(ids[0]) else ""
        if not MOD_ID.fullmatch(mod_id):
            raise MaintenanceError("清单没有唯一有效的模组 ID。")
        return Copy(source, folder, mod_id, (root.findtext("Name") or mod_id).strip()[:256], root,
                    hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, ET.ParseError):
        return Copy(source, folder, "", folder.name, None, "", "manifest.xml 不可读或无效。")


def inventory(paths: GamePaths) -> tuple[list[Copy], list[str]]:
    copies, warnings = [], []
    for source in ("local", "workshop"):
        root = mods_root(paths, source)
        if root is None:
            continue
        try:
            for folder, info in entries(root, 4096):
                if source == "local" and folder.name.startswith("."):
                    continue
                if is_link(info):
                    warnings.append(f"{source}/{folder.name}：链接未扫描，不能确认模组 ID。")
                elif stat.S_ISDIR(info.st_mode) and clean_part(folder.name):
                    try:
                        (folder / "manifest.xml").lstat()
                    except FileNotFoundError:
                        continue
                    copy = read_copy(source, folder)
                    copies.append(copy)
                    if copy.error:
                        warnings.append(f"{source}/{folder.name}：{copy.error}")
        except FileNotFoundError:
            continue
        except (OSError, MaintenanceError) as exc:
            warnings.append(f"无法完整扫描 {source} 模组目录。（{type(exc).__name__}）")
    return copies, warnings


def public_copy(copy: Copy) -> dict:
    return {"source": copy.source, "folder": copy.root.name, "mod_id": copy.id, "name": copy.name}
