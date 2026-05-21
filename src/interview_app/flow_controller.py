from __future__ import annotations

from typing import Any


class FlowController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def go_next(self) -> None:
        self.app.next_question()

    def go_back(self) -> None:
        self.app.prev_question()

    def active_flow(self) -> list[dict[str, Any]]:
        return list(self.app.active_flow)
