from pathlib import Path

import pytest
from pypdf import PdfWriter

from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore
from onboarding_vault import EncryptedArtifactVault, OnboardingVault, VaultIntegrityError


def _pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as file:
        writer.write(file)


def test_intake_fills_package_into_encrypted_artifacts_and_secure_temp_open(tmp_path):
    vault = OnboardingVault(b"p" * 32)
    artifacts = EncryptedArtifactVault(
        tmp_path / "sealed", tmp_path / "temporary", vault=vault
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "package.sqlite3", vault=vault),
        OnboardingAccess("admin", "admin-1"),
        artifact_vault=artifacts,
    )
    source = tmp_path / "welcome.pdf"
    _pdf(source)
    field = service.create_intake_field(
        stable_id="employee.preferred_name",
        label="Preferred name",
        field_type="short_text",
        sensitivity="personal",
        aliases=["preferred name"],
    )
    service.create_pdf_mapping(
        document_key="welcome",
        page_number=1,
        rect=(72, 650, 200, 20),
        field_id=field.id,
        required=True,
    )
    package = service.publish_document_package(
        service.create_document_package_draft(
            package_key="teacher-start",
            school="Palmdale",
            title="Teacher start",
            document_paths=[source],
        ).id
    )
    template = service.publish_task_template(
        service.create_task_template_draft(
            template_key="employment-package",
            school="Palmdale",
            title="Complete employment package",
            owner_role="Director",
            due_offset_days=0,
            package_key=package.package_key,
        ).id
    )
    employee = service.create_employee(
        legal_name="Package Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id,
        title="Complete employment package",
        owner_role="Director",
        due_date="2026-07-20",
        package_version_id=package.id,
        template_key=template.template_key,
        template_version=template.version,
        template_id=template.id,
    )
    submission = service.submit_intake(
        submission_id="submission-1",
        employee_id=employee.id,
        application_id="application-1",
        schema_version=1,
        values={},
    )
    assert submission.status == "attention"
    assert service.store.list_filled_artifacts(submission_id=submission.id) == []
    correction = service.correct_intake_submission(
        submission.id,
        correction_id="submission-1-correction-1",
        values={"employee.preferred_name": "Jordan"},
    )
    assert correction.status == "accepted"

    generated = service.store.list_filled_artifacts(submission_id=correction.id)
    individual = [item for item in generated if item.kind.startswith("individual:")]
    merged = next(item for item in generated if item.kind == "merged")
    manifest = next(item for item in generated if item.kind == "manifest")

    assert len(individual) == 1
    assert all((tmp_path / "sealed" / f"{item.id}.obv").read_bytes()[:5] != b"%PDF-" for item in generated)
    opened = service.open_filled_artifact(
        employee_id=employee.id,
        artifact_id=merged.id,
        suffix=".pdf",
    )
    assert opened.read_bytes().startswith(b"%PDF-")
    service.close_filled_artifact(opened)
    assert not opened.exists()
    assert list((tmp_path / "temporary").iterdir()) == []
    with pytest.raises(ValueError, match="sensitive-data confirmation"):
        service.export_filled_artifact(
            employee_id=employee.id,
            artifact_id=merged.id,
            destination=tmp_path / "exported.pdf",
            confirmed_sensitive=False,
        )
    exported = service.export_filled_artifact(
        employee_id=employee.id,
        artifact_id=merged.id,
        destination=tmp_path / "exported.pdf",
        confirmed_sensitive=True,
    )
    assert exported.read_bytes().startswith(b"%PDF-")
    assert any(
        event["action"] == "filled_artifact.exported"
        for event in service.store.list_audit_events(entity_id=employee.id)
    )

    assert manifest.suffix == ".txt"
    sealed = tmp_path / "sealed" / f"{merged.id}.obv"
    sealed.write_bytes(sealed.read_bytes()[:-1] + b"x")
    with pytest.raises(VaultIntegrityError):
        service.open_filled_artifact(
            employee_id=employee.id,
            artifact_id=merged.id,
            suffix=".pdf",
        )
