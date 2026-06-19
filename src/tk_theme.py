from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import ttk


COLORS: dict[str, str] = {
    "app_bg": "#eef2f7",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#cbd5e1",
    "text": "#0f172a",
    "muted": "#475569",
    "subtle": "#64748b",
    "primary": "#1d4ed8",
    "primary_dark": "#1e3a8a",
    "success": "#166534",
    "warning": "#92400e",
    "warning_bg": "#fffbeb",
    "danger": "#991b1b",
    "danger_bg": "#fef2f2",
    "focus": "#2563eb",
}

SPACING: dict[str, int] = {
    "xs": 4,
    "sm": 6,
    "md": 10,
    "lg": 14,
    "xl": 20,
}


def font_tuple(size: int, *, delta: int = 0, weight: str = "normal") -> tuple[str, int] | tuple[str, int, str]:
    resolved_size = max(8, int(size) + int(delta))
    if weight == "normal":
        return ("TkDefaultFont", resolved_size)
    return ("TkDefaultFont", resolved_size, weight)


def apply_professional_ops_theme(root: tk.Misc, *, font_size: int = 10) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=COLORS["app_bg"])
    base_font = font_tuple(font_size)
    heading_font = font_tuple(font_size, delta=1, weight="bold")

    style.configure(".", font=base_font)
    style.configure("TFrame", background=COLORS["app_bg"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["app_bg"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", background=COLORS["app_bg"], foreground=COLORS["muted"])
    style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure("SurfaceMuted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
    style.configure("Heading.TLabel", background=COLORS["app_bg"], foreground=COLORS["text"], font=heading_font)
    style.configure("SurfaceHeading.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=heading_font)

    style.configure("TLabelframe", background=COLORS["surface"], borderwidth=1, relief="solid")
    style.configure(
        "TLabelframe.Label",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=heading_font,
    )
    style.configure("TButton", padding=(10, 6))
    style.configure("Primary.TButton", padding=(12, 7), font=heading_font)
    style.configure("Secondary.TButton", padding=(10, 6))
    style.configure("Danger.TButton", padding=(10, 6))
    style.configure("TEntry", padding=4)
    style.configure("TCombobox", padding=4)
    style.configure("TRadiobutton", background=COLORS["app_bg"], foreground=COLORS["text"])
    style.configure("TCheckbutton", background=COLORS["app_bg"], foreground=COLORS["text"])
    style.configure("Horizontal.TProgressbar", troughcolor=COLORS["surface_alt"], background=COLORS["primary"])
    style.configure("Treeview", rowheight=max(24, int(font_size) + 14), background=COLORS["surface"], fieldbackground=COLORS["surface"])
    style.configure("Treeview.Heading", font=heading_font, background=COLORS["surface_alt"], foreground=COLORS["text"])
    style.map(
        "TButton",
        focuscolor=[("focus", COLORS["focus"])],
        bordercolor=[("focus", COLORS["focus"])],
    )
    style.map("Treeview", background=[("selected", COLORS["primary"])], foreground=[("selected", "#ffffff")])
    return style


def configure_text_widget(widget: tk.Text, *, font_size: int = 10, danger: bool = False) -> None:
    widget.configure(
        bg=COLORS["danger_bg"] if danger else COLORS["surface_alt"],
        fg=COLORS["text"],
        insertbackground=COLORS["text"],
        relief="solid",
        bd=1,
        highlightthickness=2,
        highlightcolor=COLORS["focus"],
        highlightbackground=COLORS["border"],
        padx=12,
        pady=10,
        font=font_tuple(font_size),
    )


def configure_plain_button(button: tk.Button, *, role: str = "secondary", font_size: int = 10, **kwargs: Any) -> None:
    palette = {
        "primary": (COLORS["primary"], "#ffffff"),
        "secondary": (COLORS["surface"], COLORS["text"]),
        "warning": (COLORS["warning_bg"], COLORS["warning"]),
        "danger": (COLORS["danger_bg"], COLORS["danger"]),
    }
    bg, fg = palette.get(role, palette["secondary"])
    button.configure(
        bg=bg,
        fg=fg,
        activebackground=bg,
        activeforeground=fg,
        bd=1,
        relief="solid",
        highlightthickness=2,
        highlightcolor=COLORS["focus"],
        highlightbackground=COLORS["border"],
        font=font_tuple(font_size, weight="bold" if role == "primary" else "normal"),
        **kwargs,
    )
