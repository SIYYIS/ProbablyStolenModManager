"""Preview changes and expose Windows actions using verified catalog entries."""

from __future__ import annotations

from dataclasses import asdict

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.dependency_plan import plan_dependencies
from core.file_ops import is_link
from core.scanner import InstalledMod, scan_installed_mods, validate_active_mods
from core.workshop import open_workshop_page_in_steam
from .operations import Operations, check_order, import_share
from .schemas import DependencyPreview, ModID, Name, Order, Profile, RequestBody, ShareImport


class Compare(RequestBody):
    before: Name | None = None
    after: Name


class ModOpen(RequestBody):
    id: ModID
    workshop: bool = False


def order_diff(before: list[str], after: list[str], mods: dict[str, InstalledMod]) -> dict:
    old, new = set(before), set(after)
    before_shared = [mid for mid in before if mid in new]
    after_shared = [mid for mid in after if mid in old]
    old_pos = {mid: i + 1 for i, mid in enumerate(before)}
    new_pos = {mid: i + 1 for i, mid in enumerate(after)}

    def row(mid: str) -> dict:
        mod = mods.get(mid)
        return {"id": mid, "name": mod.display_name if mod else mid,
                "before": old_pos.get(mid), "after": new_pos.get(mid),
                "workshop_id": mod.workshop_id if mod else None}

    return {"added": [row(mid) for mid in after if mid not in old],
            "removed": [row(mid) for mid in before if mid not in new],
            "moved": [row(mid) for a, mid in zip(before_shared, after_shared) if a != mid],
            "missing": [row(mid) for mid in after if mid not in mods],
            "overrides": [row(mid) for mid in after if mid in mods and mods[mid].overridden_workshop]}


def config_diff(before: Path | None, after: Path | None) -> dict:
    def files(root):
        if root is None or is_link(root) or not root.is_dir():
            return {}
        return {p.name: p for p in root.iterdir() if p.suffix.lower() == '.json' and p.is_file() and not is_link(p)}

    old, new = files(before), files(after)
    changes = []
    clipped = False

    def read(path):
        if not path:
            return b''
        with path.open('rb') as stream:
            return stream.read(1024 * 1024 + 1)

    def leaf(value, path=(), depth=0):
        if isinstance(value, dict) and value and depth < 10:
            for key, child in value.items():
                yield from leaf(child, (*path, key), depth + 1)
        else:
            yield path, json.dumps(value, ensure_ascii=True, sort_keys=True)

    for filename in sorted(old.keys() | new.keys()):
        a, b = read(old.get(filename)), read(new.get(filename))
        if a == b and (filename in old) == (filename in new) and len(a) <= 1024 * 1024:
            continue
        try:
            if max(len(a), len(b)) > 1024 * 1024:
                raise ValueError
            av = dict(leaf(json.loads(a))) if filename in old else {}
            bv = dict(leaf(json.loads(b))) if filename in new else {}
            delta = [(" › ".join(key), av.get(key), bv.get(key)) for key in sorted(av.keys() | bv.keys())
                     if av.get(key) != bv.get(key)]
            if not delta and (filename in old) != (filename in new):
                delta = [("", "已存在" if filename in old else None, "已存在" if filename in new else None)]
        except (ValueError, UnicodeError, RecursionError):
            delta = [("", "无法预览" if filename in old else None, "无法预览" if filename in new else None)]
        for key, av, bv in delta:
            if len(changes) == 200:
                clipped = True
                break
            changes.append({"file": filename, "key": key[:280],
                            "before": (av[:340] + '…' if len(av) > 340 else av) if av is not None else None,
                            "after": (bv[:340] + '…' if len(bv) > 340 else bv) if bv is not None else None})
        if clipped:
            break
    return {"config_changes": changes, "clipped": clipped}


def management_router(ops: Operations) -> APIRouter:
    router = APIRouter()

    @router.post('/order/dependencies')
    def dependency_preview(body: DependencyPreview):
        order = check_order(body.order)
        hints = None
        if body.share_code is not None:
            imported = import_share(body.share_code, include_workshop=True)
            if imported['order'] != order:
                raise HTTPException(422, '前置检查的加载顺序与分享码不一致。')
            hints = imported['workshop']
        mods = scan_installed_mods(ops.paths(ops.settings()))
        verified = {mid: mod.workshop_id for mid, mod in mods.items()
                    if mod.source == 'workshop' and mod.workshop_id and not mod.manifest.parse_error}
        plan = plan_dependencies(order, mods, share_hints=hints, verified_catalog=verified)
        return {'order': plan['draft'], 'ready': plan['review_ready'], 'cycles': plan['cycles'],
                'limited': plan.get('limited', False),
                'enable': [item for item in plan['items'] if item['state'] == 'inactive'],
                'missing': [item for item in plan['items'] if item['state'] == 'missing'],
                'invalid': [item for item in plan['items'] if item['state'] == 'invalid_manifest']}

    @router.post('/order/changes')
    def order_changes(body: Order):
        settings = ops.settings()
        paths = ops.paths(settings)
        mods = scan_installed_mods(paths)
        order = check_order(body.order)
        before = ops.inspect_save(paths).active_mods
        return {**order_diff(before, order, mods), "issues": [asdict(item) for item in validate_active_mods(order, mods, ops.game_version(settings, paths))]}

    @router.post('/profiles/compare')
    def compare(body: Compare):
        settings = ops.settings()
        paths = ops.paths(settings)
        profiles = {profile.name: profile for profile in ops.profiles.list_profiles()}
        if body.after not in profiles or (body.before is not None and body.before not in profiles):
            raise HTTPException(404, '未找到预设。')
        after = profiles[body.after]
        before = profiles[body.before] if body.before else None
        target = ops.profiles._profile_path(after.name) / 'ModConfigs' if after.has_isolated_configs else None
        source = (ops.profiles._profile_path(before.name) / 'ModConfigs' if before.has_isolated_configs else None) if before else paths.mod_configs_dir
        mods = scan_installed_mods(paths)
        order = check_order(after.active_mods)
        return {**order_diff(before.active_mods if before else ops.inspect_save(paths).active_mods, order, mods),
                **(config_diff(source, target) if target is not None and (source is not None or before is None) else {"config_changes": [], "clipped": False}),
                "config_comparable": target is not None and (source is not None or before is None),
                "order_comparable": after.mode == 'json_keys' and (before is None or before.mode == 'json_keys'),
                "issues": [asdict(item) for item in validate_active_mods(order, mods, ops.game_version(settings, paths))]}

    @router.post('/share/preview')
    def share_preview(body: ShareImport):
        return ops.preview_share(body.code)

    @router.post('/mods/open')
    def open_mod(body: ModOpen):
        mod = scan_installed_mods(ops.paths(ops.settings())).get(body.id)
        if mod is None:
            raise HTTPException(404, '未找到已安装模组。')
        if body.workshop:
            if not mod.workshop_id:
                raise HTTPException(404, '该模组没有已识别的工坊条目。')
            if not open_workshop_page_in_steam(mod.workshop_id):
                raise HTTPException(409, '无法打开工坊页面，请检查 Steam 或默认浏览器。')
            return {'message': '已请求打开工坊页面。'}
        if os.name != 'nt' or is_link(mod.folder) or not mod.folder.is_dir():
            raise HTTPException(409, '无法打开本地模组目录。')
        os.startfile(str(mod.folder.resolve()))
        return {'message': '已打开模组目录。'}

    @router.post('/profiles/shortcut')
    def shortcut(body: Profile):
        from .profile_launch import create_shortcut
        return create_shortcut(ops, body.name)

    return router
