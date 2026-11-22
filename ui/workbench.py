"""The desktop shell and its local mod workbench."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import FONT, MUTED, WARNING, panel, text_options


def build_workbench(app) -> None:
    shell = ttk.Frame(app.root, padding=12)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)

    rail = ttk.Frame(shell, style="Rail.TFrame", padding=(12, 14))
    rail.grid(row=0, column=0, sticky="ns", padx=(0, 12))
    rail_body = ttk.Frame(rail, style="RailBody.TFrame", width=156)
    rail_body.pack(fill=tk.BOTH, expand=True)
    rail_body.grid_columnconfigure(0, minsize=152, weight=1)
    rail_body.grid_rowconfigure(8, weight=1)
    brand = ttk.Frame(rail_body, style="RailBody.TFrame")
    brand.grid(row=0, column=0, sticky="ew", pady=(0, 23))
    ttk.Label(brand, text="▦", style="Badge.TLabel", font=(FONT, 16, "bold")).pack(side=tk.LEFT, padx=(0, 8))
    wordmark = ttk.Frame(brand, style="RailBody.TFrame")
    wordmark.pack(side=tk.LEFT)
    ttk.Label(wordmark, text="深空当铺", style="Brand.Rail.TLabel").pack(anchor="w")
    ttk.Label(wordmark, text="MOD 管理器", style="Caption.Rail.TLabel").pack(anchor="w")
    ttk.Label(rail_body, text="工作台", style="Section.Rail.TLabel").grid(
        row=1, column=0, sticky="w", padx=9, pady=(0, 7),
    )

    pages = {"local": ("▤", "本地模组"), "config": ("≡", "参数配置"), "workshop": ("◇", "创意工坊")}
    buttons = {}
    for row, (key, (icon, label)) in enumerate(pages.items(), start=2):
        button = ttk.Button(rail_body, text=f"{icon}   {label}", style="Rail.TButton",
                            command=lambda k=key: app._show_page(k))
        button.grid(row=row, column=0, sticky="ew", pady=(0, 3))
        buttons[key] = button

    ttk.Label(rail_body, text="管理工具", style="Section.Rail.TLabel").grid(
        row=5, column=0, sticky="w", padx=9, pady=(19, 7),
    )
    for row, (label, command) in zip((6, 7, 9), (
        ("↔   分享码", app.on_share_code_dialog),
        ("↶   备份恢复", app.on_backups_dialog),
        ("⚙   路径与设置", app.on_settings_dialog),
    )):
        ttk.Button(rail_body, text=label, command=command, style="Rail.TButton").grid(
            row=row, column=0, sticky="ew", pady=(0, 3),
        )

    content = ttk.Frame(shell)
    content.grid(row=0, column=1, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.rowconfigure(2, weight=1)

    top_outer, top = panel(content, padding=(12, 8))
    top_outer.grid(row=0, column=0, sticky="ew")
    top.columnconfigure(0, weight=1)
    app.page_path_var = tk.StringVar()
    ttk.Label(top, textvariable=app.page_path_var, style="Muted.Surface.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(top, text="预设", style="Surface.TLabel").grid(row=0, column=1, padx=(0, 6))
    app.profile_var = tk.StringVar()
    app.profile_combo = ttk.Combobox(top, textvariable=app.profile_var, state="readonly", width=14)
    app.profile_combo.grid(row=0, column=2, sticky="ew")
    ttk.Button(top, text="应用", style="Soft.TButton", command=app.on_apply_profile).grid(row=0, column=3, padx=(6, 0))
    menu_button = ttk.Menubutton(top, text="管理预设")
    profile_menu = tk.Menu(menu_button, tearoff=False)
    profile_menu.add_command(label="保存或更新预设", command=app.on_save_profile)
    profile_menu.add_command(label="删除预设", command=app.on_delete_profile)
    menu_button.configure(menu=profile_menu)
    menu_button.grid(row=0, column=4, padx=(4, 10))
    ttk.Button(top, text="▶  切换并启动", style="Accent.TButton", command=app.on_switch_and_launch).grid(row=0, column=5)

    heading = ttk.Frame(content)
    heading.grid(row=1, column=0, sticky="ew", pady=(10, 10))
    app.page_title_var = tk.StringVar()
    ttk.Label(heading, textvariable=app.page_title_var, style="Heading.TLabel").pack(side=tk.LEFT)
    app.path_badge_var = tk.StringVar()
    app.path_badge = ttk.Label(heading, textvariable=app.path_badge_var, style="Badge.TLabel")
    app.path_badge.pack(side=tk.RIGHT)

    stage = ttk.Frame(content)
    stage.grid(row=2, column=0, sticky="nsew")
    stage.rowconfigure(0, weight=1)
    stage.columnconfigure(0, weight=1)
    app.page_frames = {}
    for key in pages:
        app.page_frames[key] = page = ttk.Frame(stage)
        page.grid(row=0, column=0, sticky="nsew")
    app.tab_local, app.tab_config, app.tab_workshop = app.page_frames.values()
    app.page_navigation = {key: (label, buttons[key]) for key, (_, label) in pages.items()}

    bottom = ttk.Frame(content)
    bottom.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    app.status_var = tk.StringVar(value="就绪")
    ttk.Label(bottom, text="状态 /", style="Status.TLabel").pack(side=tk.LEFT, anchor="n", padx=(0, 6))
    status = ttk.Label(bottom, textvariable=app.status_var, style="Status.TLabel")
    status.pack(side=tk.LEFT, fill=tk.X, expand=True)
    bottom.bind("<Configure>", lambda e: status.configure(wraplength=max(1, e.width - 58)))


def build_local_page(app) -> None:
    metrics = ttk.Frame(app.tab_local)
    metrics.pack(fill=tk.X, pady=(0, 10))
    app.local_metrics = [tk.StringVar(value="0") for _ in range(3)]
    for i, (label, value) in enumerate(zip(("已安装", "待加载", "缺失模组"), app.local_metrics)):
        metrics.columnconfigure(i, weight=1, uniform="metrics")
        card, body = panel(metrics, padding=(13, 7))
        card.grid(row=0, column=i, sticky="ew", padx=(0, 9) if i < 2 else 0)
        ttk.Label(body, text=label, style="Muted.Surface.TLabel").pack(anchor="w")
        metric = ttk.Label(body, textvariable=value, style="Metric.TLabel")
        metric.pack(anchor="w")
        if i == 2:
            app.missing_label = metric

    toolbar = ttk.Frame(app.tab_local)
    toolbar.pack(fill=tk.X, pady=(0, 9))
    ttk.Label(toolbar, text="搜索模组").pack(side=tk.LEFT)
    app.local_search_var = tk.StringVar()
    app.local_search_entry = ttk.Entry(toolbar, textvariable=app.local_search_var, width=10)
    app.local_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(7, 8))
    app.local_search_var.trace_add("write", lambda *_: app._render_local_lists())
    ttk.Button(toolbar, text="↻ 刷新", command=app.refresh_all).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(toolbar, text="依赖排序", command=app.on_auto_sort).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(toolbar, text="诊断", command=app.on_run_diagnostics).pack(side=tk.LEFT, padx=(0, 8))
    app.order_state_var = tk.StringVar()
    ttk.Label(toolbar, textvariable=app.order_state_var, width=8, anchor="center", style="Status.TLabel").pack(side=tk.LEFT)
    ttk.Button(toolbar, text="保存加载顺序", style="Accent.TButton", command=app.on_save_order).pack(side=tk.RIGHT)

    grid = ttk.Frame(app.tab_local)
    grid.pack(fill=tk.BOTH, expand=True)
    grid.rowconfigure(0, weight=1)
    grid.columnconfigure(0, minsize=218, weight=1, uniform="tables")
    grid.columnconfigure(1, minsize=218, weight=1, uniform="tables")
    grid.columnconfigure(2, minsize=183, weight=0)
    app.local_empty_labels = []

    for i, (attr, label) in enumerate((("inactive_tree", "未启用模组"), ("active_tree", "加载顺序"))):
        card, body = panel(grid, padding=(8, 8))
        card.grid(row=0, column=i, sticky="nsew", padx=(0, 8))
        ttk.Label(body, text=label, style="PanelTitle.TLabel").pack(anchor="w", padx=3, pady=(0, 7))
        table = ttk.Frame(body, style="Surface.TFrame")
        table.pack(fill=tk.BOTH, expand=True)
        tree = _mod_table(table)
        setattr(app, attr, tree)
        tree.bind("<<TreeviewSelect>>", lambda _, t=tree: app._on_select_mod(t))
        empty = ttk.Label(table, style="Muted.Surface.TLabel", padding=6, anchor="center")
        app.local_empty_labels.append(empty)

    card, detail = panel(grid, padding=10)
    card.grid(row=0, column=2, sticky="nsew")
    ttk.Label(detail, text="模组详情", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 7))
    ttk.Separator(detail).pack(fill=tk.X)
    detail_body = ttk.Frame(detail, style="Surface.TFrame")
    detail_body.pack(fill=tk.BOTH, expand=True, pady=(6, 4))
    app.mod_detail_text = tk.Text(detail_body, width=17, height=1, wrap=tk.WORD, state=tk.DISABLED, **text_options())
    scroll = ttk.Scrollbar(detail_body, orient=tk.VERTICAL, command=app.mod_detail_text.yview)
    app.mod_detail_text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app.mod_detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    app.mod_detail_text.tag_configure("title", font=(FONT, 12, "bold"), spacing3=7)
    app.mod_detail_text.tag_configure("caption", foreground=MUTED, font=(FONT, 9))
    app.mod_detail_text.tag_configure("warning", foreground=WARNING)
    ttk.Separator(detail).pack(fill=tk.X, pady=(2, 7))
    order_tools = ttk.Frame(detail, style="Surface.TFrame")
    order_tools.pack(fill=tk.X)
    order_tools.columnconfigure((0, 1), weight=1, uniform="order")
    up = ttk.Button(order_tools, text="↑ 上移", command=lambda: app.on_move_active(-1))
    down = ttk.Button(order_tools, text="↓ 下移", command=lambda: app.on_move_active(1))
    disable = ttk.Button(order_tools, text="← 停用所选", command=app.on_disable_selected)
    up.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    down.grid(row=0, column=1, sticky="ew", padx=(3, 0))
    disable.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    app.order_buttons = up, down, disable


def _mod_table(parent: ttk.Frame) -> ttk.Treeview:
    cols = ("name", "source", "compat")
    tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse", height=4)
    tree.heading("name", text="模组名称")
    tree.heading("source", text="来源")
    tree.heading("compat", text="状态")
    tree.column("name", width=102, minwidth=56)
    tree.column("source", width=53, minwidth=53, stretch=False, anchor=tk.CENTER)
    tree.column("compat", width=76, minwidth=76, stretch=False, anchor=tk.CENTER)
    ysb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=ysb.set)
    ysb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tree.tag_configure("warning", foreground=WARNING)
    return tree
