from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from onboarding_scroll_helpers import bind_canvas_mousewheel, build_scrollable_canvas_area


@dataclass(slots=True)
class ScrollableModalContainer:
    dialog: tk.Toplevel
    canvas: tk.Canvas
    interior: ttk.Frame
    button_bar: ttk.Frame
    scrollbar: ttk.Scrollbar


def build_scrollable_modal_container(
    parent: tk.Misc,
    *,
    title: str,
    body_padding: int | tuple[int, int, int, int] = 10,
    button_padding: tuple[int, int, int, int] = (10, 0, 10, 10),
) -> ScrollableModalContainer:
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(0, weight=1)

    scrollable_area = build_scrollable_canvas_area(
        dialog,
        interior_padding=body_padding,
        canvas_kwargs={"takefocus": 0},
    )
    shell = scrollable_area.shell
    shell.grid(row=0, column=0, sticky="nsew")
    canvas = scrollable_area.canvas
    scrollbar = scrollable_area.scrollbar
    interior = scrollable_area.interior
    window_id = scrollable_area.window_id

    button_bar = ttk.Frame(shell, padding=button_padding)
    button_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
    button_bar.columnconfigure(0, weight=1)

    def _update_scroll_region(_event: tk.Event | None = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _match_interior_width(event: tk.Event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    interior.bind("<Configure>", _update_scroll_region)
    canvas.bind("<Configure>", _match_interior_width)
    bind_canvas_mousewheel(canvas, activate_widgets=(canvas, interior))

    _update_scroll_region()
    return ScrollableModalContainer(
        dialog=dialog,
        canvas=canvas,
        interior=interior,
        button_bar=button_bar,
        scrollbar=scrollbar,
    )
