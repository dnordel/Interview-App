from __future__ import annotations

from dataclasses import dataclass
import re
import tkinter as tk
from tkinter import ttk
from typing import Iterable

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
