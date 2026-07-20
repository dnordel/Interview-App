from pathlib import Path

from platform_services import validate_existing_file_path


def test_validate_existing_file_path_returns_file_for_valid_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / 'guide.pdf'
    pdf_path.write_text('pdf', encoding='utf-8')

    resolved_path, reason = validate_existing_file_path(str(pdf_path), allowed_suffixes={'.pdf'})

    assert resolved_path == str(pdf_path)
    assert reason == ''


def test_validate_existing_file_path_rejects_directory(tmp_path: Path) -> None:
    resolved_path, reason = validate_existing_file_path(str(tmp_path), allowed_suffixes={'.pdf'})

    assert resolved_path == ''
    assert reason == 'not_file'


def test_validate_existing_file_path_rejects_missing_path(tmp_path: Path) -> None:
    resolved_path, reason = validate_existing_file_path(str(tmp_path / 'missing.pdf'), allowed_suffixes={'.pdf'})

    assert resolved_path == ''
    assert reason == 'missing'


def test_validate_existing_file_path_allows_empty_path() -> None:
    resolved_path, reason = validate_existing_file_path('', allowed_suffixes={'.pdf'})

    assert resolved_path == ''
    assert reason == 'empty'
