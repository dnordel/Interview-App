from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from docx_compat import Document

from app_content import sanitize_filename


POSITION_OPTIONS = [
    "Teacher",
    "Lead Teacher Floater/Teacher",
    "Cook",
    "Assistant Director",
    "Director",
    "Site Supervisor",
]


@dataclass(frozen=True)
class OfferInput:
    first_name: str
    last_name: str
    city: str
    position: str
    start_date: date
    start_time_12h: str
    end_time_12h: str
    hourly_pay: float
    hours: int
    created_on: date

    @property
    def pto(self) -> int:
        return int(2 * self.hours)

    @property
    def pto2(self) -> int:
        return int(4 * self.hours)

    @property
    def offer_deadline(self) -> date:
        return self.created_on + timedelta(days=3)


class OfferTemplateError(ValueError):
    pass


class OfferLetterService:
    ALLOWED_TEMPLATE_SUFFIXES = {".docx", ".docm"}
    PLACEHOLDER_ORDER = [
        "[First Name]",
        "[Last Name]",
        "[City]",
        "[Position]",
        "[StartDate]",
        "[StartTime]",
        "[EndTime]",
        "[HourlyPay]",
        "[Hours]",
        "[PTO]",
        "[PTO2]",
        "[OfferDeadline]",
    ]

    @staticmethod
    def classify_employment_type(hours: int) -> str:
        return "full_time" if hours >= 30 else "part_time"

    @classmethod
    def validate_template_path(cls, path: Path) -> None:
        if path.suffix.lower() not in cls.ALLOWED_TEMPLATE_SUFFIXES:
            raise OfferTemplateError("Offer template must be a .docx or .docm file.")
        if not path.exists() or not path.is_file():
            raise OfferTemplateError(f"Template not found: {path}")

    @classmethod
    def build_replacements(cls, data: OfferInput) -> dict[str, str]:
        return {
            "[First Name]": data.first_name.strip(),
            "[Last Name]": data.last_name.strip(),
            "[City]": data.city.strip(),
            "[Position]": data.position.strip(),
            "[StartDate]": data.start_date.strftime("%m/%d/%Y"),
            "[StartTime]": data.start_time_12h.strip(),
            "[EndTime]": data.end_time_12h.strip(),
            "[HourlyPay]": f"{data.hourly_pay:.2f}",
            "[Hours]": str(data.hours),
            "[PTO]": str(data.pto),
            "[PTO2]": str(data.pto2),
            "[OfferDeadline]": data.offer_deadline.strftime("%m/%d/%Y"),
        }

    @classmethod
    def render_offer(cls, template_path: Path, output_path: Path, data: OfferInput) -> Path:
        cls.validate_template_path(template_path)
        replacements = cls.build_replacements(data)

        doc = Document(str(template_path))
        cls._replace_document_text(doc, replacements)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        return output_path

    @classmethod
    def _replace_document_text(cls, doc: Document, replacements: dict[str, str]) -> None:
        for paragraph in doc.paragraphs:
            cls._replace_in_paragraph(paragraph, replacements)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        cls._replace_in_paragraph(paragraph, replacements)

    @staticmethod
    def _replace_in_paragraph(paragraph: Any, replacements: dict[str, str]) -> None:
        if not paragraph.runs:
            return
        text = "".join(run.text for run in paragraph.runs)
        for token, value in replacements.items():
            text = text.replace(token, value)
        if not paragraph.runs:
            return
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""


def build_offer_filename(first_name: str, last_name: str, created_on: date) -> str:
    date_part = created_on.strftime("%Y-%m-%d")
    name_part = sanitize_filename(f"{first_name.strip()}_{last_name.strip()}")
    return f"{date_part} - Offer - {name_part}.docx"


def parse_clock_12h(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%I:%M %p")
