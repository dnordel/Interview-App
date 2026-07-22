from pathlib import Path
import json
import shutil

import pytest

from staffing_change_stage import StaffingChangeStage
from staffing_service import StaffingChangeConflict, StaffingService, staffing_change_conflict_message
from staffing_store import StaffingStore


def _merge_dropbox_artifacts(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for artifact in source.rglob("*.json"):
        destination = target / artifact.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, destination)


def test_conflict_message_shows_only_conflicting_local_and_incoming_values() -> None:
    conflict = StaffingChangeConflict(
        event_id="event-1",
        source_replica="director:palmdale:pmd",
        school="Palmdale",
        operation="update_assignment_details",
        base_snapshot={},
        local_snapshot={},
        remote_payload={"notes": "Director note", "shift_start": "08:00"},
        conflicting_fields=("assignment:12.notes",),
        local_values=(("Notes", "Admin note"),),
        incoming_values=(("Notes", "Director note"),),
    )

    message = staffing_change_conflict_message(conflict)

    assert "Notes: Admin note → Director note" in message
    assert "Shift Start" not in message


def _seed_offer_slot(store: StaffingStore) -> int:
    return store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 1",
        position_type="Teacher",
        status="need_now",
    )


def test_independent_machine_events_survive_dropbox_merge(tmp_path: Path) -> None:
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    admin_stage = StaffingChangeStage(admin_dir)
    director_stage = StaffingChangeStage(director_dir)

    admin_event = admin_stage.publish(
        source_replica="admin",
        school="Palmdale",
        operation="open_position",
        payload={"assignment_id": 1},
    )
    director_event = director_stage.publish(
        source_replica="director:palmdale",
        school="Palmdale",
        operation="mark_not_needed",
        payload={"assignment_id": 2, "confirmed": True},
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert [event.id for event in admin_stage.pending_for(replica="admin")] == [director_event]
    assert [event.id for event in director_stage.pending_for(replica="director:palmdale", school="Palmdale")] == [
        admin_event
    ]


def test_dropbox_conflicted_event_copy_is_deduplicated_by_event_id(tmp_path: Path) -> None:
    stage_dir = tmp_path / "staffing_change_events"
    stage = StaffingChangeStage(stage_dir)
    event_id = stage.publish(
        source_replica="admin",
        school="Palmdale",
        operation="open_position",
        payload={"assignment_id": 12},
    )
    event_file = next(stage_dir.rglob("event-*.json"))
    shutil.copy2(
        event_file,
        event_file.with_name(f"{event_file.stem} (Director PMD's conflicted copy 2026-07-16).json"),
    )

    pending = stage.pending_for(replica="director:palmdale", school="Palmdale")

    assert [event.id for event in pending] == [event_id]


def test_later_user_event_waits_for_delayed_predecessor(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source = StaffingChangeStage(source_dir)
    source.publish(
        source_replica="admin:owner",
        source_database="admin",
        school="Palmdale",
        operation="first_edit",
        payload={"assignment_id": 1},
    )
    source.publish(
        source_replica="admin:owner",
        source_database="admin",
        school="Palmdale",
        operation="second_edit",
        payload={"assignment_id": 1},
    )
    files = {
        json.loads(path.read_text(encoding="utf-8"))["operation"]: path
        for path in source_dir.rglob("event-*.json")
    }
    later = files["second_edit"]
    later_target = target_dir / later.relative_to(source_dir)
    later_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(later, later_target)
    target = StaffingChangeStage(target_dir)

    assert target.pending_for(replica="director:palmdale", school="Palmdale") == []

    earlier = files["first_edit"]
    earlier_target = target_dir / earlier.relative_to(source_dir)
    earlier_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(earlier, earlier_target)

    assert [event.operation for event in target.pending_for(replica="director:palmdale", school="Palmdale")] == [
        "first_edit",
        "second_edit",
    ]


def test_simultaneous_admin_and_director_changes_converge_after_dropbox_merge(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    admin_assignment = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_assignment = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    admin = StaffingService(admin_store, change_stage=StaffingChangeStage(admin_dir), replica="admin")
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        school_scope="Palmdale",
    )

    admin.open_position(admin_assignment)
    director.open_position(director_assignment)
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert [admin_store.get_assignment(item).status for item in (admin_assignment, director_assignment)] == [
        "need_now",
        "need_now",
    ]
    assert [director_store.get_assignment(item).status for item in (admin_assignment, director_assignment)] == [
        "need_now",
        "need_now",
    ]


def test_simultaneous_same_position_edits_converge_to_newer_change(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    admin_conflicts = []
    director_conflicts = []
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
        conflict_resolver=lambda conflict: admin_conflicts.append(conflict) is None,
        clock=lambda: "2026-07-16T09:00:01Z",
    )
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
        conflict_resolver=lambda conflict: not (director_conflicts.append(conflict) is None),
        clock=lambda: "2026-07-16T09:00:02Z",
    )

    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Admin note")
    director.update_assignment_details(assignment_id, classroom="Tranquility", notes="Director note")
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert admin_conflicts[0].source_replica == "director:palmdale:pmd"
    assert admin_conflicts[0].remote_payload["notes"] == "Director note"
    assert director_conflicts[0].source_replica == "admin:owner"
    assert director_conflicts[0].remote_payload["notes"] == "Admin note"
    assert admin_store.get_assignment(assignment_id).notes == "Director note"
    assert director_store.get_assignment(assignment_id).notes == "Director note"


def test_simultaneous_different_fields_on_same_position_merge_without_prompt(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
        conflict_resolver=lambda _conflict: pytest.fail("different fields must not prompt"),
    )
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
        conflict_resolver=lambda _conflict: pytest.fail("different fields must not prompt"),
    )

    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Admin note")
    director.update_assignment_details(
        assignment_id,
        classroom="Tranquility",
        shift_start="08:00",
        shift_end="16:30",
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert (admin_store.get_assignment(assignment_id).notes, admin_store.get_assignment(assignment_id).shift_start) == (
        "Admin note",
        "08:00",
    )
    assert (director_store.get_assignment(assignment_id).notes, director_store.get_assignment(assignment_id).shift_start) == (
        "Admin note",
        "08:00",
    )


def test_rejected_same_field_conflict_still_applies_non_conflicting_fields(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    conflicts = []
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
        conflict_resolver=lambda conflict: not (conflicts.append(conflict) is None),
    )
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
    )

    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Admin note")
    director.update_assignment_details(
        assignment_id,
        classroom="Tranquility",
        notes="Director note",
        shift_start="08:00",
        shift_end="16:30",
    )
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert len(conflicts) == 1
    assert conflicts[0].conflicting_fields == (f"assignment:{assignment_id}.notes",)
    saved = admin_store.get_assignment(assignment_id)
    assert (saved.notes, saved.shift_start, saved.shift_end) == ("Admin note", "08:00", "16:30")


def test_conflict_choices_publish_resolution_and_replicas_converge(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale", classroom="Tranquility",
        position_name="Teacher 1", position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    admin_prompts = []
    director_prompts = []
    admin = StaffingService(
        admin_store, change_stage=StaffingChangeStage(admin_dir),
        replica="admin", publisher="admin:owner",
        clock=lambda: "2026-07-16T09:00:01Z",
        conflict_resolver=lambda conflict: not (admin_prompts.append(conflict) is None),
    )
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store, change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale", publisher="director:palmdale:pmd", school_scope="Palmdale",
        clock=lambda: "2026-07-16T09:00:02Z",
        conflict_resolver=lambda conflict: not (director_prompts.append(conflict) is None),
    )
    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Admin note")
    director.update_assignment_details(assignment_id, classroom="Tranquility", notes="Director note")
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert len(admin_prompts) == len(director_prompts) == 1
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert len(admin_prompts) == len(director_prompts) == 1
    assert admin_store.get_assignment(assignment_id).notes == "Director note"
    assert director_store.get_assignment(assignment_id).notes == "Director note"


def test_incoming_delete_prompts_when_receiver_changed_record(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin.sqlite3"
    director_db = tmp_path / "director.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale", classroom="Tranquility",
        position_name="Teacher 1", position_type="Teacher",
    )
    shutil.copy2(admin_db, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    conflicts = []
    admin = StaffingService(
        admin_store, change_stage=StaffingChangeStage(admin_dir), replica="admin",
        conflict_resolver=lambda conflict: not (conflicts.append(conflict) is None),
    )
    director = StaffingService(
        director_store, change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale", school_scope="Palmdale",
    )
    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Keep me")
    director.delete_position(assignment_id, confirmed=True)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert len(conflicts) == 1
    saved = admin_store.get_assignment(assignment_id)
    assert saved.notes == "Keep me"
    with admin_store.connect() as conn:
        assert conn.execute("SELECT active FROM assignments WHERE id = ?", (assignment_id,)).fetchone()[0] == 1
    _merge_dropbox_artifacts(admin_dir, director_dir)
    assert director.replay_staged_changes() == 2
    with director_store.connect() as conn:
        assert conn.execute("SELECT active FROM assignments WHERE id = ?", (assignment_id,)).fetchone()[0] == 1


def test_sequential_peer_edits_on_same_position_do_not_prompt(tmp_path: Path) -> None:
    admin_db = tmp_path / "admin" / "staffing_dashboard.sqlite3"
    director_db = tmp_path / "director" / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_db)
    admin_store.initialize()
    assignment_id = admin_store.seed_assignment(
        school="Palmdale",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    director_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(admin_db, director_db)
    admin_dir = tmp_path / "admin" / "staffing_change_events"
    director_dir = tmp_path / "director" / "staffing_change_events"
    conflicts = []
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
        clock=lambda: "2026-07-16T09:00:01Z",
    )
    director_store = StaffingStore(director_db)
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
        conflict_resolver=lambda conflict: conflicts.append(conflict) is None,
        clock=lambda: "2026-07-16T09:00:05Z",
    )

    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="First")
    _merge_dropbox_artifacts(admin_dir, director_dir)
    assert director.replay_staged_changes() == 1
    admin.update_assignment_details(assignment_id, classroom="Tranquility", notes="Second")
    _merge_dropbox_artifacts(admin_dir, director_dir)

    assert director.replay_staged_changes() == 1
    assert conflicts == []
    assert director_store.get_assignment(assignment_id).notes == "Second"


def test_person_change_replays_to_peer_database(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    director_store = StaffingStore(tmp_path / "director.sqlite3")
    admin_store.initialize()
    director_store.initialize()
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
    )
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
    )

    admin.add_person(name="New Teacher", role="Teacher", permit_status="permit_in_process", units=12)
    _merge_dropbox_artifacts(admin_dir, director_dir)

    assert director.replay_staged_changes() == 1
    person = next(item for item in director_store.list_people() if item.name == "New Teacher")
    assert (person.role, person.permit_status, person.units) == ("Teacher", "permit_in_process", 12)


def test_person_edit_replays_by_stable_name(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    admin_store.initialize()
    admin_person = StaffingService(admin_store).add_person(name="Existing Teacher", role="Teacher")
    director_db = tmp_path / "director.sqlite3"
    shutil.copy2(admin_store.path, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
    )
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
    )

    admin.update_person(
        admin_person.id,
        name="Renamed Teacher",
        role="Lead Teacher",
        permit_status="teacher_permit_approved",
        units=24,
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)

    assert director.replay_staged_changes() == 1
    person = next(item for item in director_store.list_people() if item.name == "Renamed Teacher")
    assert (person.role, person.permit_status, person.units) == ("Lead Teacher", "teacher_permit_approved", 24)

    admin.deactivate_person(admin_person.id)
    _merge_dropbox_artifacts(admin_dir, director_dir)

    assert director.replay_staged_changes() == 1
    assert next(item for item in director_store.list_people() if item.name == "Renamed Teacher").active is False


def test_simultaneous_different_person_fields_merge_without_prompt(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    admin_store.initialize()
    person = StaffingService(admin_store).add_person(name="Existing Teacher", role="Teacher")
    director_db = tmp_path / "director.sqlite3"
    shutil.copy2(admin_store.path, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store, change_stage=StaffingChangeStage(admin_dir), replica="admin",
        conflict_resolver=lambda _conflict: pytest.fail("different person fields must not prompt"),
    )
    director = StaffingService(
        director_store, change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale", school_scope="Palmdale",
        conflict_resolver=lambda _conflict: pytest.fail("different person fields must not prompt"),
    )
    admin.update_person(person.id, name=person.name, role="Lead Teacher")
    director.update_person(
        person.id, name=person.name, role="Teacher", permit_status="teacher_permit_approved"
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    admin_person = next(item for item in admin_store.list_people() if item.name == person.name)
    director_person = next(item for item in director_store.list_people() if item.name == person.name)
    assert (admin_person.role, admin_person.permit_status) == ("Lead Teacher", "teacher_permit_approved")
    assert (director_person.role, director_person.permit_status) == ("Lead Teacher", "teacher_permit_approved")


def test_person_conflict_resolution_converges_without_repeat_prompt(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    admin_store.initialize()
    person = StaffingService(admin_store).add_person(name="Existing Teacher", role="Teacher")
    director_db = tmp_path / "director.sqlite3"
    shutil.copy2(admin_store.path, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin_prompts = []
    director_prompts = []
    admin = StaffingService(
        admin_store, change_stage=StaffingChangeStage(admin_dir), replica="admin",
        clock=lambda: "2026-07-16T09:00:01Z",
        conflict_resolver=lambda conflict: not (admin_prompts.append(conflict) is None),
    )
    director = StaffingService(
        director_store, change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale", school_scope="Palmdale",
        clock=lambda: "2026-07-16T09:00:02Z",
        conflict_resolver=lambda conflict: not (director_prompts.append(conflict) is None),
    )
    admin.update_person(person.id, name=person.name, role="Lead Teacher")
    director.update_person(person.id, name=person.name, role="Assistant Teacher")
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)
    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    assert len(admin_prompts) == len(director_prompts) == 1
    assert next(item for item in admin_store.list_people() if item.id == person.id).role == "Assistant Teacher"
    assert next(item for item in director_store.list_people() if item.id == person.id).role == "Assistant Teacher"


def test_classroom_edit_replays_to_peer_database(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    admin_store.initialize()
    classroom_id = admin_store.create_classroom(school="Palmdale", name="Tranquility", program="Preschool")
    director_db = tmp_path / "director.sqlite3"
    shutil.copy2(admin_store.path, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
    )
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
    )

    admin.update_classroom(
        classroom_id=classroom_id,
        school="Palmdale",
        name="Tranquility",
        program="Infant Toddler",
        licensed_capacity=18,
        display_order=3,
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)

    assert director.replay_staged_changes() == 1
    classroom = next(item for item in director_store.list_classrooms() if item.name == "Tranquility")
    assert (classroom.program, classroom.licensed_capacity, classroom.display_order) == ("Infant Toddler", 18, 3)


def test_simultaneous_different_classroom_fields_merge_without_prompt(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    admin_store.initialize()
    classroom_id = admin_store.create_classroom(school="Palmdale", name="Tranquility")
    director_db = tmp_path / "director.sqlite3"
    shutil.copy2(admin_store.path, director_db)
    director_store = StaffingStore(director_db)
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store, change_stage=StaffingChangeStage(admin_dir), replica="admin",
        conflict_resolver=lambda _conflict: pytest.fail("different classroom fields must not prompt"),
    )
    director = StaffingService(
        director_store, change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale", school_scope="Palmdale",
        conflict_resolver=lambda _conflict: pytest.fail("different classroom fields must not prompt"),
    )
    admin.update_classroom(
        classroom_id=classroom_id, school="Palmdale", name="Tranquility", program="Preschool"
    )
    director.update_classroom(
        classroom_id=classroom_id, school="Palmdale", name="Tranquility", licensed_capacity=18
    )
    _merge_dropbox_artifacts(admin_dir, director_dir)
    _merge_dropbox_artifacts(director_dir, admin_dir)

    assert admin.replay_staged_changes() == 1
    assert director.replay_staged_changes() == 1
    admin_room = next(item for item in admin_store.list_classrooms() if item.id == classroom_id)
    director_room = next(item for item in director_store.list_classrooms() if item.id == classroom_id)
    assert (admin_room.program, admin_room.licensed_capacity) == ("Preschool", 18)
    assert (director_room.program, director_room.licensed_capacity) == ("Preschool", 18)


def test_classroom_add_and_deactivate_replay_to_peer_database(tmp_path: Path) -> None:
    admin_store = StaffingStore(tmp_path / "admin.sqlite3")
    director_store = StaffingStore(tmp_path / "director.sqlite3")
    admin_store.initialize()
    director_store.initialize()
    admin_dir = tmp_path / "admin_events"
    director_dir = tmp_path / "director_events"
    admin = StaffingService(
        admin_store,
        change_stage=StaffingChangeStage(admin_dir),
        replica="admin",
        publisher="admin:owner",
    )
    director = StaffingService(
        director_store,
        change_stage=StaffingChangeStage(director_dir),
        replica="director:palmdale",
        publisher="director:palmdale:pmd",
        school_scope="Palmdale",
    )

    classroom = admin.add_classroom(school="Palmdale", name="New Room", program="Preschool")
    _merge_dropbox_artifacts(admin_dir, director_dir)
    assert director.replay_staged_changes() == 1
    assert any(item.name == "New Room" for item in director_store.list_classrooms())

    admin.deactivate_classroom(classroom.id)
    _merge_dropbox_artifacts(admin_dir, director_dir)
    assert director.replay_staged_changes() == 1
    assert all(item.name != "New Room" for item in director_store.list_classrooms())


def test_staffing_change_stage_delivers_once_to_peer_not_source(tmp_path: Path) -> None:
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")

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
        position_name="Teacher 1",
        position_type="Teacher",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
        position_name="Teacher 1",
        position_type="Teacher",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
    _seed_offer_slot(admin_store)
    admin_referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-123",
        candidate_name="Candidate One",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        candidate_email="candidate@example.org",
    )
    shutil.copy2(admin_path, director_path)
    with admin_store.connect() as conn:
        conn.execute("DELETE FROM director_candidate_referrals WHERE history_id = ?", ("hist-123",))
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
        offer_position_id="lead_teacher",
    )

    assert admin.replay_staged_changes() == 1
    completed = admin.list_completed_director_interviews(school="Palmdale")
    assert [(row.history_id, row.decision, row.proposed_classroom, row.offer_position_id) for row in completed] == [
        ("hist-123", "hire", "Harmony", "lead_teacher")
    ]


def test_acknowledged_director_interview_self_repairs_missing_destination(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    _seed_offer_slot(admin_store)
    referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-repair",
        candidate_name="Candidate Repair",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        candidate_email="candidate@example.org",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
    director = StaffingService(
        StaffingStore(director_path),
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    director.record_director_interview(
        referral.id,
        director_name="Director One",
        completed_date="2026-07-21",
        rating=9,
        decision="hire",
        decision_notes="Strong fit",
        proposed_shift_start="08:00",
        proposed_shift_end="16:30",
        proposed_classroom="Harmony",
        offer_position_id="teacher",
    )
    event = stage.pending_for(replica="admin")[0]
    stage.acknowledge(event.id, replica="admin")
    assert stage.pending_for(replica="admin") == []
    assert admin.list_completed_director_interviews(school="Palmdale") == []

    assert admin.replay_staged_changes() == 1
    completed = admin.list_completed_director_interviews(school="Palmdale")
    assert [(row.history_id, row.completed_date, row.offer_position_id) for row in completed] == [
        ("hist-repair", "2026-07-21", "teacher")
    ]
    assert admin.replay_staged_changes() == 0


def test_acknowledged_director_interview_self_repair_defers_when_destination_is_locked(
    tmp_path: Path,
) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    _seed_offer_slot(admin_store)
    referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-locked-repair",
        candidate_name="Candidate Locked Repair",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        candidate_email="candidate@example.org",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
    director = StaffingService(
        StaffingStore(director_path),
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    director.record_director_interview(
        referral.id,
        director_name="Director One",
        completed_date="2026-07-21",
        rating=9,
        decision="hire",
        decision_notes="Strong fit",
        proposed_shift_start="08:00",
        proposed_shift_end="16:30",
        proposed_classroom="Harmony",
        offer_position_id="teacher",
    )
    event = stage.pending_for(replica="admin")[0]
    stage.acknowledge(event.id, replica="admin")

    with admin_store.write_connection("other_editor"):
        assert admin.replay_staged_changes() == 0

    assert admin.replay_staged_changes() == 1


def test_acknowledged_director_interview_repairs_partial_destination_contact(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    _seed_offer_slot(admin_store)
    referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-contact-repair",
        candidate_name="Candidate Contact Repair",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        candidate_email="wrong@example.org",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
    director = StaffingService(
        StaffingStore(director_path),
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    interview_values = {
        "director_name": "Director One",
        "completed_date": "2026-07-21",
        "rating": 9,
        "decision": "hire",
        "decision_notes": "Strong fit",
        "proposed_shift_start": "8:00 AM",
        "proposed_shift_end": "4:30 PM",
        "proposed_classroom": "Harmony",
        "offer_position_id": "teacher",
    }
    director.record_director_interview(
        referral.id,
        **interview_values,
        candidate_email="correct@example.org",
        candidate_phone="555-123-4567",
    )
    admin_store.record_director_interview(
        referral.id,
        **interview_values,
        candidate_email="wrong@example.org",
        candidate_phone="",
        now="2026-07-21T12:00:00Z",
    )
    event = stage.pending_for(replica="admin")[0]
    stage.acknowledge(event.id, replica="admin")

    assert admin.replay_staged_changes() == 1
    repaired = admin_store.list_director_candidate_referrals(
        school="Palmdale",
        include_completed=True,
    )[0]
    assert (repaired.candidate_email, repaired.candidate_phone) == (
        "correct@example.org",
        "(555) 123-4567",
    )
    assert admin.replay_staged_changes() == 0


def test_acknowledged_director_interview_records_proof_without_duplicate_replay(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    _seed_offer_slot(admin_store)
    referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-proof-repair",
        candidate_name="Candidate Proof Repair",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        candidate_email="candidate@example.org",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
    director = StaffingService(
        StaffingStore(director_path),
        change_stage=stage,
        replica="director:palmdale",
        school_scope="Palmdale",
    )
    admin = StaffingService(admin_store, change_stage=stage, replica="admin")
    interview_values = {
        "director_name": "Director One",
        "completed_date": "2026-07-21",
        "rating": 9,
        "decision": "hire",
        "decision_notes": "Strong fit",
        "proposed_shift_start": "8:00 AM",
        "proposed_shift_end": "4:30 PM",
        "proposed_classroom": "Harmony",
        "offer_position_id": "teacher",
    }
    director.record_director_interview(
        referral.id,
        **interview_values,
        candidate_email="candidate@example.org",
        candidate_phone="555-123-4567",
    )
    admin_store.record_director_interview(
        referral.id,
        **interview_values,
        candidate_email="candidate@example.org",
        candidate_phone="(555) 123-4567",
        now="2026-07-21T12:00:00Z",
    )
    event = stage.pending_for(replica="admin")[0]
    stage.acknowledge(event.id, replica="admin")
    with admin_store.connect() as conn:
        versions_before = int(
            conn.execute("SELECT COUNT(*) FROM director_interview_versions").fetchone()[0]
        )

    assert admin.replay_staged_changes() == 1
    with admin_store.connect() as conn:
        versions_after = int(
            conn.execute("SELECT COUNT(*) FROM director_interview_versions").fetchone()[0]
        )
    assert versions_after == versions_before
    assert admin_store.list_change_delivery_proof_ids(
        operation="record_director_interview"
    ) == {event.id}
    assert admin.replay_staged_changes() == 0


def test_completed_director_interview_replay_preserves_typed_contact_for_hire(tmp_path: Path) -> None:
    admin_path = tmp_path / "staffing_dashboard.sqlite3"
    director_path = tmp_path / "staffing_dashboard_palmdale.sqlite3"
    admin_store = StaffingStore(admin_path)
    admin_store.initialize()
    _seed_offer_slot(admin_store)
    admin_referral = StaffingService(admin_store).upsert_director_candidate_referral(
        history_id="hist-contact-replay",
        candidate_name="Candidate Contact",
        school="Palmdale",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
    )
    shutil.copy2(admin_path, director_path)
    stage = StaffingChangeStage(tmp_path / "staffing_change_events")
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
        offer_position_id="teacher_floater",
        candidate_email="candidate@example.org",
        candidate_phone="555-123-4567",
    )

    assert admin.replay_staged_changes() == 1
    completed = admin.list_completed_director_interviews(school="Palmdale")
    referrals = admin_store.list_director_candidate_referrals(school="Palmdale", include_completed=True)
    assert [(row.history_id, row.decision, row.offer_position_id) for row in completed] == [
        ("hist-contact-replay", "hire", "teacher_floater")
    ]
    assert [(row.candidate_email, row.candidate_phone) for row in referrals] == [
        ("candidate@example.org", "(555) 123-4567")
    ]
