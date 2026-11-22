"""Workshop layout and presentation state for the Tkinter app."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Iterable

from .theme import BORDER, FONT, MUTED, TEXT, panel, text_options

if TYPE_CHECKING:
    from core.workshop import WorkshopItem

    from .app import ModManagerApp


def build_workshop_page(app: ModManagerApp, filter_tags: Iterable[tuple[str, str]]) -> None:
    """Build the filters, results table and detail panel in app.tab_workshop."""
    _build_filters(app, filter_tags)

    body = ttk.Frame(app.tab_workshop)
    body.pack(fill=tk.BOTH, expand=True)
    body.columnconfigure(0, weight=3)
    body.columnconfigure(1, weight=2)
    body.rowconfigure(0, weight=1)

    results_outer, results = panel(body, padding=10)
    results_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    _build_results(app, results)

    details_outer, details = panel(body, padding=10)
    details_outer.grid(row=0, column=1, sticky="nsew")
    _build_details(app, details)
    render_workshop_detail(app, None)


def _build_filters(app: ModManagerApp, filter_tags: Iterable[tuple[str, str]]) -> None:
    outer, inner = panel(app.tab_workshop, padding=8)
    outer.pack(fill=tk.X, pady=(0, 10))
    inner.columnconfigure(0, weight=1)

    criteria = ttk.Frame(inner, style="Surface.TFrame")
    criteria.grid(row=0, column=0, sticky="ew")
    criteria.columnconfigure(3, weight=1)

    labels = [label for _, label in filter_tags]
    app.ws_cat_var = tk.StringVar(master=inner, value=labels[0] if labels else "")
    app.ws_search_var = tk.StringVar(master=inner)

    ttk.Label(criteria, text="分类", style="Surface.TLabel").grid(row=0, column=0, padx=(0, 6))
    app.ws_cat_combo = ttk.Combobox(
        criteria, textvariable=app.ws_cat_var, values=labels, state="readonly", width=13
    )
    app.ws_cat_combo.grid(row=0, column=1, padx=(0, 12))
    ttk.Label(criteria, text="关键词", style="Surface.TLabel").grid(row=0, column=2, padx=(0, 6))
    search = ttk.Entry(criteria, textvariable=app.ws_search_var, width=12)
    search.grid(row=0, column=3, sticky="ew")
    search.bind("<Return>", lambda _event: app.on_fetch_workshop())

    actions = ttk.Frame(inner, style="Surface.TFrame")
    actions.grid(row=0, column=1, sticky="e", padx=(10, 0))
    ttk.Button(actions, text="搜索工坊", style="Accent.TButton", command=app.on_fetch_workshop).pack(
        side=tk.LEFT
    )
    ttk.Button(actions, text="按 ID 查询", style="Soft.TButton", command=app.on_lookup_workshop_ids).pack(
        side=tk.LEFT, padx=(6, 0)
    )

    wrapped = False

    def reflow(event: tk.Event) -> None:
        nonlocal wrapped
        needed = criteria.winfo_reqwidth() + actions.winfo_reqwidth() + 10
        wrap = event.width < needed
        if wrap == wrapped:
            return
        wrapped = wrap
        criteria.grid_configure(columnspan=2 if wrap else 1)
        if wrap:
            actions.grid_configure(row=1, column=0, columnspan=2, padx=0, pady=(6, 0))
        else:
            actions.grid_configure(row=0, column=1, columnspan=1, padx=(10, 0), pady=0)

    inner.bind("<Configure>", reflow, add="+")


def _build_results(app: ModManagerApp, parent: ttk.Frame) -> None:
    header = ttk.Frame(parent, style="Surface.TFrame")
    header.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(header, text="工坊结果", style="PanelTitle.TLabel").pack(side=tk.LEFT)
    app.ws_result_count_var = tk.StringVar(master=parent, value="待搜索")
    ttk.Label(header, textvariable=app.ws_result_count_var, style="Muted.Surface.TLabel").pack(side=tk.RIGHT)

    table = ttk.Frame(parent, style="Surface.TFrame")
    table.pack(fill=tk.BOTH, expand=True)
    table.columnconfigure(0, weight=1)
    table.rowconfigure(0, weight=1)
    columns = ("title", "tags", "subs")
    app.ws_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", height=10)
    app.ws_tree.heading("title", text="模组名称", anchor=tk.W)
    app.ws_tree.heading("tags", text="分类", anchor=tk.W)
    app.ws_tree.heading("subs", text="订阅数", anchor=tk.E)
    app.ws_tree.column("title", width=196, minwidth=100)
    app.ws_tree.column("tags", width=115, minwidth=60)
    app.ws_tree.column("subs", width=68, minwidth=68, stretch=False, anchor=tk.E)
    scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=app.ws_tree.yview)
    app.ws_tree.configure(yscrollcommand=scroll.set)
    app.ws_tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    app.ws_tree.bind("<<TreeviewSelect>>", lambda _event: app._on_select_workshop_item())

    app.ws_results_empty = ttk.Label(app.ws_tree, text="暂无工坊结果", style="Muted.Surface.TLabel")
    app.ws_results_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)


def _build_details(app: ModManagerApp, parent: ttk.Frame) -> None:
    ttk.Label(parent, text="模组详情", style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))

    footer = ttk.Frame(parent, style="Surface.TFrame")
    footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
    footer.columnconfigure(0, weight=1, uniform="workshop_link")
    footer.columnconfigure(1, weight=1, uniform="workshop_link")
    tk.Frame(footer, height=1, background=BORDER).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    download = ttk.Button(
        footer, text="↓ 下载到 Mods/", style="Accent.TButton", command=app.on_manual_download_workshop
    )
    download.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
    steam = ttk.Button(footer, text="Steam 打开", style="Soft.TButton", command=app.on_open_in_steam)
    steam.grid(row=2, column=0, sticky="ew", padx=(0, 3))
    browser = ttk.Button(footer, text="浏览器查看", style="Soft.TButton", command=app.on_open_in_browser)
    browser.grid(row=2, column=1, sticky="ew", padx=(3, 0))
    app.ws_item_buttons = (download, steam, browser)

    detail = ttk.Frame(parent, style="Surface.TFrame")
    detail.pack(fill=tk.BOTH, expand=True)
    options = text_options()
    options.update(width=25, height=8, wrap=tk.WORD, state=tk.DISABLED)
    app.ws_detail_text = tk.Text(detail, **options)
    scroll = ttk.Scrollbar(detail, orient=tk.VERTICAL, command=app.ws_detail_text.yview)
    app.ws_detail_text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app.ws_detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    app.ws_detail_text.tag_configure("title", font=(FONT, 13, "bold"), foreground=TEXT, spacing3=5)
    app.ws_detail_text.tag_configure("meta", foreground=MUTED, spacing2=2)
    app.ws_detail_text.tag_configure("section", font=(FONT, 10, "bold"), foreground=TEXT, spacing1=9, spacing3=4)
    app.ws_detail_text.tag_configure("description", foreground=TEXT, spacing2=3)


def update_workshop_results(app: ModManagerApp) -> None:
    """Call after replacing all tree rows; the replacement starts unselected."""
    count = len(app.ws_tree.get_children())
    app.ws_result_count_var.set(f"{count} 个模组")
    if count:
        app.ws_results_empty.place_forget()
    else:
        app.ws_results_empty.configure(text="未找到匹配的模组")
        app.ws_results_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
    selected = app.ws_tree.selection()
    if selected:
        app.ws_tree.selection_remove(*selected)
    render_workshop_detail(app, None)


def render_workshop_detail(app: ModManagerApp, item: WorkshopItem | None, description: str = "") -> None:
    """Show the active item and its already-resolved description, or clear the selection state."""
    detail = app.ws_detail_text
    detail.configure(state=tk.NORMAL)
    try:
        detail.delete("1.0", tk.END)
        if item is None:
            detail.insert(tk.END, "未选中模组", ("meta",))
        else:
            detail.insert(tk.END, f"{item.title}\n", ("title",))
            detail.insert(
                tk.END,
                f"工坊 ID：{item.published_file_id}  ·  {item.file_size // 1024:,} KB\n"
                f"分类：{', '.join(item.zh_tags) or '无'}\n",
                ("meta",),
            )
            detail.insert(tk.END, "简介\n", ("section",))
            detail.insert(tk.END, description or "暂无简介", ("description",))
            detail.insert(tk.END, "\n\n" + item.url, ("meta",))
        detail.yview_moveto(0)
    finally:
        detail.configure(state=tk.DISABLED)
    for button in app.ws_item_buttons:
        button.state(["disabled" if item is None else "!disabled"])
