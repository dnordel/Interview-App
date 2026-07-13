from datetime import date
from pathlib import Path

import pytest

from scoring_reporting import (
    OfferInput,
    OfferLetterService,
    OfferTemplateError,
    build_school_offer_filename,
    next_available_offer_path,
)


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


def test_offer_replacements_include_candidate_title() -> None:
    data = OfferInput(
        first_name="Tatiana",
        last_name="Zuluaga",
        city="Palmdale",
        position="Teacher",
        start_date=date(2026, 7, 27),
        start_time_12h="09:30 AM",
        end_time_12h="06:30 PM",
        hourly_pay=24.0,
        hours=40,
        created_on=date(2026, 7, 13),
        title="Ms.",
    )

    replacements = OfferLetterService.build_replacements(data)

    assert replacements["[Title]"] == "Ms."


@pytest.mark.parametrize(
    ("school", "expected"),
    [
        ("Palmdale", "Launch Pad Learning PMD Offer of Employment to Tatiana Zuluaga.docx"),
        ("North Long Beach", "Launch Pad Learning NLB Offer of Employment to Tatiana Zuluaga.docx"),
        ("Hawthorne", "Preschool Partners, LLC Offer of Employment to Tatiana Zuluaga.docx"),
    ],
)
def test_school_offer_filename_uses_school_legal_name(school: str, expected: str) -> None:
    assert build_school_offer_filename(school, "Tatiana Zuluaga") == expected


def test_next_available_offer_path_preserves_existing_revisions(tmp_path: Path) -> None:
    filename = "Launch Pad Learning PMD Offer of Employment to Tatiana Zuluaga.docx"
    (tmp_path / filename).touch()
    (tmp_path / "Launch Pad Learning PMD Offer of Employment to Tatiana Zuluaga (2).docx").touch()

    assert next_available_offer_path(tmp_path, filename).name == (
        "Launch Pad Learning PMD Offer of Employment to Tatiana Zuluaga (3).docx"
    )
