"""Shared desktop palette and controls for the mod workbench."""

from __future__ import annotations

import sys
import tkinter as tk
from math import sqrt
from tkinter import ttk

BG = "#D3E9FB"
SURFACE = "#FBFDFF"
SIDEBAR = "#EBF5FD"
TEXT = "#192C41"
MUTED = "#526780"
BORDER = "#CFDFEF"
ACCENT = "#1463C1"
SOFT = "#E5F1FF"
WARNING = "#995012"
FONT = "Microsoft YaHei UI" if sys.platform == "win32" else "TkDefaultFont"


def _rounded_tile(root: tk.Tk, fill: str, border: str, radius: int = 8) -> tk.PhotoImage:
    # A wide center avoids thousands of tiny tile draws on Windows.
    size = 64
    tile = tk.PhotoImage(master=root, width=size, height=size)
    for inset, color in ((0, border), (1, fill)):
        r = radius - inset
        for y in range(inset, size - inset):
            distance = max(0, radius - min(y, size - 1 - y))
            x = radius - int(sqrt(r * r - distance * distance))
            tile.put(color, to=(x, y, size - x, y + 1))
    return tile


def configure_theme(root: tk.Tk) -> None:
    root.configure(background=BG)
    root.option_add("*Menu.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 10), foreground=TEXT, background=BG)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Surface.TLabel", background=SURFACE)
    style.configure("Muted.Surface.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 9))
    style.configure("PanelTitle.TLabel", background=SURFACE, font=(FONT, 11, "bold"))
    style.configure("Metric.TLabel", background=SURFACE, font=(FONT, 21, "bold"))
    style.configure("MetricAlert.TLabel", background=SURFACE, foreground=WARNING, font=(FONT, 21, "bold"))
    style.configure("Heading.TLabel", font=(FONT, 22, "bold"))
    style.configure("Status.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Badge.TLabel", background=SOFT, foreground=ACCENT, font=(FONT, 9), padding=(9, 4))
    style.configure("Alert.Badge.TLabel", background="#FFF0D9", foreground=WARNING)

    # Stretch a small corner tile instead of recreating a canvas at each resize.
    images = []
    for name, color, outline in (("Panel", SURFACE, BORDER), ("Rail", SIDEBAR, "#F8FCFF")):
        image = _rounded_tile(root, color, outline)
        images.append(image)
        style.element_create(f"{name}.border", "image", image, border=8, sticky="nsew")
        style.layout(f"{name}.TFrame", [(f"{name}.border", {"sticky": "nsew"})])
    root._workbench_tiles = images
    style.configure("RailBody.TFrame", background=SIDEBAR)
    style.configure("Rail.TLabel", background=SIDEBAR)
    style.configure("Brand.Rail.TLabel", background=SIDEBAR, font=(FONT, 14, "bold"))
    style.configure("Section.Rail.TLabel", background=SIDEBAR, foreground=MUTED, font=(FONT, 9, "bold"))
    style.configure("Caption.Rail.TLabel", background=SIDEBAR, foreground=MUTED, font=(FONT, 9))

    for name, fill, ink, outline, hover, pressed in (
        ("TButton", SURFACE, TEXT, BORDER, SOFT, "#D9E9FA"),
        ("Soft.TButton", SOFT, "#164C89", "#D7E7F9", "#D8EAFF", "#CEE4FF"),
        ("Accent.TButton", ACCENT, "#FFFFFF", ACCENT, "#1056AA", "#0D468D"),
        ("Rail.TButton", SIDEBAR, TEXT, SIDEBAR, SOFT, "#CFE5FB"),
        ("Active.Rail.TButton", "#D5EAFE", "#105AAE", "#D5EAFE", "#CCE5FD", "#BFDDFB"),
    ):
        rail = "Rail" in name
        style.configure(
            name, background=fill, foreground=ink, bordercolor=outline,
            lightcolor=fill, darkcolor=fill, relief="flat", borderwidth=1,
            anchor="w" if rail else "center", padding=(10, 7) if rail else (10, 5),
            font=(FONT, 10 if rail else 9, "bold" if name.startswith(("Accent", "Active")) else "normal"),
            focuscolor=ink,
        )
        style.map(name, foreground=[("disabled", "#5F7287")], bordercolor=[("focus", "#6CA4DB")])
        for key in ("background", "lightcolor", "darkcolor"):
            style.map(name, **{key: [("disabled", "#EAF0F6"), ("pressed", pressed), ("active", hover)]})

    for name in ("TEntry", "TCombobox"):
        style.configure(name, padding=(8, 5), background=SURFACE, fieldbackground=SURFACE,
                        bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED)
        style.map(name, fieldbackground=[("readonly", SURFACE)],
                  bordercolor=[("focus", ACCENT)], selectbackground=[("readonly", SOFT)],
                  selectforeground=[("readonly", TEXT)])
    style.configure("Surface.TCheckbutton", background=SURFACE)
    style.map("Surface.TCheckbutton", background=[("active", SURFACE)])
    style.configure("TSeparator", background=BORDER)
    style.configure("TMenubutton", background=SURFACE, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, arrowsize=8,
                    relief="flat", padding=(8, 5), font=(FONT, 9))
    style.map("TMenubutton", background=[("active", SOFT)], bordercolor=[("focus", ACCENT)])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, borderwidth=0,
                    rowheight=max(28, round(21 * float(root.tk.call("tk", "scaling")))))
    style.configure("Treeview.Heading", background="#EFF6FD", bordercolor="#EFF6FD",
                    lightcolor="#EFF6FD", darkcolor="#EFF6FD", relief="flat",
                    padding=(6, 7), font=(FONT, 9, "bold"), foreground=MUTED)
    style.map("Treeview.Heading", background=[("active", "#EFF6FD")])
    style.map("Treeview", background=[("selected", "#DAECFF")], foreground=[("selected", "#104F99")])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", width=11, arrowsize=10, gripcount=0,
                        background="#C8DAEC", troughcolor="#F0F6FC", bordercolor=SURFACE,
                        lightcolor="#C8DAEC", darkcolor="#C8DAEC", arrowcolor=MUTED)


def panel(parent: tk.Misc, padding: int | tuple[int, ...] = 12) -> tuple[ttk.Frame, ttk.Frame]:
    outer = ttk.Frame(parent, style="Panel.TFrame", padding=padding)
    inner = ttk.Frame(outer, style="Surface.TFrame")
    inner.pack(fill=tk.BOTH, expand=True)
    return outer, inner


def text_options() -> dict[str, object]:
    return dict(background=SURFACE, foreground=TEXT, insertbackground=TEXT,
                selectbackground=SOFT, selectforeground=ACCENT, font=(FONT, 10),
                borderwidth=0, highlightthickness=0, padx=0, pady=4, spacing1=2, spacing3=3)
