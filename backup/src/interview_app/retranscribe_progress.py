from __future__ import annotations

from dataclasses import dataclass
from tkinter import Toplevel, ttk
from typing import Any


@dataclass(slots=True)
class RetranscriptionProgressState:
    total_steps: int
    completed_steps: int = 0
    status_text: str = "Preparing transcription retry..."


class RetranscriptionProgressDialog:
    def __init__(self, parent: Any, total_steps: int) -> None:
        self._parent = parent
        self._state = RetranscriptionProgressState(total_steps=max(int(total_steps), 1))
        self._window: Toplevel | None = None
        self._status_label: ttk.Label | None = None
        self._progressbar: ttk.Progressbar | None = None

    def show(self) -> None:
        if self._window is not None:
            return
        window = Toplevel(self._parent)
        window.title("Retrying transcription")
        window.resizable(False, False)
        window.geometry("420x130")
        window.transient(self._parent)
        window.protocol("WM_DELETE_WINDOW", window.iconify)

        container = ttk.Frame(window, padding=12)
        container.pack(fill="both", expand=True)

        status = ttk.Label(container, text=self._state.status_text, anchor="w")
        status.pack(fill="x", pady=(0, 8))

        progress = ttk.Progressbar(
            container,
            mode="determinate",
            maximum=self._state.total_steps,
            value=self._state.completed_steps,
        )
        progress.pack(fill="x", pady=(0, 8))

        detail = ttk.Label(container, text=self._format_progress(), anchor="w")
        detail.pack(fill="x")

        self._window = window
        self._progressbar = progress
        self._status_label = detail

    def update(self, *, completed_steps: int, status_text: str) -> None:
        self._state.completed_steps = min(max(int(completed_steps), 0), self._state.total_steps)
        self._state.status_text = str(status_text or "Retrying transcription...")
        if self._window is None:
            return
        if self._progressbar is not None:
            self._progressbar.configure(value=self._state.completed_steps)
        if self._status_label is not None:
            self._status_label.configure(text=self._format_progress())
        self._window.title(self._state.status_text)
        self._window.update_idletasks()

    def close(self) -> None:
        if self._window is None:
            return
        self._window.destroy()
        self._window = None

    def _format_progress(self) -> str:
        return f"{self._state.completed_steps} of {self._state.total_steps} complete"

