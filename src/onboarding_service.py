from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import wraps
import io
import json
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4
import re

from onboarding_attachments import WindowsDefenderAttachmentScanner, validate_task_attachment
from onboarding_store import DocumentPackageVersion, FilledArtifact, IntakeField, IntakeSubmission, OnboardingEmployee, OnboardingStore, OnboardingTask, OwnerRoleConfig, PackageDocument, PdfFieldMapping, TaskAttachment, TaskComment, TaskCommentRevision, TaskTemplateAttachment, TaskTemplateVersion
from onboarding_reminders_v2 import OnboardingReminderCoordinator, ReminderPreview, ReminderPreviewMessage, ReminderSendResult
from onboarding_staffing_bridge import DirectorIdentity
from onboarding_vault import EncryptedArtifactVault, OnboardingVault

if TYPE_CHECKING:
    from onboarding_pdf_fill import AcroFormField
    from onboarding_sync import OnboardingSyncCoordinator


OnboardingRole = Literal["admin", "director"]


def _sync_mutation(method: Callable) -> Callable:
    @wraps(method)
    def wrapped(self: "OnboardingService", *args: object, **kwargs: object) -> object:
        self.sync_pending()
        result = method(self, *args, **kwargs)
        self._replay_after_mutation()
        return result
    return wrapped


def _sync_after_mutation(method: Callable) -> Callable:
    @wraps(method)
    def wrapped(self: "OnboardingService", *args: object, **kwargs: object) -> object:
        result = method(self, *args, **kwargs)
        self._replay_after_mutation()
        return result
    return wrapped
DEPARTURE_CATEGORIES = {
    "voluntary_resignation",
    "involuntary_termination",
    "job_abandonment",
    "transfer",
    "end_of_temporary_or_contract_role",
    "other",
}
INTAKE_FIELD_TYPES = {
    "short_text", "long_text", "date", "email", "phone", "ssn", "number",
    "yes_no", "single_choice", "multiple_choice", "signature", "initials",
}
DID_NOT_START_REASONS = {"candidate_withdrew", "offer_rescinded", "no_show", "other"}
EMPLOYEE_PROFILE_FIELDS = {
    "legal_name", "preferred_name", "role", "acceptance_date", "start_date",
    "address_line1", "address_line2", "city", "state", "postal_code",
    "personal_email", "work_email", "phone", "notes", "source_history_id", "dob", "ssn",
}
TASK_EDIT_FIELDS = {
    "title", "owner_role", "watcher_roles", "due_date", "critical",
    "dependency_ids", "parent_task_id", "required", "notes",
}


@dataclass(frozen=True)
class OnboardingAccess:
    role: OnboardingRole
    actor: str
    school_scope: str = ""

    def __post_init__(self) -> None:
        role = str(self.role or "").strip().casefold()
        actor = str(self.actor or "").strip()
        scope = str(self.school_scope or "").strip()
        if role not in {"admin", "director"}:
            raise ValueError("Onboarding role must be admin or director.")
        if not actor:
            raise ValueError("Onboarding actor is required.")
        if role == "director" and not scope:
            raise ValueError("Director onboarding access requires a school scope.")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "school_scope", scope)


class OnboardingPermissionError(PermissionError):
    pass


@dataclass(frozen=True)
class RetentionPurgeCandidate:
    employee_id: str
    school: str
    last_working_day: str
    departure_category: str
    eligible_on: str


@dataclass(frozen=True)
class TaskTemplateUpgradePreview:
    employee_id: str
    task_id: str
    template_id: str
    template_key: str
    from_version: int
    to_version: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True)
class PdfMappingFitResult:
    mapping_id: str
    fits: bool
    detail: str = ""


@dataclass(frozen=True)
class PdfMappingPreviewResult:
    output_path: Path | None
    acroform_fields: tuple[AcroFormField, ...]
    synthetic_values: tuple[tuple[str, object], ...]
    overflow_errors: tuple[str, ...]
    required_signatures: tuple[str, ...]
    mapping_results: tuple[PdfMappingFitResult, ...] = ()


@dataclass(frozen=True)
class TaskMetrics:
    open: int
    blocked: int
    blocked_overdue: int
    overdue: int
    actionable_overdue: int
    completed: int


@dataclass(frozen=True)
class GeneratedPackageArtifacts:
    individual_artifact_ids: tuple[str, ...]
    merged_artifact_id: str
    manifest_artifact_id: str
    sealed_paths: tuple[Path, ...]


class OnboardingService:
    """Toolkit-neutral onboarding workflow with service-enforced access."""

    def __init__(
        self,
        store: OnboardingStore,
        access: OnboardingAccess,
        *,
        attachment_scanner: Callable[[Path], str] | None = None,
        sync: OnboardingSyncCoordinator | None = None,
        director_resolver: Callable[[str], DirectorIdentity] | None = None,
        device_cache_path: Path | None = None,
        artifact_vault: EncryptedArtifactVault | None = None,
        notification_dispatcher: Callable[[str, dict[str, str], str], object] | None = None,
    ) -> None:
        self.store = store
        self.access = access
        self.attachment_scanner = attachment_scanner or WindowsDefenderAttachmentScanner()
        self.sync = sync
        self.reminders = OnboardingReminderCoordinator(store, role=access.role)
        self.director_resolver = director_resolver
        self.device_cache_path = None if device_cache_path is None else Path(device_cache_path)
        self.artifact_vault = artifact_vault
        self.notification_dispatcher = notification_dispatcher
        self.last_notification_outcome = "not_configured"
        self.last_sync_error = ""

    @property
    def onboarding_locked(self) -> bool:
        return self.store.vault.is_locked

    def lock_onboarding(self) -> None:
        self.sync_pending()
        self.cleanup_decrypted_artifacts()
        self.store.vault.lock()

    def forget_device(self) -> None:
        if self.device_cache_path is None:
            raise ValueError("Onboarding device cache path is unavailable.")
        self.sync_pending()
        self.cleanup_decrypted_artifacts()
        self.store.vault.forget_device(self.device_cache_path)
        self.store.vault.lock()

    def cleanup_decrypted_artifacts(self) -> int:
        return 0 if self.artifact_vault is None else self.artifact_vault.cleanup_stale()

    def sync_pending(self) -> int:
        return 0 if self.sync is None else self.sync.replay_pending()

    def sync_health(self) -> object:
        return None if self.sync is None else self.sync.health()

    def list_sync_conflicts(self) -> tuple[object, ...]:
        return () if self.sync is None else self.sync.conflicts()

    def _replay_after_mutation(self) -> None:
        if self.sync is not None and hasattr(self.sync, "conflict_resolver"):
            if self.sync.conflict_resolver is None:
                return
        try:
            self.sync_pending()
            self.last_sync_error = ""
        except (OSError, ValueError) as exc:
            self.last_sync_error = type(exc).__name__

    def preview_legacy_import(self, source_path: Path) -> Any:
        self._require_admin("Legacy onboarding migration")
        from onboarding_migrations import preview_legacy_json_migration

        return preview_legacy_json_migration(source_path)

    @_sync_mutation
    def import_legacy_data(
        self,
        source_path: Path,
        *,
        backup_dir: Path,
        expected_sha256: str,
        confirmation: str,
    ) -> Any:
        self._require_admin("Legacy onboarding migration")
        if str(confirmation or "").strip() != "IMPORT":
            raise ValueError("Legacy onboarding migration requires typed IMPORT confirmation.")
        from onboarding_migrations import migrate_legacy_json_to_v2

        result = migrate_legacy_json_to_v2(
            source_path,
            service=self,
            backup_dir=backup_dir,
            confirmed=True,
            expected_sha256=expected_sha256,
        )
        self.store.append_audit_event(
            entity_id=f"legacy-import:{result.preview.source_sha256[:12]}",
            action="migration.legacy_json_imported",
            actor=self.access.actor,
            school="*",
            version=1,
            details={
                "source_sha256": result.preview.source_sha256,
                "imported_employees": result.imported_employees,
                "imported_tasks": result.imported_tasks,
            },
            created_at=_utc_now(),
        )
        return result

    @_sync_mutation
    def create_employee(
        self,
        *,
        legal_name: str,
        school: str,
        role: str,
        acceptance_date: str,
        start_date: str,
        preferred_name: str = "",
        address_line1: str = "",
        address_line2: str = "",
        city: str = "",
        state: str = "",
        postal_code: str = "",
        personal_email: str = "",
        work_email: str = "",
        phone: str = "",
        notes: str = "",
        source_history_id: str = "",
        dob: str = "",
        ssn: str = "",
    ) -> OnboardingEmployee:
        clean_name = _required_text(legal_name, "Legal name")
        clean_school = _required_text(school, "School")
        duplicate_review = bool(
            self.store.possible_duplicate_employees(legal_name=clean_name, school=clean_school)
        )
        employee = self._new_employee(
            legal_name=clean_name,
            preferred_name=preferred_name,
            school=clean_school,
            role=role,
            acceptance_date=acceptance_date,
            start_date=start_date,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            postal_code=postal_code,
            personal_email=personal_email,
            work_email=work_email,
            phone=phone,
            notes=notes,
            source_history_id=source_history_id,
            dob=dob,
            ssn=ssn,
            status="merge_review" if duplicate_review else "active",
        )
        self.store.insert_employee(employee, actor=self.access.actor)
        if not duplicate_review:
            self._seed_published_task_templates(employee)
        if self.sync is not None:
            self.sync.publish_employee(employee, base=None, changed_fields=tuple(EMPLOYEE_PROFILE_FIELDS))
        return employee

    def list_employees(self) -> list[OnboardingEmployee]:
        school = self.access.school_scope if self.access.role == "director" else ""
        return self.store.list_employees(school=school)

    @_sync_after_mutation
    def update_employee(
        self,
        employee_id: str,
        *,
        expected_version: int,
        changes: dict[str, str],
    ) -> OnboardingEmployee:
        employee = self.get_employee(employee_id)
        if employee.version != int(expected_version):
            raise ValueError("Onboarding employee changed since it was opened.")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Employee profile changes are required.")
        unknown = sorted(set(changes) - EMPLOYEE_PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported employee profile field: {unknown[0]}")
        values = {name: str(getattr(employee, name)) for name in EMPLOYEE_PROFILE_FIELDS}
        values.update({name: str(value or "") for name, value in changes.items()})
        normalized = _normalize_employee_profile(values)
        updated = replace(
            employee,
            **normalized,
            email=normalized["personal_email"],
            version=employee.version + 1,
            updated_at=_utc_now(),
        )
        self.store.replace_employee(
            updated,
            expected_version=employee.version,
            actor=self.access.actor,
            changed_fields=tuple(changes),
        )
        saved = self.get_employee(employee.id)
        if self.sync is not None:
            self.sync.publish_employee(saved, base=employee, changed_fields=tuple(changes))
        return saved

    @_sync_mutation
    def accept_offer(
        self,
        *,
        application_id: str,
        legal_name: str,
        school: str,
        role: str,
        acceptance_date: str,
        start_date: str,
        email: str,
        phone: str,
        hiring_director_id: str,
        hiring_director_name: str,
    ) -> OnboardingEmployee:
        application_key = _required_text(application_id, "Application ID")
        existing = self.store.employee_for_application(application_key)
        if existing is not None:
            self._require_school(existing.school)
            return existing
        clean_email = str(email or "").strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean_email):
            raise ValueError("A valid candidate email is required for Hire.")
        phone_digits = re.sub(r"\D", "", str(phone or ""))
        if len(phone_digits) != 10:
            raise ValueError("A 10-digit candidate phone is required for Hire.")
        employee = self._new_employee(
            legal_name=legal_name,
            preferred_name="",
            school=school,
            role=role,
            acceptance_date=acceptance_date,
            start_date=start_date,
            source_application_id=application_key,
            email=clean_email,
            phone=phone_digits,
            hiring_director_id=_required_text(hiring_director_id, "Hiring Director ID"),
            hiring_director_name=_required_text(hiring_director_name, "Hiring Director name"),
        )
        self.store.insert_employee(employee, actor=self.access.actor)
        self._seed_published_task_templates(employee)
        return employee

    @_sync_mutation
    def accept_legacy_offer(
        self,
        *,
        application_id: str,
        legal_name: str,
        school: str,
        role: str,
        acceptance_date: str,
        start_date: str,
        email: str = "",
        phone: str = "",
        hiring_director_id: str = "",
        hiring_director_name: str = "",
    ) -> OnboardingEmployee:
        self._require_admin("Legacy offer migration")
        application_key = _required_text(application_id, "Application ID")
        existing = self.store.employee_for_application(application_key)
        if existing is not None:
            return existing
        employee = self._new_employee(
            legal_name=legal_name,
            preferred_name="",
            school=school,
            role=role,
            acceptance_date=acceptance_date,
            start_date=start_date,
            source_application_id=application_key,
            email=email,
            phone=phone,
            hiring_director_id=str(hiring_director_id or "").strip(),
            hiring_director_name=str(hiring_director_name or "").strip(),
            status="active" if str(email or "").strip() and str(phone or "").strip() else "attention",
        )
        self.store.insert_employee(employee, actor=self.access.actor)
        self._seed_published_task_templates(employee)
        return employee

    @_sync_mutation
    def create_task(
        self,
        *,
        employee_id: str,
        title: str,
        owner_role: str,
        due_date: str,
        watcher_roles: list[str] | None = None,
        critical: bool = False,
        dependency_ids: list[str] | None = None,
        parent_task_id: str = "",
        required: bool = True,
        template_key: str = "",
        template_version: int = 0,
        template_id: str = "",
        notes: str = "",
        package_version_id: str = "",
    ) -> OnboardingTask:
        employee = self.store.get_employee(_required_text(employee_id, "Employee ID"))
        self._require_school(employee.school)
        dependencies = tuple(dict.fromkeys(str(value or "").strip() for value in (dependency_ids or []) if str(value or "").strip()))
        for dependency_id in dependencies:
            dependency = self.store.get_task(dependency_id)
            if dependency.employee_id != employee.id:
                raise ValueError("Task dependencies must belong to the same employee.")
        clean_parent = str(parent_task_id or "").strip()
        if clean_parent:
            parent = self.store.get_task(clean_parent)
            if parent.employee_id != employee.id:
                raise ValueError("Task parent must belong to the same employee.")
        clean_package_version_id = str(package_version_id or "").strip()
        if clean_package_version_id:
            package = self.store.get_document_package(clean_package_version_id)
            templates = self.store.applicable_task_templates(school=employee.school)
            matching_template = next(
                (
                    item for item in templates
                    if item.template_key == str(template_key or "").strip()
                    and item.version == int(template_version)
                    and (not str(template_id or "").strip() or item.id == str(template_id).strip())
                    and item.package_key == package.package_key
                ),
                None,
            )
            if package.status != "published" or package.school.casefold() != employee.school.casefold() or matching_template is None:
                raise ValueError("Document packages may be assigned only through a matching published task template.")
        now = _utc_now()
        task = OnboardingTask(
            id=str(uuid4()),
            employee_id=employee.id,
            school=employee.school,
            title=_required_text(title, "Task title"),
            owner_role=_required_text(owner_role, "Task owner role"),
            watcher_roles=tuple(dict.fromkeys(str(value or "").strip() for value in (watcher_roles or []) if str(value or "").strip())),
            due_date=_date_text(due_date, "Task due date"),
            critical=bool(critical),
            status="open",
            version=1,
            dependency_ids=dependencies,
            parent_task_id=clean_parent,
            required=bool(required),
            template_key=str(template_key or "").strip(),
            template_version=int(template_version),
            template_id=str(template_id or "").strip(),
            notes=str(notes or "").strip(),
            package_version_id=clean_package_version_id,
        )
        self.store.insert_task(task, actor=self.access.actor, created_at=now)
        if self.sync is not None:
            self.sync.publish_task(
                task,
                base=None,
                changed_fields=tuple(
                    name for name in task.__dataclass_fields__
                    if name not in {"id", "employee_id", "school", "version"}
                ),
            )
        self._emit_task_notification("onboarding.task.created", task)
        return task

    @_sync_mutation
    def create_task_template_draft(
        self, *, template_key: str, school: str, title: str, owner_role: str,
        due_offset_days: int, watcher_roles: list[str] | None = None, critical: bool = False,
        package_key: str = "",
        content: str = "",
        base_template_id: str = "",
        override_fields: list[str] | None = None,
    ) -> TaskTemplateVersion:
        self._require_admin("Task templates")
        key = _required_text(template_key, "Template key")
        scope = _required_text(school, "Template school")
        base_id = str(base_template_id or "").strip()
        allowed_overrides = {"title", "owner_role", "watcher_roles", "due_offset_days", "critical", "package_key", "content"}
        overrides = tuple(dict.fromkeys(str(value) for value in (override_fields or [])))
        if set(overrides) - allowed_overrides:
            raise ValueError("Task template override field is invalid.")
        if base_id:
            base = self.store.get_task_template(base_id)
            if base.school != "*" or base.template_key != key or base.status != "published":
                raise ValueError("School override base must be the matching published global template.")
        template = TaskTemplateVersion(
            id=str(uuid4()), template_key=key, school=scope,
            title=_required_text(title, "Template title"),
            owner_role=_required_text(owner_role, "Template owner role"),
            watcher_roles=tuple(dict.fromkeys(watcher_roles or [])),
            due_offset_days=int(due_offset_days), critical=bool(critical),
            version=self.store.next_task_template_version(key, school=scope),
            status="draft", created_at=_utc_now(),
            package_key=str(package_key or "").strip(),
            content=str(content or "").strip(), base_template_id=base_id,
            override_fields=overrides,
        )
        self.store.insert_task_template(template)
        if self.sync is not None:
            self.sync.publish_task_template(template)
        return template

    def list_task_template_versions(self) -> list[TaskTemplateVersion]:
        self._require_admin("Task templates")
        return self.store.list_task_template_versions()

    @_sync_mutation
    def add_task_template_attachment(
        self, template_id: str, path: Path
    ) -> TaskTemplateAttachment:
        self._require_admin("Task template attachments")
        template = self.store.get_task_template(_required_text(template_id, "Template ID"))
        if template.status != "draft":
            raise ValueError("Task template attachments may only change on drafts.")
        validated = validate_task_attachment(path)
        scan_status = str(self.attachment_scanner(validated.path) or "").strip().casefold()
        if scan_status == "flagged":
            raise ValueError("Windows Defender flagged the task template attachment.")
        if scan_status not in {"clean", "unavailable"}:
            raise ValueError("Task template attachment scanner returned an invalid state.")
        attachment = TaskTemplateAttachment(
            id=str(uuid4()), template_id=template.id, school=template.school,
            name=validated.name, media_type=validated.media_type,
            sha256=hashlib.sha256(validated.content).hexdigest(),
            size_bytes=len(validated.content), scan_status=scan_status,
            warning="Windows Defender scan unavailable." if scan_status == "unavailable" else "",
            created_at=_utc_now(),
        )
        self.store.insert_task_template_attachment(attachment, content=validated.content)
        if self.sync is not None:
            self.sync.publish_task_template(template)
        return attachment

    def list_task_template_attachments(self, template_id: str) -> list[TaskTemplateAttachment]:
        self._require_admin("Task template attachments")
        template = self.store.get_task_template(_required_text(template_id, "Template ID"))
        return self.store.list_task_template_attachments(template.id)

    @_sync_mutation
    def publish_task_template(self, template_id: str) -> TaskTemplateVersion:
        self._require_admin("Task templates")
        template = self.store.publish_task_template(_required_text(template_id, "Template ID"), published_at=_utc_now())
        if self.sync is not None:
            self.sync.publish_task_template(template)
        return template

    @_sync_mutation
    def deprecate_task_template(self, template_id: str) -> TaskTemplateVersion:
        self._require_admin("Task templates")
        template = self.store.deprecate_task_template(_required_text(template_id, "Template ID"))
        if self.sync is not None:
            self.sync.publish_task_template(template)
        return template

    def preview_task_template_upgrade(
        self,
        template_id: str,
        *,
        employee_ids: list[str],
    ) -> tuple[TaskTemplateUpgradePreview, ...]:
        self._require_admin("Task template upgrades")
        target = self.store.get_task_template(_required_text(template_id, "Template ID"))
        if target.status != "published":
            raise ValueError("Task template upgrade target must be published.")
        selected_ids = tuple(dict.fromkeys(_required_text(value, "Employee ID") for value in employee_ids))
        if not selected_ids:
            raise ValueError("Task template upgrade requires at least one employee.")
        previews: list[TaskTemplateUpgradePreview] = []
        tasks = self.store.list_tasks()
        for employee_id in selected_ids:
            employee = self.get_employee(employee_id)
            if employee.status not in {"active", "attention"}:
                continue
            if target.school != "*" and target.school.casefold() != employee.school.casefold():
                raise ValueError("Task template school does not match selected employee.")
            desired = self._task_template_values(target, employee)
            for task in tasks:
                if task.employee_id != employee.id or task.template_key != target.template_key or task.template_id == target.id:
                    continue
                changed_fields = tuple(
                    sorted(
                        name for name, value in desired.items()
                        if getattr(task, name) != value
                    )
                )
                if not changed_fields:
                    continue
                previews.append(
                    TaskTemplateUpgradePreview(
                        employee_id=employee.id,
                        task_id=task.id,
                        template_id=target.id,
                        template_key=target.template_key,
                        from_version=task.template_version,
                        to_version=target.version,
                        changed_fields=changed_fields,
                    )
                )
        return tuple(sorted(previews, key=lambda item: (item.employee_id, item.task_id)))

    @_sync_mutation
    def apply_task_template_upgrade(
        self,
        template_id: str,
        *,
        employee_ids: list[str],
    ) -> list[OnboardingTask]:
        previews = self.preview_task_template_upgrade(template_id, employee_ids=employee_ids)
        target = self.store.get_task_template(template_id)
        applied: list[OnboardingTask] = []
        for preview in previews:
            task = self.get_task(preview.task_id)
            employee = self.get_employee(preview.employee_id)
            desired = self._task_template_values(target, employee)
            updated = replace(task, **desired, version=task.version + 1)
            self.store.replace_task(
                updated,
                expected_version=task.version,
                actor=self.access.actor,
                updated_at=_utc_now(),
                changed_fields=preview.changed_fields,
            )
            if self.sync is not None:
                self.sync.publish_task(updated, base=task, changed_fields=preview.changed_fields)
            applied.append(updated)
        return applied

    def _task_template_values(
        self,
        template: TaskTemplateVersion,
        employee: OnboardingEmployee,
    ) -> dict[str, object]:
        package = (
            self.store.latest_published_document_package(template.package_key, school=employee.school)
            if template.package_key else None
        )
        if template.package_key and package is None:
            raise ValueError(f"Published task template package is unavailable: {template.package_key}")
        return {
            "title": template.title,
            "owner_role": template.owner_role,
            "watcher_roles": template.watcher_roles,
            "due_date": (
                date.fromisoformat(employee.start_date) + timedelta(days=template.due_offset_days)
            ).isoformat(),
            "critical": template.critical,
            "template_id": template.id,
            "template_version": template.version,
            "package_version_id": "" if package is None else package.id,
            "notes": template.content,
        }

    def _seed_published_task_templates(self, employee: OnboardingEmployee) -> None:
        start = date.fromisoformat(employee.start_date)
        for template in self.store.applicable_task_templates(school=employee.school):
            package_version = (
                self.store.latest_published_document_package(template.package_key, school=employee.school)
                if template.package_key else None
            )
            if template.package_key and package_version is None:
                raise ValueError(f"Published task template package is unavailable: {template.package_key}")
            task = self.create_task(
                employee_id=employee.id, title=template.title, owner_role=template.owner_role,
                due_date=(start + timedelta(days=template.due_offset_days)).isoformat(),
                watcher_roles=list(template.watcher_roles), critical=template.critical,
                template_key=template.template_key, template_version=template.version,
                template_id=template.id,
                notes=template.content,
                package_version_id="" if package_version is None else package_version.id,
            )
            self._copy_task_template_attachments(template.id, task)

    def _copy_task_template_attachments(
        self, template_id: str, task: OnboardingTask
    ) -> None:
        for source in self.store.list_task_template_attachments(template_id):
            stored, content = self.store.get_task_template_attachment(source.id)
            if hashlib.sha256(content).hexdigest() != stored.sha256:
                raise ValueError("Task template attachment hash validation failed.")
            attachment = TaskAttachment(
                id=str(uuid4()), task_id=task.id, employee_id=task.employee_id,
                school=task.school, name=stored.name, media_type=stored.media_type,
                sha256=stored.sha256, size_bytes=stored.size_bytes,
                scan_status=stored.scan_status, warning=stored.warning, created_at=_utc_now(),
            )
            self.store.insert_task_attachment(attachment, content=content)
            if self.sync is not None:
                self.sync.publish_task_attachment(attachment)

    def get_task(self, task_id: str) -> OnboardingTask:
        task = self.store.get_task(_required_text(task_id, "Task ID"))
        self._require_school(task.school)
        return task

    @_sync_after_mutation
    def update_task(
        self,
        task_id: str,
        *,
        expected_version: int,
        changes: dict[str, object],
    ) -> OnboardingTask:
        task = self.get_task(task_id)
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Task changes are required.")
        unknown = sorted(set(changes) - TASK_EDIT_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported task field: {unknown[0]}")
        values: dict[str, object] = {}
        if "title" in changes:
            values["title"] = _required_text(changes["title"], "Task title")
        if "owner_role" in changes:
            values["owner_role"] = _required_text(changes["owner_role"], "Task owner role")
        if "due_date" in changes:
            values["due_date"] = _date_text(changes["due_date"], "Task due date")
        for field_name in ("critical", "required"):
            if field_name not in changes:
                continue
            if not isinstance(changes[field_name], bool):
                raise ValueError(f"Task {field_name.replace('_', ' ')} must be true or false.")
            values[field_name] = changes[field_name]
        if "notes" in changes:
            values["notes"] = str(changes["notes"] or "").strip()
        if "watcher_roles" in changes:
            raw_watchers = changes["watcher_roles"]
            if not isinstance(raw_watchers, (list, tuple)):
                raise ValueError("Task watcher roles must be a list.")
            values["watcher_roles"] = tuple(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in raw_watchers
                    if str(value or "").strip()
                )
            )
        if "dependency_ids" in changes:
            raw_dependencies = changes["dependency_ids"]
            if not isinstance(raw_dependencies, (list, tuple)):
                raise ValueError("Task dependencies must be a list.")
            dependencies = tuple(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in raw_dependencies
                    if str(value or "").strip()
                )
            )
            for dependency_id in dependencies:
                if dependency_id == task.id:
                    raise ValueError("Task cannot depend on itself.")
                dependency = self.get_task(dependency_id)
                if dependency.employee_id != task.employee_id:
                    raise ValueError("Task dependencies must belong to the same employee.")
                if self._task_reaches(dependency.id, task.id):
                    raise ValueError("Task dependency would create a cycle.")
            values["dependency_ids"] = dependencies
        if "parent_task_id" in changes:
            parent_id = str(changes["parent_task_id"] or "").strip()
            if parent_id:
                if parent_id == task.id:
                    raise ValueError("Task cannot be its own parent.")
                parent = self.get_task(parent_id)
                if parent.employee_id != task.employee_id:
                    raise ValueError("Task parent must belong to the same employee.")
            values["parent_task_id"] = parent_id
        updated = replace(task, **values, version=task.version + 1)
        self.store.replace_task(
            updated,
            expected_version=int(expected_version),
            actor=self.access.actor,
            updated_at=_utc_now(),
            changed_fields=tuple(sorted(values)),
        )
        if self.sync is not None:
            self.sync.publish_task(updated, base=task, changed_fields=tuple(sorted(values)))
        return updated

    def _task_reaches(self, start_task_id: str, target_task_id: str) -> bool:
        pending = [start_task_id]
        seen: set[str] = set()
        while pending:
            current_id = pending.pop()
            if current_id == target_task_id:
                return True
            if current_id in seen:
                continue
            seen.add(current_id)
            pending.extend(self.get_task(current_id).dependency_ids)
        return False

    def list_tasks(self) -> list[OnboardingTask]:
        school = self.access.school_scope if self.access.role == "director" else ""
        return self.store.list_tasks(school=school)

    def query_tasks(
        self,
        *,
        search: str = "",
        school: str = "",
        owner_role: str = "",
        watcher_role: str = "",
        employee_id: str = "",
        statuses: tuple[str, ...] = (),
        urgency: str = "",
        blocked: bool | None = None,
        due_from: str = "",
        due_to: str = "",
        as_of: str = "",
    ) -> list[OnboardingTask]:
        school_filter = str(school or "").strip()
        if school_filter:
            self._require_school(school_filter)
        employee_filter = str(employee_id or "").strip()
        if employee_filter:
            self.get_employee(employee_filter)
        status_filter = {str(value or "").strip().casefold() for value in statuses if str(value or "").strip()}
        allowed_statuses = {"open", "blocked", "completed", "cancelled"}
        if not status_filter.issubset(allowed_statuses):
            raise ValueError("Task status filter is invalid.")
        urgency_filter = str(urgency or "").strip().casefold()
        if urgency_filter not in {"", "critical", "overdue", "due_today", "upcoming"}:
            raise ValueError("Task urgency filter is invalid.")
        start_date = _date_text(due_from, "Task due-from date") if due_from else ""
        end_date = _date_text(due_to, "Task due-to date") if due_to else ""
        if start_date and end_date and start_date > end_date:
            raise ValueError("Task due-from date cannot be after due-to date.")
        today = _date_text(as_of, "Task query as-of date") if as_of else ""
        if urgency_filter in {"overdue", "due_today", "upcoming"} and not today:
            raise ValueError("Task urgency filter requires an as-of date.")
        owner_filter = str(owner_role or "").strip().casefold()
        watcher_filter = str(watcher_role or "").strip().casefold()
        search_tokens = tuple(str(search or "").casefold().split())
        employees = {employee.id: employee for employee in self.list_employees()}
        tasks = self.list_tasks()
        statuses_by_id = {task.id: task.status for task in tasks}
        result: list[OnboardingTask] = []
        for task in tasks:
            employee = employees.get(task.employee_id)
            if employee is None:
                continue
            is_blocked = task.status == "blocked" or any(
                statuses_by_id.get(dependency_id) != "completed"
                for dependency_id in task.dependency_ids
            )
            haystack = " ".join(
                (
                    employee.legal_name,
                    employee.preferred_name,
                    task.school,
                    task.title,
                    task.owner_role,
                    *task.watcher_roles,
                )
            ).casefold()
            if search_tokens and not all(token in haystack for token in search_tokens):
                continue
            if school_filter and task.school.casefold() != school_filter.casefold():
                continue
            if owner_filter and task.owner_role.casefold() != owner_filter:
                continue
            if watcher_filter and watcher_filter not in {role.casefold() for role in task.watcher_roles}:
                continue
            if employee_filter and task.employee_id != employee_filter:
                continue
            if status_filter and task.status.casefold() not in status_filter:
                continue
            if blocked is not None and is_blocked is not blocked:
                continue
            if start_date and task.due_date < start_date:
                continue
            if end_date and task.due_date > end_date:
                continue
            if urgency_filter == "critical" and not task.critical:
                continue
            if urgency_filter == "overdue" and not (task.due_date < today and task.status not in {"completed", "cancelled"}):
                continue
            if urgency_filter == "due_today" and task.due_date != today:
                continue
            if urgency_filter == "upcoming" and not (task.due_date > today and task.status not in {"completed", "cancelled"}):
                continue
            result.append(task)
        return result

    def task_metrics(self, *, as_of: str) -> TaskMetrics:
        today = _date_text(as_of, "Task metrics as-of date")
        tasks = self.list_tasks()
        statuses = {task.id: task.status for task in tasks}
        open_tasks = [task for task in tasks if task.status not in {"completed", "cancelled"}]
        blocked_ids = {
            task.id
            for task in open_tasks
            if task.status == "blocked"
            or any(statuses.get(dependency_id) != "completed" for dependency_id in task.dependency_ids)
        }
        overdue_ids = {task.id for task in open_tasks if task.due_date < today}
        return TaskMetrics(
            open=len(open_tasks),
            blocked=len(blocked_ids),
            blocked_overdue=len(blocked_ids & overdue_ids),
            overdue=len(overdue_ids),
            actionable_overdue=len(overdue_ids - blocked_ids),
            completed=sum(task.status == "completed" for task in tasks),
        )

    def get_employee(self, employee_id: str) -> OnboardingEmployee:
        employee = self.store.get_employee(_required_text(employee_id, "Employee ID"))
        self._require_school(employee.school)
        return employee

    @_sync_mutation
    def complete_task(self, task_id: str) -> OnboardingTask:
        task = self.get_task(task_id)
        incomplete = [dependency_id for dependency_id in task.dependency_ids if self.store.get_task(dependency_id).status != "completed"]
        if incomplete:
            if task.status != "blocked":
                self.store.set_task_status(task.id, status="blocked", actor=self.access.actor, updated_at=_utc_now())
            raise ValueError("Task is blocked by incomplete dependencies.")
        incomplete_subtasks = [
            child for child in self.list_tasks()
            if child.parent_task_id == task.id and child.required and child.status != "completed"
        ]
        if incomplete_subtasks:
            raise ValueError("Task is blocked by incomplete required subtasks.")
        completed = self.store.set_task_status(task.id, status="completed", actor=self.access.actor, updated_at=_utc_now())
        if self.sync is not None:
            self.sync.publish_task(completed, base=task, changed_fields=("status",))
        self._emit_task_notification("onboarding.task.completed", completed)
        return completed

    def _emit_task_notification(self, event_type: str, task: OnboardingTask) -> None:
        if self.notification_dispatcher is None:
            self.last_notification_outcome = "not_configured"
            return
        payload = {
            "school": task.school,
            "task_title": task.title,
            "owner_role": task.owner_role,
            "due_date": task.due_date,
        }
        suffix = event_type.rsplit(".", 1)[-1]
        key = f"onboarding:task:{task.id}:{suffix}:v{task.version}"
        try:
            outcome = self.notification_dispatcher(event_type, payload, key)
        except Exception:
            self.last_notification_outcome = "failed"
            return
        self.last_notification_outcome = "suppressed" if not outcome else "dispatched"

    @_sync_mutation
    def add_task_comment(self, task_id: str, *, body: str) -> TaskComment:
        task = self.get_task(task_id)
        now = _utc_now()
        comment = TaskComment(
            id=str(uuid4()),
            task_id=task.id,
            employee_id=task.employee_id,
            school=task.school,
            author=self.access.actor,
            body=_required_text(body, "Comment"),
            version=1,
            redacted=False,
            created_at=now,
            updated_at=now,
        )
        self.store.insert_task_comment(comment)
        if self.sync is not None:
            self.sync.publish_task_comment(comment)
        return comment

    def list_task_comments(self, task_id: str) -> list[TaskComment]:
        task = self.get_task(_required_text(task_id, "Task ID"))
        return self.store.list_task_comments(task.id)

    @_sync_mutation
    def edit_task_comment(self, comment_id: str, *, body: str) -> TaskComment:
        comment = self.store.get_task_comment(_required_text(comment_id, "Comment ID"))
        self._require_school(comment.school)
        if comment.author != self.access.actor:
            raise OnboardingPermissionError("Only the comment author may edit it.")
        if comment.redacted:
            raise ValueError("Redacted comments cannot be edited.")
        revised = self.store.revise_task_comment(
            comment.id,
            body=_required_text(body, "Comment"),
            editor=self.access.actor,
            reason="author_edit",
            redacted=False,
            updated_at=_utc_now(),
        )
        if self.sync is not None:
            self.sync.publish_task_comment(revised)
        return revised

    @_sync_mutation
    def redact_task_comment(self, comment_id: str, *, reason: str) -> TaskComment:
        self._require_admin("Comment redaction")
        comment = self.store.get_task_comment(_required_text(comment_id, "Comment ID"))
        clean_reason = _required_text(reason, "Redaction reason")
        revised = self.store.revise_task_comment(
            comment.id,
            body="[Redacted by Admin]",
            editor=self.access.actor,
            reason=clean_reason,
            redacted=True,
            updated_at=_utc_now(),
        )
        if self.sync is not None:
            self.sync.publish_task_comment(revised)
        return revised

    def list_task_comment_revisions(self, comment_id: str) -> list[TaskCommentRevision]:
        comment = self.store.get_task_comment(_required_text(comment_id, "Comment ID"))
        self._require_school(comment.school)
        return self.store.list_task_comment_revisions(comment.id)

    @_sync_mutation
    def add_task_attachment(self, task_id: str, path: Path) -> TaskAttachment:
        task = self.get_task(task_id)
        validated = validate_task_attachment(path)
        scan_status = str(self.attachment_scanner(validated.path) or "").strip().casefold()
        if scan_status == "flagged":
            raise ValueError("Windows Defender flagged the task attachment.")
        if scan_status not in {"clean", "unavailable"}:
            raise ValueError("Task attachment scanner returned an invalid state.")
        warning = "Windows Defender scan unavailable." if scan_status == "unavailable" else ""
        attachment = TaskAttachment(
            id=str(uuid4()),
            task_id=task.id,
            employee_id=task.employee_id,
            school=task.school,
            name=validated.name,
            media_type=validated.media_type,
            sha256=hashlib.sha256(validated.content).hexdigest(),
            size_bytes=len(validated.content),
            scan_status=scan_status,
            warning=warning,
            created_at=_utc_now(),
        )
        self.store.insert_task_attachment(attachment, content=validated.content)
        if self.sync is not None:
            self.sync.publish_task_attachment(attachment)
        return attachment

    def read_task_attachment(self, attachment_id: str) -> bytes:
        self.sync_pending()
        attachment, content = self.store.get_task_attachment(_required_text(attachment_id, "Attachment ID"))
        self._require_school(attachment.school)
        if hashlib.sha256(content).hexdigest() != attachment.sha256:
            raise ValueError("Task attachment hash validation failed.")
        return content

    def list_task_attachments(self, task_id: str) -> list[TaskAttachment]:
        task = self.get_task(_required_text(task_id, "Task ID"))
        return self.store.list_task_attachments(task.id)

    def list_task_audit_events(self, task_id: str) -> list[dict[str, object]]:
        task = self.get_task(_required_text(task_id, "Task ID"))
        return self.store.list_audit_events(entity_id=task.id)

    def list_employee_audit_events(self, employee_id: str) -> list[dict[str, object]]:
        employee = self.get_employee(_required_text(employee_id, "Employee ID"))
        return self.store.list_audit_events(entity_id=employee.id)

    def list_employee_filled_artifacts(self, employee_id: str) -> list[FilledArtifact]:
        employee = self.get_employee(_required_text(employee_id, "Employee ID"))
        return self.store.list_employee_filled_artifacts(employee.id)

    def open_task_attachment(self, attachment_id: str) -> Path:
        self.sync_pending()
        if self.artifact_vault is None:
            raise ValueError("Encrypted onboarding artifact vault is unavailable.")
        attachment, content = self.store.get_task_attachment(
            _required_text(attachment_id, "Attachment ID")
        )
        self._require_school(attachment.school)
        if hashlib.sha256(content).hexdigest() != attachment.sha256:
            raise ValueError("Task attachment hash validation failed.")
        suffix = Path(attachment.name).suffix.casefold()
        target = self.artifact_vault.temp_root / f"task-{attachment.id}-{uuid4().hex}{suffix}"
        OnboardingVault._write_bytes_atomic(target, content)
        self.store.append_audit_event(
            entity_id=attachment.employee_id,
            action="task_attachment.opened",
            actor=self.access.actor,
            school=attachment.school,
            version=self.get_task(attachment.task_id).version,
            details={"attachment_id": attachment.id, "media_type": attachment.media_type},
            created_at=_utc_now(),
        )
        return target

    def close_task_attachment(self, path: Path) -> None:
        if self.artifact_vault is None:
            raise ValueError("Encrypted onboarding artifact vault is unavailable.")
        self.artifact_vault.cleanup_temp(path)

    def preview_reminders(
        self,
        *,
        recipient_resolver: Callable[[str, str], str],
        admin_fallback_email: str,
        now: datetime,
        config_revision: str = "",
    ) -> ReminderPreview:
        self.sync_pending()
        return self.reminders.preview(
            recipient_resolver=recipient_resolver,
            admin_fallback_email=admin_fallback_email,
            now=now,
            school_scope=self.access.school_scope if self.access.role == "director" else "",
            config_revision=config_revision,
        )

    def send_reminder_preview(
        self,
        token: str,
        *,
        sender: Callable[[ReminderPreviewMessage], None],
        now: datetime,
        confirmed: bool,
        admin_override_reason: str = "",
        config_revision: str = "",
    ) -> ReminderSendResult:
        self.sync_pending()
        return self.reminders.send(
            token,
            sender=sender,
            now=now,
            confirmed=confirmed,
            admin_override_reason=admin_override_reason,
            config_revision=config_revision,
        )

    def retry_failed_reminders(
        self,
        run_id: str,
        *,
        sender: Callable[[ReminderPreviewMessage], None],
        now: datetime,
        confirmed: bool,
    ) -> ReminderSendResult:
        self.sync_pending()
        return self.reminders.retry_failed(
            run_id,
            sender=sender,
            now=now,
            confirmed=confirmed,
        )

    def list_reminder_run_history(self, *, limit: int = 100) -> list[dict[str, object]]:
        school = self.access.school_scope if self.access.role == "director" else ""
        return self.store.list_reminder_runs(school=school, limit=limit)

    def scheduler_run_recorded(self, *, local_day: str) -> bool:
        self._require_admin("Automatic reminder scheduler")
        return self.store.has_scheduler_run(local_day=_date_text(local_day, "Scheduler day"))

    def record_scheduler_run(
        self,
        *,
        local_day: str,
        state: str,
        sent_count: int,
        failed_count: int,
        skipped_count: int,
        created_at: str,
    ) -> None:
        self._require_admin("Automatic reminder scheduler")
        self.store.record_scheduler_run(
            local_day=_date_text(local_day, "Scheduler day"),
            state=_required_text(state, "Scheduler state"),
            sent_count=max(0, int(sent_count)),
            failed_count=max(0, int(failed_count)),
            skipped_count=max(0, int(skipped_count)),
            created_at=_required_text(created_at, "Scheduler timestamp"),
        )

    def scheduler_health(self) -> dict[str, object]:
        return self.store.scheduler_health()

    def list_owner_roles(self, *, school: str = "") -> list[OwnerRoleConfig]:
        scope = str(school or "").strip()
        if self.access.role == "director":
            scope = scope or self.access.school_scope
            self._require_school(scope)
        else:
            scope = _required_text(scope, "Owner-role school")
        return self.store.list_owner_roles(school=scope)

    @_sync_mutation
    def configure_owner_role(
        self,
        *,
        school: str,
        role: str,
        email: str = "",
        active: bool = True,
    ) -> OwnerRoleConfig:
        self._require_admin("Owner-role configuration")
        scope = _required_text(school, "Owner-role school")
        clean_role = _required_text(role, "Owner role")
        clean_email = str(email or "").strip()
        if clean_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email):
            raise ValueError("Owner-role email must be valid.")
        existing = next(
            (
                item for item in self.store.list_owner_roles(school=scope)
                if item.school.casefold() == scope.casefold() and item.role.casefold() == clean_role.casefold()
            ),
            None,
        )
        config = OwnerRoleConfig(
            school=scope,
            role=clean_role,
            email=clean_email,
            active=bool(active),
            version=1 if existing is None else existing.version + 1,
        )
        saved = self.store.upsert_owner_role(config)
        if self.sync is not None:
            self.sync.publish_owner_role(saved)
        return saved

    def resolve_owner_recipient(
        self,
        *,
        role: str,
        admin_fallback_email: str,
        school: str = "",
    ) -> tuple[str, str]:
        scope = str(school or "").strip()
        if self.access.role == "director":
            scope = scope or self.access.school_scope
        scope = _required_text(scope, "Owner-role school")
        self._require_school(scope)
        fallback = str(admin_fallback_email or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fallback):
            raise ValueError("Admin fallback email must be valid.")
        clean_role = _required_text(role, "Owner role")
        config = next(
            (item for item in self.store.list_owner_roles(school=scope) if item.role.casefold() == clean_role.casefold()),
            None,
        )
        if config is not None and config.email:
            return config.email, ""
        return fallback, f"{scope} {clean_role} has no email; Admin fallback will be used."

    @_sync_mutation
    def mark_employment_ended(
        self,
        employee_id: str,
        *,
        last_working_day: str,
        departure_category: str,
        departure_director_id: str = "",
        departure_director_name: str = "",
        notes: str = "",
    ) -> OnboardingEmployee:
        employee = self.store.get_employee(_required_text(employee_id, "Employee ID"))
        self._require_school(employee.school)
        category = str(departure_category or "").strip().casefold().replace(" ", "_")
        if category not in DEPARTURE_CATEGORIES:
            raise ValueError("A standard departure category is required.")
        final_day = _date_text(last_working_day, "Last working day")
        if final_day < employee.start_date:
            raise ValueError("Last working day cannot be before start date.")
        if employee.role.casefold() == "director" and self.access.role != "admin":
            raise OnboardingPermissionError("Director departures require admin attribution.")
        if employee.role.casefold() != "director" and not departure_director_id and not departure_director_name:
            current = self.current_director(employee.id)
            departure_director_id = current.person_id
            departure_director_name = current.name
        before_tasks = [task for task in self.store.list_tasks(school=employee.school) if task.employee_id == employee.id]
        ended = self.store.end_employment(
            employee.id,
            last_working_day=final_day,
            departure_category=category,
            departure_notes=str(notes or "").strip(),
            departure_director_id=_required_text(departure_director_id, "Departure Director ID"),
            departure_director_name=_required_text(departure_director_name, "Departure Director name"),
            actor=self.access.actor,
            archived_at=_utc_now(),
        )
        self._publish_lifecycle_changes(employee, ended, before_tasks)
        return ended

    def current_director(self, employee_id: str) -> DirectorIdentity:
        employee = self.get_employee(employee_id)
        if self.director_resolver is None:
            raise ValueError("Current Director resolver is unavailable.")
        identity = self.director_resolver(employee.school)
        if identity.school.casefold() != employee.school.casefold():
            raise ValueError("Current Director resolver returned the wrong school.")
        return identity

    @_sync_mutation
    def transfer_employee(self, employee_id: str, *, new_school: str) -> OnboardingEmployee:
        self._require_admin("Employee transfers")
        before = self.get_employee(_required_text(employee_id, "Employee ID"))
        transferred = self.store.transfer_employee(
            before.id,
            new_school=_required_text(new_school, "New school"),
            actor=self.access.actor,
            updated_at=_utc_now(),
        )
        if self.sync is not None:
            tasks = tuple(task for task in self.store.list_tasks(school=transferred.school) if task.employee_id == transferred.id)
            self.sync.publish_employee_transfer(before, transferred, tasks=tasks)
        return transferred

    @_sync_mutation
    def mark_did_not_start(
        self,
        employee_id: str,
        *,
        reason: str,
        notes: str = "",
    ) -> OnboardingEmployee:
        employee = self.store.get_employee(_required_text(employee_id, "Employee ID"))
        self._require_school(employee.school)
        clean_reason = str(reason or "").strip().casefold().replace(" ", "_")
        if clean_reason not in DID_NOT_START_REASONS:
            raise ValueError("A standard Did Not Start reason is required.")
        before_tasks = [task for task in self.store.list_tasks(school=employee.school) if task.employee_id == employee.id]
        archived = self.store.mark_did_not_start(
            employee.id,
            reason=clean_reason,
            notes=str(notes or "").strip(),
            actor=self.access.actor,
            archived_at=_utc_now(),
        )
        self._publish_lifecycle_changes(employee, archived, before_tasks)
        return archived

    @_sync_mutation
    def archive_correction(self, employee_id: str, *, reason: str) -> OnboardingEmployee:
        self._require_admin("Employee correction archive")
        employee = self.get_employee(employee_id)
        if employee.status == "archived":
            return employee
        clean_reason = str(reason or "").strip().casefold()
        if clean_reason not in {"duplicate", "test_record", "cancelled_before_start"}:
            raise ValueError("Correction reason must be duplicate, test_record, or cancelled_before_start.")
        before_tasks = [task for task in self.store.list_tasks(school=employee.school) if task.employee_id == employee.id]
        archived = self.store.archive_correction(
            employee.id,
            reason=clean_reason,
            actor=self.access.actor,
            archived_at=_utc_now(),
        )
        self._publish_lifecycle_changes(employee, archived, before_tasks)
        return archived

    def _publish_lifecycle_changes(
        self,
        before_employee: OnboardingEmployee,
        after_employee: OnboardingEmployee,
        before_tasks: list[OnboardingTask],
    ) -> None:
        if self.sync is None:
            return
        employee_fields = tuple(
            name for name in before_employee.__dataclass_fields__
            if name not in {"id", "school", "version", "created_at", "updated_at"}
            and getattr(before_employee, name) != getattr(after_employee, name)
        )
        self.sync.publish_employee(after_employee, base=before_employee, changed_fields=employee_fields)
        for before_task in before_tasks:
            after_task = self.store.get_task(before_task.id)
            task_fields = tuple(
                name for name in before_task.__dataclass_fields__
                if name not in {"id", "employee_id", "school", "version"}
                and getattr(before_task, name) != getattr(after_task, name)
            )
            if task_fields:
                self.sync.publish_task(after_task, base=before_task, changed_fields=task_fields)

    @_sync_mutation
    def permanently_delete_employee(self, employee_id: str, *, confirmation: str) -> None:
        self._require_admin("Permanent employee deletion")
        employee = self.get_employee(employee_id)
        if employee.status != "archived":
            raise ValueError("Active employment must be archived first.")
        if confirmation != f"DELETE {employee.id}":
            raise ValueError("Permanent deletion confirmation does not match.")
        correction_reason = employee.departure_category.removeprefix("correction:")
        if not correction_reason:
            raise ValueError("Permanent deletion is limited to correction records.")
        tombstone_payload = {"correction_reason": correction_reason}
        self.store.permanently_remove_employee(
            employee.id,
            actor=self.access.actor,
            deleted_at=_utc_now(),
            tombstone_payload=tombstone_payload,
            action="employee.permanently_deleted",
        )
        if self.sync is not None:
            self.sync.publish_employee_tombstone(
                employee,
                action="employee.permanently_deleted",
                tombstone_payload=tombstone_payload,
            )

    def preview_retention_purge(
        self,
        *,
        as_of: str,
        retention_years: int = 7,
    ) -> list[RetentionPurgeCandidate]:
        self._require_admin("Retention purge")
        today = date.fromisoformat(_date_text(as_of, "Purge as-of date"))
        years = int(retention_years)
        if years < 1:
            raise ValueError("Retention years must be at least one.")
        candidates: list[RetentionPurgeCandidate] = []
        for employee in self.store.list_employees():
            if employee.status != "archived" or not employee.last_working_day:
                continue
            final_day = date.fromisoformat(employee.last_working_day)
            eligible_on = _add_years(final_day, years)
            if eligible_on > today:
                continue
            candidates.append(
                RetentionPurgeCandidate(
                    employee_id=employee.id,
                    school=employee.school,
                    last_working_day=employee.last_working_day,
                    departure_category=employee.departure_category,
                    eligible_on=eligible_on.isoformat(),
                )
            )
        return sorted(candidates, key=lambda item: (item.eligible_on, item.employee_id))

    @_sync_mutation
    def purge_retained_employee(
        self,
        employee_id: str,
        *,
        as_of: str,
        confirmation: str,
        retention_years: int = 7,
    ) -> None:
        self._require_admin("Retention purge")
        employee = self.get_employee(employee_id)
        eligible = {item.employee_id for item in self.preview_retention_purge(as_of=as_of, retention_years=retention_years)}
        if employee.id not in eligible:
            raise ValueError("Employee is not eligible for retention purge.")
        if confirmation != f"PURGE {employee.id}":
            raise ValueError("Retention purge confirmation does not match.")
        tombstone_payload = {
            "purged": True,
            "school": employee.school,
            "acceptance_date": employee.acceptance_date,
            "start_date": employee.start_date,
            "last_working_day": employee.last_working_day,
            "departure_category": employee.departure_category,
            "departure_director_id": employee.departure_director_id,
            "departure_director_name": employee.departure_director_name,
        }
        self.store.permanently_remove_employee(
            employee.id,
            actor=self.access.actor,
            deleted_at=_utc_now(),
            tombstone_payload=tombstone_payload,
            action="employee.retention_purged",
        )
        if self.sync is not None:
            self.sync.publish_employee_tombstone(
                employee,
                action="employee.retention_purged",
                tombstone_payload=tombstone_payload,
            )

    def masked_ssn(self, employee_id: str) -> str:
        ssn = self.get_employee(employee_id).ssn
        return "" if not ssn else f"***-**-{ssn[-4:]}"

    def reveal_ssn(self, employee_id: str, *, reason: str) -> str:
        employee = self.get_employee(employee_id)
        clean_reason = _required_text(reason, "SSN reveal reason")
        if not employee.ssn:
            return ""
        self.store.append_audit_event(
            entity_id=employee.id,
            action="employee.ssn_revealed",
            actor=self.access.actor,
            school=employee.school,
            version=employee.version,
            details={"reason_category": "authorized_business_use", "reason_length": len(clean_reason)},
            created_at=_utc_now(),
        )
        return employee.ssn

    @_sync_mutation
    def create_intake_field(
        self,
        *,
        stable_id: str,
        label: str,
        field_type: str,
        sensitivity: str,
        aliases: list[str],
        validation: dict[str, object] | None = None,
        help_text: str = "",
        options: list[str] | None = None,
    ) -> IntakeField:
        self._require_admin("Intake fields")
        field_kind = str(field_type or "").strip().casefold()
        if field_kind not in INTAKE_FIELD_TYPES:
            raise ValueError("Unsupported intake field type.")
        stable_key = _required_text(stable_id, "Stable field ID")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", stable_key):
            raise ValueError("Stable field ID must use lowercase letters, numbers, dots, underscores, or hyphens.")
        field = IntakeField(
            id=str(uuid4()),
            stable_id=stable_key,
            label=_required_text(label, "Field label"),
            aliases=tuple(dict.fromkeys(str(value or "").strip() for value in aliases if str(value or "").strip())),
            field_type=field_kind,
            sensitivity=_required_text(sensitivity, "Field sensitivity").casefold(),
            validation_json=json.dumps(validation or {}, sort_keys=True, separators=(",", ":")),
            help_text=str(help_text or "").strip(),
            options=tuple(str(value or "").strip() for value in (options or []) if str(value or "").strip()),
            version=1,
        )
        self.store.insert_intake_field(field)
        if self.sync is not None:
            self.sync.publish_intake_field(field)
        return field

    def get_intake_field(self, field_id: str) -> IntakeField:
        self._require_admin("Intake fields")
        return self.store.get_intake_field(_required_text(field_id, "Field ID"))

    def list_intake_fields(self) -> list[IntakeField]:
        self._require_admin("Intake fields")
        return self.store.list_intake_fields()

    def list_pdf_mappings(self) -> list[PdfFieldMapping]:
        self._require_admin("PDF mappings")
        return self.store.list_pdf_mappings()

    def preview_pdf_mapping(self, source_path: Path) -> PdfMappingPreviewResult:
        from onboarding_pdf_fill import PdfFillEngine, detect_acroform_fields

        self._require_admin("PDF mapping preview")
        source = Path(source_path).resolve(strict=True)
        fields = {field.id: field for field in self.store.list_intake_fields()}
        mappings = [item for item in self.store.list_pdf_mappings() if item.document_key == source.stem]
        synthetic = {
            field.stable_id: _synthetic_intake_value(field)
            for field in fields.values()
        }
        preview_root = (
            self.artifact_vault.temp_root if self.artifact_vault is not None
            else self.store.path.parent / "synthetic_previews"
        )
        preview_root.mkdir(parents=True, exist_ok=True)
        output = preview_root / f"synthetic-{uuid4().hex}.pdf"
        manifest = preview_root / f"synthetic-{uuid4().hex}.json"
        try:
            result = PdfFillEngine().fill_document(
                source_path=source, output_path=output, mappings=mappings,
                fields=fields, values=synthetic, manifest_path=manifest,
            )
        except ValueError as exc:
            return PdfMappingPreviewResult(
                output_path=None, acroform_fields=detect_acroform_fields(source),
                synthetic_values=tuple(sorted(synthetic.items())),
                overflow_errors=(str(exc),), required_signatures=(),
                mapping_results=tuple(
                    PdfMappingFitResult(item.id, False, str(exc)) for item in mappings
                ),
            )
        return PdfMappingPreviewResult(
            output_path=result.output_path, acroform_fields=detect_acroform_fields(source),
            synthetic_values=tuple(sorted(synthetic.items())), overflow_errors=(),
            required_signatures=result.required_signatures,
            mapping_results=tuple(PdfMappingFitResult(item.id, True) for item in mappings),
        )

    def search_intake_fields(self, query: str) -> list[IntakeField]:
        self._require_admin("Intake fields")
        needle = str(query or "").strip().casefold()
        fields = [field for field in self.store.list_intake_fields() if not field.deprecated]
        if not needle:
            return fields
        return [
            field for field in fields
            if needle in field.label.casefold()
            or needle in field.stable_id.casefold()
            or any(needle in alias.casefold() for alias in field.aliases)
        ]

    def suggest_similar_intake_fields(self, label: str, *, aliases: list[str] | None = None) -> list[IntakeField]:
        self._require_admin("Intake fields")
        candidates = {_required_text(label, "Field label").casefold(), *(
            str(value or "").strip().casefold() for value in (aliases or []) if str(value or "").strip()
        )}
        scored: list[tuple[float, IntakeField]] = []
        for field in self.store.list_intake_fields():
            if field.deprecated:
                continue
            names = {field.label.casefold(), field.stable_id.casefold().replace("_", " "), *(alias.casefold() for alias in field.aliases)}
            score = max(SequenceMatcher(None, left, right).ratio() for left in candidates for right in names)
            token_overlap = any(set(left.split()) & set(right.split()) for left in candidates for right in names)
            if score >= 0.7 or token_overlap:
                scored.append((score, field))
        return [field for _score, field in sorted(scored, key=lambda item: (-item[0], item[1].label.casefold()))]

    @_sync_mutation
    def deprecate_intake_field(self, field_id: str) -> IntakeField:
        self._require_admin("Intake fields")
        return self.store.deprecate_intake_field(_required_text(field_id, "Field ID"))

    @_sync_mutation
    def create_pdf_mapping(
        self,
        *,
        document_key: str,
        page_number: int,
        rect: tuple[float, float, float, float],
        field_id: str = "",
        new_field: dict[str, object] | None = None,
        required: bool = False,
        font_name: str = "Helvetica",
        font_size: float = 10.0,
        alignment: str = "left",
        multiline: bool = False,
        formatting: dict[str, object] | None = None,
    ) -> PdfFieldMapping:
        self._require_admin("PDF mappings")
        if bool(str(field_id or "").strip()) == bool(new_field):
            raise ValueError("Select one existing field or create one new field.")
        if new_field:
            field = self.create_intake_field(**new_field)
        else:
            field = self.get_intake_field(field_id)
        if len(rect) != 4 or any(float(value) < 0 for value in rect[:2]) or any(float(value) <= 0 for value in rect[2:]):
            raise ValueError("PDF mapping rectangle must contain non-negative coordinates and positive size.")
        if int(page_number) < 1:
            raise ValueError("PDF mapping page number must be at least one.")
        clean_alignment = str(alignment or "").strip().casefold()
        if clean_alignment not in {"left", "center", "right"}:
            raise ValueError("PDF mapping alignment must be left, center, or right.")
        mapping = PdfFieldMapping(
            id=str(uuid4()),
            document_key=_required_text(document_key, "Document key"),
            page_number=int(page_number),
            rect=tuple(float(value) for value in rect),
            field_id=field.id,
            required=bool(required),
            font_name=_required_text(font_name, "Font name"),
            font_size=float(font_size),
            alignment=clean_alignment,
            multiline=bool(multiline),
            formatting_json=json.dumps(formatting or {}, sort_keys=True, separators=(",", ":")),
        )
        if mapping.font_size <= 0:
            raise ValueError("PDF mapping font size must be positive.")
        self.store.insert_pdf_mapping(mapping)
        if self.sync is not None:
            self.sync.publish_pdf_mapping(mapping)
        return mapping

    @_sync_mutation
    def submit_intake(
        self,
        *,
        submission_id: str,
        employee_id: str,
        application_id: str,
        schema_version: int,
        values: dict[str, object],
    ) -> IntakeSubmission:
        submission_key = _required_text(submission_id, "Submission ID")
        existing = self.store.get_intake_submission(submission_key)
        if existing is not None:
            self._require_school(existing.school)
            return existing
        employee = self.store.get_employee(_required_text(employee_id, "Employee ID"))
        self._require_school(employee.school)
        if int(schema_version) < 1:
            raise ValueError("Intake schema version must be at least one.")
        if not isinstance(values, dict):
            raise ValueError("Intake values must be an object.")
        fields = {field.stable_id: field for field in self.store.list_intake_fields() if not field.deprecated}
        unknown = sorted(set(values) - set(fields))
        if unknown:
            raise ValueError(f"Unknown intake field: {unknown[0]}")
        normalized = {
            stable_id: _validate_intake_value(fields[stable_id], value)
            for stable_id, value in values.items()
        }
        submission = IntakeSubmission(
            id=submission_key,
            employee_id=employee.id,
            application_id=_required_text(application_id, "Application ID"),
            school=employee.school,
            schema_version=int(schema_version),
            values=normalized,
            revision=1,
            status="accepted",
            created_at=_utc_now(),
        )
        self.store.insert_intake_submission(submission)
        submission = self._generate_assigned_packages(submission, employee)
        if self.sync is not None:
            self.sync.publish_intake_submission(submission)
            self._publish_submission_artifacts(submission.id)
        return submission

    @_sync_mutation
    def correct_intake_submission(
        self,
        submission_id: str,
        *,
        correction_id: str,
        values: dict[str, object],
    ) -> IntakeSubmission:
        original = self.store.get_intake_submission(_required_text(submission_id, "Submission ID"))
        if original is None:
            raise ValueError("Intake submission not found.")
        self._require_school(original.school)
        existing = self.store.get_intake_submission(_required_text(correction_id, "Correction ID"))
        if existing is not None:
            if existing.corrects_submission_id != original.id:
                raise ValueError("Correction ID already belongs to another submission.")
            return existing
        fields = {field.stable_id: field for field in self.store.list_intake_fields()}
        unknown = sorted(set(values) - set(fields))
        if unknown:
            raise ValueError(f"Unknown intake field: {unknown[0]}")
        normalized = {
            stable_id: _validate_intake_value(fields[stable_id], value)
            for stable_id, value in values.items()
        }
        correction = IntakeSubmission(
            id=correction_id,
            employee_id=original.employee_id,
            application_id=original.application_id,
            school=original.school,
            schema_version=original.schema_version,
            values=normalized,
            revision=original.revision + 1,
            status="accepted",
            created_at=_utc_now(),
            corrects_submission_id=original.id,
        )
        self.store.insert_intake_submission(correction)
        employee = self.store.get_employee(original.employee_id)
        correction = self._generate_assigned_packages(correction, employee)
        if self.sync is not None:
            self.sync.publish_intake_submission(correction)
            self._publish_submission_artifacts(correction.id)
        return correction

    def _publish_submission_artifacts(self, submission_id: str) -> None:
        if self.sync is None or self.artifact_vault is None:
            return
        for artifact in self.store.list_filled_artifacts(submission_id=submission_id):
            self.sync.publish_filled_artifact(
                artifact,
                sealed_path=self.artifact_vault.root / f"{artifact.id}.obv",
            )

    def _generate_assigned_packages(
        self,
        submission: IntakeSubmission,
        employee: OnboardingEmployee,
    ) -> IntakeSubmission:
        if self.artifact_vault is None:
            return submission
        package_ids = tuple(dict.fromkeys(
            task.package_version_id
            for task in self.store.list_tasks(school=employee.school)
            if task.employee_id == employee.id and task.package_version_id
        ))
        try:
            for package_version_id in package_ids:
                self.generate_filled_package(
                    submission_id=submission.id,
                    package_version_id=package_version_id,
                    publish_sync=False,
                )
        except (OSError, ValueError) as exc:
            submission = self.store.update_intake_submission_status(
                submission.id,
                status="attention",
            )
            self.store.append_audit_event(
                entity_id=employee.id,
                action="intake.package_generation_attention",
                actor=self.access.actor,
                school=employee.school,
                version=employee.version,
                details={"error_category": type(exc).__name__},
                created_at=_utc_now(),
            )
        return submission

    @_sync_mutation
    def create_document_package_draft(
        self,
        *,
        package_key: str,
        school: str,
        title: str,
        document_paths: list[Path],
    ) -> DocumentPackageVersion:
        self._require_admin("Document packages")
        clean_key = _required_text(package_key, "Package key")
        clean_school = _required_text(school, "School")
        if not document_paths:
            raise ValueError("Document package requires at least one PDF.")
        documents: list[PackageDocument] = []
        contents: list[bytes] = []
        for position, path_value in enumerate(document_paths, start=1):
            path = Path(path_value)
            if path.suffix.casefold() != ".pdf" or not path.is_file():
                raise ValueError("Document packages accept PDF files only.")
            content = path.read_bytes()
            if len(content) > 25 * 1024 * 1024:
                raise ValueError("Document package PDF exceeds the 25 MB limit.")
            if not content.startswith(b"%PDF-"):
                raise ValueError("Document package file signature is not PDF.")
            documents.append(
                PackageDocument(
                    position=position,
                    name=path.name,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
            contents.append(content)
        package = DocumentPackageVersion(
            id=str(uuid4()),
            package_key=clean_key,
            school=clean_school,
            title=_required_text(title, "Package title"),
            version=self.store.next_package_version(clean_key, school=clean_school),
            status="draft",
            documents=tuple(documents),
            created_at=_utc_now(),
        )
        self.store.insert_document_package(package, document_contents=contents)
        if self.sync is not None:
            self.sync.publish_document_package(package)
        return package

    @_sync_mutation
    def publish_document_package(self, package_version_id: str) -> DocumentPackageVersion:
        self._require_admin("Document packages")
        package = self.store.publish_document_package(
            _required_text(package_version_id, "Package version ID"),
            published_at=_utc_now(),
        )
        if self.sync is not None:
            self.sync.publish_document_package(package)
        return package

    def validate_document_package(self, package_version_id: str) -> tuple[str, ...]:
        from pypdf import PdfReader

        self._require_admin("Document packages")
        package = self.store.get_document_package(
            _required_text(package_version_id, "Package version ID")
        )
        contents = self.store.get_document_package_contents(package.id)
        issues: list[str] = []
        for document, content in zip(package.documents, contents, strict=True):
            if hashlib.sha256(content).hexdigest() != document.sha256 or len(content) != document.size_bytes:
                issues.append(f"{document.name}: encrypted content does not match metadata")
                continue
            try:
                reader = PdfReader(io.BytesIO(content))
                if not reader.pages:
                    issues.append(f"{document.name}: PDF has no pages")
            except (OSError, ValueError) as exc:
                issues.append(f"{document.name}: invalid PDF ({type(exc).__name__})")
        return tuple(issues)

    def latest_published_document_package(
        self,
        package_key: str,
        *,
        school: str,
    ) -> DocumentPackageVersion | None:
        self._require_admin("Document packages")
        return self.store.latest_published_document_package(
            _required_text(package_key, "Package key"),
            school=_required_text(school, "School"),
        )

    def list_document_package_versions(self) -> list[DocumentPackageVersion]:
        self._require_admin("Document packages")
        return self.store.list_document_package_versions()

    @_sync_mutation
    def upgrade_employee_package(
        self,
        *,
        package_key: str,
        package_version_id: str,
        employee_ids: list[str],
    ) -> int:
        self._require_admin("Document package upgrades")
        key = _required_text(package_key, "Package key")
        target = self.store.get_document_package(_required_text(package_version_id, "Package version ID"))
        if target.status != "published" or target.package_key != key:
            raise ValueError("Package upgrade target must be a published version of the selected package.")
        selected = set(employee_ids)
        if not selected:
            raise ValueError("Package upgrade requires at least one employee.")
        changed = 0
        now = _utc_now()
        for employee_id in selected:
            employee = self.get_employee(employee_id)
            if employee.school.casefold() != target.school.casefold():
                raise ValueError("Package upgrade school does not match employee school.")
            for task in self.store.list_tasks(school=employee.school):
                if task.employee_id != employee.id or not task.package_version_id:
                    continue
                current = self.store.get_document_package(task.package_version_id)
                if current.package_key != key or current.id == target.id:
                    continue
                upgraded = replace(task, package_version_id=target.id, version=task.version + 1)
                self.store.replace_task(
                    upgraded,
                    expected_version=task.version,
                    actor=self.access.actor,
                    updated_at=now,
                    changed_fields=("package_version_id",),
                )
                if self.sync is not None:
                    self.sync.publish_task(upgraded, base=task, changed_fields=("package_version_id",))
                changed += 1
        return changed

    def preview_employee_package_upgrade(
        self,
        *,
        package_key: str,
        package_version_id: str,
        employee_ids: list[str],
    ) -> tuple[str, ...]:
        self._require_admin("Document package upgrades")
        key = _required_text(package_key, "Package key")
        target = self.store.get_document_package(
            _required_text(package_version_id, "Package version ID")
        )
        if target.status != "published" or target.package_key != key:
            raise ValueError("Package upgrade target must be a published version of the selected package.")
        impacted: list[str] = []
        for employee_id in dict.fromkeys(employee_ids):
            employee = self.get_employee(employee_id)
            if employee.school.casefold() != target.school.casefold():
                raise ValueError("Package upgrade school does not match employee school.")
            if any(
                task.employee_id == employee.id
                and task.package_version_id
                and self.store.get_document_package(task.package_version_id).package_key == key
                and task.package_version_id != target.id
                for task in self.store.list_tasks(school=employee.school)
            ):
                impacted.append(employee.id)
        return tuple(impacted)

    def generate_filled_package(
        self,
        *,
        submission_id: str,
        package_version_id: str,
        publish_sync: bool = True,
    ) -> GeneratedPackageArtifacts:
        from onboarding_pdf_fill import PdfFillEngine

        self.sync_pending()
        if self.artifact_vault is None:
            raise ValueError("Encrypted onboarding artifact vault is unavailable.")
        submission = self.store.get_intake_submission(_required_text(submission_id, "Submission ID"))
        if submission is None:
            raise ValueError("Intake submission not found.")
        self._require_school(submission.school)
        package = self.store.get_document_package(
            _required_text(package_version_id, "Package version ID")
        )
        if package.status != "published" or package.school.casefold() != submission.school.casefold():
            raise ValueError("Filled package must use a published version for the employee school.")
        documents = package.documents
        contents = self.store.get_document_package_contents(package.id)
        fields = {field.id: field for field in self.store.list_intake_fields()}
        engine = PdfFillEngine()
        working_paths: list[Path] = []
        filled_paths: list[Path] = []
        required_signatures: set[str] = set()
        sealed_paths: list[Path] = []
        individual_ids: list[str] = []
        created_at = _utc_now()
        try:
            for document, content in zip(documents, contents, strict=True):
                work_key = uuid4().hex
                source = self.artifact_vault.temp_root / f"source-{work_key}.pdf"
                filled = self.artifact_vault.temp_root / f"filled-{work_key}.pdf"
                manifest = self.artifact_vault.temp_root / f"manifest-{work_key}.txt"
                OnboardingVault._write_bytes_atomic(source, content)
                working_paths.extend((source, filled, manifest))
                mappings = self.store.list_pdf_mappings(Path(document.name).stem)
                result = engine.fill_document(
                    source_path=source,
                    output_path=filled,
                    mappings=mappings,
                    fields=fields,
                    values=submission.values,
                    manifest_path=manifest,
                )
                required_signatures.update(result.required_signatures)
                filled_paths.append(filled)

            merged = self.artifact_vault.temp_root / f"merged-{uuid4().hex}.pdf"
            merge_manifest = self.artifact_vault.temp_root / f"package-manifest-{uuid4().hex}.txt"
            working_paths.extend((merged, merge_manifest))
            merged_result = engine.merge_documents(
                document_paths=filled_paths,
                output_path=merged,
                manifest_path=merge_manifest,
            )
            manifest_payload = {
                "schema_version": 1,
                "submission_id": submission.id,
                "package_version_id": package.id,
                "individual_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in filled_paths],
                "merged_sha256": merged_result.output_sha256,
                "required_signatures": sorted(required_signatures),
            }
            OnboardingVault._write_bytes_atomic(
                merge_manifest,
                (json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            )

            records: list[tuple[FilledArtifact, Path]] = []
            for position, path in enumerate(filled_paths, start=1):
                artifact_id = f"filled-{uuid4().hex}"
                sealed = self.artifact_vault.seal_file(
                    submission.school, path, artifact_id=artifact_id
                )
                sealed_paths.append(sealed)
                individual_ids.append(artifact_id)
                records.append((FilledArtifact(
                    id=artifact_id, employee_id=submission.employee_id,
                    submission_id=submission.id, package_version_id=package.id,
                    school=submission.school, kind=f"individual:{position}", suffix=".pdf",
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), created_at=created_at,
                ), sealed))
            merged_id = f"merged-{uuid4().hex}"
            sealed_merged = self.artifact_vault.seal_file(
                submission.school, merged, artifact_id=merged_id
            )
            sealed_paths.append(sealed_merged)
            records.append((FilledArtifact(
                id=merged_id, employee_id=submission.employee_id,
                submission_id=submission.id, package_version_id=package.id,
                school=submission.school, kind="merged", suffix=".pdf",
                sha256=merged_result.output_sha256, created_at=created_at,
            ), sealed_merged))
            manifest_id = f"manifest-{uuid4().hex}"
            sealed_manifest = self.artifact_vault.seal_file(
                submission.school, merge_manifest, artifact_id=manifest_id
            )
            sealed_paths.append(sealed_manifest)
            records.append((FilledArtifact(
                id=manifest_id, employee_id=submission.employee_id,
                submission_id=submission.id, package_version_id=package.id,
                school=submission.school, kind="manifest", suffix=".txt",
                sha256=hashlib.sha256(merge_manifest.read_bytes()).hexdigest(), created_at=created_at,
            ), sealed_manifest))
            for record, _sealed in records:
                self.store.insert_filled_artifact(record)
                if self.sync is not None and publish_sync:
                    self.sync.publish_filled_artifact(record, sealed_path=_sealed)
            return GeneratedPackageArtifacts(
                individual_artifact_ids=tuple(individual_ids),
                merged_artifact_id=merged_id,
                manifest_artifact_id=manifest_id,
                sealed_paths=tuple(sealed_paths),
            )
        finally:
            for path in working_paths:
                if path.exists() and self.artifact_vault.temp_root in path.resolve().parents:
                    path.unlink(missing_ok=True)

    def open_filled_artifact(
        self,
        *,
        employee_id: str,
        artifact_id: str,
        suffix: str,
    ) -> Path:
        self.sync_pending()
        if self.artifact_vault is None:
            raise ValueError("Encrypted onboarding artifact vault is unavailable.")
        employee = self.get_employee(employee_id)
        artifact = self.store.get_filled_artifact(_required_text(artifact_id, "Artifact ID"))
        if artifact.employee_id != employee.id or artifact.school.casefold() != employee.school.casefold():
            raise OnboardingPermissionError("Filled artifact is outside the authorized employee scope.")
        if str(suffix or "").casefold() != artifact.suffix:
            raise ValueError("Filled artifact suffix does not match stored metadata.")
        opened = self.artifact_vault.open_temp(
            artifact.school,
            self.artifact_vault.root / f"{artifact.id}.obv",
            artifact_id=artifact.id,
            suffix=artifact.suffix,
        )
        if hashlib.sha256(opened.read_bytes()).hexdigest() != artifact.sha256:
            self.artifact_vault.cleanup_temp(opened)
            raise ValueError("Filled artifact hash validation failed.")
        self.store.append_audit_event(
            entity_id=employee.id,
            action="filled_artifact.opened",
            actor=self.access.actor,
            school=employee.school,
            version=employee.version,
            details={"artifact_id": artifact.id, "kind": artifact.kind},
            created_at=_utc_now(),
        )
        return opened

    def close_filled_artifact(self, path: Path) -> None:
        if self.artifact_vault is None:
            raise ValueError("Encrypted onboarding artifact vault is unavailable.")
        self.artifact_vault.cleanup_temp(path)

    def export_filled_artifact(
        self,
        *,
        employee_id: str,
        artifact_id: str,
        destination: Path,
        confirmed_sensitive: bool,
    ) -> Path:
        if not confirmed_sensitive:
            raise ValueError("Filled artifact export requires sensitive-data confirmation.")
        artifact = self.store.get_filled_artifact(_required_text(artifact_id, "Artifact ID"))
        target = Path(destination).resolve()
        if target.exists() or target.suffix.casefold() != artifact.suffix:
            raise ValueError("Filled artifact export destination must be a new matching file type.")
        target.parent.mkdir(parents=True, exist_ok=True)
        opened = self.open_filled_artifact(
            employee_id=employee_id,
            artifact_id=artifact.id,
            suffix=artifact.suffix,
        )
        try:
            OnboardingVault._write_bytes_atomic(target, opened.read_bytes())
        finally:
            self.close_filled_artifact(opened)
        employee = self.get_employee(employee_id)
        self.store.append_audit_event(
            entity_id=employee.id,
            action="filled_artifact.exported",
            actor=self.access.actor,
            school=employee.school,
            version=employee.version,
            details={"artifact_id": artifact.id, "kind": artifact.kind},
            created_at=_utc_now(),
        )
        return target

    def _new_employee(
        self,
        *,
        legal_name: str,
        preferred_name: str,
        school: str,
        role: str,
        acceptance_date: str,
        start_date: str,
        source_application_id: str = "",
        email: str = "",
        phone: str = "",
        hiring_director_id: str = "",
        hiring_director_name: str = "",
        address_line1: str = "",
        address_line2: str = "",
        city: str = "",
        state: str = "",
        postal_code: str = "",
        personal_email: str = "",
        work_email: str = "",
        notes: str = "",
        source_history_id: str = "",
        dob: str = "",
        ssn: str = "",
        status: str = "active",
    ) -> OnboardingEmployee:
        clean_school = _required_text(school, "School")
        self._require_school(clean_school)
        now = _utc_now()
        profile = _normalize_employee_profile(
            {
                "legal_name": legal_name,
                "preferred_name": preferred_name,
                "role": role,
                "acceptance_date": acceptance_date,
                "start_date": start_date,
                "address_line1": address_line1,
                "address_line2": address_line2,
                "city": city,
                "state": state,
                "postal_code": postal_code,
                "personal_email": personal_email or email,
                "work_email": work_email,
                "phone": phone,
                "notes": notes,
                "source_history_id": source_history_id,
                "dob": dob,
                "ssn": ssn,
            }
        )
        return OnboardingEmployee(
            id=str(uuid4()),
            legal_name=profile["legal_name"],
            preferred_name=profile["preferred_name"],
            school=clean_school,
            role=profile["role"],
            acceptance_date=profile["acceptance_date"],
            start_date=profile["start_date"],
            status=str(status or "active").strip().casefold(),
            version=1,
            created_at=now,
            updated_at=now,
            source_application_id=source_application_id,
            email=profile["personal_email"],
            phone=profile["phone"],
            hiring_director_id=hiring_director_id,
            hiring_director_name=hiring_director_name,
            address_line1=profile["address_line1"],
            address_line2=profile["address_line2"],
            city=profile["city"],
            state=profile["state"],
            postal_code=profile["postal_code"],
            personal_email=profile["personal_email"],
            work_email=profile["work_email"],
            notes=profile["notes"],
            source_history_id=profile["source_history_id"],
            dob=profile["dob"],
            ssn=profile["ssn"],
        )

    def _require_school(self, school: str) -> None:
        if self.access.role != "director":
            return
        if school.casefold() != self.access.school_scope.casefold():
            raise OnboardingPermissionError("Employee is outside the director school scope.")

    def _require_admin(self, feature: str) -> None:
        if self.access.role != "admin":
            raise OnboardingPermissionError(f"{feature} are admin-only.")


def _required_text(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required.")
    return clean


def _date_text(value: str, label: str) -> str:
    clean = _required_text(value, label)
    try:
        return date.fromisoformat(clean).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _normalize_employee_profile(values: dict[str, str]) -> dict[str, str]:
    normalized = {name: str(values.get(name, "") or "").strip() for name in EMPLOYEE_PROFILE_FIELDS}
    normalized["legal_name"] = _required_text(normalized["legal_name"], "Legal name")
    normalized["role"] = _required_text(normalized["role"], "Role")
    normalized["acceptance_date"] = _date_text(normalized["acceptance_date"], "Acceptance date")
    normalized["start_date"] = _date_text(normalized["start_date"], "Start date")
    if normalized["acceptance_date"] > normalized["start_date"]:
        raise ValueError("Acceptance date cannot be after start date.")
    for field_name in ("personal_email", "work_email"):
        value = normalized[field_name]
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError(f"{field_name.replace('_', ' ').title()} must be a valid email address.")
    if normalized["phone"]:
        normalized["phone"] = re.sub(r"\D", "", normalized["phone"])
        if len(normalized["phone"]) != 10:
            raise ValueError("Phone must contain 10-digit contact number.")
    if normalized["ssn"]:
        normalized["ssn"] = re.sub(r"\D", "", normalized["ssn"])
        if len(normalized["ssn"]) != 9:
            raise ValueError("SSN must contain 9-digit value.")
    if normalized["dob"]:
        normalized["dob"] = _date_text(normalized["dob"], "DOB")
    if normalized["state"]:
        if not re.fullmatch(r"[A-Za-z]{2}", normalized["state"]):
            raise ValueError("State must use a two-letter code.")
        normalized["state"] = normalized["state"].upper()
    if normalized["postal_code"] and not re.fullmatch(r"\d{5}(?:-\d{4})?", normalized["postal_code"]):
        raise ValueError("Postal code must be a valid ZIP code.")
    return normalized


def _validate_intake_value(field: IntakeField, value: object) -> object:
    if field.field_type in {"short_text", "long_text", "signature", "initials"}:
        if not isinstance(value, str):
            raise ValueError(f"Invalid value for intake field: {field.stable_id}")
        return value
    if field.field_type == "date":
        return _date_text(str(value), field.label)
    if field.field_type == "email":
        text = str(value or "").strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text):
            raise ValueError(f"Invalid value for intake field: {field.stable_id}")
    return text
    if field.field_type in {"phone", "ssn"}:
        digits = re.sub(r"\D", "", str(value or ""))
        expected = 10 if field.field_type == "phone" else 9
        if len(digits) != expected:
            raise ValueError(f"Invalid value for intake field: {field.stable_id}")
        return digits
    if field.field_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"Invalid value for intake field: {field.stable_id}")
        return value
    if field.field_type == "yes_no":
        if not isinstance(value, bool):
            raise ValueError(f"Invalid value for intake field: {field.stable_id}")
        return value
    allowed = set(field.options)
    selected = value if isinstance(value, list) else [value]
    if not selected or any(str(item) not in allowed for item in selected):
        raise ValueError(f"Invalid value for intake field: {field.stable_id}")
    return [str(item) for item in selected] if field.field_type == "multiple_choice" else str(selected[0])


def _synthetic_intake_value(field: IntakeField) -> object:
    samples: dict[str, object] = {
        "short_text": "Sample value", "long_text": "Synthetic preview value",
        "date": "2026-07-20", "email": "preview@example.invalid",
        "phone": "6615550101", "ssn": "123456789", "number": 42,
        "yes_no": True, "signature": "", "initials": "",
    }
    if field.field_type == "single_choice":
        return field.options[0] if field.options else "Sample"
    if field.field_type == "multiple_choice":
        return [field.options[0]] if field.options else ["Sample"]
    return samples.get(field.field_type, "Sample value")
