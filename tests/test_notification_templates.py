from __future__ import annotations

from notification_models import NotificationRecipient, NotificationRule
from notification_templates import (
    NOTIFICATION_TEMPLATE_FIELD_CATALOG,
    notification_payload_from_mapping,
    render_notification_templates,
    validate_notification_rule,
)


def _rule(**updates: object) -> NotificationRule:
    values: dict[str, object] = {
        "event_type": "staffing.assignment.need_now",
        "label": "Position needed",
        "subject_template": "Need **{position_name}**",
        "body_template": "Hi **{person_name}**\n\n- Open [staffing](https://example.org)",
        "recipients": [NotificationRecipient(recipient_type="role", role_key="hiring_manager")],
        "active": True,
    }
    values.update(updates)
    return NotificationRule(**values)  # type: ignore[arg-type]


def test_notification_templates_render_safe_multipart_content() -> None:
    rendered = render_notification_templates(
        _rule(body_template="Hi **{person_name}** <script>alert(1)</script>\n\n- [Open](javascript:alert(1))"),
        {"position_name": "Teacher", "person_name": "<Alex>"},
    )

    assert rendered.subject == "Need **Teacher**"
    assert "Hi <Alex>" in rendered.plain_body
    assert "<strong>&lt;Alex&gt;</strong>" in rendered.html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.html_body
    assert "href=" not in rendered.html_body
    assert rendered.unresolved_fields == ()


def test_notification_validation_blocks_incomplete_enabled_rule_but_warns_when_disabled() -> None:
    enabled = _rule(subject_template="", body_template="", recipients=[])
    disabled = _rule(subject_template="", body_template="", recipients=[], active=False)

    enabled_issues = validate_notification_rule(enabled)
    disabled_issues = validate_notification_rule(disabled)

    assert {issue.code for issue in enabled_issues if issue.blocking} == {
        "missing_subject",
        "missing_body",
        "missing_recipients",
    }
    assert {issue.code for issue in disabled_issues if not issue.blocking} >= {
        "missing_subject",
        "missing_body",
        "missing_recipients",
    }
    assert not any(issue.blocking for issue in disabled_issues)


def test_notification_validation_blocks_unknown_variable_only_when_enabled() -> None:
    enabled = validate_notification_rule(_rule(subject_template="Hello {invented_field}"))
    disabled = validate_notification_rule(_rule(subject_template="Hello {invented_field}", active=False))

    assert any(issue.code == "unknown_fields" and issue.blocking for issue in enabled)
    assert any(issue.code == "unknown_fields" and not issue.blocking for issue in disabled)


def test_notification_catalog_exposes_curated_staffing_and_interview_fields() -> None:
    keys = {field.key for field in NOTIFICATION_TEMPLATE_FIELD_CATALOG}

    assert {
        "notice_date",
        "final_day",
        "permit_status",
        "ece_units",
        "degree",
        "years_experience",
        "interview_answers_summary",
        "interview_answer_1",
    }.issubset(keys)
    assert not validate_notification_rule(
        _rule(subject_template="{person_name} gave notice on {notice_date}", body_template="{interview_answers_summary}")
    )


def test_notification_payload_from_mapping_extracts_safe_nested_report_values() -> None:
    payload = notification_payload_from_mapping(
        {
            "candidate_name": "Jordan Lee",
            "candidate": {
                "school": "Hawthorne",
                "qualification": {
                    "degree_type": "BA",
                    "ece_units_completed": 24,
                    "years_experience": 5,
                },
            },
            "questions": [
                {"prompt": "Why preschool?", "transcript": "Because early learning matters."},
                {"title": "Guidance", "candidate_transcript": "I redirect with routines."},
            ],
            "audit_token": "do-not-expose",
        }
    )

    assert payload["candidate_name"] == "Jordan Lee"
    assert payload["school"] == "Hawthorne"
    assert payload["degree"] == "BA"
    assert payload["degree_type"] == "BA"
    assert payload["ece_units"] == "24"
    assert payload["years_experience"] == "5"
    assert payload["interview_answer_1"] == "Because early learning matters."
    assert payload["interview_answers_summary"] == (
        "Why preschool?: Because early learning matters.\n"
        "Guidance: I redirect with routines."
    )
    assert "audit_token" not in payload
