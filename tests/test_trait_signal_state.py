from pathlib import Path

import yaml

from scoring_reporting import (
    count_selected_trait_checkbox_entries,
    default_signal_ui_definition,
    ensure_trait_signal_ui_definition,
    load_trait_signal_ui_definition,
    normalize_trait_signal_selection_state,
    resolve_trait_selection_value,
    trait_requires_signal_selection,
    write_canonical_selected_signal_ids,
)


def test_runtime_contract_declares_explicit_trait_signal_ui_sections() -> None:
    runtime_contract = yaml.safe_load(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml").read_text(encoding="utf-8")
    )

    core_config = runtime_contract["ui"]["core_signals"]
    extended_config = runtime_contract["ui"]["extended_signals"]

    assert core_config["section_label"] == "Core Signals (Most Important)"
    assert extended_config["section_label"] == "Additional Observations"
    assert extended_config["collapsible"] is True
    assert extended_config["default_collapsed"] is True
    assert extended_config["grouped"] is True


def test_load_trait_signal_ui_definition_returns_non_empty_signal_ids_for_known_trait():
    definition = load_trait_signal_ui_definition("T10_Behavior")

    assert definition["trait_id"] == "trait_10"
    assert definition["core_section_label"] == "Core Signals (Most Important)"
    assert definition["extended_section_label"] == "Additional Observations"
    assert definition["extended_collapsible"] is True
    assert definition["extended_default_collapsed"] is True
    assert definition["valid_signal_ids"]
    assert "Q10_BEHAVIOR_SKILL_BUILDING" in definition["valid_signal_ids"]
    assert definition["core_signals"]
    assert all("signal_id" in signal for signal in definition["core_signals"])
    assert any(group["group_label"] == "positive" for group in definition["extended_groups"])


def test_normalize_trait_signal_selection_state_filters_compatibility_variants_to_valid_ids():
    selected_signal_ids = normalize_trait_signal_selection_state(
        {
            "selected_signals": {"S_CHILD_CENTERED": True, "INVALID": True},
            "signal_selections": {"core": ["S_COREGULATION"]},
        },
        ["S_CHILD_CENTERED", "S_COREGULATION"],
    )

    assert selected_signal_ids == ["S_CHILD_CENTERED"]


def test_write_canonical_selected_signal_ids_replaces_legacy_selection_fields():
    state = {
        "selected_signals": {"legacy": True},
        "signal_selections": {"core": ["legacy"]},
    }

    write_canonical_selected_signal_ids(state, ["S_CHILD_CENTERED", "S_CHILD_CENTERED", " "])

    assert state["selected_signal_ids"] == ["S_CHILD_CENTERED"]
    assert "selected_signals" not in state
    assert "signal_selections" not in state


def test_count_selected_trait_checkbox_entries_counts_nested_legacy_payloads():
    count = count_selected_trait_checkbox_entries(
        {
            "signal_selections": {
                "core": ["S_CHILD_CENTERED", {"selected": False}],
                "extended": [{"selected": True}],
            }
        },
        "T10_Behavior",
    )

    assert count == 2


def test_resolve_trait_selection_value_prefers_first_supported_field():
    selection_value = resolve_trait_selection_value(
        {
            "selected_signal_ids": ["S_CHILD_CENTERED"],
            "selected_signals": {"legacy": True},
        }
    )

    assert selection_value == ["S_CHILD_CENTERED"]


def test_trait_requires_signal_selection_matches_finalize_rule() -> None:
    assert trait_requires_signal_selection(
        {"selected_signal_ids": []},
        {"skipped": False, "absolute_disqualifier": False},
        "T10_Behavior",
    ) is True
    assert trait_requires_signal_selection(
        {"selected_signal_ids": []},
        {"skipped": True, "absolute_disqualifier": False},
        "T10_Behavior",
    ) is False
    assert trait_requires_signal_selection(
        {"selected_signal_ids": []},
        {"skipped": False, "absolute_disqualifier": True},
        "T10_Behavior",
    ) is False


def test_load_trait_signal_ui_definition_supports_rubric_trait_id_aliases():
    definition = load_trait_signal_ui_definition("trait_10")

    assert definition["trait_id"] == "trait_10"
    assert "Q10_BEHAVIOR_SKILL_BUILDING" in definition["valid_signal_ids"]


def test_default_signal_ui_definition_matches_explicit_runtime_contract_sections() -> None:
    definition = default_signal_ui_definition("unknown_trait")

    assert definition["trait_id"] == "unknown_trait"
    assert definition["core_section_label"] == "Core Signals (Most Important)"
    assert definition["extended_section_label"] == "Additional Observations"
    assert definition["extended_collapsible"] is True
    assert definition["extended_default_collapsed"] is True


def test_load_trait_signal_ui_definition_uses_repo_root_paths_from_src_cwd(src_cwd):
    definition = load_trait_signal_ui_definition("trait_10")

    assert src_cwd.name == "src"
    assert definition["trait_id"] == "trait_10"
    assert definition["valid_signal_ids"]
    assert "Q10_BEHAVIOR_SKILL_BUILDING" in definition["valid_signal_ids"]


def test_load_trait_signal_ui_definition_supports_trait_1_runtime_alias_resolution():
    definition = load_trait_signal_ui_definition("trait_1")

    assert definition["trait_id"] == "trait_1"
    assert definition["valid_signal_ids"]
    assert "Q1_BEHAVIOR_AS_COMMUNICATION" in definition["valid_signal_ids"]


def test_ensure_trait_signal_ui_definition_rejects_missing_runtime_trait_definition() -> None:
    from scoring_reporting import ReportingValidationError

    try:
        ensure_trait_signal_ui_definition("trait_999")
    except ReportingValidationError as exc:
        assert "trait_999" in str(exc)
    else:
        raise AssertionError("Expected ReportingValidationError for missing runtime trait definition")
