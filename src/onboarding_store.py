from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from onboarding_vault import OnboardingVault, VaultIntegrityError, load_or_create_device_vault


@dataclass(frozen=True)
class OnboardingEmployee:
    id: str
    legal_name: str
    preferred_name: str
    school: str
    role: str
    acceptance_date: str
    start_date: str
    status: str
    version: int
    created_at: str
    updated_at: str
    source_application_id: str = ""
    email: str = ""
    phone: str = ""
    hiring_director_id: str = ""
    hiring_director_name: str = ""
    last_working_day: str = ""
    departure_category: str = ""
    departure_notes: str = ""
    departure_director_id: str = ""
    departure_director_name: str = ""
    archived_at: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    personal_email: str = ""
    work_email: str = ""
    notes: str = ""
    source_history_id: str = ""
    dob: str = ""
    ssn: str = ""

    @property
    def did_not_start_reason(self) -> str:
        prefix = "did_not_start:"
        return self.departure_category.removeprefix(prefix) if self.departure_category.startswith(prefix) else ""


@dataclass(frozen=True)
class OnboardingTask:
    id: str
    employee_id: str
    school: str
    title: str
    owner_role: str
    watcher_roles: tuple[str, ...]
    due_date: str
    critical: bool
    status: str
    version: int
    dependency_ids: tuple[str, ...]
    parent_task_id: str = ""
    required: bool = True
    template_key: str = ""
    template_version: int = 0
    notes: str = ""
    package_version_id: str = ""
    template_id: str = ""


@dataclass(frozen=True)
class TaskComment:
    id: str
    task_id: str
    employee_id: str
    school: str
    author: str
    body: str
    version: int
    redacted: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskCommentRevision:
    comment_id: str
    version: int
    body: str
    editor: str
    reason: str
    created_at: str


@dataclass(frozen=True)
class TaskAttachment:
    id: str
    task_id: str
    employee_id: str
    school: str
    name: str
    media_type: str
    sha256: str
    size_bytes: int
    scan_status: str
    warning: str
    created_at: str


@dataclass(frozen=True)
class TaskTemplateAttachment:
    id: str
    template_id: str
    school: str
    name: str
    media_type: str
    sha256: str
    size_bytes: int
    scan_status: str
    warning: str
    created_at: str


@dataclass(frozen=True)
class TaskTemplateVersion:
    id: str
    template_key: str
    school: str
    title: str
    owner_role: str
    watcher_roles: tuple[str, ...]
    due_offset_days: int
    critical: bool
    version: int
    status: str
    created_at: str
    published_at: str = ""
    package_key: str = ""
    content: str = ""
    base_template_id: str = ""
    override_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnerRoleConfig:
    school: str
    role: str
    email: str
    active: bool
    version: int


@dataclass(frozen=True)
class IntakeField:
    id: str
    stable_id: str
    label: str
    aliases: tuple[str, ...]
    field_type: str
    sensitivity: str
    validation_json: str
    help_text: str
    options: tuple[str, ...]
    version: int
    deprecated: bool = False


@dataclass(frozen=True)
class PdfFieldMapping:
    id: str
    document_key: str
    page_number: int
    rect: tuple[float, float, float, float]
    field_id: str
    required: bool
    font_name: str
    font_size: float
    alignment: str
    multiline: bool
    formatting_json: str


@dataclass(frozen=True)
class IntakeSubmission:
    id: str
    employee_id: str
    application_id: str
    school: str
    schema_version: int
    values: dict[str, Any]
    revision: int
    status: str
    created_at: str
    corrects_submission_id: str = ""


@dataclass(frozen=True)
class PackageDocument:
    position: int
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class DocumentPackageVersion:
    id: str
    package_key: str
    school: str
    title: str
    version: int
    status: str
    documents: tuple[PackageDocument, ...]
    created_at: str
    published_at: str = ""


@dataclass(frozen=True)
class FilledArtifact:
    id: str
    employee_id: str
    submission_id: str
    package_version_id: str
    school: str
    kind: str
    suffix: str
    sha256: str
    created_at: str


class OnboardingStore:
    """SQLite persistence for one onboarding replica."""

    def __init__(self, path: Path, *, vault: OnboardingVault | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.vault = vault or load_or_create_device_vault(self.path.parent / "onboarding_vault_key.dpapi")
        self._initialize()

    def insert_employee(self, employee: OnboardingEmployee, *, actor: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_employees (
                    id, legal_name, preferred_name, school, role,
                    acceptance_date, start_date, status, version, created_at, updated_at,
                    source_application_id, email, phone, hiring_director_id, hiring_director_name
                    , last_working_day, departure_category, departure_notes,
                    departure_director_id, departure_director_name, archived_at,
                    address_line1, address_line2, city, state, postal_code,
                    personal_email, work_email, notes, source_history_id, dob, ssn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    employee.id,
                    employee.legal_name,
                    employee.preferred_name,
                    employee.school,
                    employee.role,
                    employee.acceptance_date,
                    employee.start_date,
                    employee.status,
                    employee.version,
                    employee.created_at,
                    employee.updated_at,
                    employee.source_application_id,
                    self._encrypt_text(employee.school, employee.email, f"employee.email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.phone, f"employee.phone:{employee.id}"),
                    employee.hiring_director_id,
                    employee.hiring_director_name,
                    employee.last_working_day,
                    employee.departure_category,
                    employee.departure_notes,
                    employee.departure_director_id,
                    employee.departure_director_name,
                    employee.archived_at,
                    self._encrypt_text(employee.school, employee.address_line1, f"employee.address_line1:{employee.id}"),
                    self._encrypt_text(employee.school, employee.address_line2, f"employee.address_line2:{employee.id}"),
                    self._encrypt_text(employee.school, employee.city, f"employee.city:{employee.id}"),
                    self._encrypt_text(employee.school, employee.state, f"employee.state:{employee.id}"),
                    self._encrypt_text(employee.school, employee.postal_code, f"employee.postal_code:{employee.id}"),
                    self._encrypt_text(employee.school, employee.personal_email, f"employee.personal_email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.work_email, f"employee.work_email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.notes, f"employee.notes:{employee.id}"),
                    employee.source_history_id,
                    self._encrypt_text(employee.school, employee.dob, f"employee.dob:{employee.id}"),
                    self._encrypt_text(employee.school, employee.ssn, f"employee.ssn:{employee.id}"),
                ),
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'employee', 'employee.created', ?, ?, ?, '{}', ?)
                """,
                (employee.id, actor, employee.school, employee.version, employee.created_at),
            )

    def list_employees(self, *, school: str = "") -> list[OnboardingEmployee]:
        sql = "SELECT * FROM onboarding_employees WHERE deleted_at = ''"
        parameters: tuple[Any, ...] = ()
        if school:
            sql += " AND school = ? COLLATE NOCASE"
            parameters = (school,)
        sql += " ORDER BY legal_name COLLATE NOCASE, id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._employee_from_row(row) for row in rows]

    def employee_for_application(self, application_id: str) -> OnboardingEmployee | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_employees WHERE source_application_id = ? AND deleted_at = ''",
                (str(application_id or "").strip(),),
            ).fetchone()
        return None if row is None else self._employee_from_row(row)

    def possible_duplicate_employees(self, *, legal_name: str, school: str) -> list[OnboardingEmployee]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM onboarding_employees
                WHERE legal_name = ? COLLATE NOCASE AND school = ? COLLATE NOCASE
                  AND deleted_at = ''
                ORDER BY created_at, id
                """,
                (str(legal_name or "").strip(), str(school or "").strip()),
            ).fetchall()
        return [self._employee_from_row(row) for row in rows]

    def get_employee(self, employee_id: str) -> OnboardingEmployee:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_employees WHERE id = ? AND deleted_at = ''",
                (str(employee_id or "").strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Onboarding employee not found.")
        return self._employee_from_row(row)

    def replace_employee(
        self,
        employee: OnboardingEmployee,
        *,
        expected_version: int | None = None,
        actor: str = "",
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE onboarding_employees
                SET legal_name = ?, preferred_name = ?, school = ?, role = ?,
                    acceptance_date = ?, start_date = ?, status = ?, version = ?,
                    updated_at = ?, source_application_id = ?, email = ?, phone = ?,
                    hiring_director_id = ?, hiring_director_name = ?, last_working_day = ?,
                    departure_category = ?, departure_notes = ?, departure_director_id = ?,
                    departure_director_name = ?, archived_at = ?, address_line1 = ?,
                    address_line2 = ?, city = ?, state = ?, postal_code = ?,
                    personal_email = ?, work_email = ?, notes = ?, source_history_id = ?,
                    dob = ?, ssn = ?
                WHERE id = ? AND (? IS NULL OR version = ?)
                """,
                (
                    employee.legal_name,
                    employee.preferred_name,
                    employee.school,
                    employee.role,
                    employee.acceptance_date,
                    employee.start_date,
                    employee.status,
                    employee.version,
                    employee.updated_at,
                    employee.source_application_id,
                    self._encrypt_text(employee.school, employee.email, f"employee.email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.phone, f"employee.phone:{employee.id}"),
                    employee.hiring_director_id,
                    employee.hiring_director_name,
                    employee.last_working_day,
                    employee.departure_category,
                    employee.departure_notes,
                    employee.departure_director_id,
                    employee.departure_director_name,
                    employee.archived_at,
                    self._encrypt_text(employee.school, employee.address_line1, f"employee.address_line1:{employee.id}"),
                    self._encrypt_text(employee.school, employee.address_line2, f"employee.address_line2:{employee.id}"),
                    self._encrypt_text(employee.school, employee.city, f"employee.city:{employee.id}"),
                    self._encrypt_text(employee.school, employee.state, f"employee.state:{employee.id}"),
                    self._encrypt_text(employee.school, employee.postal_code, f"employee.postal_code:{employee.id}"),
                    self._encrypt_text(employee.school, employee.personal_email, f"employee.personal_email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.work_email, f"employee.work_email:{employee.id}"),
                    self._encrypt_text(employee.school, employee.notes, f"employee.notes:{employee.id}"),
                    employee.source_history_id,
                    self._encrypt_text(employee.school, employee.dob, f"employee.dob:{employee.id}"),
                    self._encrypt_text(employee.school, employee.ssn, f"employee.ssn:{employee.id}"),
                    employee.id,
                    expected_version,
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Onboarding employee changed since it was opened.")
            if actor:
                connection.execute(
                    """
                    INSERT INTO onboarding_audit_events (
                        entity_id, entity_type, action, actor, school, entity_version,
                        details_json, created_at
                    ) VALUES (?, 'employee', 'employee.updated', ?, ?, ?, ?, ?)
                    """,
                    (
                        employee.id,
                        actor,
                        employee.school,
                        employee.version,
                        json.dumps({"changed_fields": sorted(set(changed_fields))}),
                        employee.updated_at,
                    ),
                )

    def end_employment(
        self,
        employee_id: str,
        *,
        last_working_day: str,
        departure_category: str,
        departure_notes: str,
        departure_director_id: str,
        departure_director_name: str,
        actor: str,
        archived_at: str,
    ) -> OnboardingEmployee:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT school, version FROM onboarding_employees WHERE id = ? AND deleted_at = ''",
                (employee_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Onboarding employee not found.")
            version = int(row["version"]) + 1
            connection.execute(
                """
                UPDATE onboarding_employees
                SET status = 'archived', version = ?, updated_at = ?, archived_at = ?,
                    last_working_day = ?, departure_category = ?, departure_notes = ?,
                    departure_director_id = ?, departure_director_name = ?
                WHERE id = ?
                """,
                (
                    version,
                    archived_at,
                    archived_at,
                    last_working_day,
                    departure_category,
                    departure_notes,
                    departure_director_id,
                    departure_director_name,
                    employee_id,
                ),
            )
            connection.execute(
                """
                UPDATE onboarding_tasks
                SET status = 'cancelled', version = version + 1, updated_at = ?
                WHERE employee_id = ? AND deleted_at = '' AND status NOT IN ('completed', 'cancelled')
                """,
                (archived_at, employee_id),
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'employee', 'employee.employment_ended', ?, ?, ?, ?, ?)
                """,
                (
                    employee_id,
                    actor,
                    str(row["school"]),
                    version,
                    json.dumps({"departure_category": departure_category, "last_working_day": last_working_day}),
                    archived_at,
                ),
            )
        return self.get_employee(employee_id)

    def transfer_employee(
        self,
        employee_id: str,
        *,
        new_school: str,
        actor: str,
        updated_at: str,
    ) -> OnboardingEmployee:
        employee = self.get_employee(employee_id)
        if employee.school.casefold() == new_school.casefold():
            return employee
        sensitive_employee_fields = {
            "email": employee.email,
            "phone": employee.phone,
            "address_line1": employee.address_line1,
            "address_line2": employee.address_line2,
            "city": employee.city,
            "state": employee.state,
            "postal_code": employee.postal_code,
            "personal_email": employee.personal_email,
            "work_email": employee.work_email,
            "notes": employee.notes,
            "dob": employee.dob,
            "ssn": employee.ssn,
        }
        tasks = [task for task in self.list_tasks(school=employee.school) if task.employee_id == employee.id]
        with self._connect() as connection:
            comment_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_task_comments WHERE employee_id = ? AND deleted_at = ''",
                    (employee.id,),
                ).fetchall()
            ]
            attachment_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_task_attachments WHERE employee_id = ?",
                    (employee.id,),
                ).fetchall()
            ]
            intake_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_intake_submissions WHERE employee_id = ?",
                    (employee.id,),
                ).fetchall()
            ]
        comment_revisions = {
            comment_id: self.list_task_comment_revisions(comment_id) for comment_id in comment_ids
        }
        attachments = {
            attachment_id: self.get_task_attachment(attachment_id) for attachment_id in attachment_ids
        }
        submissions = {
            submission_id: self.get_intake_submission(submission_id) for submission_id in intake_ids
        }
        with self._connect() as connection:
            version = employee.version + 1
            encrypted_values = {
                name: self._encrypt_text(new_school, value, f"employee.{name}:{employee.id}")
                for name, value in sensitive_employee_fields.items()
            }
            connection.execute(
                """
                UPDATE onboarding_employees
                SET school = ?, email = ?, phone = ?, address_line1 = ?, address_line2 = ?,
                    city = ?, state = ?, postal_code = ?, personal_email = ?, work_email = ?,
                    notes = ?, dob = ?, ssn = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_school,
                    encrypted_values["email"],
                    encrypted_values["phone"],
                    encrypted_values["address_line1"],
                    encrypted_values["address_line2"],
                    encrypted_values["city"],
                    encrypted_values["state"],
                    encrypted_values["postal_code"],
                    encrypted_values["personal_email"],
                    encrypted_values["work_email"],
                    encrypted_values["notes"],
                    encrypted_values["dob"],
                    encrypted_values["ssn"],
                    version,
                    updated_at,
                    employee.id,
                ),
            )
            for task in tasks:
                connection.execute(
                    """
                    UPDATE onboarding_tasks
                    SET school = ?, notes = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_school,
                        self._encrypt_text(new_school, task.notes, f"task.notes:{task.id}"),
                        updated_at,
                        task.id,
                    ),
                )
            for comment_id, revisions in comment_revisions.items():
                connection.execute(
                    "UPDATE onboarding_task_comments SET school = ? WHERE id = ?",
                    (new_school, comment_id),
                )
                for revision in revisions:
                    connection.execute(
                        """
                        UPDATE onboarding_task_comment_revisions
                        SET body_encrypted = ?, reason_encrypted = ?
                        WHERE comment_id = ? AND version = ?
                        """,
                        (
                            self._encrypt_text(new_school, revision.body, f"comment.body:{comment_id}:v{revision.version}"),
                            self._encrypt_text(new_school, revision.reason, f"comment.reason:{comment_id}:v{revision.version}"),
                            comment_id,
                            revision.version,
                        ),
                    )
            for attachment_id, (attachment, content) in attachments.items():
                connection.execute(
                    """
                    UPDATE onboarding_task_attachments
                    SET school = ?, name_encrypted = ?, content_encrypted = ?
                    WHERE id = ?
                    """,
                    (
                        new_school,
                        self._encrypt_text(new_school, attachment.name, f"attachment.name:{attachment_id}"),
                        self.vault.encrypt(new_school, content, context=f"attachment.content:{attachment_id}"),
                        attachment_id,
                    ),
                )
            for submission_id, submission in submissions.items():
                if submission is None:
                    continue
                connection.execute(
                    """
                    UPDATE onboarding_intake_submissions
                    SET school = ?, values_encrypted = ? WHERE id = ?
                    """,
                    (
                        new_school,
                        self.vault.encrypt(
                            new_school,
                            json.dumps(submission.values, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                            context=f"intake.values:{submission_id}",
                        ),
                        submission_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'employee', 'employee.transferred', ?, ?, ?, ?, ?)
                """,
                (
                    employee.id,
                    actor,
                    new_school,
                    version,
                    json.dumps({"from_school": employee.school, "to_school": new_school}),
                    updated_at,
                ),
            )
        return self.get_employee(employee.id)

    def mark_did_not_start(
        self,
        employee_id: str,
        *,
        reason: str,
        notes: str,
        actor: str,
        archived_at: str,
    ) -> OnboardingEmployee:
        employee = self.get_employee(employee_id)
        with self._connect() as connection:
            version = employee.version + 1
            connection.execute(
                """
                UPDATE onboarding_employees
                SET status = 'archived', version = ?, updated_at = ?, archived_at = ?,
                    departure_category = ?, departure_notes = ?, last_working_day = '',
                    departure_director_id = '', departure_director_name = ''
                WHERE id = ?
                """,
                (version, archived_at, archived_at, f"did_not_start:{reason}", notes, employee.id),
            )
            connection.execute(
                """
                UPDATE onboarding_tasks SET status = 'cancelled', version = version + 1, updated_at = ?
                WHERE employee_id = ? AND deleted_at = '' AND status NOT IN ('completed', 'cancelled')
                """,
                (archived_at, employee.id),
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'employee', 'employee.did_not_start', ?, ?, ?, ?, ?)
                """,
                (
                    employee.id,
                    actor,
                    employee.school,
                    version,
                    json.dumps({"reason": reason}),
                    archived_at,
                ),
            )
        return self.get_employee(employee.id)

    def archive_correction(
        self,
        employee_id: str,
        *,
        reason: str,
        actor: str,
        archived_at: str,
    ) -> OnboardingEmployee:
        employee = self.get_employee(employee_id)
        with self._connect() as connection:
            version = employee.version + 1
            connection.execute(
                """
                UPDATE onboarding_employees
                SET status = 'archived', version = ?, updated_at = ?, archived_at = ?,
                    departure_category = ?
                WHERE id = ?
                """,
                (version, archived_at, archived_at, f"correction:{reason}", employee.id),
            )
            connection.execute(
                """
                UPDATE onboarding_tasks SET status = 'cancelled', version = version + 1, updated_at = ?
                WHERE employee_id = ? AND deleted_at = '' AND status NOT IN ('completed', 'cancelled')
                """,
                (archived_at, employee.id),
            )
            self._insert_audit(
                connection,
                entity_id=employee.id,
                action="employee.correction_archived",
                actor=actor,
                school=employee.school,
                version=version,
                details={"correction_reason": reason},
                created_at=archived_at,
            )
        return self.get_employee(employee.id)

    def permanently_remove_employee(
        self,
        employee_id: str,
        *,
        actor: str,
        deleted_at: str,
        tombstone_payload: dict[str, Any],
        action: str,
    ) -> None:
        employee = self.get_employee(employee_id)
        with self._connect() as connection:
            task_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_tasks WHERE employee_id = ?", (employee.id,)
                ).fetchall()
            ]
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                connection.execute(
                    f"DELETE FROM onboarding_task_dependencies WHERE task_id IN ({placeholders}) OR dependency_task_id IN ({placeholders})",
                    tuple(task_ids + task_ids),
                )
            comment_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_task_comments WHERE employee_id = ?", (employee.id,)
                ).fetchall()
            ]
            if comment_ids:
                placeholders = ",".join("?" for _ in comment_ids)
                connection.execute(
                    f"DELETE FROM onboarding_task_comment_revisions WHERE comment_id IN ({placeholders})",
                    tuple(comment_ids),
                )
            connection.execute("DELETE FROM onboarding_task_comments WHERE employee_id = ?", (employee.id,))
            connection.execute("DELETE FROM onboarding_task_attachments WHERE employee_id = ?", (employee.id,))
            connection.execute("DELETE FROM onboarding_tasks WHERE employee_id = ?", (employee.id,))
            connection.execute("DELETE FROM onboarding_intake_submissions WHERE employee_id = ?", (employee.id,))
            connection.execute("DELETE FROM onboarding_employees WHERE id = ?", (employee.id,))
            connection.execute(
                """
                INSERT OR REPLACE INTO onboarding_tombstones (
                    entity_id, entity_type, school, deleted_at, payload_json
                ) VALUES (?, 'employee', ?, ?, ?)
                """,
                (
                    employee.id,
                    employee.school,
                    deleted_at,
                    json.dumps(tombstone_payload, sort_keys=True, separators=(",", ":")),
                ),
            )
            self._insert_audit(
                connection,
                entity_id=employee.id,
                action=action,
                actor=actor,
                school=employee.school,
                version=employee.version + 1,
                details={"tombstone": True},
                created_at=deleted_at,
            )

    def append_audit_event(
        self,
        *,
        entity_id: str,
        action: str,
        actor: str,
        school: str,
        version: int,
        details: dict[str, Any] | None = None,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            self._insert_audit(
                connection,
                entity_id=entity_id,
                action=action,
                actor=actor,
                school=school,
                version=version,
                details=details or {},
                created_at=created_at,
            )

    def list_audit_events(self, *, entity_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM onboarding_audit_events"
        parameters: tuple[Any, ...] = ()
        if entity_id:
            sql += " WHERE entity_id = ?"
            parameters = (entity_id,)
        sql += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            {
                "id": int(row["id"]),
                "entity_id": str(row["entity_id"]),
                "entity_type": str(row["entity_type"]),
                "action": str(row["action"]),
                "actor": str(row["actor"]),
                "school": str(row["school"]),
                "entity_version": int(row["entity_version"]),
                "details": json.loads(str(row["details_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def get_tombstone(self, entity_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_tombstones WHERE entity_id = ?", (entity_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Onboarding tombstone not found.")
        return {
            "entity_id": str(row["entity_id"]),
            "entity_type": str(row["entity_type"]),
            "school": str(row["school"]),
            "deleted_at": str(row["deleted_at"]),
            "payload": json.loads(str(row["payload_json"])),
        }

    def data_revision(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM onboarding_meta WHERE key = 'data_revision'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def has_sent_reminder_batch(self, *, school: str, local_day: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM onboarding_reminder_runs
                WHERE school = ? COLLATE NOCASE AND local_day = ? AND state = 'sent'
                LIMIT 1
                """,
                (school, local_day),
            ).fetchone()
        return row is not None

    def record_reminder_message_run(
        self,
        *,
        run_id: str,
        school: str,
        local_day: str,
        role: str,
        state: str,
        task_count: int,
        error_category: str,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_reminder_runs (
                    run_id, school, local_day, role, state, task_count,
                    error_category, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, school, local_day, role, state, task_count, error_category, created_at),
            )

    def list_reminder_runs(self, *, school: str = "", limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            if str(school or "").strip():
                rows = connection.execute(
                    """
                    SELECT run_id, school, local_day, role, state, task_count,
                           error_category, created_at
                    FROM onboarding_reminder_runs
                    WHERE school = ? COLLATE NOCASE
                    ORDER BY id DESC LIMIT ?
                    """,
                    (str(school).strip(), safe_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT run_id, school, local_day, role, state, task_count,
                           error_category, created_at
                    FROM onboarding_reminder_runs
                    ORDER BY id DESC LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def has_scheduler_run(self, *, local_day: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM onboarding_scheduler_runs WHERE local_day = ?",
                (str(local_day).strip(),),
            ).fetchone()
        return row is not None

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
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_scheduler_runs (
                    local_day, state, sent_count, failed_count, skipped_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (local_day, state, sent_count, failed_count, skipped_count, created_at),
            )

    def scheduler_health(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT local_day, state, sent_count, failed_count, skipped_count, created_at
                FROM onboarding_scheduler_runs ORDER BY local_day DESC LIMIT 1
                """
            ).fetchone()
        return {} if row is None else dict(row)

    def insert_filled_artifact(self, artifact: FilledArtifact) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_filled_artifacts (
                    id, employee_id, submission_id, package_version_id, school,
                    kind, suffix, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.employee_id,
                    artifact.submission_id,
                    artifact.package_version_id,
                    artifact.school,
                    artifact.kind,
                    artifact.suffix,
                    artifact.sha256,
                    artifact.created_at,
                ),
            )

    def get_filled_artifact(self, artifact_id: str) -> FilledArtifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_filled_artifacts WHERE id = ?",
                (str(artifact_id).strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Filled onboarding artifact not found.")
        return FilledArtifact(
            id=str(row["id"]),
            employee_id=str(row["employee_id"]),
            submission_id=str(row["submission_id"]),
            package_version_id=str(row["package_version_id"]),
            school=str(row["school"]),
            kind=str(row["kind"]),
            suffix=str(row["suffix"]),
            sha256=str(row["sha256"]),
            created_at=str(row["created_at"]),
        )

    def list_filled_artifacts(self, *, submission_id: str) -> list[FilledArtifact]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM onboarding_filled_artifacts WHERE submission_id = ? ORDER BY id",
                (str(submission_id).strip(),),
            ).fetchall()
        return [self.get_filled_artifact(str(row["id"])) for row in rows]

    def list_employee_filled_artifacts(self, employee_id: str) -> list[FilledArtifact]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM onboarding_filled_artifacts
                WHERE employee_id = ? ORDER BY created_at DESC, id""",
                (employee_id,),
            ).fetchall()
        return [self.get_filled_artifact(str(row["id"])) for row in rows]

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        *,
        entity_id: str,
        action: str,
        actor: str,
        school: str,
        version: int,
        details: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO onboarding_audit_events (
                entity_id, entity_type, action, actor, school, entity_version,
                details_json, created_at
            ) VALUES (?, 'employee', ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                action,
                actor,
                school,
                version,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )

    def insert_task(self, task: OnboardingTask, *, actor: str, created_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_tasks (
                    id, employee_id, school, title, owner_role, watcher_roles_json,
                    due_date, critical, status, version, parent_task_id, required,
                    created_at, updated_at, template_key, template_version, notes, package_version_id, template_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.employee_id,
                    task.school,
                    task.title,
                    task.owner_role,
                    json.dumps(task.watcher_roles),
                    task.due_date,
                    int(task.critical),
                    task.status,
                    task.version,
                    task.parent_task_id,
                    int(task.required),
                    created_at,
                    created_at,
                    task.template_key,
                    task.template_version,
                    self._encrypt_text(task.school, task.notes, f"task.notes:{task.id}"),
                    task.package_version_id,
                    task.template_id,
                ),
            )
            connection.executemany(
                "INSERT INTO onboarding_task_dependencies (task_id, dependency_task_id) VALUES (?, ?)",
                [(task.id, dependency_id) for dependency_id in task.dependency_ids],
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'task', 'task.created', ?, ?, ?, '{}', ?)
                """,
                (task.id, actor, task.school, task.version, created_at),
            )

    def list_owner_roles(self, *, school: str) -> list[OwnerRoleConfig]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM onboarding_owner_roles
                WHERE school IN ('*', ?)
                ORDER BY role COLLATE NOCASE, CASE WHEN school = ? THEN 0 ELSE 1 END
                """,
                (school, school),
            ).fetchall()
        selected: dict[str, OwnerRoleConfig] = {}
        for row in rows:
            key = str(row["role"]).casefold()
            selected.setdefault(
                key,
                OwnerRoleConfig(
                    school=str(row["school"]),
                    role=str(row["role"]),
                    email=self._decrypt_text(
                        str(row["school"]),
                        row["email_encrypted"],
                        f"owner-role.email:{row['school']}:{row['role']}",
                    ),
                    active=bool(row["active"]),
                    version=int(row["version"]),
                ),
            )
        return sorted((item for item in selected.values() if item.active), key=lambda item: item.role.casefold())

    def upsert_owner_role(self, config: OwnerRoleConfig) -> OwnerRoleConfig:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_owner_roles (school, role, email_encrypted, active, version)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(school, role) DO UPDATE SET
                    email_encrypted = excluded.email_encrypted,
                    active = excluded.active,
                    version = excluded.version
                """,
                (
                    config.school,
                    config.role,
                    self._encrypt_text(
                        config.school,
                        config.email,
                        f"owner-role.email:{config.school}:{config.role}",
                    ),
                    int(config.active),
                    config.version,
                ),
            )
        return config

    def get_owner_role(self, *, school: str, role: str) -> OwnerRoleConfig | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_owner_roles WHERE school = ? AND role = ? COLLATE NOCASE",
                (school, role),
            ).fetchone()
        if row is None:
            return None
        return OwnerRoleConfig(
            school=str(row["school"]),
            role=str(row["role"]),
            email=self._decrypt_text(
                str(row["school"]),
                row["email_encrypted"],
                f"owner-role.email:{row['school']}:{row['role']}",
            ),
            active=bool(row["active"]),
            version=int(row["version"]),
        )

    def get_task(self, task_id: str) -> OnboardingTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_tasks WHERE id = ? AND deleted_at = ''",
                (str(task_id or "").strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Onboarding task not found.")
            dependencies = connection.execute(
                "SELECT dependency_task_id FROM onboarding_task_dependencies WHERE task_id = ? ORDER BY dependency_task_id",
                (task_id,),
            ).fetchall()
        return self._task_from_row(row, tuple(str(item[0]) for item in dependencies))

    def list_tasks(self, *, school: str = "") -> list[OnboardingTask]:
        sql = "SELECT id FROM onboarding_tasks WHERE deleted_at = ''"
        parameters: tuple[Any, ...] = ()
        if school:
            sql += " AND school = ? COLLATE NOCASE"
            parameters = (school,)
        sql += " ORDER BY due_date, title COLLATE NOCASE, id"
        with self._connect() as connection:
            task_ids = [str(row[0]) for row in connection.execute(sql, parameters).fetchall()]
        return [self.get_task(task_id) for task_id in task_ids]

    def set_task_status(self, task_id: str, *, status: str, actor: str, updated_at: str) -> OnboardingTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT school, version FROM onboarding_tasks WHERE id = ? AND deleted_at = ''",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Onboarding task not found.")
            version = int(row["version"]) + 1
            connection.execute(
                "UPDATE onboarding_tasks SET status = ?, version = ?, updated_at = ? WHERE id = ?",
                (status, version, updated_at, task_id),
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'task', ?, ?, ?, ?, '{}', ?)
                """,
                (task_id, f"task.{status}", actor, str(row["school"]), version, updated_at),
            )
        return self.get_task(task_id)

    def replace_task(
        self,
        task: OnboardingTask,
        *,
        expected_version: int,
        actor: str,
        updated_at: str,
        changed_fields: tuple[str, ...],
    ) -> None:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE onboarding_tasks
                SET school = ?, title = ?, owner_role = ?, watcher_roles_json = ?,
                    due_date = ?, critical = ?, status = ?, version = ?,
                    parent_task_id = ?, required = ?, template_key = ?, template_version = ?,
                    notes = ?, package_version_id = ?, template_id = ?, updated_at = ?
                WHERE id = ? AND version = ? AND deleted_at = ''
                """,
                (
                    task.school,
                    task.title,
                    task.owner_role,
                    json.dumps(task.watcher_roles),
                    task.due_date,
                    int(task.critical),
                    task.status,
                    task.version,
                    task.parent_task_id,
                    int(task.required),
                    task.template_key,
                    task.template_version,
                    self._encrypt_text(task.school, task.notes, f"task.notes:{task.id}"),
                    task.package_version_id,
                    task.template_id,
                    updated_at,
                    task.id,
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Onboarding task changed since it was opened.")
            connection.execute("DELETE FROM onboarding_task_dependencies WHERE task_id = ?", (task.id,))
            connection.executemany(
                "INSERT INTO onboarding_task_dependencies (task_id, dependency_task_id) VALUES (?, ?)",
                [(task.id, dependency_id) for dependency_id in task.dependency_ids],
            )
            connection.execute(
                """
                INSERT INTO onboarding_audit_events (
                    entity_id, entity_type, action, actor, school, entity_version,
                    details_json, created_at
                ) VALUES (?, 'task', 'task.synced', ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    actor,
                    task.school,
                    task.version,
                    json.dumps({"changed_fields": sorted(set(changed_fields))}),
                    updated_at,
                ),
            )

    def insert_task_comment(self, comment: TaskComment) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_task_comments (
                    id, task_id, employee_id, school, author, version, redacted,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    comment.id,
                    comment.task_id,
                    comment.employee_id,
                    comment.school,
                    comment.author,
                    comment.version,
                    int(comment.redacted),
                    comment.created_at,
                    comment.updated_at,
                ),
            )
            self._insert_comment_revision(connection, comment, editor=comment.author, reason="")

    def get_task_comment(self, comment_id: str) -> TaskComment:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_task_comments WHERE id = ? AND deleted_at = ''",
                (comment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Task comment not found.")
            revision = connection.execute(
                """
                SELECT * FROM onboarding_task_comment_revisions
                WHERE comment_id = ? AND version = ?
                """,
                (comment_id, int(row["version"])),
            ).fetchone()
        if revision is None:
            raise ValueError("Task comment revision is missing.")
        school = str(row["school"])
        body = self._decrypt_text(
            school,
            revision["body_encrypted"],
            f"comment.body:{comment_id}:v{int(row['version'])}",
        )
        return TaskComment(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            employee_id=str(row["employee_id"]),
            school=school,
            author=str(row["author"]),
            body=body,
            version=int(row["version"]),
            redacted=bool(row["redacted"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_task_comments(self, task_id: str) -> list[TaskComment]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM onboarding_task_comments
                WHERE task_id = ? AND deleted_at = '' ORDER BY created_at, id""",
                (task_id,),
            ).fetchall()
        return [self.get_task_comment(str(row[0])) for row in rows]

    def revise_task_comment(
        self,
        comment_id: str,
        *,
        body: str,
        editor: str,
        reason: str,
        redacted: bool,
        updated_at: str,
    ) -> TaskComment:
        current = self.get_task_comment(comment_id)
        revised = TaskComment(
            id=current.id,
            task_id=current.task_id,
            employee_id=current.employee_id,
            school=current.school,
            author=current.author,
            body=body,
            version=current.version + 1,
            redacted=redacted,
            created_at=current.created_at,
            updated_at=updated_at,
        )
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE onboarding_task_comments
                SET version = ?, redacted = ?, updated_at = ?
                WHERE id = ? AND version = ? AND deleted_at = ''
                """,
                (revised.version, int(redacted), updated_at, current.id, current.version),
            )
            if result.rowcount != 1:
                raise ValueError("Task comment changed since it was opened.")
            self._insert_comment_revision(connection, revised, editor=editor, reason=reason)
        return self.get_task_comment(current.id)

    def list_task_comment_revisions(self, comment_id: str) -> list[TaskCommentRevision]:
        comment = self.get_task_comment(comment_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM onboarding_task_comment_revisions
                WHERE comment_id = ? ORDER BY version
                """,
                (comment.id,),
            ).fetchall()
        revisions: list[TaskCommentRevision] = []
        for row in rows:
            version = int(row["version"])
            revisions.append(
                TaskCommentRevision(
                    comment_id=comment.id,
                    version=version,
                    body=self._decrypt_text(
                        comment.school,
                        row["body_encrypted"],
                        f"comment.body:{comment.id}:v{version}",
                    ),
                    editor=str(row["editor"]),
                    reason=self._decrypt_text(
                        comment.school,
                        row["reason_encrypted"],
                        f"comment.reason:{comment.id}:v{version}",
                    ),
                    created_at=str(row["created_at"]),
                )
            )
        return revisions

    def _insert_comment_revision(
        self,
        connection: sqlite3.Connection,
        comment: TaskComment,
        *,
        editor: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO onboarding_task_comment_revisions (
                comment_id, version, body_encrypted, editor, reason_encrypted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                comment.id,
                comment.version,
                self._encrypt_text(
                    comment.school,
                    comment.body,
                    f"comment.body:{comment.id}:v{comment.version}",
                ),
                editor,
                self._encrypt_text(
                    comment.school,
                    reason,
                    f"comment.reason:{comment.id}:v{comment.version}",
                ),
                comment.updated_at,
            ),
        )

    def insert_task_attachment(self, attachment: TaskAttachment, *, content: bytes) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_task_attachments (
                    id, task_id, employee_id, school, name_encrypted, media_type,
                    sha256, size_bytes, scan_status, warning, content_encrypted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment.id,
                    attachment.task_id,
                    attachment.employee_id,
                    attachment.school,
                    self._encrypt_text(
                        attachment.school,
                        attachment.name,
                        f"attachment.name:{attachment.id}",
                    ),
                    attachment.media_type,
                    attachment.sha256,
                    attachment.size_bytes,
                    attachment.scan_status,
                    attachment.warning,
                    self.vault.encrypt(
                        attachment.school,
                        bytes(content),
                        context=f"attachment.content:{attachment.id}",
                    ),
                    attachment.created_at,
                ),
            )

    def insert_task_template_attachment(
        self, attachment: TaskTemplateAttachment, *, content: bytes
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO onboarding_task_template_attachments (
                    id, template_id, school, name_encrypted, media_type, sha256,
                    size_bytes, scan_status, warning, content_encrypted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attachment.id, attachment.template_id, attachment.school,
                    self._encrypt_text(
                        attachment.school, attachment.name,
                        f"template_attachment.name:{attachment.id}",
                    ),
                    attachment.media_type, attachment.sha256, attachment.size_bytes,
                    attachment.scan_status, attachment.warning,
                    self.vault.encrypt(
                        attachment.school, bytes(content),
                        context=f"template_attachment.content:{attachment.id}",
                    ),
                    attachment.created_at,
                ),
            )

    def get_task_template_attachment(
        self, attachment_id: str
    ) -> tuple[TaskTemplateAttachment, bytes]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_task_template_attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Task template attachment not found.")
        school = str(row["school"])
        attachment = TaskTemplateAttachment(
            id=str(row["id"]), template_id=str(row["template_id"]), school=school,
            name=self._decrypt_text(
                school, row["name_encrypted"], f"template_attachment.name:{row['id']}"
            ),
            media_type=str(row["media_type"]), sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]), scan_status=str(row["scan_status"]),
            warning=str(row["warning"]), created_at=str(row["created_at"]),
        )
        content = self.vault.decrypt(
            school, bytes(row["content_encrypted"]),
            context=f"template_attachment.content:{attachment.id}",
        )
        return attachment, content

    def list_task_template_attachments(self, template_id: str) -> list[TaskTemplateAttachment]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM onboarding_task_template_attachments
                WHERE template_id = ? ORDER BY created_at, id""",
                (template_id,),
            ).fetchall()
        return [self.get_task_template_attachment(str(row[0]))[0] for row in rows]

    def get_task_attachment(self, attachment_id: str) -> tuple[TaskAttachment, bytes]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_task_attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Task attachment not found.")
        school = str(row["school"])
        attachment = TaskAttachment(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            employee_id=str(row["employee_id"]),
            school=school,
            name=self._decrypt_text(school, row["name_encrypted"], f"attachment.name:{row['id']}"),
            media_type=str(row["media_type"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            scan_status=str(row["scan_status"]),
            warning=str(row["warning"]),
            created_at=str(row["created_at"]),
        )
        content = self.vault.decrypt(
            school,
            bytes(row["content_encrypted"]),
            context=f"attachment.content:{attachment.id}",
        )
        return attachment, content

    def list_task_attachments(self, task_id: str) -> list[TaskAttachment]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM onboarding_task_attachments
                WHERE task_id = ? ORDER BY created_at, id""",
                (task_id,),
            ).fetchall()
        return [self.get_task_attachment(str(row[0]))[0] for row in rows]

    def insert_intake_field(self, field: IntakeField) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_intake_fields (
                    id, stable_id, label, aliases_json, field_type, sensitivity,
                    validation_json, help_text, options_json, version, deprecated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    field.id,
                    field.stable_id,
                    field.label,
                    json.dumps(field.aliases),
                    field.field_type,
                    field.sensitivity,
                    field.validation_json,
                    field.help_text,
                    json.dumps(field.options),
                    field.version,
                    int(field.deprecated),
                ),
            )

    def get_intake_field(self, field_id: str) -> IntakeField:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_intake_fields WHERE id = ?",
                (field_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Intake field not found.")
        return self._intake_field_from_row(row)

    def list_intake_fields(self) -> list[IntakeField]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM onboarding_intake_fields ORDER BY label COLLATE NOCASE, stable_id"
            ).fetchall()
        return [self._intake_field_from_row(row) for row in rows]

    def deprecate_intake_field(self, field_id: str) -> IntakeField:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE onboarding_intake_fields SET deprecated = 1 WHERE id = ?",
                (field_id,),
            )
            if result.rowcount != 1:
                raise ValueError("Intake field not found.")
        return self.get_intake_field(field_id)

    def insert_pdf_mapping(self, mapping: PdfFieldMapping) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_pdf_mappings (
                    id, document_key, page_number, x, y, width, height, field_id,
                    required, font_name, font_size, alignment, multiline, formatting_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mapping.id,
                    mapping.document_key,
                    mapping.page_number,
                    *mapping.rect,
                    mapping.field_id,
                    int(mapping.required),
                    mapping.font_name,
                    mapping.font_size,
                    mapping.alignment,
                    int(mapping.multiline),
                    mapping.formatting_json,
                ),
            )

    def list_pdf_mappings(self, document_key: str = "") -> list[PdfFieldMapping]:
        sql = "SELECT * FROM onboarding_pdf_mappings"
        parameters: tuple[Any, ...] = ()
        if document_key:
            sql += " WHERE document_key = ?"
            parameters = (document_key,)
        sql += " ORDER BY document_key, page_number, id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            PdfFieldMapping(
                id=str(row["id"]),
                document_key=str(row["document_key"]),
                page_number=int(row["page_number"]),
                rect=(float(row["x"]), float(row["y"]), float(row["width"]), float(row["height"])),
                field_id=str(row["field_id"]),
                required=bool(row["required"]),
                font_name=str(row["font_name"]),
                font_size=float(row["font_size"]),
                alignment=str(row["alignment"]),
                multiline=bool(row["multiline"]),
                formatting_json=str(row["formatting_json"]),
            )
            for row in rows
        ]

    def insert_intake_submission(self, submission: IntakeSubmission) -> None:
        encrypted = self.vault.encrypt(
            submission.school,
            json.dumps(submission.values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=f"intake.values:{submission.id}",
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_intake_submissions (
                    id, employee_id, application_id, school, schema_version,
                    values_encrypted, revision, status, created_at, corrects_submission_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.id,
                    submission.employee_id,
                    submission.application_id,
                    submission.school,
                    submission.schema_version,
                    encrypted,
                    submission.revision,
                    submission.status,
                    submission.created_at,
                    submission.corrects_submission_id,
                ),
            )

    def get_intake_submission(self, submission_id: str) -> IntakeSubmission | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_intake_submissions WHERE id = ?",
                (submission_id,),
            ).fetchone()
        if row is None:
            return None
        payload = self.vault.decrypt(
            str(row["school"]),
            bytes(row["values_encrypted"]),
            context=f"intake.values:{row['id']}",
        )
        values = json.loads(payload.decode("utf-8"))
        if not isinstance(values, dict):
            raise VaultIntegrityError("Encrypted intake values must decode to an object.")
        return IntakeSubmission(
            id=str(row["id"]),
            employee_id=str(row["employee_id"]),
            application_id=str(row["application_id"]),
            school=str(row["school"]),
            schema_version=int(row["schema_version"]),
            values=values,
            revision=int(row["revision"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            corrects_submission_id=str(row["corrects_submission_id"]),
        )

    def update_intake_submission_status(self, submission_id: str, *, status: str) -> IntakeSubmission:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE onboarding_intake_submissions SET status = ? WHERE id = ?",
                (str(status).strip(), str(submission_id).strip()),
            )
        if cursor.rowcount != 1:
            raise ValueError("Intake submission not found.")
        submission = self.get_intake_submission(submission_id)
        if submission is None:
            raise ValueError("Intake submission not found.")
        return submission

    def next_package_version(self, package_key: str, *, school: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(version) FROM onboarding_package_versions WHERE package_key = ? AND school = ? COLLATE NOCASE",
                (package_key, school),
            ).fetchone()
        return int(row[0] or 0) + 1

    def insert_document_package(
        self,
        package: DocumentPackageVersion,
        *,
        document_contents: list[bytes],
    ) -> None:
        if len(package.documents) != len(document_contents):
            raise ValueError("Package document metadata/content count mismatch.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding_package_versions (
                    id, package_key, school, title, version, status, created_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package.id,
                    package.package_key,
                    package.school,
                    package.title,
                    package.version,
                    package.status,
                    package.created_at,
                    package.published_at,
                ),
            )
            for document, content in zip(package.documents, document_contents, strict=True):
                encrypted = self.vault.encrypt(
                    package.school,
                    content,
                    context=f"package.document:{package.id}:{document.position}",
                )
                connection.execute(
                    """
                    INSERT INTO onboarding_package_documents (
                        package_version_id, position, name, sha256, size_bytes, content_encrypted
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package.id,
                        document.position,
                        document.name,
                        document.sha256,
                        document.size_bytes,
                        encrypted,
                    ),
                )

    def get_document_package(self, package_version_id: str) -> DocumentPackageVersion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM onboarding_package_versions WHERE id = ?",
                (package_version_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Document package version not found.")
            documents = connection.execute(
                """
                SELECT position, name, sha256, size_bytes
                FROM onboarding_package_documents
                WHERE package_version_id = ? ORDER BY position
                """,
                (package_version_id,),
            ).fetchall()
        return self._document_package_from_row(row, documents)

    def get_document_package_contents(self, package_version_id: str) -> tuple[bytes, ...]:
        package = self.get_document_package(package_version_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT position, content_encrypted
                FROM onboarding_package_documents
                WHERE package_version_id = ? ORDER BY position
                """,
                (package.id,),
            ).fetchall()
        if [int(row["position"]) for row in rows] != [document.position for document in package.documents]:
            raise ValueError("Document package content order is invalid.")
        return tuple(
            self.vault.decrypt(
                package.school,
                row["content_encrypted"],
                context=f"package.document:{package.id}:{int(row['position'])}",
            )
            for row in rows
        )

    def publish_document_package(self, package_version_id: str, *, published_at: str) -> DocumentPackageVersion:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE onboarding_package_versions
                SET status = 'published', published_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (published_at, package_version_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a draft document package may be published.")
        return self.get_document_package(package_version_id)

    def latest_published_document_package(self, package_key: str, *, school: str) -> DocumentPackageVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM onboarding_package_versions
                WHERE package_key = ? AND school = ? COLLATE NOCASE AND status = 'published'
                ORDER BY version DESC LIMIT 1
                """,
                (package_key, school),
            ).fetchone()
        return None if row is None else self.get_document_package(str(row[0]))

    def list_document_package_versions(self) -> list[DocumentPackageVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM onboarding_package_versions
                ORDER BY school COLLATE NOCASE, package_key COLLATE NOCASE, version DESC"""
            ).fetchall()
        return [self.get_document_package(str(row[0])) for row in rows]

    def insert_task_template(self, template: TaskTemplateVersion) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO onboarding_task_template_versions
                (id, template_key, school, title, owner_role, watcher_roles_json,
                 due_offset_days, critical, version, status, created_at, published_at, package_key,
                 content, base_template_id, override_fields_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (template.id, template.template_key, template.school, template.title,
                 template.owner_role, json.dumps(template.watcher_roles), template.due_offset_days,
                 int(template.critical), template.version, template.status,
                 template.created_at, template.published_at, template.package_key, template.content,
                 template.base_template_id, json.dumps(template.override_fields)),
            )

    def next_task_template_version(self, template_key: str, *, school: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(version) FROM onboarding_task_template_versions WHERE template_key = ? AND school = ?",
                (template_key, school),
            ).fetchone()
        return int(row[0] or 0) + 1

    def publish_task_template(self, template_id: str, *, published_at: str) -> TaskTemplateVersion:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE onboarding_task_template_versions SET status = 'published', published_at = ? WHERE id = ? AND status = 'draft'",
                (published_at, template_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a draft task template may be published.")
        return self.get_task_template(template_id)

    def deprecate_task_template(self, template_id: str) -> TaskTemplateVersion:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE onboarding_task_template_versions SET status = 'deprecated' WHERE id = ? AND status = 'published'",
                (template_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a published task template may be deprecated.")
        return self.get_task_template(template_id)

    def get_task_template(self, template_id: str) -> TaskTemplateVersion:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM onboarding_task_template_versions WHERE id = ?", (template_id,)).fetchone()
        if row is None:
            raise ValueError("Task template version not found.")
        return self._task_template_from_row(row)

    def applicable_task_templates(self, *, school: str) -> list[TaskTemplateVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM onboarding_task_template_versions
                WHERE status = 'published' AND school IN ('*', ?)
                ORDER BY template_key, CASE WHEN school = ? THEN 0 ELSE 1 END, version DESC""",
                (school, school),
            ).fetchall()
        selected: dict[str, TaskTemplateVersion] = {}
        for row in rows:
            template = self._task_template_from_row(row)
            selected.setdefault(template.template_key, template)
        resolved: list[TaskTemplateVersion] = []
        for template in selected.values():
            if template.base_template_id and template.override_fields:
                base = self.get_task_template(template.base_template_id)
                values = {
                    name: getattr(template, name) if name in template.override_fields else getattr(base, name)
                    for name in ("title", "owner_role", "watcher_roles", "due_offset_days", "critical", "package_key", "content")
                }
                template = TaskTemplateVersion(
                    id=template.id, template_key=template.template_key, school=template.school,
                    version=template.version, status=template.status, created_at=template.created_at,
                    published_at=template.published_at, base_template_id=template.base_template_id,
                    override_fields=template.override_fields, **values,
                )
            resolved.append(template)
        return resolved

    def list_task_template_versions(self) -> list[TaskTemplateVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM onboarding_task_template_versions
                ORDER BY school COLLATE NOCASE, template_key COLLATE NOCASE, version DESC"""
            ).fetchall()
        return [self._task_template_from_row(row) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS onboarding_employees (
                    id TEXT PRIMARY KEY,
                    legal_name TEXT NOT NULL,
                    preferred_name TEXT NOT NULL DEFAULT '',
                    school TEXT NOT NULL,
                    role TEXT NOT NULL,
                    acceptance_date TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT '',
                    source_application_id TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    hiring_director_id TEXT NOT NULL DEFAULT '',
                    hiring_director_name TEXT NOT NULL DEFAULT ''
                    , last_working_day TEXT NOT NULL DEFAULT ''
                    , departure_category TEXT NOT NULL DEFAULT ''
                    , departure_notes TEXT NOT NULL DEFAULT ''
                    , departure_director_id TEXT NOT NULL DEFAULT ''
                    , departure_director_name TEXT NOT NULL DEFAULT ''
                    , archived_at TEXT NOT NULL DEFAULT ''
                    , address_line1 BLOB NOT NULL DEFAULT X''
                    , address_line2 BLOB NOT NULL DEFAULT X''
                    , city BLOB NOT NULL DEFAULT X''
                    , state BLOB NOT NULL DEFAULT X''
                    , postal_code BLOB NOT NULL DEFAULT X''
                    , personal_email BLOB NOT NULL DEFAULT X''
                    , work_email BLOB NOT NULL DEFAULT X''
                    , notes BLOB NOT NULL DEFAULT X''
                    , source_history_id TEXT NOT NULL DEFAULT ''
                    , dob BLOB NOT NULL DEFAULT X''
                    , ssn BLOB NOT NULL DEFAULT X''
                );
                CREATE INDEX IF NOT EXISTS idx_onboarding_employees_school
                    ON onboarding_employees (school, status);
                CREATE TABLE IF NOT EXISTS onboarding_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    school TEXT NOT NULL,
                    entity_version INTEGER NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_tombstones (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    school TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO onboarding_meta (key, value) VALUES ('data_revision', 0);
                CREATE TABLE IF NOT EXISTS onboarding_reminder_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    school TEXT NOT NULL,
                    local_day TEXT NOT NULL,
                    role TEXT NOT NULL,
                    state TEXT NOT NULL,
                    task_count INTEGER NOT NULL,
                    error_category TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_scheduler_runs (
                    local_day TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    sent_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    skipped_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_owner_roles (
                    school TEXT NOT NULL,
                    role TEXT NOT NULL COLLATE NOCASE,
                    email_encrypted BLOB NOT NULL DEFAULT X'',
                    active INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (school, role)
                );
                INSERT OR IGNORE INTO onboarding_owner_roles (school, role) VALUES
                    ('*', 'Office Manager'),
                    ('*', 'Payroll'),
                    ('*', 'Benefits'),
                    ('*', 'Director'),
                    ('*', 'IT');
                CREATE TABLE IF NOT EXISTS onboarding_tasks (
                    id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL REFERENCES onboarding_employees(id),
                    school TEXT NOT NULL,
                    title TEXT NOT NULL,
                    owner_role TEXT NOT NULL,
                    watcher_roles_json TEXT NOT NULL DEFAULT '[]',
                    due_date TEXT NOT NULL,
                    critical INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    parent_task_id TEXT NOT NULL DEFAULT '',
                    required INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_onboarding_tasks_queue
                    ON onboarding_tasks (school, status, due_date);
                CREATE TABLE IF NOT EXISTS onboarding_task_dependencies (
                    task_id TEXT NOT NULL REFERENCES onboarding_tasks(id) ON DELETE CASCADE,
                    dependency_task_id TEXT NOT NULL REFERENCES onboarding_tasks(id),
                    PRIMARY KEY (task_id, dependency_task_id),
                    CHECK (task_id != dependency_task_id)
                );
                CREATE TABLE IF NOT EXISTS onboarding_task_comments (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES onboarding_tasks(id),
                    employee_id TEXT NOT NULL REFERENCES onboarding_employees(id),
                    school TEXT NOT NULL,
                    author TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    redacted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS onboarding_task_comment_revisions (
                    comment_id TEXT NOT NULL REFERENCES onboarding_task_comments(id),
                    version INTEGER NOT NULL,
                    body_encrypted BLOB NOT NULL,
                    editor TEXT NOT NULL,
                    reason_encrypted BLOB NOT NULL DEFAULT X'',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (comment_id, version)
                );
                CREATE TABLE IF NOT EXISTS onboarding_task_attachments (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES onboarding_tasks(id),
                    employee_id TEXT NOT NULL REFERENCES onboarding_employees(id),
                    school TEXT NOT NULL,
                    name_encrypted BLOB NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    scan_status TEXT NOT NULL,
                    warning TEXT NOT NULL DEFAULT '',
                    content_encrypted BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_intake_fields (
                    id TEXT PRIMARY KEY,
                    stable_id TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    help_text TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    deprecated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS onboarding_pdf_mappings (
                    id TEXT PRIMARY KEY,
                    document_key TEXT NOT NULL,
                    page_number INTEGER NOT NULL CHECK (page_number >= 1),
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    width REAL NOT NULL CHECK (width > 0),
                    height REAL NOT NULL CHECK (height > 0),
                    field_id TEXT NOT NULL REFERENCES onboarding_intake_fields(id),
                    required INTEGER NOT NULL,
                    font_name TEXT NOT NULL,
                    font_size REAL NOT NULL,
                    alignment TEXT NOT NULL,
                    multiline INTEGER NOT NULL,
                    formatting_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_intake_submissions (
                    id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL REFERENCES onboarding_employees(id),
                    application_id TEXT NOT NULL,
                    school TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
                    values_encrypted BLOB NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    corrects_submission_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS onboarding_package_versions (
                    id TEXT PRIMARY KEY,
                    package_key TEXT NOT NULL,
                    school TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'deprecated')),
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT '',
                    UNIQUE (package_key, school, version)
                );
                CREATE TABLE IF NOT EXISTS onboarding_package_documents (
                    package_version_id TEXT NOT NULL REFERENCES onboarding_package_versions(id),
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_encrypted BLOB NOT NULL,
                    PRIMARY KEY (package_version_id, position)
                );
                CREATE TABLE IF NOT EXISTS onboarding_filled_artifacts (
                    id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL REFERENCES onboarding_employees(id),
                    submission_id TEXT NOT NULL REFERENCES onboarding_intake_submissions(id),
                    package_version_id TEXT NOT NULL REFERENCES onboarding_package_versions(id),
                    school TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    suffix TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS onboarding_task_template_versions (
                    id TEXT PRIMARY KEY, template_key TEXT NOT NULL, school TEXT NOT NULL,
                    title TEXT NOT NULL, owner_role TEXT NOT NULL, watcher_roles_json TEXT NOT NULL,
                    due_offset_days INTEGER NOT NULL, critical INTEGER NOT NULL,
                    version INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','published','deprecated')),
                    created_at TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT '', package_key TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '', base_template_id TEXT NOT NULL DEFAULT '',
                    override_fields_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(template_key, school, version)
                );
                CREATE TABLE IF NOT EXISTS onboarding_task_template_attachments (
                    id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL REFERENCES onboarding_task_template_versions(id),
                    school TEXT NOT NULL,
                    name_encrypted BLOB NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    scan_status TEXT NOT NULL,
                    warning TEXT NOT NULL DEFAULT '',
                    content_encrypted BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS onboarding_employee_revision_insert
                AFTER INSERT ON onboarding_employees BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_employee_revision_update
                AFTER UPDATE ON onboarding_employees BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_employee_revision_delete
                AFTER DELETE ON onboarding_employees BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_task_revision_insert
                AFTER INSERT ON onboarding_tasks BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_task_revision_update
                AFTER UPDATE ON onboarding_tasks BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_task_revision_delete
                AFTER DELETE ON onboarding_tasks BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_owner_role_revision_insert
                AFTER INSERT ON onboarding_owner_roles BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                CREATE TRIGGER IF NOT EXISTS onboarding_owner_role_revision_update
                AFTER UPDATE ON onboarding_owner_roles BEGIN
                    UPDATE onboarding_meta SET value = value + 1 WHERE key = 'data_revision';
                END;
                """
            )
            self._ensure_employee_columns(connection)
            self._ensure_task_columns(connection)
            self._ensure_task_template_columns(connection)
            self._ensure_intake_submission_columns(connection)
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_onboarding_employee_application
                ON onboarding_employees (source_application_id)
                WHERE source_application_id != ''
                """
            )

    @staticmethod
    def _ensure_employee_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(onboarding_employees)").fetchall()
        }
        additions = {
            "source_application_id": "TEXT NOT NULL DEFAULT ''",
            "email": "BLOB NOT NULL DEFAULT X''",
            "phone": "BLOB NOT NULL DEFAULT X''",
            "hiring_director_id": "TEXT NOT NULL DEFAULT ''",
            "hiring_director_name": "TEXT NOT NULL DEFAULT ''",
            "last_working_day": "TEXT NOT NULL DEFAULT ''",
            "departure_category": "TEXT NOT NULL DEFAULT ''",
            "departure_notes": "TEXT NOT NULL DEFAULT ''",
            "departure_director_id": "TEXT NOT NULL DEFAULT ''",
            "departure_director_name": "TEXT NOT NULL DEFAULT ''",
            "archived_at": "TEXT NOT NULL DEFAULT ''",
            "address_line1": "BLOB NOT NULL DEFAULT X''",
            "address_line2": "BLOB NOT NULL DEFAULT X''",
            "city": "BLOB NOT NULL DEFAULT X''",
            "state": "BLOB NOT NULL DEFAULT X''",
            "postal_code": "BLOB NOT NULL DEFAULT X''",
            "personal_email": "BLOB NOT NULL DEFAULT X''",
            "work_email": "BLOB NOT NULL DEFAULT X''",
            "notes": "BLOB NOT NULL DEFAULT X''",
            "source_history_id": "TEXT NOT NULL DEFAULT ''",
            "dob": "BLOB NOT NULL DEFAULT X''",
            "ssn": "BLOB NOT NULL DEFAULT X''",
        }
        for name, definition in additions.items():
            if name not in existing:
                OnboardingStore._add_column_if_missing(
                    connection, "onboarding_employees", name, definition
                )

    @staticmethod
    def _ensure_task_columns(connection: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(onboarding_tasks)")}
        if "template_key" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_tasks", "template_key", "TEXT NOT NULL DEFAULT ''"
            )
        if "template_version" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_tasks", "template_version", "INTEGER NOT NULL DEFAULT 0"
            )
        if "notes" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_tasks", "notes", "BLOB NOT NULL DEFAULT X''"
            )
        if "package_version_id" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_tasks", "package_version_id", "TEXT NOT NULL DEFAULT ''"
            )
        if "template_id" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_tasks", "template_id", "TEXT NOT NULL DEFAULT ''"
            )

    @staticmethod
    def _ensure_task_template_columns(connection: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(onboarding_task_template_versions)")}
        if "package_key" not in existing:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_task_template_versions", "package_key", "TEXT NOT NULL DEFAULT ''"
            )
        if "content" not in existing:
            OnboardingStore._add_column_if_missing(connection, "onboarding_task_template_versions", "content", "TEXT NOT NULL DEFAULT ''")
        if "base_template_id" not in existing:
            OnboardingStore._add_column_if_missing(connection, "onboarding_task_template_versions", "base_template_id", "TEXT NOT NULL DEFAULT ''")
        if "override_fields_json" not in existing:
            OnboardingStore._add_column_if_missing(connection, "onboarding_task_template_versions", "override_fields_json", "TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def _ensure_intake_submission_columns(connection: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(onboarding_intake_submissions)")}
        if "corrects_submission_id" not in existing:
            OnboardingStore._add_column_if_missing(
                connection,
                "onboarding_intake_submissions",
                "corrects_submission_id",
                "TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        try:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _employee_from_row(self, row: sqlite3.Row) -> OnboardingEmployee:
        employee_id = str(row["id"])
        school = str(row["school"])
        return OnboardingEmployee(
            id=employee_id,
            legal_name=str(row["legal_name"]),
            preferred_name=str(row["preferred_name"]),
            school=school,
            role=str(row["role"]),
            acceptance_date=str(row["acceptance_date"]),
            start_date=str(row["start_date"]),
            status=str(row["status"]),
            version=int(row["version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            source_application_id=str(row["source_application_id"]),
            email=self._decrypt_text(school, row["email"], f"employee.email:{employee_id}"),
            phone=self._decrypt_text(school, row["phone"], f"employee.phone:{employee_id}"),
            hiring_director_id=str(row["hiring_director_id"]),
            hiring_director_name=str(row["hiring_director_name"]),
            last_working_day=str(row["last_working_day"]),
            departure_category=str(row["departure_category"]),
            departure_notes=str(row["departure_notes"]),
            departure_director_id=str(row["departure_director_id"]),
            departure_director_name=str(row["departure_director_name"]),
            archived_at=str(row["archived_at"]),
            address_line1=self._decrypt_text(school, row["address_line1"], f"employee.address_line1:{employee_id}"),
            address_line2=self._decrypt_text(school, row["address_line2"], f"employee.address_line2:{employee_id}"),
            city=self._decrypt_text(school, row["city"], f"employee.city:{employee_id}"),
            state=self._decrypt_text(school, row["state"], f"employee.state:{employee_id}"),
            postal_code=self._decrypt_text(school, row["postal_code"], f"employee.postal_code:{employee_id}"),
            personal_email=self._decrypt_text(school, row["personal_email"], f"employee.personal_email:{employee_id}"),
            work_email=self._decrypt_text(school, row["work_email"], f"employee.work_email:{employee_id}"),
            notes=self._decrypt_text(school, row["notes"], f"employee.notes:{employee_id}"),
            source_history_id=str(row["source_history_id"]),
            dob=self._decrypt_text(school, row["dob"], f"employee.dob:{employee_id}"),
            ssn=self._decrypt_text(school, row["ssn"], f"employee.ssn:{employee_id}"),
        )

    def _encrypt_text(self, school: str, value: str, context: str) -> bytes:
        clean = str(value or "")
        if not clean:
            return b""
        return self.vault.encrypt(school, clean.encode("utf-8"), context=context)

    def _decrypt_text(self, school: str, value: Any, context: str) -> str:
        if value in {None, "", b""}:
            return ""
        if isinstance(value, str):
            raise VaultIntegrityError("Unencrypted sensitive onboarding value was rejected.")
        return self.vault.decrypt(school, bytes(value), context=context).decode("utf-8")

    def _task_from_row(self, row: sqlite3.Row, dependency_ids: tuple[str, ...]) -> OnboardingTask:
        watchers = json.loads(str(row["watcher_roles_json"]))
        return OnboardingTask(
            id=str(row["id"]),
            employee_id=str(row["employee_id"]),
            school=str(row["school"]),
            title=str(row["title"]),
            owner_role=str(row["owner_role"]),
            watcher_roles=tuple(str(value) for value in watchers),
            due_date=str(row["due_date"]),
            critical=bool(row["critical"]),
            status=str(row["status"]),
            version=int(row["version"]),
            dependency_ids=dependency_ids,
            parent_task_id=str(row["parent_task_id"]),
            required=bool(row["required"]),
            template_key=str(row["template_key"]),
            template_version=int(row["template_version"]),
            notes=self._decrypt_text(str(row["school"]), row["notes"], f"task.notes:{row['id']}"),
            package_version_id=str(row["package_version_id"]),
            template_id=str(row["template_id"]),
        )

    @staticmethod
    def _task_template_from_row(row: sqlite3.Row) -> TaskTemplateVersion:
        return TaskTemplateVersion(
            id=str(row["id"]), template_key=str(row["template_key"]), school=str(row["school"]),
            title=str(row["title"]), owner_role=str(row["owner_role"]),
            watcher_roles=tuple(json.loads(str(row["watcher_roles_json"]))),
            due_offset_days=int(row["due_offset_days"]), critical=bool(row["critical"]),
            version=int(row["version"]), status=str(row["status"]),
            created_at=str(row["created_at"]), published_at=str(row["published_at"]),
            package_key=str(row["package_key"]),
            content=str(row["content"]), base_template_id=str(row["base_template_id"]),
            override_fields=tuple(str(value) for value in json.loads(str(row["override_fields_json"]))),
        )

    @staticmethod
    def _intake_field_from_row(row: sqlite3.Row) -> IntakeField:
        return IntakeField(
            id=str(row["id"]),
            stable_id=str(row["stable_id"]),
            label=str(row["label"]),
            aliases=tuple(str(value) for value in json.loads(str(row["aliases_json"]))),
            field_type=str(row["field_type"]),
            sensitivity=str(row["sensitivity"]),
            validation_json=str(row["validation_json"]),
            help_text=str(row["help_text"]),
            options=tuple(str(value) for value in json.loads(str(row["options_json"]))),
            version=int(row["version"]),
            deprecated=bool(row["deprecated"]),
        )

    @staticmethod
    def _document_package_from_row(
        row: sqlite3.Row,
        documents: list[sqlite3.Row],
    ) -> DocumentPackageVersion:
        return DocumentPackageVersion(
            id=str(row["id"]),
            package_key=str(row["package_key"]),
            school=str(row["school"]),
            title=str(row["title"]),
            version=int(row["version"]),
            status=str(row["status"]),
            documents=tuple(
                PackageDocument(
                    position=int(document["position"]),
                    name=str(document["name"]),
                    sha256=str(document["sha256"]),
                    size_bytes=int(document["size_bytes"]),
                )
                for document in documents
            ),
            created_at=str(row["created_at"]),
            published_at=str(row["published_at"]),
        )
