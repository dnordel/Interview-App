from __future__ import annotations

from typing import Any


class UiShellController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def clear_page(self) -> None:
        self.app.clear_page()

    def clear_footer(self) -> None:
        self.app.clear_footer()

    def set_footer_actions(self, *, left_actions: Any = None, right_actions: Any = None) -> None:
        self.app.set_footer_actions(left_actions=left_actions, right_actions=right_actions)
