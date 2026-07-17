from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


_SCHEMA_VERSION = 1
_EVENT_KIND = "staffing_change_event"
_RECEIPT_KIND = "staffing_change_receipt"


@dataclass(frozen=True)
class StaffingChangeEvent:
    id: str
    source_replica: str
    source_database: str
    school: str
    operation: str
    payload: dict[str, Any]
    created_at: str
    predecessor_event_id: str = ""


class StaffingChangeStage:
    """Immutable Dropbox-safe staffing event outbox with per-replica receipts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def publish(
        self,
        *,
        source_replica: str,
        source_database: str = "",
        school: str,
        operation: str,
        payload: dict[str, Any],
    ) -> str:
        source = _required_text(source_replica, "Source replica")
        source_db = str(source_database or "").strip() or source
        clean_school = _required_text(school, "School")
        clean_operation = _required_text(operation, "Operation")
        if not isinstance(payload, dict):
            raise ValueError("Staffing change payload must be an object.")
        existing_events, _receipts = self._load_artifacts()
        predecessors = [event for event in existing_events.values() if event.source_replica == source]
        predecessor_event_id = (
            max(predecessors, key=lambda event: (event.created_at, event.id)).id if predecessors else ""
        )
        event_id = uuid4().hex
        created_at = _utc_now_iso()
        record = {
            "kind": _EVENT_KIND,
            "schema_version": _SCHEMA_VERSION,
            "event_id": event_id,
            "source_replica": source,
            "source_database": source_db,
            "school": clean_school,
            "operation": clean_operation,
            "payload": payload,
            "created_at": created_at,
            "predecessor_event_id": predecessor_event_id,
        }
        stamp = created_at.replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")
        source_slug = _safe_slug(source)
        self._write_immutable(Path("outbox") / source_slug / f"event-{stamp}-{event_id}.json", record)
        return event_id

    def pending_for(self, *, replica: str, school: str = "") -> list[StaffingChangeEvent]:
        clean_replica = _required_text(replica, "Replica")
        school_filter = str(school or "").strip()
        events, receipts = self._load_artifacts()
        candidates = [
            event
            for event in events.values()
            if event.source_database != clean_replica
            and (event.id, clean_replica) not in receipts
            and (not school_filter or event.school in {school_filter, "*"})
            and (
                not event.predecessor_event_id
                or event.predecessor_event_id in events
                or (event.predecessor_event_id, clean_replica) in receipts
            )
        ]
        remaining = {event.id: event for event in candidates}
        ordered: list[StaffingChangeEvent] = []
        while remaining:
            ready = [
                event
                for event in remaining.values()
                if not event.predecessor_event_id or event.predecessor_event_id not in remaining
            ]
            if not ready:
                raise ValueError("Cyclic staffing event predecessor chain.")
            for event in sorted(ready, key=lambda item: (item.created_at, item.id)):
                ordered.append(event)
                remaining.pop(event.id)
        return ordered

    def acknowledge(self, event_id: str, *, replica: str) -> None:
        clean_event_id = _required_text(event_id, "Event ID")
        clean_replica = _required_text(replica, "Replica")
        events, receipts = self._load_artifacts()
        if clean_event_id not in events:
            raise ValueError("Staffing change event not found.")
        if (clean_event_id, clean_replica) in receipts:
            return
        receipt_id = uuid4().hex
        record = {
            "kind": _RECEIPT_KIND,
            "schema_version": _SCHEMA_VERSION,
            "receipt_id": receipt_id,
            "event_id": clean_event_id,
            "replica": clean_replica,
            "acknowledged_at": _utc_now_iso(),
        }
        replica_slug = re.sub(r"[^a-z0-9]+", "_", clean_replica.casefold()).strip("_") or "replica"
        self._write_immutable(
            Path("receipts") / replica_slug / f"receipt-{clean_event_id}-{replica_slug}-{receipt_id}.json",
            record,
        )

    def _load_artifacts(self) -> tuple[dict[str, StaffingChangeEvent], set[tuple[str, str]]]:
        if not self.path.exists():
            return {}, set()
        if not self.path.is_dir():
            raise ValueError("Staffing change stage path must be a directory.")
        events: dict[str, StaffingChangeEvent] = {}
        event_records: dict[str, str] = {}
        receipts: set[tuple[str, str]] = set()
        for artifact in sorted(self.path.rglob("*.json")):
            try:
                record = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid staffing change artifact: {artifact.name}") from exc
            if not isinstance(record, dict) or record.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError(f"Invalid staffing change artifact: {artifact.name}")
            kind = str(record.get("kind", ""))
            if kind == _EVENT_KIND:
                event = self._event_from_record(record, artifact.name)
                canonical = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                previous = event_records.get(event.id)
                if previous is not None and previous != canonical:
                    raise ValueError(f"Conflicting staffing event copies: {event.id}")
                events[event.id] = event
                event_records[event.id] = canonical
            elif kind == _RECEIPT_KIND:
                receipts.add(
                    (
                        _required_text(record.get("event_id"), "Event ID"),
                        _required_text(record.get("replica"), "Replica"),
                    )
                )
            else:
                raise ValueError(f"Invalid staffing change artifact: {artifact.name}")
        return events, receipts

    @staticmethod
    def _event_from_record(record: dict[str, Any], filename: str) -> StaffingChangeEvent:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid staffing change event payload: {filename}")
        return StaffingChangeEvent(
            id=_required_text(record.get("event_id"), "Event ID"),
            source_replica=_required_text(record.get("source_replica"), "Source replica"),
            source_database=(
                str(record.get("source_database", "")).strip()
                or _required_text(record.get("source_replica"), "Source replica")
            ),
            school=_required_text(record.get("school"), "School"),
            operation=_required_text(record.get("operation"), "Operation"),
            payload=payload,
            created_at=_required_text(record.get("created_at"), "Created at"),
            predecessor_event_id=str(record.get("predecessor_event_id", "") or "").strip(),
        )

    def _write_immutable(self, relative_path: Path, record: dict[str, Any]) -> None:
        if self.path.exists() and not self.path.is_dir():
            raise ValueError("Staffing change stage path must be a directory.")
        self.path.mkdir(parents=True, exist_ok=True)
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_") or "replica"
