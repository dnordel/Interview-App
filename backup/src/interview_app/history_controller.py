from __future__ import annotations

from typing import Any, Callable
from tkinter import ttk

from ui_components.history_data_grid import HistoryDataGrid


class HistoryController:
    def __init__(
        self,
        app: Any,
        shared_state: Any,
        grid_factory: Callable[..., HistoryDataGrid] = HistoryDataGrid,
    ) -> None:
        self.app = app
        self.shared_state = shared_state
        self._grid_factory = grid_factory
        self.history_grid: HistoryDataGrid | None = None

    def build_history_table(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Interview History")
        box.pack(fill="both", expand=True)
        self.history_grid = self._grid_factory(
            box,
            on_offer_action=self._on_offer_action,
            on_retranscribe_action=self._on_retranscribe_action,
            on_open_transcript_link=self._on_open_transcript_link,
            on_open_notes_link=self._on_open_notes_link,
            on_row_selected=self._on_row_selected,
            on_sort_changed=self._on_sort_changed,
            sort_column=self.app.history_sort_column,
            sort_desc=self.app.history_sort_desc,
        )
        self.history_grid.pack(fill="both", expand=True)

    def refresh_history_tree(self) -> None:
        if self.history_grid is None:
            return
        self.history_grid.set_rows(self.app.history_store.load())
        self.history_grid.set_filter_text(self.app.history_search_var.get())
        rows = self.history_grid.visible_rows()
        self.app.history_rows = rows
        self.shared_state.history_rows = rows

    def selected_history_row(self) -> dict[str, Any] | None:
        if self.history_grid is None:
            return None
        return self.history_grid.selected_row()

    def _on_sort_changed(self, column: str, desc: bool) -> None:
        self.app.history_sort_column = column
        self.app.history_sort_desc = desc
        if self.history_grid is None:
            return
        rows = self.history_grid.visible_rows()
        self.app.history_rows = rows
        self.shared_state.history_rows = rows

    def _on_row_selected(self, row: dict[str, Any]) -> None:
        self.app.history_selected_row = row

    def _on_offer_action(self, row: dict[str, Any]) -> None:
        self.app._history_actions_service().handle_offer_action_for_row(row)

    def _on_retranscribe_action(self, row: dict[str, Any]) -> None:
        self.app._history_actions_service().handle_retranscribe_for_row(row)

    def _on_open_transcript_link(self, row: dict[str, Any]) -> None:
        self._open_history_link(row, "transcript_path")

    def _on_open_notes_link(self, row: dict[str, Any]) -> None:
        self._open_history_link(row, "interview_notes_path")

    def _open_history_link(self, row: dict[str, Any], key: str) -> None:
        path_value = str(row.get(key, "")).strip()
        if not HistoryDataGrid._path_exists(path_value):
            return
        self.app._open_path_in_default_app(path_value)
