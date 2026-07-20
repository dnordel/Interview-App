from __future__ import annotations

from onboarding_service import OnboardingAccess, OnboardingPermissionError, OnboardingService
from onboarding_store import OnboardingStore
from onboarding_vault import EncryptedArtifactVault, OnboardingVault
import pytest
from pypdf import PdfWriter
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace


def test_director_creates_and_reads_only_school_employee(tmp_path):
    store = OnboardingStore(tmp_path / "onboarding.sqlite3")
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-1", school_scope="Palmdale"),
    )

    employee = director.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
    )

    assert employee.school == "Palmdale"
    assert employee.version == 1
    assert director.list_employees() == [employee]


def test_employee_mutation_replays_before_publish_and_after_commit(tmp_path):
    calls: list[str] = []
    sync = SimpleNamespace(
        replay_pending=lambda: calls.append("replay") or 0,
        publish_employee=lambda *args, **kwargs: calls.append("publish"),
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "sync-mutation.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"), sync=sync,
    )

    service.create_employee(
        legal_name="Jordan Rivera", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-20",
    )

    assert calls == ["replay", "publish", "replay"]


def test_store_additively_migrates_early_employee_schema(tmp_path):
    path = tmp_path / "onboarding.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE onboarding_employees (
                id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, preferred_name TEXT NOT NULL,
                school TEXT NOT NULL, role TEXT NOT NULL, acceptance_date TEXT NOT NULL,
                start_date TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT NOT NULL
            )
            """
        )

    store = OnboardingStore(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(onboarding_employees)")}
    assert {"source_application_id", "email", "last_working_day", "archived_at"} <= columns


def test_offer_acceptance_requires_contact_and_is_idempotent(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )

    with pytest.raises(ValueError, match="email"):
        service.accept_offer(
            application_id="app-1",
            legal_name="Jordan Rivera",
            school="Palmdale",
            role="Teacher",
            acceptance_date="2026-07-01",
            start_date="2026-07-20",
            email="",
            phone="6615550101",
            hiring_director_id="director-1",
            hiring_director_name="Morgan Lee",
        )

    first = service.accept_offer(
        application_id="app-1",
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
        email="jordan@example.com",
        phone="(661) 555-0101",
        hiring_director_id="director-1",
        hiring_director_name="Morgan Lee",
    )
    second = service.accept_offer(
        application_id="app-1",
        legal_name="Changed Name",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
        email="jordan@example.com",
        phone="6615550101",
        hiring_director_id="director-1",
        hiring_director_name="Morgan Lee",
    )

    assert second == first
    assert len(service.list_employees()) == 1


def test_task_assignment_and_completion_emit_pii_safe_idempotent_notifications(tmp_path):
    emitted: list[tuple[str, dict[str, str], str]] = []
    service = OnboardingService(
        OnboardingStore(tmp_path / "notifications.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
        notification_dispatcher=lambda event, payload, key: emitted.append((event, payload, key)),
    )
    employee = service.create_employee(
        legal_name="Sensitive Person", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-20",
        personal_email="person@example.com",
    )

    task = service.create_task(
        employee_id=employee.id, title="Complete payroll forms", owner_role="Payroll",
        due_date="2026-07-18",
    )
    service.complete_task(task.id)

    assert [item[0] for item in emitted] == ["onboarding.task.created", "onboarding.task.completed"]
    assert emitted[0][1] == {
        "school": "Palmdale", "task_title": "Complete payroll forms",
        "owner_role": "Payroll", "due_date": "2026-07-18",
    }
    assert "Sensitive Person" not in str(emitted)
    assert "person@example.com" not in str(emitted)
    assert emitted[0][2] == f"onboarding:task:{task.id}:created:v1"
    assert emitted[1][2] == f"onboarding:task:{task.id}:completed:v2"
    database_bytes = b"".join(path.read_bytes() for path in tmp_path.glob("onboarding.sqlite3*"))
    assert b"jordan@example.com" not in database_bytes


def test_accepted_offer_seeds_latest_published_task_template_snapshot(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    draft = service.create_task_template_draft(
        template_key="orientation",
        school="*",
        title="Complete orientation",
        owner_role="Director",
        due_offset_days=2,
    )
    service.publish_task_template(draft.id)

    employee = service.accept_offer(
        application_id="app-template",
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
        email="jordan@example.com",
        phone="6615550101",
        hiring_director_id="director-1",
        hiring_director_name="Morgan Lee",
    )

    task = service.list_tasks()[0]
    assert task.employee_id == employee.id
    assert task.title == "Complete orientation"
    assert task.due_date == "2026-07-22"
    assert task.template_key == "orientation" and task.template_version == 1


def test_task_dependency_blocks_completion_until_predecessor_complete(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    employee = service.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
    )
    prerequisite = service.create_task(
        employee_id=employee.id,
        title="Verify identity",
        owner_role="Office Manager",
        due_date="2026-07-15",
    )
    dependent = service.create_task(
        employee_id=employee.id,
        title="Submit payroll",
        owner_role="Payroll",
        due_date="2026-07-16",
        dependency_ids=[prerequisite.id],
    )

    with pytest.raises(ValueError, match="blocked"):
        service.complete_task(dependent.id)
    assert service.get_task(dependent.id).status == "blocked"

    service.complete_task(prerequisite.id)
    assert service.complete_task(dependent.id).status == "completed"
    assert {task.title for task in service.list_tasks()} == {"Verify identity", "Submit payroll"}


def test_employment_end_archives_employee_and_cancels_open_tasks(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    employee = service.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
    )
    task = service.create_task(
        employee_id=employee.id,
        title="Complete orientation",
        owner_role="Director",
        due_date="2026-07-20",
    )

    ended = service.mark_employment_ended(
        employee.id,
        last_working_day="2027-08-31",
        departure_category="voluntary_resignation",
        departure_director_id="director-1",
        departure_director_name="Morgan Lee",
    )

    assert ended.status == "archived"
    assert ended.last_working_day == "2027-08-31"
    assert service.get_task(task.id).status == "cancelled"


def test_pdf_mapper_reuses_existing_field_or_creates_new_field_inline(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    existing = service.create_intake_field(
        stable_id="employee.legal_name",
        label="Legal name",
        field_type="short_text",
        sensitivity="personal",
        aliases=["Full legal name"],
    )

    reused = service.create_pdf_mapping(
        document_key="i9",
        page_number=1,
        rect=(72, 120, 240, 18),
        field_id=existing.id,
        required=True,
    )
    created = service.create_pdf_mapping(
        document_key="i9",
        page_number=1,
        rect=(72, 160, 180, 18),
        new_field={
            "stable_id": "employee.preferred_name",
            "label": "Preferred name",
            "field_type": "short_text",
            "sensitivity": "personal",
            "aliases": [],
        },
    )

    assert reused.field_id == existing.id
    assert service.get_intake_field(created.field_id).stable_id == "employee.preferred_name"
    assert {field.stable_id for field in service.search_intake_fields("name")} == {
        "employee.legal_name",
        "employee.preferred_name",
    }


def test_intake_submission_fails_closed_on_unknown_fields_and_is_idempotent(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    employee = service.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
    )
    service.create_intake_field(
        stable_id="employee.preferred_name",
        label="Preferred name",
        field_type="short_text",
        sensitivity="personal",
        aliases=[],
    )

    with pytest.raises(ValueError, match="Unknown intake field"):
        service.submit_intake(
            submission_id="submission-1",
            employee_id=employee.id,
            application_id="app-1",
            schema_version=1,
            values={"employee.unknown": "value"},
        )

    first = service.submit_intake(
        submission_id="submission-1",
        employee_id=employee.id,
        application_id="app-1",
        schema_version=1,
        values={"employee.preferred_name": "Jordan"},
    )
    second = service.submit_intake(
        submission_id="submission-1",
        employee_id=employee.id,
        application_id="app-1",
        schema_version=1,
        values={"employee.preferred_name": "Changed"},
    )

    assert second == first
    assert first.values == {"employee.preferred_name": "Jordan"}

    correction = service.correct_intake_submission(
        "submission-1",
        correction_id="submission-1-r2",
        values={"employee.preferred_name": "Jordy"},
    )
    assert correction.revision == 2
    assert correction.corrects_submission_id == first.id
    assert correction.values == {"employee.preferred_name": "Jordy"}
    assert service.store.get_intake_submission(first.id).values == {"employee.preferred_name": "Jordan"}


def test_document_package_publish_is_immutable_and_versions_increment(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    pdf = tmp_path / "welcome.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf.open("wb") as file:
        writer.write(file)

    first_draft = service.create_document_package_draft(
        package_key="teacher-start",
        school="Palmdale",
        title="Teacher Start Package",
        document_paths=[pdf],
    )
    assert service.validate_document_package(first_draft.id) == ()
    first = service.publish_document_package(first_draft.id)
    second = service.create_document_package_draft(
        package_key="teacher-start",
        school="Palmdale",
        title="Teacher Start Package",
        document_paths=[pdf],
    )

    assert first.version == 1 and first.status == "published"
    assert second.version == 2 and second.status == "draft"
    assert service.latest_published_document_package("teacher-start", school="Palmdale") == first


def test_task_template_assigns_latest_package_snapshot_and_admin_upgrades_selected_employee(tmp_path):
    store = OnboardingStore(tmp_path / "package-assignment.sqlite3")
    service = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    second_pdf.write_bytes(b"%PDF-1.4\nsecond\n%%EOF")
    first = service.publish_document_package(
        service.create_document_package_draft(
            package_key="teacher-start", school="Palmdale", title="Teacher start", document_paths=[first_pdf]
        ).id
    )
    template = service.create_task_template_draft(
        template_key="paperwork", school="Palmdale", title="Complete paperwork",
        owner_role="Director", due_offset_days=0, package_key="teacher-start",
    )
    service.publish_task_template(template.id)
    employee = service.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    seeded = next(task for task in service.list_tasks() if task.employee_id == employee.id)
    assert seeded.package_version_id == first.id

    second = service.publish_document_package(
        service.create_document_package_draft(
            package_key="teacher-start", school="Palmdale", title="Teacher start", document_paths=[second_pdf]
        ).id
    )
    assert service.get_task(seeded.id).package_version_id == first.id

    assert service.preview_employee_package_upgrade(
        package_key="teacher-start",
        package_version_id=second.id,
        employee_ids=[employee.id],
    ) == (employee.id,)

    changed = service.upgrade_employee_package(
        package_key="teacher-start", package_version_id=second.id, employee_ids=[employee.id]
    )
    assert changed == 1
    assert service.get_task(seeded.id).package_version_id == second.id


def test_intake_field_similarity_and_deprecation_preserve_existing_mapping(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "field-lifecycle.sqlite3"),
        OnboardingAccess(role="admin", actor="owner"),
    )
    field = service.create_intake_field(
        stable_id="personal_email", label="Personal email address", field_type="email",
        sensitivity="contact", aliases=["home email"],
    )
    suggestions = service.suggest_similar_intake_fields("Personal email", aliases=["private email"])
    assert [item.id for item in suggestions] == [field.id]
    mapping = service.create_pdf_mapping(
        document_key="w4", page_number=1, rect=(10, 10, 100, 12), field_id=field.id,
    )

    deprecated = service.deprecate_intake_field(field.id)

    assert deprecated.deprecated is True
    assert service.get_intake_field(field.id).deprecated is True
    assert mapping.field_id == field.id
    assert service.search_intake_fields("personal email") == []


def test_admin_transfer_moves_employee_and_tasks_while_director_cannot_transfer(tmp_path):
    store = OnboardingStore(tmp_path / "onboarding.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="admin-1"))
    employee = admin.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
        notes="Private employee note",
        personal_email="jordan@example.com",
        address_line1="123 Main St",
        dob="1990-01-02",
        ssn="123456789",
    )
    task = admin.create_task(
        employee_id=employee.id,
        title="Complete orientation",
        owner_role="Director",
        due_date="2026-07-20",
        notes="Private task note",
    )
    comment = admin.add_task_comment(task.id, body="Private comment")
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-1", school_scope="Palmdale"),
    )

    with pytest.raises(PermissionError, match="admin"):
        director.transfer_employee(employee.id, new_school="Hawthorne")
    moved = admin.transfer_employee(employee.id, new_school="Hawthorne")

    assert moved.school == "Hawthorne"
    assert admin.get_task(task.id).school == "Hawthorne"
    assert moved.notes == "Private employee note"
    assert admin.get_task(task.id).notes == "Private task note"
    assert admin.list_task_comment_revisions(comment.id)[0].body == "Private comment"
    assert director.list_employees() == []


def test_did_not_start_archives_without_departure_director(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    employee = service.create_employee(
        legal_name="Jordan Rivera",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-20",
    )
    task = service.create_task(
        employee_id=employee.id,
        title="Complete orientation",
        owner_role="Director",
        due_date="2026-07-20",
    )

    archived = service.mark_did_not_start(employee.id, reason="candidate_withdrew")

    assert archived.status == "archived"
    assert archived.did_not_start_reason == "candidate_withdrew"
    assert archived.departure_director_id == ""
    assert service.get_task(task.id).status == "cancelled"


def test_employee_profile_is_validated_encrypted_and_versioned_through_service(tmp_path):
    store = OnboardingStore(tmp_path / "profile.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    employee = admin.create_employee(
        legal_name="Jordan Lee",
        preferred_name="Jordy",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
        address_line1="123 Main St",
        city="Palmdale",
        state="CA",
        postal_code="93550",
        personal_email="jordan@example.com",
        work_email="jlee@lpl.test",
        phone="(661) 555-0123",
        notes="Needs a secure follow-up",
        source_history_id="history-1",
        dob="1990-02-03",
        ssn="123-45-6789",
    )

    assert employee.phone == "6615550123"
    assert employee.ssn == "123456789"
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale"),
    )
    updated = director.update_employee(
        employee.id,
        expected_version=1,
        changes={"preferred_name": "Jordan", "notes": "Updated secure note"},
    )
    assert updated.preferred_name == "Jordan"
    assert updated.notes == "Updated secure note"
    assert updated.version == 2

    with pytest.raises(ValueError, match="changed since"):
        director.update_employee(employee.id, expected_version=1, changes={"preferred_name": "J"})
    with sqlite3.connect(store.path) as connection:
        raw = connection.execute(
            "SELECT personal_email, notes, dob, ssn FROM onboarding_employees WHERE id = ?",
            (employee.id,),
        ).fetchone()
    assert all(isinstance(value, bytes) for value in raw)
    assert b"jordan@example.com" not in raw[0]
    assert b"Updated secure note" not in raw[1]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("personal_email", "invalid", "valid email"),
        ("phone", "123", "10-digit"),
        ("ssn", "123", "9-digit"),
        ("dob", "02/03/1990", "YYYY-MM-DD"),
        ("postal_code", "ABC", "ZIP code"),
    ],
)
def test_employee_profile_rejects_invalid_optional_values(tmp_path, field, value, message):
    service = OnboardingService(
        OnboardingStore(tmp_path / f"invalid-{field}.sqlite3"),
        OnboardingAccess(role="admin", actor="owner"),
    )
    values = {
        "legal_name": "Jordan Lee",
        "school": "Palmdale",
        "role": "Teacher",
        "acceptance_date": "2026-07-01",
        "start_date": "2026-07-15",
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        service.create_employee(**values)


def test_manual_possible_duplicate_enters_merge_review_without_seeded_tasks(tmp_path):
    store = OnboardingStore(tmp_path / "duplicates.sqlite3")
    service = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    template = service.create_task_template_draft(
        template_key="orientation",
        school="Palmdale",
        title="Complete orientation",
        owner_role="Director",
        due_offset_days=0,
    )
    service.publish_task_template(template.id)
    first = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    duplicate = service.create_employee(
        legal_name=" jordan lee ",
        school="PALMDALE",
        role="Teacher",
        acceptance_date="2026-07-02",
        start_date="2026-07-16",
    )

    assert first.status == "active"
    assert duplicate.status == "merge_review"
    assert [task.employee_id for task in service.list_tasks()] == [first.id]


def test_legacy_offer_without_contact_is_attention_and_idempotent(tmp_path):
    store = OnboardingStore(tmp_path / "legacy-offer.sqlite3")
    service = OnboardingService(store, OnboardingAccess(role="admin", actor="migration"))

    first = service.accept_legacy_offer(
        application_id="legacy-1",
        legal_name="Taylor Diaz",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-06-01",
        start_date="2026-06-15",
    )
    second = service.accept_legacy_offer(
        application_id="legacy-1",
        legal_name="Ignored Change",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-06-01",
        start_date="2026-06-15",
    )

    assert first == second
    assert first.status == "attention"
    assert first.email == "" and first.phone == ""


def test_archive_delete_and_retention_purge_require_admin_confirmation(tmp_path):
    store = OnboardingStore(tmp_path / "retention.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    active = admin.create_employee(
        legal_name="Test Record",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    with pytest.raises(ValueError, match="archived first"):
        admin.permanently_delete_employee(active.id, confirmation=f"DELETE {active.id}")
    archived = admin.archive_correction(active.id, reason="test_record")
    assert archived.status == "archived"
    with pytest.raises(ValueError, match="confirmation"):
        admin.permanently_delete_employee(active.id, confirmation="DELETE")
    admin.permanently_delete_employee(active.id, confirmation=f"DELETE {active.id}")
    tombstone = store.get_tombstone(active.id)
    assert tombstone["payload"]["correction_reason"] == "test_record"
    assert "Test Record" not in str(tombstone)

    old = admin.create_employee(
        legal_name="Old Employee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2017-01-01",
        start_date="2017-02-01",
    )
    admin.mark_employment_ended(
        old.id,
        last_working_day="2018-06-30",
        departure_category="voluntary_resignation",
        departure_director_id="director-1",
        departure_director_name="Director Snapshot",
    )
    candidates = admin.preview_retention_purge(as_of="2026-07-19")
    assert [candidate.employee_id for candidate in candidates] == [old.id]
    admin.purge_retained_employee(
        old.id,
        as_of="2026-07-19",
        confirmation=f"PURGE {old.id}",
    )
    purged = store.get_tombstone(old.id)
    assert purged["payload"]["purged"] is True
    assert purged["payload"]["departure_category"] == "voluntary_resignation"
    assert "Old Employee" not in str(purged)


def test_ssn_is_masked_by_default_and_reveal_is_audited(tmp_path):
    store = OnboardingStore(tmp_path / "ssn.sqlite3")
    service = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    employee = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
        ssn="123-45-6789",
    )

    assert service.masked_ssn(employee.id) == "***-**-6789"
    assert service.reveal_ssn(employee.id, reason="Payroll verification") == "123456789"
    events = store.list_audit_events(entity_id=employee.id)
    assert events[-1]["action"] == "employee.ssn_revealed"
    assert "123456789" not in str(events[-1])


def test_lock_and_forget_device_have_distinct_cache_behavior(tmp_path):
    vault = OnboardingVault(b"k" * 32)
    keep_cache = tmp_path / "keep.dpapi"
    vault.cache_for_device(keep_cache)
    locked = OnboardingService(
        OnboardingStore(tmp_path / "locked.sqlite3", vault=vault),
        OnboardingAccess(role="admin", actor="owner"),
        device_cache_path=keep_cache,
    )

    locked.lock_onboarding()

    assert locked.onboarding_locked is True
    assert keep_cache.exists()

    forget_vault = OnboardingVault(b"f" * 32)
    forget_cache = tmp_path / "forget.dpapi"
    forget_vault.cache_for_device(forget_cache)
    forgotten = OnboardingService(
        OnboardingStore(tmp_path / "forgotten.sqlite3", vault=forget_vault),
        OnboardingAccess(role="admin", actor="owner"),
        device_cache_path=forget_cache,
    )
    forgotten.forget_device()

    assert forgotten.onboarding_locked is True
    assert not forget_cache.exists()


def test_lock_onboarding_removes_all_decrypted_temp_artifacts(tmp_path):
    vault = OnboardingVault(b"l" * 32)
    artifacts = EncryptedArtifactVault(tmp_path / "sealed", tmp_path / "temp", vault=vault)
    exposed = artifacts.temp_root / "exposed.pdf"
    exposed.write_bytes(b"private")
    service = OnboardingService(
        OnboardingStore(tmp_path / "locked-temp.sqlite3", vault=vault),
        OnboardingAccess(role="admin", actor="admin-1"), artifact_vault=artifacts,
    )

    service.lock_onboarding()

    assert not exposed.exists()
    assert service.onboarding_locked


def test_required_subtask_blocks_parent_and_comment_revisions_are_encrypted(tmp_path):
    store = OnboardingStore(tmp_path / "task-collaboration.sqlite3")
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale"),
    )
    employee = director.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    parent = director.create_task(
        employee_id=employee.id,
        title="Finish onboarding",
        owner_role="Director",
        due_date="2026-07-15",
        notes="Private task note",
    )
    child = director.create_task(
        employee_id=employee.id,
        title="Verify identity",
        owner_role="Director",
        due_date="2026-07-14",
        parent_task_id=parent.id,
        required=True,
    )

    with pytest.raises(ValueError, match="required subtasks"):
        director.complete_task(parent.id)
    comment = director.add_task_comment(parent.id, body="Initial private comment")
    edited = director.edit_task_comment(comment.id, body="Corrected private comment")
    assert edited.version == 2
    revisions = director.list_task_comment_revisions(comment.id)
    assert [revision.body for revision in revisions] == ["Initial private comment", "Corrected private comment"]
    with sqlite3.connect(store.path) as connection:
        raw = connection.execute(
            "SELECT body_encrypted FROM onboarding_task_comment_revisions WHERE comment_id = ? ORDER BY version",
            (comment.id,),
        ).fetchall()
    assert all(isinstance(row[0], bytes) for row in raw)
    assert b"private comment" not in b"".join(row[0] for row in raw)

    director.complete_task(child.id)
    assert director.complete_task(parent.id).status == "completed"


def test_director_updates_task_collaboration_fields_with_version_guard(tmp_path):
    store = OnboardingStore(tmp_path / "task-edit.sqlite3")
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale"),
    )
    employee = director.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    dependency = director.create_task(
        employee_id=employee.id, title="Identity check", owner_role="Director", due_date="2026-07-14"
    )
    task = director.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director", due_date="2026-07-15"
    )

    updated = director.update_task(
        task.id,
        expected_version=task.version,
        changes={
            "title": "New employee orientation",
            "owner_role": "Office Manager",
            "watcher_roles": ["Director", "IT", "Director"],
            "due_date": "2026-07-16",
            "critical": True,
            "dependency_ids": [dependency.id],
            "notes": "Bring identification",
        },
    )

    assert updated.version == 2
    assert updated.title == "New employee orientation"
    assert updated.owner_role == "Office Manager"
    assert updated.watcher_roles == ("Director", "IT")
    assert updated.dependency_ids == (dependency.id,)
    assert updated.critical is True
    with pytest.raises(ValueError, match="changed since"):
        director.update_task(task.id, expected_version=task.version, changes={"title": "Stale"})


def test_task_query_combines_authorized_queue_filters(tmp_path):
    store = OnboardingStore(tmp_path / "task-query.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    palmdale = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    hawthorne = admin.create_employee(
        legal_name="Sam Cruz", school="Hawthorne", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    prerequisite = admin.create_task(
        employee_id=palmdale.id, title="Verify identity", owner_role="IT", due_date="2026-07-18"
    )
    wanted = admin.create_task(
        employee_id=palmdale.id, title="Payroll enrollment", owner_role="Payroll",
        watcher_roles=["Director"], due_date="2026-07-19", critical=True,
        dependency_ids=[prerequisite.id],
    )
    admin.create_task(
        employee_id=hawthorne.id, title="Payroll enrollment", owner_role="Payroll",
        watcher_roles=["Director"], due_date="2026-07-19", critical=True,
    )
    director = OnboardingService(
        store, OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale")
    )

    result = director.query_tasks(
        search="Jordan payroll",
        owner_role="Payroll",
        watcher_role="Director",
        employee_id=palmdale.id,
        statuses=("open",),
        urgency="critical",
        blocked=True,
        due_from="2026-07-18",
        due_to="2026-07-20",
        as_of="2026-07-20",
    )

    assert [task.id for task in result] == [wanted.id]


def test_task_metrics_separate_blocked_overdue_from_actionable_overdue(tmp_path):
    store = OnboardingStore(tmp_path / "task-metrics.sqlite3")
    director = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale"),
    )
    employee = director.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    prerequisite = director.create_task(
        employee_id=employee.id, title="Verify identity", owner_role="Director", due_date="2026-07-20",
    )
    director.create_task(
        employee_id=employee.id, title="Blocked paperwork", owner_role="Director",
        due_date="2026-07-18", dependency_ids=[prerequisite.id],
    )
    director.create_task(
        employee_id=employee.id, title="Actionable overdue", owner_role="Director", due_date="2026-07-18",
    )
    completed = director.create_task(
        employee_id=employee.id, title="Complete", owner_role="Director", due_date="2026-07-16",
    )
    director.complete_task(completed.id)

    metrics = director.task_metrics(as_of="2026-07-19")

    assert metrics.open == 3
    assert metrics.blocked == 1
    assert metrics.blocked_overdue == 1
    assert metrics.overdue == 2
    assert metrics.actionable_overdue == 1
    assert metrics.completed == 1


def test_owner_roles_are_configurable_by_admin_and_school_scoped_for_director(tmp_path):
    store = OnboardingStore(tmp_path / "owner-roles.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    seeded = admin.list_owner_roles(school="Palmdale")
    assert {item.role for item in seeded} == {"Office Manager", "Payroll", "Benefits", "Director", "IT"}

    configured = admin.configure_owner_role(
        school="Palmdale", role="Payroll", email="payroll-palmdale@example.com", active=True
    )
    director = OnboardingService(
        store, OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale")
    )
    roles = {item.role: item for item in director.list_owner_roles()}
    assert roles["Payroll"] == configured
    assert director.resolve_owner_recipient(
        role="Payroll", admin_fallback_email="admin@example.com"
    ) == ("payroll-palmdale@example.com", "")
    assert director.resolve_owner_recipient(
        role="IT", admin_fallback_email="admin@example.com"
    ) == ("admin@example.com", "Palmdale IT has no email; Admin fallback will be used.")
    with pytest.raises(OnboardingPermissionError, match="admin-only"):
        director.configure_owner_role(school="Palmdale", role="IT", email="it@example.com")


def test_published_task_template_deprecates_without_changing_existing_task_snapshot(tmp_path):
    store = OnboardingStore(tmp_path / "template-deprecate.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    template = admin.publish_task_template(
        admin.create_task_template_draft(
            template_key="orientation", school="Palmdale", title="Orientation",
            owner_role="Director", due_offset_days=0,
        ).id
    )
    first_employee = admin.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    first_task = next(task for task in admin.list_tasks() if task.employee_id == first_employee.id)

    deprecated = admin.deprecate_task_template(template.id)
    second_employee = admin.create_employee(
        legal_name="Sam Cruz", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-02", start_date="2026-07-16",
    )

    assert deprecated.status == "deprecated"
    assert admin.get_task(first_task.id).template_version == template.version
    assert [task for task in admin.list_tasks() if task.employee_id == second_employee.id] == []


def test_admin_previews_and_applies_template_upgrade_only_to_selected_employees(tmp_path):
    store = OnboardingStore(tmp_path / "template-upgrade.sqlite3")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    first = admin.publish_task_template(
        admin.create_task_template_draft(
            template_key="orientation", school="Palmdale", title="Orientation",
            owner_role="Director", watcher_roles=[], due_offset_days=0,
        ).id
    )
    employees = [
        admin.create_employee(
            legal_name=name, school="Palmdale", role="Teacher",
            acceptance_date="2026-07-01", start_date="2026-07-15",
        )
        for name in ("Jordan Lee", "Sam Cruz")
    ]
    original_tasks = {
        task.employee_id: task for task in admin.list_tasks() if task.template_id == first.id
    }
    second = admin.publish_task_template(
        admin.create_task_template_draft(
            template_key="orientation", school="Palmdale", title="New employee orientation",
            owner_role="Office Manager", watcher_roles=["Director"], due_offset_days=2,
            critical=True,
        ).id
    )

    preview = admin.preview_task_template_upgrade(
        second.id, employee_ids=[employee.id for employee in employees]
    )
    assert {item.employee_id for item in preview} == {employee.id for employee in employees}
    assert set(preview[0].changed_fields) == {
        "critical", "due_date", "owner_role", "template_id", "template_version", "title", "watcher_roles"
    }

    applied = admin.apply_task_template_upgrade(second.id, employee_ids=[employees[0].id])
    assert len(applied) == 1
    assert applied[0].template_id == second.id
    assert applied[0].template_version == second.version
    assert applied[0].title == "New employee orientation"
    assert applied[0].due_date == "2026-07-17"
    assert admin.get_task(original_tasks[employees[1].id].id).template_id == first.id


def test_only_comment_author_edits_and_admin_redaction_requires_reason(tmp_path):
    store = OnboardingStore(tmp_path / "comment-auth.sqlite3")
    first = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-one", school_scope="Palmdale"),
    )
    employee = first.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    task = first.create_task(
        employee_id=employee.id,
        title="Orientation",
        owner_role="Director",
        due_date="2026-07-15",
    )
    comment = first.add_task_comment(task.id, body="Comment")
    second = OnboardingService(
        store,
        OnboardingAccess(role="director", actor="director-two", school_scope="Palmdale"),
    )
    with pytest.raises(PermissionError, match="author"):
        second.edit_task_comment(comment.id, body="Unauthorized")
    admin = OnboardingService(store, OnboardingAccess(role="admin", actor="owner"))
    with pytest.raises(ValueError, match="reason"):
        admin.redact_task_comment(comment.id, reason="")
    redacted = admin.redact_task_comment(comment.id, reason="Contains sensitive content")
    assert redacted.redacted is True
    assert redacted.body == "[Redacted by Admin]"


def test_every_onboarding_store_connection_enforces_foreign_keys(tmp_path):
    store = OnboardingStore(tmp_path / "foreign-keys.sqlite3")

    with store._connect() as connection:
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert enabled == 1


def test_additive_store_migration_tolerates_concurrent_startup(tmp_path):
    path = tmp_path / "concurrent-migration.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE onboarding_employees (id TEXT PRIMARY KEY)")

    def add_column(_index):
        with sqlite3.connect(path, timeout=10) as connection:
            OnboardingStore._add_column_if_missing(
                connection, "onboarding_employees", "postal_code", "BLOB NOT NULL DEFAULT X''"
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add_column, range(4)))

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(onboarding_employees)")}
    assert "postal_code" in columns


def test_admin_library_reads_include_all_versions_and_director_is_denied(tmp_path):
    store = OnboardingStore(tmp_path / "library-reads.sqlite3")
    admin = OnboardingService(store, OnboardingAccess("admin", "admin-1"))
    director = OnboardingService(
        store, OnboardingAccess("director", "director-1", "Palmdale")
    )
    field = admin.create_intake_field(
        stable_id="employee.nickname", label="Nickname", field_type="short_text",
        sensitivity="personal", aliases=["preferred nickname"],
    )
    template = admin.create_task_template_draft(
        template_key="orientation", school="Palmdale", title="Orientation",
        owner_role="Director", due_offset_days=1,
    )

    assert admin.list_task_template_versions() == [template]
    assert admin.list_intake_fields() == [field]
    assert admin.list_document_package_versions() == []
    assert admin.list_pdf_mappings() == []
    for operation in (
        director.list_task_template_versions,
        director.list_intake_fields,
        director.list_document_package_versions,
        director.list_pdf_mappings,
    ):
        with pytest.raises(OnboardingPermissionError):
            operation()


def test_task_comment_listing_is_task_and_school_authorized(tmp_path):
    store = OnboardingStore(tmp_path / "comment-listing.sqlite3")
    director = OnboardingService(
        store, OnboardingAccess("director", "director-1", "Palmdale")
    )
    employee = director.create_employee(
        legal_name="Comment Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = director.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director",
        due_date="2026-07-20",
    )
    comment = director.add_task_comment(task.id, body="Bring identification")

    assert director.list_task_comments(task.id) == [comment]


def test_task_attachment_lists_and_opens_through_contained_temp_copy(tmp_path):
    vault = OnboardingVault(b"t" * 32)
    artifact_vault = EncryptedArtifactVault(
        tmp_path / "sealed", tmp_path / "temporary", vault=vault
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "attachment-open.sqlite3", vault=vault),
        OnboardingAccess("director", "director-1", "Palmdale"),
        attachment_scanner=lambda _path: "clean",
        artifact_vault=artifact_vault,
    )
    employee = service.create_employee(
        legal_name="Attachment Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id, title="Documents", owner_role="Director",
        due_date="2026-07-20",
    )
    source = tmp_path / "note.txt"
    source.write_text("private attachment", encoding="utf-8")
    attachment = service.add_task_attachment(task.id, source)

    assert service.list_task_attachments(task.id) == [attachment]
    opened = service.open_task_attachment(attachment.id)
    assert opened.parent == artifact_vault.temp_root
    assert opened.read_text(encoding="utf-8") == "private attachment"
    service.close_task_attachment(opened)
    assert not opened.exists()


def test_task_audit_events_are_read_through_authorized_task(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "task-audit.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Audit Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id, title="Audit task", owner_role="Director",
        due_date="2026-07-20",
    )

    events = service.list_task_audit_events(task.id)
    assert events[-1]["action"] == "task.created"
    assert events[-1]["entity_id"] == task.id
    assert service.list_employee_audit_events(employee.id)[0]["action"] == "employee.created"


def test_admin_previews_and_imports_legacy_data_through_service_boundary(tmp_path):
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"employees": []}), encoding="utf-8")
    service = OnboardingService(
        OnboardingStore(tmp_path / "legacy-service.sqlite3"),
        OnboardingAccess("admin", "migration-admin"),
    )

    preview = service.preview_legacy_import(source)
    result = service.import_legacy_data(
        source,
        backup_dir=tmp_path / "backups",
        expected_sha256=preview.source_sha256,
        confirmation="IMPORT",
    )

    assert result.preview == preview
    assert result.backup_path.is_file()
    assert service.store.list_audit_events()[-1]["action"] == "migration.legacy_json_imported"


def test_director_cannot_preview_or_import_legacy_data(tmp_path):
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"employees": []}), encoding="utf-8")
    service = OnboardingService(
        OnboardingStore(tmp_path / "director-legacy.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    with pytest.raises(OnboardingPermissionError, match="admin-only"):
        service.preview_legacy_import(source)
    with pytest.raises(OnboardingPermissionError, match="admin-only"):
        service.import_legacy_data(
            source, backup_dir=tmp_path / "backups",
            expected_sha256="digest", confirmation="IMPORT",
        )


def test_school_task_template_overrides_only_selected_global_fields(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "template-overrides.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    global_template = service.publish_task_template(service.create_task_template_draft(
        template_key="orientation", school="*", title="Global orientation",
        owner_role="Director", watcher_roles=["Payroll"], due_offset_days=2,
        content="Global instructions",
    ).id)
    school_template = service.publish_task_template(service.create_task_template_draft(
        template_key="orientation", school="Palmdale", title="Ignored school title",
        owner_role="Office Manager", watcher_roles=[], due_offset_days=99,
        content="Ignored school content", base_template_id=global_template.id,
        override_fields=["owner_role"],
    ).id)
    service.create_employee(
        legal_name="Override Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-20",
    )
    [task] = service.list_tasks()

    assert school_template.override_fields == ("owner_role",)
    assert task.template_id == school_template.id
    assert task.title == "Global orientation"
    assert task.owner_role == "Office Manager"
    assert task.watcher_roles == ("Payroll",)
    assert task.notes == "Global instructions"
    assert task.due_date == "2026-07-22"


def test_published_template_attachment_seeds_independent_encrypted_task_snapshot(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "template-attachment.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
        attachment_scanner=lambda _path: "unavailable",
    )
    draft = service.create_task_template_draft(
        template_key="welcome", school="Palmdale", title="Read welcome guide",
        owner_role="Director", due_offset_days=0,
    )
    source = tmp_path / "welcome.txt"
    source.write_text("private onboarding instructions", encoding="utf-8")
    template_attachment = service.add_task_template_attachment(draft.id, source)
    service.publish_task_template(draft.id)
    employee = service.create_employee(
        legal_name="Taylor Snapshot", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-20",
    )
    seeded = next(task for task in service.list_tasks() if task.employee_id == employee.id)
    attachments = service.list_task_attachments(seeded.id)

    assert len(attachments) == 1
    assert attachments[0].id != template_attachment.id
    assert service.read_task_attachment(attachments[0].id) == b"private onboarding instructions"
    assert b"private onboarding instructions" not in service.store.path.read_bytes()


def test_pdf_mapping_preview_uses_synthetic_values_and_reports_signature_manifest(tmp_path):
    source = tmp_path / "preview.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with source.open("wb") as file:
        writer.write(file)
    service = OnboardingService(
        OnboardingStore(tmp_path / "preview.sqlite3"), OnboardingAccess("admin", "admin-1")
    )
    name = service.create_intake_field(
        stable_id="employee.name", label="Name", aliases=[], field_type="short_text", sensitivity="standard"
    )
    signature = service.create_intake_field(
        stable_id="employee.signature", label="Signature", aliases=[], field_type="signature", sensitivity="sensitive"
    )
    for field, rect in ((name, (20, 200, 150, 20)), (signature, (20, 100, 150, 30))):
        service.create_pdf_mapping(
            document_key="preview", page_number=1, rect=rect, field_id=field.id,
            required=True, font_name="Helvetica", font_size=10,
            alignment="left", multiline=False,
        )

    preview = service.preview_pdf_mapping(source)

    assert preview.output_path is not None and preview.output_path.is_file()
    assert preview.overflow_errors == ()
    assert {item.mapping_id: item.fits for item in preview.mapping_results} == {
        mapping.id: True for mapping in service.list_pdf_mappings()
    }
    assert preview.required_signatures == ("employee.signature",)
    assert dict(preview.synthetic_values)["employee.name"] == "Sample value"
