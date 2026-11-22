"""Easy Save 3 (SaveFile.es3) reader, backup guard, profile switcher, and share codes."""

from __future__ import annotations

import base64
import json
import re
import shutil
import uuid
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

from .file_ops import (
    atomic_write,
    content_digest,
    copy_plain_tree,
    is_link,
    recover_staged_directories,
    staged_directory,
    write_lock,
)
from .paths import GamePaths, is_game_running

MOD_PROFILE_KEYS = ("modOrder", "modTrust", "modTrustMac", "workshopOwners")
SHARE_PREFIX = "PSMM1:"
_BACKUP_STAMP = re.compile(r"_(\d{8}_\d{6})(?:_(\d{6})_[0-9a-f]{8})?\.es3\.bak\Z")


@dataclass
class ProfileMeta:
    """Metadata stored for a saved mod preset/profile."""

    name: str
    created_at: str
    updated_at: str
    save_note: str = ""
    active_mods: list[str] = field(default_factory=list)
    mode: str = "json_keys"  # "json_keys" | "full_file"
    has_isolated_configs: bool = False


@dataclass
class SaveFileState:
    """Current parsed state of SaveFile.es3."""

    exists: bool
    is_corrupted_zero: bool
    is_json: bool
    active_mods: list[str]
    raw_data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    content: Optional[bytes] = field(default=None, repr=False, compare=False)


def _safe_profile_dir_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name.strip())
    if (
        not cleaned or cleaned.endswith((".", " ")) or cleaned.casefold().startswith(".psmm-")
        or any(ord(c) < 32 for c in cleaned) or PureWindowsPath(cleaned).is_reserved()
    ):
        raise ValueError("无效的预设名称：请避免空名称、点号结尾及系统保留名。")
    return cleaned


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=4).encode("utf-8")


def _set_order(data: dict[str, Any], order: list[str]) -> None:
    old = data.get("modOrder")
    if isinstance(old, dict) and "value" in old:
        data["modOrder"] = {**old, "value": list(order)}
    elif isinstance(old, list):
        data["modOrder"] = list(order)
    else:
        data["modOrder"] = {
            "__type": "System.Collections.Generic.List`1[[System.String, mscorlib]],mscorlib",
            "value": list(order),
        }


def _extract_es3_list(entry: Any) -> list[str]:
    if isinstance(entry, dict) and isinstance(entry.get("value"), list):
        return [str(x) for x in entry["value"]]
    if isinstance(entry, list):
        return [str(x) for x in entry]
    return []


def inspect_save_file(save_file: Optional[Path]) -> SaveFileState:
    """Read SaveFile.es3 without modifying it, detecting 0-byte corruption."""
    if save_file is None or not save_file.exists():
        return SaveFileState(exists=False, is_corrupted_zero=False, is_json=False, active_mods=[])

    try:
        raw_bytes = save_file.read_bytes()
    except OSError as exc:
        return SaveFileState(
            exists=True,
            is_corrupted_zero=False,
            is_json=False,
            active_mods=[],
            error=f"读取 SaveFile.es3 失败: {exc}",
        )

    if len(raw_bytes) == 0 or all(b == 0 for b in raw_bytes):
        return SaveFileState(
            exists=True,
            is_corrupted_zero=True,
            is_json=False,
            active_mods=[],
            error="检测到 SaveFile.es3 为空或全 0 字节损坏，可从自动备份中恢复。",
            content=raw_bytes,
        )

    try:
        text = raw_bytes.decode("utf-8-sig")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SaveFileState(
            exists=True,
            is_corrupted_zero=False,
            is_json=False,
            active_mods=[],
            error="SaveFile.es3 无法解析为明文 JSON。",
            content=raw_bytes,
        )

    if not isinstance(data, dict):
        return SaveFileState(
            exists=True,
            is_corrupted_zero=False,
            is_json=False,
            active_mods=[],
            error="SaveFile.es3 JSON 根节点不是对象。",
            content=raw_bytes,
        )

    active_mods = _extract_es3_list(data.get("modOrder"))
    return SaveFileState(
        exists=True,
        is_corrupted_zero=False,
        is_json=True,
        active_mods=active_mods,
        raw_data=data,
        content=raw_bytes,
    )


class ProfileManager:
    """Manages mod profiles, SaveFile.es3 backups, ModConfigs isolation, and share codes."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.profiles_dir = workspace_dir / "profiles"
        self.backups_dir = workspace_dir / "backups"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def backup_save_file(self, save_file: Path, tag: str = "auto") -> Optional[Path]:
        state = inspect_save_file(save_file)
        return self._backup_state(state, tag)

    @staticmethod
    def _backup_configs_path(backup: Path) -> Path:
        return backup.with_name(backup.name + ".ModConfigs")

    def _backup_state(self, state: SaveFileState, tag: str, configs: Optional[Path] = None) -> Optional[Path]:
        if not state.exists or state.is_corrupted_zero:
            return None
        if state.content is None:
            raise OSError(state.error or "无法读取原存档，未写入任何数据。")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", tag):
            raise ValueError("无效的备份标签")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = self.backups_dir / f"SaveFile_{tag}_{stamp}_{uuid.uuid4().hex[:8]}.es3.bak"
        if configs is not None:
            if is_link(configs):
                raise OSError(f"拒绝备份链接目录: {configs}")
            with staged_directory(self._backup_configs_path(dest)) as (pending, commit):
                if configs.exists():
                    copy_plain_tree(configs, pending)
                else:
                    pending.mkdir()
                commit()
        atomic_write(dest, state.content)
        return dest

    def recover_pending_switch(self, paths: GamePaths) -> tuple[bool, str]:
        """Reconcile an interrupted profile switch before showing the active data."""
        target = paths.mod_configs_dir
        if paths.save_file is None or target is None or not target.parent.is_dir():
            return True, ""
        if not any(target.parent.glob(".psmm-txn-*/transaction.json")):
            return True, ""
        try:
            with write_lock(paths.save_file.parent):
                if is_game_running():
                    return False, "请关闭游戏后刷新，恢复未完成的预设切换。"
                recovered = recover_staged_directories(target.parent, target.name, paths.save_file)
            return True, "已检查并恢复中断的预设切换。" if recovered else ""
        except OSError as exc:
            return False, f"无法自动恢复上次预设切换: {exc}"

    @staticmethod
    def recover_live_configs(paths: GamePaths) -> None:
        """Reconcile ModConfigs before a write; caller holds the save-directory lock."""
        target = paths.mod_configs_dir
        if target is not None:
            recover_staged_directories(target.parent, target.name, paths.save_file)

    def list_backups(self) -> list[Path]:
        """Return backups sorted newest first."""
        def sort_key(path: Path) -> tuple[str, str, int]:
            match = _BACKUP_STAMP.search(path.name)
            if match:
                return match[1], match[2] or "000000", path.stat().st_mtime_ns
            return "", "", path.stat().st_mtime_ns

        return sorted(
            (p for p in self.backups_dir.glob("SaveFile_*.es3.bak") if p.is_file() and not is_link(p)),
            key=sort_key,
            reverse=True,
        )

    @staticmethod
    def _replace_live(save_file: Path, expected: SaveFileState, new_bytes: bytes,
                      config_target: Optional[Path] = None, config_source: Optional[Path] = None) -> None:
        def check_live() -> None:
            if is_game_running():
                raise OSError("检测到游戏正在运行，请先关闭游戏再修改存档。")
            if is_link(save_file):
                raise OSError("存档是链接文件，拒绝写入。")
            try:
                existing = save_file.read_bytes()
            except FileNotFoundError:
                existing = None
            if existing != expected.content or (expected.exists and expected.content is None):
                raise OSError("存档已变更或无法读取，请刷新后重试。")

        if config_target is not None and config_source is not None:
            with staged_directory(config_target, save_transition=(
                content_digest(expected.content), content_digest(new_bytes)
            )) as (pending, commit):
                copy_plain_tree(config_source, pending)
                check_live()
                commit()
                check_live()
                atomic_write(save_file, new_bytes)
        else:
            check_live()
            atomic_write(save_file, new_bytes)

    def restore_backup(self, backup_path: Path, paths: GamePaths) -> tuple[bool, str]:
        if paths.save_file is None:
            return False, "未定位到 SaveFile.es3 路径"
        if is_game_running():
            return False, "检测到游戏正在运行，请先关闭游戏后再恢复备份。"
        save_file = paths.save_file
        try:
            if is_link(backup_path) or backup_path.resolve().parent != self.backups_dir.resolve():
                return False, "只能恢复管理器备份目录中的普通文件。"
            chosen = inspect_save_file(backup_path)
            if not chosen.content or chosen.is_corrupted_zero:
                return False, "备份不存在、不可读，或内容为空/全零。"
            config_copy = self._backup_configs_path(backup_path)
            with write_lock(save_file.parent):
                self.recover_live_configs(paths)
                current = inspect_save_file(save_file)
                restore_configs = paths.mod_configs_dir if config_copy.exists() or is_link(config_copy) else None
                self._backup_state(current, "pre_restore", restore_configs)
                self._replace_live(save_file, current, chosen.content, restore_configs, config_copy)
            detail = "（含模组配置）" if restore_configs is not None else "（仅存档）"
            return True, f"已从备份恢复{detail}: {backup_path.name}"
        except OSError as exc:
            return False, f"恢复失败: {exc}"

    def _profile_path(self, name: str) -> Path:
        root = self.profiles_dir.resolve()
        pdir = root / _safe_profile_dir_name(name)
        if is_link(pdir) or pdir.resolve().parent != root:
            raise ValueError("预设目录不在允许范围内，或是链接目录。")
        return pdir

    @staticmethod
    def _read_profile(pdir: Path) -> ProfileMeta:
        path = pdir / "profile_meta.json"
        if is_link(path):
            raise ValueError("预设元数据不能是链接文件。")
        meta = ProfileMeta(**json.loads(path.read_text(encoding="utf-8")))
        if (not isinstance(meta.name, str) or meta.mode not in ("json_keys", "full_file")
                or not isinstance(meta.active_mods, list)
                or not all(isinstance(mid, str) for mid in meta.active_mods)
                or not isinstance(meta.has_isolated_configs, bool)):
            raise ValueError("预设元数据格式异常。")
        return meta

    def list_profiles(self) -> list[ProfileMeta]:
        """List all saved profiles sorted by name."""
        profiles: list[ProfileMeta] = []
        for sub in sorted(self.profiles_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith(".psmm-"):
                try:
                    if is_link(sub):
                        continue
                    meta = self._read_profile(sub)
                    if self._profile_path(meta.name) == sub.resolve():
                        profiles.append(meta)
                except (OSError, ValueError, TypeError):
                    continue
        return profiles

    def save_profile(
        self,
        name: str,
        paths: GamePaths,
        save_note: str = "",
        isolate_configs: bool = True,
    ) -> tuple[bool, str]:
        """Snapshot current SaveFile.es3 mod keys (and optionally ModConfigs) as a profile."""
        if paths.save_file is None or not paths.save_file.is_file() or is_link(paths.save_file):
            return False, "未找到 SaveFile.es3，请先启动一次游戏并在模组菜单启用至少一个模组。"
        if is_game_running():
            return False, "检测到游戏正在运行，请先关闭游戏再保存预设。"
        try:
            with write_lock(self.profiles_dir), write_lock(paths.save_file.parent):
                recover_staged_directories(self.profiles_dir)
                self.recover_live_configs(paths)
                pdir = self._profile_path(name)
                now = datetime.now().isoformat(timespec="seconds")
                previous = self._read_profile(pdir) if pdir.exists() else None
                if previous is not None and previous.name != name.strip():
                    return False, "预设名和已有方案映射到同一目录，请换一个名称。"
                state = inspect_save_file(paths.save_file)
                if not state.content or state.is_corrupted_zero:
                    return False, state.error or "存档不可读，无法保存预设。"
                config_source = paths.mod_configs_dir if isolate_configs else None
                meta = ProfileMeta(
                    name=name.strip(), created_at=previous.created_at if previous else now,
                    updated_at=now, save_note=save_note.strip(), active_mods=state.active_mods,
                    mode="json_keys" if state.is_json else "full_file",
                    has_isolated_configs=config_source is not None,
                )
                with staged_directory(pdir) as (pending, commit):
                    pending.mkdir()
                    if state.raw_data is not None:
                        keys = {k: state.raw_data[k] for k in MOD_PROFILE_KEYS if k in state.raw_data}
                        atomic_write(pending / "mod_keys.json", _json_bytes(keys))
                    atomic_write(pending / "SaveFile.es3.snapshot", state.content)
                    if config_source is not None:
                        if config_source.exists() or is_link(config_source):
                            copy_plain_tree(config_source, pending / "ModConfigs")
                        else:
                            (pending / "ModConfigs").mkdir()
                    atomic_write(pending / "profile_meta.json", _json_bytes(asdict(meta)))
                    if is_game_running() or paths.save_file.read_bytes() != state.content:
                        return False, "保存期间游戏启动或存档已变更，请关闭游戏后重试。"
                    commit()
        except (OSError, ValueError, TypeError) as exc:
            return False, f"保存预设失败: {exc}"
        if meta.mode == "full_file":
            return True, f"已保存方案「{meta.name}」（整档快照，恢复时将替换整个存档）"
        return True, f"已保存方案「{meta.name}」（含 {len(meta.active_mods)} 个已启用模组）"

    def apply_profile(
        self,
        name: str,
        paths: GamePaths,
        override_order: Optional[list[str]] = None,
        restore_configs: bool = True,
        allow_full_restore: bool = False,
        expected_save: SaveFileState | None = None,
    ) -> tuple[bool, str]:
        """Apply a saved profile onto SaveFile.es3 (and optionally ModConfigs/)."""
        if is_game_running():
            return False, "检测到《深空当铺》正在运行，请先关闭游戏再切换方案，以免退出时被游戏覆盖。"
        if paths.save_file is None:
            return False, "未配置或未找到 SaveFile.es3 路径"

        save_file = paths.save_file
        try:
            with write_lock(self.profiles_dir), write_lock(save_file.parent):
                recover_staged_directories(self.profiles_dir)
                self.recover_live_configs(paths)
                pdir = self._profile_path(name)
                if not pdir.is_dir():
                    return False, f"方案「{name}」不存在"
                meta = self._read_profile(pdir)
                if meta.name != name.strip():
                    return False, "预设名称不匹配。"
                live = inspect_save_file(save_file)
                if expected_save is not None and (live.exists != expected_save.exists or live.content != expected_save.content):
                    return False, "存档在切换预设前发生变化。请重新预览。"
                if meta.mode == "json_keys":
                    if live.raw_data is None:
                        return False, "当前存档不是可合并的 JSON，未覆盖存档。请先恢复可用存档。"
                    keys_file = pdir / "mod_keys.json"
                    if is_link(keys_file):
                        return False, "拒绝读取链接形式的模组数据。"
                    saved_keys = json.loads(keys_file.read_text(encoding="utf-8"))
                    if not isinstance(saved_keys, dict) or not saved_keys.keys() <= set(MOD_PROFILE_KEYS):
                        return False, "预设模组数据格式异常。"
                    merged = {k: v for k, v in live.raw_data.items() if k not in MOD_PROFILE_KEYS}
                    merged.update(saved_keys)
                    if override_order is not None:
                        _set_order(merged, override_order)
                    data = _json_bytes(merged)
                else:
                    if not allow_full_restore:
                        return False, "这是整档快照，恢复会覆盖游戏设置。请先明确确认整档恢复。"
                    if override_order is not None:
                        return False, "整档快照不支持更改加载顺序。"
                    snap_file = pdir / "SaveFile.es3.snapshot"
                    if is_link(snap_file):
                        return False, "拒绝读取链接形式的快照。"
                    snapshot = inspect_save_file(snap_file)
                    if not snapshot.content or snapshot.is_corrupted_zero:
                        return False, "整档快照不存在、不可读，或内容为空/全零。"
                    data = snapshot.content

                target = paths.mod_configs_dir if restore_configs and meta.has_isolated_configs else None
                source = pdir / "ModConfigs" if target is not None else None
                if source is not None and (is_link(source) or not source.is_dir()):
                    return False, "预设缺少独立配置目录，未切换。"
                backup = self._backup_state(live, "pre_switch", target)
                self._replace_live(save_file, live, data, target, source)
        except (OSError, ValueError, TypeError) as exc:
            return False, f"切换预设失败: {exc}"
        suffix = f"，切换前备份: {backup.name}" if backup else ""
        if meta.mode == "full_file":
            return True, f"已恢复整档快照「{meta.name}」{suffix}"
        count = len(override_order if override_order is not None else meta.active_mods)
        return True, f"已切换到方案「{meta.name}」（共 {count} 个启用模组）{suffix}"

    def update_live_mod_order(
        self, paths: GamePaths, new_order: list[str]
    ) -> tuple[bool, str]:
        """Update modOrder inside live SaveFile.es3 (reordering or disabling already-trusted mods).

        Note: To authorize brand-new mods that have never been approved, the user must
        click Enable once inside the game so the game computes modTrustMac.
        """
        if is_game_running():
            return False, "检测到游戏正在运行，请先关闭游戏。"
        if paths.save_file is None or not paths.save_file.is_file():
            return False, "SaveFile.es3 不存在，无法写入排序。"
        if len(set(new_order)) != len(new_order) or not all(isinstance(mid, str) and mid for mid in new_order):
            return False, "加载顺序包含空或重复的模组 ID。"
        try:
            with write_lock(paths.save_file.parent):
                self.recover_live_configs(paths)
                state = inspect_save_file(paths.save_file)
                if state.raw_data is None:
                    return False, "SaveFile.es3 非明文 JSON，无法写入排序。"
                updated = dict(state.raw_data)
                _set_order(updated, new_order)
                self._backup_state(state, "reorder")
                self._replace_live(paths.save_file, state, _json_bytes(updated))
        except (OSError, ValueError, TypeError) as exc:
            return False, f"写入排序失败: {exc}"
        return True, f"已更新加载顺序（共 {len(new_order)} 个模组）"

    def delete_profile(self, name: str) -> tuple[bool, str]:
        try:
            with write_lock(self.profiles_dir):
                recover_staged_directories(self.profiles_dir)
                pdir = self._profile_path(name)
                if not pdir.is_dir() or self._read_profile(pdir).name != name.strip():
                    return False, "方案不存在或名称不匹配"
                shutil.rmtree(pdir)
        except (OSError, ValueError, TypeError) as exc:
            return False, f"删除预设失败: {exc}"
        return True, f"已删除方案「{name}」"

    @staticmethod
    def export_share_code(
        profile_name: str,
        active_mods: list[str],
        workshop_ids: dict[str, Optional[str]],
        note: str = "",
    ) -> str:
        """Export an r2modman-style share code containing mod IDs, Workshop IDs, and order."""
        payload = {
            "v": 1,
            "name": profile_name,
            "note": note,
            "mods": [
                {"id": mid, "workshop_id": workshop_ids.get(mid)} for mid in active_mods
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw, level=9)
        return SHARE_PREFIX + base64.urlsafe_b64encode(compressed).decode("ascii")

    @staticmethod
    def decode_share_code(code: str) -> dict[str, Any]:
        """Decode a PSMM1 share code into its mod list payload."""
        text = code.strip()
        if not text.startswith(SHARE_PREFIX):
            raise ValueError("无效的分享码格式（必须以 PSMM1: 开头）")
        raw = base64.urlsafe_b64decode(text[len(SHARE_PREFIX) :])
        payload = json.loads(zlib.decompress(raw).decode("utf-8"))
        if not isinstance(payload, dict) or "mods" not in payload:
            raise ValueError("分享码数据结构异常")
        return payload
