from pathlib import Path
import json

from cross_database_change_stage import CrossDatabaseChangeStage


def test_change_stage_keeps_domains_isolated_in_shared_folder(tmp_path: Path) -> None:
    staffing = CrossDatabaseChangeStage(tmp_path, domain="staffing")
    onboarding = CrossDatabaseChangeStage(tmp_path, domain="onboarding")

    staffing.publish(
        source_replica="admin",
        school="Palmdale",
        operation="update_position",
        payload={"assignment_id": 1},
    )
    onboarding_id = onboarding.publish(
        source_replica="director:palmdale",
        school="Palmdale",
        operation="complete_task",
        payload={"task_id": "task-1"},
    )

    assert staffing.pending_for(replica="director:palmdale", school="Palmdale")[0].domain == "staffing"
    assert [event.id for event in onboarding.pending_for(replica="admin")] == [onboarding_id]


def test_delayed_predecessor_waits_then_replays_in_causal_order(tmp_path: Path) -> None:
    stage = CrossDatabaseChangeStage(tmp_path / "changes", domain="onboarding")
    first_id = stage.publish(
        source_replica="admin", school="Palmdale", operation="first", payload={"value": 1}
    )
    second_id = stage.publish(
        source_replica="admin", school="Palmdale", operation="second", payload={"value": 2}
    )
    first_path = next(
        path for path in (tmp_path / "changes").rglob("event-*.json")
        if first_id in path.read_text(encoding="utf-8")
    )
    held_path = tmp_path / first_path.name
    first_path.replace(held_path)

    assert stage.pending_for(replica="director:palmdale", school="Palmdale") == []

    held_path.replace(first_path)
    assert [event.id for event in stage.pending_for(replica="director:palmdale", school="Palmdale")] == [
        first_id,
        second_id,
    ]


def test_dropbox_conflict_copy_dedupes_identical_and_reports_divergence_without_blocking(tmp_path: Path) -> None:
    stage = CrossDatabaseChangeStage(tmp_path / "changes", domain="onboarding")
    event_id = stage.publish(
        source_replica="admin", school="Palmdale", operation="update", payload={"value": 1}
    )
    original = next((tmp_path / "changes").rglob("event-*.json"))
    conflict_copy = original.with_name(f"{original.stem} (device conflicted copy){original.suffix}")
    conflict_copy.write_bytes(original.read_bytes())

    assert [event.id for event in stage.pending_for(replica="director:palmdale")] == [event_id]

    record = json.loads(conflict_copy.read_text(encoding="utf-8"))
    record["payload"] = {"value": 2}
    conflict_copy.write_text(json.dumps(record), encoding="utf-8")
    assert [event.id for event in stage.pending_for(replica="director:palmdale")] == [event_id]
    [issue] = stage.health_issues()
    assert issue.category == "dropbox_conflict_copy"
    assert issue.artifact_name == conflict_copy.name


def test_corrupted_artifact_is_reported_without_blocking_valid_unrelated_event(tmp_path: Path) -> None:
    stage = CrossDatabaseChangeStage(tmp_path / "changes", domain="onboarding")
    event_id = stage.publish(
        source_replica="admin", school="Palmdale", operation="update", payload={"value": 1}
    )
    corrupt = tmp_path / "changes" / "onboarding" / "outbox" / "broken" / "event-broken.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{broken", encoding="utf-8")

    assert [event.id for event in stage.pending_for(replica="director:palmdale")] == [event_id]
    [issue] = stage.health_issues()
    assert issue.category == "corrupted_artifact"
    assert issue.artifact_name == "event-broken.json"


def test_missing_predecessor_is_visible_as_delayed_health_issue(tmp_path: Path) -> None:
    stage = CrossDatabaseChangeStage(tmp_path / "changes", domain="onboarding")
    stage.publish(source_replica="admin", school="Palmdale", operation="first", payload={})
    second = stage.publish(source_replica="admin", school="Palmdale", operation="second", payload={})
    first_path = next(
        path for path in (tmp_path / "changes").rglob("event-*.json")
        if json.loads(path.read_text(encoding="utf-8"))["event_id"] != second
    )
    first_path.unlink()

    assert stage.pending_for(replica="director:palmdale") == []
    assert stage.health_issues()[0].category == "delayed_predecessor"
