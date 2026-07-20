from pathlib import Path
import sqlite3
import zipfile

import pytest

from onboarding_attachments import validate_task_attachment
from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore


def _task_service(tmp_path: Path, scanner):
    store = OnboardingStore(tmp_path / "attachments.sqlite3")
    service = OnboardingService(
        store,
        OnboardingAccess(role="admin", actor="owner"),
        attachment_scanner=scanner,
    )
    employee = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id,
        title="Upload documents",
        owner_role="Director",
        due_date="2026-07-15",
    )
    return store, service, task


def test_attachment_is_signature_checked_scanned_and_encrypted(tmp_path: Path) -> None:
    source = tmp_path / "identity.pdf"
    source.write_bytes(b"%PDF-1.4\nprivate attachment")
    store, service, task = _task_service(tmp_path, lambda _path: "unavailable")

    attachment = service.add_task_attachment(task.id, source)

    assert attachment.name == "identity.pdf"
    assert attachment.scan_status == "unavailable"
    assert attachment.warning == "Windows Defender scan unavailable."
    assert service.read_task_attachment(attachment.id) == source.read_bytes()
    with sqlite3.connect(store.path) as connection:
        raw = connection.execute(
            "SELECT name_encrypted, content_encrypted FROM onboarding_task_attachments WHERE id = ?",
            (attachment.id,),
        ).fetchone()
    assert b"identity.pdf" not in raw[0]
    assert b"private attachment" not in raw[1]


def test_flagged_or_invalid_attachment_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "identity.pdf"
    source.write_bytes(b"%PDF-1.4\ncontent")
    _store, service, task = _task_service(tmp_path, lambda _path: "flagged")
    with pytest.raises(ValueError, match="flagged"):
        service.add_task_attachment(task.id, source)

    executable = tmp_path / "run.exe"
    executable.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="not allowed"):
        validate_task_attachment(executable)


def test_macro_enabled_office_archive_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/vbaProject.bin", b"macro")

    with pytest.raises(ValueError, match="macro"):
        validate_task_attachment(source)
