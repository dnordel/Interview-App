from scoring_reporting import (
    find_unknown_placeholders,
    missing_placeholder_keys,
    render_template,
    validate_template_map,
)
import scoring_reporting


def test_find_unknown_placeholders_by_context() -> None:
    assert render_template is scoring_reporting.render_template
    unknown = find_unknown_placeholders("Hi {candidate_name} {bogus}", "director")
    assert unknown == {"bogus"}


def test_render_template_keeps_unknown_tokens() -> None:
    text = render_template("Hello {candidate_name} {missing}", {"candidate_name": "Ava"}, context="director")
    assert text == "Hello Ava {missing}"


def test_render_template_can_neutralize_unknown_tokens() -> None:
    text = render_template(
        "Hello {candidate_name} {missing}",
        {"candidate_name": "Ava"},
        context="director",
        unknown_policy="empty",
    )
    assert text == "Hello Ava "


def test_render_template_can_reject_unknown_tokens() -> None:
    try:
        render_template(
            "Hello {candidate_name} {missing}",
            {"candidate_name": "Ava"},
            context="director",
            unknown_policy="error",
        )
    except ValueError as exc:
        assert "missing" in str(exc)
        return
    raise AssertionError("Expected ValueError for unknown placeholder")


def test_render_template_supports_square_brackets() -> None:
    text = render_template("Hi [first_name] [candidate_name]", {"first_name": "Ava", "candidate_name": "Ava Smith"}, context="director")
    assert text == "Hi Ava Ava Smith"


def test_missing_placeholder_keys_returns_allowlisted_missing_items() -> None:
    template = "Hello [first_name] [last_name] [candidate_name]"
    values = {"candidate_name": "Ava Smith", "first_name": "Ava", "last_name": ""}
    missing = missing_placeholder_keys(template, values, "director")
    assert missing == ["last_name"]


def test_validate_template_map_returns_field_errors() -> None:
    templates = {
        "director_subject": "Director {candidate_name}",
        "director_body": "Use {bad}",
    }
    contexts = {
        "director_subject": "director",
        "director_body": "director",
    }
    errors = validate_template_map(templates, contexts)
    assert errors == {"director_body": {"bad"}}
