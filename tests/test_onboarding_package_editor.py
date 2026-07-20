import pytest

from onboarding_package_editor import DocumentPackageDraftEditor


def test_package_editor_adds_replaces_removes_and_reorders_validated_pdfs(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    replacement = tmp_path / "replacement.pdf"
    for path in (first, second, replacement):
        path.write_bytes(b"%PDF-1.4\n%%EOF")
    editor = DocumentPackageDraftEditor()

    editor.add(first)
    editor.add(second)
    editor.replace(0, replacement)
    editor.move(1, 0)
    removed = editor.remove(1)

    assert editor.paths == (second.resolve(),)
    assert removed == replacement.resolve()
    with pytest.raises(ValueError, match="PDF"):
        editor.add(tmp_path / "not-pdf.txt")
