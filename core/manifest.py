"""Official manifest.xml parser, GameVersions matcher, and capability classifier."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CAPABILITY_RISK_MAP: dict[str, tuple[str, str]] = {
    "network": ("High", "访问互联网，可向外部服务发送或接收数据"),
    "steam-auth": ("Medium", "向外部在线服务证明玩家的 Steam 正版身份票证"),
    "steam": ("Medium", "通过你的账户使用 Steam 功能（成就、好友、游戏内界面）"),
    "process": ("Medium", "在系统浏览器打开字面 http/https 链接"),
    "files": ("High", "读取或写入此电脑上任意位置的文件（受当前账户权限限制）"),
    "threads": ("Deprecated", "在后台运行代码，可能在模组停用后仍继续运行"),
    "reflection-external": ("Deprecated", "通过反射访问游戏或模组范围之外的代码"),
    "dynamic-code": ("Deprecated", "运行时加载或生成代码"),
    "native": ("Deprecated", "调用原生系统库"),
    "window": ("Medium", "移动或调整游戏窗口的大小与位置"),
}

_VERSION_RE = re.compile(r"^([0-9]{1,9})(?:\.([0-9]{1,9}))?(?:\.([0-9]{1,9}))?(?:\.([0-9]{1,9}))?$")
_LEGACY_RE = re.compile(r"^0\.\d+(?:\.\d+){0,2}[A-Za-z]?$", re.ASCII)


@dataclass
class ModManifest:
    """Parsed representation of a mod's manifest.xml."""

    mod_id: str
    name: str
    author: str = ""
    mod_version: str = ""
    description: str = ""
    game_versions: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    assemblies: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    manifest_path: Optional[Path] = None
    parse_error: Optional[str] = None

    def capability_badges(self) -> list[tuple[str, str, str]]:
        """Return (capability, risk_level, description_zh) for each declared capability."""
        out: list[tuple[str, str, str]] = []
        for cap in self.capabilities:
            risk, desc = CAPABILITY_RISK_MAP.get(cap, ("Unknown", "未知声明能力"))
            out.append((cap, risk, desc))
        return out

    def has_deprecated_capabilities(self) -> bool:
        """Return True if any capability is slated for rejection after 2026-12-10."""
        return any(
            CAPABILITY_RISK_MAP.get(c, ("", ""))[0] == "Deprecated"
            for c in self.capabilities
        )


def _parse_version_tuple(raw: str) -> Optional[tuple[int, ...]]:
    """Parse dotted numeric version up to 4 segments."""
    m = _VERSION_RE.match(raw.strip())
    if not m:
        return None
    parts = [int(g) for g in m.groups() if g is not None]
    return tuple(parts)


def _pad_version(v: tuple[int, ...], fill: int = 0) -> tuple[int, int, int, int]:
    """Pad a 1-4 segment version tuple to 4 segments."""
    lst = list(v)
    while len(lst) < 4:
        lst.append(fill)
    return (lst[0], lst[1], lst[2], lst[3])


def version_entry_status(entry: str, current_game_version: str) -> str:
    """Evaluate a <Version> entry against the game's 4-part version.

    Returns one of: 'match', 'mismatch', 'legacy', 'invalid', 'unknown'.
    Rules follow ModdingGuide.zh-CN.md:
    - Open-ended forms ('*', '1.0 - *', '1.0+') are invalid (red).
    - On 0.x builds the legacy menu compares the leading numeric versions.
    - Prefix entries ('1.0.0' or '1.0.0.*') match all sub-revisions.
    - Closed intervals ('1.0.0.5 - 1.0.1') match inclusive bounds.
    """
    text = entry.strip()
    if not text:
        return "invalid"
    if text in ("*", "1.0+") or text.endswith("+") or "- *" in text or "*-" in text:
        return "invalid"

    running = current_game_version.strip()
    if _LEGACY_RE.fullmatch(running):
        declared = re.match(r"[0-9.]+", text)
        if declared is None:
            return "invalid"
        current = running.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        prefix = declared[0].rstrip(".")
        return "match" if (prefix == current or prefix.startswith(current + ".")
                           or current.startswith(prefix + ".")) else "mismatch"
    cur = _parse_version_tuple(running)
    if cur is None:
        return "unknown"
    cur4 = _pad_version(cur, 0)

    if _LEGACY_RE.fullmatch(text):
        return "legacy"

    if " - " in text:
        left_s, right_s = [p.strip() for p in text.split(" - ", 1)]
        if "*" in left_s or "*" in right_s:
            return "invalid"
        left = _parse_version_tuple(left_s)
        right = _parse_version_tuple(right_s)
        if left is None or right is None:
            return "invalid"
        low = _pad_version(left, 0)
        high = _pad_version(right, 999999)
        return "match" if low <= cur4 <= high else "mismatch"

    cleaned = text[:-2] if text.endswith(".*") else text
    if "*" in cleaned:
        return "invalid"
    pref = _parse_version_tuple(cleaned)
    if pref is None:
        return "invalid"
    if pref[0] == 0:
        return "legacy"
    return "match" if cur4[: len(pref)] == pref else "mismatch"


def check_game_version_compatibility(
    game_versions: list[str], current_game_version: str
) -> str:
    """Return 'unknown' (no entries), 'compatible', 'legacy', or 'incompatible'."""
    if not game_versions or not current_game_version:
        return "unknown"
    statuses = [version_entry_status(v, current_game_version) for v in game_versions]
    if "match" in statuses:
        return "compatible"
    if "unknown" in statuses:
        return "unknown"
    if "legacy" in statuses and "mismatch" not in statuses and "invalid" not in statuses:
        return "legacy"
    return "incompatible"


def _child_text(root: ET.Element, tag: str, default: str = "") -> str:
    elem = root.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return default


def parse_manifest(manifest_path: Path) -> ModManifest:
    """Parse a mod's manifest.xml safely; never raises on malformed XML."""
    try:
        raw = manifest_path.read_text(encoding="utf-8-sig", errors="replace")
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError) as exc:
        fallback_id = manifest_path.parent.name
        return ModManifest(
            mod_id=fallback_id,
            name=fallback_id,
            manifest_path=manifest_path,
            parse_error=f"manifest.xml 解析失败: {exc}",
        )

    mod_id = _child_text(root, "ID") or manifest_path.parent.name
    name = _child_text(root, "Name") or mod_id

    game_versions: list[str] = []
    gv_node = root.find("GameVersions")
    if gv_node is not None:
        for v in gv_node.findall("Version"):
            if v.text and v.text.strip():
                game_versions.append(v.text.strip())

    prerequisites: list[str] = []
    pre_node = root.find("Prerequisites")
    if pre_node is not None:
        for m in pre_node.findall("Mod"):
            if m.text and m.text.strip():
                prerequisites.append(m.text.strip())

    assemblies: list[str] = []
    asm_node = root.find("Assemblies")
    if asm_node is not None:
        for a in asm_node.findall("Assembly"):
            if a.text and a.text.strip():
                assemblies.append(a.text.strip())

    capabilities: list[str] = []
    cap_node = root.find("Capabilities")
    if cap_node is not None:
        for c in cap_node.findall("Capability"):
            if c.text and c.text.strip():
                capabilities.append(c.text.strip().lower())

    return ModManifest(
        mod_id=mod_id,
        name=name,
        author=_child_text(root, "Author"),
        mod_version=_child_text(root, "ModVersion"),
        description=_child_text(root, "Description"),
        game_versions=game_versions,
        prerequisites=prerequisites,
        assemblies=assemblies,
        capabilities=capabilities,
        manifest_path=manifest_path,
    )
