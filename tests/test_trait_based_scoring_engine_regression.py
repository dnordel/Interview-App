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
    return refs


def _sample_config():
    return {
        "paths": {
            "traits_dir": ".",
            "signal_dictionary": "./shared_signal_dictionary.json",
            "output_dir": "./results",
        },
        "data_model": {"signal_resolution": {"allow_custom_signals": True}},
        "scoring": {"core_multiplier": 1.5},
        "decision_engine": {
            "thresholds": {"strong_hire": 20, "hire": 10, "borderline": 0},
            "override_rules": {"auto_reject_if_critical": True},
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
        "locked_rule": "Contract override: selected critical signal triggers immediate no_hire",
        "override_rationale": "Contract override: selected critical signal triggers immediate no_hire",
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
    assert result["totals"]["final"] == pytest.approx(6.5)
    assert result["decision"] == "no_hire"


def test_make_decision_uses_decision_engine_only(scoring_engine_class):
    config, signal_dictionary, _traits, _resolved_paths = scoring_engine_class.load_runtime_bundle(
        Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
    )
    config["decision"] = {
        "thresholds": {"strong_hire": 999, "hire": 999, "borderline": 999},
        "modifiers": {"critical_flag": {"override": "no_hire"}},
    }

    engine = scoring_engine_class(config, signal_dictionary)

    assert engine.make_decision(final_score=25, critical_flag=False) == "strong_hire"
    assert engine.make_decision(final_score=1, critical_flag=False) == "borderline"


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
    config["scoring"]["core_multiplier"] = "1.5"

    with pytest.raises(TypeError, match="scoring.core_multiplier"):
        scoring_engine_class(config, signal_dictionary)


def test_runtime_bundle_resolves_paths_relative_to_contract_file(runtime_bundle):
    config, signal_dictionary, traits, resolved_paths = runtime_bundle

    assert config["paths"]["traits_dir"] == "."
    assert resolved_paths["traits_dir"].name == "Trait-Based Scoring"
    assert resolved_paths["signal_dictionary"].name == "shared_signal_dictionary.json"
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



def test_runtime_bundle_rejects_paths_outside_runtime_bundle(scoring_engine_class, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    contract_dir = tmp_path / "bundle"
    contract_dir.mkdir()
    config = {
        "paths": {
            "traits_dir": "../outside",
            "signal_dictionary": "signals.json",
            "output_dir": "results",
        }
    }
    (contract_dir / "signals.json").write_text(json.dumps({"signals": []}), encoding="utf-8")
    (contract_dir / "results").mkdir()

    with pytest.raises(ValueError, match="escapes runtime bundle"):
        scoring_engine_class.validate_configured_paths(config, contract_dir / "contract.yaml")


def test_startup_schema_fails_closed_for_malformed_signal_dictionary(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {"signals": [{"id": "S1", "default_weight": "bad"}]}

    with pytest.raises(TypeError, match="signal_dictionary.signals.0.default_weight"):
        scoring_engine_class(config, signal_dictionary)

def test_load_runtime_bundle_missing_contract_file_raises_file_not_found(scoring_engine_class, tmp_path):
    with pytest.raises(FileNotFoundError):
        scoring_engine_class.load_runtime_bundle(tmp_path / "missing.yaml")


def test_load_traits_from_dir_loads_normalized_trait_files(scoring_engine_class, tmp_path):
    trait_payload = {
        "trait_id": "T1",
        "question": "Question",
        "core_signals": [{"id": "Q1", "maps_to": ["S1"], "base_weight": 2}],
    }
    (tmp_path / "T1.json").write_text(json.dumps(trait_payload), encoding="utf-8")
    (tmp_path / "notes.json").write_text(json.dumps({"ignored": True}), encoding="utf-8")

    traits = scoring_engine_class.load_traits_from_dir(tmp_path)

    assert traits[0]["trait_id"] == "trait_1"
    assert traits[0]["trait_aliases"] == ["trait_1", "T1"]
    assert traits[0]["core_signals"] == [{
        "id": "Q1",
        "maps_to": ["S1"],
        "base_weight": 2,
        "ref": "S1",
        "weight": 2,
        "label": "S1",
    }]


def test_load_traits_from_dir_fails_closed_for_malformed_trait(scoring_engine_class, tmp_path):
    (tmp_path / "T1.json").write_text(json.dumps({"trait_id": "T1"}), encoding="utf-8")

    with pytest.raises(KeyError, match="question"):
        scoring_engine_class.load_traits_from_dir(tmp_path)


def test_loads_all_configured_traits_and_resolves_all_signal_refs(runtime_bundle, scoring_engine_class):
    config, signal_dictionary, traits, _resolved_paths = runtime_bundle
    engine = scoring_engine_class(config, signal_dictionary)
    dictionary_refs = {signal["id"] for signal in signal_dictionary["signals"]}

    expected_trait_ids = {f"trait_{index}" for index in range(1, 12)}
    assert len(traits) == len(expected_trait_ids)
    assert {trait["trait_id"] for trait in traits} == expected_trait_ids

    for trait in traits:
        selected_refs = _build_selected_refs(trait)
        assert selected_refs
        assert set(selected_refs).issubset(dictionary_refs)

        result = engine.score_trait(trait, selected_refs)

        assert result["trait_id"] == trait["trait_id"]
        assert len(result["selected_core"]) == len(trait.get("core_signals", []))

        expected_extended = sum(
            len(group.get("signals", [])) for group in trait.get("extended_signal_groups", [])
        )
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


def test_high_score_with_critical_selection_returns_no_hire_and_metadata(scoring_engine_class):
    config = _sample_config()
    signal_dictionary = {
        "signals": [
            {"id": "S1", "label": "Critical", "default_weight": 15, "is_critical": True},
            {"id": "S2", "label": "Support", "default_weight": 5, "is_critical": False},
        ]
    }
    traits = [{"trait_id": "T1", "core_signals": [{"ref": "S1"}], "extended_signal_groups": [{"signals": [{"ref": "S2"}]}]}]
    engine = scoring_engine_class(config, signal_dictionary)

    result = engine.score_session(traits, {"T1": ["S1", "S2"]})

    assert result["decision"] == "no_hire"
    assert result["any_critical_selected"] is True
    assert result["triggered_critical"] is True
    assert result["locked_rule"] == "Contract override: selected critical signal triggers immediate no_hire"


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


def test_debug_trait_prints_selected_signals(scoring_engine_class, capsys):
    config = _sample_config()
    signal_dictionary = {"signals": [{"id": "S1", "label": "Signal 1", "default_weight": 2, "is_critical": False}]}
    engine = scoring_engine_class(config, signal_dictionary)
    trait = {"trait_id": "T1", "core_signals": [{"ref": "S1"}], "extended_signal_groups": []}

    engine.debug_trait(trait, ["S1"])

    captured = capsys.readouterr()
    assert "DEBUG TRACE" in captured.out
    assert "Trait: T1" in captured.out
