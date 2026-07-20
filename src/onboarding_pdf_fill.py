from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from onboarding_store import IntakeField, PdfFieldMapping


@dataclass(frozen=True)
class PdfFillResult:
    output_path: Path
    manifest_path: Path
    output_sha256: str
    required_signatures: tuple[str, ...]


@dataclass(frozen=True)
class AcroFormField:
    name: str
    field_type: str
    page_number: int
    rect: tuple[float, float, float, float]


class PdfFillEngine:
    """Flatten typed intake values into static PDF mappings."""

    def __init__(self, *, minimum_font_size: float = 6.0) -> None:
        if float(minimum_font_size) <= 0:
            raise ValueError("Minimum PDF font size must be positive.")
        self.minimum_font_size = float(minimum_font_size)

    def fill_document(
        self,
        *,
        source_path: Path,
        output_path: Path,
        mappings: list[PdfFieldMapping],
        fields: dict[str, IntakeField],
        values: dict[str, object],
        manifest_path: Path,
    ) -> PdfFillResult:
        source = Path(source_path)
        if not source.is_file():
            raise ValueError("Source PDF was not found.")
        reader = PdfReader(source)
        signatures: list[str] = []
        mappings_by_page: dict[int, list[PdfFieldMapping]] = {}
        for mapping in mappings:
            if mapping.field_id not in fields:
                raise ValueError(f"PDF mapping references unknown field: {mapping.field_id}")
            if mapping.page_number > len(reader.pages):
                raise ValueError("PDF mapping page is outside the source document.")
            mappings_by_page.setdefault(mapping.page_number, []).append(mapping)
        writer = PdfWriter()
        for page_number, page in enumerate(reader.pages, start=1):
            page_mappings = mappings_by_page.get(page_number, [])
            if page_mappings:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                overlay, page_signatures = self._overlay_page(
                    width=width,
                    height=height,
                    mappings=page_mappings,
                    fields=fields,
                    values=values,
                )
                signatures.extend(page_signatures)
                page.merge_page(PdfReader(overlay).pages[0])
            writer.add_page(page)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.parent / f".{output.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as file:
                writer.write(file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        output_hash = _sha256_file(output)
        manifest = Path(manifest_path)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest_payload = {
            "schema_version": 1,
            "source_sha256": _sha256_file(source),
            "output_sha256": output_hash,
            "required_signatures": sorted(set(signatures)),
            "mapping_ids": [mapping.id for mapping in mappings],
        }
        _write_json_atomic(manifest, manifest_payload)
        return PdfFillResult(
            output_path=output,
            manifest_path=manifest,
            output_sha256=output_hash,
            required_signatures=tuple(manifest_payload["required_signatures"]),
        )

    def _overlay_page(
        self,
        *,
        width: float,
        height: float,
        mappings: list[PdfFieldMapping],
        fields: dict[str, IntakeField],
        values: dict[str, object],
    ) -> tuple[io.BytesIO, list[str]]:
        buffer = io.BytesIO()
        document = canvas.Canvas(buffer, pagesize=(width, height))
        signatures: list[str] = []
        for mapping in mappings:
            field = fields[mapping.field_id]
            if field.field_type in {"signature", "initials"}:
                signatures.append(field.stable_id)
                continue
            value = values.get(field.stable_id)
            if value is None or value == "":
                if mapping.required:
                    raise ValueError(f"Required PDF value is missing: {field.label}")
                continue
            text = _formatted_value(value, mapping.formatting_json)
            self._draw_text(document, mapping, text)
        document.save()
        buffer.seek(0)
        return buffer, signatures

    def merge_documents(
        self,
        *,
        document_paths: list[Path],
        output_path: Path,
        manifest_path: Path,
    ) -> PdfFillResult:
        if not document_paths:
            raise ValueError("Merged PDF requires at least one document.")
        writer = PdfWriter()
        source_hashes: list[str] = []
        for path_value in document_paths:
            path = Path(path_value).resolve(strict=True)
            if path.suffix.casefold() != ".pdf" or not path.is_file():
                raise ValueError("Merged document input must be a PDF file.")
            reader = PdfReader(path)
            for page in reader.pages:
                writer.add_page(page)
            source_hashes.append(_sha256_file(path))
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.parent / f".{output.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as file:
                writer.write(file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        output_hash = _sha256_file(output)
        manifest = Path(manifest_path)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            manifest,
            {
                "schema_version": 1,
                "source_sha256": source_hashes,
                "output_sha256": output_hash,
                "required_signatures": [],
            },
        )
        return PdfFillResult(
            output_path=output,
            manifest_path=manifest,
            output_sha256=output_hash,
            required_signatures=(),
        )

    def _draw_text(self, document: canvas.Canvas, mapping: PdfFieldMapping, text: str) -> None:
        x, y, box_width, box_height = mapping.rect
        font_size = float(mapping.font_size)
        if not mapping.multiline:
            while font_size > self.minimum_font_size and stringWidth(text, mapping.font_name, font_size) > box_width:
                font_size = max(self.minimum_font_size, font_size - 0.5)
            if stringWidth(text, mapping.font_name, font_size) > box_width or font_size > box_height:
                raise ValueError(f"PDF value overflows mapping: {mapping.id}")
            document.setFont(mapping.font_name, font_size)
            if mapping.alignment == "right":
                document.drawRightString(x + box_width, y, text)
            elif mapping.alignment == "center":
                document.drawCentredString(x + (box_width / 2), y, text)
            else:
                document.drawString(x, y, text)
            return
        lines = text.splitlines() or [text]
        line_height = font_size * 1.2
        while font_size > self.minimum_font_size and (len(lines) * line_height > box_height or any(stringWidth(line, mapping.font_name, font_size) > box_width for line in lines)):
            font_size = max(self.minimum_font_size, font_size - 0.5)
            line_height = font_size * 1.2
        if len(lines) * line_height > box_height or any(stringWidth(line, mapping.font_name, font_size) > box_width for line in lines):
            raise ValueError(f"PDF value overflows mapping: {mapping.id}")
        text_object = document.beginText(x, y + box_height - font_size)
        text_object.setFont(mapping.font_name, font_size)
        text_object.setLeading(line_height)
        for line in lines:
            text_object.textLine(line)
        document.drawText(text_object)


def _formatted_value(value: object, formatting_json: str) -> str:
    formatting = json.loads(str(formatting_json or "{}"))
    return format_pdf_value(value, formatting)


def format_pdf_value(value: object, formatting: dict[str, object]) -> str:
    text = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
    choices = formatting.get("choice_values")
    if isinstance(choices, dict) and text in choices:
        text = str(choices[text])
    if isinstance(value, bool) and ("true_value" in formatting or "false_value" in formatting):
        text = str(formatting.get("true_value" if value else "false_value", ""))
    date_pattern = str(formatting.get("date_pattern") or "").strip()
    if date_pattern:
        parsed = date.fromisoformat(text)
        replacements = {
            "YYYY": f"{parsed.year:04d}",
            "MM": f"{parsed.month:02d}",
            "DD": f"{parsed.day:02d}",
        }
        for token, replacement in replacements.items():
            date_pattern = date_pattern.replace(token, replacement)
        text = date_pattern
    mask = str(formatting.get("mask") or "").strip().casefold()
    digits = "".join(character for character in text if character.isdigit())
    if mask == "phone" and len(digits) == 10:
        text = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif mask == "ssn" and len(digits) == 9:
        text = f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    casing = str(formatting.get("casing", "")).casefold()
    if casing == "upper":
        text = text.upper()
    elif casing == "lower":
        text = text.lower()
    elif casing == "title":
        text = text.title()
    return text


def detect_acroform_fields(source_path: Path) -> tuple[AcroFormField, ...]:
    source = Path(source_path).resolve(strict=True)
    if source.suffix.casefold() != ".pdf" or not source.is_file():
        raise ValueError("AcroForm source must be a PDF file.")
    reader = PdfReader(source)
    detected: list[AcroFormField] = []
    type_names = {"/Tx": "text", "/Btn": "button", "/Ch": "choice", "/Sig": "signature"}
    for page_number, page in enumerate(reader.pages, start=1):
        annotations = page.get("/Annots", ())
        for reference in annotations:
            annotation = reference.get_object()
            if str(annotation.get("/Subtype", "")) != "/Widget":
                continue
            parent = annotation.get("/Parent")
            parent_object = parent.get_object() if parent is not None else {}
            name = str(annotation.get("/T") or parent_object.get("/T") or "").strip()
            rect_value = annotation.get("/Rect")
            if not name or rect_value is None or len(rect_value) != 4:
                continue
            x1, y1, x2, y2 = (float(value) for value in rect_value)
            field_type = str(annotation.get("/FT") or parent_object.get("/FT") or "")
            detected.append(
                AcroFormField(
                    name=name,
                    field_type=type_names.get(field_type, field_type.removeprefix("/").casefold() or "unknown"),
                    page_number=page_number,
                    rect=(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)),
                )
            )
    return tuple(detected)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
