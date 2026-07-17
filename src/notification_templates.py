from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import Any
from urllib.parse import urlparse

from email_security import is_valid_email_address
from notification_models import NotificationRule


@dataclass(frozen=True)
class NotificationTemplateField:
    key: str
    label: str
    group: str


NOTIFICATION_TEMPLATE_FIELD_CATALOG = (
    NotificationTemplateField("candidate_name", "Candidate name", "Candidate"),
    NotificationTemplateField("candidate", "Candidate", "Candidate"),
    NotificationTemplateField("candidate_email", "Candidate email", "Candidate"),
    NotificationTemplateField("honorific", "Honorific", "Candidate"),
    NotificationTemplateField("person_name", "Person name", "Person"),
    NotificationTemplateField("school", "School", "School"),
    NotificationTemplateField("school_code", "School code", "School"),
    NotificationTemplateField("school_location", "School location", "School"),
    NotificationTemplateField("director_name", "Director name", "School"),
    NotificationTemplateField("hiring_manager_name", "Hiring manager", "Staffing"),
    NotificationTemplateField("recruiter_name", "Recruiter", "Staffing"),
    NotificationTemplateField("company_name", "Company", "System"),
    NotificationTemplateField("department", "Department", "Staffing"),
    NotificationTemplateField("location", "Location", "School"),
    NotificationTemplateField("program", "Program", "Staffing"),
    NotificationTemplateField("position", "Position", "Staffing"),
    NotificationTemplateField("position_name", "Position name", "Staffing"),
    NotificationTemplateField("position_type", "Position type", "Staffing"),
    NotificationTemplateField("position_title", "Position title", "Staffing"),
    NotificationTemplateField("classroom", "Classroom", "Staffing"),
    NotificationTemplateField("classroom_capacity", "Classroom capacity", "Staffing"),
    NotificationTemplateField("ratio_group", "Ratio group", "Staffing"),
    NotificationTemplateField("slot_group", "Slot group", "Staffing"),
    NotificationTemplateField("assignment_status", "Assignment status", "Staffing"),
    NotificationTemplateField("notes", "Notes", "Staffing"),
    NotificationTemplateField("start_date", "Start date", "Staffing"),
    NotificationTemplateField("shift_start", "Shift start", "Staffing"),
    NotificationTemplateField("shift_end", "Shift end", "Staffing"),
    NotificationTemplateField("notice_given", "Notice given", "Staffing"),
    NotificationTemplateField("notice_date", "Notice date", "Staffing"),
    NotificationTemplateField("date_notice_given", "Date notice given", "Staffing"),
    NotificationTemplateField("final_working_day", "Final working day", "Staffing"),
    NotificationTemplateField("final_day", "Final day", "Staffing"),
    NotificationTemplateField("last_working_day", "Last working day", "Staffing"),
    NotificationTemplateField("permit_status", "Permit status", "Person"),
    NotificationTemplateField("permit_effective_date", "Permit effective date", "Person"),
    NotificationTemplateField("permit_documentation_received", "Permit docs received", "Person"),
    NotificationTemplateField("permit_notes", "Permit notes", "Person"),
    NotificationTemplateField("ece_units", "ECE units", "Candidate"),
    NotificationTemplateField("ece_units_completed", "ECE units completed", "Candidate"),
    NotificationTemplateField("degree", "Degree", "Candidate"),
    NotificationTemplateField("has_degree", "Has degree", "Candidate"),
    NotificationTemplateField("degree_type", "Degree type", "Candidate"),
    NotificationTemplateField("degree_in_ece", "Degree in ECE", "Candidate"),
    NotificationTemplateField("degree_display", "Degree display", "Candidate"),
    NotificationTemplateField("degree_in_ece_display", "Degree in ECE display", "Candidate"),
    NotificationTemplateField("total_units_completed", "Total units", "Candidate"),
    NotificationTemplateField("infant_toddler_class_completed", "Infant/toddler class", "Candidate"),
    NotificationTemplateField("years_experience", "Years experience", "Candidate"),
    NotificationTemplateField("experience_years", "Experience years", "Candidate"),
    NotificationTemplateField("experience", "Experience", "Candidate"),
    NotificationTemplateField("interview_date", "Interview date", "Interview"),
    NotificationTemplateField("history_id", "History ID", "Interview"),
    NotificationTemplateField("outcome", "Outcome", "Interview"),
    NotificationTemplateField("score", "Score", "Interview"),
    NotificationTemplateField("interview_score", "Interview score", "Interview"),
    NotificationTemplateField("director_interview_score", "Director interview score", "Interview"),
    NotificationTemplateField("interview_answers_summary", "Interview answers summary", "Interview"),
    NotificationTemplateField("interview_answer_1", "Interview answer 1", "Interview"),
    NotificationTemplateField("interview_answer_2", "Interview answer 2", "Interview"),
    NotificationTemplateField("interview_answer_3", "Interview answer 3", "Interview"),
    NotificationTemplateField("interview_answer_4", "Interview answer 4", "Interview"),
    NotificationTemplateField("interview_answer_5", "Interview answer 5", "Interview"),
    NotificationTemplateField("offer_status", "Offer status", "Offer"),
    NotificationTemplateField("offer_path", "Offer path", "Offer"),
    NotificationTemplateField("offer_pdf_path", "Offer PDF path", "Offer"),
    NotificationTemplateField("requested_pay", "Requested pay", "Offer"),
    NotificationTemplateField("offer_amount", "Offer amount", "Offer"),
    NotificationTemplateField("proposed_classroom", "Proposed classroom", "Offer"),
    NotificationTemplateField("onboarding_guide_path", "Onboarding guide path", "Offer"),
    NotificationTemplateField("reply_by_date", "Reply by date", "Offer"),
    NotificationTemplateField("generated_date", "Generated date", "System"),
)

NOTIFICATION_TEMPLATE_FIELDS = tuple(field.key for field in NOTIFICATION_TEMPLATE_FIELD_CATALOG)


@dataclass(frozen=True)
class NotificationRuleIssue:
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class RenderedNotification:
    subject: str
    plain_body: str
    html_body: str
    unresolved_fields: tuple[str, ...] = ()


def notification_template_fields(rule: NotificationRule) -> tuple[str, ...]:
    fields: set[str] = set()
    for template in (rule.subject_template, rule.body_template):
        try:
            parsed = Formatter().parse(str(template or ""))
            for _literal, field_name, _format_spec, _conversion in parsed:
                if field_name:
                    fields.add(field_name.split(".", 1)[0].split("[", 1)[0])
        except ValueError:
            continue
    return tuple(sorted(fields))


def notification_payload_from_mapping(source: Mapping[str, Any]) -> dict[str, str]:
    payload: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        if key not in NOTIFICATION_TEMPLATE_FIELDS or value in (None, ""):
            return
        if isinstance(value, bool):
            payload[key] = "Yes" if value else "No"
        elif isinstance(value, (list, tuple)):
            payload[key] = "\n".join(str(item) for item in value if str(item).strip())
        else:
            payload[key] = str(value).strip()

    for key in NOTIFICATION_TEMPLATE_FIELDS:
        put(key, source.get(key))

    candidate = source.get("candidate") if isinstance(source.get("candidate"), Mapping) else {}
    qualification = candidate.get("qualification") if isinstance(candidate.get("qualification"), Mapping) else {}
    scoring = source.get("scoring") if isinstance(source.get("scoring"), Mapping) else {}

    put("candidate_name", candidate.get("candidate_name") or candidate.get("name") or source.get("candidate_name"))
    put("candidate", candidate.get("candidate_name") or candidate.get("name") or source.get("candidate"))
    put("candidate_email", candidate.get("candidate_email") or candidate.get("email") or source.get("candidate_email"))
    put("school", candidate.get("school") or source.get("school"))
    put("position", source.get("position") or source.get("candidate_position") or source.get("role"))
    put("interview_date", candidate.get("interview_date") or source.get("interview_date") or source.get("date"))
    put("history_id", source.get("history_id") or source.get("id"))
    put("outcome", scoring.get("outcome") or source.get("outcome"))
    score = scoring.get("percent_of_max") or scoring.get("weighted_total") or source.get("score")
    put("score", score)
    put("interview_score", score)

    degree = qualification.get("degree_type") or qualification.get("degree") or source.get("degree_type")
    has_degree = qualification.get("has_degree") if "has_degree" in qualification else source.get("has_degree")
    degree_in_ece = (
        qualification.get("degree_in_ece") if "degree_in_ece" in qualification else source.get("degree_in_ece")
    )
    put("degree", degree)
    put("degree_type", degree)
    put("has_degree", has_degree)
    put("degree_in_ece", degree_in_ece)
    has_degree_value = has_degree is True or str(has_degree).strip().casefold() in {"true", "yes", "1"}
    put("degree_display", degree if has_degree_value and degree else "No")
    if has_degree_value:
        degree_in_ece_value = degree_in_ece is True or str(degree_in_ece).strip().casefold() in {"true", "yes", "1"}
        payload["degree_in_ece_display"] = f"\nDegree in ECE: {'Yes' if degree_in_ece_value else 'No'}"
    ece_units = qualification.get("ece_units_completed") or source.get("ece_units_completed") or source.get("ece_units")
    put("ece_units", ece_units)
    put("ece_units_completed", ece_units)
    put("total_units_completed", qualification.get("total_units_completed") or source.get("total_units_completed"))
    put(
        "infant_toddler_class_completed",
        qualification.get("infant_toddler_class_completed") or source.get("infant_toddler_class_completed"),
    )
    experience = qualification.get("years_experience") or source.get("years_experience") or source.get("experience_years")
    put("years_experience", experience)
    put("experience_years", experience)
    put("experience", experience)

    questions = source.get("questions") if isinstance(source.get("questions"), list) else []
    answer_lines: list[str] = []
    for index, question in enumerate(item for item in questions if isinstance(item, Mapping)):
        if index >= 5:
            break
        answer = str(question.get("transcript") or question.get("candidate_transcript") or question.get("answer") or "").strip()
        if not answer:
            continue
        title = str(question.get("prompt") or question.get("title") or question.get("question") or f"Answer {index + 1}").strip()
        put(f"interview_answer_{index + 1}", answer)
        answer_lines.append(f"{title}: {answer}" if title else answer)
    put("interview_answers_summary", "\n".join(answer_lines))
    return payload


def validate_notification_rule(rule: NotificationRule) -> tuple[NotificationRuleIssue, ...]:
    issues: list[NotificationRuleIssue] = []

    def add(code: str, message: str, *, active_only: bool = False) -> None:
        issues.append(NotificationRuleIssue(code, message, bool(rule.active) if active_only else True))

    if not str(rule.event_type or "").strip():
        add("missing_event", "Event is required.")
    if not str(rule.label or "").strip():
        add("missing_label", "Label is required.")
    if rule.trigger_timing not in {"event", "date_offset"}:
        add("invalid_timing", "Timing must be Event or Reference date.")
    if rule.trigger_timing == "date_offset" and not str(rule.date_field or "").strip():
        add("missing_date_field", "Reference date field is required.")
    if not str(rule.subject_template or "").strip():
        add("missing_subject", "Subject template is required before enabling.", active_only=True)
    if not str(rule.body_template or "").strip():
        add("missing_body", "Body template is required before enabling.", active_only=True)
    if not [recipient for recipient in rule.recipients if recipient.active]:
        add("missing_recipients", "At least one active recipient is required before enabling.", active_only=True)

    for recipient in rule.recipients:
        if not recipient.active:
            continue
        if recipient.recipient_type == "email" and not is_valid_email_address(str(recipient.email or "")):
            add("invalid_recipient", "Invalid recipient email.")
        if recipient.recipient_type == "role" and recipient.role_key not in {
            "candidate", "director", "executive_director", "hiring_manager",
            "hr_manager", "payroll", "office_manager"
        }:
            add("invalid_recipient_role", "One or more recipient roles are invalid.")

    malformed = False
    for template in (rule.subject_template, rule.body_template):
        try:
            list(Formatter().parse(str(template or "")))
        except ValueError:
            malformed = True
    if malformed:
        add("malformed_template", "Subject or body contains malformed template braces.")

    unknown = sorted(set(notification_template_fields(rule)) - set(NOTIFICATION_TEMPLATE_FIELDS))
    if unknown:
        issues.append(
            NotificationRuleIssue(
                "unknown_fields",
                f"Unknown template variables: {', '.join(unknown)}.",
                bool(rule.active),
            )
        )
    return tuple(issues)


def render_notification_templates(rule: NotificationRule, payload: dict[str, str]) -> RenderedNotification:
    unresolved: set[str] = set()

    class SafeValues(dict[str, str]):
        def __missing__(self, key: str) -> str:
            unresolved.add(key)
            return "{" + key + "}"

    values = SafeValues({str(key): str(value) for key, value in payload.items()})
    try:
        subject = str(rule.subject_template or "").format_map(values)
        body = str(rule.body_template or "").format_map(values)
    except (KeyError, ValueError, AttributeError):
        subject = str(rule.subject_template or "")
        body = str(rule.body_template or "")
    return RenderedNotification(
        subject=subject,
        plain_body=_markdown_to_plain(body),
        html_body=_markdown_to_safe_html(body),
        unresolved_fields=tuple(sorted(unresolved)),
    )


def _markdown_to_plain(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _markdown_to_safe_html(value: str) -> str:
    escaped = html.escape(str(value or ""), quote=True)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"_([^_\n]+)_", r"<em>\1</em>", escaped)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), html.unescape(match.group(2))
        parsed = urlparse(url)
        if parsed.scheme.casefold() not in {"http", "https", "mailto"}:
            return label
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, escaped)
    output: list[str] = []
    in_list = False
    for line in escaped.splitlines():
        if line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            output.append("</ul>")
            in_list = False
        output.append(f"<p>{line}</p>" if line else "<br>")
    if in_list:
        output.append("</ul>")
    return "".join(output)
