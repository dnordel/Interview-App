from __future__ import annotations

from types import SimpleNamespace

from ui_composition import HistoryDataGrid


class _FakeTree:
    def __init__(self, column: str = "#5") -> None:
        self._column = column
        self._selected: tuple[str, ...] = ()

    def identify_row(self, _y: int) -> str:
        return "row-1"

    def identify_column(self, _x: int) -> str:
        return self._column

    def selection_set(self, item_id: str) -> None:
        self._selected = (item_id,)

    def selection(self) -> tuple[str, ...]:
        return self._selected


def _build_grid() -> HistoryDataGrid:
    grid = object.__new__(HistoryDataGrid)
    grid.sort_column = "interview_date"
    grid.sort_desc = True
    grid.filter_text = ""
    grid._all_rows = []
    grid._visible_rows = []
    grid._tooltip_window = None
    grid._tooltip_label = None
    grid._tooltip_text = ""
    grid._hide_tooltip = lambda: None
    return grid


def test_filtered_sorted_rows_applies_filter_and_score_sort() -> None:
    grid = _build_grid()
    grid.filter_text = "ana"
    grid.sort_column = "interview_score"
    grid.sort_desc = True
    rows = [
        {"history_id": "1", "candidate_name": "Ana", "interview_score": "15"},
        {"history_id": "2", "candidate_name": "Brian", "interview_score": "30"},
        {"history_id": "3", "candidate_name": "Anabelle", "interview_score": "20"},
    ]

    actual = grid._filtered_sorted_rows(rows)

    assert [row["history_id"] for row in actual] == ["3", "1"]


def test_handle_click_dispatches_callbacks_by_column() -> None:
    calls: list[str] = []
    row = {"history_id": "row-1", "candidate_name": "Test"}
    grid = _build_grid()
    grid._visible_rows = [row]
    grid._on_row_selected = lambda _row: calls.append("selected")
    grid._on_offer_action = lambda _row: calls.append("offer")
    grid._on_retranscribe_action = lambda _row: calls.append("retranscribe")
    grid._on_open_transcript_link = lambda _row: calls.append("transcript")
    grid._on_open_notes_link = lambda _row: calls.append("notes")

    for column, expected in [
        ("#5", "offer"),
        ("#6", "notes"),
    ]:
        calls.clear()
        grid._tree = _FakeTree(column)
        grid._handle_click(SimpleNamespace(x=1, y=1))
        assert calls == ["selected", expected]


def test_notes_link_label_shows_processing_until_deepseek_completes() -> None:
    grid = _build_grid()

    assert grid._row_values({"deepseek_processing_status": "processing"})[-1] == "Processing"
