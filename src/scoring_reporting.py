from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from importlib import import_module
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from types import ModuleType
from typing import Any, Final, Iterable, Optional
from urllib import error, request
from urllib.parse import quote, urlparse

from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
import yaml

from platform_services import Document, sanitize_filename


TITLE_OPTIONS: tuple[str, str] = ("Mr.", "Ms.")
DEFAULT_CANDIDATE_TITLE = "Ms."

CANONICAL_DEGREE_TYPES: tuple[str, ...] = (
    "AA",
    "AS",
    "BA",
    "BS",
    "MA",
    "MS",
    "MBA",
    "PhD",
    "EdD",
)

_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
}

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

PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}|\[([A-Za-z_][A-Za-z0-9_]*)\]")
UNKNOWN_PLACEHOLDER_PRESERVE = "preserve"
UNKNOWN_PLACEHOLDER_EMPTY = "empty"
UNKNOWN_PLACEHOLDER_ERROR = "error"
UNKNOWN_PLACEHOLDER_POLICIES = {
    UNKNOWN_PLACEHOLDER_PRESERVE,
    UNKNOWN_PLACEHOLDER_EMPTY,
    UNKNOWN_PLACEHOLDER_ERROR,
}


@dataclass(frozen=True)
class PlaceholderMeta:
    key: str
    label: str
    description: str

    @property
    def token(self) -> str:
        return f"[{self.key}]"

    @property
    def alternate_token(self) -> str:
        return f"{{{self.key}}}"


PLACEHOLDER_METADATA: dict[str, PlaceholderMeta] = {
    "candidate_name": PlaceholderMeta("candidate_name", "Candidate name", "Interview candidate full name."),
    "first_name": PlaceholderMeta("first_name", "First name", "Candidate or employee first name."),
    "last_name": PlaceholderMeta("last_name", "Last name", "Candidate or employee last name."),
    "school": PlaceholderMeta("school", "School", "School name tied to the candidate or reminder run."),
    "track": PlaceholderMeta("track", "Track", "Interview track selected for the candidate."),
    "interview_date": PlaceholderMeta("interview_date", "Interview date", "Interview date value for the candidate."),
    "offer_path": PlaceholderMeta("offer_path", "Offer file path", "Generated offer file path, when available."),
    "employee_summary": PlaceholderMeta("employee_summary", "Employees summary", "Distinct employee names in the reminder batch."),
    "task_summary": PlaceholderMeta("task_summary", "Task summary", "Rendered task line items for reminder/escalation emails."),
    "due_date_summary": PlaceholderMeta("due_date_summary", "Due date summary", "Comma-separated due date list for reminder/escalation emails."),
    "count": PlaceholderMeta("count", "Count", "Number of reminder items in the current batch."),
}

PLACEHOLDERS_BY_CONTEXT: dict[str, tuple[str, ...]] = {
    "interview": ("candidate_name", "first_name", "last_name", "school", "track", "interview_date"),
    "director": ("candidate_name", "first_name", "last_name", "school", "track", "interview_date"),
    "offer": ("candidate_name", "first_name", "last_name", "school", "track", "interview_date", "offer_path"),
    "welcome": ("candidate_name", "first_name", "last_name", "school", "track", "interview_date", "offer_path"),
    "onboarding_reminder": ("employee_summary", "first_name", "last_name", "task_summary", "due_date_summary", "school", "count"),
    "escalation": ("employee_summary", "first_name", "last_name", "task_summary", "due_date_summary", "school", "count"),
}


def normalize_candidate_title(value: Any) -> str:
    text = str(value or "").strip()
    if text in TITLE_OPTIONS:
        return text
    return DEFAULT_CANDIDATE_TITLE


@dataclass
class CandidateQualification:
    has_degree: bool | None = None
    degree_type: str = ""
    degree_in_ece: bool = False
    ece_units_completed: int | None = None
    infant_toddler_class_completed: bool = False
    total_units_completed: int | None = None
    years_experience: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_degree": self.has_degree,
            "degree_type": self.degree_type,
            "degree_in_ece": self.degree_in_ece,
            "ece_units_completed": self.ece_units_completed,
            "infant_toddler_class_completed": self.infant_toddler_class_completed,
            "total_units_completed": self.total_units_completed,
            "years_experience": self.years_experience,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateQualification":
        has_degree_raw = payload.get("has_degree", None)
        has_degree = has_degree_raw if isinstance(has_degree_raw, bool) else None
        degree_type = normalize_degree_type(payload.get("degree_type", ""))
        degree_in_ece = bool(payload.get("degree_in_ece", False))
        ece_units_completed = coerce_non_negative_int(payload.get("ece_units_completed"))
        infant_toddler_class_completed = bool(payload.get("infant_toddler_class_completed", False))
        total_units_completed = coerce_non_negative_int(payload.get("total_units_completed"))
        years_experience = coerce_non_negative_int(payload.get("years_experience"))
        return cls(
            has_degree=has_degree,
            degree_type=degree_type,
            degree_in_ece=degree_in_ece,
            ece_units_completed=ece_units_completed,
            infant_toddler_class_completed=infant_toddler_class_completed,
            total_units_completed=total_units_completed,
            years_experience=years_experience,
        )


def normalize_degree_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in CANONICAL_DEGREE_TYPES:
        return text
    return ""


def coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def validate_candidate_qualification(
    has_degree_raw: str,
    degree_type_raw: str,
    degree_in_ece: bool,
    ece_units_raw: str,
    total_units_raw: str,
    infant_toddler_class_completed: bool,
    years_experience_raw: str,
) -> tuple[bool, str, CandidateQualification]:
    has_degree = parse_yes_no(has_degree_raw)
    if has_degree is None:
        return False, "Please confirm whether the candidate has a degree.", CandidateQualification()

    ece_units_completed = coerce_non_negative_int(ece_units_raw)
    if not degree_in_ece and ece_units_completed is None:
        return False, "ECE units completed is required and must be a non-negative whole number unless the degree is in ECE.", CandidateQualification()

    degree_type = ""
    total_units_completed: int | None = None

    if has_degree:
        degree_type = normalize_degree_type(degree_type_raw)
        if not degree_type:
            allowed = ", ".join(CANONICAL_DEGREE_TYPES)
            return False, f"Degree type is required and must be one of: {allowed}.", CandidateQualification()
    else:
        total_units_completed = coerce_non_negative_int(total_units_raw)
        if total_units_completed is None:
            return False, "Total units completed is required when no degree is reported.", CandidateQualification()

    years_experience = coerce_non_negative_int(years_experience_raw)
    if years_experience is None:
        return False, "Years of experience is required and must be a non-negative whole number.", CandidateQualification()

    qualification = CandidateQualification(
        has_degree=has_degree,
        degree_type=degree_type,
        degree_in_ece=degree_in_ece,
        ece_units_completed=ece_units_completed,
        infant_toddler_class_completed=infant_toddler_class_completed,
        total_units_completed=total_units_completed,
        years_experience=years_experience,
    )
    return True, "", qualification


def parse_yes_no(value: str) -> bool | None:
    text = str(value or "").strip().lower()
    if text == "yes":
        return True
    if text == "no":
        return False
    return None


def sanitize_email_subject(subject: str) -> str:
    text = str(subject or "")
    return text.replace("\r", " ").replace("\n", " ").strip()


def is_valid_email_address(value: str) -> bool:
    return bool(_EMAIL_PATTERN.match(str(value or "").strip()))


def sender_email_error_reason(value: str) -> str | None:
    email = str(value or "").strip()
    if not email:
        return "missing"
    if not is_valid_email_address(email):
        return "invalid_format"
    return None


def sender_email_domain_type(value: str) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email:
        return "unknown"
    domain = email.split("@", 1)[1]
    if domain in _PUBLIC_EMAIL_DOMAINS:
        return "public"
    if domain.endswith(".edu"):
        return "education"
    return "organization"


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


def placeholder_meta_for_context(context: str) -> list[PlaceholderMeta]:
    keys = PLACEHOLDERS_BY_CONTEXT.get(context, ())
    return [PLACEHOLDER_METADATA[key] for key in keys if key in PLACEHOLDER_METADATA]


def placeholder_tokens_for_context(context: str) -> list[str]:
    return [meta.token for meta in placeholder_meta_for_context(context)]


def placeholder_picker_options(contexts: Iterable[str]) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for context in contexts:
        for meta in placeholder_meta_for_context(context):
            if meta.key in seen:
                continue
            options.append(f"{meta.token} / {meta.alternate_token} — {meta.label}: {meta.description}")
            seen.add(meta.key)
    return options


def token_from_picker_label(label: str) -> str:
    prefix = label.split(" ", 1)[0].strip()
    token = prefix.split("/", 1)[0].strip()
    return token if token_to_key(token) else ""


def token_to_key(token: str) -> str:
    text = str(token or "").strip()
    if len(text) < 3:
        return ""
    open_char, close_char = text[0], text[-1]
    if (open_char, close_char) not in {("[", "]"), ("{", "}")}:
        return ""
    key = text[1:-1].strip()
    return key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else ""


def _match_key(match: re.Match[str]) -> str:
    return match.group(1) or match.group(2) or ""


def extract_placeholders(text: str) -> set[str]:
    return {_match_key(match) for match in PLACEHOLDER_PATTERN.finditer(str(text or "")) if _match_key(match)}


def missing_placeholder_keys(text: str, values: dict[str, object], context: str) -> list[str]:
    allowlist = set(PLACEHOLDERS_BY_CONTEXT.get(context, ()))
    needed = sorted(extract_placeholders(text) & allowlist)
    return [key for key in needed if not str(values.get(key, "")).strip()]


def find_unknown_placeholders(text: str, context: str) -> set[str]:
    allowlist = set(PLACEHOLDERS_BY_CONTEXT.get(context, ()))
    return {name for name in extract_placeholders(text) if name not in allowlist}


def validate_template_map(templates: dict[str, str], context_by_key: dict[str, str]) -> dict[str, set[str]]:
    unknown_by_key: dict[str, set[str]] = {}
    for key, value in templates.items():
        context = context_by_key.get(key, "")
        unknown = find_unknown_placeholders(value, context)
        if unknown:
            unknown_by_key[key] = unknown
    return unknown_by_key


def render_template(
    template: str,
    values: dict[str, object],
    *,
    context: str | None = None,
    unknown_policy: str = UNKNOWN_PLACEHOLDER_PRESERVE,
) -> str:
    text = str(template or "")
    if not text:
        return ""
    policy = unknown_policy if unknown_policy in UNKNOWN_PLACEHOLDER_POLICIES else UNKNOWN_PLACEHOLDER_PRESERVE
    unknown = find_unknown_placeholders(text, context or "") if context else set()
    if unknown and policy == UNKNOWN_PLACEHOLDER_ERROR:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown placeholders for context '{context}': {names}")

    allowed = set(PLACEHOLDERS_BY_CONTEXT.get(context or "", ()))

    def _replace(match: re.Match[str]) -> str:
        key = _match_key(match)
        if context and key not in allowed:
            if policy == UNKNOWN_PLACEHOLDER_EMPTY:
                return ""
            return match.group(0)
        if key not in values:
            return match.group(0)
        return str(values.get(key, ""))

    return PLACEHOLDER_PATTERN.sub(_replace, text)


def insert_token_into_widget(widget: tk.Misc, token: str) -> bool:
    if isinstance(widget, tk.Text):
        widget.insert(tk.INSERT, token)
        widget.focus_set()
        return True
    if isinstance(widget, ttk.Entry):
        widget.insert(widget.index(tk.INSERT), token)
        widget.focus_set()
        return True
    if isinstance(widget, tk.Entry):
        widget.insert(widget.index(tk.INSERT), token)
        widget.focus_set()
        return True
    return False


def insert_token_into_focused_widget(root: tk.Misc, token: str, allowed_widgets: Iterable[tk.Misc]) -> bool:
    focused = root.focus_get()
    if focused in set(allowed_widgets):
        return insert_token_into_widget(focused, token)
    return False


class ReportingValidationError(ValueError):
    """Raised when a report cannot be scored or exported due to invalid draft data."""


class ScoringEngine:
    """
    Computes:
    - weighted totals
    - percent of max
    - critical trait override flags
    - absolute disqualifier lock
    - final outcome: Hire / Borderline / No Hire
    """

    @staticmethod
    def _coerce_raw_score(value: Any) -> tuple[Optional[int], int]:
        if isinstance(value, int) and value in {1, 2, 3, 4, 5}:
            return value, value
        if isinstance(value, str) and value.isdigit():
            v = int(value)
            if v in {1, 2, 3, 4, 5}:
                return v, v
        return None, 0

    @staticmethod
    def _get_track_config(rubric: dict[str, Any], track_key: Any) -> dict[str, Any]:
        tracks = rubric.get("tracks", {}) or {}
        resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
        track_cfg = tracks[resolved_track_key]
        return track_cfg

    @staticmethod
    def _resolve_track_key_for_scoring(rubric: dict[str, Any], track_key: Any) -> str:
        tracks = rubric.get("tracks", {}) or {}
        if isinstance(track_key, str) and track_key in tracks:
            return track_key
        if tracks:
            return next(iter(tracks))
        raise ReportingValidationError(
            "Invalid track key in draft. This track is missing from the current rubric."
        )

    @staticmethod
    def _calculate_percent(weighted_total: int, denominator: int) -> tuple[Optional[Decimal], float]:
        if denominator <= 0:
            return None, 0.0

        pct = (Decimal(weighted_total) * Decimal("100")) / Decimal(denominator)
        pct_rounded = pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return pct, float(pct_rounded)

    @staticmethod
    def _is_critical_priority(priority: Any) -> bool:
        return isinstance(priority, str) and priority.strip().lower() == "critical"

    @staticmethod
    def evaluate(rubric: dict[str, Any], track_key: Any, trait_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
        track_cfg = ScoringEngine._get_track_config(rubric, resolved_track_key)
        traits = [
            t for t in rubric["traits"]
            if "all" in t["applicable_tracks"] or resolved_track_key in t["applicable_tracks"]
        ]

        trait_q_overrides = rubric.get("trait_question_overrides", {}) or {}

        rows: list[dict[str, Any]] = []
        weighted_total = 0
        weighted_max_possible_included_traits = 0
        skipped_traits_count = 0
        scored_traits_count = 0

        critical_eq_1 = False
        critical_lt_3 = False
        disqualifier_present = False

        for trait in traits:
            tid = trait["id"]
            state = trait_results.get(tid, {}) or {}

            dq = bool(state.get("absolute_disqualifier", False))
            disqualifier_present = disqualifier_present or dq

            raw_display, raw_for_math = ScoringEngine._coerce_raw_score(state.get("raw_score", None))
            skipped = bool(state.get("skipped", False)) or raw_display is None

            weight = int(trait["weight"])
            weighted = 0
            if skipped:
                skipped_traits_count += 1
            else:
                scored_traits_count += 1
                weighted_max_possible_included_traits += 5 * weight
                weighted = raw_for_math * weight
                weighted_total += weighted

            is_critical = ScoringEngine._is_critical_priority(trait.get("priority"))
            has_scored_value = raw_display is not None
            if is_critical and not skipped and has_scored_value:
                if raw_for_math == 1:
                    critical_eq_1 = True
                if raw_for_math < 3:
                    critical_lt_3 = True

            pq = trait_q_overrides.get(tid) or trait["primary_question"]
            model_signal_suggestions = list(state.get("model_signal_suggestions", []) or [])
            selected_signal_ids = list(state.get("selected_signal_ids", []) or [])
            deepseek_advisory = _deepseek_signal_advisory(tid, model_signal_suggestions)
            model_trait_score = state.get("model_trait_score", {}) if isinstance(state.get("model_trait_score"), dict) else {}
            deepseek_raw_score = _coerce_deepseek_raw_score(
                model_trait_score.get("raw_score", state.get("deepseek_raw_score", deepseek_advisory.get("suggested_raw_score")))
            )
            explicit_suggested_raw_score = coerce_raw_score(state.get("suggested_raw_score"))
            suggested_raw_score = deepseek_advisory.get("suggested_raw_score", explicit_suggested_raw_score)
            if suggested_raw_score is None:
                suggested_raw_score = explicit_suggested_raw_score
            final_raw_score = raw_display
            adjustment_reason = str(state.get("adjustment_reason") or "").strip()
            interviewer_adjusted = bool(
                suggested_raw_score is not None
                and final_raw_score is not None
                and int(final_raw_score) != int(suggested_raw_score)
            )
            if explicit_suggested_raw_score is not None and interviewer_adjusted and not adjustment_reason:
                raise ReportingValidationError(
                    f"Trait '{tid}' final raw score differs from suggested raw score but adjustment_reason is missing."
                )
            deepseek_score = (
                suggested_raw_score * weight
                if suggested_raw_score is not None
                else deepseek_raw_score * weight
                if deepseek_raw_score is not None
                else None
            )
            auto_no_hire_signal_ids = list(deepseek_advisory.get("auto_no_hire_signal_ids", []) or [])
            if auto_no_hire_signal_ids:
                disqualifier_present = True

            rows.append(
                {
                    "trait_id": tid,
                    "trait_name": trait["name"],
                    "priority": trait["priority"],
                    "weight": weight,
                    "skipped": skipped,
                    "raw_score": raw_display,
                    "raw_score_math": raw_for_math,
                    "weighted_score": weighted,
                    "question_notes": state.get("question_notes", ""),
                    "trait_notes": state.get("trait_notes", ""),
                    "verbatim_notes": state.get("verbatim_notes", ""),
                    "no_example_after_followups": bool(state.get("no_example_after_followups", False)),
                    "absolute_disqualifier": dq,
                    "primary_question": pq,
                    "system_checkbox_score": weighted,
                    "net_signal_score": deepseek_advisory.get("net_signal_score"),
                    "suggested_raw_score": suggested_raw_score,
                    "final_raw_score": final_raw_score,
                    "interviewer_adjusted": interviewer_adjusted,
                    "adjustment_reason": adjustment_reason,
                    "deepseek_raw_score": deepseek_raw_score,
                    "deepseek_calculated_score": deepseek_score,
                    "deepseek_signal_score": deepseek_score,
                    "auto_no_hire_present": bool(auto_no_hire_signal_ids),
                    "auto_no_hire_signal_ids": auto_no_hire_signal_ids,
                    "auto_no_hire_reasons": list(deepseek_advisory.get("auto_no_hire_reasons", []) or []),
                    "auto_no_hire_quotes": list(deepseek_advisory.get("auto_no_hire_quotes", []) or []),
                    "model_trait_score": model_trait_score,
                    "selected_signal_ids": selected_signal_ids,
                    "model_signal_suggestions": model_signal_suggestions,
                    "model_signal_override": trait_signal_override_state(state),
                }
            )

        configured_max_weighted = int(track_cfg["max_weighted_total"])
        effective_max_weighted = weighted_max_possible_included_traits or configured_max_weighted

        logic_denominator = effective_max_weighted
        pct, percent_of_max = ScoringEngine._calculate_percent(weighted_total, logic_denominator)
        logic_pct, _logic_percent_of_max = ScoringEngine._calculate_percent(weighted_total, logic_denominator)

        percent_label = f"{percent_of_max}%"
        if weighted_max_possible_included_traits == 0:
            percent_label = "N/A (all questions skipped)"

        locked_rule: Optional[str] = None
        pct_for_logic = float(logic_pct) if logic_pct is not None else 0.0

        missing_required_scores = any(
            not row.get("skipped", False) and row.get("raw_score") is None
            for row in rows
        )
        auto_no_hire_present = any(bool(row.get("auto_no_hire_present")) for row in rows)

        if missing_required_scores:
            outcome = "Incomplete"
            locked_rule = "One or more applicable traits are missing final raw scores"
        elif auto_no_hire_present:
            outcome = "No Hire"
            locked_rule = "DeepSeek automatic no-hire signal observed => Immediate NO HIRE"
        elif disqualifier_present:
            outcome = "No Hire"
            locked_rule = "Any Absolute Disqualifier observed => Immediate NO HIRE"
        elif critical_eq_1:
            outcome = "No Hire"
            locked_rule = "Any Critical trait raw score = 1 => Immediate NO HIRE"
        elif critical_lt_3:
            outcome = "No Hire"
            locked_rule = "Any Critical trait raw score < 3 => Cannot assign HIRE"
        elif pct_for_logic >= 80:
            outcome = "Hire"
        elif pct_for_logic >= 65:
            outcome = "Borderline"
        else:
            outcome = "No Hire"

        return {
            "rows": rows,
            "weighted_total": weighted_total,
            "configured_max_weighted_total": configured_max_weighted,
            "max_weighted_total": effective_max_weighted,
            "max_weighted_total_included_traits": weighted_max_possible_included_traits,
            "percent_of_max": percent_of_max,
            "percent_of_max_label": percent_label,
            "skipped_traits_count": skipped_traits_count,
            "scored_traits_count": scored_traits_count,
            "critical_eq_1": critical_eq_1,
            "critical_lt_3": critical_lt_3,
            "disqualifier_present": disqualifier_present,
            "auto_no_hire_present": auto_no_hire_present,
            "locked_rule": locked_rule,
            "outcome": outcome,
        }


def _legacy_deepseek_score(trait_id: str, model_signal_suggestions: list[dict[str, Any]]) -> int | float | None:
    signal_ids = [
        str(item.get("signal_id") or "").strip()
        for item in model_signal_suggestions
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    if not signal_ids:
        return None
    try:
        runtime_bundle = _load_runtime_bundle(DEFAULT_ENGINE_RUNTIME_CONTRACT)
        for trait_definition in runtime_bundle.get("traits", []) or []:
            if canonical_trait_id(trait_definition.get("trait_id")) == canonical_trait_id(trait_id):
                return _score_trait_signal_ids(trait_definition, signal_ids, runtime_bundle=runtime_bundle)
    except (FileNotFoundError, ImportError, KeyError, PermissionError, TypeError, ValueError):
            return None
    return None


def _deepseek_signal_advisory(trait_id: str, model_signal_suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    signal_ids = [
        str(item.get("signal_id") or "").strip()
        for item in model_signal_suggestions
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    if not signal_ids:
        return {
            "net_signal_score": None,
            "suggested_raw_score": None,
            "auto_no_hire_signal_ids": [],
            "auto_no_hire_reasons": [],
            "auto_no_hire_quotes": [],
        }
    try:
        runtime_bundle = _load_runtime_bundle(DEFAULT_ENGINE_RUNTIME_CONTRACT)
        for trait_definition in runtime_bundle.get("traits", []) or []:
            if canonical_trait_id(trait_definition.get("trait_id")) == canonical_trait_id(trait_id):
                return _score_trait_signal_advisory(
                    trait_definition,
                    model_signal_suggestions,
                    runtime_bundle=runtime_bundle,
                )
    except (FileNotFoundError, ImportError, KeyError, PermissionError, TypeError, ValueError):
        return {
            "net_signal_score": None,
            "suggested_raw_score": None,
            "auto_no_hire_signal_ids": [],
            "auto_no_hire_reasons": [],
            "auto_no_hire_quotes": [],
        }
    return {
        "net_signal_score": None,
        "suggested_raw_score": None,
        "auto_no_hire_signal_ids": [],
        "auto_no_hire_reasons": [],
        "auto_no_hire_quotes": [],
    }


def _signal_score_to_raw_score(net_signal_score: int | float) -> int:
    if net_signal_score >= 7:
        return 5
    if net_signal_score >= 4:
        return 4
    if net_signal_score >= 1:
        return 3
    if net_signal_score >= -3:
        return 2
    return 1


def _coerce_deepseek_raw_score(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed in VALID_RAW_SCORES:
        return parsed
    return None


class DraftManager:
    """Saves and loads interview drafts as JSON under <base>/drafts."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.drafts_dir = self.base_dir / "drafts"
        self.final_dir = self.base_dir / "final"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)

    def save_draft(self, payload: dict[str, Any]) -> Path:
        candidate = payload.get("candidate", {}).get("name", "Unknown")
        safe = sanitize_filename(candidate or "Unknown")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.drafts_dir / f"draft-{stamp}-{safe}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    def load_draft(self, path: Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)


_EXECUTIVE_SUMMARY_HEADINGS: Final[dict[str, str]] = {
    "recommendation": "recommendation",
    "overall fit": "overall_fit",
    "overall fit summary": "overall_fit",
    "key strengths": "strengths",
    "strengths": "strengths",
    "key concerns": "concerns",
    "key concerns or risks": "concerns",
    "concerns": "concerns",
    "concerns or risks": "concerns",
    "risks": "concerns",
    "role-specific analysis": "role_specific",
    "role specific analysis": "role_specific",
    "role-specific match": "role_specific",
    "role specific match": "role_specific",
    "score pattern analysis": "score_pattern",
    "score pattern": "score_pattern",
    "suggested follow-up questions": "follow_up",
    "suggested follow up questions": "follow_up",
    "follow-up questions": "follow_up",
    "follow up questions": "follow_up",
    "final hiring notes": "final_notes",
    "hiring notes": "final_notes",
}
_EXECUTIVE_SUMMARY_LIST_SECTIONS: Final[set[str]] = {"strengths", "concerns", "follow_up"}


def _clean_executive_summary_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return text.strip(" \t:-")


def _split_executive_summary_heading(line: str) -> tuple[str | None, str]:
    candidate = str(line or "").strip()
    candidate = re.sub(r"^\s*[-*]\s+", "", candidate)
    match = re.match(r"^\*\*(?P<label>[^*]+?)\*\*\s*:?\s*(?P<body>.*)$", candidate)
    if match:
        label = re.sub(r"\s+", " ", match.group("label")).strip().lower()
        section = _EXECUTIVE_SUMMARY_HEADINGS.get(label)
        if section:
            return section, _clean_executive_summary_text(match.group("body"))
    match = re.match(r"^(?P<label>[A-Za-z][A-Za-z -]{2,40})\s*:\s*(?P<body>.*)$", candidate)
    if not match:
        return None, ""
    label = re.sub(r"\s+", " ", match.group("label")).strip().lower()
    section = _EXECUTIVE_SUMMARY_HEADINGS.get(label)
    if not section:
        return None, ""
    return section, _clean_executive_summary_text(match.group("body"))


def _parse_executive_summary_sections(summary: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "recommendation": [],
        "overall_fit": [],
        "strengths": [],
        "concerns": [],
        "role_specific": [],
        "score_pattern": [],
        "follow_up": [],
        "final_notes": [],
        "additional_notes": [],
    }
    current_section = "additional_notes"
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section, body = _split_executive_summary_heading(line)
        if section:
            current_section = section
            if body:
                sections[current_section].append(body)
            continue
        cleaned = _clean_executive_summary_text(line)
        if cleaned:
            sections[current_section].append(cleaned)
    return {key: values for key, values in sections.items() if values}


def _executive_summary_sections_from_structured(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    mapped = {
        "recommendation": value.get("recommendation"),
        "overall_fit": value.get("overall_fit"),
        "role_specific": value.get("role_specific_match"),
        "score_pattern": value.get("score_pattern"),
        "strengths": value.get("key_strengths"),
        "concerns": value.get("key_concerns_or_risks"),
        "follow_up": value.get("suggested_follow_up_questions"),
        "final_notes": value.get("final_hiring_notes"),
    }
    sections: dict[str, list[str]] = {}
    for key, raw_value in mapped.items():
        if isinstance(raw_value, list):
            cleaned_items = [_clean_executive_summary_text(item) for item in raw_value]
            cleaned_items = [item for item in cleaned_items if item]
        else:
            cleaned = _clean_executive_summary_text(raw_value)
            cleaned_items = [cleaned] if cleaned else []
        if cleaned_items:
            sections[key] = cleaned_items
    return sections


class DocxExporter:
    """Exports a finalized interview report to a single .docx file (one per candidate)."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _require_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            raise ReportingValidationError("Draft is missing candidate details; unable to export report.")
        return candidate

    @staticmethod
    def _require_candidate_field(candidate: dict[str, Any], field_name: str) -> str:
        value = candidate.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ReportingValidationError(f"Draft is missing required candidate field: '{field_name}'.")

    @staticmethod
    def _extract_full_candidate_transcript(payload: dict[str, Any]) -> str:
        transcript_segments: list[str] = []
        flow_transcript = payload.get("flow_transcript", []) or []
        for item in flow_transcript:
            if not isinstance(item, dict):
                continue
            tx = str(item.get("candidate_transcript") or "").strip()
            if tx:
                transcript_segments.append(tx)

        if transcript_segments:
            return "\n\n".join(transcript_segments).strip()

        audio_recording = payload.get("audio_recording", {}) or {}
        if isinstance(audio_recording, list):
            for item in audio_recording:
                if not isinstance(item, dict):
                    continue
                tx = str(item.get("candidate_transcript") or "").strip()
                if tx:
                    transcript_segments.append(tx)
        elif isinstance(audio_recording, dict):
            tx = str(audio_recording.get("candidate_transcript") or "").strip()
            if tx:
                transcript_segments.append(tx)

        return "\n\n".join(transcript_segments).strip()

    def export(self, rubric: dict[str, Any], payload: dict[str, Any], scoring: dict[str, Any]) -> Path:
        candidate = self._require_candidate(payload)
        cname = self._require_candidate_field(candidate, "name")
        interview_date = self._require_candidate_field(candidate, "interview_date")
        track_key = self._require_candidate_field(candidate, "track")
        school = candidate.get("school", "")
        qualification = candidate.get("qualification", {}) or {}
        track_cfg = ScoringEngine._get_track_config(rubric, track_key)
        track_label = str(track_cfg.get("label") or track_key)

        body_font = "Arial"
        navy = "1F4E79"
        teal = "0F766E"
        pale_blue = "EAF3F8"
        pale_teal = "E6F4F1"
        pale_green = "EAF6EA"
        pale_yellow = "FFF7D6"
        white = "FFFFFF"
        dark_text = "1F2937"
        content_width_inches = 7.2
        ai_generated_label = "AI-generated"
        ai_suggested_label = "AI-suggested"
        ai_advisory_label = "AI advisory"

        scoring = dict(scoring)
        normalized_scoring_rows: list[dict[str, Any]] = []
        for row in scoring.get("rows", []) or []:
            normalized_row = dict(row)
            if not normalized_row.get("skipped", False) and normalized_row.get("raw_score") is None:
                normalized_row["skipped"] = True
            normalized_scoring_rows.append(normalized_row)
        scoring["rows"] = normalized_scoring_rows
        scoring["skipped_traits_count"] = sum(1 for row in normalized_scoring_rows if row.get("skipped", False))
        scoring["scored_traits_count"] = sum(1 for row in normalized_scoring_rows if not row.get("skipped", False))
        included_max_from_rows = sum(
            5 * int(row.get("weight", 0) or 0)
            for row in normalized_scoring_rows
            if not row.get("skipped", False)
        )
        if included_max_from_rows:
            scoring["max_weighted_total"] = included_max_from_rows
            scoring["max_weighted_total_included_traits"] = included_max_from_rows
            if "weighted_total" in scoring:
                _display_pct, display_percent = ScoringEngine._calculate_percent(int(scoring.get("weighted_total", 0) or 0), included_max_from_rows)
                scoring["percent_of_max"] = display_percent
                scoring["percent_of_max_label"] = f"{display_percent}%"
        if str(scoring.get("locked_rule") or "").startswith("One or more applicable traits are missing final raw scores"):
            scoring["locked_rule"] = None
            thresholds = track_cfg.get("thresholds", {}) or {}
            hire_min = float(thresholds.get("hire_percent_min", 80))
            borderline_min = float(thresholds.get("borderline_percent_min", 65))
            percent = float(scoring.get("percent_of_max", 0) or 0)
            if scoring.get("critical_eq_1") or scoring.get("disqualifier_present"):
                scoring["outcome"] = "No Hire"
            elif percent >= hire_min:
                scoring["outcome"] = "Hire"
            elif percent >= borderline_min:
                scoring["outcome"] = "Borderline"
            else:
                scoring["outcome"] = "No Hire"

        included_rows = [row for row in scoring["rows"] if not row.get("skipped", False)]
        deepseek_values = [row.get("deepseek_calculated_score") for row in included_rows]
        deepseek_total_complete = bool(included_rows) and all(value is not None for value in deepseek_values)
        deepseek_total = sum(int(value) for value in deepseek_values) if deepseek_total_complete else None
        trait_based_values: list[int | None] = []
        for row in included_rows:
            model_trait_score = row.get("model_trait_score", {}) if isinstance(row.get("model_trait_score"), dict) else {}
            raw_score = model_trait_score.get("raw_score")
            if raw_score is None:
                trait_based_values.append(None)
                continue
            trait_based_values.append(int(raw_score) * int(row.get("weight", 0) or 0))
        trait_based_total_complete = bool(included_rows) and all(value is not None for value in trait_based_values)
        trait_based_total = sum(int(value) for value in trait_based_values if value is not None) if trait_based_total_complete else None
        model_suggestion_status = str(payload.get("model_suggestion_status") or "").strip()
        model_scoring_status = str(payload.get("model_scoring_status") or "").strip()
        any_ai_score = any(value is not None for value in deepseek_values)

        doc = Document()
        for section in doc.sections:
            section.top_margin = Inches(0.55)
            section.right_margin = Inches(0.65)
            section.bottom_margin = Inches(0.55)
            section.left_margin = Inches(0.65)

        for style_name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "List Bullet"):
            style = doc.styles[style_name]
            style.font.name = body_font
            style.font.size = Pt(12)
            style.font.color.rgb = RGBColor.from_string(dark_text)
            if style_name.startswith("Heading") or style_name == "Title":
                style.font.bold = True
            if style_name in {"Heading 1", "Heading 2", "Heading 3"}:
                style.font.color.rgb = RGBColor.from_string(navy)
                style.paragraph_format.space_before = Pt(10)
                style.paragraph_format.space_after = Pt(4)
            elif style_name == "List Bullet":
                style.paragraph_format.space_after = Pt(4)
                style.paragraph_format.line_spacing = 1.12
            if style_name == "Title":
                style.font.size = Pt(17)
                style.font.color.rgb = RGBColor.from_string(navy)

        def normalize_paragraph(paragraph: Any, *, size: float | None = 12, color: str | None = dark_text) -> None:
            for run in paragraph.runs:
                run.font.name = body_font
                if size is not None:
                    run.font.size = Pt(size)
                if color is not None:
                    run.font.color.rgb = RGBColor.from_string(color)

        def add_heading(text: str, level: int = 1) -> Any:
            paragraph = doc.add_heading(text, level=level)
            normalize_paragraph(paragraph, size=15 if level == 1 else 13, color=navy)
            return paragraph

        def add_text(
            text: str,
            *,
            style: str | None = None,
            bold: bool = False,
            align: Any = None,
            size: float = 12,
            color: str = dark_text,
        ) -> Any:
            paragraph = doc.add_paragraph(style=style)
            if align is not None:
                paragraph.alignment = align
            paragraph.paragraph_format.space_after = Pt(4)
            paragraph.paragraph_format.line_spacing = 1.12
            run = paragraph.add_run(text)
            run.font.name = body_font
            run.font.size = Pt(size)
            run.font.color.rgb = RGBColor.from_string(color)
            run.bold = bold
            return paragraph

        def add_labeled_text(label: str, text: str, *, space_after: float | None = None) -> Any:
            paragraph = doc.add_paragraph()
            if space_after is not None:
                paragraph.paragraph_format.space_after = Pt(space_after)
            paragraph.paragraph_format.line_spacing = 1.12
            label_run = paragraph.add_run(label)
            label_run.bold = True
            label_run.font.name = body_font
            label_run.font.size = Pt(12)
            label_run.font.color.rgb = RGBColor.from_string(navy)
            value_run = paragraph.add_run(text)
            value_run.font.name = body_font
            value_run.font.size = Pt(12)
            value_run.font.color.rgb = RGBColor.from_string(dark_text)
            return paragraph

        def _replace_child(parent: Any, tag: str, child: Any) -> None:
            for existing in list(parent):
                if existing.tag == tag:
                    parent.remove(existing)
            parent.append(child)

        def set_cell_shading(cell: Any, fill: str) -> None:
            tc_pr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:fill"), fill)
            _replace_child(tc_pr, qn("w:shd"), shd)

        def set_cell_margins(cell: Any, *, top: int = 80, bottom: int = 80, start: int = 120, end: int = 120) -> None:
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_mar = tc_pr.first_child_found_in("w:tcMar")
            if tc_mar is None:
                tc_mar = OxmlElement("w:tcMar")
                tc_pr.append(tc_mar)
            for margin_name, margin_value in {
                "top": top,
                "bottom": bottom,
                "start": start,
                "end": end,
            }.items():
                node = tc_mar.find(qn(f"w:{margin_name}"))
                if node is None:
                    node = OxmlElement(f"w:{margin_name}")
                    tc_mar.append(node)
                node.set(qn("w:w"), str(margin_value))
                node.set(qn("w:type"), "dxa")

        def set_cell_width(cell: Any, width_dxa: int) -> None:
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.first_child_found_in("w:tcW")
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(width_dxa))
            tc_w.set(qn("w:type"), "dxa")

        def repeat_header_row(row: Any) -> None:
            tr_pr = row._tr.get_or_add_trPr()
            tbl_header = OxmlElement("w:tblHeader")
            tbl_header.set(qn("w:val"), "true")
            _replace_child(tr_pr, qn("w:tblHeader"), tbl_header)

        def prevent_row_split(row: Any) -> None:
            tr_pr = row._tr.get_or_add_trPr()
            cant_split = OxmlElement("w:cantSplit")
            _replace_child(tr_pr, qn("w:cantSplit"), cant_split)

        def set_table_geometry(table: Any, widths: list[float], *, indent_dxa: int = 120, prevent_splits: bool = True) -> None:
            width_dxa = [int(width * 1440) for width in widths]
            table.autofit = False
            tbl_pr = table._tbl.tblPr
            tbl_w = OxmlElement("w:tblW")
            tbl_w.set(qn("w:w"), str(sum(width_dxa)))
            tbl_w.set(qn("w:type"), "dxa")
            _replace_child(tbl_pr, qn("w:tblW"), tbl_w)
            tbl_ind = OxmlElement("w:tblInd")
            tbl_ind.set(qn("w:w"), str(indent_dxa))
            tbl_ind.set(qn("w:type"), "dxa")
            _replace_child(tbl_pr, qn("w:tblInd"), tbl_ind)
            tbl_grid = OxmlElement("w:tblGrid")
            for width in width_dxa:
                grid_col = OxmlElement("w:gridCol")
                grid_col.set(qn("w:w"), str(width))
                tbl_grid.append(grid_col)
            for existing in list(table._tbl):
                if existing.tag == qn("w:tblGrid"):
                    table._tbl.remove(existing)
            table._tbl.insert(1, tbl_grid)
            for row in table.rows:
                if prevent_splits:
                    prevent_row_split(row)
                for index, cell in enumerate(row.cells):
                    set_cell_width(cell, width_dxa[min(index, len(width_dxa) - 1)])

        def format_cell(
            cell: Any,
            *,
            bold: bool = False,
            fill: str | None = None,
            color: str = dark_text,
            align: Any | None = None,
        ) -> None:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            if fill:
                set_cell_shading(cell, fill)
            for paragraph in cell.paragraphs:
                if align is not None:
                    paragraph.alignment = align
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.08
                normalize_paragraph(paragraph, size=12, color=color)
                for run in paragraph.runs:
                    run.bold = bold

        def add_box(
            text: str,
            *,
            fill: str = pale_blue,
            bold: bool = False,
            color: str = dark_text,
            allow_split: bool = False,
        ) -> Any:
            table = doc.add_table(rows=1, cols=1)
            table.style = "Table Grid"
            set_table_geometry(table, [content_width_inches], prevent_splits=not allow_split)
            cell = table.rows[0].cells[0]
            cell.text = text
            format_cell(cell, bold=bold, fill=fill, color=color)
            return table

        def compact_transcript_attempts(text: str) -> str:
            raw_text = str(text or "").strip()
            if not raw_text:
                return ""
            pattern = re.compile(r"(?ms)^\[Q\d+\s+Attempt\s+\d+\]\s*\n?(.*?)(?=^\[Q\d+\s+Attempt\s+\d+\]\s*\n?|\Z)")
            matches = [match.group(1).strip() for match in pattern.finditer(raw_text)]
            if not matches:
                return raw_text
            unique_parts: list[str] = []
            seen: set[str] = set()
            for part in matches:
                normalized = re.sub(r"\s+", " ", part).strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                unique_parts.append(part)
            return "\n\n".join(unique_parts).strip()

        def add_key_value_table(
            rows: list[tuple[str, str]],
            *,
            label_fill: str = pale_blue,
            label_color: str = navy,
        ) -> Any:
            table = doc.add_table(rows=0, cols=2)
            table.style = "Table Grid"
            for label, value in rows:
                cells = table.add_row().cells
                cells[0].text = label
                cells[1].text = value
                format_cell(cells[0], bold=True, fill=label_fill, color=label_color)
                format_cell(cells[1], fill=white)
            set_table_geometry(table, [2.2, 5.0])
            return table

        def set_cell_paragraph_text(
            cell: Any,
            values: list[str],
            *,
            numbered: bool = False,
            bulleted: bool = False,
        ) -> None:
            cell.text = ""
            for index, value in enumerate(values):
                paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.line_spacing = 1.08
                if numbered:
                    paragraph.style = "List Number"
                elif bulleted:
                    paragraph.style = "List Bullet"
                text = _clean_executive_summary_text(value)
                run = paragraph.add_run(text)
                run.font.name = body_font
                run.font.size = Pt(12)
                run.font.color.rgb = RGBColor.from_string(dark_text)

        def add_executive_two_column_table(
            left_title: str,
            left_values: list[str],
            right_title: str,
            right_values: list[str],
            *,
            left_fill: str = pale_blue,
            right_fill: str = pale_blue,
            header_color: str = navy,
        ) -> Any:
            table = doc.add_table(rows=2, cols=2)
            table.style = "Table Grid"
            header = table.rows[0].cells
            body = table.rows[1].cells
            header[0].text = left_title
            header[1].text = right_title
            set_cell_paragraph_text(body[0], left_values, bulleted=True)
            set_cell_paragraph_text(body[1], right_values, bulleted=True)
            format_cell(header[0], bold=True, fill=left_fill, color=header_color, align=WD_ALIGN_PARAGRAPH.CENTER)
            format_cell(header[1], bold=True, fill=right_fill, color=header_color, align=WD_ALIGN_PARAGRAPH.CENTER)
            format_cell(body[0], fill=white)
            format_cell(body[1], fill=white)
            set_table_geometry(table, [3.6, 3.6], prevent_splits=False)
            return table

        def add_executive_at_a_glance(sections: dict[str, list[str]]) -> None:
            rows = [
                ("Overall Fit", " ".join(sections.get("overall_fit", []))),
                ("Role-Specific Match", " ".join(sections.get("role_specific", []))),
                ("Score Pattern", " ".join(sections.get("score_pattern", []))),
            ]
            rows = [(label, value) for label, value in rows if value.strip()]
            if rows:
                add_key_value_table(rows, label_fill="263940", label_color="B7D4FF")

        def render_executive_summary(summary: str, structured_sections: Any = None) -> None:
            sections = _executive_summary_sections_from_structured(structured_sections)
            if not sections:
                sections = _parse_executive_summary_sections(summary)
            if not sections:
                return
            add_heading("Executive Summary")
            recommendation = " ".join(sections.get("recommendation", [])).strip()
            if recommendation:
                add_box(f"Recommendation: {recommendation}", fill="3A3100", bold=True, color=white)
            add_executive_at_a_glance(sections)
            strengths = sections.get("strengths", [])
            concerns = sections.get("concerns", [])
            if strengths or concerns:
                add_executive_two_column_table(
                    "Key Strengths",
                    strengths or ["None cited"],
                    "Key Concerns or Risks",
                    concerns or ["None cited"],
                    left_fill="203B23",
                    right_fill="3A3100",
                    header_color="B7D4FF",
                )
            follow_up = sections.get("follow_up", [])
            if follow_up:
                add_text("Suggested Follow-Up Questions", bold=True, color=navy)
                for item in follow_up:
                    add_text(_clean_executive_summary_text(item), style="List Number")
            final_notes = " ".join(sections.get("final_notes", [])).strip()
            if final_notes:
                add_box(f"Final Hiring Notes: {final_notes}", fill="263940", color=white)
            additional_notes = sections.get("additional_notes", [])
            if additional_notes:
                add_text("Additional Notes", bold=True, color=navy)
                for item in additional_notes:
                    add_text(_clean_executive_summary_text(item))

        def signal_label_lookup() -> dict[str, str]:
            labels: dict[str, str] = {}
            metadata = scoring.get("engine_metadata", {}) or {}
            for trait_definition in metadata.get("traits", []) or []:
                if not isinstance(trait_definition, dict):
                    continue
                for signal in iter_trait_schema_signals(trait_definition):
                    signal_id = resolve_trait_signal_selection_id(signal)
                    label = resolve_trait_signal_label(signal, fallback=signal_id)
                    if signal_id and label:
                        labels[signal_id] = label
                    runtime_id = resolve_trait_signal_runtime_id(signal)
                    if runtime_id and label:
                        labels[runtime_id] = label
            return labels

        signal_labels = signal_label_lookup()

        title = add_text(
            "Candidate Interview Decision Brief",
            style="Title",
            align=WD_ALIGN_PARAGRAPH.CENTER,
            size=17,
            color=navy,
        )
        title.runs[0].bold = True

        subtitle_parts = [cname, school, track_label, f"Interview Date: {interview_date}"]
        add_text(
            " | ".join(part for part in subtitle_parts if part),
            bold=True,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            color=teal,
        )

        raw_flow_transcript = payload.get("flow_transcript", []) or []

        def is_intro_flow_item(item: dict[str, Any]) -> bool:
            item_type = str(item.get("type") or "").strip().lower()
            item_id = str(item.get("id") or item.get("trait_id") or "").strip().lower()
            return item_type in {"intro", "intro_script", "introduction"} or item_id in {"intro", "intro_script"}

        flow_transcript = [
            item
            for item in raw_flow_transcript
            if isinstance(item, dict) and not is_intro_flow_item(item)
        ]
        answer_summaries = [
            item
            for item in payload.get("answer_summaries", []) or []
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        ]
        flow_by_index = {
            item.get("flow_index", index): item
            for index, item in enumerate(flow_transcript, start=1)
            if isinstance(item, dict)
        }
        skipped_trait_ids = {
            canonical_trait_id(row.get("trait_id"))
            for row in scoring["rows"]
            if row.get("skipped", False) and canonical_trait_id(row.get("trait_id"))
        }

        def flow_question_for_index(flow_index: Any) -> str:
            item = flow_by_index.get(flow_index, {}) or {}
            return str(item.get("question") or item.get("title") or "").strip()

        def is_scored_answer_summary(item: dict[str, Any]) -> bool:
            flow_item = flow_by_index.get(item.get("flow_index"), {}) or {}
            if not isinstance(flow_item, dict):
                return False
            item_type = str(flow_item.get("type") or "").strip().lower()
            if item_type and item_type != "trait":
                return False
            return scoring_row_for_flow_item(flow_item) is not None

        def row_has_risk(row: dict[str, Any]) -> bool:
            return bool(
                row.get("absolute_disqualifier")
                or row.get("no_example_after_followups")
                or (row.get("raw_score") is not None and row.get("raw_score") <= 2)
                or str(row.get("model_trait_score", {}).get("risks_or_gaps") or "").strip()
            )

        def evidence_status_for_row(row: dict[str, Any]) -> str:
            if str(row.get("verbatim_notes") or "").strip():
                return "Verbatim noted"
            if str(row.get("question_notes") or "").strip() or str(row.get("trait_notes") or "").strip():
                return "Interviewer notes"
            if row.get("model_trait_score", {}).get("evidence_quote"):
                return "AI evidence"
            return "None recorded"

        def scoring_row_for_flow_item(item: dict[str, Any]) -> dict[str, Any] | None:
            item_trait_id = canonical_trait_id(item.get("id") or item.get("trait_id"))
            if item_trait_id:
                for row in scoring["rows"]:
                    row_trait_id = canonical_trait_id(row.get("trait_id"))
                    aliases = {
                        canonical_trait_id(alias)
                        for alias in row.get("trait_aliases", []) or []
                        if canonical_trait_id(alias)
                    }
                    if item_trait_id == row_trait_id or item_trait_id in aliases:
                        return row
            question = str(item.get("question") or "").strip()
            title = str(item.get("title") or "").strip()
            for row in scoring["rows"]:
                if question and question == str(row.get("primary_question") or "").strip():
                    return row
                if title and title == str(row.get("trait_name") or "").strip():
                    return row
            return None

        def scoring_row_for_answer_summary(item: dict[str, Any]) -> dict[str, Any] | None:
            flow_item = flow_by_index.get(item.get("flow_index"), {}) or {}
            if isinstance(flow_item, dict):
                row = scoring_row_for_flow_item(flow_item)
                if row is not None:
                    return row
            return None

        def raw_rating_text(value: Any) -> str:
            return "N/A" if value is None else f"{value}/5"

        def answer_summary_appendix_text(item: dict[str, Any]) -> str:
            evidence = "; ".join(
                str(quote or "").strip()
                for quote in item.get("evidence_quotes", []) or []
                if str(quote or "").strip()
            )
            lines = [
                f"Evidence: {evidence or 'None cited'}",
                f"Rubric alignment: {str(item.get('rubric_alignment') or '').strip() or 'None cited'}",
                f"Risk/gap: {str(item.get('risks_or_gaps') or '').strip() or 'None cited'}",
            ]
            return "\n".join(lines)

        def threshold_status_text() -> str:
            thresholds = track_cfg.get("thresholds", {}) or {}
            hire_min = thresholds.get("hire_percent_min")
            borderline_min = thresholds.get("borderline_percent_min")
            percent = scoring.get("percent_of_max")
            if hire_min is not None and percent is not None and float(percent) >= float(hire_min):
                return f"Meets hire score threshold ({percent_of_max_label} >= {hire_min}%)."
            if borderline_min is not None and percent is not None and float(percent) >= float(borderline_min):
                return f"Meets borderline score threshold ({percent_of_max_label} >= {borderline_min}%)."
            if percent is not None:
                return f"Below score threshold ({percent_of_max_label})."
            return "Threshold status unavailable."

        percent_of_max_label = scoring.get("percent_of_max_label", f"{scoring['percent_of_max']}%")
        missing_score_traits = [
            str(row.get("trait_name") or row.get("name") or row.get("trait_id") or "").strip()
            for row in included_rows
            if row.get("raw_score") is None
        ]
        missing_score_traits = [name for name in missing_score_traits if name]
        if deepseek_total is not None:
            _ai_pct_raw, ai_percent = ScoringEngine._calculate_percent(deepseek_total, int(scoring["max_weighted_total"]))
            deepseek_total_text = f"{deepseek_total} / {scoring['max_weighted_total']} ({ai_percent}%)"
        elif not any_ai_score and (model_suggestion_status or model_scoring_status):
            deepseek_total_text = (
                "not generated "
                f"(suggestions: {model_suggestion_status or 'not available'}; scoring: {model_scoring_status or 'not available'})"
            )
        else:
            deepseek_total_text = "N/A (incomplete)"
        if trait_based_total is not None:
            _trait_pct_raw, trait_percent = ScoringEngine._calculate_percent(
                trait_based_total,
                int(scoring["max_weighted_total"]),
            )
            trait_based_total_text = f"{trait_based_total} / {scoring['max_weighted_total']} ({trait_percent}%)"
        elif not any(str((row.get("model_trait_score") or {}).get("raw_score") or "").strip() for row in included_rows):
            trait_based_total_text = (
                "not generated "
                f"(suggestions: {model_suggestion_status or 'not available'}; scoring: {model_scoring_status or 'not available'})"
            )
        else:
            trait_based_total_text = "N/A (incomplete)"
        recommendation_text = str(scoring["outcome"])
        if str(scoring.get("outcome") or "").strip().lower() == "incomplete" and missing_score_traits:
            recommendation_text = f"{recommendation_text} (missing final raw score: {', '.join(missing_score_traits)})"
        add_box(
            "Recommendation: "
            f"{recommendation_text}. "
            f"Interviewer score: {scoring['weighted_total']} / {scoring['max_weighted_total']} "
            f"({percent_of_max_label}). "
            f"Threshold status: {threshold_status_text()} "
            f"Override/disqualifier status: "
            f"{'Active' if scoring['critical_eq_1'] or scoring['disqualifier_present'] or scoring['locked_rule'] else 'None active'}. "
            f"AI advisory score: {deepseek_total_text}. "
            f"{ai_generated_label} trait-based score: {trait_based_total_text}. "
            f"Candidate: {cname}.",
            fill=pale_green if scoring["outcome"] == "Hire" else pale_yellow,
            bold=True,
        )

        executive_summary = str(payload.get("executive_summary") or "").strip()
        executive_summary_sections = payload.get("executive_summary_sections")
        if executive_summary or isinstance(executive_summary_sections, dict):
            render_executive_summary(executive_summary, executive_summary_sections)
        interview_highlights = [
            str(item or "").strip()
            for item in payload.get("interview_highlights", []) or []
            if str(item or "").strip()
        ]
        if interview_highlights:
            add_heading(f"{ai_generated_label} Evidence Summary")
            for highlight in interview_highlights:
                add_text(highlight, style="List Bullet")

        add_heading("Scorecard Snapshot")
        table = doc.add_table(rows=1, cols=7)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "Trait"
        hdr[1].text = "Priority"
        hdr[2].text = "Weight"
        hdr[3].text = "Interviewer\nRaw Score"
        hdr[4].text = "Interviewer\nWeighted Score"
        hdr[5].text = "AI-suggested\nRaw Score"
        hdr[6].text = "AI-suggested\nWeighted Score"
        repeat_header_row(table.rows[0])
        for cell in hdr:
            format_cell(cell, bold=True, fill=pale_blue, color=navy, align=WD_ALIGN_PARAGRAPH.CENTER)

        for row in scoring["rows"]:
            if row.get("skipped", False):
                continue
            cells = table.add_row().cells
            cells[0].text = row["trait_name"]
            cells[1].text = row["priority"]
            cells[2].text = str(row["weight"])
            raw_display = row.get("raw_score", None)
            cells[3].text = "N/A" if raw_display is None else str(raw_display)
            cells[4].text = str(row.get("system_checkbox_score", row["weighted_score"]))
            suggested_raw_score = row.get("suggested_raw_score")
            cells[5].text = "N/A" if suggested_raw_score is None else str(suggested_raw_score)
            deepseek_score = row.get("deepseek_calculated_score")
            cells[6].text = "N/A" if deepseek_score is None else str(deepseek_score)
            row_fill = pale_yellow if row_has_risk(row) else pale_green if raw_display is not None and raw_display >= 4 else white
            for index, cell in enumerate(cells):
                format_cell(
                    cell,
                    fill=pale_teal if index in {5, 6} else row_fill,
                    align=WD_ALIGN_PARAGRAPH.CENTER if index in {2, 3, 4, 5, 6} else None,
                )
        set_table_geometry(table, [2.05, 0.85, 0.5, 0.9, 1.0, 0.9, 1.0])

        add_box(
            f"Weighted Total: {scoring['weighted_total']} / {scoring['max_weighted_total']} | "
            f"AI-suggested Total: {deepseek_total_text} | "
            f"Skipped scored questions: {scoring.get('skipped_traits_count', 0)} | "
            f"Percent of Max: {percent_of_max_label} | Final Outcome: {scoring['outcome']}",
            fill=pale_teal,
            bold=True,
        )

        locked_rule_display = str(scoring["locked_rule"] if scoring["locked_rule"] else "None").replace(
            "DeepSeek",
            ai_generated_label,
        )
        add_box(
            "Override Summary: "
            f"Any Critical trait = 1: {'Yes' if scoring['critical_eq_1'] else 'No'} | "
            f"Any Absolute Disqualifier observed: {'Yes' if scoring['disqualifier_present'] else 'No'} | "
            f"Outcome lock rule: {locked_rule_display}",
            fill=pale_blue,
        )

        add_heading("Consolidated Answer Summaries")
        if not answer_summaries:
            add_text("No generated answer summaries available.")
        else:
            for item in sorted(answer_summaries, key=lambda value: value.get("flow_index", 0) or 0):
                flow_index = item.get("flow_index")
                if flow_index not in flow_by_index:
                    continue
                question = flow_question_for_index(flow_index) or f"Question {flow_index or ''}".strip()
                row = scoring_row_for_answer_summary(item)
                model_trait_score = row.get("model_trait_score", {}) if row else {}
                rows = [
                    ("Question text", question),
                ]
                if is_scored_answer_summary(item):
                    rows.extend(
                        [
                            ("Interviewer rating", raw_rating_text(row.get("raw_score") if row else None)),
                            ("AI-advisory rating", raw_rating_text(row.get("suggested_raw_score") if row else None)),
                            ("AI-trait-based rating", raw_rating_text(model_trait_score.get("raw_score") if model_trait_score else None)),
                        ]
                    )
                rows.append(("Answer summary", str(item.get("summary") or "").strip()))
                summary_card = doc.add_table(rows=len(rows) + 1, cols=2)
                summary_card.style = "Table Grid"
                title_cell = summary_card.rows[0].cells[0].merge(summary_card.rows[0].cells[1])
                title_cell.text = f"Question: {question}"
                format_cell(title_cell, bold=True, fill=pale_blue, color=navy)
                for row_index, (label, value) in enumerate(rows, start=1):
                    cells = summary_card.rows[row_index].cells
                    cells[0].text = label
                    cells[1].text = value or "None cited"
                    format_cell(cells[0], bold=True, fill=pale_blue, color=navy)
                    format_cell(cells[1], fill=pale_teal if "rating" in label.lower() else white)
                set_table_geometry(summary_card, [1.8, 5.4], prevent_splits=False)

        has_degree = qualification.get("has_degree", None)
        has_degree_text = "Yes" if has_degree is True else "No" if has_degree is False else "Not provided"
        degree_type = str(qualification.get("degree_type", "") or "").strip() or "N/A"
        degree_in_ece = "Yes" if qualification.get("degree_in_ece", False) else "No"
        ece_units = qualification.get("ece_units_completed", None)
        ece_units_text = "N/A" if ece_units is None else str(ece_units)
        infant_toddler = "Yes" if qualification.get("infant_toddler_class_completed", False) else "No"
        total_units = qualification.get("total_units_completed", None)
        total_units_text = "N/A" if total_units is None else str(total_units)
        years_experience = qualification.get("years_experience", None)
        years_experience_text = "N/A" if years_experience is None else str(years_experience)

        add_heading("Candidate Snapshot")
        add_key_value_table(
            [
                ("Candidate Name", cname),
                ("School/Location", str(school or "N/A")),
                ("Track", track_label),
                ("Interview Date", interview_date),
                ("Has degree", has_degree_text),
                ("Degree type", degree_type),
                ("Degree in Early Childhood Education (ECE)", degree_in_ece),
                ("ECE units completed", ece_units_text),
                ("Infant/toddler class completed", infant_toddler),
                ("Total units completed (if no degree)", total_units_text),
                ("Years of experience", years_experience_text),
            ]
        )

        add_heading("Director Decision Brief")
        strongest_rows = [
            row
            for row in scoring["rows"]
            if not row.get("skipped", False) and row.get("raw_score") is not None and row.get("raw_score") >= 4
        ]
        risk_rows = [row for row in scoring["rows"] if row_has_risk(row)]
        if strongest_rows:
            add_box(
                "Strongest evidence: "
                + "; ".join(f"{row['trait_name']} ({row.get('raw_score')}/5)" for row in strongest_rows[:3]),
                fill=pale_green,
                bold=True,
            )
        else:
            add_box("Strongest evidence: No high-scoring trait evidence recorded.", fill=pale_blue)
        if risk_rows:
            add_box(
                "Main risks/gaps: " + "; ".join(f"{row['trait_name']}" for row in risk_rows[:4]),
                fill=pale_yellow,
                bold=True,
            )
        else:
            add_box("Main risks/gaps: None recorded.", fill=pale_green)
        add_box(
            f"Score drivers: {scoring['weighted_total']} / {scoring['max_weighted_total']} "
            f"({percent_of_max_label}); skipped scored questions: {scoring.get('skipped_traits_count', 0)}.",
            fill=pale_blue,
        )
        add_box(
            "Follow-up needed: "
            + ("Review critical safety notes before decision." if risk_rows else "No required follow-up captured in notes."),
            fill=pale_yellow if risk_rows else pale_green,
        )

        add_heading("Critical Safety Review")
        critical_rows = [row for row in scoring["rows"] if str(row.get("priority") or "").lower() == "critical"]
        if critical_rows:
            safety_table = doc.add_table(rows=1, cols=4)
            safety_table.style = "Table Grid"
            hdr = safety_table.rows[0].cells
            hdr[0].text = "Critical Trait"
            hdr[1].text = "Score"
            hdr[2].text = "Risk Flag"
            hdr[3].text = "Evidence"
            repeat_header_row(safety_table.rows[0])
            for cell in hdr:
                format_cell(cell, bold=True, fill=pale_blue, color=navy, align=WD_ALIGN_PARAGRAPH.CENTER)
            for row in critical_rows:
                cells = safety_table.add_row().cells
                raw_display = row.get("raw_score", None)
                cells[0].text = row["trait_name"]
                cells[1].text = "N/A" if raw_display is None else str(raw_display)
                cells[2].text = "Yes" if row_has_risk(row) else "No"
                cells[3].text = str(row.get("verbatim_notes") or row.get("question_notes") or row.get("trait_notes") or "").strip() or "None recorded"
                row_fill = pale_yellow if cells[2].text == "Yes" else pale_green
                for index, cell in enumerate(cells):
                    format_cell(cell, fill=row_fill)
            set_table_geometry(safety_table, [2.15, 0.7, 0.8, 3.55])
        else:
            add_text("No critical traits configured for this track.")

        add_text("Global disqualifiers reviewed:", bold=True)
        for d in rubric["absolute_disqualifiers"]:
            add_text(str(d), style="List Bullet")

        add_text("Observed disqualifier evidence (from verbatim notes):", bold=True)
        evidence_added = False
        for row in scoring["rows"]:
            if row["absolute_disqualifier"] and (row.get("verbatim_notes") or "").strip():
                add_text(f"{row['trait_name']}: {row['verbatim_notes'].strip()}", style="List Bullet")
                evidence_added = True
        if not evidence_added:
            add_text("None recorded", style="List Bullet")

        add_heading("Hiring Manager Evidence Notes")
        for idx, row in enumerate(scoring["rows"], start=1):
            if row.get("skipped", False):
                continue
            add_heading(f"{idx}. {row['trait_name']}", level=2)
            add_text(f"Priority: {row['priority']} | Weight: x{row['weight']}")
            add_labeled_text("Primary Question: ", str(row["primary_question"]))
            raw_display = row.get("raw_score", None)
            system_score = row.get("system_checkbox_score", row.get("weighted_score"))
            model_trait_score = row.get("model_trait_score", {}) or {}
            ne = "Yes" if row.get("no_example_after_followups") else "No"
            add_key_value_table(
                [
                    ("Final interviewer raw score", "N/A" if raw_display is None else str(raw_display)),
                    ("Human weighted score", "N/A" if system_score is None else str(system_score)),
                    ("Evidence status", evidence_status_for_row(row)),
                    ("Risk/gap", str(model_trait_score.get("risks_or_gaps") or "").strip() or "None cited"),
                    ("Automatic no-hire signal IDs", ", ".join(row.get("auto_no_hire_signal_ids", []) or []) or "None"),
                    ("Automatic no-hire reasons", "; ".join(row.get("auto_no_hire_reasons", []) or []) or "None"),
                    ("Automatic no-hire quotes", "; ".join(row.get("auto_no_hire_quotes", []) or []) or "None"),
                    ("No example after follow-ups", ne),
                ]
            )
            add_labeled_text("Question Notes: ", str(row["question_notes"]))
            add_labeled_text("Trait Notes: ", str(row["trait_notes"]))
            add_labeled_text("Verbatim quote/notes: ", str(row["verbatim_notes"]))

        add_heading("Custom Questions (Non-scored)")
        custom_answers = payload.get("custom_answers", []) or []
        if not custom_answers:
            add_text("None.")
        else:
            custom_table = doc.add_table(rows=1, cols=2)
            custom_table.style = "Table Grid"
            header_cells = custom_table.rows[0].cells
            header_cells[0].text = "Question"
            header_cells[1].text = "Answer"
            repeat_header_row(custom_table.rows[0])
            format_cell(header_cells[0], bold=True, fill=navy, color=white)
            format_cell(header_cells[1], bold=True, fill=navy, color=white)
            for i, item in enumerate(custom_answers, start=1):
                qtext = (item.get("question_text") or "").strip()
                ans = (item.get("answer") or "").strip()
                cells = custom_table.add_row().cells
                cells[0].text = qtext or f"Custom question {i}"
                cells[1].text = ans if ans else "N/A"
                format_cell(cells[0])
                format_cell(cells[1])
            set_table_geometry(custom_table, [2.4, 4.8])

        add_heading("Interview Transcript Appendix")
        if not flow_transcript:
            add_text("No flow transcript available.")
        else:
            for i, item in enumerate(flow_transcript, start=1):
                itype = (item.get("type") or "").strip()
                item_trait_id = canonical_trait_id(item.get("id") or item.get("trait_id"))
                if itype == "trait" and item_trait_id in skipped_trait_ids:
                    continue
                item_title = (item.get("title") or "").strip() or "Question"
                qtext = (item.get("question") or "").strip()
                add_heading(f"{i}. {item_title} ({itype})", level=2)
                if qtext:
                    add_labeled_text("Question: ", qtext, space_after=3)
                cand_tx = compact_transcript_attempts(item.get("candidate_transcript") or "")
                if cand_tx:
                    add_text("Full Candidate Answer (auto-transcribed)", bold=True)
                    add_box(cand_tx, allow_split=True)
                else:
                    add_labeled_text("Full Candidate Answer (auto-transcribed): ", "Not captured")
                matching_summaries = [
                    summary
                    for summary in answer_summaries
                    if summary.get("flow_index") == item.get("flow_index", i)
                ]
                for summary in matching_summaries:
                    add_text("Answer summary evidence", bold=True)
                    add_box(answer_summary_appendix_text(summary), allow_split=True)

        add_heading("AI Advisory Appendix")
        add_box(
            f"{ai_advisory_label} content is supporting information only. "
            "Human interviewer scores and notes remain the hiring record source of truth.",
            fill=pale_teal,
        )
        model_suggestion_status = str(payload.get("model_suggestion_status") or "not available").strip() or "not available"
        model_scoring_status = str(payload.get("model_scoring_status") or "not available").strip() or "not available"

        def row_has_ai_advisory(row: dict[str, Any]) -> bool:
            model_trait_score = row.get("model_trait_score", {}) or {}
            return bool(
                row.get("deepseek_calculated_score") is not None
                or row.get("deepseek_raw_score") is not None
                or row.get("net_signal_score") is not None
                or row.get("suggested_raw_score") is not None
                or any(str(value or "").strip() for value in model_trait_score.values())
                or row.get("model_signal_suggestions")
            )

        for idx, row in enumerate(scoring["rows"], start=1):
            if row.get("skipped", False):
                continue
            deepseek_score = row.get("deepseek_calculated_score")
            deepseek_raw = row.get("deepseek_raw_score")
            net_signal_score = row.get("net_signal_score")
            suggested_raw_score = row.get("suggested_raw_score")
            model_trait_score = row.get("model_trait_score", {}) or {}
            add_heading(f"{idx}. {row['trait_name']}", level=2)
            if not row_has_ai_advisory(row):
                add_box(
                    "AI advisory scoring not generated for this trait "
                    f"(suggestions: {model_suggestion_status}; scoring: {model_scoring_status}).",
                    fill=pale_yellow,
                )
                continue
            add_key_value_table(
                [
                    ("AI net signal score", "N/A" if net_signal_score is None else str(net_signal_score)),
                    (f"{ai_suggested_label} raw score", "N/A" if suggested_raw_score is None else str(suggested_raw_score)),
                    (f"{ai_suggested_label} weighted score", "N/A" if deepseek_score is None else str(deepseek_score)),
                    (f"{ai_advisory_label} raw score", "N/A" if deepseek_raw is None else str(deepseek_raw)),
                    (f"{ai_generated_label} score evidence", str(model_trait_score.get("evidence_quote") or "").strip() or "None cited"),
                    (f"{ai_generated_label} score rationale", str(model_trait_score.get("rationale") or "").strip() or "None cited"),
                    (f"{ai_generated_label} score risk/gap", str(model_trait_score.get("risks_or_gaps") or "").strip() or "None cited"),
                    (f"Interviewer adjusted from {ai_suggested_label} score", "Yes" if row.get("interviewer_adjusted") else "No"),
                    ("Adjustment reason", str(row.get("adjustment_reason") or "").strip() or "None"),
                ]
            )
            selected_signal_ids = [str(signal_id) for signal_id in row.get("selected_signal_ids", []) or [] if str(signal_id).strip()]
            if selected_signal_ids:
                add_text("Compatibility selected signal IDs:", bold=True)
                add_box(", ".join(selected_signal_ids))
            model_suggestions = row.get("model_signal_suggestions", []) or []
            if model_suggestions:
                add_text(f"{ai_advisory_label} signal observations:", bold=True)
                suggestion_table = doc.add_table(rows=1, cols=5)
                suggestion_table.style = "Table Grid"
                suggestion_headers = suggestion_table.rows[0].cells
                suggestion_headers[0].text = "Signal"
                suggestion_headers[1].text = "Confidence"
                suggestion_headers[2].text = "Evidence"
                suggestion_headers[3].text = "Rationale"
                suggestion_headers[4].text = "Signal source"
                repeat_header_row(suggestion_table.rows[0])
                for cell in suggestion_headers:
                    format_cell(cell, bold=True, fill=navy, color=white, align=WD_ALIGN_PARAGRAPH.CENTER)
                override = row.get("model_signal_override", {}) or {}
                rejected = set(override.get("rejected_signal_ids", []) or [])
                for suggestion in model_suggestions:
                    if not isinstance(suggestion, dict):
                        continue
                    signal_id = str(suggestion.get("signal_id") or "").strip()
                    rationale = str(suggestion.get("rationale") or "").strip()
                    evidence_quote = str(suggestion.get("evidence_quote") or "").strip()
                    confidence = suggestion.get("confidence", 0)
                    if signal_id:
                        cells = suggestion_table.add_row().cells
                        label = signal_labels.get(signal_id, signal_id)
                        cells[0].text = f"{label} ({signal_id})" if label != signal_id else signal_id
                        cells[1].text = str(confidence)
                        cells[2].text = evidence_quote or "None cited"
                        cells[3].text = rationale
                        cells[4].text = f"Used by {ai_advisory_label} scoring"
                        if signal_id in rejected:
                            cells[4].text = "AI suggestion"
                        for index, cell in enumerate(cells):
                            format_cell(cell, fill=pale_teal if index == 4 else white)
                set_table_geometry(suggestion_table, [1.45, 0.75, 1.9, 1.9, 1.2])
                if override:
                    add_key_value_table(
                        [
                            ("AI signal-scored observations", ", ".join(override.get("accepted_signal_ids", []) or []) or "None"),
                            ("AI-suggested observations", ", ".join(override.get("rejected_signal_ids", []) or []) or "None"),
                            ("Compatibility selected-only observations", ", ".join(override.get("manual_only_signal_ids", []) or []) or "None"),
                        ]
                    )

        for paragraph in doc.paragraphs:
            normalize_paragraph(paragraph, size=None, color=None)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    set_cell_margins(cell)
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.line_spacing = 1.08
                        normalize_paragraph(paragraph, size=None, color=None)

        school_part = sanitize_filename(school) if school else "UnknownSchool"
        filename = f"{interview_date} - {school_part} - {sanitize_filename(cname)} - Interview.docx"
        out_path = self.output_dir / filename
        doc.save(out_path)
        return out_path


class DirectorEmailDraftError(RuntimeError):
    pass


def open_outlook_draft(*, subject: str, body: str, attachments: list[str], to_recipients: str = "") -> None:
    if not sys.platform.startswith("win"):
        raise DirectorEmailDraftError("Outlook draft is only supported on Windows.")

    existing_files = [str(Path(path).expanduser()) for path in attachments if Path(path).expanduser().exists()]
    escaped_subject = _ps_quote(sanitize_email_subject(subject))
    escaped_body = _ps_quote(body)
    escaped_to = _ps_quote(to_recipients)
    attachment_script = "\n".join(
        [f"$mail.Attachments.Add('{_ps_quote(path)}') | Out-Null" for path in existing_files]
    )

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $outlook = New-Object -ComObject Outlook.Application\n"
        "  if ($null -eq $outlook) { throw 'Outlook COM automation is unavailable.' }\n"
        "  $mail = $outlook.CreateItem(0)\n"
        "  if ($null -eq $mail) { throw 'Unable to create an Outlook draft item.' }\n"
        f"  $mail.Subject = '{escaped_subject}'\n"
        f"  $mail.Body = '{escaped_body}'\n"
        f"  $mail.To = '{escaped_to}'\n"
        f"  {attachment_script}\n"
        "  $mail.Display()\n"
        "} catch {\n"
        "  Write-Error $_.Exception.Message\n"
        "  exit 1\n"
        "}\n"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise DirectorEmailDraftError(f"Could not open Outlook draft. {stderr}".strip()) from exc


def build_mailto_url(*, subject: str, body: str, to_recipients: str = "") -> str:
    recipient_value = _normalize_mailto_recipients(to_recipients)
    query_parts: list[str] = []
    subject_value = sanitize_email_subject(subject)
    body_value = str(body or "").strip()
    if subject_value:
        query_parts.append(f"subject={quote(subject_value)}")
    if body_value:
        query_parts.append(f"body={quote(body_value)}")
    query = "&".join(query_parts)
    if not query:
        return f"mailto:{recipient_value}"
    return f"mailto:{recipient_value}?{query}"


def _normalize_mailto_recipients(to_recipients: str) -> str:
    raw_recipients = str(to_recipients or "").strip()
    if not raw_recipients:
        return ""

    uses_semicolon_separator = ";" in raw_recipients
    separator = ";" if uses_semicolon_separator else ","
    recipients = [token.strip() for token in raw_recipients.replace(";", ",").split(",")]
    encoded_recipients = [quote(recipient, safe="@") for recipient in recipients if recipient]
    return separator.join(encoded_recipients)


def _ps_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


class DirectorReferralError(RuntimeError):
    pass


def _allowed_referral_hosts_from_env() -> set[str]:
    raw_hosts = str(os.environ.get("DIRECTOR_REFERRAL_ALLOWED_HOSTS", "")).strip()
    if not raw_hosts:
        return set()
    hosts = [part.strip().lower() for part in raw_hosts.split(",")]
    return {host for host in hosts if host}


def _validate_referral_endpoint(endpoint: str, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme.lower() != "https":
        raise DirectorReferralError("Director referral endpoint must use HTTPS.")
    if not host:
        raise DirectorReferralError("Director referral endpoint host is missing.")

    enforced_hosts = allowed_hosts if allowed_hosts is not None else _allowed_referral_hosts_from_env()
    if enforced_hosts and host not in {h.lower() for h in enforced_hosts if h}:
        raise DirectorReferralError("Director referral endpoint host is not in the allowlist.")

    return endpoint


def build_director_packet(
    *,
    payload: dict[str, Any],
    scoring: dict[str, Any],
    report_path: Path,
    integration_path: Path,
    referral_packet: dict[str, str],
    generated_transcript_path: Path | None = None,
) -> dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    documents = {
        "resume_path": str(referral_packet.get("resume_path", "")).strip(),
        "interview_notes_path": str(referral_packet.get("interview_notes_path", "")).strip(),
        "transcript_path": str(generated_transcript_path or referral_packet.get("transcript_path", "")).strip(),
        "final_report_path": str(report_path),
        "integration_export_path": str(integration_path),
    }
    return {
        "event": "director_referral_packet",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "name": str(candidate.get("name", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
        },
        "scoring": {
            "outcome": scoring.get("outcome"),
            "percent_of_max": scoring.get("percent_of_max"),
            "weighted_total": scoring.get("weighted_total"),
            "max_weighted_total": scoring.get("max_weighted_total"),
        },
        "documents": documents,
    }


def send_director_packet(
    packet: dict[str, Any],
    endpoint: str,
    *,
    timeout_seconds: int = 12,
    allowed_hosts: set[str] | None = None,
) -> dict[str, Any]:
    endpoint_clean = str(endpoint or "").strip()
    if not endpoint_clean:
        raise DirectorReferralError("Director referral endpoint is not configured.")
    _validate_referral_endpoint(endpoint_clean, allowed_hosts)

    req = request.Request(
        endpoint_clean,
        data=json.dumps(packet).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            return {
                "status_code": int(getattr(resp, "status", 200) or 200),
                "response": response_body,
            }
    except error.HTTPError as exc:
        raise DirectorReferralError(f"Referral endpoint rejected packet ({exc.code}): {exc.reason}") from exc
    except error.URLError as exc:
        raise DirectorReferralError(f"Failed to reach referral endpoint: {exc.reason}") from exc


def default_referral_endpoint() -> str:
    return str(os.environ.get("DIRECTOR_REFERRAL_ENDPOINT", "")).strip()


def append_communication_log(base_dir: Path, event: dict[str, Any], *, candidate_name: str) -> Path:
    comm_dir = Path(base_dir).expanduser() / "communications"
    comm_dir.mkdir(parents=True, exist_ok=True)
    safe_candidate = sanitize_filename(candidate_name or "Unknown")
    out_path = comm_dir / f"director-referral-{safe_candidate}.jsonl"
    line = json.dumps(event, ensure_ascii=False)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return out_path


def normalize_outcome_label(outcome: Any) -> str:
    raw = str(outcome or "").strip().lower()
    mapping = {
        "hire": "hire",
        "borderline": "borderline",
        "no hire": "no_hire",
        "no_hire": "no_hire",
        "nohire": "no_hire",
    }
    return mapping.get(raw, "borderline")


def build_integration_payload(
    payload: dict[str, Any],
    scoring: dict[str, Any],
    *,
    include_flow_slices: bool = True,
) -> dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    rows = scoring.get("rows", []) or []
    custom_answers = payload.get("custom_answers", []) or []
    flow_transcript = payload.get("flow_transcript", []) or []

    trait_notes = [_trait_note(row) for row in rows]
    referral_packet = payload.get("referral_packet", {}) or {}
    communication_log = payload.get("communication_log", []) or []

    export_payload: dict[str, Any] = {
        "candidate": {
            "name": str(candidate.get("name", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
            "qualification": {
                "has_degree": candidate.get("qualification", {}).get("has_degree", None),
                "degree_type": str(candidate.get("qualification", {}).get("degree_type", "")).strip(),
                "degree_in_ece": bool(candidate.get("qualification", {}).get("degree_in_ece", False)),
                "ece_units_completed": candidate.get("qualification", {}).get("ece_units_completed", None),
                "infant_toddler_class_completed": bool(
                    candidate.get("qualification", {}).get("infant_toddler_class_completed", False)
                ),
                "total_units_completed": candidate.get("qualification", {}).get("total_units_completed", None),
                "years_experience": candidate.get("qualification", {}).get("years_experience", None),
            },
        },
        "percent_of_max": float(scoring.get("percent_of_max", 0.0) or 0.0),
        "decision": normalize_outcome_label(scoring.get("outcome")),
        "interview_notes": {
            "traits": trait_notes,
            "custom_answers": custom_answers,
        },
        "referral_packet": {
            "resume_path": str(referral_packet.get("resume_path", "")).strip(),
            "interview_notes_path": str(referral_packet.get("interview_notes_path", "")).strip(),
            "transcript_path": str(referral_packet.get("transcript_path", "")).strip(),
        },
        "communication_log": list(communication_log),
    }

    slices = _flow_slices(flow_transcript)
    if include_flow_slices and slices:
        export_payload["flow_transcript_slices"] = slices

    summary_fields = {
        "executive_summary": str(payload.get("executive_summary") or "").strip(),
        "interview_highlights": [
            str(item or "").strip()
            for item in payload.get("interview_highlights", []) or []
            if str(item or "").strip()
        ],
        "answer_summaries": [
            item
            for item in payload.get("answer_summaries", []) or []
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        ],
        "summary_status": str(payload.get("summary_status") or "").strip(),
        "summary_warnings": [
            str(item or "").strip()
            for item in payload.get("summary_warnings", []) or []
            if str(item or "").strip()
        ],
    }
    for key, value in summary_fields.items():
        if value:
            export_payload[key] = value

    return export_payload


def serialize_integration_payload(
    base_output_dir: Path,
    export_payload: dict[str, Any],
    *,
    candidate_name: str,
) -> Path:
    base_dir = Path(base_output_dir).expanduser().resolve()
    export_dir = (base_dir / "integration_exports").resolve()
    if base_dir not in {export_dir, *export_dir.parents}:
        raise ValueError("Refusing to write integration export outside base output directory")

    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_candidate = sanitize_filename(candidate_name or "Unknown")
    out_path = export_dir / f"integration-{stamp}-{safe_candidate}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(export_payload, f, indent=2, ensure_ascii=False)

    return out_path


def _trait_note(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trait_id": row.get("trait_id", ""),
        "trait_name": row.get("trait_name", ""),
        "raw_score": row.get("raw_score"),
        "question_notes": row.get("question_notes", ""),
        "trait_notes": row.get("trait_notes", ""),
        "verbatim_notes": row.get("verbatim_notes", ""),
    }


def _flow_slices(flow_transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    for item in flow_transcript:
        candidate_tx = str(item.get("candidate_transcript", "")).strip()
        if not candidate_tx:
            continue

        slices.append(
            {
                "type": item.get("type", ""),
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "candidate_transcript": candidate_tx,
            }
        )

    return slices


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


DEFAULT_EXTENDED_GROUP_LABEL = "Extended Signals"
DEFAULT_ENGINE_MODULE_CONTRACT = Path("contracts/trait_based_scoring_engine.contract.yaml")
DEFAULT_ENGINE_RUNTIME_CONTRACT = Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
RUBRIC_TRAIT_ID_PATTERN = re.compile(r"trait_(\d+)", re.IGNORECASE)
RUNTIME_TRAIT_ID_PATTERN = re.compile(r"T(\d+)(?:_[A-Za-z0-9_]+)?")
SignalUIDefinition = dict[str, Any]
DEFAULT_CORE_DISPLAY_STYLE = "checkbox"
DEFAULT_EXTENDED_DISPLAY_STYLE = "checkbox"
DEFAULT_CORE_SECTION_LABEL = "Core Signals (Most Important)"
DEFAULT_EXTENDED_SECTION_LABEL = "Additional Observations"
DEFAULT_EXTENDED_COLLAPSIBLE = True
DEFAULT_EXTENDED_DEFAULT_COLLAPSED = True
TRAIT_SELECTION_FIELDS = ("selected_signal_ids", "selected_signals", "signal_selections")
SELECTION_COLLECTION_TYPES = (list, tuple, set)

CanonicalSignal = dict[str, Any]
CanonicalSignalGroup = dict[str, Any]

CANONICAL_SIGNAL_COMPARISON_KEYS = (
    "signal_id",
    "label",
    "group_label",
    "weight",
    "is_critical",
)


class CanonicalSignalRecord(dict[str, Any]):
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        return _comparison_view(self) == _comparison_view(other)


class CanonicalSignalGroupRecord(dict[str, Any]):
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        return {
            "group_id": self.get("group_id"),
            "group_label": self.get("group_label"),
            "signals": self.get("signals", []),
        } == {
            "group_id": other.get("group_id"),
            "group_label": other.get("group_label"),
            "signals": other.get("signals", []),
        }


def _comparison_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in CANONICAL_SIGNAL_COMPARISON_KEYS}


def build_signal_dictionary_index(signal_dictionary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signals = signal_dictionary.get("signals", []) if isinstance(signal_dictionary, dict) else []
    index: dict[str, dict[str, Any]] = {}
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        signal_id = str(signal.get("id", "") or "").strip()
        if signal_id:
            index[signal_id] = signal
    return index


def normalize_trait_signal(signal: dict[str, Any], *, default_group_label: str) -> CanonicalSignal:
    signal_id = resolve_trait_signal_selection_id(signal)
    return CanonicalSignalRecord(
        {
            "signal_id": signal_id,
            "runtime_signal_id": resolve_trait_signal_runtime_id(signal),
            "selection_aliases": signal_selection_aliases(signal),
            "label": resolve_trait_signal_label(signal, fallback=signal_id),
            "group_label": str(signal.get("group", "") or default_group_label),
            "weight": resolve_trait_signal_weight(signal),
            "is_critical": bool(signal.get("is_critical", False)),
        }
    )


def resolve_trait_signal_selection_id(signal: dict[str, Any]) -> str:
    if str(signal.get("id", "") or "").strip() and signal.get("maps_to"):
        return str(signal.get("id") or "").strip()
    if str(signal.get("ref", "") or "").strip():
        return str(signal.get("ref") or "").strip()
    return resolve_trait_signal_runtime_id(signal)


def resolve_trait_signal_runtime_id(signal: dict[str, Any]) -> str:
    return str(signal.get("ref") or signal.get("id") or "").strip()


def signal_selection_aliases(signal: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    question_signal_id = str(signal.get("id", "") or "").strip()
    if question_signal_id:
        aliases.append(question_signal_id)
    runtime_signal_id = resolve_trait_signal_runtime_id(signal)
    if runtime_signal_id:
        aliases.append(runtime_signal_id)
    for mapped_signal_id in signal.get("maps_to", []) or []:
        alias = str(mapped_signal_id or "").strip()
        if alias:
            aliases.append(alias)
    return list(dict.fromkeys(aliases))


def resolve_trait_signal_label(signal: dict[str, Any], *, fallback: str = "") -> str:
    return str(signal.get("label", "") or fallback).strip()


def resolve_trait_signal_weight(signal: dict[str, Any]) -> float:
    raw_weight = signal.get("weight", signal.get("base_weight", signal.get("default_weight", 0)))
    return float(raw_weight or 0)


def normalize_core_signals(core_signals: list[dict[str, Any]]) -> list[CanonicalSignal]:
    return _normalize_signal_collection(core_signals, default_group_label="Core")


def normalize_extended_signal_groups(
    trait_definition: dict[str, Any],
    *,
    signal_dictionary_index: dict[str, dict[str, Any]] | None = None,
) -> list[CanonicalSignalGroup]:
    explicit_groups = trait_definition.get("extended_signal_groups", []) or []
    if explicit_groups:
        return _normalize_explicit_extended_groups(explicit_groups)
    return _normalize_runtime_extended_signals(
        trait_definition.get("extended_signals", []) or [],
        signal_dictionary_index=signal_dictionary_index or {},
    )


def iter_trait_schema_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    signals = list(trait_definition.get("core_signals", []) or [])
    for group in trait_definition.get("extended_signal_groups", []) or []:
        if isinstance(group, dict):
            signals.extend(group.get("signals", []) or [])
    signals.extend(trait_definition.get("extended_signals", []) or [])
    return [signal for signal in signals if isinstance(signal, dict)]


def _normalize_signal_collection(signals: list[dict[str, Any]], *, default_group_label: str) -> list[CanonicalSignal]:
    normalized_signals: list[CanonicalSignal] = []
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        normalized = normalize_trait_signal(signal, default_group_label=default_group_label)
        if normalized["signal_id"]:
            normalized_signals.append(normalized)
    return normalized_signals


def _normalize_explicit_extended_groups(groups: list[dict[str, Any]]) -> list[CanonicalSignalGroup]:
    normalized_groups: list[CanonicalSignalGroup] = []
    for index, group in enumerate(groups or [], start=1):
        if not isinstance(group, dict):
            continue
        group_label = str(group.get("group_label", "") or f"Group {index}").strip()
        normalized_groups.append(
            CanonicalSignalGroupRecord(
                {
                    "group_id": str(group.get("group_id", "") or f"group_{index}").strip(),
                    "group_label": group_label,
                    "signals": _normalize_signal_collection(
                        group.get("signals", []) or [],
                        default_group_label=group_label,
                    ),
                }
            )
        )
    return normalized_groups


def _normalize_runtime_extended_signals(
    signals: list[dict[str, Any]],
    *,
    signal_dictionary_index: dict[str, dict[str, Any]],
) -> list[CanonicalSignalGroup]:
    grouped: dict[str, list[CanonicalSignal]] = {}
    ordered_labels: list[str] = []
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        group_label = _runtime_extended_group_label(signal, signal_dictionary_index)
        if group_label not in grouped:
            grouped[group_label] = []
            ordered_labels.append(group_label)
        normalized = normalize_trait_signal(signal, default_group_label=group_label)
        if normalized["signal_id"]:
            grouped[group_label].append(normalized)
    return [
        CanonicalSignalGroupRecord(
            {
                "group_id": _group_id_from_label(group_label),
                "group_label": group_label,
                "signals": grouped[group_label],
            }
        )
        for group_label in ordered_labels
        if grouped[group_label]
    ]


def _runtime_extended_group_label(signal: dict[str, Any], signal_dictionary_index: dict[str, dict[str, Any]]) -> str:
    explicit_group = str(signal.get("group", "") or "").strip()
    if explicit_group:
        return explicit_group
    signal_category = str(signal.get("signal_category", "") or signal.get("category", "") or "").strip()
    if signal_category:
        return signal_category
    for mapped_signal_id in signal.get("maps_to", []) or []:
        dictionary_signal = signal_dictionary_index.get(str(mapped_signal_id).strip(), {})
        category = str(dictionary_signal.get("category", "") or "").strip()
        if category:
            return category
    return DEFAULT_EXTENDED_GROUP_LABEL


def _group_id_from_label(group_label: str) -> str:
    words = [part.lower() for part in str(group_label).split() if part.strip()]
    if not words:
        return "extended_signals"
    return "_".join(words)


def load_trait_definitions_from_runtime_bundle(runtime_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    bundled_traits = runtime_bundle.get("traits")
    if isinstance(bundled_traits, list):
        bundled_trait_definitions = [_normalize_trait_definition(item) for item in bundled_traits if isinstance(item, dict)]
        if bundled_trait_definitions:
            return bundled_trait_definitions

    resolved_trait_dir = _resolve_traits_dir_from_bundle(runtime_bundle)
    if resolved_trait_dir is not None:
        return load_trait_definitions_from_dir(resolved_trait_dir)

    runtime_contract_path = runtime_bundle.get("runtime_contract_path")
    if runtime_contract_path:
        return load_trait_definitions_from_contract(runtime_contract_path)
    return []


def load_trait_definitions_from_contract(runtime_contract_path: str | Path) -> list[dict[str, Any]]:
    contract_payload = _load_yaml(Path(runtime_contract_path))
    trait_dir = _resolve_traits_dir_from_contract_payload(contract_payload, runtime_contract_path)
    return load_trait_definitions_from_dir(trait_dir)


def load_trait_definitions_from_dir(traits_dir: str | Path) -> list[dict[str, Any]]:
    resolved_dir = Path(traits_dir).expanduser().resolve()
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        return []

    trait_definitions: list[dict[str, Any]] = []
    for trait_path in sorted(resolved_dir.glob("T*.json")):
        trait_payload = _load_json(trait_path)
        if isinstance(trait_payload, dict):
            trait_definitions.append(_normalize_trait_definition(trait_payload))
    return trait_definitions


def canonical_trait_id(trait_id: Any) -> str:
    candidate = str(trait_id or "").strip()
    if not candidate:
        return ""
    rubric_match = RUBRIC_TRAIT_ID_PATTERN.fullmatch(candidate)
    if rubric_match:
        return f"trait_{int(rubric_match.group(1))}"
    runtime_match = RUNTIME_TRAIT_ID_PATTERN.fullmatch(candidate)
    if runtime_match:
        return f"trait_{int(runtime_match.group(1))}"
    return candidate


def trait_id_aliases(trait_id: Any) -> list[str]:
    candidate = str(trait_id or "").strip()
    if not candidate:
        return []
    canonical_id = canonical_trait_id(candidate)
    aliases = [canonical_id]
    if candidate != canonical_id:
        aliases.append(candidate)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _normalize_trait_definition(trait_definition: dict[str, Any]) -> dict[str, Any]:
    canonical_id = canonical_trait_id(trait_definition.get("trait_id"))
    normalized = dict(trait_definition)
    normalized["trait_id"] = canonical_id or str(trait_definition.get("trait_id", "") or "").strip()
    normalized["trait_aliases"] = trait_id_aliases(trait_definition.get("trait_id"))
    return normalized


def _resolve_traits_dir_from_bundle(runtime_bundle: dict[str, Any]) -> Path | None:
    resolved_paths = runtime_bundle.get("resolved_paths")
    if not isinstance(resolved_paths, dict):
        return None

    traits_dir = resolved_paths.get("traits_dir")
    if not traits_dir:
        return None
    return Path(traits_dir)


def _resolve_traits_dir_from_contract_payload(
    contract_payload: dict[str, Any],
    runtime_contract_path: str | Path,
) -> Path:
    paths = contract_payload.get("paths")
    if not isinstance(paths, dict):
        return Path(runtime_contract_path).expanduser().resolve().parent

    traits_dir = paths.get("traits_dir")
    if not isinstance(traits_dir, str) or not traits_dir.strip():
        return Path(runtime_contract_path).expanduser().resolve().parent

    base_dir = Path(runtime_contract_path).expanduser().resolve().parent
    return (base_dir / traits_dir).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _load_yaml(path: Path) -> dict[str, Any]:
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        return {}

    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def default_signal_ui_definition(trait_id: str) -> SignalUIDefinition:
    return {
        "trait_id": trait_id,
        "core_display_style": DEFAULT_CORE_DISPLAY_STYLE,
        "extended_display_style": DEFAULT_EXTENDED_DISPLAY_STYLE,
        "core_section_label": DEFAULT_CORE_SECTION_LABEL,
        "extended_section_label": DEFAULT_EXTENDED_SECTION_LABEL,
        "extended_collapsible": DEFAULT_EXTENDED_COLLAPSIBLE,
        "extended_default_collapsed": DEFAULT_EXTENDED_DEFAULT_COLLAPSED,
        "core_signals": [],
        "extended_groups": [],
        "valid_signal_ids": [],
    }


def load_trait_signal_ui_definition(
    trait_id: str,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> SignalUIDefinition:
    runtime_bundle = load_module_contract_runtime_bundle(
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    validate_runtime_bundle_metadata(runtime_bundle)
    trait_definition = _find_trait_definition(runtime_bundle.get("trait_definitions", []), trait_id)
    if not trait_definition:
        return _empty_signal_ui_definition(trait_id)
    return _build_signal_ui_definition(runtime_bundle, trait_definition)


def ensure_trait_signal_ui_definition(
    trait_id: str,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> SignalUIDefinition:
    definition = load_trait_signal_ui_definition(
        trait_id,
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    if definition.get("valid_signal_ids"):
        return definition
    raise ReportingValidationError(
        f"Trait scoring configuration mismatch: rubric trait '{trait_id}' is missing a runtime signal definition."
    )


def normalize_trait_signal_selection_state(trait_state: dict[str, Any] | None, valid_signal_ids: list[str]) -> list[str]:
    normalized_state = normalize_trait_state_item(trait_state)
    allowed = set(valid_signal_ids)
    selected_signal_ids = normalized_state.get("selected_signal_ids", []) or []
    if not allowed:
        return []
    return [signal_id for signal_id in selected_signal_ids if signal_id in allowed]


def write_canonical_selected_signal_ids(trait_state: dict[str, Any], selected_signal_ids: list[str]) -> None:
    canonical_ids = list(dict.fromkeys(signal_id for signal_id in selected_signal_ids if str(signal_id).strip()))
    trait_state["selected_signal_ids"] = canonical_ids
    trait_state.pop("selected_signals", None)
    trait_state.pop("signal_selections", None)


def normalize_model_signal_suggestions(suggestions: Any, valid_signal_ids: list[str]) -> list[dict[str, Any]]:
    allowed = set(valid_signal_ids)
    if not isinstance(suggestions, list) or not allowed:
        return []
    best_by_signal: dict[str, dict[str, Any]] = {}
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        signal_id = str(item.get("signal_id") or item.get("id") or item.get("ref") or "").strip()
        if not signal_id or signal_id not in allowed:
            continue
        candidate = {
            "signal_id": signal_id,
            "confidence": _normalize_model_confidence(item.get("confidence")),
            "rationale": str(item.get("rationale") or "").strip()[:180],
            "evidence_quote": str(item.get("evidence_quote") or "").strip()[:220],
        }
        existing = best_by_signal.get(signal_id)
        if existing is None or candidate["confidence"] > existing["confidence"]:
            best_by_signal[signal_id] = candidate
    return list(best_by_signal.values())


def write_canonical_model_signal_suggestions(
    trait_state: dict[str, Any],
    suggestions: list[dict[str, Any]],
) -> None:
    trait_state["model_signal_suggestions"] = [
        {
            "signal_id": str(item.get("signal_id") or "").strip(),
            "confidence": _normalize_model_confidence(item.get("confidence")),
            "rationale": str(item.get("rationale") or "").strip()[:180],
            "evidence_quote": str(item.get("evidence_quote") or "").strip()[:220],
        }
        for item in suggestions
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]


def trait_signal_override_state(trait_state: dict[str, Any]) -> dict[str, list[str]]:
    manual = _normalize_selected_signal_ids(trait_state)
    model = [
        str(item.get("signal_id") or "").strip()
        for item in trait_state.get("model_signal_suggestions", []) or []
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    manual_set = set(manual)
    model_set = set(model)
    return {
        "accepted_signal_ids": [signal_id for signal_id in model if signal_id in manual_set],
        "rejected_signal_ids": [signal_id for signal_id in model if signal_id not in manual_set],
        "manual_only_signal_ids": [signal_id for signal_id in manual if signal_id not in model_set],
    }


def _normalize_model_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(parsed, 0.0), 1.0)


def count_selected_trait_checkbox_entries(state: dict[str, Any], trait_id: str) -> int:
    selection_value = resolve_trait_selection_value(state)
    if selection_value is None:
        return 0
    return _count_selected_entries(selection_value, trait_id)


def resolve_trait_selection_value(state: dict[str, Any]) -> Any:
    for field_name in TRAIT_SELECTION_FIELDS:
        if field_name in state:
            return state[field_name]
    return None


def trait_requires_signal_selection(raw_state: dict[str, Any], normalized_trait_state: dict[str, Any], trait_id: str) -> bool:
    if normalized_trait_state["skipped"]:
        return False
    if normalized_trait_state["absolute_disqualifier"]:
        return False
    return count_selected_trait_checkbox_entries(raw_state, trait_id) == 0


def _count_selected_entries(selection_value: Any, trait_id: str) -> int:
    if isinstance(selection_value, dict):
        return _count_selected_mapping_entries(selection_value, trait_id)
    if isinstance(selection_value, SELECTION_COLLECTION_TYPES):
        return _count_selected_sequence_entries(selection_value, trait_id)
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: expected mapping or list-like value."
    )


def _count_selected_mapping_entries(selection_value: dict[str, Any], trait_id: str) -> int:
    if _is_boolean_mapping(selection_value):
        return sum(1 for is_selected in selection_value.values() if is_selected)
    if _is_grouped_selection_mapping(selection_value):
        return sum(_count_selected_sequence_entries(group_value, trait_id) for group_value in selection_value.values())
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: mapping entries must be booleans or list-like groups."
    )


def _count_selected_sequence_entries(selection_value: Any, trait_id: str) -> int:
    count = 0
    for item in selection_value:
        count += _count_selected_sequence_item(item, trait_id)
    return count


def _count_selected_sequence_item(item: Any, trait_id: str) -> int:
    if isinstance(item, bool):
        return int(item)
    if isinstance(item, str):
        if item.strip():
            return 1
        raise ValueError(f"Trait '{trait_id}' has malformed trait checkbox selections: blank signal reference.")
    if isinstance(item, dict):
        return _count_selected_item_mapping(item, trait_id)
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: list items must be strings, booleans, or mappings."
    )


def _count_selected_item_mapping(item: dict[str, Any], trait_id: str) -> int:
    if "selected" not in item:
        raise ValueError(
            f"Trait '{trait_id}' has malformed trait checkbox selections: mapping items must include 'selected'."
        )
    if not isinstance(item.get("selected"), bool):
        raise ValueError(
            f"Trait '{trait_id}' has malformed trait checkbox selections: 'selected' must be a boolean."
        )
    return int(item["selected"])


def _is_boolean_mapping(selection_value: dict[str, Any]) -> bool:
    return bool(selection_value) and all(isinstance(value, bool) for value in selection_value.values())


def _is_grouped_selection_mapping(selection_value: dict[str, Any]) -> bool:
    return bool(selection_value) and all(
        isinstance(value, dict) or isinstance(value, SELECTION_COLLECTION_TYPES)
        for value in selection_value.values()
    )


def _find_trait_definition(trait_definitions: list[dict[str, Any]], trait_id: str) -> dict[str, Any]:
    candidate_ids = set(trait_id_aliases(trait_id))
    canonical_id = canonical_trait_id(trait_id)
    if canonical_id:
        candidate_ids.add(canonical_id)
    if not candidate_ids:
        return {}
    for trait_definition in trait_definitions:
        definition_ids = set(trait_id_aliases(trait_definition.get("trait_id")))
        definition_ids.update(str(alias).strip() for alias in trait_definition.get("trait_aliases", []) or [])
        if candidate_ids.intersection(definition_ids):
            return trait_definition
    runtime_prefix = _runtime_trait_id_alias(str(trait_id or ""))
    if not runtime_prefix:
        return {}
    for trait_definition in trait_definitions:
        candidate = str(trait_definition.get("trait_id", "") or "").strip()
        if candidate.startswith(runtime_prefix):
            return trait_definition
    return {}


def _runtime_trait_id_alias(trait_id: str) -> str:
    match = re.fullmatch(r"trait_(\d+)", trait_id.strip().lower())
    if not match:
        return ""
    numeric_prefix = f"T{int(match.group(1))}_"
    return numeric_prefix


def _empty_signal_ui_definition(trait_id: str) -> SignalUIDefinition:
    return default_signal_ui_definition(trait_id)


def _build_signal_ui_definition(runtime_bundle: dict[str, Any], trait_definition: dict[str, Any]) -> SignalUIDefinition:
    ui_config = runtime_bundle.get("config", {}).get("ui", {})
    core_config = ui_config.get("core_signals", {})
    extended_config = ui_config.get("extended_signals", {})
    signal_dictionary_index = build_signal_dictionary_index(runtime_bundle.get("signal_dictionary", {}))
    core_signals = normalize_core_signals(trait_definition.get("core_signals", []))
    extended_groups = normalize_extended_signal_groups(
        trait_definition,
        signal_dictionary_index=signal_dictionary_index,
    )
    core_signals = [_normalize_ui_signal(signal) for signal in core_signals]
    extended_groups = [_normalize_ui_group(group) for group in extended_groups]
    valid_signal_ids = _collect_valid_signal_ids(core_signals)
    for group in extended_groups:
        valid_signal_ids.extend(_collect_valid_signal_ids(group.get("signals", [])))
    return {
        "trait_id": str(trait_definition.get("trait_id", "") or ""),
        "core_display_style": str(core_config.get("display_style", DEFAULT_CORE_DISPLAY_STYLE) or DEFAULT_CORE_DISPLAY_STYLE),
        "extended_display_style": str(
            extended_config.get("display_style", DEFAULT_EXTENDED_DISPLAY_STYLE) or DEFAULT_EXTENDED_DISPLAY_STYLE
        ),
        "core_section_label": str(core_config.get("section_label", DEFAULT_CORE_SECTION_LABEL) or DEFAULT_CORE_SECTION_LABEL),
        "extended_section_label": str(
            extended_config.get("section_label", DEFAULT_EXTENDED_SECTION_LABEL) or DEFAULT_EXTENDED_SECTION_LABEL
        ),
        "extended_collapsible": bool(extended_config.get("collapsible", DEFAULT_EXTENDED_COLLAPSIBLE)),
        "extended_default_collapsed": bool(
            extended_config.get("default_collapsed", DEFAULT_EXTENDED_DEFAULT_COLLAPSED)
        ),
        "core_signals": core_signals,
        "extended_groups": extended_groups,
        "valid_signal_ids": list(dict.fromkeys(valid_signal_ids)),
    }


def _normalize_ui_group(group: dict[str, Any]) -> dict[str, Any]:
    normalized_group = dict(group)
    normalized_group["signals"] = [_normalize_ui_signal(signal) for signal in group.get("signals", [])]
    return normalized_group


def _normalize_ui_signal(signal: dict[str, Any]) -> dict[str, Any]:
    normalized_signal = dict(signal)
    aliases = [str(alias).strip() for alias in signal.get("selection_aliases", []) if str(alias).strip()]
    if aliases:
        normalized_signal["signal_id"] = _preferred_ui_signal_id(aliases)
        normalized_signal["selection_aliases"] = aliases
        return normalized_signal
    signal_id = str(signal.get("signal_id", "") or "").strip()
    normalized_signal["selection_aliases"] = [signal_id] if signal_id else []
    return normalized_signal


def _preferred_ui_signal_id(aliases: list[str]) -> str:
    for alias in aliases:
        if alias.startswith("Q"):
            return alias
    if len(aliases) == 1:
        return aliases[0]
    return aliases[1]


def _collect_valid_signal_ids(signals: list[dict[str, Any]]) -> list[str]:
    valid_signal_ids: list[str] = []
    for signal in signals:
        aliases = signal.get("selection_aliases", [])
        if aliases:
            valid_signal_ids.extend(str(alias).strip() for alias in aliases if str(alias).strip())
            continue
        signal_id = str(signal.get("signal_id", "") or "").strip()
        if signal_id:
            valid_signal_ids.append(signal_id)
    return valid_signal_ids

VALID_RAW_SCORES = {1, 2, 3, 4, 5}
CANONICAL_TRAIT_STATE_SCHEMA_VERSION = "1.2.0"
OUTPUT_SCHEMA_VERSION = "1.0.0"
DECISION_LABELS = {
    "strong_hire": "Hire",
    "hire": "Hire",
    "borderline": "Borderline",
    "no_hire": "No Hire",
}


def build_trait_scoring_payload(
    rubric: dict[str, Any],
    track_key: Any,
    trait_state: dict[str, dict[str, Any]] | None,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> dict[str, Any]:
    runtime_bundle = load_module_contract_runtime_bundle(
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    normalized_state = normalize_app_trait_state(trait_state)
    validate_normalized_state(normalized_state)
    engine_output = invoke_scoring_engine(
        rubric,
        track_key,
        normalized_state,
        runtime_bundle=runtime_bundle,
        engine_runtime_contract_path=runtime_bundle["runtime_contract_path"],
    )
    return map_engine_output_to_normalized_shape(
        rubric=rubric,
        track_key=track_key,
        normalized_state=normalized_state,
        engine_output=engine_output,
        runtime_bundle=runtime_bundle,
    )


def load_module_contract_runtime_bundle(
    *,
    engine_module_contract_path: str | Path,
    engine_runtime_contract_path: str | Path,
) -> dict[str, Any]:
    module_contract_path = _resolve_contract_path(engine_module_contract_path)
    module_contract = _load_yaml(module_contract_path)
    runtime_contract_path = _resolve_contract_path(engine_runtime_contract_path)
    runtime_bundle = _load_runtime_bundle(runtime_contract_path)
    runtime_bundle_with_path = {**runtime_bundle, "runtime_contract_path": str(runtime_contract_path)}
    trait_definitions = load_trait_definitions(runtime_bundle_with_path)
    runtime_error = runtime_bundle.get("runtime_error")
    metadata = {
        "module_contract": module_contract,
        "runtime_contract_path": str(runtime_contract_path),
        "runtime_bundle_loaded": bool(runtime_bundle) and not runtime_error,
        "runtime_error": runtime_error,
        "trait_definitions": trait_definitions,
    }
    return {**runtime_bundle, **metadata}


def load_trait_definitions(runtime_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return load_trait_definitions_from_runtime_bundle(runtime_bundle)


def validate_runtime_bundle_metadata(runtime_bundle: dict[str, Any]) -> None:
    runtime_error = str(runtime_bundle.get("runtime_error", "") or "").strip()
    if runtime_error:
        raise ReportingValidationError(f"Unable to load scoring runtime bundle: {runtime_error}")
    if runtime_bundle.get("runtime_bundle_loaded"):
        return
    runtime_contract_path = runtime_bundle.get("runtime_contract_path", "runtime bundle")
    raise ReportingValidationError(f"Unable to load scoring runtime bundle: {runtime_contract_path}")


def normalize_skipped(value: Any) -> bool:
    return _normalize_bool(value)


def normalize_absolute_disqualifier(value: Any) -> bool:
    return _normalize_bool(value)


def coerce_raw_score(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in VALID_RAW_SCORES:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            if parsed in VALID_RAW_SCORES:
                return parsed
        return None
    return None


def normalize_verbatim_notes(value: Any) -> str:
    return str(value or "").strip()


def normalize_trait_state_item(state: dict[str, Any] | None) -> dict[str, Any]:
    source = state if isinstance(state, dict) else {}
    skipped = normalize_skipped(source.get("skipped", False))
    raw_score = coerce_raw_score(source.get("raw_score"))
    selected_signal_ids = _normalize_selected_signal_ids(source)
    model_suggestions = [
        {
            "signal_id": str(item.get("signal_id") or "").strip(),
            "confidence": _normalize_model_confidence(item.get("confidence")),
            "rationale": str(item.get("rationale") or "").strip()[:180],
            "evidence_quote": str(item.get("evidence_quote") or "").strip()[:220],
        }
        for item in source.get("model_signal_suggestions", []) or []
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    normalized = {
        "schema_version": CANONICAL_TRAIT_STATE_SCHEMA_VERSION,
        "raw_score": raw_score,
        "raw_score_invalid": _is_invalid_raw_score_input(source.get("raw_score")),
        "suggested_raw_score": coerce_raw_score(source.get("suggested_raw_score")),
        "adjustment_reason": str(source.get("adjustment_reason") or "").strip(),
        "selected_signal_ids": selected_signal_ids,
        "model_signal_suggestions": model_suggestions,
        "skipped": skipped,
        "absolute_disqualifier": normalize_absolute_disqualifier(source.get("absolute_disqualifier", False)),
        "no_example_after_followups": _normalize_bool(source.get("no_example_after_followups", False)),
        "verbatim_notes": normalize_verbatim_notes(source.get("verbatim_notes")),
    }
    normalized["model_signal_override"] = trait_signal_override_state(normalized)
    if skipped:
        normalized["selected_signal_ids"] = []
        normalized["raw_score"] = None
        normalized["model_signal_override"] = trait_signal_override_state(normalized)
    return normalized


def normalize_app_trait_state(trait_state: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(trait_state, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for trait_id, state in trait_state.items():
        if not isinstance(state, dict):
            continue
        normalized[canonical_trait_id(trait_id)] = normalize_trait_state_item(state)
    return normalized


def validate_normalized_state(normalized_state: dict[str, dict[str, Any]]) -> None:
    for trait_id, state in normalized_state.items():
        if state.get("raw_score_invalid"):
            raise ReportingValidationError(f"Trait '{trait_id}' has invalid raw_score '{state.get('raw_score')}'.")
        raw_score = state.get("raw_score")
        if raw_score is not None and raw_score not in VALID_RAW_SCORES:
            raise ReportingValidationError(f"Trait '{trait_id}' has invalid raw_score '{raw_score}'.")
        if state.get("skipped"):
            continue
        suggested_raw_score = state.get("suggested_raw_score")
        if (
            suggested_raw_score is not None
            and raw_score is not None
            and suggested_raw_score != raw_score
            and not state.get("adjustment_reason")
        ):
            raise ReportingValidationError(
                f"Trait '{trait_id}' final raw score differs from suggested raw score but adjustment_reason is missing."
            )
        if state.get("absolute_disqualifier") and not state.get("verbatim_notes"):
            raise ReportingValidationError(
                f"Trait '{trait_id}' has disqualifier checked but no verbatim notes."
            )


def invoke_scoring_engine(
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    *,
    runtime_bundle: dict[str, Any],
    engine_runtime_contract_path: str | Path,
) -> dict[str, Any]:
    runtime_contract_path = _resolve_contract_path(engine_runtime_contract_path)
    trait_definitions = load_trait_definitions(runtime_bundle)
    validate_runtime_bundle_metadata(
        {
            "runtime_bundle_loaded": bool(runtime_bundle) and not runtime_bundle.get("runtime_error"),
            "runtime_error": runtime_bundle.get("runtime_error"),
            "runtime_contract_path": str(runtime_contract_path),
        }
    )
    _validate_trait_scoring_configuration(rubric, track_key, trait_definitions, normalized_state)

    engine = _build_trait_engine(runtime_bundle, runtime_contract_path)
    selections = _build_trait_selections(trait_definitions, normalized_state)
    session_result = engine.score_session(trait_definitions, selections)
    return _build_compatibility_engine_output(
        rubric=rubric,
        track_key=track_key,
        normalized_state=normalized_state,
        trait_definitions=trait_definitions,
        runtime_bundle=runtime_bundle,
        session_result=session_result,
    )


def _validate_trait_scoring_configuration(
    rubric: dict[str, Any],
    track_key: Any,
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> None:
    runtime_trait_ids = _trait_ids_from_runtime_definitions(trait_definitions)
    input_trait_ids = _trait_ids_from_normalized_state(normalized_state)
    resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    rubric_trait_ids = _trait_ids_from_rubric(rubric, resolved_track_key)

    _raise_for_missing_trait_overlap(input_trait_ids, runtime_trait_ids)
    _raise_for_rubric_runtime_mismatch(resolved_track_key, rubric_trait_ids, runtime_trait_ids)


def _trait_ids_from_runtime_definitions(trait_definitions: list[dict[str, Any]]) -> set[str]:
    return {
        canonical_trait_id(item.get("trait_id"))
        for item in trait_definitions
        if canonical_trait_id(item.get("trait_id"))
    }


def _trait_ids_from_normalized_state(normalized_state: dict[str, dict[str, Any]]) -> set[str]:
    return {canonical_trait_id(trait_id) for trait_id in normalized_state if canonical_trait_id(trait_id)}


def _trait_ids_from_rubric(rubric: dict[str, Any], resolved_track_key: str) -> set[str]:
    rubric_traits = _rubric_trait_map(rubric, resolved_track_key)
    return {trait_id for trait_id in rubric_traits if trait_id}


def _raise_for_missing_trait_overlap(input_trait_ids: set[str], runtime_trait_ids: set[str]) -> None:
    overlap = sorted(input_trait_ids.intersection(runtime_trait_ids))
    if overlap:
        return
    input_list = ", ".join(sorted(input_trait_ids)) or "<none>"
    runtime_list = ", ".join(sorted(runtime_trait_ids)) or "<none>"
    raise ReportingValidationError(
        "Trait scoring configuration mismatch: finalized trait inputs do not overlap "
        f"the trait runtime bundle. Input traits: {input_list}. Runtime traits: {runtime_list}."
    )


def _raise_for_rubric_runtime_mismatch(
    resolved_track_key: str,
    rubric_trait_ids: set[str],
    runtime_trait_ids: set[str],
) -> None:
    if not rubric_trait_ids:
        raise ReportingValidationError(
            "Trait scoring configuration mismatch: rubric track "
            f"'{resolved_track_key}' does not define any trait-scoring entries."
        )
    missing_runtime_traits = sorted(rubric_trait_ids.difference(runtime_trait_ids))
    if not missing_runtime_traits:
        return
    runtime_list = ", ".join(sorted(runtime_trait_ids)) or "<none>"
    missing_list = ", ".join(missing_runtime_traits)
    raise ReportingValidationError(
        "Trait scoring configuration mismatch: rubric track "
        f"'{resolved_track_key}' includes traits missing from the runtime bundle: {missing_list}. "
        f"Runtime traits: {runtime_list}."
    )


def map_engine_output_to_normalized_shape(
    *,
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    engine_output: dict[str, Any],
    runtime_bundle: dict[str, Any],
) -> dict[str, Any]:
    rows = list(engine_output.get("rows", []) or [])
    traits = [_map_trait_row(row) for row in rows]
    resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    summary = _build_summary(engine_output)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "track_key": resolved_track_key,
        "summary": summary,
        "traits": traits,
        "rows": rows,
        "normalized_state": normalized_state,
        "engine_metadata": runtime_bundle,
        **summary,
    }


def _build_summary(engine_output: dict[str, Any]) -> dict[str, Any]:
    configured_max = int(engine_output.get("configured_max_weighted_total", 0) or 0)
    included_max = int(engine_output.get("max_weighted_total_included_traits", 0) or 0)
    denominator = _resolve_scoring_denominator(engine_output, included_max, configured_max)
    percent_label = _resolve_percent_label(engine_output, denominator)
    return {
        "weighted_total": int(engine_output.get("weighted_total", 0) or 0),
        "configured_max_weighted_total": configured_max,
        "max_weighted_total": int(engine_output.get("max_weighted_total", denominator) or denominator),
        "max_weighted_total_included_traits": included_max,
        "percent_denominator": denominator,
        "percent_of_max": float(engine_output.get("percent_of_max", 0.0) or 0.0),
        "percent_of_max_label": percent_label,
        "percent_label": percent_label,
        "outcome": str(engine_output.get("outcome", "") or ""),
        "critical_eq_1": bool(engine_output.get("critical_eq_1", False)),
        "critical_lt_3": bool(engine_output.get("critical_lt_3", False)),
        "any_critical_selected": bool(engine_output.get("any_critical_selected", False)),
        "disqualifier_present": bool(engine_output.get("disqualifier_present", False)),
        "auto_no_hire_present": bool(engine_output.get("auto_no_hire_present", False)),
        "triggered_critical": bool(engine_output.get("triggered_critical", False)),
        "locked_rule": engine_output.get("locked_rule"),
        "override_rationale": engine_output.get("override_rationale"),
        "skipped_traits_count": int(engine_output.get("skipped_traits_count", 0) or 0),
        "scored_traits_count": int(engine_output.get("scored_traits_count", 0) or 0),
    }


def _resolve_scoring_denominator(engine_output: dict[str, Any], included_max: int, configured_max: int) -> int:
    explicit_denominator = engine_output.get("percent_denominator")
    if explicit_denominator is not None:
        return int(explicit_denominator or 0)
    if included_max > 0:
        return included_max
    return configured_max


def _resolve_percent_label(engine_output: dict[str, Any], denominator: int) -> str:
    label = str(engine_output.get("percent_of_max_label", "") or "").strip()
    if label:
        return label
    if denominator <= 0:
        return "N/A (all questions skipped)"
    percent_value = float(engine_output.get("percent_of_max", 0.0) or 0.0)
    return f"{percent_value}%"


def _map_trait_row(row: dict[str, Any]) -> dict[str, Any]:
    signal_counts = row.get("signal_counts", {}) or {}
    return {
        "trait_id": str(row.get("trait_id", "") or ""),
        "trait_name": str(row.get("trait_name", "") or ""),
        "priority": row.get("priority"),
        "weight": int(row.get("weight", 0) or 0),
        "primary_question": str(row.get("primary_question", "") or ""),
        "score": {
            "raw": row.get("raw_score"),
            "raw_for_math": int(row.get("raw_score_math", 0) or 0),
            "weighted": int(row.get("weighted_score", 0) or 0),
            "skipped": bool(row.get("skipped", False)),
            "net_signal_score": row.get("net_signal_score"),
            "suggested_raw_score": row.get("suggested_raw_score"),
            "final_raw_score": row.get("final_raw_score"),
            "interviewer_adjusted": bool(row.get("interviewer_adjusted", False)),
            "adjustment_reason": str(row.get("adjustment_reason", "") or ""),
        },
        "flags": {
            "absolute_disqualifier": bool(row.get("absolute_disqualifier", False)),
            "auto_no_hire_present": bool(row.get("auto_no_hire_present", False)),
            "auto_no_hire_signal_ids": list(row.get("auto_no_hire_signal_ids", []) or []),
            "no_example_after_followups": bool(row.get("no_example_after_followups", False)),
        },
        "notes": {
            "verbatim": str(row.get("verbatim_notes", "") or ""),
            "question_notes": str(row.get("question_notes", "") or ""),
            "trait_notes": str(row.get("trait_notes", "") or ""),
        },
        "signal_counts": {
            "core": int(signal_counts.get("core", 0) or 0),
            "extended": int(signal_counts.get("extended", 0) or 0),
        },
        "model_signal_suggestions": list(row.get("model_signal_suggestions", []) or []),
        "model_signal_override": dict(row.get("model_signal_override", {}) or {}),
        "session_trait_outcome": str(row.get("session_trait_outcome", "") or ""),
    }


def _is_invalid_raw_score_input(value: Any) -> bool:
    if value in {None, ""}:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return coerce_raw_score(value) is None


def _normalize_selected_signal_ids(source: dict[str, Any]) -> list[str]:
    for field_name in ("selected_signal_ids", "selected_signals", "signal_selections"):
        if field_name not in source:
            continue
        normalized = _normalize_selection_variant(source[field_name])
        return normalized
    return []


def _normalize_selection_variant(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return _normalize_selection_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return _normalize_selection_sequence(value)
    return []


def _normalize_selection_mapping(value: dict[str, Any]) -> list[str]:
    if not value:
        return []
    if all(isinstance(item, bool) for item in value.values()):
        return [key.strip() for key, is_selected in value.items() if is_selected and str(key).strip()]

    selected_signal_ids: list[str] = []
    for group_value in value.values():
        selected_signal_ids.extend(_normalize_selection_variant(group_value))
    return _dedupe_signal_ids(selected_signal_ids)


def _normalize_selection_sequence(value: list[Any] | tuple[Any, ...] | set[Any]) -> list[str]:
    selected_signal_ids: list[str] = []
    for item in value:
        signal_id = _normalize_selection_item(item)
        if signal_id:
            selected_signal_ids.append(signal_id)
    return _dedupe_signal_ids(selected_signal_ids)


def _normalize_selection_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    if not item.get("selected"):
        return ""
    for field_name in ("signal_id", "ref", "id", "value"):
        candidate = str(item.get(field_name, "") or "").strip()
        if candidate:
            return candidate
    return ""


def _dedupe_signal_ids(signal_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(signal_id for signal_id in signal_ids if signal_id))


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in {"true", "1", "yes", "on"}:
            return True
        if stripped in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


def _contract_resolution_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_contract_path_from_base(path: str | Path, *, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _resolve_contract_path(path: str | Path) -> Path:
    return _resolve_contract_path_from_base(path, base_dir=_contract_resolution_base_dir())


def _load_yaml(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {}
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _load_runtime_bundle(runtime_contract_path: Path) -> dict[str, Any]:
    resolved_path = runtime_contract_path.expanduser().resolve()
    if not resolved_path.exists():
        return {}

    try:
        engine_class = _load_trait_engine_class(resolved_path)
        config, signal_dictionary, traits, resolved_paths = engine_class.load_runtime_bundle(resolved_path)
    except (FileNotFoundError, ImportError, KeyError, PermissionError, TypeError, ValueError) as exc:
        return {"runtime_error": str(exc), "traits": []}

    return {
        "config": config,
        "signal_dictionary": signal_dictionary,
        "traits": traits,
        "resolved_paths": {key: str(value) for key, value in resolved_paths.items()},
    }


def _load_trait_engine_class(runtime_contract_path: Path) -> type[Any]:
    engine_module_path = runtime_contract_path.resolve().parent / "trait_based_scoring_engine.py"
    spec = importlib.util.spec_from_file_location("trait_based_scoring_engine_adapter", engine_module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load scoring engine module from {engine_module_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScoringEngine


def _has_trait_definition_overlap(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> bool:
    trait_ids = {canonical_trait_id(item.get("trait_id")) for item in trait_definitions if canonical_trait_id(item.get("trait_id"))}
    normalized_ids = {canonical_trait_id(trait_id) for trait_id in normalized_state if canonical_trait_id(trait_id)}
    return bool(trait_ids.intersection(normalized_ids))


def _build_trait_engine(runtime_bundle: dict[str, Any], runtime_contract_path: Path) -> Any:
    engine_class = _load_trait_engine_class(Path(runtime_contract_path))
    return engine_class(runtime_bundle["config"], runtime_bundle["signal_dictionary"])


def _build_trait_selections(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    selections: dict[str, list[str]] = {}
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        if not trait_id:
            continue
        state = normalized_state.get(trait_id) or {}
        selections[trait_id] = _select_signal_refs_for_state(trait_definition, state)
    return selections


def _select_signal_refs_for_state(trait_definition: dict[str, Any], state: dict[str, Any]) -> list[str]:
    if state.get("skipped"):
        return []
    model_signal_ids = [
        str(item.get("signal_id") or "").strip()
        for item in state.get("model_signal_suggestions", []) or []
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    selected_signal_ids = {
        str(signal_id).strip()
        for signal_id in (model_signal_ids or state.get("selected_signal_ids", []) or [])
        if str(signal_id).strip()
    }
    resolved_refs: list[str] = []
    for signal in _iter_trait_signals(trait_definition):
        runtime_signal_id = resolve_trait_signal_runtime_id(signal)
        if not runtime_signal_id:
            continue
        if resolve_trait_signal_weight(signal) == 0:
            continue
        if not selected_signal_ids.intersection(signal_selection_aliases(signal)):
            continue
        resolved_refs.append(runtime_signal_id)
    return resolved_refs


def _positive_signal_refs(trait_definition: dict[str, Any]) -> list[str]:
    return _signal_refs_by_weight(trait_definition, positive=True)


def _negative_signal_refs(trait_definition: dict[str, Any]) -> list[str]:
    return _signal_refs_by_weight(trait_definition, positive=False)


def _signal_refs_by_weight(trait_definition: dict[str, Any], *, positive: bool) -> list[str]:
    refs: list[str] = []
    for signal in _iter_trait_signals(trait_definition):
        weight = resolve_trait_signal_weight(signal)
        signal_id = resolve_trait_signal_selection_id(signal)
        if positive and weight > 0 and signal_id:
            refs.append(signal_id)
        if (not positive) and weight < 0 and signal_id:
            refs.append(signal_id)
    return refs


def _iter_trait_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    return iter_trait_schema_signals(trait_definition)


def _build_compatibility_engine_output(
    *,
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    trait_definitions: list[dict[str, Any]],
    runtime_bundle: dict[str, Any],
    session_result: dict[str, Any],
) -> dict[str, Any]:
    session_traits = {
        canonical_trait_id(item.get("trait_id")): item
        for item in session_result.get("traits", []) or []
        if canonical_trait_id(item.get("trait_id"))
    }
    resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    rubric_trait_map = _rubric_trait_map(rubric, resolved_track_key)
    rows = _build_rows(trait_definitions, rubric_trait_map, normalized_state, session_traits, session_result, runtime_bundle)
    weighted_total = sum(int(row.get("weighted_score", 0) or 0) for row in rows)
    configured_max_weighted_total = _configured_max_weighted_total(rubric, resolved_track_key, trait_definitions)
    included_max_weighted_total = _max_weighted_total(trait_definitions, normalized_state)
    percent_denominator = _resolve_percent_denominator(included_max_weighted_total, configured_max_weighted_total)
    percent_of_max = _percent_of_max(weighted_total, percent_denominator)
    percent_label = _percent_label(percent_of_max, included_max_weighted_total)
    missing_required_scores = any(
        not row.get("skipped", False) and row.get("raw_score") is None
        for row in rows
    )
    auto_no_hire_present = any(bool(row.get("auto_no_hire_present")) for row in rows)
    disqualifier_present = any(bool(state.get("absolute_disqualifier")) for state in normalized_state.values())
    any_critical_selected = bool(session_result.get("any_critical_selected", False))
    triggered_critical = bool(session_result.get("triggered_critical", False))
    critical_eq_1 = any(
        str(row.get("priority") or "").strip().lower() == "critical"
        and row.get("raw_score") == 1
        and not row.get("skipped", False)
        for row in rows
    )
    critical_lt_3 = any(
        str(row.get("priority") or "").strip().lower() == "critical"
        and row.get("raw_score") is not None
        and int(row.get("raw_score") or 0) < 3
        and not row.get("skipped", False)
        for row in rows
    )
    locked_rule = session_result.get("locked_rule")
    override_rationale = session_result.get("override_rationale")
    if missing_required_scores:
        outcome = "Incomplete"
        locked_rule = "One or more applicable traits are missing final raw scores"
    elif auto_no_hire_present:
        outcome = "No Hire"
        locked_rule = "DeepSeek automatic no-hire signal observed => Immediate NO HIRE"
    elif disqualifier_present:
        outcome = "No Hire"
        locked_rule = "Any Absolute Disqualifier observed => Immediate NO HIRE"
    elif critical_eq_1:
        outcome = "No Hire"
        locked_rule = "Any Critical trait raw score = 1 => Immediate NO HIRE"
    elif critical_lt_3:
        outcome = "No Hire"
        locked_rule = "Any Critical trait raw score < 3 => Cannot assign HIRE"
    elif percent_of_max >= 80:
        outcome = "Hire"
    elif percent_of_max >= 65:
        outcome = "Borderline"
    else:
        outcome = "No Hire"
    return {
        "rows": rows,
        "weighted_total": weighted_total,
        "configured_max_weighted_total": configured_max_weighted_total,
        "max_weighted_total": percent_denominator,
        "max_weighted_total_included_traits": included_max_weighted_total,
        "percent_denominator": percent_denominator,
        "percent_of_max": percent_of_max,
        "percent_of_max_label": percent_label,
        "percent_label": percent_label,
        "skipped_traits_count": sum(1 for state in normalized_state.values() if state.get("skipped")),
        "scored_traits_count": sum(1 for state in normalized_state.values() if not state.get("skipped")),
        "critical_eq_1": critical_eq_1,
        "critical_lt_3": critical_lt_3,
        "any_critical_selected": any_critical_selected,
        "disqualifier_present": disqualifier_present,
        "auto_no_hire_present": auto_no_hire_present,
        "triggered_critical": triggered_critical,
        "locked_rule": locked_rule,
        "override_rationale": override_rationale,
        "outcome": outcome,
        "session_result": session_result,
    }


def _rubric_trait_map(rubric: dict[str, Any], resolved_track_key: str) -> dict[str, dict[str, Any]]:
    rubric_traits = rubric.get("traits", []) or []
    mapping: dict[str, dict[str, Any]] = {}
    for trait in rubric_traits:
        tracks = trait.get("applicable_tracks", []) or []
        if tracks and "all" not in tracks and resolved_track_key not in tracks:
            continue
        trait_id = canonical_trait_id(trait.get("id"))
        if trait_id:
            mapping[trait_id] = trait
    return mapping


def _build_rows(
    trait_definitions: list[dict[str, Any]],
    rubric_trait_map: dict[str, dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
    session_traits: dict[str, dict[str, Any]],
    session_result: dict[str, Any],
    runtime_bundle: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        if not trait_id:
            continue
        state = normalized_state.get(trait_id) or {}
        session_trait = session_traits.get(trait_id, {})
        rubric_trait = rubric_trait_map.get(trait_id, {})
        rows.append(_build_trait_row(trait_definition, rubric_trait, state, session_trait, session_result, runtime_bundle))
    return rows


def _build_trait_row(
    trait_definition: dict[str, Any],
    rubric_trait: dict[str, Any],
    state: dict[str, Any],
    session_trait: dict[str, Any],
    session_result: dict[str, Any],
    runtime_bundle: dict[str, Any],
) -> dict[str, Any]:
    raw_score = state.get("raw_score")
    skipped = bool(state.get("skipped", False)) or raw_score is None
    canonical_id = canonical_trait_id(trait_definition.get("trait_id"))
    trait_label = str(rubric_trait.get("id") or canonical_id or "")
    weight = int(rubric_trait.get("weight", 0) or 0)
    system_checkbox_score = 0 if skipped else int(raw_score or 0) * weight
    advisory = _score_trait_signal_advisory(
        trait_definition,
        list(state.get("model_signal_suggestions", []) or []),
        runtime_bundle=runtime_bundle,
    )
    suggested_raw_score = advisory.get("suggested_raw_score")
    deepseek_score = int(suggested_raw_score) * weight if suggested_raw_score is not None else None
    adjustment_reason = str(state.get("adjustment_reason") or "").strip()
    return {
        "trait_id": canonical_id,
        "trait_name": str(rubric_trait.get("name") or trait_label),
        "priority": rubric_trait.get("priority"),
        "weight": weight,
        "skipped": skipped,
        "raw_score": raw_score,
        "raw_score_math": int(raw_score or 0),
        "weighted_score": system_checkbox_score,
        "system_checkbox_score": system_checkbox_score,
        "net_signal_score": advisory.get("net_signal_score"),
        "suggested_raw_score": suggested_raw_score,
        "final_raw_score": raw_score,
        "interviewer_adjusted": bool(
            suggested_raw_score is not None
            and raw_score is not None
            and int(raw_score) != int(suggested_raw_score)
        ),
        "adjustment_reason": adjustment_reason,
        "deepseek_calculated_score": deepseek_score,
        "deepseek_signal_score": deepseek_score,
        "auto_no_hire_present": bool(advisory.get("auto_no_hire_signal_ids")),
        "auto_no_hire_signal_ids": list(advisory.get("auto_no_hire_signal_ids", []) or []),
        "auto_no_hire_reasons": list(advisory.get("auto_no_hire_reasons", []) or []),
        "auto_no_hire_quotes": list(advisory.get("auto_no_hire_quotes", []) or []),
        "verbatim_notes": str(state.get("verbatim_notes", "") or ""),
        "no_example_after_followups": bool(state.get("no_example_after_followups", False)),
        "absolute_disqualifier": bool(state.get("absolute_disqualifier", False)),
        "primary_question": str(rubric_trait.get("primary_question") or trait_definition.get("question", "") or ""),
        "question_notes": "",
        "trait_notes": "",
        "signal_counts": {
            "core": len(session_trait.get("selected_core", []) or []),
            "extended": len(session_trait.get("selected_extended", []) or []),
        },
        "selected_signal_ids": list(state.get("selected_signal_ids", []) or []),
        "model_signal_suggestions": list(state.get("model_signal_suggestions", []) or []),
        "model_signal_override": dict(state.get("model_signal_override", {}) or {}),
        "session_trait_outcome": str(session_result.get("decision", "") or ""),
        "trait_aliases": trait_id_aliases(trait_definition.get("trait_id")),
    }


def _score_trait_signal_ids(
    trait_definition: dict[str, Any],
    signal_ids: list[str],
    *,
    runtime_bundle: dict[str, Any],
) -> int | float | None:
    if not signal_ids:
        return None
    selected_refs = set(_select_signal_refs_for_state(trait_definition, {"selected_signal_ids": signal_ids}))
    if not selected_refs:
        return None
    advisory = _score_trait_signal_advisory(
        trait_definition,
        [{"signal_id": signal_id} for signal_id in signal_ids],
        runtime_bundle=runtime_bundle,
    )
    return advisory.get("net_signal_score")


def _score_trait_signal_advisory(
    trait_definition: dict[str, Any],
    model_signal_suggestions: list[dict[str, Any]],
    *,
    runtime_bundle: dict[str, Any],
) -> dict[str, Any]:
    signal_ids = [
        str(item.get("signal_id") or "").strip()
        for item in model_signal_suggestions
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    ]
    if not signal_ids:
        return {
            "net_signal_score": None,
            "suggested_raw_score": None,
            "auto_no_hire_signal_ids": [],
            "auto_no_hire_reasons": [],
            "auto_no_hire_quotes": [],
        }
    selected_refs = set(_select_signal_refs_for_state(trait_definition, {"selected_signal_ids": signal_ids}))
    if not selected_refs:
        return {
            "net_signal_score": None,
            "suggested_raw_score": None,
            "auto_no_hire_signal_ids": [],
            "auto_no_hire_reasons": [],
            "auto_no_hire_quotes": [],
        }
    signal_dictionary = {
        str(item.get("id") or "").strip(): item
        for item in (runtime_bundle.get("signal_dictionary", {}) or {}).get("signals", []) or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    suggestions_by_id = {
        str(item.get("signal_id") or "").strip(): item
        for item in model_signal_suggestions
        if isinstance(item, dict) and str(item.get("signal_id") or "").strip()
    }

    def signal_weight(signal: dict[str, Any]) -> float:
        for field_name in ("weight", "base_weight", "default_weight"):
            if field_name in signal and not isinstance(signal.get(field_name), bool):
                value = signal.get(field_name)
                if isinstance(value, str) and value.strip().upper() == "AUTO_NO_HIRE":
                    return 0.0
                return float(value or 0)
        signal_id = resolve_trait_signal_runtime_id(signal)
        dictionary_signal = signal_dictionary.get(signal_id, {})
        return float(dictionary_signal.get("default_weight", 0) or 0)

    def is_auto_no_hire(signal: dict[str, Any]) -> bool:
        if bool(signal.get("is_auto_no_hire", False)) or bool(signal.get("auto_no_hire", False)):
            return True
        if str(signal.get("signal_category", "") or "").strip().lower() == "automatic_no_hire":
            return True
        return str(signal.get("base_weight", "") or "").strip().upper() == "AUTO_NO_HIRE"

    net_signal_score = 0.0
    auto_no_hire_signal_ids: list[str] = []
    auto_no_hire_reasons: list[str] = []
    auto_no_hire_quotes: list[str] = []
    for signal in _iter_trait_signals(trait_definition):
        runtime_signal_id = resolve_trait_signal_runtime_id(signal)
        if runtime_signal_id not in selected_refs:
            continue
        net_signal_score += signal_weight(signal)
        if is_auto_no_hire(signal):
            auto_no_hire_signal_ids.append(runtime_signal_id)
            auto_no_hire_reasons.append(str(signal.get("label") or runtime_signal_id))
            suggestion = suggestions_by_id.get(runtime_signal_id, {})
            quote = str(suggestion.get("evidence_quote") or "").strip()
            if quote:
                auto_no_hire_quotes.append(quote)
    score_value: int | float
    if net_signal_score.is_integer():
        score_value = int(net_signal_score)
    else:
        score_value = round(net_signal_score, 2)
    return {
        "net_signal_score": score_value,
        "suggested_raw_score": _signal_score_to_raw_score(score_value),
        "auto_no_hire_signal_ids": auto_no_hire_signal_ids,
        "auto_no_hire_reasons": auto_no_hire_reasons,
        "auto_no_hire_quotes": auto_no_hire_quotes,
    }


def _max_weighted_total(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> int:
    total = 0.0
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        state = normalized_state.get(trait_id) or {}
        if state.get("skipped") or state.get("raw_score") is None:
            continue
        total += _max_trait_final_score(trait_definition)
    return int(round(total))


def _max_trait_final_score(trait_definition: dict[str, Any]) -> float:
    core_total = sum(
        max(resolve_trait_signal_weight(signal), 0.0)
        for signal in trait_definition.get("core_signals", []) or []
        if isinstance(signal, dict)
    )
    extended_total = sum(max(resolve_trait_signal_weight(signal), 0.0) for signal in _iter_extended_trait_signals(trait_definition))
    return core_total + extended_total


def _iter_extended_trait_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    return iter_trait_schema_signals(
        {
            "extended_signal_groups": trait_definition.get("extended_signal_groups", []),
            "extended_signals": trait_definition.get("extended_signals", []),
        }
    )


def _configured_max_weighted_total(
    rubric: dict[str, Any],
    resolved_track_key: str,
    trait_definitions: list[dict[str, Any]],
) -> int:
    track_cfg = ((rubric.get("tracks") or {}).get(resolved_track_key) or {})
    configured_value = track_cfg.get("max_weighted_total")
    if configured_value is not None:
        return int(configured_value or 0)
    return int(round(sum(_max_trait_final_score(trait_definition) for trait_definition in trait_definitions)))


def _resolve_percent_denominator(included_max_weighted_total: int, configured_max_weighted_total: int) -> int:
    if included_max_weighted_total > 0:
        return included_max_weighted_total
    return configured_max_weighted_total


def _percent_of_max(weighted_total: int, max_weighted_total: int) -> float:
    if max_weighted_total <= 0:
        return 0.0
    return round((float(weighted_total) * 100.0) / float(max_weighted_total), 2)


def _percent_label(percent_of_max: float, included_max_weighted_total: int) -> str:
    if included_max_weighted_total <= 0:
        return "N/A (all questions skipped)"
    return f"{percent_of_max}%"
_COMPAT_MODULES: tuple[str, ...] = (
    "trait_scoring_adapter",
)

_WRAPPER_POLICY = (
    "Legacy scoring/reporting modules are compatibility wrappers during flattening. "
    "New production imports should prefer scoring_reporting."
)


def available_modules() -> tuple[str, ...]:
    return _COMPAT_MODULES


def module_ownership() -> dict[str, str]:
    return {module_name: "scoring_reporting" for module_name in _COMPAT_MODULES}


def wrapper_policy() -> str:
    return _WRAPPER_POLICY


def load_compat_module(module_name: str) -> ModuleType:
    if module_name not in _COMPAT_MODULES:
        raise AttributeError(f"{module_name!r} is not part of scoring_reporting")
    return import_module(module_name)


def public_symbols(module_name: str | None = None) -> tuple[str, ...]:
    module_names = (module_name,) if module_name is not None else _COMPAT_MODULES
    symbols: set[str] = set()
    for compat_name in module_names:
        module = load_compat_module(compat_name)
        symbols.update(name for name in dir(module) if not name.startswith("_"))
    return tuple(sorted(symbols))


def resolve_compat_symbol(symbol_name: str) -> Any:
    for module_name in _COMPAT_MODULES:
        module = import_module(module_name)
        if hasattr(module, symbol_name):
            return getattr(module, symbol_name)
    raise AttributeError(f"scoring_reporting has no attribute {symbol_name!r}")


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"scoring_reporting has no attribute {name!r}")
    if name in _COMPAT_MODULES:
        return import_module(name)
    return resolve_compat_symbol(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_COMPAT_MODULES) | set(public_symbols()))

