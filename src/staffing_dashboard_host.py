from __future__ import annotations

import os
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
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
from notification_service import (
    NotificationService,
    load_email_account_settings,
    load_notification_directory,
    migrate_legacy_onboarding_email_account,
    resolve_onboarding_role_recipient,
    send_onboarding_reminder_digest,
)
from notification_store import NotificationStore
from notification_templates import notification_payload_from_mapping
from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_scheduler_v2 import OnboardingAutomaticReminderScheduler
from onboarding_paths import OnboardingPaths, migrate_legacy_onboarding_artifacts
from onboarding_pilot_gate import enabled_director_schools
from onboarding_store import OnboardingStore
from onboarding_sync import OnboardingChangeStage, OnboardingSyncConflict, OnboardingSyncCoordinator
from onboarding_staffing_bridge import StaffingDirectorResolver
from onboarding_workspace_v2 import OnboardingDashboardV2Workspace
from onboarding_vault import EncryptedArtifactVault, OnboardingKeyring, OnboardingVault, load_or_create_device_vault
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
        onboarding_store_path: Path | None = None,
        onboarding_vault: OnboardingVault | None = None,
        onboarding_pilot_schools: tuple[str, ...] = ("Palmdale",),
        onboarding_rollout_path: Path | None = None,
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
        self.onboarding_store: OnboardingStore | None = None
        self.onboarding_workspace: OnboardingDashboardV2Workspace | None = None
        self.onboarding_sync: OnboardingSyncCoordinator | None = None
        self.onboarding_scheduler: OnboardingAutomaticReminderScheduler | None = None
        self.onboarding_sync_timer: Any | None = None
        self.onboarding_paths = OnboardingPaths.from_user_artifacts(Path(notification_store_path).parent)
        self._notification_store_path = Path(notification_store_path)
        self._onboarding_store_path = Path(onboarding_store_path) if onboarding_store_path is not None else None
        self._onboarding_vault = onboarding_vault
        enabled_schools = {str(school or "").strip().casefold() for school in onboarding_pilot_schools}
        rollout_path = (
            Path(onboarding_rollout_path)
            if onboarding_rollout_path is not None
            else self.onboarding_paths.root / "pilot" / "evidence.jsonl"
        )
        enabled_schools.update(school.casefold() for school in enabled_director_schools(rollout_path))
        self._onboarding_enabled = access.role == "admin" or access.school_scope.casefold() in enabled_schools
        if self._onboarding_enabled:
            self._register_lazy_onboarding_pages()

    def _register_lazy_onboarding_pages(self) -> None:
        self.page.register_external_section("onboarding", "ONBOARDING")
        for page_id, label, factory_key in OnboardingDashboardV2Workspace._PAGES:
            if factory_key == "templates" and self.access.role != "admin":
                continue
            self.page.register_external_page(
                "onboarding",
                page_id,
                label,
                provider=lambda key=factory_key: self._onboarding_page_widget(key),
                before_leave=self._before_leaving_onboarding,
            )

    def _before_leaving_onboarding(self) -> bool:
        workspace = self.onboarding_workspace
        return True if workspace is None else bool(workspace.request_navigation_away())

    def _onboarding_page_widget(self, factory_key: str) -> Any:
        workspace = self.ensure_onboarding_workspace()
        if workspace is None:
            raise ValueError("Onboarding is unavailable for this staffing scope.")
        return workspace.build_page(factory_key)

    def ensure_onboarding_workspace(self) -> OnboardingDashboardV2Workspace | None:
        if self.onboarding_workspace is not None:
            return self.onboarding_workspace
        if not self._onboarding_enabled:
            return None
        migration_schools = (self.access.school_scope,) if self.access.role == "director" else ()
        migrate_legacy_onboarding_artifacts(self.onboarding_paths, schools=migration_schools)
        onboarding_path = self._onboarding_store_path or self._default_onboarding_store_path(
            self._notification_store_path
        )
        vault = self._onboarding_vault or self._resolve_onboarding_vault(self.onboarding_paths.root)
        self.onboarding_store = OnboardingStore(onboarding_path, vault=vault)
        replica = "admin" if self.access.role == "admin" else f"director:{self.access.school_scope.casefold()}"
        self.onboarding_sync = OnboardingSyncCoordinator(
            store=self.onboarding_store,
            stage=OnboardingChangeStage(self.onboarding_paths.change_stage),
            vault=vault,
            replica=replica,
            school_scope=self.access.school_scope if self.access.role == "director" else "",
            conflict_resolver=self._resolve_onboarding_sync_conflict,
            artifact_root=self.onboarding_paths.encrypted_files,
        )
        self.onboarding_sync.replay_pending()
        director_resolver = StaffingDirectorResolver(self.store)
        device_cache_path = self._onboarding_device_cache_path(self.onboarding_paths.keyring)
        artifact_vault = EncryptedArtifactVault(
            self.onboarding_paths.encrypted_files,
            device_cache_path.parent / "temp" / device_cache_path.stem,
            vault=vault,
        )
        artifact_vault.cleanup_stale()
        shared_artifacts = self._notification_store_path.parent
        email_settings_path = shared_artifacts / "email_account_settings.json"
        directory_path = shared_artifacts / "notification_directory.json"

        def dispatch_onboarding_notification(
            event_type: str, payload: dict[str, str], idempotency_key: str
        ) -> object:
            return NotificationService(
                store=NotificationStore(self._notification_store_path),
                email_settings=load_email_account_settings(email_settings_path),
                directory=load_notification_directory(directory_path),
            ).emit_event(event_type, payload, idempotency_key)

        onboarding_service = OnboardingService(
            self.onboarding_store,
            OnboardingAccess(
                role=self.access.role,
                actor=self.access.actor,
                school_scope=self.access.school_scope,
            ),
            sync=self.onboarding_sync,
            director_resolver=director_resolver,
            device_cache_path=device_cache_path,
            artifact_vault=artifact_vault,
            notification_dispatcher=dispatch_onboarding_notification,
        )
        migrate_legacy_onboarding_email_account(
            legacy_path=shared_artifacts / "interviews" / "onboarding_settings.json",
            shared_path=email_settings_path,
        )

        def reminder_recipient(school: str, role: str) -> str:
            configured = resolve_onboarding_role_recipient(
                load_notification_directory(directory_path),
                school=school,
                role=role,
                director_resolver=director_resolver,
            )
            if configured:
                return configured
            recipient, warning = onboarding_service.resolve_owner_recipient(
                role=role,
                school=school,
                admin_fallback_email=load_notification_directory(directory_path).hr_manager,
            )
            return "" if warning else recipient

        def reminder_sender(message: Any) -> None:
            send_onboarding_reminder_digest(
                load_email_account_settings(email_settings_path),
                message,
                rule_store=NotificationStore(self._notification_store_path),
            )

        def reminder_config_revision() -> str:
            digest = hashlib.sha256()
            for path in (email_settings_path, directory_path, self._notification_store_path):
                digest.update(path.read_bytes() if path.is_file() else b"")
            return digest.hexdigest()

        self.onboarding_workspace = OnboardingDashboardV2Workspace(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            service=onboarding_service,
            reminder_recipient_resolver=reminder_recipient,
            admin_fallback_email=lambda: load_notification_directory(directory_path).hr_manager,
            reminder_sender=reminder_sender,
            reminder_config_revision=reminder_config_revision,
        )
        if self.access.role == "admin":
            self.onboarding_scheduler = OnboardingAutomaticReminderScheduler(
                onboarding_service,
                recipient_resolver=reminder_recipient,
                admin_fallback_email=lambda: load_notification_directory(directory_path).hr_manager,
                sender=reminder_sender,
                config_revision=reminder_config_revision,
            )
        self.onboarding_sync_timer = self.QtCore.QTimer(self.parent)
        self.onboarding_sync_timer.setInterval(15_000)
        self.onboarding_sync_timer.timeout.connect(self._sync_onboarding_timer)
        self.onboarding_sync_timer.start()
        return self.onboarding_workspace

    def warm_onboarding(self) -> bool:
        try:
            return self.ensure_onboarding_workspace() is not None
        except (OSError, ValueError, sqlite3.DatabaseError):
            return False

    def sync_onboarding(self) -> int:
        if self.onboarding_sync is None:
            return 0
        return self.onboarding_sync.replay_pending()

    def request_onboarding_close(self) -> bool:
        if self.onboarding_workspace is not None and not self.onboarding_workspace.request_close():
            return False
        return True

    def cleanup_onboarding(self) -> None:
        if self.onboarding_sync_timer is not None:
            self.onboarding_sync_timer.stop()
        if self.onboarding_workspace is not None:
            self.onboarding_workspace.service.cleanup_decrypted_artifacts()

    def resume_onboarding(self) -> None:
        if self.onboarding_sync_timer is not None and not self.onboarding_sync_timer.isActive():
            self.onboarding_sync_timer.start()

    def _sync_onboarding_timer(self) -> None:
        try:
            self.sync_onboarding()
            if self.onboarding_scheduler is not None:
                self.onboarding_scheduler.run_if_due(datetime.now().astimezone())
        except (OSError, ValueError, sqlite3.DatabaseError):
            return

    def _resolve_onboarding_sync_conflict(self, conflict: OnboardingSyncConflict) -> bool | str:
        if self.onboarding_workspace is not None:
            if not self.onboarding_workspace.request_navigation_away():
                return "defer"
        fields = ", ".join(field.replace("_", " ").title() for field in conflict.fields)
        local_state = ", ".join(f"{name}: {value}" for name, value in conflict.local_values) or "Unavailable"
        incoming_state = ", ".join(f"{name}: {value}" for name, value in conflict.incoming_values) or "Unavailable"
        entity_label = conflict.entity_type.replace("_", " ").title()
        answer = self.QtWidgets.QMessageBox.question(
            self.parent,
            "Onboarding Sync Conflict",
            (
                "Another onboarding replica changed the same field.\n\n"
                f"{entity_label} {conflict.entity_id}\n"
                f"School: {conflict.school}\nFields: {fields}\n"
                f"Local version: {conflict.local_version}\n"
                f"Incoming version: {conflict.incoming_version}\n\n"
                f"Local: {local_state}\nIncoming: {incoming_state}\n\n"
                "Yes — Use Incoming\nNo — Keep Local"
            ),
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == self.QtWidgets.QMessageBox.StandardButton.Yes

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

    def _default_onboarding_store_path(self, notification_store_path: Path) -> Path:
        if self.access.role == "admin":
            return self.onboarding_paths.admin_replica
        return self.onboarding_paths.director_replica(self.access.school_scope)

    def _resolve_onboarding_vault(self, shared_root: Path) -> OnboardingVault:
        keyring_path = Path(shared_root) / "keyring.json"
        if not keyring_path.exists() and os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
            return load_or_create_device_vault(Path(shared_root) / "onboarding_vault_key.dpapi")
        cache_path = self._onboarding_device_cache_path(keyring_path)
        if cache_path.exists():
            return OnboardingVault.from_device_cache(cache_path)
        if keyring_path.exists():
            passphrase = str(os.environ.get("ONBOARDING_KEYRING_PASSPHRASE") or "")
            if not passphrase:
                passphrase = self._prompt_onboarding_secret("Unlock Onboarding", "Organization passphrase:")
            vault = OnboardingKeyring(keyring_path).unlock_with_passphrase(passphrase)
            vault.cache_for_device(cache_path)
            return vault
        if self.access.role != "admin":
            raise ValueError("Onboarding keyring is not initialized. An admin must open Onboarding first.")
        passphrase = self._prompt_onboarding_secret(
            "Set Up Onboarding Encryption",
            "Create an organization passphrase (12+ characters):",
        )
        recovery_key = self._prompt_onboarding_secret(
            "Set Up Onboarding Recovery",
            "Enter the organization recovery key (12+ characters):",
        )
        vault = OnboardingKeyring.create(keyring_path, passphrase=passphrase, recovery_key=recovery_key)
        vault.cache_for_device(cache_path)
        return vault

    def _prompt_onboarding_secret(self, title: str, label: str) -> str:
        value, accepted = self.QtWidgets.QInputDialog.getText(
            self.parent,
            title,
            label,
            self.QtWidgets.QLineEdit.EchoMode.Password,
        )
        if not accepted or not str(value or ""):
            raise ValueError("Onboarding encryption unlock was cancelled.")
        return str(value)

    @staticmethod
    def _onboarding_device_cache_path(keyring_path: Path) -> Path:
        identity = hashlib.sha256(str(Path(keyring_path).resolve()).casefold().encode("utf-8")).hexdigest()[:20]
        local_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return local_root / "LPL_InterviewTool" / "onboarding_keys" / f"{identity}.dpapi"
