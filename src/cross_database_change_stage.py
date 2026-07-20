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


@dataclass(frozen=True)
class CrossDatabaseChangeEvent:
    id: str
    domain: str
    source_replica: str
    source_database: str
    school: str
    operation: str
    payload: dict[str, Any]
    created_at: str
    predecessor_event_id: str = ""


@dataclass(frozen=True)
class CrossDatabaseStageIssue:
    category: str
    artifact_name: str
    detail: str


class CrossDatabaseChangeStage:
    """Immutable Dropbox-safe event outbox shared by replicated DB domains."""

    def __init__(self, path: Path, *, domain: str) -> None:
        self.path = Path(path)
        self.domain = _required_text(domain, "Change domain").casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.domain):
            raise ValueError("Change domain must use lowercase letters, numbers, and underscores.")
        self._health_issues: list[CrossDatabaseStageIssue] = []

    def health_issues(self) -> tuple[CrossDatabaseStageIssue, ...]:
        self._load_artifacts()
        return tuple(self._health_issues)

    @property
    def event_kind(self) -> str:
        return f"{self.domain}_change_event"

    @property
    def receipt_kind(self) -> str:
        return f"{self.domain}_change_receipt"

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
            raise ValueError(f"{self.domain.title()} change payload must be an object.")
        existing_events, _receipts = self._load_artifacts()
        predecessors = [event for event in existing_events.values() if event.source_replica == source]
        predecessor_event_id = (
            max(predecessors, key=lambda event: (event.created_at, event.id)).id if predecessors else ""
        )
        event_id = uuid4().hex
        created_at = _utc_now_iso()
        record = {
            "kind": self.event_kind,
            "schema_version": _SCHEMA_VERSION,
            "event_id": event_id,
            "domain": self.domain,
            "source_replica": source,
            "source_database": source_db,
            "school": clean_school,
            "operation": clean_operation,
            "payload": payload,
            "created_at": created_at,
            "predecessor_event_id": predecessor_event_id,
        }
        stamp = created_at.replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")
        self._write_immutable(Path(self.domain) / "outbox" / _safe_slug(source) / f"event-{stamp}-{event_id}.json", record)
        return event_id

    def pending_for(self, *, replica: str, school: str = "") -> list[CrossDatabaseChangeEvent]:
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
        ordered: list[CrossDatabaseChangeEvent] = []
        while remaining:
            ready = [
                event
                for event in remaining.values()
                if not event.predecessor_event_id or event.predecessor_event_id not in remaining
            ]
            if not ready:
                raise ValueError(f"Cyclic {self.domain} event predecessor chain.")
            for event in sorted(ready, key=lambda item: (item.created_at, item.id)):
                ordered.append(event)
                remaining.pop(event.id)
        return ordered

    def acknowledge(self, event_id: str, *, replica: str) -> None:
        clean_event_id = _required_text(event_id, "Event ID")
        clean_replica = _required_text(replica, "Replica")
        events, receipts = self._load_artifacts()
        if clean_event_id not in events:
            raise ValueError(f"{self.domain.title()} change event not found.")
        if (clean_event_id, clean_replica) in receipts:
            return
        receipt_id = uuid4().hex
        record = {
            "kind": self.receipt_kind,
            "schema_version": _SCHEMA_VERSION,
            "domain": self.domain,
            "receipt_id": receipt_id,
            "event_id": clean_event_id,
            "replica": clean_replica,
            "acknowledged_at": _utc_now_iso(),
        }
        replica_slug = _safe_slug(clean_replica)
        self._write_immutable(
            Path(self.domain) / "receipts" / replica_slug / f"receipt-{clean_event_id}-{replica_slug}-{receipt_id}.json",
            record,
        )

    def _load_artifacts(self) -> tuple[dict[str, CrossDatabaseChangeEvent], set[tuple[str, str]]]:
        self._health_issues = []
        if not self.path.exists():
            return {}, set()
        if not self.path.is_dir():
            raise ValueError(f"{self.domain.title()} change stage path must be a directory.")
        events: dict[str, CrossDatabaseChangeEvent] = {}
        event_records: dict[str, str] = {}
        event_artifacts: dict[str, Path] = {}
        receipts: set[tuple[str, str]] = set()
        domain_root = self.path / self.domain
        search_root = domain_root if domain_root.exists() else self.path
        for artifact in sorted(search_root.rglob("*.json")):
            try:
                record = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._health_issues.append(CrossDatabaseStageIssue(
                    category="corrupted_artifact", artifact_name=artifact.name,
                    detail="Artifact is unreadable or is not valid JSON.",
                ))
                continue
            if not isinstance(record, dict) or record.get("schema_version") != _SCHEMA_VERSION:
                self._health_issues.append(CrossDatabaseStageIssue(
                    category="corrupted_artifact", artifact_name=artifact.name,
                    detail="Artifact schema is invalid.",
                ))
                continue
            kind = str(record.get("kind", ""))
            if kind == self.event_kind:
                try:
                    event = self._event_from_record(record, artifact.name)
                except ValueError:
                    self._health_issues.append(CrossDatabaseStageIssue(
                        category="corrupted_artifact", artifact_name=artifact.name,
                        detail="Event fields or payload are invalid.",
                    ))
                    continue
                canonical = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                previous = event_records.get(event.id)
                if previous is not None and previous != canonical:
                    previous_artifact = event_artifacts[event.id]
                    current_is_conflict = "conflicted copy" in artifact.name.casefold()
                    previous_is_conflict = "conflicted copy" in previous_artifact.name.casefold()
                    issue_artifact = artifact if current_is_conflict or not previous_is_conflict else previous_artifact
                    self._health_issues.append(CrossDatabaseStageIssue(
                        category="dropbox_conflict_copy",
                        artifact_name=issue_artifact.name,
                        detail="Divergent event copy ignored; canonical event remains pending.",
                    ))
                    if current_is_conflict or not previous_is_conflict:
                        continue
                events[event.id] = event
                event_records[event.id] = canonical
                event_artifacts[event.id] = artifact
            elif kind == self.receipt_kind:
                try:
                    receipts.add((_required_text(record.get("event_id"), "Event ID"), _required_text(record.get("replica"), "Replica")))
                except ValueError:
                    self._health_issues.append(CrossDatabaseStageIssue(
                        category="corrupted_artifact", artifact_name=artifact.name,
                        detail="Receipt fields are invalid.",
                    ))
            elif search_root == self.path:
                continue
            else:
                self._health_issues.append(CrossDatabaseStageIssue(
                    category="corrupted_artifact", artifact_name=artifact.name,
                    detail="Artifact kind is invalid.",
                ))
        for event in events.values():
            if event.predecessor_event_id and event.predecessor_event_id not in events:
                self._health_issues.append(CrossDatabaseStageIssue(
                    category="delayed_predecessor",
                    artifact_name=event_artifacts[event.id].name,
                    detail="Predecessor event is not available yet; replay is deferred.",
                ))
        return events, receipts

    def _event_from_record(self, record: dict[str, Any], filename: str) -> CrossDatabaseChangeEvent:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid {self.domain} change event payload: {filename}")
        return CrossDatabaseChangeEvent(
            id=_required_text(record.get("event_id"), "Event ID"),
            domain=self.domain,
            source_replica=_required_text(record.get("source_replica"), "Source replica"),
            source_database=str(record.get("source_database", "")).strip() or _required_text(record.get("source_replica"), "Source replica"),
            school=_required_text(record.get("school"), "School"),
            operation=_required_text(record.get("operation"), "Operation"),
            payload=payload,
            created_at=_required_text(record.get("created_at"), "Created at"),
            predecessor_event_id=str(record.get("predecessor_event_id", "") or "").strip(),
        )

    def _write_immutable(self, relative_path: Path, record: dict[str, Any]) -> None:
        if self.path.exists() and not self.path.is_dir():
            raise ValueError(f"{self.domain.title()} change stage path must be a directory.")
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
