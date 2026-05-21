from __future__ import annotations

from typing import Any


class DashboardController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def refresh_dashboard(self) -> None:
        self.app._refresh_dashboard_snapshot()
