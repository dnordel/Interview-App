from io import StringIO
from pathlib import Path
import importlib.util
import json

import pytest


def _load_engine_class():
    module_path = Path("Trait-Based Scoring/trait_based_scoring_engine.py")
    spec = importlib.util.spec_from_file_location("trait_based_scoring_engine", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.ScoringEngine


@pytest.fixture()
def scoring_engine_class():
    return _load_engine_class()


@pytest.fixture()
def runtime_bundle(scoring_engine_class):
    return scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )


def _build_selected_refs(trait):
    refs = [signal["ref"] for signal in trait.get("core_signals", [])]
    for group in trait.get("extended_signal_groups", []):
        refs.extend(signal["ref"] for signal in group.get("signals", []))
    refs.extend(signal["ref"] for signal in trait.get("extended_signals", []))
    return refs


def _sample_config():
    return {
        "paths": {
            "traits_dir": ".",
            "signal_dictionary": "./shared_signal_dictionary.json",
            "output_dir": "./results",
        },
        "data_model": {"signal_resolution": {"allow_custom_signals": True}},
        "scoring": {
            "signal_score_to_raw_score": [
                {"min": 7, "max": None, "raw_score": 5},
                {"min": 4, "max": 6, "raw_score": 4},
                {"min": 1, "max": 3, "raw_score": 3},
                {"min": -3, "max": 0, "raw_score": 2},
                {"min": None, "max": -4, "raw_score": 1},
            ],
        },
        "decision_engine": {
            "thresholds": {"hire": 10, "borderline": 0},
            "override_rules": {"auto_reject_if_auto_no_hire_signal": True},
        },
    }


def test_static_path_helpers_cover_contract_relative_resolution(scoring_engine_class, tmp_path):
    contract_path = tmp_path / "nested" / "contract.yaml"
    contract_path.parent.mkdir()
    contract_path.write_text("paths: {}\n", encoding="utf-8")

    assert scoring_engine_class._path(["a", "b"]) == "a.b"
    assert scoring_engine_class._contract_base_dir(contract_path) == contract_path.resolve().parent
    assert scoring_engine_class.resolve_configured_path(contract_path, "signals.json") == contract_path.resolve().parent / "signals.json"


def test_require_helpers_validate_nested_types(scoring_engine_class, tmp_path):
    payload = {"a": {"b": {"flag": True, "count": 3}}}
    base = tmp_path / "engine"
    base.mkdir()
    (base / "shared_signal_dictionary.json").write_text(json.dumps({"signals": []}), encoding="utf-8")
    (base / "results").mkdir()
    config = _sample_config()
    config["_contract_path"] = str(base / "contract.yaml")
    engine = scoring_engine_class(config, {"signals": []})

    assert scoring_engine_class._require_dict_static(payload, ["a", "b"]) == {"flag": True, "count": 3}
    assert scoring_engine_class._require_value(payload["a"], "b", ["a", "b"]) == {"flag": True, "count": 3}
    assert engine._require_dict(payload, ["a", "b"]) == {"flag": True, "count": 3}
    assert engine._require_bool(payload, ["a", "b", "flag"]) is True
    assert engine._require_number(payload, ["a", "b", "count"]) == 3


def test_module_import_and_critical_override_path(scoring_engine_class):
    engine = scoring_engine_class(_sample_config(), {
        "signals": [
            {"id": "S1", "label": "Critical", "default_weight": 15, "is_critical": True},
            {"id": "S2", "label": "Support", "default_weight": 5, "is_critical": False},
        ]
    })

    summary = engine.build_decision_summary(final_score=25, critical_flag=True)

    assert summary == {
        "decision": "no_hire",
        "triggered_critical": True,
        "locked_rule": "Contract override: selected automatic no-hire signal triggers immediate no_hire",
        "override_rationale": "Contract override: selected automatic no-hire signal triggers immediate no_hire",
    }


def test_yaml_config_scores_and_decides_without_key_errors(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {
        "signals": [
            {
                "id": "S1",
                "label": "Signal 1",
                "default_weight": 3,
                "is_critical": True,
            },
            {
                "id": "S2",
                "label": "Signal 2",
                "default_weight": 2,
                "is_critical": False,
            },
        ]
    }
    traits = [
        {
            "trait_id": "T1",
            "core_signals": [{"ref": "S1"}],
            "extended_signal_groups": [{"signals": [{"ref": "S2"}]}],
        }
    ]

    engine = scoring_engine_class(config, signal_dictionary)
    result = engine.score_session(traits, {"T1": ["S1", "S2"]})

    assert result["totals"]["core"] == 3
    assert result["totals"]["extended"] == 2
    assert result["totals"]["raw_signal_total"] == pytest.approx(5)
    assert result["totals"]["weighted_trait_score_1_to_5"] == pytest.approx(4)
    assert result["totals"]["weighted_trait_percent"] == pytest.approx(80)
    assert result["decision"] == "hire"
    assert result["traits"][0]["suggested_raw_score"] == 4
    assert result["traits"][0]["trait_score_1_to_5"] == 4
    assert result["traits"][0]["final_score"] == 4


def test_session_score_uses_weighted_average_not_raw_signal_sum(scoring_engine_class):
    config = _sample_config()
    config["decision_engine"]["thresholds"] = {"hire_percent_min": 80, "borderline_percent_min": 65}
    signal_dictionary = {
        "signals": [
            {"id": "STRONG", "label": "Strong", "default_weight": 8},
            {"id": "LOW", "label": "Low", "default_weight": -4},
            {"id": "EXTRA", "label": "Extra", "default_weight": 2},
        ]
    }
    traits = [
        {
            "trait_id": "T1",
            "trait_multiplier": 3,
            "applicable_tracks": ["preschool"],
            "core_signals": [{"ref": "STRONG"}],
            "extended_signal_groups": [],
        },
        {
            "trait_id": "T2",
            "trait_multiplier": 1,
            "applicable_tracks": ["preschool"],
            "core_signals": [{"ref": "LOW"}, {"ref": "EXTRA"}],
            "extended_signal_groups": [],
        },
        {
            "trait_id": "BSS1",
            "trait_multiplier": 10,
            "applicable_tracks": ["behavior_support"],
            "core_signals": [{"ref": "LOW"}],
            "extended_signal_groups": [],
        },
    ]

    engine = scoring_engine_class(config, signal_dictionary)
    result = engine.score_session(
        traits,
        {"T1": ["STRONG"], "T2": ["LOW", "EXTRA"], "BSS1": ["LOW"]},
        track="preschool",
    )

    assert [trait["trait_id"] for trait in result["traits"]] == ["T1", "T2"]
    assert result["totals"]["raw_signal_total"] == pytest.approx(6)
    assert result["totals"]["weighted_trait_score_1_to_5"] == pytest.approx(4.25)
    assert result["totals"]["weighted_trait_percent"] == pytest.approx(85.0)
    assert result["totals"]["trait_weight_sum"] == pytest.approx(4)
    assert result["decision"] == "hire"


def test_score_session_excludes_skipped_traits_from_advisory_average(scoring_engine_class):
    config = _sample_config()
    config["decision_engine"]["thresholds"] = {"hire_percent_min": 80, "borderline_percent_min": 65}
    signal_dictionary = {
        "signals": [
            {"id": "STRONG", "label": "Strong", "default_weight": 8},
            {"id": "LOW", "label": "Low", "default_weight": -4},
        ]
    }
    traits = [
        {
            "trait_id": "T1",
            "trait_multiplier": 2,
            "core_signals": [{"ref": "STRONG"}],
            "extended_signal_groups": [],
        },
        {
            "trait_id": "T2",
            "trait_multiplier": 10,
            "core_signals": [{"ref": "LOW"}],
            "extended_signal_groups": [],
        },
    ]

    engine = scoring_engine_class(config, signal_dictionary)
    result = engine.score_session(
        traits,
        {
            "T1": ["STRONG"],
            "T2": {"skipped": True, "selected_signal_ids": ["LOW"]},
        },
    )

    assert [trait["trait_id"] for trait in result["traits"]] == ["T1"]
    assert result["totals"]["skipped_traits_count"] == 1
    assert result["totals"]["trait_weight_sum"] == pytest.approx(2)
    assert result["totals"]["raw_signal_total"] == pytest.approx(8)
    assert result["totals"]["weighted_trait_percent"] == pytest.approx(100)
    assert result["decision"] == "hire"


def test_make_decision_uses_decision_engine_only(scoring_engine_class):
    config, signal_dictionary, _traits, _resolved_paths = scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )
    config["decision_engine"]["thresholds"] = {"hire": 20, "borderline": 5}

    engine = scoring_engine_class(config, signal_dictionary)

    assert engine.make_decision(final_score=25, critical_flag=False) == "hire"
    assert engine.make_decision(final_score=1, critical_flag=False) == "no_hire"


def test_startup_schema_raises_clear_path_for_missing_sections(scoring_engine_class):
    config, signal_dictionary, _traits, _resolved_paths = scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )
    del config["decision_engine"]["thresholds"]

    with pytest.raises(KeyError, match="decision_engine.thresholds"):
        scoring_engine_class(config, signal_dictionary)


def test_startup_schema_raises_clear_path_for_invalid_types(scoring_engine_class):
    config, signal_dictionary, _traits, _resolved_paths = scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )
    del config["scoring"]

    with pytest.raises(KeyError, match="scoring"):
        scoring_engine_class(config, signal_dictionary)


def test_runtime_bundle_resolves_paths_relative_to_contract_file(runtime_bundle):
    config, signal_dictionary, traits, resolved_paths = runtime_bundle

    assert config["paths"]["traits_dir"] == "."
    assert resolved_paths["traits_dir"].name == "Trait-Based Scoring"
    assert resolved_paths["weighted_signals"].name == "preschool_teacher_interview_signals_weighted.json"
    assert resolved_paths["output_dir"].name == "results"
    assert signal_dictionary["signals"]
    assert traits


def test_validate_configured_paths_requires_existing_readable_targets(scoring_engine_class, tmp_path):
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text("paths: {}\n", encoding="utf-8")
    config = {
        "paths": {
            "traits_dir": "missing-traits",
            "signal_dictionary": "missing-signals.json",
            "output_dir": "missing-output",
        }
    }

    with pytest.raises(FileNotFoundError, match="paths.traits_dir"):
        scoring_engine_class.validate_configured_paths(config, contract_path)


def test_assert_path_accessible_accepts_regular_files_and_rejects_missing_paths(scoring_engine_class, tmp_path):
    file_path = tmp_path / "signals.json"
    file_path.write_text("{}", encoding="utf-8")

    scoring_engine_class._assert_path_accessible(file_path, "signal_dictionary")

    with pytest.raises(FileNotFoundError):
        scoring_engine_class._assert_path_accessible(tmp_path / "missing.json", "signal_dictionary")


def test_load_runtime_bundle_missing_contract_file_raises_file_not_found(scoring_engine_class, tmp_path):
    with pytest.raises(FileNotFoundError):
        scoring_engine_class.load_runtime_bundle(tmp_path / "missing.yaml")


def test_load_traits_from_dir_loads_json_files(scoring_engine_class, tmp_path):
    (tmp_path / "T1.json").write_text(json.dumps({"trait_id": "T1"}), encoding="utf-8")
    (tmp_path / "notes.json").write_text(json.dumps({"ignored": True}), encoding="utf-8")

    traits = scoring_engine_class.load_traits_from_dir(tmp_path)

    assert traits == [{"trait_id": "T1"}]


def test_load_runtime_bundle_uses_weighted_signal_source(scoring_engine_class):
    config, signal_dictionary, traits, resolved_paths = scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )

    expected_trait_ids = {f"trait_{index}" for index in range(1, 16)}
    expected_trait_ids.update({f"bss_trait_{index}" for index in range(1, 14)})
    expected_trait_ids.update({f"ades_trait_{index}" for index in range(1, 18)})

    assert config["_weighted_signal_source"] == str(resolved_paths["weighted_signals"])
    assert {trait["trait_id"] for trait in traits} == expected_trait_ids
    assert "trait_11_json_version" not in {trait["trait_id"] for trait in traits}
    assert signal_dictionary["signals"]
    assert all("default_weight" in signal for signal in signal_dictionary["signals"])


def test_loads_all_configured_traits_and_resolves_all_signal_refs(runtime_bundle, scoring_engine_class):
    config, signal_dictionary, traits, _resolved_paths = runtime_bundle
    engine = scoring_engine_class(config, signal_dictionary)
    dictionary_refs = {signal["id"] for signal in signal_dictionary["signals"]}

    expected_trait_ids = {f"trait_{index}" for index in range(1, 16)}
    expected_trait_ids.update({f"bss_trait_{index}" for index in range(1, 14)})
    expected_trait_ids.update({f"ades_trait_{index}" for index in range(1, 18)})
    assert len(traits) == len(expected_trait_ids)
    assert {trait["trait_id"] for trait in traits} == expected_trait_ids

    for trait in traits:
        selected_refs = _build_selected_refs(trait)
        assert selected_refs
        assert set(selected_refs).issubset(dictionary_refs)

        result = engine.score_trait(trait, selected_refs)

        assert result["trait_id"] == trait["trait_id"]
        assert len(result["selected_core"]) == len(trait.get("core_signals", []))

        expected_extended = sum(len(group.get("signals", [])) for group in trait.get("extended_signal_groups", []))
        expected_extended += len(trait.get("extended_signals", []))
        assert len(result["selected_extended"]) == expected_extended


def test_resolve_signal_handles_unknown_refs_based_on_configuration(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {"signals": [{"id": "S1", "label": "Signal 1", "default_weight": 2, "is_critical": False}]}
    engine = scoring_engine_class(config, signal_dictionary)

    assert engine.resolve_signal({"ref": "S1"})["label"] == "Signal 1"
    assert engine.resolve_signal({"ref": "UNKNOWN"}) is None

    config["data_model"]["signal_resolution"]["allow_custom_signals"] = False
    strict_engine = scoring_engine_class(config, signal_dictionary)
    with pytest.raises(ValueError, match="Unknown signal ref"):
        strict_engine.resolve_signal({"ref": "UNKNOWN"})


def test_high_score_with_auto_no_hire_selection_returns_no_hire_and_metadata(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {
        "signals": [
            {"id": "S1", "label": "Critical", "default_weight": 0, "is_critical": True, "is_auto_no_hire": True},
            {"id": "S2", "label": "Support", "default_weight": 5, "is_critical": False},
        ]
    }
    traits = [{"trait_id": "T1", "core_signals": [{"ref": "S1"}], "extended_signal_groups": [{"signals": [{"ref": "S2"}]}]}]
    engine = scoring_engine_class(config, signal_dictionary)

    result = engine.score_session(traits, {"T1": ["S1", "S2"]})

    assert result["decision"] == "no_hire"
    assert result["any_critical_selected"] is True
    assert result["triggered_critical"] is True
    assert result["locked_rule"] == "Contract override: selected automatic no-hire signal triggers immediate no_hire"


def test_no_critical_selection_uses_threshold_decision(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {
        "signals": [
            {"id": "S1", "label": "Critical", "default_weight": 15, "is_critical": True},
            {"id": "S2", "label": "Support", "default_weight": 12, "is_critical": False},
        ]
    }
    traits = [{"trait_id": "T1", "core_signals": [{"ref": "S2"}], "extended_signal_groups": []}]
    engine = scoring_engine_class(config, signal_dictionary)

    result = engine.score_session(traits, {"T1": ["S2"]})

    assert result["decision"] == "hire"
    assert result["any_critical_selected"] is False
    assert result["triggered_critical"] is False
    assert result["locked_rule"] is None


def test_build_decision_summary_matches_threshold_and_override_paths(scoring_engine_class):
    engine = scoring_engine_class(_sample_config(), {"signals": []})

    assert engine.build_decision_summary(12, False)["decision"] == "hire"
    summary = engine.build_decision_summary(12, True)
    assert summary["decision"] == "no_hire"
    assert summary["triggered_critical"] is True


def test_signal_score_to_raw_score_conversion_boundaries(scoring_engine_class):
    engine = scoring_engine_class(_sample_config(), {"signals": []})

    assert [engine.convert_signal_score_to_raw_score(value) for value in [7, 6, 4, 3, 1, 0, -3, -4]] == [
        5,
        4,
        4,
        3,
        3,
        2,
        2,
        1,
    ]


def test_positive_and_negative_signals_net_to_suggested_score(scoring_engine_class):
    engine = scoring_engine_class(
        _sample_config(),
        {"signals": [
            {"id": "P1", "label": "Positive", "default_weight": 3},
            {"id": "P2", "label": "Positive", "default_weight": 2},
            {"id": "N1", "label": "Concern", "default_weight": -2},
        ]},
    )

    result = engine.score_trait(
        {
            "trait_id": "trait_1",
            "core_signals": [{"ref": "P1"}, {"ref": "N1"}],
            "extended_signal_groups": [{"signals": [{"ref": "P2"}]}],
        },
        ["P1", "P2", "N1"],
    )

    assert result["net_signal_score"] == 3
    assert result["suggested_raw_score"] == 3


def test_debug_trait_prints_selected_signals(scoring_engine_class, capsys):
    config = _sample_config()
    signal_dictionary = {"signals": [{"id": "S1", "label": "Signal 1", "default_weight": 2, "is_critical": False}]}
    engine = scoring_engine_class(config, signal_dictionary)
    trait = {"trait_id": "T1", "core_signals": [{"ref": "S1"}], "extended_signal_groups": []}

    engine.debug_trait(trait, ["S1"])

    captured = capsys.readouterr()
    assert "DEBUG TRACE" in captured.out
    assert "Trait: T1" in captured.out
