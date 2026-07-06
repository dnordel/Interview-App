from __future__ import annotations

from pathlib import Path
from datetime import date

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
    assert assignment.current_filled_date == "2026-07-02"
    assert store.active_history_count(assignment_id) == 0
    assert store.closed_days_to_fill() == [1]


def test_mark_filled_uses_coming_start_date_as_filled_date(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-01T09:00:00Z",
                "2026-07-01T09:05:00Z",
                "2026-07-12T10:00:00Z",
            ]
        ),
    )
    service.open_position(assignment_id)
    service.mark_coming(assignment_id, person_name="Emily Carter", start_date="2026-07-10")

    service.mark_filled(assignment_id)

    assignment = store.get_assignment(assignment_id)
    assert assignment.status == "filled"
    assert assignment.current_filled_date == "2026-07-10"
    assert store.closed_days_to_fill() == [9]


def test_move_person_to_open_assignment_requires_confirmation_and_reopens_source(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    source_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Jane Doe",
    )
    target_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony",
        position_name="Teacher 2",
        position_type="Teacher",
        status="need_now",
    )
    service = StaffingService(store, clock=_Clock(["2026-07-05T09:00:00Z"]))

    result = service.move_person(source_id, target_id, confirmed=True)

    source = store.get_assignment(source_id)
    target = store.get_assignment(target_id)
    assert result.assignment_id == target_id
    assert source.status == "need_now"
    assert source.person_id is None
    assert source.current_opened_date == "2026-07-05T09:00:00Z"
    assert target.status == "filled"
    assert target.person_name == "Jane Doe"
    assert store.active_history_count(source_id) == 1


def test_update_assignment_details_edits_classroom_shift_and_permit(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Jane Doe",
    )
    service = StaffingService(store, clock=_Clock(["2026-07-05T10:00:00Z"]))

    result = service.update_assignment_details(
        assignment_id,
        classroom="Harmony",
        classroom_program="Toddler",
        position_name="Lead Teacher",
        position_type="Lead",
        person_name="Janet Doe",
        start_date="2026-07-06",
        shift_start="08:30",
        shift_end="17:00",
        permit_status="teacher_permit_approved",
        notes="Moved to lead role.",
    )

    assignment = store.get_assignment(assignment_id)
    assert result.assignment_id == assignment_id
    assert assignment.classroom == "Harmony"
    assert assignment.classroom_program == "Toddler"
    assert assignment.position_name == "Lead Teacher"
    assert assignment.position_type == "Lead"
    assert assignment.person_name == "Janet Doe"
    assert assignment.start_date == "2026-07-06"
    assert assignment.shift_start == "08:30"
    assert assignment.shift_end == "17:00"
    assert assignment.permit_status == "teacher_permit_approved"
    assert assignment.notes == "Moved to lead role."


def test_update_permit_status_records_effective_date_units_and_notes(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Imgard",
        permit_status="permit_in_process",
    )
    assignment = store.get_assignment(assignment_id)
    service = StaffingService(store, clock=_Clock(["2026-07-05T10:00:00Z"]))

    service.update_permit_status(
        assignment.person_id or 0,
        "teacher_permit_approved",
        effective_date="2026-07-06",
        units=24,
        documentation_received=True,
        notes="Permit file received.",
    )

    updated = store.get_assignment(assignment_id)
    with store.connect() as conn:
        person = store.person_context(conn, assignment.person_id or 0)
    assert updated.status == "filled"
    assert updated.permit_status == "teacher_permit_approved"
    assert person.permit_effective_date == "2026-07-06"
    assert person.units == 24
    assert person.permit_documentation_received is True
    assert person.permit_notes == "Permit file received."


def test_add_person_creates_active_employee_record(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store, clock=_Clock(["2026-07-06T09:00:00Z"]))

    person = service.add_person(
        name="Nina Patel",
        role="Aide",
        permit_status="permit_in_process",
        units=5,
    )

    people = store.list_people()
    assert person.id == people[0].id
    assert person.name == "Nina Patel"
    assert person.role == "Aide"
    assert person.permit_status == "permit_in_process"
    assert person.units == 5
    assert person.active is True
    assert person.updated_at == "2026-07-06T09:00:00Z"


def test_add_classroom_creates_classroom_record_without_positions(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store, clock=_Clock(["2026-07-06T09:00:00Z"]))

    classroom = service.add_classroom(
        school="Hawthorne",
        name="Sunflower",
        program="Preschool",
        licensed_capacity=18,
    )

    classrooms = store.list_classrooms()
    assert classroom.id == classrooms[0].id
    assert classroom.school == "Hawthorne"
    assert classroom.name == "Sunflower"
    assert classroom.program == "Preschool"
    assert classroom.licensed_capacity == 18
    assert classroom.active is True
    assert store.list_assignments() == []


def test_add_position_creates_need_now_assignment_and_open_history(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store, clock=_Clock(["2026-07-06T09:00:00Z"]))

    result = service.add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        classroom_program="Preschool",
        licensed_capacity=24,
        position_name="Teacher 3",
        position_type="Teacher",
        initial_status="need_now",
        notes="New classroom slot.",
    )

    assignment = store.get_assignment(result.assignment_id)
    assert result.status == "need_now"
    assert assignment.school == "Hawthorne"
    assert assignment.classroom == "Harmony 1"
    assert assignment.classroom_program == "Preschool"
    assert assignment.classroom_capacity == 24
    assert assignment.position_name == "Teacher 3"
    assert assignment.position_type == "Teacher"
    assert assignment.current_opened_date == "2026-07-06T09:00:00Z"
    assert assignment.notes == "New classroom slot."
    assert store.active_history_count(result.assignment_id) == 1


def test_locked_staffing_action_queues_and_replays_when_db_unlocks(tmp_path: Path) -> None:
    db_path = tmp_path / "staffing.sqlite3"
    lock_owner = StaffingStore(db_path)
    store = StaffingStore(db_path)
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
                "2026-07-01T09:01:00Z",
                "2026-07-01T09:02:00Z",
                "2026-07-01T09:03:00Z",
            ]
        ),
    )

    with lock_owner.write_connection("other-director"):
        result = service.open_position(assignment_id)
        metrics = service.staffing_metrics(today=date(2026, 7, 1))

    assert result.status == "queued"
    assert metrics.rows[0].status == "need_now"
    assert metrics.open_count == 1
    assert store.pending_operations_path.exists()
    assert store.get_assignment(assignment_id).status == "dont_need_now"

    applied = service.flush_pending_operations()

    assert applied == 1
    assert not store.pending_operations_path.exists()
    assert store.get_assignment(assignment_id).status == "need_now"
