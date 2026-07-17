from __future__ import annotations

import json
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence
from uuid import uuid4


ReportRole = Literal["admin", "director"]
ReportState = Literal["draft", "finalized", "reopened"]


class CandidateReportError(ValueError):
    """Base error for candidate-report lifecycle operations."""


class CandidateReportNotFoundError(CandidateReportError):
    """Raised when no structured report exists for a history id."""


class CandidateReportPermissionError(CandidateReportError):
    """Raised when a role attempts a forbidden report mutation."""


class CandidateReportStaleError(CandidateReportError):
    """Raised when optimistic concurrency detects a newer saved report."""


class CandidateReportValidationError(CandidateReportError):
    def __init__(self, issues: list["CandidateReportValidationIssue"]):
        super().__init__("Candidate report has blocking validation issues.")
        self.issues = list(issues)


@dataclass(frozen=True)
class CandidateReportValidationIssue:
    severity: Literal["blocking", "warning", "information"]
    code: str
    message: str
    field_path: str = ""


@dataclass(frozen=True)
class CandidateReportDifference:
    field_path: str
    saved_value: Any
    current_value: Any
    local_value: Any


@dataclass(frozen=True)
class CandidateReportRecord:
    history_id: str
    state: ReportState
    snapshot: dict[str, Any]
    row_version: int
    version_number: int
    updated_by: str
    updated_role: ReportRole
    updated_at: str
    reopen_reason: str = ""


@dataclass(frozen=True)
class CandidateReportVersion:
    revision_id: str
    history_id: str
    version_number: int
    state: ReportState
    snapshot: dict[str, Any]
    actor: str
    actor_role: ReportRole
    reason: str
    created_at: str


@dataclass(frozen=True)
class CandidateReportAuditEvent:
    event_id: int
    history_id: str
    revision_id: str
    action: str
    field_path: str
    old_value: Any
    new_value: Any
    actor: str
    actor_role: ReportRole
    reason: str
    source: str
    app_version: str
    created_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def export_candidate_report_audit_csv(events: Sequence[dict[str, Any]], destination: Path) -> Path:
    path = Path(destination)
    if path.suffix.casefold() != ".csv":
        raise CandidateReportError("Audit export destination must be a CSV file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "Date & Time", "Version", "User", "Role", "Action", "Field",
        "Old Value", "New Value", "Reason", "Source", "Revision ID",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for event in events:
            def display(key: str) -> str:
                value = event.get(key, "")
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False, sort_keys=True)
                return str(value if value is not None else "Not captured")

            writer.writerow(
                {
                    "Date & Time": display("created_at"), "Version": display("version"),
                    "User": display("actor"), "Role": display("actor_role"),
                    "Action": display("action"), "Field": display("field_path"),
                    "Old Value": display("old_value"), "New Value": display("new_value"),
                    "Reason": display("reason"), "Source": display("source"),
                    "Revision ID": display("revision_id"),
                }
            )
    return path


def build_candidate_report_snapshot(
    payload: dict[str, Any],
    scoring: dict[str, Any],
    history_entry: dict[str, Any],
    *,
    report_path: str = "",
) -> dict[str, Any]:
    """Build canonical structured report data from one finalized interview."""

    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    qualification = candidate.get("qualification") if isinstance(candidate.get("qualification"), dict) else {}
    score_rows = scoring.get("rows") if isinstance(scoring.get("rows"), list) else []
    scores_by_id: dict[str, dict[str, Any]] = {}
    for row in score_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("trait_id") or row.get("id") or "").strip()
        if key:
            scores_by_id[key] = dict(row)

    questions: list[dict[str, Any]] = []
    flow = payload.get("flow_transcript") if isinstance(payload.get("flow_transcript"), list) else []
    for index, item in enumerate(flow):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() == "intro":
            continue
        question_id = str(item.get("id") or item.get("question_id") or index).strip()
        score_row = scores_by_id.get(question_id, {})
        transcript = str(item.get("candidate_transcript") or "")
        questions.append(
            {
                "flow_index": int(item.get("flow_index", index) or index),
                "question_id": question_id,
                "type": str(item.get("type") or ""),
                "title": str(item.get("title") or ""),
                "prompt": str(item.get("question") or item.get("prompt") or ""),
                "transcript": transcript,
                "original_transcript": transcript,
                "interviewer_notes": str(
                    score_row.get("question_notes")
                    or score_row.get("verbatim_notes")
                    or item.get("evaluator_notes")
                    or ""
                ),
                "trait_notes": str(score_row.get("trait_notes") or ""),
                "rating": score_row.get("final_raw_score", score_row.get("raw_score")),
                "weight": score_row.get("weight"),
                "weighted_score": score_row.get("weighted_score"),
                "priority": str(score_row.get("priority") or ""),
                "skipped": bool(item.get("skipped", score_row.get("skipped", False))),
                "skip_reason": str(item.get("skip_reason") or score_row.get("skip_reason") or ""),
                "absolute_disqualifier": bool(score_row.get("absolute_disqualifier", False)),
                "no_example_after_followups": bool(score_row.get("no_example_after_followups", False)),
            }
        )

    summaries = {
        "executive_summary": str(payload.get("executive_summary") or ""),
        "strengths": list(payload.get("interview_highlights") or payload.get("strengths") or []),
        "concerns": list(payload.get("concerns") or payload.get("concerns_and_risks") or []),
        "follow_up_items": list(payload.get("follow_up_items") or []),
        "recommendation_rationale": str(payload.get("recommendation_rationale") or ""),
        "review_needed": False,
    }
    return {
        "schema_version": 1,
        "history_id": str(history_entry.get("history_id") or ""),
        "candidate": {
            **dict(candidate),
            "candidate_name": str(
                candidate.get("candidate_name")
                or candidate.get("name")
                or history_entry.get("candidate_name")
                or ""
            ),
            "interview_date": str(candidate.get("interview_date") or history_entry.get("interview_date") or ""),
            "school": str(candidate.get("school") or history_entry.get("school") or ""),
            "track": str(candidate.get("track") or history_entry.get("track") or ""),
            "qualification": dict(qualification),
        },
        "questions": questions,
        "scoring": json.loads(json.dumps(scoring, default=str)),
        "summaries": summaries,
        "rubric_snapshot": payload.get("rubric_snapshot", {}),
        "report_path": str(report_path or history_entry.get("interview_notes_path") or ""),
        "finalized_at": str(history_entry.get("saved_at") or utc_now()),
    }


def validate_candidate_report(snapshot: dict[str, Any]) -> list[CandidateReportValidationIssue]:
    issues: list[CandidateReportValidationIssue] = []
    questions = snapshot.get("questions") if isinstance(snapshot.get("questions"), list) else []
    for index, question in enumerate(questions):
        if not isinstance(question, dict) or str(question.get("type") or "") != "trait":
            continue
        path = f"questions.{index}"
        if question.get("skipped"):
            if not str(question.get("skip_reason") or "").strip():
                issues.append(
                    CandidateReportValidationIssue(
                        "blocking", "missing_skip_reason", "Skipped scored question requires a reason.", f"{path}.skip_reason"
                    )
                )
            continue
        rating = question.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or int(rating) != rating or not 1 <= int(rating) <= 5:
            issues.append(
                CandidateReportValidationIssue(
                    "blocking", "invalid_rating", "Scored question requires an integer rating from 1 to 5.", f"{path}.rating"
                )
            )
        if question.get("absolute_disqualifier") and not str(question.get("interviewer_notes") or "").strip():
            issues.append(
                CandidateReportValidationIssue(
                    "blocking",
                    "disqualifier_without_evidence",
                    "Absolute disqualifier requires supporting interviewer notes.",
                    f"{path}.interviewer_notes",
                )
            )
    summaries = snapshot.get("summaries") if isinstance(snapshot.get("summaries"), dict) else {}
    if bool(summaries.get("review_needed")):
        issues.append(
            CandidateReportValidationIssue(
                "warning", "narrative_review_needed", "Narrative fields may be stale after evaluation changes.", "summaries"
            )
        )
    return issues


def recalculate_candidate_report(
    snapshot: dict[str, Any],
    *,
    rubric: dict[str, Any],
    track_key: str,
) -> dict[str, Any]:
    """Return a report copy recalculated by the canonical scoring engine."""

    from scoring_reporting import ScoringEngine

    updated = json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))
    questions = updated.get("questions") if isinstance(updated.get("questions"), list) else []
    trait_inputs: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict) or str(question.get("type") or "") != "trait":
            continue
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            continue
        trait_inputs[question_id] = {
            "raw_score": None if question.get("skipped") else question.get("rating"),
            "question_notes": str(question.get("interviewer_notes") or ""),
            "trait_notes": str(question.get("trait_notes") or ""),
            "verbatim_notes": str(question.get("interviewer_notes") or ""),
            "absolute_disqualifier": bool(question.get("absolute_disqualifier", False)),
            "no_example_after_followups": bool(question.get("no_example_after_followups", False)),
            "skipped": bool(question.get("skipped", False)),
            "skip_reason": str(question.get("skip_reason") or ""),
        }
    scoring = ScoringEngine.evaluate(rubric, track_key, trait_inputs)
    updated["scoring"] = scoring
    rows_by_id = {
        str(row.get("trait_id") or row.get("id") or ""): row
        for row in scoring.get("rows", [])
        if isinstance(row, dict)
    }
    for question in questions:
        if not isinstance(question, dict):
            continue
        row = rows_by_id.get(str(question.get("question_id") or ""))
        if row is None:
            continue
        question["weight"] = row.get("weight")
        question["weighted_score"] = row.get("weighted_score")
        question["priority"] = str(row.get("priority") or question.get("priority") or "")
    summaries = updated.get("summaries") if isinstance(updated.get("summaries"), dict) else {}
    updated["summaries"] = {**summaries, "review_needed": True}
    return updated


def resolve_legacy_report_path(
    history_path: Path,
    history_id: str,
    *,
    school_scope: str = "",
) -> Path:
    """Resolve one stored legacy DOCX after history-id and school authorization checks."""

    source = Path(history_path)
    db_path = source if source.suffix.casefold() == ".sqlite3" else source.with_suffix(".sqlite3")
    if not db_path.is_file():
        raise CandidateReportNotFoundError("Interview history database not found.")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM interview_history WHERE row_key = ? OR history_id = ? LIMIT 1",
            (str(history_id or "").strip(), str(history_id or "").strip()),
        ).fetchone()
    if row is None:
        raise CandidateReportNotFoundError("Legacy candidate report not found.")
    payload = CandidateReportRepository._decode_object(row[0])
    clean_scope = str(school_scope or "").strip()
    report_school = str(payload.get("school") or "").strip()
    if clean_scope and clean_scope.casefold() != report_school.casefold():
        raise CandidateReportPermissionError("Candidate report is outside the director school scope.")
    path_text = next(
        (
            str(payload.get(key) or "").strip()
            for key in ("interview_notes_path", "saved_report_path", "report_path", "notes_path")
            if str(payload.get(key) or "").strip()
        ),
        "",
    )
    path = Path(path_text)
    if path.suffix.casefold() != ".docx" or not path.is_file():
        raise CandidateReportNotFoundError("Saved legacy Word report is missing or invalid.")
    return path.resolve()


class CandidateReportRepository:
    """Versioned structured candidate reports stored beside interview history."""

    def __init__(self, history_path: Path):
        path = Path(history_path)
        self.db_path = path if path.suffix.casefold() == ".sqlite3" else path.with_suffix(".sqlite3")

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            self.initialize_connection(conn)
            conn.commit()

    @staticmethod
    def initialize_connection(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidate_reports (
                history_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                working_snapshot_json TEXT NOT NULL,
                finalized_snapshot_json TEXT NOT NULL,
                row_version INTEGER NOT NULL DEFAULT 1,
                version_number INTEGER NOT NULL DEFAULT 1,
                reopen_reason TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL,
                updated_role TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_report_versions (
                revision_id TEXT PRIMARY KEY,
                history_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                state TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(history_id, version_number)
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_report_versions_history
                ON candidate_report_versions(history_id, version_number DESC);
            CREATE TABLE IF NOT EXISTS candidate_report_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                history_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                action TEXT NOT NULL,
                field_path TEXT NOT NULL DEFAULT '',
                old_value_json TEXT NOT NULL DEFAULT 'null',
                new_value_json TEXT NOT NULL DEFAULT 'null',
                actor TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                app_version TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_report_audit_history
                ON candidate_report_audit_events(history_id, event_id DESC);
            """
        )

    @classmethod
    def insert_initial_on_connection(
        cls,
        conn: sqlite3.Connection,
        history_id: str,
        snapshot: dict[str, Any],
        *,
        actor: str,
        actor_role: ReportRole = "admin",
        app_version: str = "",
    ) -> None:
        cls._require_role(actor_role)
        key = cls._required(history_id, "History id")
        now = utc_now()
        revision_id = str(uuid4())
        encoded = cls._json(snapshot)
        conn.execute(
            """
            INSERT INTO candidate_reports (
                history_id, state, working_snapshot_json, finalized_snapshot_json,
                row_version, version_number, reopen_reason, updated_by, updated_role, updated_at
            ) VALUES (?, 'finalized', ?, ?, 1, 1, '', ?, ?, ?)
            """,
            (key, encoded, encoded, cls._actor(actor), actor_role, now),
        )
        cls._insert_version(conn, revision_id, key, 1, "finalized", snapshot, actor, actor_role, "", now)
        cls._insert_audit(conn, key, revision_id, "report_created", "", None, snapshot, actor, actor_role, "", "interviewer", app_version, now)

    def exists(self, history_id: str) -> bool:
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM candidate_reports WHERE history_id = ?", (str(history_id),)).fetchone()
        return row is not None

    def load_visible_version(self, history_id: str, *, role: ReportRole, school_scope: str = "") -> CandidateReportRecord:
        self._require_role(role)
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM candidate_reports WHERE history_id = ?", (str(history_id),)).fetchone()
        if row is None:
            raise CandidateReportNotFoundError("Structured candidate report not found.")
        working = self._decode_object(row["working_snapshot_json"])
        finalized = self._decode_object(row["finalized_snapshot_json"])
        snapshot = finalized if role == "director" else working
        clean_school = str(school_scope or "").strip()
        report_school = str((snapshot.get("candidate") or {}).get("school") or "").strip()
        if clean_school and report_school.casefold() != clean_school.casefold():
            raise CandidateReportPermissionError("Candidate report is outside the director school scope.")
        return CandidateReportRecord(
            history_id=str(row["history_id"]),
            state=str(row["state"]),
            snapshot=snapshot,
            row_version=int(row["row_version"]),
            version_number=int(row["version_number"]),
            updated_by=str(row["updated_by"]),
            updated_role=str(row["updated_role"]),
            updated_at=str(row["updated_at"]),
            reopen_reason=str(row["reopen_reason"]),
        )

    def reopen(
        self,
        history_id: str,
        *,
        expected_row_version: int,
        reason: str,
        actor: str,
        role: ReportRole,
        app_version: str = "",
    ) -> CandidateReportRecord:
        if role != "admin":
            raise CandidateReportPermissionError("Only admin may reopen an initial interview report.")
        clean_reason = self._required(reason, "Reopen reason")
        return self._save_state(
            history_id,
            snapshot=None,
            expected_row_version=expected_row_version,
            state="reopened",
            action="report_reopened",
            actor=actor,
            role=role,
            reason=clean_reason,
            app_version=app_version,
        )

    def save_draft(
        self,
        history_id: str,
        snapshot: dict[str, Any],
        *,
        expected_row_version: int,
        actor: str,
        role: ReportRole,
        reason: str = "",
        force: bool = False,
        app_version: str = "",
    ) -> CandidateReportRecord:
        if role != "admin":
            raise CandidateReportPermissionError("Only admin may edit an initial interview report.")
        return self._save_state(
            history_id,
            snapshot=snapshot,
            expected_row_version=expected_row_version,
            state="reopened",
            action="draft_saved",
            actor=actor,
            role=role,
            reason=reason,
            force=force,
            app_version=app_version,
        )

    def save_changes(
        self,
        history_id: str,
        snapshot: dict[str, Any],
        *,
        expected_row_version: int,
        actor: str,
        role: ReportRole,
        reason: str = "",
        force: bool = False,
        app_version: str = "",
    ) -> CandidateReportRecord:
        if role != "admin":
            raise CandidateReportPermissionError("Only admin may edit an initial interview report.")
        blocking = [issue for issue in validate_candidate_report(snapshot) if issue.severity == "blocking"]
        if blocking:
            raise CandidateReportValidationError(blocking)
        return self._save_state(
            history_id,
            snapshot=snapshot,
            expected_row_version=expected_row_version,
            state="reopened",
            action="changes_saved",
            actor=actor,
            role=role,
            reason=reason,
            force=force,
            app_version=app_version,
        )

    def finalize(
        self,
        history_id: str,
        snapshot: dict[str, Any],
        *,
        expected_row_version: int,
        actor: str,
        role: ReportRole,
        reason: str = "",
        force: bool = False,
        app_version: str = "",
    ) -> CandidateReportRecord:
        if role != "admin":
            raise CandidateReportPermissionError("Only admin may finalize an initial interview report.")
        issues = validate_candidate_report(snapshot)
        blocking = [issue for issue in issues if issue.severity == "blocking"]
        if blocking:
            raise CandidateReportValidationError(blocking)
        return self._save_state(
            history_id,
            snapshot=snapshot,
            expected_row_version=expected_row_version,
            state="finalized",
            action="report_finalized",
            actor=actor,
            role=role,
            reason=reason,
            force=force,
            app_version=app_version,
        )

    def compare_version(
        self,
        history_id: str,
        local_snapshot: dict[str, Any],
        *,
        role: ReportRole,
        saved_snapshot: dict[str, Any] | None = None,
    ) -> list[CandidateReportDifference]:
        current = self.load_visible_version(history_id, role=role)
        saved = dict(saved_snapshot) if saved_snapshot is not None else current.snapshot
        paths = {
            path
            for path, _old, _new in [*self._diff(saved, current.snapshot), *self._diff(saved, local_snapshot)]
        }
        return [
            CandidateReportDifference(
                field_path=path,
                saved_value=self._value_at_path(saved, path),
                current_value=self._value_at_path(current.snapshot, path),
                local_value=self._value_at_path(local_snapshot, path),
            )
            for path in sorted(paths)
        ]

    def sync_report_path(
        self,
        history_id: str,
        report_path: Path,
        *,
        app_version: str = "",
    ) -> CandidateReportRecord:
        path = Path(report_path)
        if path.suffix.casefold() != ".docx" or not path.is_file():
            raise CandidateReportValidationError(
                [CandidateReportValidationIssue("blocking", "invalid_report_path", "Generated Word report is missing or invalid.", "report_path")]
            )
        self.initialize()
        key = self._required(history_id, "History id")
        resolved = str(path.resolve())
        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidate_reports WHERE history_id = ?", (key,)).fetchone()
            if row is None:
                raise CandidateReportNotFoundError("Structured candidate report not found.")
            working = self._decode_object(row["working_snapshot_json"])
            finalized = self._decode_object(row["finalized_snapshot_json"])
            old_path = finalized.get("report_path")
            working["report_path"] = resolved
            finalized["report_path"] = resolved
            version_number = int(row["version_number"]) + 1
            revision_id = str(uuid4())
            conn.execute(
                """
                UPDATE candidate_reports
                SET working_snapshot_json = ?, finalized_snapshot_json = ?, row_version = row_version + 1,
                    version_number = ?, updated_by = 'system', updated_role = 'admin', updated_at = ?
                WHERE history_id = ?
                """,
                (self._json(working), self._json(finalized), version_number, now, key),
            )
            self._insert_version(
                conn, revision_id, key, version_number, str(row["state"]),
                working, "system", "admin", "Generated Word report updated", now,
            )
            self._insert_audit(
                conn, key, revision_id, "report_document_updated", "report_path", old_path, resolved,
                "system", "admin", "Generated Word report updated", "calculated", app_version, now,
            )
            conn.commit()
        return self.load_visible_version(key, role="admin")

    def sync_imported_transcripts(
        self,
        history_id: str,
        transcripts_by_question_id: dict[str, str],
        *,
        app_version: str = "",
    ) -> CandidateReportRecord:
        clean_transcripts = {
            str(question_id).strip(): str(transcript).strip()
            for question_id, transcript in transcripts_by_question_id.items()
            if str(question_id).strip() and str(transcript).strip()
        }
        self.initialize()
        key = self._required(history_id, "History id")
        if not clean_transcripts:
            return self.load_visible_version(key, role="admin")

        def apply_transcripts(snapshot: dict[str, Any]) -> set[str]:
            changed: set[str] = set()
            questions = snapshot.get("questions") if isinstance(snapshot.get("questions"), list) else []
            for question in questions:
                if not isinstance(question, dict):
                    continue
                question_id = str(question.get("question_id") or "").strip()
                transcript = clean_transcripts.get(question_id)
                if transcript is None or str(question.get("transcript") or "") == transcript:
                    continue
                question["transcript"] = transcript
                question["original_transcript"] = transcript
                changed.add(question_id)
            return changed

        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidate_reports WHERE history_id = ?", (key,)).fetchone()
            if row is None:
                raise CandidateReportNotFoundError("Structured candidate report not found.")
            working = self._decode_object(row["working_snapshot_json"])
            finalized = self._decode_object(row["finalized_snapshot_json"])
            changed_ids = apply_transcripts(working) | apply_transcripts(finalized)
            if not changed_ids:
                conn.rollback()
                return self.load_visible_version(key, role="admin")
            version_number = int(row["version_number"]) + 1
            revision_id = str(uuid4())
            reason = "Imported interview transcripts synchronized"
            conn.execute(
                """
                UPDATE candidate_reports
                SET working_snapshot_json = ?, finalized_snapshot_json = ?, row_version = row_version + 1,
                    version_number = ?, updated_by = 'system', updated_role = 'admin', updated_at = ?
                WHERE history_id = ?
                """,
                (self._json(working), self._json(finalized), version_number, now, key),
            )
            self._insert_version(
                conn, revision_id, key, version_number, str(row["state"]),
                working, "system", "admin", reason, now,
            )
            self._insert_audit(
                conn, key, revision_id, "report_transcripts_imported", "questions", None,
                {"question_ids": sorted(changed_ids), "count": len(changed_ids)},
                "system", "admin", reason, "interviewer", app_version, now,
            )
            conn.commit()
        return self.load_visible_version(key, role="admin")

    def list_versions(self, history_id: str) -> list[CandidateReportVersion]:
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM candidate_report_versions WHERE history_id = ? ORDER BY version_number DESC",
                (str(history_id),),
            ).fetchall()
        return [
            CandidateReportVersion(
                revision_id=str(row["revision_id"]), history_id=str(row["history_id"]),
                version_number=int(row["version_number"]), state=str(row["state"]),
                snapshot=self._decode_object(row["snapshot_json"]), actor=str(row["actor"]),
                actor_role=str(row["actor_role"]), reason=str(row["reason"]), created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def list_audit_events(self, history_id: str) -> list[CandidateReportAuditEvent]:
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM candidate_report_audit_events WHERE history_id = ? ORDER BY event_id DESC",
                (str(history_id),),
            ).fetchall()
        return [
            CandidateReportAuditEvent(
                event_id=int(row["event_id"]), history_id=str(row["history_id"]), revision_id=str(row["revision_id"]),
                action=str(row["action"]), field_path=str(row["field_path"]),
                old_value=self._decode_json(row["old_value_json"]), new_value=self._decode_json(row["new_value_json"]),
                actor=str(row["actor"]), actor_role=str(row["actor_role"]), reason=str(row["reason"]),
                source=str(row["source"]), app_version=str(row["app_version"]), created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def _save_state(
        self,
        history_id: str,
        *,
        snapshot: dict[str, Any] | None,
        expected_row_version: int,
        state: ReportState,
        action: str,
        actor: str,
        role: ReportRole,
        reason: str,
        force: bool = False,
        app_version: str = "",
    ) -> CandidateReportRecord:
        self.initialize()
        key = self._required(history_id, "History id")
        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidate_reports WHERE history_id = ?", (key,)).fetchone()
            if row is None:
                raise CandidateReportNotFoundError("Structured candidate report not found.")
            if int(row["row_version"]) != int(expected_row_version) and not (force and role == "admin"):
                raise CandidateReportStaleError("Candidate report changed since it was opened.")
            old_snapshot = self._decode_object(row["working_snapshot_json"])
            next_snapshot = dict(snapshot) if snapshot is not None else old_snapshot
            next_row_version = int(row["row_version"]) + 1
            next_version_number = int(row["version_number"]) + 1
            revision_id = str(uuid4())
            finalized_json = self._json(next_snapshot) if state == "finalized" else str(row["finalized_snapshot_json"])
            conn.execute(
                """
                UPDATE candidate_reports
                SET state = ?, working_snapshot_json = ?, finalized_snapshot_json = ?, row_version = ?,
                    version_number = ?, reopen_reason = ?, updated_by = ?, updated_role = ?, updated_at = ?
                WHERE history_id = ?
                """,
                (
                    state, self._json(next_snapshot), finalized_json, next_row_version, next_version_number,
                    str(reason or row["reopen_reason"] or ""), self._actor(actor), role, now, key,
                ),
            )
            self._insert_version(conn, revision_id, key, next_version_number, state, next_snapshot, actor, role, reason, now)
            changes = self._diff(old_snapshot, next_snapshot)
            if not changes:
                changes = [("", old_snapshot if action == "report_reopened" else None, next_snapshot if action != "report_reopened" else None)]
            for field_path, old_value, new_value in changes:
                self._insert_audit(
                    conn, key, revision_id, action, field_path, old_value, new_value,
                    actor, role, reason, "administrator", app_version, now,
                )
            conn.commit()
        return self.load_visible_version(key, role="admin")

    @staticmethod
    def _insert_version(
        conn: sqlite3.Connection, revision_id: str, history_id: str, version_number: int,
        state: ReportState, snapshot: dict[str, Any], actor: str, actor_role: ReportRole,
        reason: str, created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO candidate_report_versions (
                revision_id, history_id, version_number, state, snapshot_json,
                actor, actor_role, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (revision_id, history_id, version_number, state, CandidateReportRepository._json(snapshot),
             CandidateReportRepository._actor(actor), actor_role, str(reason or ""), created_at),
        )

    @staticmethod
    def _insert_audit(
        conn: sqlite3.Connection, history_id: str, revision_id: str, action: str,
        field_path: str, old_value: Any, new_value: Any, actor: str, actor_role: ReportRole,
        reason: str, source: str, app_version: str, created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO candidate_report_audit_events (
                history_id, revision_id, action, field_path, old_value_json, new_value_json,
                actor, actor_role, reason, source, app_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (history_id, revision_id, action, field_path, CandidateReportRepository._json(old_value),
             CandidateReportRepository._json(new_value), CandidateReportRepository._actor(actor), actor_role,
             str(reason or ""), source, str(app_version or ""), created_at),
        )

    @classmethod
    def _diff(cls, old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
        if isinstance(old, dict) and isinstance(new, dict):
            changes: list[tuple[str, Any, Any]] = []
            for key in sorted(set(old) | set(new)):
                child = f"{path}.{key}" if path else str(key)
                changes.extend(cls._diff(old.get(key), new.get(key), child))
            return changes
        if isinstance(old, list) and isinstance(new, list):
            changes = []
            for index in range(max(len(old), len(new))):
                child = f"{path}.{index}" if path else str(index)
                old_value = old[index] if index < len(old) else None
                new_value = new[index] if index < len(new) else None
                changes.extend(cls._diff(old_value, new_value, child))
            return changes
        if old == new:
            return []
        return [(path, old, new)]

    @staticmethod
    def _value_at_path(value: Any, path: str) -> Any:
        current = value
        for part in path.split(".") if path else []:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return None
        return current

    @staticmethod
    def _required(value: Any, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise CandidateReportError(f"{label} is required.")
        return clean

    @staticmethod
    def _actor(value: Any) -> str:
        return str(value or "unknown").strip() or "unknown"

    @staticmethod
    def _require_role(role: str) -> None:
        if role not in {"admin", "director"}:
            raise CandidateReportPermissionError("Unknown candidate-report role.")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _decode_json(value: Any) -> Any:
        try:
            return json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return None

    @classmethod
    def _decode_object(cls, value: Any) -> dict[str, Any]:
        decoded = cls._decode_json(value)
        return decoded if isinstance(decoded, dict) else {}
