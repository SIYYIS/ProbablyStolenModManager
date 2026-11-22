"""Layout and view switching for the mod configuration editor."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from .theme import ACCENT, BG, BORDER, FONT, SOFT, SURFACE, TEXT, panel, text_options

if TYPE_CHECKING:
    from .app import ModManagerApp

__all__ = ["build_config_page", "reset_config_editor", "show_config_editor"]

_EMPTY_MESSAGE = "尚未打开配置文件"
_MODE_LABELS = {"empty": "未打开", "form": "参数表单", "raw": "原始 JSON"}


def _scrollable(host: ttk.Frame, widget: tk.Canvas | tk.Listbox | tk.Text) -> None:
    host.columnconfigure(0, weight=1)
    host.rowconfigure(0, weight=1)
    vertical = ttk.Scrollbar(host, orient=tk.VERTICAL, command=widget.yview)
    horizontal = ttk.Scrollbar(host, orient=tk.HORIZONTAL, command=widget.xview)
    widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    widget.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns", padx=(4, 0))
    horizontal.grid(row=1, column=0, sticky="ew", pady=(4, 0))


def _divider(parent: ttk.Frame) -> None:
    tk.Frame(parent, background=BORDER, height=1, borderwidth=0).pack(fill=tk.X, pady=(8, 10))


class _ConfigEditor:
    def __init__(self, parent: ttk.Frame, mode_label: tk.StringVar, save: ttk.Button) -> None:
        self.mode_label = mode_label
        self.save = save
        self.empty_message = tk.StringVar(master=parent, value=_EMPTY_MESSAGE)

        body = ttk.Frame(parent, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.views = {
            mode: ttk.Frame(body, style="Surface.TFrame") for mode in _MODE_LABELS
        }
        self.active: str | None = None

        empty = self.views["empty"]
        empty_label = ttk.Label(
            empty, textvariable=self.empty_message, style="Muted.Surface.TLabel", justify=tk.CENTER,
        )
        empty_label.place(relx=0.5, rely=0.45, anchor=tk.CENTER)
        empty.bind("<Configure>", lambda e: empty_label.configure(wraplength=max(1, e.width - 24)))

        form = self.views["form"]
        self.canvas = tk.Canvas(
            form, background=SURFACE, highlightthickness=0, borderwidth=0,
            width=1, height=1, xscrollincrement=20, yscrollincrement=20,
        )
        self.form_frame = ttk.Frame(self.canvas, style="Surface.TFrame")
        self.form_frame.columnconfigure(1, weight=1)
        self.form_window = self.canvas.create_window((0, 0), window=self.form_frame, anchor="nw")
        self.form_width = 0
        self.canvas.bind("<Configure>", self._size_form)
        self.canvas.bind("<Map>", self._size_form)
        self.form_frame.bind("<Configure>", self._size_form)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(sequence, self._scroll_form)
        _scrollable(form, self.canvas)

        raw = self.views["raw"]
        raw_options = {**text_options(), "wrap": tk.NONE, "undo": True, "width": 1, "height": 1}
        self.raw_text = tk.Text(raw, **raw_options)
        _scrollable(raw, self.raw_text)

    def _size_form(self, _event: tk.Event) -> None:
        width = max(self.canvas.winfo_width(), self.form_frame.winfo_reqwidth())
        if width != self.form_width:
            self.canvas.itemconfigure(self.form_window, width=width)
            self.form_width = width
        self.canvas.configure(scrollregion=self.canvas.bbox(self.form_window))

    def _scroll_form(self, event: tk.Event) -> str:
        if event.num == 4 or event.num == 5:
            steps = -1 if event.num == 4 else 1
        elif event.delta:
            steps = max(1, abs(event.delta) // 120) * (-1 if event.delta > 0 else 1)
        else:
            return "break"
        self.canvas.yview_scroll(steps * 3, "units")
        return "break"

    def _bind_form_wheel(self) -> None:
        # The canvas tag also handles wheel events above dynamically built form fields.
        canvas_tag = str(self.canvas)
        pending = [self.form_frame]
        while pending:
            widget = pending.pop()
            tags = widget.bindtags()
            if canvas_tag not in tags:
                widget.bindtags((tags[0], canvas_tag, *tags[1:]))
            pending.extend(widget.winfo_children())

    def show(self, mode: str) -> None:
        if mode not in self.views:
            raise ValueError(f"Unknown config editor mode: {mode!r}")
        if self.active != mode:
            if self.active is not None:
                self.views[self.active].grid_remove()
            self.views[mode].grid(row=0, column=0, sticky="nsew")
            self.active = mode
        self.mode_label.set(_MODE_LABELS[mode])
        self.save.state(["disabled" if mode == "empty" else "!disabled"])
        if mode == "form":
            self._bind_form_wheel()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        elif mode == "raw":
            self.raw_text.edit_reset()
            self.raw_text.edit_modified(False)
            self.raw_text.mark_set(tk.INSERT, "1.0")
            self.raw_text.xview_moveto(0)
            self.raw_text.yview_moveto(0)


def build_config_page(app: ModManagerApp) -> None:
    """Build once in app.tab_config, after the shared theme has been configured."""
    page = tk.Frame(app.tab_config, background=BG)
    page.pack(fill=tk.BOTH, expand=True)
    page.columnconfigure(0, minsize=210)
    page.columnconfigure(1, weight=1)
    page.rowconfigure(0, weight=1)

    outer, left = panel(page, padding=12)
    outer.grid(row=0, column=0, sticky="nsew")
    ttk.Label(left, text="可配置模组", style="PanelTitle.TLabel").pack(anchor=tk.W)
    _divider(left)
    list_host = ttk.Frame(left, style="Surface.TFrame")
    list_host.pack(fill=tk.BOTH, expand=True)
    app.cfg_mod_list = tk.Listbox(
        list_host, selectmode=tk.BROWSE, exportselection=False, activestyle="none",
        background=SURFACE, foreground=TEXT, selectbackground=SOFT, selectforeground=ACCENT,
        highlightbackground=BORDER, highlightcolor=ACCENT, highlightthickness=1,
        borderwidth=0, font=(FONT, 10), width=1, height=1,
    )
    app.cfg_mod_list.bind("<<ListboxSelect>>", lambda _event: app._on_select_cfg_mod())
    _scrollable(list_host, app.cfg_mod_list)

    outer, right = panel(page, padding=12)
    outer.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
    heading = ttk.Frame(right, style="Surface.TFrame")
    heading.pack(fill=tk.X)
    ttk.Label(heading, text="配置编辑器", style="PanelTitle.TLabel").pack(side=tk.LEFT)
    mode_label = tk.StringVar(master=heading)
    ttk.Label(heading, textvariable=mode_label, style="Muted.Surface.TLabel").pack(side=tk.RIGHT)

    toolbar = ttk.Frame(right, style="Surface.TFrame")
    toolbar.pack(fill=tk.X, pady=(10, 0))
    toolbar.columnconfigure(1, weight=1)
    ttk.Label(toolbar, text="配置文件", style="Surface.TLabel").grid(row=0, column=0, sticky=tk.W)
    app.cfg_file_var = tk.StringVar(master=toolbar)
    app.cfg_file_combo = ttk.Combobox(toolbar, textvariable=app.cfg_file_var, state="readonly", width=1)
    app.cfg_file_combo.grid(row=0, column=1, sticky="ew", padx=(8, 10))
    app.cfg_file_combo.bind("<<ComboboxSelected>>", lambda _event: app._load_selected_cfg_file())
    save = ttk.Button(toolbar, text="保存参数修改", style="Accent.TButton", command=app.on_save_config)
    save.grid(row=0, column=2, sticky=tk.E)
    _divider(right)

    editor = app._cfg_editor = _ConfigEditor(right, mode_label, save)
    app.cfg_canvas = editor.canvas
    app.cfg_form_frame = editor.form_frame
    app.cfg_raw_text = editor.raw_text
    editor.show("empty")


def reset_config_editor(app: ModManagerApp, *, empty_message: str = _EMPTY_MESSAGE) -> None:
    """Clear editor widgets, not the selected mod/file or app-owned file state."""
    editor = app._cfg_editor
    editor.empty_message.set(empty_message)
    editor.show("empty")
    for child in app.cfg_form_frame.winfo_children():
        child.destroy()
    app._cfg_widgets.clear()
    app.cfg_raw_text.delete("1.0", tk.END)
    app.cfg_raw_text.edit_reset()
    app.cfg_raw_text.edit_modified(False)


def show_config_editor(app: ModManagerApp, mode: str) -> None:
    """Show empty/form/raw. Call after creating fields or inserting a new raw file."""
    app._cfg_editor.show(mode)
