from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

import tkinter as tk


@dataclass(slots=True)
class KeyboardPathSession:
    logger: Any
    flow_id: str
    screen_id: str
    keyboard_step_count: int = 0
    _last_keyboard_at: float = 0.0

    def bind(self, widget: tk.Misc) -> None:
        widget.bind("<KeyPress>", self._on_keypress, add="+")

    def _on_keypress(self, _event: tk.Event) -> None:
        self.mark_step()

    def mark_step(self, step_count: int = 1) -> None:
        self.keyboard_step_count += max(1, int(step_count))
        self._last_keyboard_at = monotonic()

    def complete(self, *, abandoned: bool = False, screen_id: str | None = None) -> None:
        if self.logger is None:
            return
        active_screen_id = (screen_id or self.screen_id).strip() or self.screen_id
        completed_via_keyboard = self.keyboard_step_count > 0 and self._is_recent_keyboard_activity()
        if hasattr(self.logger, "log_keyboard_path_completed"):
            self.logger.log_keyboard_path_completed(
                screen_id=active_screen_id,
                flow_id=self.flow_id,
                completed_via_keyboard=completed_via_keyboard,
                keyboard_step_count=self.keyboard_step_count,
                abandoned=bool(abandoned),
            )
            return
        self.logger.log_event(
            "ux.keyboard_path_completed",
            screen_id=active_screen_id,
            flow_id=self.flow_id,
            completed_via_keyboard=completed_via_keyboard,
            keyboard_step_count=self.keyboard_step_count,
            abandoned=bool(abandoned),
        )

    def _is_recent_keyboard_activity(self, recency_s: float = 12.0) -> bool:
        if self._last_keyboard_at <= 0:
            return False
        return (monotonic() - self._last_keyboard_at) <= recency_s
