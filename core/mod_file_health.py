"""Static mod file triage with per-file, reversible local isolation."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from secrets import token_hex
from time import time

from .maintenance_files import (
    MaintenanceError, clean_part, digest_file, digest_value, directory, entries, identity,
    is_link, json_bytes, mods_root, move_new, public_copy, read_copy, read_small, regular,
    signature, strict_json, writable_area, writable_mods,
)
from .paths import GamePaths

MAX_ENTRIES = 50_000
MAX_ISSUES = 1500
MAX_BYTES = 128 * 1024 * 1024
RECORD_LIMIT = 4096
RECORD_ID = re.compile(r"[a-f0-9]{32}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
EXEC = {".exe", ".bat", ".cmd", ".com", ".ps1", ".js", ".lnk", ".msi", ".vbs", ".scr", ".hta"}
PACKAGING = {".zip", ".7z", ".rar", ".psd", ".aseprite"}
RESERVED_DLL = {"0harmony.dll", "assembly-csharp.dll", "steamworks.net.dll", "mscorlib.dll", "newtonsoft.json.dll"}


def _folder(paths: GamePaths, source: str, folder: str) -> Path:
    base = mods_root(paths, source)
    if not base or not clean_part(folder) or (source == "local" and folder.startswith(".")):
        raise MaintenanceError("请选择有效的模组文件夹。")
    return directory(base / folder)


def _declared(copy, issue):
    if copy.manifest is None:
        issue("manifest.xml", "invalid_manifest", "blocking", copy.error)
        return None
    manifest = copy.manifest
    if len(manifest.findall("Capabilities")) != 1:
        issue("manifest.xml", "capabilities", "blocking", "清单需要一个 Capabilities 节点。")
    asm = manifest.findall("Assemblies")
    assemblies = asm[0].findall("Assembly") if len(asm) == 1 else []
    if not assemblies:
        issue("manifest.xml", "no_assemblies", "blocking", "指南要求清单声明至少一个包含 IMod 的 DLL。")
        return None
    declared, file_ids = set(), set()
    unknown = False
    for assembly in assemblies:
        parts = (assembly.text or "").strip().replace("\\", "/").split("/")
        if len(assembly) or not all(clean_part(p) for p in parts) or not parts[-1].casefold().endswith(".dll"):
            unknown = True
            issue("manifest.xml", "bad_assembly_path", "blocking", "清单含无效程序集路径，暂不隔离任何 DLL。")
            continue
        relative = "/".join(parts)
        key = relative.casefold()
        if key in declared:
            issue(relative, "duplicate_assembly", "review", "程序集路径在清单中重复。")
        declared.add(key)
        dll = parts[-1].casefold()
        if dll in RESERVED_DLL or dll.startswith(("unityengine.", "monomod.", "mono.cecil")):
            issue(relative, "bundled_game_dll", "blocking", "文件名对应指南列出的游戏或框架自带程序集；不自动移动清单声明的 DLL。")
        try:
            info = regular(copy.root.joinpath(*parts))
            if not info.st_size:
                raise MaintenanceError("程序集为空。")
            file_ids.add((info.st_dev, info.st_ino))
        except (OSError, MaintenanceError):
            issue(relative, "missing_assembly", "blocking", "清单声明的程序集缺失、为空或不能安全读取。")
    return None if unknown else (declared, file_ids)


def _walk(root: Path, source: str):
    queue = [root]
    seen = 0
    while queue:
        folder = queue.pop()
        for path, meta in entries(folder, MAX_ENTRIES + 1):
            seen += 1
            if seen > MAX_ENTRIES:
                raise MaintenanceError("达到体检条目上限。指南对超过 50,000 个文件的模组另有拒绝规则。")
            if not is_link(meta) and stat.S_ISDIR(meta.st_mode):
                if source != "local" or not path.name.startswith("."):
                    queue.append(path)
            else:
                yield path, meta


def scan_health(paths: GamePaths, source: str, folder: str) -> dict:
    root = _folder(paths, source, folder)
    copy = read_copy(source, root)
    stamp = identity(root)
    issues = []
    incomplete = False
    budget = MAX_BYTES
    writable = False
    if source == "local" and copy.manifest is not None and os.name == "nt":
        try:
            writable_area(paths, root)
            writable = root.parent == writable_mods(paths)
        except (OSError, MaintenanceError):
            pass

    def issue(path, code, severity, detail, fingerprint=None):
        if len(issues) >= MAX_ISSUES:
            raise MaintenanceError("达到体检结果上限。")
        key = digest_value([source, str(root), stamp, copy.revision, path, code, fingerprint])
        issues.append({"id": key, "path": path, "code": code, "severity": severity,
                       "detail": detail, "can_isolate": fingerprint is not None})

    try:
        declared = _declared(copy, issue)
        for file, meta in _walk(root, source):
            name = file.relative_to(root).as_posix()
            if is_link(meta) or not stat.S_ISREG(meta.st_mode) or not all(clean_part(p) for p in file.relative_to(root).parts):
                issue(name, "unreadable_entry", "review", "链接、重解析点或特殊文件未读取，不能隔离。")
                incomplete = True
                continue
            ext = file.suffix.casefold()
            if ext == ".dll" and declared is not None:
                # NTFS short aliases in Assembly paths may spell the same file differently.
                if name.casefold() in declared[0]:
                    continue
                try:
                    actual = file.lstat()
                except OSError:
                    issue(name, "unreadable_entry", "review", "无法确认程序集身份，不能隔离。")
                    incomplete = True
                    continue
                if actual.st_ino and (actual.st_dev, actual.st_ino) in declared[1]:
                    continue
                label = ("unlisted_dll", "blocking", "指南明确说明未在 Assemblies 声明的 DLL 会阻止加载。隔离前请确认它不是模组所需依赖。")
            elif ext in EXEC:
                label = ("executable", "scanner", "可执行文件或脚本属于指南要求检查的对象；此处只检查文件名，不判断实际内容或授权。")
            elif ext in PACKAGING:
                label = ("release_extra", "review", "指南建议发布包不要包含压缩包和美术源文件；这不是对当前游戏版本拒载的判定。")
            else:
                continue
            fingerprint = None
            if writable and meta.st_size <= budget:
                try:
                    fingerprint = digest_file(file, budget)
                    budget -= meta.st_size
                except (OSError, MaintenanceError):
                    pass
            issue(name, *label, fingerprint)
    except (OSError, MaintenanceError) as exc:
        incomplete = True
        if len(issues) < MAX_ISSUES:
            issue(".", "scan_incomplete", "review", str(exc) if isinstance(exc, MaintenanceError) else "部分目录无法访问。")
    return {**public_copy(copy), "issues": sorted(issues, key=lambda x: (x["path"].casefold(), x["code"])),
            "incomplete": incomplete}


def _vault(paths: GamePaths, *, create=False) -> Path:
    local = writable_mods(paths)
    vault = local.parent / ".PSMM-Quarantine"
    writable_area(paths, vault)
    if create:
        try:
            vault.mkdir()
        except FileExistsError:
            pass
    return directory(vault)


def _candidate_id(root, copy, issue, fingerprint):
    return digest_value(["local", str(root), identity(root), copy.revision, issue["path"], issue["code"], fingerprint])


def isolate_file(paths: GamePaths, folder: str, issue_id: str, *, before_write=None) -> dict:
    root = _folder(paths, "local", folder)
    if root.parent != writable_mods(paths):
        raise MaintenanceError("只能隔离 Mods 目录下的本地副本。")
    writable_area(paths, root)
    report = scan_health(paths, "local", folder)
    row = next((x for x in report["issues"] if x["id"] == issue_id and x["can_isolate"]), None)
    if row is None:
        raise MaintenanceError("文件不符合隔离条件或已经变动，请重新体检。")
    source = root.joinpath(*row["path"].split("/"))
    copy = read_copy("local", root)
    fingerprint = digest_file(source, MAX_BYTES)
    if issue_id != _candidate_id(root, copy, row, fingerprint):
        raise MaintenanceError("确认后文件或清单已经变动，请重新体检。")
    if before_write is not None:
        before_write()
    vault = _vault(paths, create=True)
    if sum(1 for _ in entries(vault, RECORD_LIMIT)) >= RECORD_LIMIT:
        raise MaintenanceError("隔离记录达到上限；请先在备份后处理旧记录。")
    record_id = token_hex(16)
    record_dir = vault / record_id
    record_dir.mkdir()
    meta = {"id": record_id, "folder": root.name, "mod_id": copy.id, "name": copy.name,
            "path": row["path"], "manifest": copy.revision, "identity": list(identity(root)),
            "sha256": fingerprint[0], "size": fingerprint[1][2], "created_at": int(time())}
    # Write the recovery journal before moving bytes. Failed or interrupted moves leave no active payload.
    with (record_dir / "record.json").open("xb") as stream:
        stream.write(json_bytes(meta))
        stream.flush()
        os.fsync(stream.fileno())
    if (signature(regular(source)) != fingerprint[1] or
            copy.revision != read_copy("local", root).revision or meta["identity"] != list(identity(root))):
        raise MaintenanceError("隔离前模组文件发生变化，未移动文件。")
    move_new(source, record_dir / "payload.bin")
    return _public_record(meta)


def _public_record(meta: dict) -> dict:
    return {key: meta[key] for key in ("id", "folder", "mod_id", "name", "path", "size", "created_at")}


def _record(folder: Path) -> dict:
    try:
        meta = strict_json(read_small(folder / "record.json", 16 * 1024))
        path = meta["path"].split("/")
        if (meta["id"] != folder.name or not clean_part(meta["folder"]) or
                not all(clean_part(p) for p in path) or Path(path[-1]).suffix.casefold() not in (EXEC | PACKAGING | {".dll"}) or
                not SHA.fullmatch(meta["manifest"]) or not SHA.fullmatch(meta["sha256"]) or
                not all(isinstance(meta[k], str) for k in ("mod_id", "name")) or
                not isinstance(meta["identity"], list) or len(meta["identity"]) != 2 or
                any(type(x) is not int for x in meta["identity"]) or
                type(meta["size"]) is not int or not 0 <= meta["size"] <= MAX_BYTES or
                type(meta["created_at"]) is not int):
            raise ValueError
        return meta
    except (AttributeError, TypeError, ValueError, OSError, KeyError, RecursionError):
        raise MaintenanceError("隔离记录损坏，未移动文件。请保留隔离目录并手动核对备份。") from None


def list_isolated(paths: GamePaths) -> list[dict]:
    if not paths.game_dir or not paths.local_mods_dir:
        return []
    try:
        vault = _vault(paths)
    except FileNotFoundError:
        return []
    result = []
    for folder, meta in entries(vault, RECORD_LIMIT):
        if not RECORD_ID.fullmatch(folder.name) or not stat.S_ISDIR(meta.st_mode) or is_link(meta):
            continue
        try:
            regular(folder / "payload.bin")
        except FileNotFoundError:
            continue  # already restored, or interrupted before move
        result.append(_public_record(_record(folder)))
    return sorted(result, key=lambda x: (-x["created_at"], x["id"]))


def restore_file(paths: GamePaths, record_id: str, *, before_write=None) -> dict:
    if not RECORD_ID.fullmatch(record_id):
        raise MaintenanceError("隔离记录 ID 无效。")
    folder = directory(_vault(paths) / record_id)
    meta = _record(folder)
    root = _folder(paths, "local", meta["folder"])
    writable_area(paths, root)
    copy = read_copy("local", root)
    if (copy.id != meta["mod_id"] or copy.revision != meta["manifest"] or
            list(identity(root)) != meta["identity"]):
        raise MaintenanceError("原模组或清单已更换，拒绝向不同副本恢复；隔离文件已保留。")
    source = folder / "payload.bin"
    digest, mark = digest_file(source, MAX_BYTES)
    if digest != meta["sha256"] or mark[2] != meta["size"]:
        raise MaintenanceError("隔离文件与记录不一致，未恢复。")
    destination = root.joinpath(*meta["path"].split("/"))
    if before_write is not None:
        before_write()
    if signature(regular(source)) != mark or copy.revision != read_copy("local", root).revision:
        raise MaintenanceError("恢复前文件发生变化，未恢复。")
    move_new(source, destination)
    return _public_record(meta)
