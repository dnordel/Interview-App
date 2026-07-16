from pathlib import Path
import shutil

from staffing_change_stage import StaffingChangeStage
from staffing_service import StaffingService
from staffing_store import StaffingStore


def test_staffing_change_stage_delivers_once_to_peer_not_source(tmp_path: Path) -> None:
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")

    event_id = stage.publish(
        source_replica="admin",
        school="Palmdale",
        operation="open_position",
        payload={"assignment_id": 12},
    )

    assert stage.pending_for(replica="admin", school="") == []
    pending = stage.pending_for(replica="director:palmdale", school="Palmdale")
    assert [(event.id, event.operation, event.payload) for event in pending] == [
        (event_id, "open_position", {"assignment_id": 12})
    ]
    stage.acknowledge(event_id, replica="director:palmdale")
    assert stage.pending_for(replica="director:palmdale", school="Palmdale") == []


def test_admin_change_replays_once_into_school_database(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Aide 1",
        position_type="Aide",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    director_store = StaffingStore(director_path)
    director = StaffingService(
        director_store,
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )

    admin.open_position(assignment_id)

    assert director_store.get_assignment(assignment_id).status == "dont_need_now"
    assert director.replay_staged_changes() == 1
    assert director_store.get_assignment(assignment_id).status == "need_now"
    assert director.replay_staged_changes() == 0


def test_director_change_replays_once_into_admin_database(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")
    director_store = StaffingStore(director_path)
    director = StaffingService(
        director_store,
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")

    director.open_position(assignment_id)

    assert admin_store.get_assignment(assignment_id).status == "dont_need_now"
    assert admin.replay_staged_changes() == 1
    assert admin_store.get_assignment(assignment_id).status == "need_now"
    assert admin.replay_staged_changes() == 0


def test_added_position_replays_into_school_database(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    director_store = StaffingStore(director_path)
    director = StaffingService(
        director_store,
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )

    created = admin.add_position(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Aide 1",
        position_type="Aide",
        initial_status="need_now",
    )

    assert director.replay_staged_changes() == 1
    replicated = director_store.get_assignment(created.assignment_id)
    assert (replicated.school, replicated.classroom, replicated.position_name, replicated.status) == (
        "Palmdale",
        "Tranquility",
        "Aide 1",
        "need_now",
    )


def test_assignment_detail_change_replays_all_fields(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Aide 1",
        position_type="Aide",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    director_store = StaffingStore(director_path)
    director = StaffingService(
        director_store,
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )

    admin.update_assignment_details(
        assignment_id,
        classroom="Harmony",
        classroom_program="Preschool",
        position_name="Teacher 4",
        position_type="Teacher",
        status="need_now",
        shift_start="08:00",
        shift_end="16:30",
        notes="Priority opening",
    )

    assert director.replay_staged_changes() == 1
    replicated = director_store.get_assignment(assignment_id)
    assert (
        replicated.classroom,
        replicated.classroom_program,
        replicated.position_name,
        replicated.position_type,
        replicated.status,
        replicated.shift_start,
        replicated.shift_end,
        replicated.notes,
    ) == ("Harmony", "Preschool", "Teacher 4", "Teacher", "need_now", "08:00", "16:30", "Priority opening")


def test_completed_director_interview_replays_by_history_id(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    admin_referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-123",
        candidate_name="Candidate One",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
    )
    shutil.copy2(admin_path, director_path)
    with admin_store.connect() as conn:
        conn.execute("DELETE FROM director_candidate_referrals WHERE history_id = ?", ("hist-123",))
    stage = StaffingChangeStage(tmp_path / "staffing_changes.sqlite3")
    director_store = StaffingStore(director_path)
    director = StaffingService(
        director_store,
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")

    director.record_director_interview(
        admin_referral.id,
        director_name="Director One",
        completed_date="2026-07-16",
        rating=9,
        decision="hire",
        decision_notes="Strong fit",
        proposed_shift_start="08:00",
        proposed_shift_end="16:30",
        proposed_classroom="Harmony",
    )

    assert admin.replay_staged_changes() == 1
    completed = admin.list_completed_director_interviews(school="Palmdale")
    assert [(row.history_id, row.decision, row.proposed_classroom) for row in completed] == [
        ("hist-123", "hire", "Harmony")
    ]
