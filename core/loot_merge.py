"""Inspect documented loot JSON and emit narrow, explicitly selected add overlays."""
from __future__ import annotations

import hashlib
import math
import os
import stat
import tempfile
from collections import defaultdict
from pathlib import Path

from .maintenance_files import (
    Copy, MaintenanceError, clean_part, digest_value, directory, entries, identity,
    inventory, is_link, json_bytes, move_new, read_small, regular, strict_json, writable_area, writable_mods,
)
from .paths import GamePaths

KNOWN_TABLES = frozenset({"junkTable", "packedFoodTable", "toolTable", "makeshiftWeaponTable",
                         "materialTable", "householdTable", "medicalTable", "dumpingGroundMedical",
                         "t1moduleTable", "t2moduleTable", "allModuleTable", "accessCardTable"})
LIMIT_BYTES = 512 * 1024
LIMIT_FILES = 160
LIMIT_ACTIONS = 6000
PATCH_NAME = "PSMM_Merged_Loot.json"


def _obj(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise MaintenanceError("文件包含未支持字段、缺少字段或结构不正确。")
    return value


def _array(value):
    if not isinstance(value, list) or len(value) > LIMIT_ACTIONS:
        raise MaintenanceError("掉落字段必须是未超过扫描上限的数组。")
    return value


def _id(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 128 or value.strip() != value
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise MaintenanceError("表或条目 ID 无效。")
    return value


def _weight(value):
    if type(value) not in (int, float) or not -3.4028234e38 <= value <= 3.4028234e38 or not math.isfinite(value):
        raise MaintenanceError("权重必须是有限数值。")
    return value


def _parse(raw: bytes):
    body = _obj(strict_json(raw), ("tables", "groups", "patches"))
    for key, kind in (("tables", "table"), ("groups", "group")):
        for registration in _array(body.get(key, [])):
            item = _obj(registration, ("id", "entries"), ("id", "entries"))
            target = _id(item["id"])
            yield kind, target, "register", None, None
            for edit in _array(item["entries"]):
                edit = _obj(edit, ("id", "weight"), ("id", "weight"))
                yield kind, target, "initial", _id(edit["id"]), _weight(edit["weight"])
    for patch in _array(body.get("patches", [])):
        item = _obj(patch, ("table", "group", "clear", "add", "remove", "setWeight"))
        if ("table" in item) == ("group" in item):
            raise MaintenanceError("每个 patch 必须指定 table 和 group 之一。")
        kind = "table" if "table" in item else "group"
        target = _id(item[kind])
        flag = item.get("clear", False)
        if type(flag) is not bool:
            raise MaintenanceError("clear 必须是布尔值。")
        if flag:
            yield kind, target, "clear", None, None
        for op in ("add", "remove", "setWeight"):
            for value in _array(item.get(op, [])):
                if op == "remove":
                    yield kind, target, op, _id(value), None
                else:
                    edit = _obj(value, ("id", "weight"), ("id", "weight"))
                    yield kind, target, op, _id(edit["id"]), _weight(edit["weight"])


def _loot_files(copy: Copy):
    folder = copy.root / "LootTables"
    try:
        folder.lstat()
    except FileNotFoundError:
        return []
    files = []
    for path, meta in entries(folder, 1024):
        if path.suffix.casefold() != ".json":
            continue
        if is_link(meta):
            raise MaintenanceError("直接加载目录中有链接文件。")
        if stat.S_ISDIR(meta.st_mode):
            continue  # documented nonrecursive scan
        if not stat.S_ISREG(meta.st_mode) or not clean_part(path.name):
            raise MaintenanceError("掉落目录包含无法安全识别的 JSON 文件。")
        if len(files) >= LIMIT_FILES:
            raise MaintenanceError("掉落文件数量超过扫描上限。")
        files.append(path)
    return sorted(files, key=lambda path: (path.name.casefold(), path.name))


def _effective(copies: list[Copy], mod_id: str) -> Copy:
    matching = [c for c in copies if c.id.casefold() == mod_id.casefold()]
    preferred = [c for c in matching if c.source == "local"] or matching
    if len(preferred) != 1 or preferred[0].id != mod_id:
        raise MaintenanceError(f"无法唯一确定 {mod_id} 的有效副本（缺失、重复或大小写不一致）。")
    return preferred[0]


def _carrier(paths: GamePaths, copy: Copy) -> list[tuple]:
    if copy.source != "local" or copy.root.parent != writable_mods(paths):
        raise MaintenanceError("草稿末位必须是本地模组副本。")
    writable_area(paths, copy.root)
    assert copy.manifest is not None
    nodes = copy.manifest.findall("Assemblies")
    if len(nodes) != 1 or len(copy.manifest.findall("Capabilities")) != 1:
        raise MaintenanceError("末位模组需要已有程序集及 Capabilities 清单节点。")
    assemblies = nodes[0].findall("Assembly")
    if not assemblies:
        raise MaintenanceError("纯 JSON 不能独立加载；需要已有有效载体模组。")
    fingerprints = []
    for assembly in assemblies:
        parts = (assembly.text or "").strip().replace("\\", "/").split("/")
        if len(assembly) or not all(clean_part(p) for p in parts) or not parts[-1].casefold().endswith(".dll"):
            raise MaintenanceError("末位模组的程序集路径无效。")
        try:
            info = regular(copy.root.joinpath(*parts))
            if not info.st_size:
                raise MaintenanceError("末位模组的清单列出了空 DLL。")
            fingerprints.append((parts, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except OSError:
            raise MaintenanceError("末位模组缺少清单列出的 DLL。") from None
    if _loot_files(copy):
        raise MaintenanceError("末位模组已有掉落 JSON；为避免覆盖或顺序歧义，只支持空掉落目录的载体。")
    return fingerprints


def compare_loot(paths: GamePaths, order: list[str]) -> dict:
    copies, errors = inventory(paths)
    targets = defaultdict(list)
    sigs = []
    file_count = action_count = 0
    carrier = None
    carrier_note = "草稿末位没有可用载体。"
    if len({item.casefold() for item in order}) != len(order):
        errors.append("草稿包含重复的模组 ID。")
    for mod_id in order:
        try:
            copy = _effective(copies, mod_id)
            sigs.append((copy.source, str(copy.root), identity(copy.root), copy.revision))
            for path in _loot_files(copy):
                file_count += 1
                if file_count > LIMIT_FILES:
                    raise MaintenanceError("达到本次对比的 JSON 文件数量上限。")
                try:
                    raw = read_small(path, LIMIT_BYTES)
                    sigs.append((path.name, hashlib.sha256(raw).hexdigest()))
                    for index, (kind, target, op, entry, weight) in enumerate(_parse(raw), 1):
                        if action_count >= LIMIT_ACTIONS:
                            raise MaintenanceError("达到本次对比的操作条数上限。")
                        action_count += 1
                        targets[(kind, target)].append({"mod_id": copy.id, "name": copy.name, "source": copy.source,
                            "file": path.name, "index": index, "operation": op, "entry": entry, "weight": weight})
                except (MaintenanceError, OSError, ValueError, RecursionError) as exc:
                    description = str(exc) if isinstance(exc, MaintenanceError) else "无法安全解析 JSON。"
                    errors.append(f"{mod_id}/LootTables/{path.name}：{description}")
                if action_count >= LIMIT_ACTIONS:
                    break
            if order and mod_id == order[-1] and not errors:
                try:
                    sigs.extend(_carrier(paths, copy))
                    carrier = {"mod_id": copy.id, "name": copy.name, "folder": copy.root.name}
                    carrier_note = "仅校验载体结构，不能确认其 DLL 或游戏内批准状态。"
                except (OSError, MaintenanceError) as exc:
                    carrier_note = str(exc) if isinstance(exc, MaintenanceError) else "无法读取载体文件。"
        except (MaintenanceError, OSError) as exc:
            errors.append(str(exc) if isinstance(exc, MaintenanceError) else f"无法读取 {mod_id} 的掉落目录。")
        if action_count >= LIMIT_ACTIONS or file_count > LIMIT_FILES:
            errors.append("对比达到扫描上限，结果不完整。")
            break

    rows = []
    target_aliases = defaultdict(set)
    for kind, target in targets:
        target_aliases[(kind, target.casefold())].add(target)
    for (kind, target), edits in sorted(targets.items()):
        owners = {x["mod_id"] for x in edits}
        by_entry = defaultdict(list)
        for edit in edits:
            if edit["entry"] is not None:
                by_entry[edit["entry"]].append(edit)
        overlaps = []
        conflicts = []
        unsafe = []
        if kind != "table" or target not in KNOWN_TABLES:
            unsafe.append("只为指南明确列出的原版物品表生成补丁。")
        if any(e["operation"] != "add" or not 1e-30 <= (e["weight"] or 0) <= 1e20 for e in edits):
            unsafe.append("该表含 clear、remove、setWeight、表声明或不在安全范围内的 add 权重。")
        if len(target_aliases[(kind, target.casefold())]) > 1 or len({x.casefold() for x in by_entry}) != len(by_entry):
            unsafe.append("表或条目 ID 存在仅大小写不同的别名，不能确认它们是否指向同一对象。")
        if any(e["operation"] == "register" and e["entry"] is None for e in edits):
            unsafe.append("无法确认该表来自原版基线。")
        for entry, options in sorted(by_entry.items()):
            if len({o["mod_id"] for o in options}) < 2:
                continue
            overlaps.append(entry)
            if len({(o["operation"], o["weight"]) for o in options}) <= 1:
                continue
            if len(options) != len({o["mod_id"] for o in options}):
                unsafe.append("同一模组对冲突条目操作多次，需要手工检查。")
            conflicts.append({"entry": entry, "options": options})
        if not conflicts:
            unsafe.append("没有需要选择的同条目差异；独立 add 无需合并。")
        rows.append({"key": f"{kind}:{target}", "kind": kind, "target": target, "owners": len(owners),
                     "edits": edits, "overlaps": overlaps, "conflicts": conflicts,
                     "blocked": list(dict.fromkeys(unsafe))})
    return {"order": list(order), "revision": digest_value([order, sigs, errors, carrier]), "targets": rows,
            "errors": errors, "carrier": carrier if not errors else None, "carrier_note": carrier_note}


def generate_loot(report: dict, key: str, choices: dict[str, str]) -> dict:
    row = next((row for row in report["targets"] if row["key"] == key), None)
    if report["errors"] or not row or row["blocked"]:
        raise MaintenanceError("存在不完整或无法可靠合并的数据，未生成补丁。")
    if set(choices) != {conflict["entry"] for conflict in row["conflicts"]}:
        raise MaintenanceError("每项冲突都必须明确选择一个来源模组。")
    adds = []
    for conflict in row["conflicts"]:
        entry = conflict["entry"]
        source = next((o for o in conflict["options"] if o["mod_id"] == choices[entry]), None)
        if not source:
            raise MaintenanceError("选中的来源未参与该条目冲突。")
        adds.append({"id": entry, "weight": source["weight"]})
    return {"patch": {"patches": [{"table": row["target"], "add": adds}]}, "draft_order": list(report["order"]),
            "file_name": PATCH_NAME}


def install_loot(paths: GamePaths, order: list[str], revision: str, key: str, choices: dict[str, str], *, before_write=None) -> dict:
    report = compare_loot(paths, order)
    if report["revision"] != revision or not report["carrier"]:
        raise MaintenanceError("草稿或模组文件已经变动，或者末位没有可用载体；请重新对比。")
    result = generate_loot(report, key, choices)
    copy = _effective(inventory(paths)[0], report["carrier"]["mod_id"])
    _carrier(paths, copy)
    folder = copy.root / "LootTables"
    if before_write is not None:
        before_write()
    try:
        folder.mkdir()
    except FileExistsError:
        pass
    directory(folder)
    fd, name = tempfile.mkstemp(prefix=".psmm-loot-", suffix=".tmp", dir=folder)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json_bytes(result["patch"]))
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck after staging and before the non-replacing publication.
        if compare_loot(paths, order)["revision"] != revision:
            raise MaintenanceError("安装前模组文件已变动，请重新对比。")
        move_new(staged, folder / PATCH_NAME)
    finally:
        # Do not clean up a staging file through a directory that was replaced.
        try:
            directory(staged.parent)
            staged.unlink(missing_ok=True)
        except (OSError, MaintenanceError):
            pass
    return {**result, "relative_path": f"{copy.root.name}/LootTables/{PATCH_NAME}"}
