from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
import tkinter as tk
from tkinter import ttk


@dataclass(slots=True)
class ScrollableCanvasArea:
    shell: ttk.Frame
    canvas: tk.Canvas
    interior: ttk.Frame
    scrollbar: ttk.Scrollbar
    window_id: int


def build_scrollable_canvas_area(
    parent: tk.Misc,
    *,
    interior_padding: int | tuple[int, int, int, int] = 0,
    canvas_kwargs: dict[str, object] | None = None,
) -> ScrollableCanvasArea:
    shell = ttk.Frame(parent)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(0, weight=1)

    resolved_canvas_kwargs = {"highlightthickness": 0, "borderwidth": 0}
    if canvas_kwargs:
        resolved_canvas_kwargs.update(canvas_kwargs)

    canvas = tk.Canvas(shell, **resolved_canvas_kwargs)
    scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    interior = ttk.Frame(canvas, padding=interior_padding)
    window_id = canvas.create_window((0, 0), window=interior, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    interior.columnconfigure(0, weight=1)
    return ScrollableCanvasArea(
        shell=shell,
        canvas=canvas,
        interior=interior,
        scrollbar=scrollbar,
        window_id=window_id,
    )


def bind_canvas_mousewheel(
    canvas: tk.Canvas,
    *,
    activate_widgets: Sequence[tk.Misc],
    release_widgets: Sequence[tk.Misc] = (),
) -> None:
    def _scroll_units(event: tk.Event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            return int(-delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        button_number = getattr(event, "num", None)
        if button_number == 4:
            return -1
        if button_number == 5:
            return 1
        return 0

    def _on_mousewheel(event: tk.Event) -> str | None:
        units = _scroll_units(event)
        if units == 0:
            return None
        canvas.yview_scroll(units, "units")
        return "break"

    def _bind_mousewheel(_event: tk.Event | None = None) -> None:
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

    def _unbind_mousewheel(_event: tk.Event | None = None) -> None:
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    for widget in activate_widgets:
        widget.bind("<Enter>", _bind_mousewheel, add="+")
        widget.bind("<Leave>", _unbind_mousewheel, add="+")
        widget.bind("<FocusIn>", _bind_mousewheel, add="+")
        widget.bind("<FocusOut>", _unbind_mousewheel, add="+")

    for widget in release_widgets:
        widget.bind("<Leave>", _unbind_mousewheel, add="+")
        widget.bind("<FocusOut>", _unbind_mousewheel, add="+")

    toplevel = canvas.winfo_toplevel()
    toplevel.bind(
        "<Destroy>",
        lambda event: _unbind_mousewheel(event) if event.widget is toplevel else None,
        add="+",
    )


def scroll_widget_into_view(canvas: tk.Canvas, widget: tk.Misc, *, padding: int = 12) -> None:
    bbox = canvas.bbox("all")
    if not bbox:
        return
    total_height = bbox[3] - bbox[1]
    if total_height <= 0:
        return

    canvas.update_idletasks()
    top = canvas.canvasy(0)
    bottom = top + canvas.winfo_height()
    widget_top = widget.winfo_rooty() - canvas.winfo_rooty() + top
    widget_bottom = widget_top + widget.winfo_height()

    if widget_top < top + padding:
        target = max(0, widget_top - padding)
        canvas.yview_moveto(target / total_height)
        return
    if widget_bottom <= bottom - padding:
        return
    target = min(total_height, widget_bottom - canvas.winfo_height() + padding)
    canvas.yview_moveto(target / total_height)
