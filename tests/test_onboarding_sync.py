from pathlib import Path
from datetime import datetime, timezone

from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import FilledArtifact, OnboardingStore
from onboarding_sync import OnboardingChangeStage, OnboardingSyncCoordinator
from onboarding_vault import EncryptedArtifactVault, OnboardingVault


def _service(path: Path, stage_path: Path, vault: OnboardingVault, *, role: str, replica: str, artifact_root: Path | None = None):
    store = OnboardingStore(path, vault=vault)
    access = OnboardingAccess(
        role=role,
        actor=replica,
        school_scope="Palmdale" if role == "director" else "",
    )
    sync = OnboardingSyncCoordinator(
        store=store,
        stage=OnboardingChangeStage(stage_path),
        vault=vault,
        replica=replica,
        school_scope="Palmdale" if role == "director" else "",
        artifact_root=artifact_root,
    )
    artifacts = None if artifact_root is None else EncryptedArtifactVault(
        artifact_root, artifact_root.parent / f"{replica.replace(':', '-')}-temp", vault=vault
    )
    return OnboardingService(
        store, access, sync=sync, artifact_vault=artifacts,
        attachment_scanner=lambda _path: "unavailable",
    ), sync


def test_encrypted_filled_artifact_replays_between_authorized_vaults(tmp_path: Path) -> None:
    vault = OnboardingVault(b"a" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(
        tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin",
        artifact_root=tmp_path / "admin-artifacts",
    )
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director",
        replica="director:palmdale", artifact_root=tmp_path / "director-artifacts",
    )
    employee = admin.create_employee(
        legal_name="Artifact Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    source = tmp_path / "filled.pdf"
    source.write_bytes(b"%PDF-1.4\nsensitive filled values\n%%EOF")
    package = admin.publish_document_package(admin.create_document_package_draft(
        package_key="artifact-package", school="Palmdale", title="Artifact package",
        document_paths=[source],
    ).id)
    submission = admin.submit_intake(
        submission_id="submission-1", employee_id=employee.id,
        application_id="application-1", schema_version=1, values={},
    )
    director_sync.replay_pending()
    artifact = FilledArtifact(
        id="merged-artifact-1", employee_id=employee.id, submission_id=submission.id,
        package_version_id=package.id, school="Palmdale", kind="merged", suffix=".pdf",
        sha256=__import__("hashlib").sha256(source.read_bytes()).hexdigest(),
        created_at="2026-07-20T08:00:00+00:00",
    )
    sealed = admin.artifact_vault.seal_file("Palmdale", source, artifact_id=artifact.id)
    admin.store.insert_filled_artifact(artifact)

    admin_sync.publish_filled_artifact(artifact, sealed_path=sealed)

    event_text = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "sensitive filled values" not in event_text
    assert director_sync.replay_pending() == 1
    opened = director.open_filled_artifact(
        employee_id=employee.id, artifact_id=artifact.id, suffix=".pdf"
    )
    assert opened.read_bytes() == source.read_bytes()


def test_automatic_fill_syncs_submission_before_its_artifacts(tmp_path: Path) -> None:
    vault = OnboardingVault(b"q" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(
        tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin",
        artifact_root=tmp_path / "admin-artifacts",
    )
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director",
        replica="director:palmdale", artifact_root=tmp_path / "director-artifacts",
    )
    source = tmp_path / "welcome.pdf"
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as file:
        writer.write(file)
    employee = admin.create_employee(
        legal_name="Automatic Fill", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    assert director_sync.replay_pending() > 0
    package = admin.publish_document_package(admin.create_document_package_draft(
        package_key="automatic-fill", school="Palmdale", title="Automatic fill",
        document_paths=[source],
    ).id)
    template = admin.publish_task_template(admin.create_task_template_draft(
        template_key="automatic-fill-task", school="Palmdale", title="Fill package",
        owner_role="Director", due_offset_days=0, package_key=package.package_key,
    ).id)
    assert director_sync.replay_pending() > 0
    admin.create_task(
        employee_id=employee.id, title="Fill package", owner_role="Director",
        due_date="2026-07-20", package_version_id=package.id,
        template_key=template.template_key, template_version=template.version,
        template_id=template.id,
    )
    assert director_sync.replay_pending() == 1

    submission = admin.submit_intake(
        submission_id="automatic-submission", employee_id=employee.id,
        application_id="automatic-application", schema_version=1, values={},
    )

    assert submission.status == "accepted"
    assert director_sync.replay_pending() > 0
    replayed = director.store.get_intake_submission(submission.id)
    assert replayed is not None
    artifacts = director.store.list_filled_artifacts(submission_id=submission.id)
    assert {artifact.kind for artifact in artifacts} == {"individual:1", "merged", "manifest"}


def test_encrypted_employee_change_replays_once_to_school_replica(tmp_path: Path) -> None:
    vault = OnboardingVault(b"k" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )

    employee = admin.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
        personal_email="jordan@example.com",
        notes="Sensitive note",
    )

    event_text = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "Jordan Lee" not in event_text
    assert "jordan@example.com" not in event_text
    assert "Sensitive note" not in event_text
    assert director_sync.replay_pending() == 1
    assert director.get_employee(employee.id).personal_email == "jordan@example.com"
    assert director_sync.replay_pending() == 0


def test_disjoint_employee_edits_auto_merge_and_same_field_uses_resolver(tmp_path: Path) -> None:
    vault = OnboardingVault(b"z" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    director_sync.replay_pending()

    admin.update_employee(employee.id, expected_version=1, changes={"preferred_name": "Jordy"})
    director.update_employee(employee.id, expected_version=1, changes={"notes": "Director note"})
    assert admin_sync.replay_pending() == 1
    assert director_sync.replay_pending() == 1
    assert admin.get_employee(employee.id).preferred_name == "Jordy"
    assert admin.get_employee(employee.id).notes == "Director note"
    assert director.get_employee(employee.id).preferred_name == "Jordy"
    assert director.get_employee(employee.id).notes == "Director note"

    conflicts = []
    admin_sync.conflict_resolver = lambda conflict: conflicts.append(conflict) is None
    admin_now = admin.get_employee(employee.id)
    director_now = director.get_employee(employee.id)
    admin.update_employee(employee.id, expected_version=admin_now.version, changes={"notes": "Admin choice"})
    director.update_employee(employee.id, expected_version=director_now.version, changes={"notes": "Director choice"})
    assert admin_sync.replay_pending() == 1
    assert conflicts and conflicts[0].fields == ("notes",)
    assert conflicts[0].local_values == (("notes", "<sensitive value changed>"),)
    assert conflicts[0].incoming_values == (("notes", "<sensitive value changed>"),)
    assert admin.get_employee(employee.id).notes == "Director choice"


def test_same_field_conflict_can_defer_without_acknowledging_event(tmp_path: Path) -> None:
    vault = OnboardingVault(b"d" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(
        tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin"
    )
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault,
        role="director", replica="director:palmdale",
    )
    employee = admin.create_employee(
        legal_name="Conflict", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    director_sync.replay_pending()
    admin.update_employee(employee.id, expected_version=1, changes={"notes": "Admin"})
    director.update_employee(employee.id, expected_version=1, changes={"notes": "Director"})
    admin_sync.conflict_resolver = lambda _conflict: "defer"

    assert admin_sync.replay_pending() == 0
    assert admin.get_employee(employee.id).notes == "Admin"
    assert admin_sync.health().state == "attention"
    assert len(admin_sync.conflicts()) == 1
    admin_sync.conflict_resolver = lambda _conflict: "use_incoming"
    assert admin_sync.replay_pending() == 1
    assert admin.get_employee(employee.id).notes == "Director"
    assert admin_sync.health().state == "healthy"


def test_task_creation_and_completion_replay_bidirectionally(tmp_path: Path) -> None:
    vault = OnboardingVault(b"t" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    director_sync.replay_pending()
    task = admin.create_task(
        employee_id=employee.id,
        title="Orientation",
        owner_role="Director",
        due_date="2026-07-15",
        notes="Private task note",
    )

    assert director_sync.replay_pending() == 1
    assert director.get_task(task.id).notes == "Private task note"
    director.complete_task(task.id)
    assert admin_sync.replay_pending() == 1
    assert admin.get_task(task.id).status == "completed"


def test_encrypted_task_comment_replays_to_school_replica(tmp_path: Path) -> None:
    vault = OnboardingVault(b"c" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-20"
    )
    director_sync.replay_pending()

    comment = admin.add_task_comment(task.id, body="Sensitive comment")

    artifacts = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "Sensitive comment" not in artifacts
    assert director_sync.replay_pending() == 1
    assert director.list_task_comment_revisions(comment.id)[0].body == "Sensitive comment"


def test_task_comment_revision_replays_without_losing_history(tmp_path: Path) -> None:
    vault = OnboardingVault(b"r" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-20"
    )
    comment = admin.add_task_comment(task.id, body="Original")
    director_sync.replay_pending()

    admin.edit_task_comment(comment.id, body="Corrected")

    assert director_sync.replay_pending() == 1
    revisions = director.list_task_comment_revisions(comment.id)
    assert [(revision.version, revision.body, revision.reason) for revision in revisions] == [
        (1, "Original", ""),
        (2, "Corrected", "author_edit"),
    ]


def test_encrypted_task_attachment_replays_and_validates_on_school_replica(tmp_path: Path) -> None:
    vault = OnboardingVault(b"a" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    admin.attachment_scanner = lambda _path: "clean"
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-20"
    )
    director_sync.replay_pending()
    source = tmp_path / "private-document.pdf"
    source.write_bytes(b"%PDF-1.4\nsensitive attachment")

    attachment = admin.add_task_attachment(task.id, source)

    artifacts = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "private-document.pdf" not in artifacts
    assert "sensitive attachment" not in artifacts
    assert director_sync.replay_pending() == 1
    assert director.read_task_attachment(attachment.id) == source.read_bytes()


def test_school_owner_role_configuration_replays_with_encrypted_email(tmp_path: Path) -> None:
    vault = OnboardingVault(b"o" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )

    configured = admin.configure_owner_role(
        school="Palmdale", role="Payroll", email="payroll@example.com"
    )

    artifacts = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "payroll@example.com" not in artifacts
    assert director_sync.replay_pending() == 1
    roles = {role.role: role for role in director.list_owner_roles()}
    assert roles["Payroll"] == configured


def test_global_intake_field_replays_so_director_can_validate_submission(tmp_path: Path) -> None:
    vault = OnboardingVault(b"f" * 32)
    stage_path = tmp_path / "changes"
    admin, admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    admin.create_intake_field(
        stable_id="employee.personal_email",
        label="Personal email",
        field_type="email",
        sensitivity="personal",
        aliases=["private email"],
    )

    assert director_sync.replay_pending() == 1
    employee = director.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    submission = director.submit_intake(
        submission_id="submission-1",
        employee_id=employee.id,
        application_id="application-1",
        schema_version=1,
        values={"employee.personal_email": "jordan@example.com"},
    )
    assert submission.values == {"employee.personal_email": "jordan@example.com"}
    assert admin_sync.replay_pending() == 2
    assert admin.submit_intake(
        submission_id="submission-1",
        employee_id=employee.id,
        application_id="application-1",
        schema_version=1,
        values={},
    ) == submission


def test_published_package_and_task_template_replay_before_director_hire(tmp_path: Path) -> None:
    vault = OnboardingVault(b"p" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    source = tmp_path / "welcome.pdf"
    source.write_bytes(b"%PDF-1.4\npackage content")
    package = admin.publish_document_package(
        admin.create_document_package_draft(
            package_key="teacher-start",
            school="Palmdale",
            title="Teacher start",
            document_paths=[source],
        ).id
    )
    template_draft = admin.create_task_template_draft(
            template_key="paperwork",
            school="Palmdale",
            title="Complete paperwork",
            owner_role="Director",
            due_offset_days=0,
            package_key="teacher-start",
        )
    guide = tmp_path / "guide.txt"
    guide.write_text("encrypted template guide", encoding="utf-8")
    admin.add_task_template_attachment(template_draft.id, guide)
    template = admin.publish_task_template(template_draft.id)

    assert director_sync.replay_pending() == 5
    employee = director.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    seeded = next(task for task in director.list_tasks() if task.employee_id == employee.id)
    assert seeded.template_id == template.id
    assert seeded.package_version_id == package.id
    [attachment] = director.list_task_attachments(seeded.id)
    assert director.read_task_attachment(attachment.id) == b"encrypted template guide"


def test_pdf_mapping_replays_with_reusable_field_reference(tmp_path: Path) -> None:
    vault = OnboardingVault(b"m" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    field = admin.create_intake_field(
        stable_id="employee.preferred_name",
        label="Preferred name",
        field_type="short_text",
        sensitivity="personal",
        aliases=[],
    )
    mapping = admin.create_pdf_mapping(
        document_key="welcome",
        page_number=1,
        rect=(10, 20, 120, 16),
        field_id=field.id,
        required=True,
    )

    assert director_sync.replay_pending() == 2
    assert director.store.list_pdf_mappings("welcome") == [mapping]


def test_reminder_preview_replays_pending_changes_before_reading_sensitive_queue(tmp_path: Path) -> None:
    vault = OnboardingVault(b"s" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, _director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Due task", owner_role="Director", due_date="2026-07-20"
    )

    preview = director.preview_reminders(
        recipient_resolver=lambda _school, _role: "director@example.com",
        admin_fallback_email="admin@example.com",
        now=datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
    )

    assert preview.messages[0].task_ids == (task.id,)


def test_restart_recovery_replays_applied_event_when_receipt_was_interrupted(tmp_path: Path) -> None:
    vault = OnboardingVault(b"i" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director_path = tmp_path / "director.sqlite3"
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    director, director_sync = _service(
        director_path, stage_path, vault, role="director", replica="director:palmdale"
    )
    assert director_sync.replay_pending() == 1
    receipt = next(stage_path.rglob("receipt-*.json"))
    receipt.unlink()

    restarted, restarted_sync = _service(
        director_path, stage_path, vault, role="director", replica="director:palmdale"
    )

    assert restarted_sync.replay_pending() == 1
    assert restarted.get_employee(employee.id) == director.get_employee(employee.id)
    assert restarted_sync.replay_pending() == 0


def test_employment_end_and_cancelled_tasks_replay_to_school_replica(tmp_path: Path) -> None:
    vault = OnboardingVault(b"e" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-20"
    )
    director_sync.replay_pending()

    admin.mark_employment_ended(
        employee.id,
        last_working_day="2026-08-01",
        departure_category="voluntary_resignation",
        departure_director_id="director-1",
        departure_director_name="Director One",
    )

    assert director_sync.replay_pending() == 2
    assert director.get_employee(employee.id).status == "archived"
    assert director.get_task(task.id).status == "cancelled"


def test_admin_transfer_moves_employee_and_task_history_between_school_replicas(tmp_path: Path) -> None:
    vault = OnboardingVault(b"x" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")

    def school_service(school: str):
        store = OnboardingStore(tmp_path / f"{school}.sqlite3", vault=vault)
        sync = OnboardingSyncCoordinator(
            store=store, stage=OnboardingChangeStage(stage_path), vault=vault,
            replica=f"director:{school.casefold()}", school_scope=school,
        )
        return OnboardingService(
            store, OnboardingAccess(role="director", actor=f"director-{school}", school_scope=school), sync=sync
        ), sync

    palmdale, palmdale_sync = school_service("Palmdale")
    hawthorne, hawthorne_sync = school_service("Hawthorne")
    employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = admin.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-20"
    )
    palmdale_sync.replay_pending()

    transferred = admin.transfer_employee(employee.id, new_school="Hawthorne")

    assert transferred.school == "Hawthorne"
    assert palmdale_sync.replay_pending() == 1
    assert hawthorne_sync.replay_pending() == 1
    assert palmdale.list_employees() == []
    assert hawthorne.get_employee(employee.id).school == "Hawthorne"
    assert hawthorne.get_task(task.id).school == "Hawthorne"


def test_permanent_delete_tombstone_replays_without_employee_pii(tmp_path: Path) -> None:
    vault = OnboardingVault(b"d" * 32)
    stage_path = tmp_path / "changes"
    admin, _admin_sync = _service(tmp_path / "admin.sqlite3", stage_path, vault, role="admin", replica="admin")
    director, director_sync = _service(
        tmp_path / "director.sqlite3", stage_path, vault, role="director", replica="director:palmdale"
    )
    employee = admin.create_employee(
        legal_name="Sensitive Name", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15", notes="private",
    )
    director_sync.replay_pending()
    admin.archive_correction(employee.id, reason="duplicate")
    director_sync.replay_pending()

    admin.permanently_delete_employee(employee.id, confirmation=f"DELETE {employee.id}")

    assert director_sync.replay_pending() == 1
    assert director.list_employees() == []
    tombstone = director.store.get_tombstone(employee.id)
    assert tombstone["payload"] == {"correction_reason": "duplicate"}
    artifacts = "\n".join(path.read_text(encoding="utf-8") for path in stage_path.rglob("event-*.json"))
    assert "Sensitive Name" not in artifacts
    assert "private" not in artifacts
