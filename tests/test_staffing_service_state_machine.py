from __future__ import annotations

from pathlib import Path

from staffing_service import StaffingService
from staffing_store import StaffingStore


class _Clock:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self) -> str:
        return next(self.values)


def test_basic_staffing_flow_opens_coming_and_fills_position(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
    )
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-01T09:00:00Z",
                "2026-07-01T09:05:00Z",
                "2026-07-04T10:00:00Z",
            ]
        ),
    )

    opened = service.open_position(assignment_id)
    coming = service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-07-02")
    filled = service.mark_filled(assignment_id)

    assignment = store.get_assignment(assignment_id)
    assert opened.status == "need_now"
    assert coming.status == "coming"
    assert coming.person_id is not None
    assert filled.status == "filled"
    assert assignment.status == "filled"
    assert assignment.person_name == "Jane Doe"
    assert assignment.current_opened_date == "2026-07-01T09:00:00Z"
    assert assignment.current_filled_date == "2026-07-04T10:00:00Z"
    assert store.active_history_count(assignment_id) == 0
    assert store.closed_days_to_fill() == [3]
