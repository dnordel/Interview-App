from __future__ import annotations

from pathlib import Path
from datetime import date
import pytest

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


def test_delete_position_removes_mistaken_unassigned_position(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(
        store,
        clock=_Clock(["2026-07-06T09:00:00Z", "2026-07-06T09:05:00Z"]),
    )
    result = service.add_position(
        school="Hawthorne",
        classroom="Office",
        classroom_program="Support",
        position_name="Director",
        position_type="Director",
        initial_status="need_now",
    )

    deleted = service.delete_position(result.assignment_id, confirmed=True)

    assert deleted.assignment_id == result.assignment_id
    assert deleted.status == "deleted"
    assert store.list_assignments() == []
    assert service.staffing_metrics(today=date(2026, 7, 6)).open_count == 0


def test_delete_position_rejects_assigned_position(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Office",
        position_name="Director",
        position_type="Director",
        status="filled",
        person_name="Violet",
    )
    service = StaffingService(store, clock=_Clock(["2026-07-06T09:00:00Z"]))

    with pytest.raises(ValueError, match="assigned person"):
        service.delete_position(assignment_id, confirmed=True)

    assert store.get_assignment(assignment_id).status == "filled"
    assert len(store.list_assignments()) == 1


def test_director_interview_completion_is_school_scoped_and_does_not_fill_position(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="need_now",
    )
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-06T09:00:00Z",
                "2026-07-06T09:05:00Z",
                "2026-07-06T09:10:00Z",
            ]
        ),
    )
    service.upsert_director_candidate_referral(
        history_id="hist-1",
        candidate_name="Jordan Lee",
        school="Hawthorne",
        position="Teacher",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        interview_date="2026-07-05",
        candidate_email="jordan@example.org",
    )
    service.upsert_director_candidate_referral(
        history_id="hist-2",
        candidate_name="Riley Park",
        school="Palmdale",
        position="Teacher",
        interviewer_rating=7.0,
        interviewer_outcome="borderline",
        interview_date="2026-07-05",
    )

    pending_hawthorne = service.list_pending_director_interviews(school="Hawthorne")
    result = service.record_director_interview(
        pending_hawthorne[0].id,
        director_name="Avery Director",
        completed_date="2026-07-06",
        rating=9.0,
        decision="hire",
        decision_notes="Strong classroom presence.",
        proposed_shift_start="8:00 AM",
        proposed_shift_end="5:00 PM",
        proposed_classroom="Harmony 1",
        follow_up_needed=True,
    )

    assignment = store.get_assignment(assignment_id)
    assert [candidate.candidate_name for candidate in pending_hawthorne] == ["Jordan Lee"]
    assert service.list_pending_director_interviews(school="Hawthorne") == []
    assert [candidate.candidate_name for candidate in service.list_pending_director_interviews(school="Palmdale")] == [
        "Riley Park"
    ]
    assert result.decision == "hire"
    assert result.owner_approval_status == "pending_owner_approval"
    assert result.proposed_shift_start == "8:00 AM"
    assert assignment.status == "need_now"
    assert assignment.person_name == ""
    assert assignment.classroom == "Harmony 1"
    palmdale_pending = service.list_pending_director_interviews(school="Palmdale")
    assert service.delete_pending_director_interviews([palmdale_pending[0].id, result.referral_id]) == 1
    assert service.list_pending_director_interviews(school="Palmdale") == []
    assert service.list_completed_director_interviews(school="Hawthorne")[0].candidate_name == "Jordan Lee"


def test_director_interview_validation_requires_notes_and_hire_shift_details(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-06T09:00:00Z",
                "2026-07-06T09:05:00Z",
                "2026-07-06T09:10:00Z",
            ]
        ),
    )
    referral = service.upsert_director_candidate_referral(
        history_id="hist-1",
        candidate_name="Jordan Lee",
        school="Hawthorne",
        position="Teacher",
        interviewer_rating=8.5,
        interviewer_outcome="borderline",
        interview_date="2026-07-05",
    )

    with pytest.raises(ValueError, match="Decision notes are required"):
        service.record_director_interview(
            referral.id,
            director_name="Avery Director",
            completed_date="2026-07-06",
            rating=5,
            decision="no_hire",
            decision_notes="",
        )
    with pytest.raises(ValueError, match="Shift start is required"):
        service.record_director_interview(
            referral.id,
            director_name="Avery Director",
            completed_date="2026-07-06",
            rating=8,
            decision="hire",
            decision_notes="Hire if schedule works.",
            proposed_classroom="Harmony 1",
        )
    assert service.list_pending_director_interviews(school="Hawthorne") == [referral]


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


def test_locked_director_referral_queues_and_replays_when_db_unlocks(tmp_path: Path) -> None:
    db_path = tmp_path / "staffing.sqlite3"
    store = StaffingStore(db_path)
    store.initialize()
    lock_path = db_path.with_suffix(db_path.suffix + ".editing.lock")
    lock_path.write_text(
        '{"owner": "other-director", "created_at": "2099-01-01T00:00:00Z"}',
        encoding="utf-8",
    )
    service = StaffingService(
        store,
        clock=_Clock(["2026-07-01T09:00:00Z", "2026-07-01T09:01:00Z"]),
    )

    queued = service.upsert_director_candidate_referral(
        history_id="hist-queued",
        candidate_name="Queued Candidate",
        school="Hawthorne",
        position="Teacher",
        interviewer_rating=8.8,
        interviewer_outcome="hire",
        interview_date="2026-07-01",
        candidate_email="queued@example.org",
        queue_on_lock=True,
    )

    assert queued.id == 0
    assert queued.history_id == "hist-queued"
    assert queued.updated_at == "2026-07-01T09:00:00Z"
    assert service.list_pending_director_interviews(school="Hawthorne") == []
    assert store.pending_operations_path.exists()

    lock_path.unlink()
    applied = service.flush_pending_operations()

    assert applied == 1
    assert not store.pending_operations_path.exists()
    pending = service.list_pending_director_interviews(school="Hawthorne")
    assert [candidate.candidate_name for candidate in pending] == ["Queued Candidate"]
    assert pending[0].interviewer_rating == 8.8
