from __future__ import annotations

import pytest

from staffing_service import StaffingService
from staffing_store import StaffingStore


class _Notifications:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, dict[str, str], str]] = []

    def emit_event(self, event_type: str, payload: dict[str, str], idempotency_key: str) -> list[object]:
        if self.fail:
            raise RuntimeError("smtp password=secret sender@example.org")
        self.events.append((event_type, payload, idempotency_key))
        return []


class _Clock:
    def __init__(self) -> None:
        self.values = iter(
            [
                "2026-07-01T10:00:00Z",
                "2026-07-01T10:05:00Z",
                "2026-07-01T10:10:00Z",
                "2026-07-01T10:15:00Z",
                "2026-07-01T10:20:00Z",
                "2026-07-01T10:25:00Z",
                "2026-07-01T10:30:00Z",
                "2026-07-01T10:35:00Z",
            ]
        )

    def __call__(self) -> str:
        return next(self.values)


@pytest.fixture()
def store(tmp_path):
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    return store


def test_open_position_commits_then_emits_need_now_notification(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
    )
    notifications = _Notifications()

    result = StaffingService(store, notification_service=notifications, clock=_Clock()).open_position(assignment_id)

    assignment = store.get_assignment(assignment_id)
    assert result.status == "need_now"
    assert assignment.status == "need_now"
    assert store.active_history_count(assignment_id) == 1
    assert len(notifications.events) == 1
    event_type, payload, idempotency_key = notifications.events[0]
    assert event_type == "staffing.assignment.need_now"
    assert idempotency_key == "staffing:1:staffing.assignment.need_now:2026-07-01T10:00:00Z"
    assert payload | {
        "school": "Hawthorne",
        "classroom": "Tranquility",
        "position_name": "Teacher 2",
        "position": "Teacher 2",
        "position_type": "Teacher",
        "slot_group": "",
        "assignment_status": "need_now",
        "person_name": "",
        "start_date": "",
        "shift_start": "",
        "shift_end": "",
        "notice_given": "",
        "notice_date": "",
        "final_working_day": "",
        "final_day": "",
        "permit_status": "",
    } == payload


def test_successful_staffing_transitions_emit_matching_events(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())

    service.open_position(assignment_id)
    service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-07-03")
    service.mark_filled(assignment_id)
    service.mark_replacing(assignment_id, notice_given="2026-07-10", final_working_day="2026-07-24")
    service.mark_not_needed(assignment_id, confirmed=True)

    assert [event[0] for event in notifications.events] == [
        "staffing.assignment.need_now",
        "staffing.assignment.coming",
        "staffing.assignment.filled",
        "employment.notice.given",
        "staffing.assignment.replace",
        "employment.notice.given",
        "staffing.assignment.not_needed",
    ]
    assert notifications.events[-1][1]["assignment_status"] == "dont_need_now"


def test_clear_replacement_moves_replace_to_need_now_and_emits_need_now(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Jane Doe",
    )
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())
    service.mark_replacing(assignment_id, notice_given="2026-07-10", final_working_day="2026-07-24")

    result = service.clear_replacement(assignment_id)

    assignment = store.get_assignment(assignment_id)
    assert result.status == "need_now"
    assert assignment.status == "need_now"
    assert assignment.person_id is None
    assert store.active_history_count(assignment_id) == 1
    assert notifications.events[-1][0] == "staffing.assignment.need_now"
    assert notifications.events[-1][1]["assignment_status"] == "need_now"


def test_mark_coming_rejects_past_start_date_without_notification(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())
    service.open_position(assignment_id)
    notifications.events.clear()

    with pytest.raises(ValueError, match="Start date cannot be in the past"):
        service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-06-30")

    assert store.get_assignment(assignment_id).status == "need_now"
    assert notifications.events == []


def test_permit_update_emits_person_scoped_event_key(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())
    service.open_position(assignment_id)
    coming = service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-07-03")

    service.update_permit_status(coming.person_id or 0, "teacher_permit_approved")

    event_type, payload, key = notifications.events[-1]
    assert event_type == "staffing.permit.updated"
    assert payload["permit_status"] == "teacher_permit_approved"
    assert payload["person_name"] == "Jane Doe"
    assert key == f"staffing:person:{coming.person_id}:staffing.permit.updated:2026-07-01T10:10:00Z"


def test_failed_transition_does_not_emit_notification(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()

    with pytest.raises(ValueError, match="Invalid transition"):
        StaffingService(store, notification_service=notifications, clock=_Clock()).mark_filled(assignment_id)

    assert notifications.events == []


def test_duplicate_active_history_blocks_open_without_notification(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher", status="replace")
    with store.connect() as conn:
        assignment = store.get_assignment(assignment_id)
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
            )
            SELECT id, classroom_id, position_name, '2026-06-01T10:00:00Z', '2026-06-01T10:00:00Z', '2026-06-01T10:00:00Z'
            FROM assignments WHERE id = ?
            """,
            (assignment.id,),
        )
    notifications = _Notifications()

    with pytest.raises(ValueError, match="Invalid assignment history state"):
        StaffingService(store, notification_service=notifications, clock=_Clock()).open_position(assignment_id)

    assert notifications.events == []


def test_missing_history_blocks_mark_filled_without_notification(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())
    service.open_position(assignment_id)
    service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-07-03")
    notifications.events.clear()
    with store.connect() as conn:
        conn.execute("DELETE FROM assignment_history WHERE assignment_id = ?", (assignment_id,))

    with pytest.raises(ValueError, match="Invalid assignment history state"):
        service.mark_filled(assignment_id)

    assert notifications.events == []


def test_failed_mark_filled_rolls_back_assignment_status(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    notifications = _Notifications()
    service = StaffingService(store, notification_service=notifications, clock=_Clock())
    service.open_position(assignment_id)
    service.mark_coming(assignment_id, person_name="Jane Doe", start_date="2026-07-03")
    with store.connect() as conn:
        conn.execute("DELETE FROM assignment_history WHERE assignment_id = ?", (assignment_id,))

    with pytest.raises(ValueError, match="Invalid assignment history state"):
        service.mark_filled(assignment_id)

    assert store.get_assignment(assignment_id).status == "coming"


def test_notification_failure_does_not_roll_back_staffing_transaction(store: StaffingStore) -> None:
    assignment_id = store.seed_assignment(school="Hawthorne", classroom="Tranquility", position_name="Teacher 2", position_type="Teacher")
    service = StaffingService(store, notification_service=_Notifications(fail=True), clock=_Clock())

    result = service.open_position(assignment_id)

    assert result.status == "need_now"
    assert store.get_assignment(assignment_id).status == "need_now"
    assert store.active_history_count(assignment_id) == 1
