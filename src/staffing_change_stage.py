from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class StaffingChangeEvent:
    id: str
    source_replica: str
    school: str
    operation: str
    payload: dict[str, Any]
    created_at: str


class StaffingChangeStage:
    """Append-only cross-database staffing change journal with per-replica receipts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def publish(
        self,
        *,
        source_replica: str,
        school: str,
        operation: str,
        payload: dict[str, Any],
    ) -> str:
        source = _required_text(source_replica, "Source replica")
        clean_school = _required_text(school, "School")
        clean_operation = _required_text(operation, "Operation")
        if not isinstance(payload, dict):
            raise ValueError("Staffing change payload must be an object.")
        payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        event_id = uuid4().hex
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staffing_change_events (
                    event_id, source_replica, school, operation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, source, clean_school, clean_operation, payload_json, created_at),
            )
        return event_id

    def pending_for(self, *, replica: str, school: str = "") -> list[StaffingChangeEvent]:
        clean_replica = _required_text(replica, "Replica")
        school_filter = str(school or "").strip()
        clauses = ["e.source_replica != ?", "r.event_id IS NULL"]
        params: list[Any] = [clean_replica, clean_replica]
        if school_filter:
            clauses.append("e.school IN (?, '*')")
            params.append(school_filter)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.event_id, e.source_replica, e.school, e.operation, e.payload_json, e.created_at
                FROM staffing_change_events e
                LEFT JOIN staffing_change_receipts r
                  ON r.event_id = e.event_id AND r.replica = ?
                WHERE {' AND '.join(clauses)}
                ORDER BY e.rowid
                """,
                tuple(params),
            ).fetchall()
        events: list[StaffingChangeEvent] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                raise ValueError("Stored staffing change payload must be an object.")
            events.append(
                StaffingChangeEvent(
                    id=str(row["event_id"]),
                    source_replica=str(row["source_replica"]),
                    school=str(row["school"]),
                    operation=str(row["operation"]),
                    payload=payload,
                    created_at=str(row["created_at"]),
                )
            )
        return events

    def acknowledge(self, event_id: str, *, replica: str) -> None:
        clean_event_id = _required_text(event_id, "Event ID")
        clean_replica = _required_text(replica, "Replica")
        acknowledged_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM staffing_change_events WHERE event_id = ?",
                (clean_event_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("Staffing change event not found.")
            conn.execute(
                """
                INSERT OR IGNORE INTO staffing_change_receipts (event_id, replica, acknowledged_at)
                VALUES (?, ?, ?)
                """,
                (clean_event_id, clean_replica, acknowledged_at),
            )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS staffing_change_events (
                event_id TEXT PRIMARY KEY,
                source_replica TEXT NOT NULL,
                school TEXT NOT NULL,
                operation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staffing_change_receipts (
                event_id TEXT NOT NULL REFERENCES staffing_change_events(event_id) ON DELETE CASCADE,
                replica TEXT NOT NULL,
                acknowledged_at TEXT NOT NULL,
                PRIMARY KEY (event_id, replica)
            );
            CREATE INDEX IF NOT EXISTS idx_staffing_change_events_school_created
                ON staffing_change_events(school, created_at);
            """
        )
        return conn


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text
