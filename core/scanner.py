"""Local & Steam Workshop mod scanner, dependency sorter, and conflict detector."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .manifest import ModManifest, check_game_version_compatibility, parse_manifest
from .file_ops import is_link
from .paths import GamePaths

DEFAULT_GAME_VERSION = ""


@dataclass
class InstalledMod:
    """Represents a discovered mod on disk (local or Steam Workshop)."""

    manifest: ModManifest
    folder: Path
    source: str  # "local" | "workshop"
    workshop_id: Optional[str] = None
    overridden_workshop: bool = False
    has_config_file: bool = False
    loot_files: list[Path] = field(default_factory=list)

    @property
    def mod_id(self) -> str:
        return self.manifest.mod_id

    @property
    def display_name(self) -> str:
        return self.manifest.name or self.manifest.mod_id


@dataclass(frozen=True)
class AssetOwner:
    mod_id: str
    name: str
    path: str


@dataclass(frozen=True)
class AssetOverlap:
    kind: str
    key: str
    comparison: str
    owners: list[AssetOwner]


@dataclass
class ModIssue:
    """Validation issue (missing prerequisite, load-order violation, loot conflict, etc.)."""

    severity: str  # "error" | "warning" | "info"
    mod_id: str
    message: str
    asset: Optional[AssetOverlap] = None


def _scan_mod_folder(folder: Path, source: str, mod_configs_dir: Optional[Path]) -> Optional[InstalledMod]:
    manifest_file = folder / "manifest.xml"
    if not manifest_file.is_file():
        return None
    manifest = parse_manifest(manifest_file)
    match = re.fullmatch(r"([0-9]{7,12})" if source == "workshop" else r"ws_([0-9]{7,12})", folder.name)
    workshop_id = match[1] if match else None

    loot_dir = folder / "LootTables"
    loot_files = sorted(loot_dir.glob("*.json")) if loot_dir.is_dir() else []

    has_cfg = False
    if mod_configs_dir and mod_configs_dir.is_dir():
        has_cfg = (mod_configs_dir / f"{manifest.mod_id}.json").is_file()

    return InstalledMod(
        manifest=manifest,
        folder=folder,
        source=source,
        workshop_id=workshop_id,
        has_config_file=has_cfg,
        loot_files=loot_files,
    )


def scan_mod_copies(paths: GamePaths) -> Iterator[InstalledMod]:
    """Discover physical copies, with Workshop copies before local overrides."""
    for root, source in ((paths.workshop_dir, "workshop"), (paths.local_mods_dir, "local")):
        if root is None or not root.is_dir():
            continue
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or (source == "local" and sub.name.startswith(".")):
                continue
            item = _scan_mod_folder(sub, source, paths.mod_configs_dir)
            if item:
                yield item


def resolve_mod_copies(copies: Iterable[InstalledMod]) -> dict[str, InstalledMod]:
    """Build the game-facing mod list, where local copies override the same mod ID."""
    mods: dict[str, InstalledMod] = {}
    for item in copies:
        if item.source == "local" and item.mod_id in mods:
            item.overridden_workshop = True
            item.workshop_id = mods[item.mod_id].workshop_id
        mods[item.mod_id] = item
    return mods


def scan_installed_mods(paths: GamePaths) -> dict[str, InstalledMod]:
    return resolve_mod_copies(scan_mod_copies(paths))


def topological_sort_mods(
    active_ids: list[str],
    installed: dict[str, InstalledMod],
) -> tuple[list[str], list[str]]:
    """Sort active mods so prerequisites always appear before dependents.

    Preserves the user's relative order wherever constraints allow (inspired by RimPy/RimSort).
    Returns (sorted_ids, cycle_or_warning_messages).
    """
    active_set = set(active_ids)
    original_pos = {mid: idx for idx, mid in enumerate(active_ids)}

    in_degree: dict[str, int] = {mid: 0 for mid in active_ids}
    dependents: dict[str, list[str]] = defaultdict(list)

    for mid in active_ids:
        mod = installed.get(mid)
        if not mod:
            continue
        for pre in mod.manifest.prerequisites:
            if pre in active_set and pre != mid:
                dependents[pre].append(mid)
                in_degree[mid] += 1

    ready = [mid for mid in active_ids if in_degree[mid] == 0]
    ready.sort(key=lambda m: original_pos[m])

    ordered: list[str] = []
    while ready:
        cur = ready.pop(0)
        ordered.append(cur)
        for nxt in dependents.get(cur, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=lambda m: original_pos[m])

    warnings: list[str] = []
    if len(ordered) < len(active_ids):
        remaining = [m for m in active_ids if m not in set(ordered)]
        warnings.append(f"检测到循环依赖，保留原顺序末尾项: {', '.join(remaining)}")
        ordered.extend(remaining)

    return ordered, warnings


def detect_loot_conflicts(
    active_ids: list[str],
    installed: dict[str, InstalledMod],
) -> dict[str, list[str]]:
    """Find LootTables targets (table/group IDs) patched by multiple active mods."""
    target_owners: dict[str, list[str]] = defaultdict(list)
    for mid in active_ids:
        mod = installed.get(mid)
        if not mod:
            continue
        for lf in mod.loot_files:
            try:
                data = json.loads(lf.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError):
                continue
            patches = data.get("patches") if isinstance(data, dict) else None
            for patch in patches if isinstance(patches, list) else []:
                if not isinstance(patch, dict):
                    continue
                target = patch.get("table") or patch.get("group")
                if isinstance(target, str) and target and mid not in target_owners[target]:
                    target_owners[target].append(mid)
    return {t: owners for t, owners in target_owners.items() if len(owners) > 1}


def _asset_digest(path: Path) -> bytes | None:
    """Bound the work of optional byte comparisons on untrusted mod files."""
    limit = 16 * 1024 * 1024
    try:
        if is_link(path) or path.stat().st_size > limit:
            return None
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        return hashlib.sha256(data).digest() if len(data) <= limit else None
    except OSError:
        return None


def detect_asset_conflicts(active_ids: list[str], installed: dict[str, InstalledMod]) -> list[ModIssue]:
    """Report static name overlaps without inferring game load/override behavior."""
    owners: dict[tuple[str, str], dict[tuple[str, Path], set[tuple[str, ...]]]] = defaultdict(dict)
    for mid in active_ids:
        mod = installed.get(mid)
        if not mod or is_link(mod.folder):
            continue
        for parts, label in [(('Textures', 'Items'), '物品贴图'), (('PaperBooks',), '书籍文件'),
                             (('Localization',), '本地化键')]:
            folder = mod.folder.joinpath(*parts)
            if any(is_link(mod.folder.joinpath(*parts[:i])) for i in range(1, len(parts) + 1)) or not folder.is_dir():
                continue
            for path in folder.iterdir():
                if is_link(path) or not path.is_file():
                    continue
                owner = (mid, path)
                if label == '物品贴图' and path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
                    owners[(label, path.stem.casefold())][owner] = set()
                elif label == '书籍文件':
                    owners[(label, path.name.casefold())][owner] = set()
                elif label == '本地化键' and path.suffix.lower() == '.csv':
                    try:
                        if path.stat().st_size > 4 * 1024 * 1024:
                            continue
                        with path.open(encoding='utf-8-sig', newline='') as stream:
                            rows = csv.reader(stream)
                            next(rows, None)
                            for row in rows:
                                if len(row) >= 2 and row[0].strip():
                                    key = (label, f'{path.stem.casefold()} / {row[0].strip()}')
                                    owners[key].setdefault(owner, set()).add(tuple(row[1:]))
                    except (OSError, UnicodeError, csv.Error):
                        continue
    issues = []
    for (label, key), entries in sorted(owners.items()):
        if len({mid for mid, _ in entries}) < 2:
            continue
        if label == '本地化键':
            comparison = 'same' if len(set().union(*entries.values())) == 1 else 'different'
        else:
            digests = {_asset_digest(path) for _, path in entries}
            comparison = 'different' if len(digests - {None}) > 1 else ('unknown' if None in digests else 'same')
        participants = [AssetOwner(mid, installed[mid].display_name, path.relative_to(installed[mid].folder).as_posix())
                        for mid, path in entries]
        status = {'same': '相同资源', 'different': '同名不同内容', 'unknown': '无法比对'}[comparison]
        names = '、'.join(f'{installed[mid].display_name} ({mid})' for mid in dict.fromkeys(mid for mid, _ in entries))
        issues.append(ModIssue('info', participants[0].mod_id, f'{label} · {status}：{key}（{names}）',
                               AssetOverlap(label, key, comparison, participants)))
        if len(issues) >= 200:
            break
    return issues


def validate_active_mods(
    active_ids: list[str],
    installed: dict[str, InstalledMod],
    game_version: str = DEFAULT_GAME_VERSION,
) -> list[ModIssue]:
    """Run RimSort/Irony-style diagnostic checks on an active load order."""
    issues: list[ModIssue] = []
    pos_map = {mid: i for i, mid in enumerate(active_ids)}

    for idx, mid in enumerate(active_ids):
        mod = installed.get(mid)
        if not mod:
            issues.append(ModIssue("warning", mid, f"启用的模组 '{mid}' 在本地或工坊目录中未找到"))
            continue

        if mod.manifest.parse_error:
            issues.append(ModIssue("error", mid, mod.manifest.parse_error))

        compat = check_game_version_compatibility(mod.manifest.game_versions, game_version)
        if compat == "incompatible":
            issues.append(
                ModIssue(
                    "warning",
                    mid,
                    f"{mod.display_name} 声明的游戏版本 ({', '.join(mod.manifest.game_versions)}) 未覆盖 {game_version}",
                )
            )

        if mod.manifest.has_deprecated_capabilities():
            issues.append(
                ModIssue(
                    "warning",
                    mid,
                    f"{mod.display_name} 声明了已弃用能力；具体限制以当前游戏的扫描结果为准",
                )
            )

        for pre in mod.manifest.prerequisites:
            if pre not in installed:
                issues.append(
                    ModIssue("error", mid, f"{mod.display_name} 缺少未安装的前置模组: {pre}")
                )
            elif pre not in pos_map:
                issues.append(
                    ModIssue("error", mid, f"{mod.display_name} 需要启用前置模组: {installed[pre].display_name} ({pre})")
                )
            elif pos_map[pre] > idx:
                issues.append(
                    ModIssue(
                        "warning",
                        mid,
                        f"加载顺序有误: {mod.display_name} 排在其前置模组 {installed[pre].display_name} ({pre}) 之前",
                    )
                )

    for target, owners in detect_loot_conflicts(active_ids, installed).items():
        issues.append(
            ModIssue(
                "info",
                owners[0],
                f"掉落表修改重叠: '{target}' · " + '、'.join(f'{installed[mid].display_name} ({mid})' for mid in owners),
            )
        )

    return issues + detect_asset_conflicts(active_ids, installed)
