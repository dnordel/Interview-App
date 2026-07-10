from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA_LOCK = threading.Lock()


class StaffingReferralQueueStore:
    def __init__(self, path: Path, *, legacy_jsonl_path: Path | None = None) -> None:
        self.path = Path(path)
        self.legacy_jsonl_path = Path(legacy_jsonl_path) if legacy_jsonl_path is not None else None
        self._legacy_import_checked = False

    def append(self, payload: dict[str, Any], *, operation: str = "director_candidate_referral") -> None:
        self._ensure_legacy_imported()
        normalized_operation = str(operation or "director_candidate_referral").strip() or "director_candidate_referral"
        school = str(payload.get("school", "") if isinstance(payload, dict) else "").strip()
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO staffing_referral_events (operation, school, payload_json, queued_at, consumed_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    normalized_operation,
                    school,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    _utc_now_iso(),
                ),
            )
            conn.commit()

    def pop_for_school(self, school: str) -> list[dict[str, Any]]:
        self._ensure_legacy_imported()
        school_filter = str(school or "").strip()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, operation, payload_json
                FROM staffing_referral_events
                WHERE consumed_at IS NULL
                  AND (? = '' OR school = ?)
                ORDER BY id
                """,
                (school_filter, school_filter),
            ).fetchall()
            consumed_at = _utc_now_iso()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE staffing_referral_events SET consumed_at = ? WHERE id IN ({placeholders})",
                    (consumed_at, *ids),
                )
            conn.commit()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            payload = dict(payload)
            payload["_operation"] = str(row["operation"] or "director_candidate_referral")
            payloads.append(payload)
        return payloads

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        with _SCHEMA_LOCK:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS staffing_referral_events (
                    id INTEGER PRIMARY KEY,
                    operation TEXT NOT NULL,
                    school TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_staffing_referral_events_school_unconsumed
                ON staffing_referral_events(school, consumed_at, id)
                """
            )
            conn.commit()
        return conn

    def _ensure_legacy_imported(self) -> None:
        if self._legacy_import_checked:
            return
        self._legacy_import_checked = True
        legacy_path = self.legacy_jsonl_path
        if legacy_path is None or not legacy_path.exists():
            return
        imported_path = legacy_path.with_suffix(legacy_path.suffix + ".imported")
        if imported_path.exists():
            return
        import_lock_path = legacy_path.with_suffix(legacy_path.suffix + ".importing")
        try:
            fd = os.open(str(import_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(json.dumps({"started_at": _utc_now_iso(), "source": str(legacy_path)}, ensure_ascii=True))
            records = self._read_legacy_records(legacy_path)
            if records:
                with closing(self.connect()) as conn:
                    conn.executemany(
                        """
                        INSERT INTO staffing_referral_events (operation, school, payload_json, queued_at, consumed_at)
                        VALUES (?, ?, ?, ?, NULL)
                        """,
                        records,
                    )
                    conn.commit()
            imported_path.write_text(
                json.dumps({"imported_at": _utc_now_iso(), "source": str(legacy_path)}, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
        finally:
            try:
                import_lock_path.unlink()
            except FileNotFoundError:
                pass

    def _read_legacy_records(self, legacy_path: Path) -> list[tuple[str, str, str, str]]:
        records: list[tuple[str, str, str, str]] = []
        for line in legacy_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            operation = str(record.get("operation") or "director_candidate_referral").strip()
            school = str(payload.get("school", "")).strip()
            queued_at = str(record.get("queued_at") or _utc_now_iso()).strip() or _utc_now_iso()
            records.append((operation, school, json.dumps(payload, ensure_ascii=True, sort_keys=True), queued_at))
        return records


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
