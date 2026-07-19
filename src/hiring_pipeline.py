from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from data_store import InterviewHistoryStore


class HiringStage(StrEnum):
    INITIAL_INTERVIEW = "initial_interview"
    DIRECTOR_REVIEW = "director_review"
    OFFER_DRAFT = "offer_draft"
    EXECUTIVE_APPROVAL = "executive_approval"
    OFFER_SENT = "offer_sent"
    ACCEPTED = "accepted"
    CLOSED = "closed"


@dataclass(frozen=True)
class PromotedOfferArtifacts:
    docx_path: Path
    pdf_path: Path
    docx_sha256: str
    pdf_sha256: str


class OfferApprovalArtifactStage:
    """Stages exact preview artifacts beside destination and promotes verified bytes."""

    def __init__(self, final_docx_path: Path) -> None:
        self.final_docx_path = Path(final_docx_path).expanduser().resolve()
        if self.final_docx_path.suffix.casefold() != ".docx":
            raise ValueError("Approved offer destination must be a DOCX path.")
        self.final_pdf_path = self.final_docx_path.with_suffix(".pdf")
        self.final_docx_path.parent.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.final_docx_path.parent / f".hiring-offer-stage-{uuid.uuid4().hex}"
        self.staging_dir.mkdir(parents=False, exist_ok=False)
        self.staged_docx_path = self.staging_dir / self.final_docx_path.name
        self.staged_pdf_path = self.staging_dir / self.final_pdf_path.name

    def hashes(self) -> tuple[str, str]:
        if not self.staged_docx_path.is_file() or not self.staged_pdf_path.is_file():
            raise ValueError("Both staged offer artifacts are required.")
        return (
            hashlib.sha256(self.staged_docx_path.read_bytes()).hexdigest(),
            hashlib.sha256(self.staged_pdf_path.read_bytes()).hexdigest(),
        )

    def promote(self) -> PromotedOfferArtifacts:
        docx_hash, pdf_hash = self.hashes()
        if self.final_docx_path.exists() or self.final_pdf_path.exists():
            raise ValueError("Approved offer destination already exists.")
        os.replace(self.staged_docx_path, self.final_docx_path)
        try:
            os.replace(self.staged_pdf_path, self.final_pdf_path)
        except OSError:
            os.replace(self.final_docx_path, self.staged_docx_path)
            raise
        final_docx_hash = hashlib.sha256(self.final_docx_path.read_bytes()).hexdigest()
        final_pdf_hash = hashlib.sha256(self.final_pdf_path.read_bytes()).hexdigest()
        if (final_docx_hash, final_pdf_hash) != (docx_hash, pdf_hash):
            raise ValueError("Promoted offer artifact hash mismatch.")
        self.staging_dir.rmdir()
        return PromotedOfferArtifacts(
            docx_path=self.final_docx_path,
            pdf_path=self.final_pdf_path,
            docx_sha256=docx_hash,
            pdf_sha256=pdf_hash,
        )

    def cleanup(self) -> None:
        self.staged_docx_path.unlink(missing_ok=True)
        self.staged_pdf_path.unlink(missing_ok=True)
        if self.staging_dir.exists():
            self.staging_dir.rmdir()


@dataclass(frozen=True)
class CandidateProfile:
    candidate_id: str
    legal_name: str
    preferred_name: str
    email: str
    phone: str
    honorific: str = "Ms."
    archived_at: str = ""


@dataclass(frozen=True)
class HiringApplication:
    application_id: str
    candidate_id: str
    history_id: str
    school: str
    position: str
    cycle_number: int
    stage: HiringStage
    attention_code: str = ""
    archived_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ApplicationEvent:
    event_id: int
    application_id: str
    event_type: str
    actor: str
    created_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OfferApprovalDates:
    offer_date: date
    reply_by_date: date
    start_date: date


@dataclass(frozen=True)
class OfferVersion:
    version_id: str
    application_id: str
    version_number: int
    status: str
    terms: dict[str, Any]
    docx_path: str = ""
    pdf_path: str = ""
    approval_date: str = ""
    document_reply_by_date: str = ""
    operational_reply_by_date: str = ""
    start_date: str = ""
    approver_name: str = ""
    approver_role: str = ""
    send_status: str = ""
    send_error: str = ""
    docx_sha256: str = ""
    pdf_sha256: str = ""
    rendered_email: str = ""
    approved_at: str = ""
    sent_at: str = ""
    superseded_by_version_id: str = ""


@dataclass(frozen=True)
class MigrationParityReport:
    source_rows: int
    profile_count: int
    application_count: int
    stage_counts: dict[str, int]
    conflict_count: int
    skipped_rows: int


class HiringOfferNotificationAdapter:
    def __init__(self, notification_service: Any) -> None:
        self.notification_service = notification_service

    def __call__(
        self,
        candidate: CandidateProfile,
        version: OfferVersion,
        pdf_path: Path,
        idempotency_key: str,
    ) -> list[Any]:
        resolved_pdf = Path(pdf_path)
        if resolved_pdf.suffix.casefold() != ".pdf":
            raise ValueError("Approved offer delivery requires a PDF attachment.")
        payload = build_offer_notification_payload(candidate, version, resolved_pdf)
        return list(
            self.notification_service.emit_event(
                "offer.approved",
                payload,
                idempotency_key,
            )
        )

    @staticmethod
    def payload(
        candidate: CandidateProfile,
        version: OfferVersion,
        pdf_path: Path,
    ) -> dict[str, Any]:
        return build_offer_notification_payload(candidate, version, pdf_path)

    def offer_accepted(
        self,
        candidate: CandidateProfile,
        version: OfferVersion,
        idempotency_key: str,
    ) -> list[Any]:
        directory = self.notification_service.directory
        school = str(version.terms.get("school", ""))
        payload = {
            "candidate": candidate.legal_name,
            "candidate_name": candidate.legal_name,
            "candidate_email": candidate.email,
            "honorific": candidate.honorific,
            "school": school,
            "position": str(version.terms.get("position", "Teacher")),
            "director_name": directory.director_names.get(school.casefold(), "Director"),
            "onboarding_guide_path": directory.onboarding_guide_path,
            "offer_version_id": version.version_id,
        }
        return list(
            self.notification_service.emit_event("offer.accepted", payload, idempotency_key)
        )


def build_offer_notification_payload(
    candidate: CandidateProfile,
    version: OfferVersion,
    pdf_path: Path,
) -> dict[str, Any]:
    """Build shared offer-approved template payload used by preview and send."""
    school = str(version.terms.get("school", ""))
    normalized_school = school.strip().casefold()
    school_code = {
        "hawthorne": "HAW",
        "north long beach": "NLB",
        "long beach": "NLB",
        "palmdale": "PMD",
    }.get(normalized_school, school)
    school_location = {
        "hawthorne": "Hawthorne",
        "north long beach": "North Long Beach",
        "long beach": "North Long Beach",
        "palmdale": "Palmdale",
    }.get(normalized_school, school or "your school")
    resolved_pdf = Path(pdf_path)
    return {
        "candidate": candidate.legal_name,
        "candidate_name": candidate.legal_name,
        "candidate_email": candidate.email,
        "honorific": candidate.honorific,
        "school": school,
        "school_code": school_code,
        "school_location": school_location,
        "position": str(version.terms.get("position", "Teacher")),
        "offer_pdf_path": str(resolved_pdf),
        "attachments": [str(resolved_pdf)],
        "offer_date": version.approval_date,
        "reply_by_date": version.document_reply_by_date,
        "start_date": version.start_date,
        "offer_version_id": version.version_id,
    }


def calculate_offer_approval_dates(approval_date: date) -> OfferApprovalDates:
    if not isinstance(approval_date, date):
        raise TypeError("Approval date must be a date.")
    reply_by = approval_date + timedelta(days=3)
    start_target = reply_by + timedelta(days=14)
    days_until_monday = (7 - start_target.weekday()) % 7
    return OfferApprovalDates(
        offer_date=approval_date,
        reply_by_date=reply_by,
        start_date=start_target + timedelta(days=days_until_monday),
    )


def normalize_candidate_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Candidate phone must contain 10 U.S. digits.")
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _validated_honorific(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in {"Mr.", "Ms."}:
        raise ValueError("Candidate honorific must be Mr. or Ms.")
    return clean


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _match_text(value: str) -> str:
    return _normalized_text(value).casefold()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class HiringPipelineStore:
    def __init__(self, history_path: Path) -> None:
        self.history_path = Path(history_path)
        self.db_path = (
            self.history_path
            if self.history_path.suffix.casefold() == ".sqlite3"
            else self.history_path.with_suffix(".sqlite3")
        )
        self._ensure_schema()

    def get_candidate(self, candidate_id: str) -> CandidateProfile:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM candidate_profiles WHERE candidate_id = ?",
                (str(candidate_id).strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Candidate profile was not found.")
        return self._candidate_from_row(row)

    def search_candidate_profiles(self, query: str = "") -> list[CandidateProfile]:
        needle = _match_text(query)
        sql = "SELECT * FROM candidate_profiles WHERE archived_at = ''"
        values: tuple[str, ...] = ()
        if needle:
            sql += " AND (normalized_name LIKE ? OR lower(preferred_name) LIKE ? OR lower(email) LIKE ? OR lower(phone) LIKE ?)"
            pattern = f"%{needle}%"
            values = (pattern, pattern, pattern, pattern)
        sql += " ORDER BY normalized_name, candidate_id"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, values).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def update_candidate_profile(
        self,
        candidate_id: str,
        *,
        legal_name: str,
        preferred_name: str,
        email: str,
        phone: str,
    ) -> CandidateProfile:
        current = self.get_candidate(candidate_id)
        clean_name = _normalized_text(legal_name)
        clean_email = _normalized_text(email)
        if not clean_name:
            raise ValueError("Legal name is required.")
        if clean_email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean_email):
            raise ValueError("Candidate email is invalid.")
        with sqlite3.connect(self.db_path) as conn:
            school_row = conn.execute(
                "SELECT normalized_school FROM candidate_profiles WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            try:
                cursor = conn.execute(
                    """
                    UPDATE candidate_profiles
                    SET legal_name = ?, preferred_name = ?, email = ?, phone = ?,
                        normalized_name = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (
                        clean_name,
                        _normalized_text(preferred_name),
                        clean_email,
                        _normalized_text(phone),
                        _match_text(clean_name),
                        _now_utc(),
                        current.candidate_id,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("Candidate identity conflicts with an existing profile for this school.") from exc
        if school_row is None or cursor.rowcount != 1:
            raise ValueError("Candidate profile was not found.")
        return self.get_candidate(candidate_id)

    def get_application(self, application_id: str) -> HiringApplication:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM hiring_applications WHERE application_id = ?",
                (str(application_id).strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Hiring application was not found.")
        return self._application_from_row(row)

    def application_for_history(self, history_id: str) -> HiringApplication | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM hiring_applications WHERE history_id = ?",
                (str(history_id).strip(),),
            ).fetchone()
        return None if row is None else self._application_from_row(row)

    def list_applications(self, *, include_archived: bool = False) -> list[HiringApplication]:
        query = "SELECT * FROM hiring_applications"
        if not include_archived:
            query += " WHERE archived_at = ''"
        query += " ORDER BY rowid"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()
        return [self._application_from_row(row) for row in rows]

    def profile_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM candidate_profiles").fetchone()[0])

    def archive_application(self, application_id: str) -> HiringApplication:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_applications SET archived_at = ?, updated_at = ?
                WHERE application_id = ? AND archived_at = ''
                """,
                (_now_utc(), _now_utc(), str(application_id).strip()),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Active hiring application was not found.")
        return self.get_application(application_id)

    def delete_candidate_profile(self, candidate_id: str) -> None:
        clean_id = str(candidate_id).strip()
        with sqlite3.connect(self.db_path) as conn:
            linked = int(
                conn.execute(
                    "SELECT COUNT(*) FROM hiring_applications WHERE candidate_id = ?",
                    (clean_id,),
                ).fetchone()[0]
            )
            if linked:
                raise ValueError("Candidate profile has linked records or files and cannot be deleted.")
            cursor = conn.execute(
                "DELETE FROM candidate_profiles WHERE candidate_id = ?",
                (clean_id,),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Candidate profile was not found.")

    def update_application_stage(
        self,
        application_id: str,
        stage: HiringStage,
        *,
        attention_code: str = "",
    ) -> HiringApplication:
        now = _now_utc()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_applications
                SET stage = ?, attention_code = ?, updated_at = ?
                WHERE application_id = ?
                """,
                (stage.value, str(attention_code).strip(), now, str(application_id).strip()),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Hiring application was not found.")
        return self.get_application(application_id)

    def append_event(
        self,
        application_id: str,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        clean_actor = _normalized_text(actor)
        clean_event = _normalized_text(event_type)
        if not clean_actor or not clean_event:
            raise ValueError("Event type and actor are required.")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO hiring_application_events (
                    application_id, event_type, actor, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(application_id).strip(),
                    clean_event,
                    clean_actor,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    _now_utc(),
                ),
            )
            conn.commit()

    def list_events(self, application_id: str) -> list[ApplicationEvent]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM hiring_application_events
                WHERE application_id = ? ORDER BY event_id
                """,
                (str(application_id).strip(),),
            ).fetchall()
        events: list[ApplicationEvent] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                payload = {}
            events.append(
                ApplicationEvent(
                    event_id=int(row["event_id"]),
                    application_id=str(row["application_id"]),
                    event_type=str(row["event_type"]),
                    actor=str(row["actor"]),
                    created_at=str(row["created_at"]),
                    payload=payload if isinstance(payload, dict) else {},
                )
            )
        return events

    def create_offer_version(
        self,
        application_id: str,
        *,
        terms: dict[str, Any],
        actor: str,
        source_key: str = "",
    ) -> OfferVersion:
        if not isinstance(terms, dict) or not terms:
            raise ValueError("Offer terms are required.")
        self.get_application(application_id)
        now = _now_utc()
        clean_source_key = _normalized_text(source_key)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            version_number = int(
                conn.execute(
                    "SELECT COUNT(*) + 1 FROM hiring_offer_versions WHERE application_id = ?",
                    (str(application_id).strip(),),
                ).fetchone()[0]
            )
            version_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO hiring_offer_versions (
                    version_id, application_id, version_number, status, terms_json, source_key, created_at, created_by
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    str(application_id).strip(),
                    version_number,
                    json.dumps(terms, ensure_ascii=False, sort_keys=True),
                    clean_source_key,
                    now,
                    _normalized_text(actor),
                ),
            )
            conn.commit()
        self.append_event(
            application_id,
            "offer_draft_created",
            actor=actor,
            payload={"version_id": version_id, "version_number": version_number},
        )
        return self.get_offer_version(version_id)

    def find_offer_version_by_source(self, application_id: str, source_key: str) -> OfferVersion | None:
        clean_source_key = _normalized_text(source_key)
        if not clean_source_key:
            return None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM hiring_offer_versions WHERE application_id = ? AND source_key = ?",
                (str(application_id).strip(), clean_source_key),
            ).fetchone()
        return None if row is None else self._offer_version_from_row(row)

    def get_offer_version(self, version_id: str) -> OfferVersion:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM hiring_offer_versions WHERE version_id = ?",
                (str(version_id).strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Offer version was not found.")
        return self._offer_version_from_row(row)

    def list_offer_versions(self, application_id: str) -> list[OfferVersion]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM hiring_offer_versions
                WHERE application_id = ? ORDER BY version_number
                """,
                (str(application_id).strip(),),
            ).fetchall()
        return [self._offer_version_from_row(row) for row in rows]

    def set_offer_status(self, version_id: str, status: str) -> OfferVersion:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE hiring_offer_versions SET status = ? WHERE version_id = ?",
                (_normalized_text(status), str(version_id).strip()),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Offer version was not found.")
        return self.get_offer_version(version_id)

    def record_offer_artifacts(
        self,
        version_id: str,
        *,
        docx_path: Path,
        pdf_path: Path,
    ) -> OfferVersion:
        """Persist pre-rendered DOCX/PDF paths for one pending offer version."""
        docx = Path(docx_path).expanduser().resolve()
        pdf = Path(pdf_path).expanduser().resolve()
        if docx.suffix.casefold() != ".docx" or not docx.is_file() or docx.stat().st_size <= 0:
            raise ValueError("Non-empty DOCX offer artifact is required.")
        if pdf.suffix.casefold() != ".pdf" or not pdf.is_file() or pdf.stat().st_size <= 0:
            raise ValueError("Non-empty PDF offer artifact is required.")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_offer_versions
                SET docx_path = ?, pdf_path = ?, docx_sha256 = ?, pdf_sha256 = ?
                WHERE version_id = ? AND status = 'pending_approval'
                """,
                (
                    str(docx),
                    str(pdf),
                    hashlib.sha256(docx.read_bytes()).hexdigest(),
                    hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    str(version_id).strip(),
                ),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Only a pending offer version may receive artifacts.")
        return self.get_offer_version(version_id)

    def record_offer_approval(
        self,
        version_id: str,
        *,
        dates: OfferApprovalDates,
        docx_path: Path,
        pdf_path: Path,
        approver_name: str,
        approver_role: str,
        rendered_email: str = "",
    ) -> OfferVersion:
        docx_hash = hashlib.sha256(Path(docx_path).read_bytes()).hexdigest()
        pdf_hash = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_offer_versions
                SET status = 'approved', docx_path = ?, pdf_path = ?,
                    approval_date = ?, document_reply_by_date = ?,
                    operational_reply_by_date = ?, start_date = ?,
                    approver_name = ?, approver_role = ?,
                    send_status = 'pending', send_error = '',
                    docx_sha256 = ?, pdf_sha256 = ?, rendered_email = ?, approved_at = ?
                WHERE version_id = ? AND status = 'pending_approval'
                """,
                (
                    str(docx_path),
                    str(pdf_path),
                    dates.offer_date.isoformat(),
                    dates.reply_by_date.isoformat(),
                    dates.reply_by_date.isoformat(),
                    dates.start_date.isoformat(),
                    approver_name,
                    approver_role,
                    docx_hash,
                    pdf_hash,
                    str(rendered_email),
                    _now_utc(),
                    str(version_id).strip(),
                ),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Only a pending offer version may be approved.")
        return self.get_offer_version(version_id)

    def record_offer_delivery(
        self,
        version_id: str,
        *,
        sent: bool,
        error: str = "",
    ) -> OfferVersion:
        status = "sent" if sent else "approved"
        send_status = "sent" if sent else "failed"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT application_id FROM hiring_offer_versions WHERE version_id = ?",
                (str(version_id).strip(),),
            ).fetchone()
            cursor = conn.execute(
                """
                UPDATE hiring_offer_versions
                SET status = ?, send_status = ?, send_error = ?, sent_at = ?
                WHERE version_id = ? AND status IN ('approved', 'sent')
                """,
                (status, send_status, str(error)[:500], _now_utc() if sent else "", str(version_id).strip()),
            )
            if sent and row is not None:
                conn.execute(
                    """
                    UPDATE hiring_offer_versions
                    SET status = 'superseded', superseded_at = ?, superseded_by_version_id = ?
                    WHERE application_id = ? AND version_id != ? AND status = 'sent'
                    """,
                    (_now_utc(), str(version_id).strip(), str(row[0]), str(version_id).strip()),
                )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Approved offer version was not found.")
        return self.get_offer_version(version_id)

    def update_operational_reply_by_date(
        self,
        version_id: str,
        reply_by_date: date,
    ) -> OfferVersion:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_offer_versions SET operational_reply_by_date = ?
                WHERE version_id = ? AND status = 'sent'
                """,
                (reply_by_date.isoformat(), str(version_id).strip()),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Only a sent offer deadline may be extended.")
        return self.get_offer_version(version_id)

    def record_initial_interview(
        self,
        *,
        history_id: str,
        legal_name: str,
        email: str,
        phone: str,
        school: str,
        position: str,
        score: float,
        outcome: str,
        honorific: str = "Ms.",
    ) -> HiringApplication:
        clean_history_id = _normalized_text(history_id)
        clean_name = _normalized_text(legal_name)
        clean_school = _normalized_text(school)
        clean_position = _normalized_text(position)
        if not all((clean_history_id, clean_name, clean_school, clean_position)):
            raise ValueError("History ID, candidate name, school, and position are required.")
        numeric_score = float(score)
        if numeric_score < 0 or numeric_score > 100:
            raise ValueError("Interview score must be between 0 and 100.")
        existing = self.application_for_history(clean_history_id)
        if existing is not None:
            return existing

        now = _now_utc()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            candidate_id = self._candidate_id_for_identity(
                conn,
                legal_name=clean_name,
                school=clean_school,
                email=_normalized_text(email),
                phone=_normalized_text(phone),
                honorific=_validated_honorific(honorific),
                now=now,
            )
            cycle_number = int(
                conn.execute(
                    """
                    SELECT COUNT(*) + 1
                    FROM hiring_applications
                    WHERE candidate_id = ? AND normalized_school = ? AND normalized_position = ?
                    """,
                    (candidate_id, _match_text(clean_school), _match_text(clean_position)),
                ).fetchone()[0]
            )
            application_id = str(uuid.uuid4())
            stage = HiringStage.DIRECTOR_REVIEW if numeric_score >= 65.0 else HiringStage.CLOSED
            conn.execute(
                """
                INSERT INTO hiring_applications (
                    application_id, candidate_id, history_id, school, normalized_school,
                    position, normalized_position, cycle_number, stage, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    candidate_id,
                    clean_history_id,
                    clean_school,
                    _match_text(clean_school),
                    clean_position,
                    _match_text(clean_position),
                    cycle_number,
                    stage.value,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO hiring_application_events (
                    application_id, event_type, actor, payload_json, created_at
                ) VALUES (?, 'initial_interview_completed', 'system', ?, ?)
                """,
                (
                    application_id,
                    self._event_payload(score=numeric_score, outcome=outcome, stage=stage.value),
                    now,
                ),
            )
            conn.commit()

        InterviewHistoryStore(self.history_path).update_row(
            clean_history_id,
            {"candidate_id": candidate_id, "application_id": application_id},
        )
        return self.get_application(application_id)

    def start_application(
        self,
        *,
        legal_name: str,
        email: str,
        phone: str,
        school: str,
        position: str,
        actor: str,
        honorific: str = "Ms.",
    ) -> HiringApplication:
        clean_name = _normalized_text(legal_name)
        clean_school = _normalized_text(school)
        clean_position = _normalized_text(position)
        if not all((clean_name, clean_school, clean_position, _normalized_text(actor))):
            raise ValueError("Candidate name, school, position, and actor are required.")
        now = _now_utc()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate_id = self._candidate_id_for_identity(
                conn,
                legal_name=clean_name,
                school=clean_school,
                email=_normalized_text(email),
                phone=_normalized_text(phone),
                honorific=_validated_honorific(honorific),
                now=now,
            )
            cycle_number = int(
                conn.execute(
                    """
                    SELECT COUNT(*) + 1 FROM hiring_applications
                    WHERE candidate_id = ? AND normalized_school = ? AND normalized_position = ?
                    """,
                    (candidate_id, _match_text(clean_school), _match_text(clean_position)),
                ).fetchone()[0]
            )
            application_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO hiring_applications (
                    application_id, candidate_id, history_id, school, normalized_school,
                    position, normalized_position, cycle_number, stage, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    candidate_id,
                    f"draft:{application_id}",
                    clean_school,
                    _match_text(clean_school),
                    clean_position,
                    _match_text(clean_position),
                    cycle_number,
                    HiringStage.INITIAL_INTERVIEW.value,
                    now,
                    now,
                ),
            )
            conn.commit()
        self.append_event(application_id, "application_started", actor=actor, payload={})
        return self.get_application(application_id)

    def start_external_offer_application(
        self,
        *,
        legal_name: str,
        email: str,
        phone: str,
        school: str,
        position: str,
        actor: str,
        honorific: str = "Ms.",
    ) -> HiringApplication:
        clean_name = _normalized_text(legal_name)
        clean_school = _normalized_text(school)
        clean_position = _normalized_text(position)
        clean_actor = _normalized_text(actor)
        if not all((clean_name, clean_school, clean_position, clean_actor)):
            raise ValueError("Candidate name, school, position, and actor are required.")
        now = _now_utc()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate_id = self._candidate_id_for_identity(
                conn,
                legal_name=clean_name,
                school=clean_school,
                email=_normalized_text(email),
                phone=_normalized_text(phone),
                honorific=_validated_honorific(honorific),
                now=now,
            )
            cycle_number = int(
                conn.execute(
                    """
                    SELECT COUNT(*) + 1 FROM hiring_applications
                    WHERE candidate_id = ? AND normalized_school = ? AND normalized_position = ?
                    """,
                    (candidate_id, _match_text(clean_school), _match_text(clean_position)),
                ).fetchone()[0]
            )
            application_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO hiring_applications (
                    application_id, candidate_id, history_id, school, normalized_school,
                    position, normalized_position, cycle_number, stage, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    candidate_id,
                    f"external_offer:{application_id}",
                    clean_school,
                    _match_text(clean_school),
                    clean_position,
                    _match_text(clean_position),
                    cycle_number,
                    HiringStage.OFFER_DRAFT.value,
                    now,
                    now,
                ),
            )
            conn.commit()
        self.append_event(
            application_id,
            "external_offer_application_created",
            actor=clean_actor,
            payload={},
        )
        return self.get_application(application_id)

    def finalize_initial_interview(
        self,
        application_id: str,
        *,
        history_id: str,
        score: float,
        outcome: str,
        actor: str,
    ) -> HiringApplication:
        application = self.get_application(application_id)
        if application.stage is not HiringStage.INITIAL_INTERVIEW:
            raise ValueError("Application is not in the initial interview stage.")
        numeric_score = float(score)
        if numeric_score < 0 or numeric_score > 100:
            raise ValueError("Interview score must be between 0 and 100.")
        clean_history_id = _normalized_text(history_id)
        if not clean_history_id:
            raise ValueError("History ID is required.")
        stage = HiringStage.DIRECTOR_REVIEW if numeric_score >= 65 else HiringStage.CLOSED
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE hiring_applications
                SET history_id = ?, stage = ?, updated_at = ?
                WHERE application_id = ? AND stage = ?
                """,
                (
                    clean_history_id,
                    stage.value,
                    _now_utc(),
                    application_id,
                    HiringStage.INITIAL_INTERVIEW.value,
                ),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Initial interview application could not be finalized.")
        self.append_event(
            application_id,
            "initial_interview_completed",
            actor=actor,
            payload={"score": numeric_score, "outcome": outcome, "stage": stage.value},
        )
        InterviewHistoryStore(self.history_path).update_row(
            clean_history_id,
            {"candidate_id": application.candidate_id, "application_id": application_id},
        )
        return self.get_application(application_id)

    @staticmethod
    def _event_payload(**values: Any) -> str:
        return json.dumps(values, ensure_ascii=False, sort_keys=True)

    def _candidate_id_for_identity(
        self,
        conn: sqlite3.Connection,
        *,
        legal_name: str,
        school: str,
        email: str,
        phone: str,
        honorific: str,
        now: str,
    ) -> str:
        row = conn.execute(
            """
            SELECT candidate_id FROM candidate_profiles
            WHERE normalized_name = ? AND normalized_school = ? AND archived_at = ''
            """,
            (_match_text(legal_name), _match_text(school)),
        ).fetchone()
        if row is not None:
            return str(row[0])
        candidate_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO candidate_profiles (
                candidate_id, legal_name, preferred_name, email, phone, honorific,
                normalized_name, normalized_school, created_at, updated_at
            ) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                legal_name,
                email,
                phone,
                honorific,
                _match_text(legal_name),
                _match_text(school),
                now,
                now,
            ),
        )
        return candidate_id

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    candidate_id TEXT PRIMARY KEY,
                    legal_name TEXT NOT NULL,
                    preferred_name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    honorific TEXT NOT NULL DEFAULT 'Ms.',
                    normalized_name TEXT NOT NULL,
                    normalized_school TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(normalized_name, normalized_school)
                );
                CREATE TABLE IF NOT EXISTS hiring_applications (
                    application_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidate_profiles(candidate_id),
                    history_id TEXT NOT NULL UNIQUE,
                    school TEXT NOT NULL,
                    normalized_school TEXT NOT NULL,
                    position TEXT NOT NULL,
                    normalized_position TEXT NOT NULL,
                    cycle_number INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    attention_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_hiring_applications_stage
                    ON hiring_applications(stage, archived_at);
                CREATE TABLE IF NOT EXISTS hiring_application_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT NOT NULL REFERENCES hiring_applications(application_id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hiring_events_application
                    ON hiring_application_events(application_id, event_id);
                CREATE TABLE IF NOT EXISTS hiring_offer_versions (
                    version_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL REFERENCES hiring_applications(application_id),
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    source_key TEXT NOT NULL DEFAULT '',
                    docx_path TEXT NOT NULL DEFAULT '',
                    pdf_path TEXT NOT NULL DEFAULT '',
                    approval_date TEXT NOT NULL DEFAULT '',
                    document_reply_by_date TEXT NOT NULL DEFAULT '',
                    operational_reply_by_date TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL DEFAULT '',
                    approver_name TEXT NOT NULL DEFAULT '',
                    approver_role TEXT NOT NULL DEFAULT '',
                    send_status TEXT NOT NULL DEFAULT '',
                    send_error TEXT NOT NULL DEFAULT '',
                    docx_sha256 TEXT NOT NULL DEFAULT '',
                    pdf_sha256 TEXT NOT NULL DEFAULT '',
                    rendered_email TEXT NOT NULL DEFAULT '',
                    approved_at TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT '',
                    superseded_by_version_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    superseded_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(application_id, version_number)
                );
                CREATE INDEX IF NOT EXISTS idx_hiring_offer_versions_application
                    ON hiring_offer_versions(application_id, version_number);
                """
            )
            offer_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(hiring_offer_versions)").fetchall()
            }
            if "source_key" not in offer_columns:
                conn.execute("ALTER TABLE hiring_offer_versions ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_offer_source "
                "ON hiring_offer_versions(application_id, source_key) WHERE source_key <> ''"
            )
            candidate_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(candidate_profiles)").fetchall()
            }
            if "honorific" not in candidate_columns:
                conn.execute("ALTER TABLE candidate_profiles ADD COLUMN honorific TEXT NOT NULL DEFAULT 'Ms.'")
            for column, definition in (
                ("docx_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("pdf_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("rendered_email", "TEXT NOT NULL DEFAULT ''"),
                ("approved_at", "TEXT NOT NULL DEFAULT ''"),
                ("sent_at", "TEXT NOT NULL DEFAULT ''"),
                ("superseded_by_version_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                existing = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(hiring_offer_versions)").fetchall()
                }
                if column not in existing:
                    conn.execute(f"ALTER TABLE hiring_offer_versions ADD COLUMN {column} {definition}")
            conn.commit()

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> CandidateProfile:
        return CandidateProfile(
            candidate_id=str(row["candidate_id"]),
            legal_name=str(row["legal_name"]),
            preferred_name=str(row["preferred_name"]),
            email=str(row["email"]),
            phone=str(row["phone"]),
            honorific=str(row["honorific"] or "Ms."),
            archived_at=str(row["archived_at"]),
        )

    @staticmethod
    def _application_from_row(row: sqlite3.Row) -> HiringApplication:
        return HiringApplication(
            application_id=str(row["application_id"]),
            candidate_id=str(row["candidate_id"]),
            history_id=str(row["history_id"]),
            school=str(row["school"]),
            position=str(row["position"]),
            cycle_number=int(row["cycle_number"]),
            stage=HiringStage(str(row["stage"])),
            attention_code=str(row["attention_code"]),
            archived_at=str(row["archived_at"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _offer_version_from_row(row: sqlite3.Row) -> OfferVersion:
        try:
            terms = json.loads(str(row["terms_json"]))
        except json.JSONDecodeError:
            terms = {}
        return OfferVersion(
            version_id=str(row["version_id"]),
            application_id=str(row["application_id"]),
            version_number=int(row["version_number"]),
            status=str(row["status"]),
            terms=terms if isinstance(terms, dict) else {},
            docx_path=str(row["docx_path"]),
            pdf_path=str(row["pdf_path"]),
            approval_date=str(row["approval_date"]),
            document_reply_by_date=str(row["document_reply_by_date"]),
            operational_reply_by_date=str(row["operational_reply_by_date"]),
            start_date=str(row["start_date"]),
            approver_name=str(row["approver_name"]),
            approver_role=str(row["approver_role"]),
            send_status=str(row["send_status"]),
            send_error=str(row["send_error"]),
            docx_sha256=str(row["docx_sha256"]),
            pdf_sha256=str(row["pdf_sha256"]),
            rendered_email=str(row["rendered_email"]),
            approved_at=str(row["approved_at"]),
            sent_at=str(row["sent_at"]),
            superseded_by_version_id=str(row["superseded_by_version_id"]),
        )


class HiringWorkflowService:
    def __init__(
        self,
        store: HiringPipelineStore,
        *,
        send_offer: Callable[[CandidateProfile, OfferVersion, Path, str], list[Any]] | None = None,
        notify_offer_accepted: Callable[[CandidateProfile, OfferVersion, str], list[Any]] | None = None,
        prepare_offer_artifacts: Callable[
            [HiringApplication, CandidateProfile, OfferVersion], tuple[Path, Path]
        ] | None = None,
    ) -> None:
        self.store = store
        self._send_offer = send_offer
        self._notify_offer_accepted = notify_offer_accepted
        self._prepare_offer_artifacts = prepare_offer_artifacts

    def record_initial_interview(self, **values: Any) -> HiringApplication:
        return self.store.record_initial_interview(**values)

    def search_candidate_profiles(self, query: str = "") -> list[CandidateProfile]:
        return self.store.search_candidate_profiles(query)

    def update_candidate_profile(self, candidate_id: str, **values: Any) -> CandidateProfile:
        return self.store.update_candidate_profile(candidate_id, **values)

    def start_application(self, **values: Any) -> HiringApplication:
        return self.store.start_application(**values)

    def start_external_offer_application(self, **values: Any) -> HiringApplication:
        return self.store.start_external_offer_application(**values)

    def finalize_initial_interview(
        self,
        application_id: str,
        **values: Any,
    ) -> HiringApplication:
        return self.store.finalize_initial_interview(application_id, **values)

    def backfill_history(self) -> list[HiringApplication]:
        history = InterviewHistoryStore(self.store.history_path)
        imported: list[HiringApplication] = []
        for row in history.load():
            history_id = history.build_row_key(row)
            if not history_id:
                continue
            try:
                score = self._history_score(row)
                imported.append(
                    self.record_initial_interview(
                        history_id=history_id,
                        legal_name=str(row.get("candidate_name") or row.get("candidate") or row.get("name") or ""),
                        email=str(row.get("candidate_email") or row.get("email") or ""),
                        phone=str(row.get("candidate_phone") or row.get("phone") or ""),
                        honorific=str(row.get("honorific") or row.get("candidate_honorific") or "Ms."),
                        school=str(row.get("school") or ""),
                        position=str(row.get("position") or row.get("role") or row.get("track") or ""),
                        score=score,
                        outcome=str(row.get("outcome") or row.get("status") or row.get("determination") or ""),
                    )
                )
            except (TypeError, ValueError):
                continue
        return imported

    def reconcile_history(self) -> MigrationParityReport:
        history = InterviewHistoryStore(self.store.history_path)
        rows = history.load()
        imported = self.backfill_history()
        valid_history_ids = {application.history_id for application in imported}
        conflicts = 0
        for row in rows:
            history_id = history.build_row_key(row)
            application = self.store.application_for_history(history_id) if history_id else None
            if application is None:
                continue
            inferred = self._strongest_history_stage(row, application.stage)
            outcome = _match_text(str(row.get("outcome") or row.get("status") or ""))
            conflict = outcome in {"no hire", "no_hire"} and inferred not in {
                HiringStage.CLOSED,
                HiringStage.INITIAL_INTERVIEW,
                HiringStage.DIRECTOR_REVIEW,
            }
            attention = "migration_conflict" if conflict else application.attention_code
            if inferred is not application.stage or attention != application.attention_code:
                self.store.update_application_stage(
                    application.application_id,
                    inferred,
                    attention_code=attention,
                )
            if conflict:
                conflicts += 1
            if conflict and application.attention_code != "migration_conflict":
                self.store.append_event(
                    application.application_id,
                    "migration_conflict_detected",
                    actor="system",
                    payload={"history_id": history_id},
                )
        applications = self.store.list_applications(include_archived=True)
        stage_counts = Counter(application.stage.value for application in applications)
        return MigrationParityReport(
            source_rows=len(rows),
            profile_count=self.store.profile_count(),
            application_count=len(applications),
            stage_counts=dict(sorted(stage_counts.items())),
            conflict_count=conflicts,
            skipped_rows=max(0, len(rows) - len(valid_history_ids)),
        )

    @staticmethod
    def _strongest_history_stage(row: dict[str, Any], fallback: HiringStage) -> HiringStage:
        offer_status = _match_text(str(row.get("offer_status") or "")).replace(" ", "_")
        if offer_status in {"accepted", "welcome_email_sent", "onboarding_ready"}:
            return HiringStage.ACCEPTED
        if offer_status in {"approved", "sent", "offer_sent"}:
            return HiringStage.OFFER_SENT
        if offer_status in {"generated", "draft", "offer_draft"}:
            return HiringStage.OFFER_DRAFT
        return fallback

    def record_director_decision(
        self,
        application_id: str,
        *,
        decision: str,
        actor: str,
    ) -> HiringApplication:
        application = self.store.get_application(application_id)
        if application.stage is not HiringStage.DIRECTOR_REVIEW:
            raise ValueError("Application is not awaiting director review.")
        normalized = _match_text(decision).replace("-", "_").replace(" ", "_")
        if normalized not in {"hire", "no_hire"}:
            raise ValueError("Director decision must be hire or no hire.")
        stage = HiringStage.OFFER_DRAFT if normalized == "hire" else HiringStage.CLOSED
        updated = self.store.update_application_stage(application_id, stage)
        self.store.append_event(
            application_id,
            "director_interview_completed",
            actor=actor,
            payload={"decision": normalized, "stage": stage.value},
        )
        return updated

    def synchronize_director_outcome(
        self,
        history_id: str,
        *,
        decision: str,
        actor: str = "Staffing v2",
    ) -> HiringApplication:
        application = self.store.application_for_history(history_id)
        if application is None:
            raise ValueError("Hiring application was not found for director outcome.")
        if application.stage is not HiringStage.DIRECTOR_REVIEW:
            return application
        return self.record_director_decision(
            application.application_id,
            decision=decision,
            actor=actor,
        )

    def create_offer_draft(
        self,
        application_id: str,
        *,
        terms: dict[str, Any],
        actor: str,
        source_key: str = "",
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        if application.stage not in {HiringStage.OFFER_DRAFT, HiringStage.OFFER_SENT}:
            raise ValueError("Application is not ready for an offer draft.")
        return self.store.create_offer_version(
            application_id, terms=terms, actor=actor, source_key=source_key
        )

    def create_external_offer(
        self,
        *,
        legal_name: str,
        email: str,
        phone: str,
        school: str,
        position: str,
        terms: dict[str, Any],
        actor: str,
        honorific: str = "Ms.",
    ) -> OfferVersion:
        application = self.start_external_offer_application(
            legal_name=legal_name,
            email=email,
            phone=phone,
            school=school,
            position=position,
            actor=actor,
            honorific=honorific,
        )
        draft = self.create_offer_draft(
            application.application_id,
            terms=terms,
            actor=actor,
        )
        return self.submit_offer_for_approval(
            application.application_id,
            draft.version_id,
            actor=actor,
        )

    def ensure_director_offer_submitted(
        self,
        application_id: str,
        *,
        source_key: str,
        terms: dict[str, Any],
        actor: str,
    ) -> OfferVersion:
        clean_source_key = _normalized_text(source_key)
        if not clean_source_key:
            raise ValueError("Director offer source key is required.")
        existing = self.store.find_offer_version_by_source(application_id, clean_source_key)
        if existing is not None:
            return existing
        try:
            draft = self.create_offer_draft(
                application_id,
                terms=terms,
                actor=actor,
                source_key=clean_source_key,
            )
        except sqlite3.IntegrityError:
            existing = self.store.find_offer_version_by_source(application_id, clean_source_key)
            if existing is not None:
                return existing
            raise
        return self.submit_offer_for_approval(application_id, draft.version_id, actor=actor)

    def submit_offer_for_approval(
        self,
        application_id: str,
        version_id: str,
        *,
        actor: str,
    ) -> OfferVersion:
        version = self.store.get_offer_version(version_id)
        if version.application_id != application_id or version.status != "draft":
            raise ValueError("Only a draft for this application may be submitted.")
        submitted = self.store.set_offer_status(version_id, "pending_approval")
        self.store.update_application_stage(application_id, HiringStage.EXECUTIVE_APPROVAL)
        self.store.append_event(
            application_id,
            "offer_submitted_for_approval",
            actor=actor,
            payload={"version_id": version_id, "version_number": submitted.version_number},
        )
        return self._prepare_pending_offer_artifacts(submitted)

    def _prepare_pending_offer_artifacts(self, version: OfferVersion) -> OfferVersion:
        if self._prepare_offer_artifacts is None:
            return version
        application = self.store.get_application(version.application_id)
        candidate = self.store.get_candidate(application.candidate_id)
        docx_path, pdf_path = self._prepare_offer_artifacts(application, candidate, version)
        return self.store.record_offer_artifacts(
            version.version_id,
            docx_path=docx_path,
            pdf_path=pdf_path,
        )

    def create_compensation_revision(
        self,
        application_id: str,
        *,
        hourly_pay: str,
        weekly_hours: str,
        actor: str,
        actor_role: str,
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        if _match_text(actor_role) != "admin":
            raise ValueError("Admin role is required for compensation revisions.")
        if application.stage is not HiringStage.OFFER_SENT:
            raise ValueError("Compensation revisions require a sent offer.")
        versions = self.store.list_offer_versions(application_id)
        sent_versions = [version for version in versions if version.status == "sent"]
        if not sent_versions:
            raise ValueError("A sent offer version is required.")
        prior = max(sent_versions, key=lambda version: version.version_number)
        pay = self._positive_decimal(hourly_pay, "Hourly pay")
        hours = self._positive_decimal(weekly_hours, "Weekly hours")
        if hours > Decimal("168"):
            raise ValueError("Weekly hours cannot exceed 168.")
        terms = dict(prior.terms)
        terms["hourly_pay"] = format(pay, "f")
        terms["weekly_hours"] = format(hours, "f")
        revision = self.store.create_offer_version(application_id, terms=terms, actor=actor)
        submitted = self.store.set_offer_status(revision.version_id, "pending_approval")
        self.store.append_event(
            application_id,
            "compensation_revision_created",
            actor=actor,
            payload={
                "version_id": submitted.version_id,
                "version_number": submitted.version_number,
                "supersedes_version_id": prior.version_id,
            },
        )
        return self._prepare_pending_offer_artifacts(submitted)

    def extend_offer_deadline(
        self,
        application_id: str,
        version_id: str,
        *,
        reply_by_date: date,
        actor: str,
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        version = self.store.get_offer_version(version_id)
        if application.stage is not HiringStage.OFFER_SENT or version.application_id != application_id:
            raise ValueError("Only a sent offer deadline may be extended.")
        extended = self.store.update_operational_reply_by_date(version_id, reply_by_date)
        self.store.append_event(
            application_id,
            "offer_deadline_extended",
            actor=actor,
            payload={
                "version_id": version_id,
                "operational_reply_by_date": reply_by_date.isoformat(),
                "document_unchanged": True,
            },
        )
        return extended

    def accept_offer(
        self,
        application_id: str,
        version_id: str,
        *,
        actor: str,
    ) -> HiringApplication:
        application = self.store.get_application(application_id)
        versions = self.store.list_offer_versions(application_id)
        sent_versions = [version for version in versions if version.status == "sent"]
        latest = max(sent_versions, key=lambda version: version.version_number, default=None)
        if application.stage is not HiringStage.OFFER_SENT or latest is None:
            raise ValueError("Application has no sent offer to accept.")
        if latest.version_id != version_id:
            raise ValueError("Only the latest sent offer version may be accepted.")
        self.store.set_offer_status(version_id, "accepted")
        accepted = self.store.update_application_stage(application_id, HiringStage.ACCEPTED)
        self.store.append_event(
            application_id,
            "offer_accepted",
            actor=actor,
            payload={
                "version_id": version_id,
                "version_number": latest.version_number,
                "onboarding_ready": True,
            },
        )
        if self._notify_offer_accepted is not None:
            self.retry_accepted_notification(application_id, version_id)
        return self.store.get_application(accepted.application_id)

    def retry_accepted_notification(self, application_id: str, version_id: str) -> bool:
        application = self.store.get_application(application_id)
        version = self.store.get_offer_version(version_id)
        if application.stage is not HiringStage.ACCEPTED or version.status != "accepted":
            raise ValueError("Only an accepted offer notification may be retried.")
        if self._notify_offer_accepted is None:
            return False
        candidate = self.store.get_candidate(application.candidate_id)
        results = list(
            self._notify_offer_accepted(
                candidate, version, f"offer:{version.version_id}:accepted"
            )
        )
        successful = bool(results) and all(
            str(getattr(result, "status", "")) in {"sent", "duplicate"}
            for result in results
        )
        self.store.update_application_stage(
            application_id,
            HiringStage.ACCEPTED,
            attention_code="" if successful else "accepted_notification_pending",
        )
        return successful

    def retry_pending_accepted_notifications(self) -> int:
        completed = 0
        for application in self.store.list_applications(include_archived=False):
            if (
                application.stage is not HiringStage.ACCEPTED
                or application.attention_code != "accepted_notification_pending"
            ):
                continue
            accepted_versions = [
                version
                for version in self.store.list_offer_versions(application.application_id)
                if version.status == "accepted"
            ]
            if not accepted_versions:
                continue
            latest = max(accepted_versions, key=lambda version: version.version_number)
            completed += int(
                self.retry_accepted_notification(application.application_id, latest.version_id)
            )
        return completed

    def archive_application(self, application_id: str, *, actor: str) -> HiringApplication:
        archived = self.store.archive_application(application_id)
        self.store.append_event(
            application_id,
            "application_archived",
            actor=actor,
            payload={},
        )
        return archived

    def refresh_expired_offer_attention(self, *, today: date | None = None) -> int:
        current = today or date.today()
        changed = 0
        for application in self.store.list_applications():
            if application.stage is not HiringStage.OFFER_SENT:
                continue
            versions = self.store.list_offer_versions(application.application_id)
            sent = [version for version in versions if version.status == "sent"]
            if not sent:
                continue
            latest = max(sent, key=lambda version: version.version_number)
            deadline_text = latest.operational_reply_by_date or latest.document_reply_by_date
            if not deadline_text:
                continue
            try:
                overdue = date.fromisoformat(deadline_text) < current
            except ValueError:
                overdue = True
            attention = "offer_overdue" if overdue else ""
            if application.attention_code == attention:
                continue
            self.store.update_application_stage(
                application.application_id,
                HiringStage.OFFER_SENT,
                attention_code=attention,
            )
            changed += 1
        return changed

    def approve_offer(
        self,
        application_id: str,
        version_id: str,
        *,
        approver_name: str,
        approver_role: str,
        approval_date: date,
        docx_path: Path,
        pdf_path: Path,
        rendered_email: str = "",
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        version = self.store.get_offer_version(version_id)
        clean_name = _normalized_text(approver_name)
        clean_role = _normalized_text(approver_role)
        if application.stage is not HiringStage.EXECUTIVE_APPROVAL:
            raise ValueError("Application is not awaiting executive approval.")
        if version.application_id != application_id or version.status != "pending_approval":
            raise ValueError("Only the selected pending offer version may be approved.")
        if not clean_name or not clean_role:
            raise ValueError("Approver name and role are required.")
        safe_docx = self._validated_artifact_path(docx_path, ".docx")
        safe_pdf = self._validated_artifact_path(pdf_path, ".pdf")
        candidate = self.store.get_candidate(application.candidate_id)
        if not self._valid_email(candidate.email):
            raise ValueError("Candidate email is missing or invalid.")

        approved = self.store.record_offer_approval(
            version_id,
            dates=calculate_offer_approval_dates(approval_date),
            docx_path=safe_docx,
            pdf_path=safe_pdf,
            approver_name=clean_name,
            approver_role=clean_role,
            rendered_email=rendered_email,
        )
        self.store.append_event(
            application_id,
            "offer_approved",
            actor=clean_name,
            payload={"version_id": version_id, "version_number": approved.version_number},
        )
        return self._deliver_approved_offer(application, approved)

    def approve_compensation_revision(
        self,
        application_id: str,
        version_id: str,
        *,
        admin_name: str,
        approval_date: date,
        docx_path: Path,
        pdf_path: Path,
        rendered_email: str = "",
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        version = self.store.get_offer_version(version_id)
        clean_name = _normalized_text(admin_name)
        if application.stage is not HiringStage.OFFER_SENT:
            raise ValueError("Application does not have a sent offer to revise.")
        if version.application_id != application_id or version.status != "pending_approval":
            raise ValueError("Only the pending compensation revision may be approved.")
        if not clean_name:
            raise ValueError("Admin name is required.")
        approved = self.store.record_offer_approval(
            version_id,
            dates=calculate_offer_approval_dates(approval_date),
            docx_path=self._validated_artifact_path(docx_path, ".docx"),
            pdf_path=self._validated_artifact_path(pdf_path, ".pdf"),
            approver_name=clean_name,
            approver_role="Admin",
            rendered_email=rendered_email,
        )
        self.store.append_event(
            application_id,
            "compensation_revision_approved",
            actor=clean_name,
            payload={"version_id": version_id, "version_number": approved.version_number},
        )
        return self._deliver_approved_offer(application, approved)

    def retry_offer_send(
        self,
        application_id: str,
        version_id: str,
        *,
        actor: str,
    ) -> OfferVersion:
        application = self.store.get_application(application_id)
        version = self.store.get_offer_version(version_id)
        if version.application_id != application_id:
            raise ValueError("Offer version does not belong to this application.")
        if version.status == "sent":
            return version
        if version.status != "approved" or version.send_status != "failed":
            raise ValueError("Only an approved offer with failed delivery may be retried.")
        self.store.append_event(
            application_id,
            "offer_send_retried",
            actor=actor,
            payload={"version_id": version_id, "version_number": version.version_number},
        )
        return self._deliver_approved_offer(application, version)

    def _deliver_approved_offer(
        self,
        application: HiringApplication,
        approved: OfferVersion,
    ) -> OfferVersion:
        version_id = approved.version_id
        if self._send_offer is None:
            failed = self.store.record_offer_delivery(
                version_id, sent=False, error="Offer delivery is not configured."
            )
            self.store.update_application_stage(
                application.application_id,
                application.stage,
                attention_code="approved_send_failed",
            )
            return failed

        try:
            safe_pdf = self._validated_artifact_path(Path(approved.pdf_path), ".pdf")
            candidate = self.store.get_candidate(application.candidate_id)
            results = self._send_offer(
                candidate,
                approved,
                safe_pdf,
                f"offer-version:{version_id}",
            )
            sent = bool(results) and all(
                str(getattr(result, "status", "")).casefold() in {"sent", "duplicate"}
                for result in results
            )
            error = "" if sent else self._safe_delivery_error(results)
        except Exception as exc:
            sent = False
            error = self._redacted_error(exc)

        delivered = self.store.record_offer_delivery(version_id, sent=sent, error=error)
        if sent:
            self.store.update_application_stage(application.application_id, HiringStage.OFFER_SENT)
            self.store.append_event(
                application.application_id,
                "offer_sent",
                actor="system",
                payload={"version_id": version_id, "version_number": delivered.version_number},
            )
            return delivered
        self.store.update_application_stage(
            application.application_id,
            application.stage,
            attention_code="approved_send_failed",
        )
        return delivered

    @staticmethod
    def _validated_artifact_path(value: Path, suffix: str) -> Path:
        path = Path(value).expanduser().resolve()
        if path.suffix.casefold() != suffix or not path.is_file():
            raise ValueError(f"Validated {suffix} offer artifact is required.")
        return path

    @staticmethod
    def _valid_email(value: str) -> bool:
        return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(value or "").strip()))

    @staticmethod
    def _positive_decimal(value: str, label: str) -> Decimal:
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label} must be a number.") from None
        if not number.is_finite() or number <= 0:
            raise ValueError(f"{label} must be greater than zero.")
        return number

    @classmethod
    def _safe_delivery_error(cls, results: list[Any]) -> str:
        messages = [str(getattr(result, "error", "")) for result in results]
        return cls._redacted_error("; ".join(message for message in messages if message))

    @staticmethod
    def _redacted_error(value: object) -> str:
        text = str(value or "Offer delivery failed.")
        text = re.sub(r"[^\s@]+@[^\s@]+", "[redacted-email]", text)
        text = re.sub(r"(?i)(password|token|secret|api[_ -]?key)\s*[:=]\s*\S+", r"\1=[redacted]", text)
        return text[:500] or "Offer delivery failed."

    @staticmethod
    def _history_score(row: dict[str, Any]) -> float:
        raw = row.get("score", row.get("percent_of_max", row.get("interview_score", 0)))
        text = str(raw or "0").strip().removesuffix("%")
        return float(text or 0)
