from pathlib import Path

import pytest

from scoring_reporting import OfferLetterService, OfferTemplateError


@pytest.mark.parametrize("suffix", [".docx", ".docm", ".DOCM"])
def test_validate_template_path_accepts_allowed_word_templates(tmp_path: Path, suffix: str):
    template_path = tmp_path / f"template{suffix}"
    template_path.write_bytes(b"placeholder")

    OfferLetterService.validate_template_path(template_path)


def test_validate_template_path_rejects_unsupported_template_type(tmp_path: Path):
    template_path = tmp_path / "template.txt"
    template_path.write_text("not a word file", encoding="utf-8")

    with pytest.raises(OfferTemplateError, match=r"\.docx or \.docm"):
        OfferLetterService.validate_template_path(template_path)
