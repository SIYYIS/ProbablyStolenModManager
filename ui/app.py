"""Desktop UI and action handlers for Probably Stolen Mod Manager."""

from __future__ import annotations

import json
import platform
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core.config_inspector import (
    ConfigEntryItem,
    EditableFile,
    discover_editable_files,
    load_mod_config_entries,
    parse_edited_value,
    save_mod_config_entries,
    save_raw_json_file,
)
from core.es3_manager import ProfileManager, inspect_save_file
from core.file_ops import is_link, write_lock
from core.manifest import check_game_version_compatibility
from core.game_review import read_game_review
from core.paths import GamePaths, discover_paths, is_game_running, launch_game
from core.scanner import (
    InstalledMod,
    scan_installed_mods,
    topological_sort_mods,
    validate_active_mods,
)
from core.settings import AppSettings
from core.workshop import (
    WorkshopItem,
    browse_workshop_public,
    download_workshop_item_manual,
    fetch_workshop_details_by_ids,
    find_downloader_executable,
    open_workshop_page_in_steam,
    parse_workshop_ids_from_text,
    query_workshop_via_api,
)
from ui.config_page import build_config_page, reset_config_editor, show_config_editor
from ui.theme import ACCENT, BG, BORDER, FONT, SOFT, SURFACE, TEXT, configure_theme, panel, text_options
from ui.workbench import build_local_page, build_workbench
from ui.workshop_page import build_workshop_page, render_workshop_detail, update_workshop_results

# Curated category filters displayed in Chinese in the Workshop tab
WORKSHOP_FILTER_TAGS = [
    ("all", "全部分类"),
    ("Gameplay", "玩法拓展"),
    ("Quality of Life", "体验优化 (QoL)"),
    ("UI", "界面美化/UI"),
    ("Items", "新增物品"),
    ("Machines", "机器与设施"),
    ("Customers", "顾客与剧情"),
    ("Overhaul", "大型重制/大修"),
    ("Translation", "汉化/本地化"),
    ("Balance", "平衡性调整"),
    ("Framework", "前置框架/库"),
    ("Bugfix", "Bug 修复"),
]


def _enable_windows_dpi_awareness() -> None:
    if platform.system() == "Windows":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass


class ModManagerApp:
    """Main desktop window for local mods, configurations, and Workshop."""

    def __init__(self, data_dir: Path) -> None:
        _enable_windows_dpi_awareness()
        self.data_dir = data_dir
        self.settings_path = data_dir / "settings.json"
        self.settings = AppSettings.load(self.settings_path)
        self.profile_mgr = ProfileManager(data_dir)

        self.paths: GamePaths = self._resolve_paths()
        self.installed_mods: dict[str, InstalledMod] = {}
        self.active_order: list[str] = []
        self._saved_order: list[str] = []
        self.workshop_items: list[WorkshopItem] = []
        self._pending_downloads: set[str] = set()

        # Config tab state
        self._current_editable_files: list[EditableFile] = []
        self._current_cfg_file: Optional[EditableFile] = None
        self._current_cfg_entries: list[ConfigEntryItem] = []
        self._current_cfg_raw: Any = None
        self._current_cfg_fmt: str = ""
        self._cfg_widgets: dict[str, tuple[Any, str]] = {}

        self.root = tk.Tk()
        self.root.title("深空当铺：可能是偷的 · Mod 预设与工坊管理器")
        self.root.geometry("1220x760")
        self.root.minsize(1000, 640)
        configure_theme(self.root)
        self._build_ui()
        self.refresh_all()

    def _resolve_paths(self) -> GamePaths:
        return discover_paths(
            custom_game_dir=self.settings.game_dir or None,
            custom_workshop_dir=self.settings.workshop_dir or None,
            custom_save_dir=self.settings.save_dir or None,
        )

    def _build_ui(self) -> None:
        build_workbench(self)
        build_local_page(self)
        build_config_page(self)
        build_workshop_page(self, WORKSHOP_FILTER_TAGS)
        self._show_page("local")
        self.root.bind("<Control-f>", self._focus_local_search)

    def _show_page(self, name: str) -> None:
        self.page_frames[name].tkraise()
        label, button = self.page_navigation[name]
        self.page_title_var.set(label)
        self.page_path_var.set(f"工作台  /  {label}")
        for _, nav in self.page_navigation.values():
            nav.configure(style="Active.Rail.TButton" if nav is button else "Rail.TButton")

    def _focus_local_search(self, _event: tk.Event) -> str:
        self._show_page("local")
        self.local_search_entry.focus_set()
        self.local_search_entry.selection_range(0, tk.END)
        return "break"

    # ------------------------------------------------------------------ Data Refresh
    def refresh_all(self) -> None:
        self.paths = self._resolve_paths()
        _, recovery_msg = self.profile_mgr.recover_pending_switch(self.paths)
        self.installed_mods = scan_installed_mods(self.paths)
        state = inspect_save_file(self.paths.save_file)

        if state.is_corrupted_zero:
            self.status_var.set("SaveFile.es3 全0字节损坏！请从侧栏打开「备份恢复」。")
        elif recovery_msg:
            self.status_var.set(recovery_msg)
        elif not state.exists:
            self.status_var.set("未检测到 SaveFile.es3，请先启动一次游戏或检查路径设置。")
        elif state.error:
            self.status_var.set(state.error)
        else:
            self.status_var.set(
                f"已加载 {len(self.installed_mods)} 个本地/工坊模组，当前启用 {len(state.active_mods)} 个"
            )

        self.active_order = list(state.active_mods)
        self._saved_order = self.active_order.copy()
        self._render_local_lists()
        self._refresh_profile_combo()
        self._refresh_config_mod_list()

        g_ok = "游戏目录已连接" if self.paths.game_dir else "未找到游戏目录"
        s_ok = "存档异常" if state.error or state.is_corrupted_zero else ("存档已找到" if state.exists else "未找到存档")
        self.path_badge_var.set(f"{g_ok}  ·  {s_ok}  ·  {self.resolved_game_version or '游戏版本待识别'}")
        self.path_badge.configure(style="Badge.TLabel" if self.paths.game_dir and s_ok == "存档已找到" else "Alert.Badge.TLabel")

    def _refresh_profile_combo(self) -> None:
        profiles = self.profile_mgr.list_profiles()
        names = [p.name for p in profiles]
        self.profile_combo["values"] = names
        if self.settings.last_profile in names:
            self.profile_var.set(self.settings.last_profile)
        elif names and not self.profile_var.get():
            self.profile_var.set(names[0])

    def _render_local_lists(self) -> None:
        self.resolved_game_version = self.settings.game_version or read_game_review(self.paths).version
        trees = (self.inactive_tree, self.active_tree)
        selected = next((tree.selection()[0] for tree in trees if tree.selection()), None)
        for tree in trees:
            tree.delete(*tree.get_children())

        query = self.local_search_var.get().strip().lower()
        active_set = set(self.active_order)

        def _mod_row(mid: str) -> tuple[str, str, str]:
            mod = self.installed_mods.get(mid)
            if not mod:
                return (mid, "—", "缺失文件")
            src = "本地覆写" if mod.overridden_workshop else ("本地" if mod.source == "local" else "工坊")
            compat = check_game_version_compatibility(
                mod.manifest.game_versions, self.resolved_game_version
            )
            compat_zh = {
                "compatible": "声明匹配",
                "legacy": "仅声明 0.x",
                "unknown": "待识别游戏版本" if mod.manifest.game_versions else "未标注",
                "incompatible": "版本不匹配",
            }.get(compat, compat)
            if mod.manifest.prerequisites:
                missing = [p for p in mod.manifest.prerequisites if p not in active_set]
                if missing:
                    compat_zh = f"缺前置({len(missing)})"
            return (mod.display_name, src, compat_zh)

        inactive = sorted(self.installed_mods.keys() - active_set,
                          key=lambda mid: self.installed_mods[mid].display_name.casefold())
        selected_tree = None
        for tree, ids, empty_label, empty_message in zip(
            trees, (inactive, dict.fromkeys(self.active_order)), self.local_empty_labels,
            ("没有未启用的模组", "暂无已启用模组"),
        ):
            for mid in ids:
                row = _mod_row(mid)
                if query and query not in f"{row[0]} {mid}".lower():
                    continue
                tag = "warning" if row[2] in ("缺失文件", "版本不匹配") or row[2].startswith("缺前置") else ""
                tree.insert("", tk.END, iid=mid, values=row, tags=(tag,) if tag else ())
            if tree.get_children():
                empty_label.place_forget()
            else:
                empty_label.configure(text="没有匹配的模组" if query else empty_message)
                empty_label.place(relx=0.5, rely=0.5, anchor="center")
            if selected is not None and tree.exists(selected):
                tree.selection_set(selected)
                tree.focus(selected)
                tree.see(selected)
                selected_tree = tree

        missing = len(active_set - self.installed_mods.keys())
        for metric, count in zip(self.local_metrics, (len(self.installed_mods), len(self.active_order), missing)):
            metric.set(str(count))
        self.missing_label.configure(style="MetricAlert.TLabel" if missing else "Metric.TLabel")
        self._update_order_status()
        self._on_select_mod(selected_tree)

    def _update_order_status(self) -> None:
        self.order_state_var.set("尚未保存" if self.active_order != self._saved_order else "")

    def _on_select_mod(self, tree: ttk.Treeview | None) -> None:
        other = self.active_tree if tree is self.inactive_tree else self.inactive_tree
        if tree is not None and tree.selection():
            if other.selection():
                other.selection_remove(*other.selection())
            mid = tree.selection()[0]
        else:
            mid = other.selection()[0] if other.selection() else None

        idx = self.active_order.index(mid) if mid in self.active_order else -1
        for button, enabled in zip(self.order_buttons, (idx > 0, 0 <= idx < len(self.active_order) - 1, idx >= 0)):
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        detail = self.mod_detail_text
        detail.configure(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        mod = self.installed_mods.get(mid)
        if mod:
            detail.insert(tk.END, f"{mod.display_name}\n", "title")
            caps = ", ".join(f"{c} [{r}]" for c, r, _ in mod.manifest.capability_badges()) or "无额外声明"
            for label, value in (
                ("ID", mid), ("版本", mod.manifest.mod_version or "—"), ("作者", mod.manifest.author or "—"),
                ("前置依赖", ", ".join(mod.manifest.prerequisites) or "无"), ("声明能力", caps),
                ("路径", mod.folder), ("简介", mod.manifest.description or "无"),
            ):
                detail.insert(tk.END, f"{label}\n", "caption")
                detail.insert(tk.END, f"{value}\n")
        elif mid is not None:
            detail.insert(tk.END, f"{mid}\n", "title")
            detail.insert(tk.END, "未在本地或工坊找到该模组。", "warning")
        else:
            detail.insert(tk.END, "未选择模组", "caption")
        detail.yview_moveto(0)
        detail.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ Profile & Order Actions
    def on_save_profile(self) -> None:
        default_name = self.profile_var.get() or "预设1"
        name = simpledialog.askstring("保存方案", "请输入方案名称（例如：星露谷种田档）：", initialvalue=default_name)
        if not name:
            return
        note = simpledialog.askstring("存档备注（可选）", "可备注该预设对应的游戏存档槽或用途：", initialvalue="") or ""
        ok, msg = self.profile_mgr.save_profile(name, self.paths, save_note=note, isolate_configs=True)
        if ok:
            self.settings.last_profile = name.strip()
            self.settings.save(self.settings_path)
            self._refresh_profile_combo()
            self.status_var.set(msg)
            messagebox.showinfo("成功", msg)
        else:
            messagebox.showerror("保存失败", msg)

    def on_apply_profile(self) -> bool:
        name = self.profile_var.get()
        if not name:
            messagebox.showwarning("提示", "请先选择一个预设方案。")
            return False
        profile = next((p for p in self.profile_mgr.list_profiles() if p.name == name), None)
        allow_full_restore = False
        if profile and profile.mode == "full_file":
            allow_full_restore = messagebox.askyesno(
                "确认整档恢复",
                "该预设保存的是完整 SaveFile.es3。恢复会覆盖音量、画质、按键等游戏设置，确定继续吗？",
            )
            if not allow_full_restore:
                return False
        ok, msg = self.profile_mgr.apply_profile(
            name, self.paths, restore_configs=True, allow_full_restore=allow_full_restore
        )
        if ok:
            self.settings.last_profile = name
            self.settings.save(self.settings_path)
            self.refresh_all()
            self.status_var.set(msg)
            messagebox.showinfo("切换成功", msg)
            return True
        messagebox.showerror("切换失败", msg)
        return False

    def on_delete_profile(self) -> None:
        name = self.profile_var.get()
        if not name:
            return
        if not messagebox.askyesno("确认删除", f"确定删除预设方案「{name}」吗？"):
            return
        ok, msg = self.profile_mgr.delete_profile(name)
        self.profile_var.set("")
        self._refresh_profile_combo()
        self.status_var.set(msg)

    def on_switch_and_launch(self) -> None:
        name = self.profile_var.get()
        if name:
            if not self.on_apply_profile():
                return
        ok, msg = launch_game(self.paths.game_dir)
        self.status_var.set(msg)
        if not ok:
            messagebox.showerror("启动失败", msg)

    def on_disable_selected(self) -> None:
        sel = self.active_tree.selection()
        if not sel:
            return
        mid = sel[0]
        if mid in self.active_order:
            self.active_order.remove(mid)
            self._render_local_lists()

    def on_move_active(self, delta: int) -> None:
        sel = self.active_tree.selection()
        if not sel:
            return
        mid = sel[0]
        if mid not in self.active_order:
            return
        idx = self.active_order.index(mid)
        new_idx = idx + delta
        if 0 <= new_idx < len(self.active_order):
            self.active_order[idx], self.active_order[new_idx] = (
                self.active_order[new_idx],
                self.active_order[idx],
            )
            self._render_local_lists()
            self.active_tree.selection_set(mid)

    def on_auto_sort(self) -> None:
        sorted_ids, warns = topological_sort_mods(self.active_order, self.installed_mods)
        self.active_order = sorted_ids
        self._render_local_lists()
        msg = "已完成拓扑依赖排序（已将所有前置模组自动前移）。"
        if warns:
            msg += "\n" + "\n".join(warns)
        messagebox.showinfo("智能排序", msg)

    def on_save_order(self) -> None:
        ok, msg = self.profile_mgr.update_live_mod_order(self.paths, self.active_order)
        if ok:
            self._saved_order = self.active_order.copy()
            self._update_order_status()
            self.status_var.set(msg)
            messagebox.showinfo("成功", msg)
        else:
            messagebox.showerror("写入失败", msg)

    def on_run_diagnostics(self) -> None:
        issues = validate_active_mods(
            self.active_order, self.installed_mods, self.resolved_game_version
        )
        if not issues:
            messagebox.showinfo("诊断检查", "未发现依赖缺失、版本冲突或加载顺序异常！")
            return
        lines = [f"[{i.severity.upper()}] {i.message}" for i in issues]
        messagebox.showinfo("诊断报告", "\n".join(lines[:30]))

    # ------------------------------------------------------------------ Config Tab Actions
    def _refresh_config_mod_list(self) -> None:
        self._current_cfg_file = None
        self._current_cfg_content = None
        self._current_editable_files = []
        self.cfg_file_combo.set("")
        self.cfg_file_combo["values"] = ()
        reset_config_editor(self)
        self.cfg_mod_list.delete(0, tk.END)
        self._cfg_mod_ids: list[str] = []
        for mid in sorted(self.installed_mods.keys(), key=lambda x: self.installed_mods[x].display_name.lower()):
            mod = self.installed_mods[mid]
            files = discover_editable_files(mid, mod.folder, self.paths.mod_configs_dir)
            if files:
                self._cfg_mod_ids.append(mid)
                tag = "★" if mod.has_config_file else "•"
                self.cfg_mod_list.insert(tk.END, f"{tag} {mod.display_name} ({mid})")

    def _on_select_cfg_mod(self) -> None:
        idxs = self.cfg_mod_list.curselection()
        if not idxs:
            return
        mid = self._cfg_mod_ids[idxs[0]]
        mod = self.installed_mods[mid]
        self._current_editable_files = discover_editable_files(
            mid, mod.folder, self.paths.mod_configs_dir
        )
        self.cfg_file_combo["values"] = [f.label for f in self._current_editable_files]
        if self._current_editable_files:
            self.cfg_file_combo.current(0)
            self._load_selected_cfg_file()
        else:
            self.cfg_file_var.set("")
            self._current_cfg_file = None
            reset_config_editor(self, empty_message="该模组没有可编辑的配置文件")

    def _load_selected_cfg_file(self) -> None:
        idx = self.cfg_file_combo.current()
        if idx < 0 or idx >= len(self._current_editable_files):
            return
        ef = self._current_editable_files[idx]
        self._current_cfg_file = None
        reset_config_editor(self, empty_message="无法打开所选配置文件")
        try:
            if is_link(ef.path):
                raise OSError("不能编辑链接文件。")
            self._current_cfg_content = ef.path.read_bytes()
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return

        if ef.kind == "mod_config":
            try:
                entries, raw_obj, fmt = load_mod_config_entries(ef.path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("读取失败", str(exc))
                return
            if fmt == "raw_json":
                messagebox.showerror("读取失败", "此配置格式不支持表单编辑，原文件未修改。")
                return
            self._current_cfg_entries = entries
            self._current_cfg_raw = raw_obj
            self._current_cfg_fmt = fmt

            for r, entry in enumerate(entries):
                ttk.Label(self.cfg_form_frame, text=entry.key, wraplength=240, style="Surface.TLabel").grid(
                    row=r, column=0, sticky="w", padx=(4, 8), pady=4
                )
                if entry.value_type == "bool":
                    var = tk.BooleanVar(value=bool(entry.value))
                    ttk.Checkbutton(self.cfg_form_frame, variable=var, text="启用", style="Surface.TCheckbutton").grid(
                        row=r, column=1, sticky="w", pady=4
                    )
                    self._cfg_widgets[entry.key] = (var, "bool")
                else:
                    val_str = (
                        json.dumps(entry.value, ensure_ascii=False)
                        if entry.value_type == "json"
                        else str(entry.value)
                    )
                    var_s = tk.StringVar(value=val_str)
                    ttk.Entry(self.cfg_form_frame, textvariable=var_s, width=20).grid(
                        row=r, column=1, sticky="ew", pady=4
                    )
                    ttk.Label(
                        self.cfg_form_frame, text=entry.value_type, style="Muted.Surface.TLabel"
                    ).grid(row=r, column=2, padx=6)
                    self._cfg_widgets[entry.key] = (var_s, entry.value_type)
            show_config_editor(self, "form")
        else:
            self.cfg_raw_text.insert("1.0", self._current_cfg_content.decode("utf-8-sig", errors="replace"))
            show_config_editor(self, "raw")
        self._current_cfg_file = ef

    def on_save_config(self) -> None:
        ef = self._current_cfg_file
        if not ef:
            return
        lock_dir = self.paths.save_data_dir if ef.kind == "mod_config" else self.paths.local_mods_dir
        if lock_dir is None:
            lock_dir = ef.path.parent
        backup_dir = self.data_dir / "config_backups"
        try:
            with write_lock(lock_dir):
                if is_game_running():
                    raise OSError("请关闭游戏后再保存配置。")
                if ef.kind == "mod_config":
                    self.profile_mgr.recover_live_configs(self.paths)
                if is_link(ef.path) or ef.path.read_bytes() != self._current_cfg_content:
                    raise OSError("配置在编辑期间已变更，请重新选择文件后再编辑。")
                if ef.kind == "mod_config":
                    updates = {k: parse_edited_value(var.get(), kind) for k, (var, kind) in self._cfg_widgets.items()}
                    save_mod_config_entries(ef.path, updates, self._current_cfg_fmt, self._current_cfg_raw, backup_dir)
                    _, self._current_cfg_raw, _ = load_mod_config_entries(ef.path)
                else:
                    save_raw_json_file(ef.path, self.cfg_raw_text.get("1.0", tk.END), backup_dir)
                self._current_cfg_content = ef.path.read_bytes()
            self.status_var.set(f"已保存配置: {ef.path.name}")
            messagebox.showinfo("保存成功", "配置参数已保存（已自动备份原文件）。")
        except Exception as exc:
            messagebox.showerror("保存失败", f"参数格式有误: {exc}")

    # ------------------------------------------------------------------ Workshop Tab Actions
    def _selected_workshop_tag(self) -> str:
        zh = self.ws_cat_var.get()
        for en, label in WORKSHOP_FILTER_TAGS:
            if label == zh:
                return en
        return "all"

    def on_fetch_workshop(self) -> None:
        tag = self._selected_workshop_tag()
        search = self.ws_search_var.get().strip()
        self.status_var.set("正在连接 Steam 创意工坊拉取列表...")

        def worker() -> None:
            try:
                if self.settings.steam_api_key.strip():
                    items, _ = query_workshop_via_api(
                        self.settings.steam_api_key, search_text=search, required_tag=tag
                    )
                else:
                    items = browse_workshop_public(search_text=search, required_tag=tag)
                self.root.after(0, lambda: self._populate_workshop_tree(items))
            except Exception as exc:
                error = str(exc)
                self.root.after(0, lambda: messagebox.showerror("拉取失败", f"连接 Steam 失败: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def on_lookup_workshop_ids(self) -> None:
        raw = simpledialog.askstring("按 ID 查询工坊 Mod", "请输入工坊链接或数字 ID（多个用逗号/空格分隔）：")
        if not raw:
            return
        ids = parse_workshop_ids_from_text(raw)
        if not ids:
            messagebox.showwarning("提示", "未识别到有效的数字工坊 ID。")
            return
        self.status_var.set(f"正在查询 {len(ids)} 个工坊条目...")

        def worker() -> None:
            try:
                items = fetch_workshop_details_by_ids(ids)
                self.root.after(0, lambda: self._populate_workshop_tree(items))
            except Exception as exc:
                error = str(exc)
                self.root.after(0, lambda: messagebox.showerror("查询失败", error))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_workshop_tree(self, items: list[WorkshopItem]) -> None:
        self.workshop_items = items
        for iid in self.ws_tree.get_children():
            self.ws_tree.delete(iid)
        for idx, it in enumerate(items):
            zh_tags = ", ".join(it.zh_tags) or "-"
            self.ws_tree.insert("", tk.END, iid=str(idx), values=(it.title, zh_tags, it.subscriptions))
        update_workshop_results(self)
        self.status_var.set(f"已获取 {len(items)} 个创意工坊模组")

    def _selected_workshop_item(self) -> Optional[WorkshopItem]:
        sel = self.ws_tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if 0 <= idx < len(self.workshop_items):
            return self.workshop_items[idx]
        return None

    def _on_select_workshop_item(self) -> None:
        it = self._selected_workshop_item()
        if not it:
            render_workshop_detail(self, None)
            return
        render_workshop_detail(self, it, it.description)

    def on_manual_download_workshop(self) -> None:
        it = self._selected_workshop_item()
        if not it:
            messagebox.showwarning("提示", "请先在左侧列表选择一个工坊模组。")
            return
        if not self.paths.local_mods_dir:
            messagebox.showerror("错误", "未定位到游戏 Mods/ 目录，请先在「路径与设置」中指定游戏目录。")
            return
        kind, exe = find_downloader_executable(self.settings.downloader_path or None)
        if not kind or not exe:
            messagebox.showwarning(
                "缺少下载器",
                "未找到 DepotDownloader 或 SteamCMD。\n请在「路径与设置」中指定 DepotDownloader.exe 或 steamcmd.exe 路径。",
            )
            return

        item_id = it.published_file_id
        if item_id in self._pending_downloads:
            return
        username = self.settings.steam_username.strip()
        if username and not messagebox.askokcancel(
            "通过下载器登录", "将在所选下载器中登录 Steam。请确认下载器来源可信。"
        ):
            return

        target_mods = self.paths.local_mods_dir
        self._pending_downloads.add(item_id)
        self.status_var.set(f"正在手动下载 {it.title} ({it.published_file_id})...")

        def worker() -> None:
            try:
                ok, msg = download_workshop_item_manual(
                    workshop_id=it.published_file_id,
                    target_mods_dir=target_mods,
                    downloader_kind=kind,
                    downloader_exe=exe,
                    steam_username=username,
                    staging_dir=self.data_dir / "downloads",
                )
            except Exception as exc:
                ok, msg = False, f"下载失败: {exc}"
            def finish() -> None:
                self._pending_downloads.discard(item_id)
                self.refresh_all()
                self.status_var.set(msg)
                if ok:
                    messagebox.showinfo("下载完成", msg)
                else:
                    messagebox.showerror("下载失败", msg)

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def on_open_in_steam(self) -> None:
        it = self._selected_workshop_item()
        if it:
            open_workshop_page_in_steam(it.published_file_id)

    def on_open_in_browser(self) -> None:
        it = self._selected_workshop_item()
        if it:
            webbrowser.open(it.url)

    # ------------------------------------------------------------------ Auxiliary Dialogs
    def _dialog(self, title: str, size: tuple[int, int]) -> tuple[tk.Toplevel, ttk.Frame]:
        win = tk.Toplevel(self.root)
        win.transient(self.root)
        win.title(title)
        win.configure(background=BG)
        win.geometry(f"{size[0]}x{size[1]}")
        win.minsize(*size)
        win.bind("<Escape>", lambda _e: win.destroy())
        outer, body = panel(win, padding=14)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ttk.Label(body, text=title, style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 12))
        return win, body

    def on_share_code_dialog(self) -> None:
        win, frm = self._dialog("方案分享码", (560, 340))
        ttk.Label(frm, text="分享码（PSMM1:...）", style="Muted.Surface.TLabel").pack(anchor="w")
        txt = tk.Text(frm, height=8, width=1, wrap=tk.WORD, **text_options())
        txt.configure(highlightbackground=BORDER, highlightcolor=ACCENT, highlightthickness=1)
        txt.pack(fill=tk.BOTH, expand=True, pady=(6, 12), ipady=2, ipadx=4)

        def do_export() -> None:
            ws_map = {
                mid: self.installed_mods[mid].workshop_id if mid in self.installed_mods else None
                for mid in self.active_order
            }
            code = self.profile_mgr.export_share_code(
                self.profile_var.get() or "当前方案", self.active_order, ws_map
            )
            txt.delete("1.0", tk.END)
            txt.insert("1.0", code)

        def do_import() -> None:
            try:
                payload = self.profile_mgr.decode_share_code(txt.get("1.0", tk.END))
                mods = [m["id"] for m in payload.get("mods", [])]
                missing = [m for m in mods if m not in self.installed_mods]
                self.active_order = mods
                self._render_local_lists()
                msg = f"已导入方案「{payload.get('name', '未命名')}」（共 {len(mods)} 个模组）。"
                if missing:
                    msg += f"\n以下 {len(missing)} 个模组尚未安装: {', '.join(missing[:10])}"
                messagebox.showinfo("导入完成", msg, parent=win)
            except Exception as exc:
                messagebox.showerror("解析失败", str(exc), parent=win)

        row = ttk.Frame(frm, style="Surface.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="导出当前模组列表", command=do_export).pack(side=tk.LEFT)
        ttk.Button(row, text="导入到加载顺序", style="Accent.TButton", command=do_import).pack(side=tk.RIGHT)

    def on_backups_dialog(self) -> None:
        backups = self.profile_mgr.list_backups()
        if not backups:
            messagebox.showinfo("备份列表", "暂无 SaveFile.es3 自动备份。")
            return
        win, frm = self._dialog("SaveFile.es3 备份恢复", (480, 320))
        backup_list = ttk.Frame(frm, style="Surface.TFrame")
        backup_list.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        lb = tk.Listbox(backup_list, font=(FONT, 10), background=SURFACE, foreground=TEXT,
                        selectbackground=SOFT, selectforeground=ACCENT, exportselection=False,
                        highlightbackground=BORDER, highlightcolor=ACCENT, borderwidth=0, activestyle="none")
        scrollbar = ttk.Scrollbar(backup_list, orient=tk.VERTICAL, command=lb.yview)
        lb.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for b in backups:
            lb.insert(tk.END, b.name)

        def do_restore() -> None:
            sel = lb.curselection()
            if not sel:
                return
            if not messagebox.askyesno("确认恢复备份", "恢复会替换当前完整存档，并恢复备份中包含的模组配置。继续吗？", parent=win):
                return
            ok, msg = self.profile_mgr.restore_backup(backups[sel[0]], self.paths)
            if ok:
                self.refresh_all()
                messagebox.showinfo("恢复成功", msg, parent=win)
                win.destroy()
            else:
                messagebox.showerror("恢复失败", msg, parent=win)

        restore = ttk.Button(frm, text="恢复选中备份", style="Accent.TButton", command=do_restore, state=tk.DISABLED)
        restore.pack(anchor=tk.E)
        lb.bind("<<ListboxSelect>>", lambda _e: restore.configure(state=tk.NORMAL if lb.curselection() else tk.DISABLED))

    def on_settings_dialog(self) -> None:
        win, body = self._dialog("路径与下载器设置", (660, 400))
        frm = ttk.Frame(body, style="Surface.TFrame")
        frm.pack(fill=tk.BOTH, expand=True)
        frm.columnconfigure(1, weight=1)

        fields = [
            ("游戏目录 (含 Probably Stolen.exe):", tk.StringVar(value=self.settings.game_dir), True),
            ("工坊目录 (workshop/content/4348910):", tk.StringVar(value=self.settings.workshop_dir), True),
            ("存档配置目录 (LocalLow/...):", tk.StringVar(value=self.settings.save_dir), True),
            ("游戏版本（留空自动识别）:", tk.StringVar(value=self.settings.game_version), False),
            ("DepotDownloader / SteamCMD 路径:", tk.StringVar(value=self.settings.downloader_path), "file"),
            ("Steam 登录账号 (手动下载工坊用):", tk.StringVar(value=self.settings.steam_username), False),
            ("Steam Web API Key (可选):", tk.StringVar(value=self.settings.steam_api_key), False),
        ]

        for r, (label, var, browse) in enumerate(fields):
            ttk.Label(frm, text=label, style="Surface.TLabel").grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(frm, textvariable=var, width=20).grid(row=r, column=1, sticky="ew", padx=6, pady=4)
            if browse is True:
                ttk.Button(
                    frm,
                    text="浏览",
                    command=lambda v=var: v.set(filedialog.askdirectory(parent=win) or v.get()),
                ).grid(row=r, column=2, pady=4)
            elif browse == "file":
                ttk.Button(
                    frm,
                    text="浏览",
                    command=lambda v=var: v.set(filedialog.askopenfilename(parent=win) or v.get()),
                ).grid(row=r, column=2, pady=4)

        def do_save() -> None:
            self.settings.game_dir = fields[0][1].get().strip()
            self.settings.workshop_dir = fields[1][1].get().strip()
            self.settings.save_dir = fields[2][1].get().strip()
            self.settings.game_version = fields[3][1].get().strip()
            self.settings.game_version_manual = bool(self.settings.game_version)
            self.settings.downloader_path = fields[4][1].get().strip()
            self.settings.steam_username = fields[5][1].get().strip()
            self.settings.steam_api_key = fields[6][1].get().strip()
            self.settings.save(self.settings_path)
            self.refresh_all()
            win.destroy()

        ttk.Button(frm, text="保存设置", style="Accent.TButton", command=do_save).grid(
            row=len(fields), column=1, sticky="e", pady=12
        )

    def run(self) -> None:
        self.root.mainloop()
