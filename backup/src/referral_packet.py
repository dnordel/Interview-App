from __future__ import annotations

from pathlib import Path
from typing import Final

REQUIRED_PACKET_DOCS: Final[tuple[tuple[str, str], ...]] = (
    ("resume_path", "Resume"),
    ("interview_notes_document_path", "Interview notes document"),
)

OPTIONAL_PACKET_DOCS: Final[tuple[tuple[str, str], ...]] = (
    ("interview_notes_path", "Interview notes (legacy)"),
    ("transcript_path", "Transcript"),
)

ALL_PACKET_DOCS: Final[tuple[tuple[str, str], ...]] = REQUIRED_PACKET_DOCS + OPTIONAL_PACKET_DOCS

ALLOWED_DOC_EXTENSIONS: Final[set[str]] = {
    ".doc",
    ".docx",
    ".pdf",
    ".txt",
    ".rtf",
}


def normalize_referral_packet(packet: dict[str, str] | None) -> dict[str, str]:
    source = packet or {}
    normalized = {key: "" for key, _ in ALL_PACKET_DOCS}
    for key, _ in ALL_PACKET_DOCS:
        normalized[key] = str(source.get(key, "") or "").strip()

    canonical_notes = normalized["interview_notes_document_path"]
    if not canonical_notes:
        canonical_notes = normalized["interview_notes_path"] or normalized["transcript_path"]
        normalized["interview_notes_document_path"] = canonical_notes
    if canonical_notes and not normalized["interview_notes_path"]:
        normalized["interview_notes_path"] = canonical_notes

    return normalized


def missing_required_docs(packet: dict[str, str] | None) -> list[str]:
    normalized = normalize_referral_packet(packet)
    missing: list[str] = []
    for key, label in REQUIRED_PACKET_DOCS:
        if normalized.get(key, ""):
            continue
        missing.append(label)
    return missing


def validate_referral_packet(packet: dict[str, str] | None) -> tuple[bool, list[str]]:
    missing = missing_required_docs(packet)
    return (len(missing) == 0), missing


def is_supported_document_path(path_text: str) -> bool:
    suffix = Path(path_text).suffix.lower()
    return suffix in ALLOWED_DOC_EXTENSIONS
