"""File and Steam operations behind the local WebUI."""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from fastapi import HTTPException

from core.config_help import KNOWN_HINTS, read_mod_readme
from core.config_inspector import (
    EditableFile,
    discover_editable_files,
    load_mod_config_entries,
    save_mod_config_entries,
    save_raw_json_file,
)
from core.dependency_plan import plan_dependencies
from core.es3_manager import (
    ProfileManager,
    SaveFileState,
    _json_bytes,
    _set_order,
    inspect_save_file,
)
from core.file_ops import atomic_write, content_digest, is_link, write_lock
from core.game_review import read_game_review
from core.manifest import check_game_version_compatibility, version_entry_status
from core.mod_cover import cover_candidates
from core.workbench_preferences import profile_activity, save_activity
from core.paths import GAME_EXE_NAME, GamePaths, discover_paths, is_game_running, launch_game
from core.scanner import DEFAULT_GAME_VERSION, InstalledMod, resolve_mod_copies, scan_installed_mods, scan_mod_copies
from core.settings import AppSettings
from core.share_verification import ShareError, create_verified_code, inspect_share, read_share
from core.test_session import active_session
from core.workshop import (
    WorkshopItem,
    download_workshop_item_manual,
    find_downloader_executable,
    parse_workshop_ids_from_text,
)
from core.workshop_cover import WorkshopCoverCache
from core.workshop_collections import installed_state, steam_id
from core.workshop_versions import read_public_update_times

from .schemas import ConfigWrite


_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_CONFIG_RELOAD = "配置文件已变更，请重新打开后再保存。"
_COMPATIBILITY = {
    "compatible": "声明匹配", "legacy": "仅声明 0.x", "unknown": "未标注", "incompatible": "版本不匹配",
}


def message(result: tuple[bool, str], status_code: int = 409) -> dict[str, str]:
    ok, detail = result
    if not ok:
        raise HTTPException(status_code, detail)
    return {"message": detail}


def check_order(order: list[str]) -> list[str]:
    if len(order) > 4096 or any(not mid.strip() or len(mid) > 512 or "\0" in mid for mid in order):
        raise HTTPException(422, "加载顺序包含无效的模组 ID，或条目过多。")
    if len(set(order)) != len(order):
        raise HTTPException(422, "加载顺序包含重复的模组 ID。")
    return order


def _save_revision(path: Path | None, state: SaveFileState) -> str:
    # Bind the revision to the selected save as well as its bytes.
    location = str(path.resolve()) if path is not None else ""
    return content_digest(f"{location}\0{content_digest(state.content)}".encode("utf-8")) or ""


def _file_id(file: EditableFile) -> str:
    return content_digest(f"{file.mod_id}\0{file.kind}\0{file.path.resolve()}".encode("utf-8")) or ""


def _no_link(path: Path | None) -> None:
    if path is not None and is_link(path):
        raise HTTPException(409, "拒绝通过链接文件或目录读写数据。")


def _config_bytes(path: Path) -> bytes:
    _no_link(path)
    with path.open("rb") as stream:
        content = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(content) > _MAX_CONFIG_BYTES:
        raise HTTPException(413, "配置文件过大，无法在网页中编辑。")
    return content


def _form_data(file: EditableFile, content: bytes) -> tuple[list, Any, str] | None:
    if file.kind != "mod_config":
        return None
    try:
        entries, raw, kind = load_mod_config_entries(file.path)
        json.dumps(raw, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        return None
    if _config_bytes(file.path) != content:
        raise HTTPException(409, _CONFIG_RELOAD)
    if kind == "raw_json" or len({entry.key for entry in entries}) != len(entries):
        return None
    return entries, raw, kind


def _check_json_value(value: Any) -> None:
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, "配置必须是有效的 UTF-8 JSON，不能包含 NaN 或 Infinity。") from None


def import_share(code: str, *, include_workshop: bool = False) -> dict:
    """Decode either format without reading any local mod files."""
    return _share_list(_share_payload(code), include_workshop=include_workshop)


def _share_payload(code: str) -> dict:
    try:
        return read_share(code)
    except ShareError:
        raise HTTPException(422, "分享码损坏、不受支持或超过大小限制。") from None


def _share_list(payload: dict, *, include_workshop: bool) -> dict:
    mods = payload["mods"]
    result = {"name": payload["name"], "order": [item["id"] for item in mods]}
    if include_workshop:
        result["workshop"] = {item["id"]: item["workshop_id"] for item in mods
                              if item["workshop_id"] is not None}
    return result


class Operations:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.absolute()
        self.settings_path = self.data_dir / "settings.json"
        for path in (self.data_dir, self.data_dir / "profiles", self.data_dir / "backups"):
            _no_link(path)
        self.profiles = ProfileManager(self.data_dir)
        self._local_lock = Lock()
        self._cover_lock = Lock()
        self.cover_cache = WorkshopCoverCache(self.data_dir / "workshop_covers")
        self._cover_retry: dict[str, float] = {}

    @contextmanager
    def local(self) -> Iterator[None]:
        # An interactive Steam download can last minutes; fail fast instead of queuing writes.
        if not self._local_lock.acquire(blocking=False):
            raise HTTPException(409, "管理器正在处理另一项本地操作，请稍后重试。")
        try:
            lease = self.data_dir / ".operations"
            _no_link(lease)
            with write_lock(lease):
                yield
        except OSError:
            raise HTTPException(409, "无法访问本地文件，请检查路径、权限或其他正在运行的管理器。") from None
        finally:
            self._local_lock.release()

    def settings(self) -> AppSettings:
        _no_link(self.settings_path)
        try:
            settings = AppSettings.load(self.settings_path)
        except (AttributeError, UnicodeError):
            settings = AppSettings()
        defaults = AppSettings()
        for field, default in vars(defaults).items():
            if type(getattr(settings, field)) is not type(default):
                setattr(settings, field, default)
        settings.game_version = settings.game_version or DEFAULT_GAME_VERSION
        return settings

    def update_settings(self, changes: dict) -> None:
        if "game_version" in changes:
            changes = {**changes, "game_version_manual": bool(changes["game_version"])}
        with write_lock(self.data_dir):
            settings = replace(self.settings(), **changes)
            data = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
            atomic_write(self.settings_path, data.encode("utf-8"))

    @staticmethod
    def paths(settings: AppSettings) -> GamePaths:
        return discover_paths(
            custom_game_dir=settings.game_dir or None,
            custom_save_dir=settings.save_dir or None,
            custom_workshop_dir=settings.workshop_dir or None,
        )

    def export_share(self, name: str, order: list[str], *, verified: bool = False) -> dict:
        name, order = name.strip(), check_order(order)
        paths = self.paths(self.settings())
        if verified:
            try:
                code = create_verified_code(paths, name, order)
            except ShareError:
                raise HTTPException(409, "无法为完整清单生成可核验分享码：模组缺失、不可读取、发生变更或超过验证限制。") from None
        else:
            observed = inspect_share(paths, order, fingerprint=False)
            try:
                code = self.profiles.export_share_code(name, order, {mid: row.workshop_id for mid, row in observed.items()})
            except UnicodeError:
                raise HTTPException(422, "分享清单必须是有效的 UTF-8 文本。") from None
        import_share(code)  # Also enforce the HTTP input size on exports.
        return {"code": code}

    def preview_share(self, code: str) -> dict:
        payload = _share_payload(code)
        result = _share_list(payload, include_workshop=True)
        version = payload["v"]
        observed = inspect_share(self.paths(self.settings()), result["order"], fingerprint=version == 2)
        rows = []
        for item in payload["mods"]:
            mid = item["id"]
            actual = observed[mid]
            if actual.missing:
                status = "missing"
            elif actual.fingerprint is None:
                status = "unverifiable"
            else:
                status = "match" if actual.fingerprint == item["fingerprint"] else "different"
            rows.append({
                "id": mid, "name": actual.name or mid, "status": status,
                "expected_source": item.get("source"), "actual_source": actual.source,
                "workshop_id": item["workshop_id"],
            })
        return {
            **result, "format": f"PSMM{version}", "version": version, "verification": rows,
            "missing": [{"id": row["id"], "workshop_id": row["workshop_id"]} for row in rows if row["status"] == "missing"],
        }

    @staticmethod
    def inspect_save(paths: GamePaths) -> SaveFileState:
        for path in (paths.save_data_dir, paths.save_file):
            _no_link(path)
        return inspect_save_file(paths.save_file)

    @staticmethod
    def game_version(settings: AppSettings, paths: GamePaths) -> str:
        return settings.game_version or read_game_review(paths).version

    def state(self, *, recover: bool = False) -> dict:
        settings = self.settings()
        paths = self.paths(settings)
        review = read_game_review(paths)
        game_version = settings.game_version or review.version
        save = self.inspect_save(paths)
        recovery_ok, recovery_msg = True, ""
        if recover:
            recovery_ok, recovery_msg = self.profiles.recover_pending_switch(paths)
            if recovery_ok and recovery_msg:
                save = self.inspect_save(paths)
        copies = list(scan_mod_copies(paths))
        # The game deduplicates by manifest ID; downloads are tracked by physical Workshop ID.
        downloads = {mod.workshop_id: mod for mod in copies if mod.workshop_id
                     and not mod.manifest.parse_error and steam_id(mod.workshop_id) == mod.workshop_id
                     and not is_link(mod.folder) and not is_link(mod.folder / 'manifest.xml')}
        installed = resolve_mod_copies(copies)
        workshop_times = read_public_update_times(self.data_dir)
        mods = []
        active = set(save.active_mods)
        for mod in sorted(installed.values(), key=lambda mod: (mod.display_name.casefold(), mod.mod_id)):
            manifest = mod.manifest
            compat = check_game_version_compatibility(manifest.game_versions, game_version)
            missing = set(manifest.prerequisites) - active
            version_label = "待识别游戏版本" if compat == "unknown" and manifest.game_versions else _COMPATIBILITY.get(compat, compat)
            status = "清单无法读取" if manifest.parse_error else (
                f"缺前置({len(missing)})" if missing else version_label)
            audit_key = (mod.mod_id, manifest.mod_version, mod.source,
                         mod.workshop_id if mod.source == "workshop" else "0")
            mods.append({
                "id": mod.mod_id, "name": mod.display_name, "author": manifest.author,
                "version": manifest.mod_version, "description": manifest.description,
                "source": "本地覆写" if mod.overridden_workshop else ("本地" if mod.source == "local" else "工坊"),
                "status": status, "folder": str(mod.folder), "workshop_id": mod.workshop_id, "prerequisites": manifest.prerequisites,
                "workshop_public_time": workshop_times.get(mod.workshop_id) if mod.workshop_id else None,
                "local_cover": next(cover_candidates(paths.local_mods_dir if mod.source == "local" else paths.workshop_dir,
                                                     mod.folder), None) is not None,
                "capabilities": manifest.capabilities, "configurable": bool(self._editable_files(mod, paths)),
                "version_checks": [{"version": entry, "status": version_entry_status(entry, game_version)}
                                   for entry in manifest.game_versions],
                "permissions": [{"id": cap, "risk": risk, "description": description}
                                for cap, risk, description in dict.fromkeys(manifest.capability_badges())],
                "trust_observed": review.trust.get(audit_key) if not manifest.parse_error else None,
                "manifest_readable": not manifest.parse_error,
            })
        revision = _save_revision(paths.save_file, save)
        activity = profile_activity(self.data_dir, paths.save_file)
        status = recovery_msg or save.error
        if not status:
            status = (f"已加载 {len(mods)} 个模组，当前启用 {len(save.active_mods)} 个" if save.exists else
                      "未找到存档，请先启动一次游戏或检查路径设置。")
        return {
            "mods": mods, "order": save.active_mods, "revision": revision,
            "workshop_downloads": [{
                "workshop_id": item_id, "id": mod.mod_id, "name": mod.display_name,
                "author": mod.manifest.author, "description": mod.manifest.description,
                "source": "Steam 工坊目录" if mod.source == "workshop" else "游戏 Mods 目录",
                "local_cover": installed[mod.mod_id] is mod and next(cover_candidates(
                    mod.folder.parent, mod.folder), None) is not None,
            } for item_id, mod in sorted(downloads.items(), key=lambda pair: (pair[1].display_name.casefold(), pair[0]))],
            "save_activity": save_activity(self.data_dir, paths.save_file, revision),
            "diagnostic_active": active_session(),
            "status": status,
            "game_ready": bool(paths.game_dir and (paths.game_dir / GAME_EXE_NAME).is_file()),
            "save_ready": bool(save.is_json and recovery_ok),
            "game_version": game_version, "last_profile": settings.last_profile,
            "game_version_source": "manual" if settings.game_version else "log" if review.version else "unknown",
            "game_audit_at": review.logged_at,
            "profiles": [{"name": p.name, "mode": p.mode, "save_note": p.save_note, **({"activity": activity[p.name]} if p.name in activity else {})}
                         for p in self.profiles.list_profiles()],
        }

    def write_order(self, paths: GamePaths, order: list[str], revision: str, *, check_dependencies: bool = False) -> dict:
        save_file = paths.save_file
        if save_file is None or not save_file.is_file():
            raise HTTPException(409, "未找到可写入的存档。")
        _no_link(save_file.parent)
        if is_game_running():
            raise HTTPException(409, "请关闭游戏后再写入排序。")
        # The public core update acquires its own lock. Reuse its write primitives so
        # revision checking and backup/replace share one filesystem critical section.
        with write_lock(save_file.parent):
            self.profiles.recover_live_configs(paths)
            save = self.inspect_save(paths)
            if _save_revision(save_file, save) != revision:
                raise HTTPException(409, "存档已变更，请刷新后重新编辑加载顺序。")
            if save.raw_data is None:
                raise HTTPException(409, "存档不是可编辑的 JSON。")
            if check_dependencies:
                plan = plan_dependencies(order, scan_installed_mods(paths))
                if not plan["review_ready"] or plan["draft"] != order:
                    raise HTTPException(409, "当前模组或加载顺序未通过前置检查，请返回前置补齐向导重新扫描。")
                if is_game_running():
                    raise HTTPException(409, "请关闭游戏后再写入排序。")
            updated = dict(save.raw_data)
            _set_order(updated, order)
            data = _json_bytes(updated)
            self.profiles._backup_state(save, "reorder")
            self.profiles._replace_live(save_file, save, data)
        return {"message": f"已更新加载顺序（共 {len(order)} 个模组）"}

    @staticmethod
    def _editable_files(mod: InstalledMod, paths: GamePaths) -> list[EditableFile]:
        mod_root = paths.local_mods_dir if mod.source == "local" else paths.workshop_dir
        safe_mod = mod.folder if mod_root is not None and not is_link(mod_root) else None
        config_root = paths.mod_configs_dir
        if paths.save_data_dir is not None and is_link(paths.save_data_dir):
            config_root = None
        return discover_editable_files(mod.mod_id, safe_mod, config_root)

    def config_files(self, paths: GamePaths, mod_id: str) -> list[EditableFile]:
        mod = scan_installed_mods(paths).get(mod_id)
        if mod is None:
            raise HTTPException(404, "未找到该模组。")
        return self._editable_files(mod, paths)

    def _config_file(self, paths: GamePaths, mod_id: str, file_id: str) -> EditableFile:
        for file in self.config_files(paths, mod_id):
            if _file_id(file) == file_id:
                return file
        raise HTTPException(404, "未找到该模组的可编辑配置。")

    def list_config_files(self, paths: GamePaths, mod_id: str) -> dict:
        mod = scan_installed_mods(paths).get(mod_id)
        if mod is None:
            raise HTTPException(404, "未找到该模组。")
        root = paths.local_mods_dir if mod.source == "local" else paths.workshop_dir
        return {"files": [{"id": _file_id(f), "label": f.label, "filename": f.path.name} for f in self._editable_files(mod, paths)],
                "readme": read_mod_readme(root, mod.folder)}

    def read_config(self, paths: GamePaths, mod_id: str, file_id: str) -> dict:
        file = self._config_file(paths, mod_id, file_id)
        content = _config_bytes(file.path)
        revision = content_digest(content)
        form = _form_data(file, content)
        if form is not None:
            entries = []
            for entry in form[0]:
                label, description, source = KNOWN_HINTS.get((mod_id, entry.key), ("", "", ""))
                entries.append({"key": entry.key, "value": entry.value, "value_type": entry.value_type,
                                "label": entry.label or label, "description": entry.description or description,
                                "help_source": "配置文件" if entry.description else source})
            return {"mode": "form", "revision": revision, "entries": entries}
        try:
            text = content.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(422, "该配置不是 UTF-8 文本。") from None
        return {"mode": "raw", "revision": revision, "text": text}

    def write_config(self, paths: GamePaths, body: ConfigWrite) -> dict:
        # Recover an interrupted profile switch even when ModConfigs is temporarily absent.
        message(self.profiles.recover_pending_switch(paths))
        file = self._config_file(paths, body.mod_id, body.id)
        if file.kind == "mod_config":
            lock_dir = paths.save_data_dir
        else:
            lock_dir = next((root for root in (paths.local_mods_dir, paths.workshop_dir)
                             if root is not None and file.path.is_relative_to(root)), file.path.parent)
        if lock_dir is None:
            raise HTTPException(409, "未找到配置目录。")
        _no_link(lock_dir)
        with write_lock(lock_dir):
            if is_game_running():
                raise HTTPException(409, "请关闭游戏后再保存配置。")
            if file.kind == "mod_config":
                self.profiles.recover_live_configs(paths)
            file = self._config_file(paths, body.mod_id, body.id)
            content = _config_bytes(file.path)
            if content_digest(content) != body.revision:
                raise HTTPException(409, _CONFIG_RELOAD)
            form = _form_data(file, content)
            backup_dir = self.data_dir / "config_backups"
            _no_link(backup_dir)
            if form is not None:
                entries, raw, kind = form
                if body.values is None or body.text is not None:
                    raise HTTPException(422, "表单配置需要提交 values。")
                types = {entry.key: entry.value_type for entry in entries}
                accepted = {"bool": (bool,), "int": (int,), "float": (int, float), "str": (str,)}
                for key, value in body.values.items():
                    if key not in types or (types[key] != "json" and type(value) not in accepted[types[key]]):
                        raise HTTPException(422, "配置包含未知参数或不匹配的参数类型。")
                try:
                    values = {k: float(v) if types[k] == "float" else v for k, v in body.values.items()}
                except OverflowError:
                    raise HTTPException(422, "数值超出配置支持的范围。") from None
                _check_json_value(values)
                expected = self._updated_form(raw, kind, values)
                self._check_config_size(expected)
                self._check_config_write(file.path, content)
                save_mod_config_entries(file.path, values, kind, raw, backup_dir)
            else:
                if body.text is None or body.values is not None:
                    raise HTTPException(422, "原始 JSON 配置需要提交 text。")
                try:
                    parsed = json.loads(body.text)
                except (ValueError, RecursionError):
                    raise HTTPException(422, "配置不是有效的 JSON。") from None
                _check_json_value(parsed)
                self._check_config_size(parsed)
                self._check_config_write(file.path, content)
                save_raw_json_file(file.path, body.text, backup_dir)
            return {"message": "已保存配置并备份原文件。", "revision": content_digest(_config_bytes(file.path))}

    @staticmethod
    def _updated_form(raw: Any, kind: str, values: dict) -> Any:
        if kind == "flat_dict":
            return {**raw, **values}
        entries = raw["entries"] if kind == "wrapped_entries" else raw
        updated = [{**entry, "value": values.get(str(entry["key"]), entry["value"])} for entry in entries]
        return {**raw, "entries": updated} if kind == "wrapped_entries" else updated

    @staticmethod
    def _check_config_size(value: Any) -> None:
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise HTTPException(413, "保存后的配置文件过大。")

    @staticmethod
    def _check_config_write(path: Path, content: bytes) -> None:
        if is_game_running() or _config_bytes(path) != content:
            raise HTTPException(409, _CONFIG_RELOAD)

    def workshop_covers(self, ids: list[str], fetch, *, cached_only: bool = False) -> dict[str, str]:
        # Cover requests are separate from startup and grouped by the frontend.
        def known():
            return {fid: link for fid in ids if (link := self.cover_cache.link(fid))}

        covers = known()
        if cached_only or len(covers) == len(ids):
            return covers
        with self._cover_lock:
            now = time.monotonic()
            covers = known()
            missing = [fid for fid in ids if fid not in covers and self._cover_retry.get(fid, 0) <= now]
            if missing:
                try:
                    items = fetch(missing)
                except Exception:
                    items = []
                expected = set(missing)
                for item in items:
                    fid = item.published_file_id
                    if fid in expected and (link := self.cover_cache.note(fid, item.preview_url)):
                        covers[fid] = link
                if len(self._cover_retry) + len(missing) > 4096:
                    self._cover_retry.clear()
                retry = time.monotonic() + 60
                self._cover_retry.update({fid: retry for fid in missing if fid not in covers})
            return covers

    def workshop_response(self, items: list[WorkshopItem]) -> dict:
        items = [item for item in items if re.fullmatch(r"[0-9]{1,20}", item.published_file_id)]
        return {"items": [{
            "id": item.published_file_id, "title": item.title,
            "description": item.description, "tags": item.tags,
            "subscriptions": item.subscriptions, "file_size": item.file_size, "url": item.url,
            "preview_url": self.cover_cache.note(item.published_file_id, item.preview_url),
        } for item in items]}

    @staticmethod
    def workshop_ids(text: str) -> list[str]:
        ids = parse_workshop_ids_from_text(text)
        if not ids or len(ids) > 30:
            raise HTTPException(422, "请输入 1 到 30 个有效的工坊链接或数字 ID。")
        return ids

    def download(self, paths: GamePaths, settings: AppSettings, item_id: str, confirm_login: bool) -> dict:
        if steam_id(item_id) != item_id:
            raise HTTPException(422, "无效的工坊 ID。")
        existing = installed_state(paths, item_id)
        if existing == 'installed':
            raise HTTPException(409, "该工坊模组已下载，无需重复安装。")
        if existing == 'blocked':
            raise HTTPException(409, "该工坊模组的安装目录已被占用，未覆盖已有文件。")
        if paths.local_mods_dir is None:
            raise HTTPException(409, "未找到游戏的 Mods 目录，请检查路径设置。")
        _no_link(paths.local_mods_dir)
        username = settings.steam_username.strip()
        if username and not confirm_login:
            raise HTTPException(409, "需要明确确认使用所配置的下载器登录 Steam。")
        if is_game_running():
            raise HTTPException(409, "请关闭游戏后再安装工坊模组。")
        kind, exe = find_downloader_executable(settings.downloader_path or None)
        if not kind or not exe:
            raise HTTPException(409, "未找到 DepotDownloader 或 SteamCMD，请检查下载器设置。")
        return message(download_workshop_item_manual(
            item_id, paths.local_mods_dir, kind, exe, steam_username=username,
            staging_dir=self.data_dir / "downloads",
        ))


def public_settings(settings: AppSettings) -> dict:
    return {
        "game_dir": settings.game_dir, "save_dir": settings.save_dir, "workshop_dir": settings.workshop_dir,
        "game_version": settings.game_version or DEFAULT_GAME_VERSION, "downloader_path": settings.downloader_path,
        "steam_username": settings.steam_username,
        "show_undocumented_config": settings.show_undocumented_config,
        "has_steam_api_key": bool(settings.steam_api_key.strip()),
    }
