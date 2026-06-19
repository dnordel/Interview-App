from scoring_reporting import (
    build_signal_dictionary_index,
    iter_trait_schema_signals,
    normalize_core_signals,
    normalize_extended_signal_groups,
    normalize_trait_signal,
    resolve_trait_signal_label,
    resolve_trait_signal_selection_id,
    resolve_trait_signal_weight,
)


def test_build_signal_dictionary_index_keeps_dictionary_entries_by_signal_id() -> None:
    index = build_signal_dictionary_index({"signals": [{"id": "S_VALIDATE", "category": "Language Used"}]})

    assert index == {"S_VALIDATE": {"id": "S_VALIDATE", "category": "Language Used"}}


def test_normalize_trait_signal_prefers_runtime_selection_id_fields() -> None:
    normalized = normalize_trait_signal(
        {"id": "Q10_VALIDATES_CHILD_FEELINGS", "label": "Validates", "base_weight": 1},
        default_group_label="Language Used",
    )

    assert normalized["signal_id"] == "Q10_VALIDATES_CHILD_FEELINGS"
    assert normalized["group_label"] == "Language Used"
    assert normalized["weight"] == 1.0


def test_resolve_trait_signal_selection_helpers_support_both_signal_schemas() -> None:
    assert resolve_trait_signal_selection_id({"ref": "S_VALIDATE"}) == "S_VALIDATE"
    assert resolve_trait_signal_selection_id({"id": "Q10_VALIDATES_CHILD_FEELINGS"}) == "Q10_VALIDATES_CHILD_FEELINGS"
    assert resolve_trait_signal_label({"label": "Validate"}, fallback="fallback") == "Validate"
    assert resolve_trait_signal_weight({"weight": 2}) == 2.0
    assert resolve_trait_signal_weight({"base_weight": -3}) == -3.0


def test_normalize_core_signals_skips_blank_entries() -> None:
    normalized = normalize_core_signals([{"ref": "S_VALIDATE", "weight": 1}, {"id": " ", "base_weight": 2}])

    assert normalized == [
        {
            "signal_id": "S_VALIDATE",
            "label": "S_VALIDATE",
            "group_label": "Core",
            "weight": 1.0,
            "is_critical": False,
        }
    ]


def test_normalize_extended_signal_groups_supports_explicit_groups() -> None:
    groups = normalize_extended_signal_groups(
        {
            "extended_signal_groups": [
                {"group_id": "group_1", "group_label": "Observations", "signals": [{"ref": "S_EXT", "weight": 1}]}
            ]
        }
    )

    assert groups[0]["group_id"] == "group_1"
    assert groups[0]["group_label"] == "Observations"
    assert groups[0]["signals"][0]["signal_id"] == "S_EXT"


def test_normalize_extended_signal_groups_supports_runtime_extended_signals() -> None:
    groups = normalize_extended_signal_groups(
        {
            "extended_signals": [
                {"id": "Q10_VALIDATES_CHILD_FEELINGS", "maps_to": ["S_VALIDATE"], "base_weight": 1},
                {"id": "Q10_USES_RESPECTFUL_LANGUAGE", "maps_to": ["S_RESPECTFUL_LANGUAGE"], "base_weight": 1},
            ]
        },
        signal_dictionary_index=build_signal_dictionary_index(
            {
                "signals": [
                    {"id": "S_VALIDATE", "category": "Language Used"},
                    {"id": "S_RESPECTFUL_LANGUAGE", "category": "Language Used"},
                ]
            }
        ),
    )

    assert groups == [
        {
            "group_id": "language_used",
            "group_label": "Language Used",
            "signals": [
                {
                    "signal_id": "Q10_VALIDATES_CHILD_FEELINGS",
                    "label": "Q10_VALIDATES_CHILD_FEELINGS",
                    "group_label": "Language Used",
                    "weight": 1.0,
                    "is_critical": False,
                },
                {
                    "signal_id": "Q10_USES_RESPECTFUL_LANGUAGE",
                    "label": "Q10_USES_RESPECTFUL_LANGUAGE",
                    "group_label": "Language Used",
                    "weight": 1.0,
                    "is_critical": False,
                },
            ],
        }
    ]


def test_iter_trait_schema_signals_flattens_both_extended_schema_variants() -> None:
    signals = iter_trait_schema_signals(
        {
            "core_signals": [{"ref": "S_CORE"}],
            "extended_signal_groups": [{"signals": [{"ref": "S_GROUP"}]}],
            "extended_signals": [{"id": "Q_EXT"}],
        }
    )

    assert signals == [{"ref": "S_CORE"}, {"ref": "S_GROUP"}, {"id": "Q_EXT"}]
