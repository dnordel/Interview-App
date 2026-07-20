from __future__ import annotations

import json

from reportlab.pdfgen import canvas

from pypdf import PdfReader, PdfWriter

from onboarding_pdf_fill import PdfFillEngine, detect_acroform_fields, format_pdf_value
from onboarding_store import IntakeField, PdfFieldMapping


def test_fill_engine_flattens_static_mapping_and_writes_hashed_manifest(tmp_path):
    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as file:
        writer.write(file)
    field = IntakeField(
        id="field-1",
        stable_id="employee.preferred_name",
        label="Preferred name",
        aliases=(),
        field_type="short_text",
        sensitivity="personal",
        validation_json="{}",
        help_text="",
        options=(),
        version=1,
    )
    mapping = PdfFieldMapping(
        id="mapping-1",
        document_key="welcome",
        page_number=1,
        rect=(72, 650, 200, 20),
        field_id=field.id,
        required=True,
        font_name="Helvetica",
        font_size=12,
        alignment="left",
        multiline=False,
        formatting_json="{}",
    )

    result = PdfFillEngine().fill_document(
        source_path=source,
        output_path=tmp_path / "filled.pdf",
        mappings=[mapping],
        fields={field.id: field},
        values={field.stable_id: "Jordan"},
        manifest_path=tmp_path / "manifest.json",
    )

    assert len(PdfReader(result.output_path).pages) == 1
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_sha256"] == result.output_sha256
    assert manifest["required_signatures"] == []


def test_detects_acroform_fields_and_merges_filled_documents(tmp_path):
    form = tmp_path / "form.pdf"
    document = canvas.Canvas(str(form), pagesize=(612, 792))
    document.acroForm.textfield(name="employee_name", x=72, y=650, width=200, height=20)
    document.showPage()
    document.save()

    detected = detect_acroform_fields(form)

    assert detected[0].name == "employee_name"
    assert detected[0].page_number == 1
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    for path in (first, second):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with path.open("wb") as file:
            writer.write(file)
    result = PdfFillEngine().merge_documents(
        document_paths=[first, second],
        output_path=tmp_path / "merged.pdf",
        manifest_path=tmp_path / "merged-manifest.json",
    )
    assert len(PdfReader(result.output_path).pages) == 2
    assert json.loads(result.manifest_path.read_text(encoding="utf-8"))["output_sha256"] == result.output_sha256


def test_pdf_value_formatting_supports_dates_masks_casing_and_choice_values():
    assert format_pdf_value("2026-07-20", {"date_pattern": "MM/DD/YYYY"}) == "07/20/2026"
    assert format_pdf_value("3105551212", {"mask": "phone"}) == "(310) 555-1212"
    assert format_pdf_value("123456789", {"mask": "ssn"}) == "123-45-6789"
    assert format_pdf_value("jordan lee", {"casing": "upper"}) == "JORDAN LEE"
    assert format_pdf_value(True, {"true_value": "Yes", "false_value": "No"}) == "Yes"
    assert format_pdf_value("teacher", {"choice_values": {"teacher": "T"}}) == "T"
