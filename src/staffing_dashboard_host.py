from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from candidate_report import (
    CandidateReportNotFoundError,
    CandidateReportPermissionError,
    CandidateReportRepository,
    resolve_legacy_report_path,
)
from candidate_report_dialog import CandidateInterviewReportDialog
from data_store import InterviewHistoryStore
from notification_models import NotificationTestPayload
from notification_templates import notification_payload_from_mapping
from staffing_dashboard_v2 import StaffingDashboardV2Page
from staffing_service import StaffingService
from staffing_store import StaffingStore


StaffingDashboardRole = Literal["admin", "director"]


@dataclass(frozen=True)
class StaffingDashboardAccess:
    role: StaffingDashboardRole
    actor: str
    school_scope: str = ""
    removal_source: str = ""

    def __post_init__(self) -> None:
        role = str(self.role or "").strip().lower()
        if role not in {"admin", "director"}:
            raise ValueError("Staffing dashboard role must be admin or director.")
        actor = str(self.actor or "").strip() or role
        school_scope = str(self.school_scope or "").strip()
        removal_source = str(self.removal_source or "").strip() or f"{role}_staffing_dashboard"
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "school_scope", school_scope)
        object.__setattr__(self, "removal_source", removal_source)


class StaffingDashboardHost:
    """Shared Staffing v2 composition and candidate-report controller."""

    def __init__(
        self,
        *,
        QtCore: Any,
        QtGui: Any,
        QtWidgets: Any,
        parent: Any,
        store: StaffingStore,
        service_factory: Callable[[], StaffingService],
        access: StaffingDashboardAccess,
        history_path: Path,
        notification_store_path: Path,
        notification_service_factory: Callable[[], Any] | None = None,
        director_referral_dismissal_callback: Callable[[list[Any], str, str], None] | None = None,
        rubric: dict[str, Any] | None = None,
        finalized_callback: Callable[[Any], None] | None = None,
        open_document: Callable[[Path], None] | None = None,
        actions: dict[str, Callable[[int], None]] | None = None,
        app_version: str = "",
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.parent = parent
        self.store = store
        self.service_factory = service_factory
        self.access = access
        self.history_path = Path(history_path)
        self.rubric = dict(rubric or {})
        self.finalized_callback = finalized_callback if access.role == "admin" else None
        self.open_document = open_document or self._default_open_document
        self.app_version = str(app_version or "")
        self.candidate_report_dialog: Any | None = None
        self.page = StaffingDashboardV2Page(
            QtCore=QtCore,
            QtGui=QtGui,
            QtWidgets=QtWidgets,
            store=store,
            service_factory=service_factory,
            actions=actions,
            school_filter=access.school_scope,
            notification_store_path=notification_store_path,
            notification_service_factory=notification_service_factory,
            notification_test_payload_provider=self.notification_test_payloads,
            director_referral_dismissal_callback=director_referral_dismissal_callback,
            candidate_report_open_callback=self.open_candidate_report,
            director_referral_removal_actor=access.actor,
            director_referral_removal_source=access.removal_source,
        )

    def notification_test_payloads(self, event_type: str) -> list[NotificationTestPayload]:
        event = str(event_type or "").strip()
        if not (event.startswith("offer.") or event.startswith("interview.rating.")):
            return []
        rows = InterviewHistoryStore(self.history_path).load()
        options: list[NotificationTestPayload] = []
        for row in reversed(rows):
            school = str(row.get("school", "") or "").strip()
            if self.access.role == "director" and self.access.school_scope:
                if school.casefold() != self.access.school_scope.casefold():
                    continue
            offer_path = str(row.get("offer_path", "") or "").strip()
            offer_pdf = ""
            if offer_path:
                candidate_pdf = Path(offer_path).with_suffix(".pdf")
                if candidate_pdf.is_file():
                    offer_pdf = str(candidate_pdf)
            candidate = str(row.get("candidate", row.get("candidate_name", "")) or "").strip()
            payload = notification_payload_from_mapping(self._notification_payload_source(row, school))
            for key, value in {
                "candidate": candidate,
                "candidate_name": candidate,
                "candidate_email": str(row.get("candidate_email", "") or ""),
                "school": school,
                "position": str(row.get("position", "") or ""),
                "offer_status": str(row.get("offer_status", "") or ""),
                "offer_path": offer_path,
                "offer_pdf_path": offer_pdf,
                "interview_date": str(row.get("interview_date", row.get("date", "")) or ""),
                "history_id": str(row.get("id", row.get("history_id", "")) or ""),
                "outcome": str(row.get("outcome", "") or ""),
                "score": str(row.get("score", "") or ""),
            }.items():
                if value or key not in payload:
                    payload[key] = value
            options.append(
                NotificationTestPayload(
                    label=f"{candidate or 'Candidate'} · {school or 'No school'} · {payload['interview_date'] or 'No date'}",
                    event_type=event,
                    payload=payload,
                    source_kind="interview_history",
                )
            )
            if len(options) >= 10:
                break
        return options

    def _notification_payload_source(self, row: dict[str, Any], school: str) -> dict[str, Any]:
        source = dict(row)
        history_id = str(row.get("id", row.get("history_id", "")) or "").strip()
        if not history_id:
            return source
        try:
            repository = CandidateReportRepository(self.history_path)
            if not repository.exists(history_id):
                return source
            record = repository.load_visible_version(
                history_id,
                role=self.access.role,
                school_scope=self.access.school_scope if self.access.role == "director" else "",
            )
        except (CandidateReportNotFoundError, CandidateReportPermissionError, sqlite3.DatabaseError, OSError):
            return source
        snapshot = dict(record.snapshot)
        snapshot.setdefault("history_id", history_id)
        snapshot.setdefault("position", row.get("position", ""))
        snapshot.setdefault("offer_status", row.get("offer_status", ""))
        snapshot.setdefault("school", school)
        return {**source, **snapshot}

    @property
    def widget(self) -> Any:
        return self.page.widget

    def open_candidate_report(self, history_id: str, school: str) -> None:
        history_key = str(history_id or "").strip()
        row_school = str(school or "").strip()
        if self.access.role == "director" and self.access.school_scope:
            if row_school.casefold() != self.access.school_scope.casefold():
                self._warn("Candidate report is outside the director school scope.")
                return
        repository = CandidateReportRepository(self.history_path)
        if not repository.exists(history_key):
            self._open_legacy_report(history_key)
            return
        try:
            director_interview = self.store.find_any_completed_director_interview(
                history_id=history_key,
                school=row_school,
            )
            dialog = CandidateInterviewReportDialog(
                QtCore=self.QtCore,
                QtGui=self.QtGui,
                QtWidgets=self.QtWidgets,
                parent=self.parent,
                repository=repository,
                history_id=history_key,
                role=self.access.role,
                actor=self.access.actor,
                school_scope=self.access.school_scope if self.access.role == "director" else "",
                rubric=self.rubric,
                director_interview=director_interview,
                director_service=self.service_factory(),
                open_document=self.open_document,
                finalized_callback=self.finalized_callback,
                app_version=self.app_version,
            )
        except (CandidateReportNotFoundError, CandidateReportPermissionError, OSError, sqlite3.DatabaseError) as exc:
            self._warn(str(exc))
            return
        self.candidate_report_dialog = dialog
        dialog.show()

    def _open_legacy_report(self, history_id: str) -> None:
        try:
            path = resolve_legacy_report_path(
                self.history_path,
                history_id,
                school_scope=self.access.school_scope if self.access.role == "director" else "",
            )
            self.open_document(path.resolve())
        except (CandidateReportNotFoundError, CandidateReportPermissionError, OSError, sqlite3.DatabaseError) as exc:
            self._warn(str(exc))

    def _warn(self, message: str) -> None:
        self.QtWidgets.QMessageBox.warning(
            self.parent,
            "Candidate Interview Report",
            str(message or "Candidate interview report could not be opened."),
        )

    @staticmethod
    def _default_open_document(path: Path) -> None:
        os.startfile(str(path))
