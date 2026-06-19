import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scoring_reporting import ReportingValidationError, build_integration_payload
from trait_scoring_adapter import (
    DEFAULT_ENGINE_MODULE_CONTRACT,
    DEFAULT_ENGINE_RUNTIME_CONTRACT,
    _contract_resolution_base_dir,
    _build_compatibility_engine_output,
    _build_summary,
    _build_trait_engine,
    _build_trait_selections,
    _configured_max_weighted_total,
    _has_trait_definition_overlap,
    _is_invalid_raw_score_input,
    _iter_trait_signals,
    _load_runtime_bundle,
    _load_trait_engine_class,
    _load_yaml,
    _map_trait_row,
    _max_trait_final_score,
    _normalize_selected_signal_ids,
    _max_weighted_total,
    _negative_signal_refs,
    _normalize_bool,
    _percent_label,
    _percent_of_max,
    _positive_signal_refs,
    _resolve_percent_denominator,
    _resolve_percent_label,
    _resolve_scoring_denominator,
    _resolve_contract_path,
    _rubric_trait_map,
    _trait_ids_from_normalized_state,
    _select_signal_refs_for_state,
    _signal_refs_by_weight,
    build_trait_scoring_payload,
    coerce_raw_score,
    invoke_scoring_engine,
    load_module_contract_runtime_bundle,
    load_trait_definitions,
    map_engine_output_to_normalized_shape,
    normalize_absolute_disqualifier,
    normalize_app_trait_state,
    normalize_skipped,
    normalize_trait_state_item,
    normalize_verbatim_notes,
    validate_normalized_state,
    validate_runtime_bundle_metadata,
)
from test_scoring_engine_contract import build_rubric


TRAIT_DEFINITION = {
    "trait_id": "trait_a",
    "question": "Describe classroom management.",
    "core_signals": [
        {"ref": "P1", "weight": 3},
        {"ref": "N1", "weight": -2},
    ],
    "extended_signal_groups": [
        {"signals": [{"ref": "P2", "weight": 1}, {"ref": "N2", "weight": -1}]}
    ],
}

TRAIT_RUNTIME_DEFINITIONS = [
    TRAIT_DEFINITION,
    {
        "trait_id": "trait_b",
        "question": "Describe collaboration.",
        "core_signals": [{"ref": "B1", "weight": 2}],
        "extended_signal_groups": [],
    },
    {
        "trait_id": "trait_c",
        "question": "Describe reliability.",
        "core_signals": [{"ref": "C1", "weight": 2}],
        "extended_signal_groups": [],
    },
]


class _FakeTraitEngine:
    def __init__(self, _config, _signal_dictionary) -> None:
        pass

    def score_session(self, trait_definitions, selections):
        traits = []
        weighted_total = 0
        for index, trait_definition in enumerate(trait_definitions, start=1):
            trait_id = trait_definition["trait_id"]
            selected = selections.get(trait_id, [])
            final_score = len(selected)
            weighted_total += final_score
            traits.append(
                {
                    "trait_id": trait_id,
                    "final_score": final_score,
                    "selected_core": selected[:1],
                    "selected_extended": selected[1:],
                }
            )
        return {
            "traits": traits,
            "totals": {"final": weighted_total},
            "decision": "hire",
            "any_critical_selected": True,
            "triggered_critical": False,
            "locked_rule": None,
            "override_rationale": None,
        }


def _trait_runtime_bundle(traits=None):
    return {
        "config": {"decision_engine": {"thresholds": {}, "override_rules": {}}},
        "signal_dictionary": {"signals": []},
        "traits": traits or TRAIT_RUNTIME_DEFINITIONS,
    }


class TestTraitScoringAdapter(unittest.TestCase):
    def _patched_trait_runtime(self):
        return patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        )

    def test_build_trait_scoring_payload_returns_normalized_scoring_shape(self):
        runtime_patch, engine_patch = self._patched_trait_runtime()
        with runtime_patch, engine_patch:
            scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {
                        "raw_score": "4",
                        "selected_signal_ids": ["P1", "P2"],
                        "verbatim_notes": "Strong classroom example.",
                    },
                    "trait_b": {"raw_score": 4, "selected_signal_ids": ["P1"]},
                    "trait_c": {"raw_score": 4, "absolute_disqualifier": False, "selected_signal_ids": ["P2"]},
                },
            )

        self.assertEqual(scoring["schema_version"], "1.0.0")
        self.assertEqual(scoring["track_key"], "general")
        self.assertEqual(scoring["outcome"], "Hire")
        self.assertEqual(scoring["summary"]["percent_of_max"], scoring["percent_of_max"])
        self.assertEqual(len(scoring["traits"]), 3)
        self.assertIn("runtime_bundle_loaded", scoring["engine_metadata"])
        self.assertIn("trait_definitions", scoring["engine_metadata"])
        self.assertEqual(scoring["traits"][0]["notes"]["verbatim"], "Strong classroom example.")

    def test_load_module_contract_runtime_bundle_returns_runtime_error_metadata_for_missing_runtime(self):
        with TemporaryDirectory() as temp_dir:
            module_contract_path = Path(temp_dir, "engine.contract.yaml")
            module_contract_path.write_text("module:\n  name: temp_engine\n", encoding="utf-8")

            runtime_bundle = load_module_contract_runtime_bundle(
                engine_module_contract_path=module_contract_path,
                engine_runtime_contract_path=Path(temp_dir, "missing_runtime.yaml"),
            )

        self.assertEqual(runtime_bundle["module_contract"]["module"]["name"], "temp_engine")
        self.assertFalse(runtime_bundle["runtime_bundle_loaded"])
        self.assertIsNone(runtime_bundle["runtime_error"])
        self.assertEqual(runtime_bundle["trait_definitions"], [])

    def test_resolve_contract_path_uses_repo_root_instead_of_process_cwd(self):
        repo_root = _contract_resolution_base_dir()

        with TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                module_contract_path = _resolve_contract_path(DEFAULT_ENGINE_MODULE_CONTRACT)
                runtime_contract_path = _resolve_contract_path(DEFAULT_ENGINE_RUNTIME_CONTRACT)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(module_contract_path, repo_root / DEFAULT_ENGINE_MODULE_CONTRACT)
        self.assertEqual(runtime_contract_path, repo_root / DEFAULT_ENGINE_RUNTIME_CONTRACT)

    def test_load_module_contract_runtime_bundle_uses_stable_default_paths_from_src_cwd(self):
        repo_root = _contract_resolution_base_dir()
        src_dir = repo_root / "src"
        original_cwd = Path.cwd()

        os.chdir(src_dir)
        try:
            runtime_bundle = load_module_contract_runtime_bundle(
                engine_module_contract_path=DEFAULT_ENGINE_MODULE_CONTRACT,
                engine_runtime_contract_path=DEFAULT_ENGINE_RUNTIME_CONTRACT,
            )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(runtime_bundle["module_contract"]["module"]["name"], "trait_based_scoring_engine")
        self.assertEqual(runtime_bundle["runtime_contract_path"], str(repo_root / DEFAULT_ENGINE_RUNTIME_CONTRACT))
        self.assertTrue(runtime_bundle["trait_definitions"])

    def test_load_module_contract_runtime_bundle_reads_runtime_errors_from_invalid_bundle(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            module_contract_path = temp_path / "engine.contract.yaml"
            runtime_contract_path = temp_path / "runtime.yaml"
            module_contract_path.write_text("module:\n  name: temp_engine\n", encoding="utf-8")
            runtime_contract_path.write_text("paths: {}\n", encoding="utf-8")

            runtime_bundle = load_module_contract_runtime_bundle(
                engine_module_contract_path=module_contract_path,
                engine_runtime_contract_path=runtime_contract_path,
            )

        self.assertFalse(runtime_bundle["runtime_bundle_loaded"])
        self.assertTrue(runtime_bundle["runtime_error"])
        self.assertEqual(runtime_bundle["trait_definitions"], [])

    def test_load_trait_definitions_reads_trait_json_files_from_runtime_contract_dependency(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_contract_path = temp_path / "trait_based_scoring_contract.yaml"
            signal_dictionary_path = temp_path / "shared_signal_dictionary.json"
            trait_path = temp_path / "T1_CustomTrait.json"

            runtime_contract_path.write_text(
                """paths:
  traits_dir: .
  signal_dictionary: ./shared_signal_dictionary.json
  output_dir: .
""",
                encoding="utf-8",
            )
            signal_dictionary_path.write_text('{"signals": []}', encoding="utf-8")
            trait_path.write_text(
                '{"trait_id": "trait_a", "core_signals": [{"ref": "P1", "weight": 1}], "extended_signal_groups": []}',
                encoding="utf-8",
            )

            trait_definitions = load_trait_definitions({"runtime_contract_path": str(runtime_contract_path)})

        self.assertEqual([item["trait_id"] for item in trait_definitions], ["trait_a"])
        self.assertEqual(trait_definitions[0]["core_signals"][0]["ref"], "P1")
        self.assertEqual(trait_definitions[0]["extended_signal_groups"], [])

    def test_validate_runtime_bundle_metadata_rejects_runtime_error(self):
        with self.assertRaises(ReportingValidationError):
            validate_runtime_bundle_metadata({"runtime_error": "missing config"})

    def test_validate_runtime_bundle_metadata_rejects_unloaded_bundle_without_error_message(self):
        with self.assertRaises(ReportingValidationError):
            validate_runtime_bundle_metadata({"runtime_bundle_loaded": False, "runtime_contract_path": "runtime.yaml"})

    def test_normalize_skipped_and_disqualifier_flags_accept_string_booleans(self):
        self.assertTrue(normalize_skipped("yes"))
        self.assertFalse(normalize_skipped("off"))
        self.assertTrue(normalize_absolute_disqualifier("true"))
        self.assertFalse(normalize_absolute_disqualifier("0"))

    def test_coerce_raw_score_accepts_valid_scores_and_rejects_invalid_inputs(self):
        self.assertEqual(coerce_raw_score(4), 4)
        self.assertEqual(coerce_raw_score(" 5 "), 5)
        self.assertIsNone(coerce_raw_score(7))
        self.assertIsNone(coerce_raw_score(True))

    def test_normalize_verbatim_notes_trims_and_stringifies(self):
        self.assertEqual(normalize_verbatim_notes("  quoted note  "), "quoted note")
        self.assertEqual(normalize_verbatim_notes(None), "")

    def test_normalize_trait_state_item_coerces_integer_and_string_scores(self):
        self.assertEqual(normalize_trait_state_item({"raw_score": 4})["raw_score"], 4)
        self.assertEqual(normalize_trait_state_item({"raw_score": "4"})["raw_score"], 4)

    def test_normalize_trait_state_item_canonicalizes_selection_variants(self):
        canonical_state = normalize_trait_state_item(
            {
                "selected_signal_ids": ["P1", "P2", "P1"],
                "model_signal_suggestions": [{"signal_id": "P1", "confidence": 0.5, "rationale": "Matched."}],
            }
        )
        mapping_state = normalize_trait_state_item({"selected_signals": {"P1": True, "P2": False, "P3": True}})
        grouped_state = normalize_trait_state_item(
            {"signal_selections": {"core": ["P1"], "extended": [{"signal_id": "P2", "selected": True}]}}
        )

        self.assertEqual(canonical_state["selected_signal_ids"], ["P1", "P2"])
        self.assertEqual(
            canonical_state["model_signal_override"],
            {"accepted_signal_ids": ["P1"], "rejected_signal_ids": [], "manual_only_signal_ids": ["P2"]},
        )
        self.assertEqual(mapping_state["selected_signal_ids"], ["P1", "P3"])
        self.assertEqual(grouped_state["selected_signal_ids"], ["P1", "P2"])

    def test_skipped_trait_normalization_drops_scores_with_or_without_value(self):
        normalized_with_score = normalize_trait_state_item({"skipped": True, "raw_score": 5})
        normalized_without_score = normalize_trait_state_item({"skipped": True})

        self.assertIsNone(normalized_with_score["raw_score"])
        self.assertTrue(normalized_with_score["skipped"])
        self.assertIsNone(normalized_without_score["raw_score"])
        self.assertTrue(normalized_without_score["skipped"])

    def test_normalize_app_trait_state_handles_empty_and_missing_state_objects(self):
        self.assertEqual(normalize_app_trait_state(None), {})
        self.assertEqual(normalize_app_trait_state({"trait_a": None}), {})

    def test_validate_normalized_state_rejects_disqualifier_without_verbatim_notes(self):
        normalized_state = normalize_app_trait_state(
            {"trait_a": {"absolute_disqualifier": True, "verbatim_notes": "   "}}
        )

        with self.assertRaises(ReportingValidationError):
            validate_normalized_state(normalized_state)

    def test_invoke_scoring_engine_returns_engine_payload(self):
        normalized_state = normalize_app_trait_state(
            {
                "trait_a": {"raw_score": 4, "selected_signal_ids": ["P1", "P2"]},
                "trait_b": {"raw_score": 4, "selected_signal_ids": ["B1"]},
                "trait_c": {"raw_score": 4, "selected_signal_ids": ["C1"]},
            }
        )

        with patch("scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine):
            engine_output = invoke_scoring_engine(
                build_rubric(),
                "general",
                normalized_state,
                runtime_bundle=_trait_runtime_bundle(),
                engine_runtime_contract_path="Trait-Based Scoring/trait_based_scoring_contract.yaml",
            )

        self.assertEqual(engine_output["weighted_total"], 160)
        self.assertEqual(len(engine_output["rows"]), 3)

    def test_normalize_app_trait_state_maps_legacy_runtime_trait_ids_to_canonical_ids(self):
        normalized_state = normalize_app_trait_state({
            "T10_Behavior": {"raw_score": 4, "selected_signal_ids": ["P1"]},
            "trait_11": {"raw_score": 3},
        })

        self.assertEqual(sorted(_trait_ids_from_normalized_state(normalized_state)), ["trait_10", "trait_11"])
        self.assertIn("trait_10", normalized_state)

    def test_invoke_scoring_engine_rejects_missing_trait_overlap(self):
        normalized_state = normalize_app_trait_state({"unknown_trait": {"raw_score": 4}})

        with patch("scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine):
            with self.assertRaises(ReportingValidationError) as exc_info:
                invoke_scoring_engine(
                    build_rubric(),
                    "general",
                    normalized_state,
                    runtime_bundle=_trait_runtime_bundle([TRAIT_DEFINITION]),
                    engine_runtime_contract_path="Trait-Based Scoring/trait_based_scoring_contract.yaml",
                )

        self.assertIn("do not overlap the trait runtime bundle", str(exc_info.exception))

    def test_invoke_scoring_engine_rejects_rubric_runtime_config_mismatch(self):
        normalized_state = normalize_app_trait_state({"trait_a": {"raw_score": 4}})

        with patch("scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine):
            with self.assertRaises(ReportingValidationError) as exc_info:
                invoke_scoring_engine(
                    build_rubric(),
                    "general",
                    normalized_state,
                    runtime_bundle=_trait_runtime_bundle([TRAIT_DEFINITION]),
                    engine_runtime_contract_path="Trait-Based Scoring/trait_based_scoring_contract.yaml",
                )

        self.assertIn("includes traits missing from the runtime bundle", str(exc_info.exception))

    def test_map_engine_output_to_normalized_shape_returns_shared_payload(self):
        with patch("scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine):
            normalized_state = normalize_app_trait_state(
                {
                    "trait_a": {"raw_score": 4, "verbatim_notes": "quoted"},
                    "trait_b": {"raw_score": 4},
                    "trait_c": {"raw_score": 4},
                }
            )
            engine_output = invoke_scoring_engine(
                build_rubric(),
                "general",
                normalized_state,
                runtime_bundle=_trait_runtime_bundle(),
                engine_runtime_contract_path="Trait-Based Scoring/trait_based_scoring_contract.yaml",
            )
        runtime_bundle = {
            "module_contract": {"module": {"name": "trait_based_scoring_engine"}},
            "runtime_contract_path": "contracts/runtime.yaml",
            "runtime_bundle_loaded": True,
            "runtime_error": None,
            "trait_definitions": [{"trait_id": "trait_a"}],
        }

        scoring = map_engine_output_to_normalized_shape(
            rubric=build_rubric(),
            track_key="general",
            normalized_state=normalized_state,
            engine_output=engine_output,
            runtime_bundle=runtime_bundle,
        )

        self.assertEqual(scoring["summary"]["weighted_total"], scoring["weighted_total"])
        self.assertEqual(scoring["engine_metadata"]["trait_definitions"][0]["trait_id"], "trait_a")
        self.assertEqual(scoring["traits"][0]["notes"]["verbatim"], "quoted")

    def test_invoke_scoring_engine_uses_explicit_runtime_contract_path_for_engine_loading(self):
        normalized_state = normalize_app_trait_state({"trait_a": {"raw_score": 4, "selected_signal_ids": ["P1"]}})
        explicit_contract_path = Path("custom-runtime/trait_scoring_contract.yaml")

        with patch("scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine) as load_engine_class:
            engine_output = invoke_scoring_engine(
                build_rubric(),
                "general",
                normalized_state,
                runtime_bundle=_trait_runtime_bundle(),
                engine_runtime_contract_path=explicit_contract_path,
            )

        self.assertEqual(engine_output["weighted_total"], 124)
        self.assertEqual(load_engine_class.call_args.args[0], explicit_contract_path.expanduser().resolve())

    def test_build_trait_scoring_payload_honors_non_default_contract_paths_end_to_end(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            module_contract_path = temp_path / "engine.contract.yaml"
            runtime_contract_path = runtime_dir / "trait_based_scoring_contract.yaml"
            signal_dictionary_path = runtime_dir / "shared_signal_dictionary.json"
            trait_paths = [
                runtime_dir / "T1_CustomTraitA.json",
                runtime_dir / "T2_CustomTraitB.json",
                runtime_dir / "T3_CustomTraitC.json",
            ]
            engine_module_path = runtime_dir / "trait_based_scoring_engine.py"

            module_contract_path.write_text("module:\n  name: temp_engine\n", encoding="utf-8")
            runtime_contract_path.write_text(
                """paths:
  traits_dir: .
  signal_dictionary: ./shared_signal_dictionary.json
  output_dir: .
scoring:
  signal_score_to_raw_score:
    - min: 7
      max:
      raw_score: 5
    - min: 4
      max: 6
      raw_score: 4
    - min: 1
      max: 3
      raw_score: 3
    - min: -3
      max: 0
      raw_score: 2
    - min:
      max: -4
      raw_score: 1
data_model:
  signal_resolution:
    allow_custom_signals: true
decision_engine:
  thresholds:
    hire: 80
    borderline: 0
  override_rules:
    auto_reject_if_auto_no_hire_signal: true
""",
                encoding="utf-8",
            )
            signal_dictionary_path.write_text('{"signals": [{"id": "P1", "label": "Signal", "default_weight": 3}]}', encoding="utf-8")
            trait_payloads = [
                '{"trait_id": "trait_a", "question": "Q1", "core_signals": [{"ref": "P1", "weight": 3}], "extended_signal_groups": []}',
                '{"trait_id": "trait_b", "question": "Q2", "core_signals": [{"ref": "P1", "weight": 3}], "extended_signal_groups": []}',
                '{"trait_id": "trait_c", "question": "Q3", "core_signals": [{"ref": "P1", "weight": 3}], "extended_signal_groups": []}',
            ]
            for trait_path, trait_payload in zip(trait_paths, trait_payloads):
                trait_path.write_text(trait_payload, encoding="utf-8")
            engine_module_path.write_text(
                """
import json
from pathlib import Path

import yaml


class ScoringEngine:
    def __init__(self, config, signal_dictionary):
        self.config = config
        self.signal_dictionary = signal_dictionary

    @classmethod
    def load_runtime_bundle(cls, contract_path):
        contract_path = Path(contract_path).resolve()
        config = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        signal_dictionary_path = contract_path.parent / "shared_signal_dictionary.json"
        traits = []
        for trait_file in sorted(contract_path.parent.glob("T*.json")):
            traits.append(json.loads(trait_file.read_text(encoding="utf-8")))
        resolved_paths = {
            "traits_dir": contract_path.parent,
            "signal_dictionary": signal_dictionary_path,
            "output_dir": contract_path.parent,
        }
        signal_dictionary = json.loads(signal_dictionary_path.read_text(encoding="utf-8"))
        return config, signal_dictionary, traits, resolved_paths

    def score_session(self, trait_definitions, selections):
        return {
            "traits": [
                {
                    "trait_id": trait_definitions[0]["trait_id"],
                    "final_score": len(selections.get(trait_definitions[0]["trait_id"], [])),
                    "selected_core": selections.get(trait_definitions[0]["trait_id"], []),
                    "selected_extended": [],
                }
            ],
            "totals": {"final": len(selections.get(trait_definitions[0]["trait_id"], []))},
            "decision": "hire",
            "any_critical_selected": False,
            "triggered_critical": False,
            "locked_rule": None,
            "override_rationale": None,
        }
""".lstrip(),
                encoding="utf-8",
            )

            scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {"trait_a": {"raw_score": 4, "selected_signal_ids": ["P1"]}},
                engine_module_contract_path=module_contract_path,
                engine_runtime_contract_path=runtime_contract_path,
            )

        self.assertEqual(scoring["weighted_total"], 124)
        self.assertEqual(scoring["engine_metadata"]["module_contract"]["module"]["name"], "temp_engine")
        self.assertEqual(scoring["engine_metadata"]["runtime_contract_path"], str(runtime_contract_path.resolve()))
        self.assertEqual(scoring["engine_metadata"]["resolved_paths"]["signal_dictionary"], str(signal_dictionary_path.resolve()))
        self.assertEqual(scoring["engine_metadata"]["trait_definitions"][0]["trait_id"], "trait_a")

    def test_build_trait_scoring_payload_rejects_invalid_scores(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            with self.assertRaises(ReportingValidationError):
                build_trait_scoring_payload(build_rubric(), "general", {"trait_a": {"raw_score": 7}})

    def test_normalized_scoring_shape_feeds_integration_export(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": 4, "verbatim_notes": "quoted"},
                    "trait_b": {"raw_score": 4},
                    "trait_c": {"raw_score": 4},
                },
            )
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "school": "PS 10",
                "track": "lead",
                "qualification": {},
            },
            "custom_answers": [],
            "flow_transcript": [],
            "referral_packet": {},
            "communication_log": [],
        }

        export_payload = build_integration_payload(payload, scoring)

        self.assertEqual(export_payload["decision"], "hire")
        self.assertEqual(export_payload["interview_notes"]["traits"][0]["verbatim_notes"], "quoted")

    def test_normalized_state_scoring_matches_integer_and_string_inputs(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            integer_scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": 4},
                    "trait_b": {"raw_score": 4},
                    "trait_c": {"raw_score": 4},
                },
            )
            string_scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": "4"},
                    "trait_b": {"raw_score": "4"},
                    "trait_c": {"raw_score": "4"},
                },
            )

        self.assertEqual(integer_scoring["weighted_total"], string_scoring["weighted_total"])
        self.assertEqual(integer_scoring["outcome"], string_scoring["outcome"])

    def test_build_trait_scoring_payload_exposes_skip_aware_denominator_fields(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": 4},
                    "trait_b": {"raw_score": 1, "skipped": True, "selected_signal_ids": []},
                    "trait_c": {"raw_score": 4},
                },
            )

        self.assertEqual(scoring["configured_max_weighted_total"], 200)
        self.assertGreater(scoring["max_weighted_total_included_traits"], 0)
        self.assertEqual(scoring["max_weighted_total"], scoring["max_weighted_total_included_traits"])
        self.assertEqual(scoring["percent_denominator"], scoring["max_weighted_total_included_traits"])
        self.assertEqual(scoring["skipped_traits_count"], 1)
        self.assertEqual(scoring["scored_traits_count"], 2)
        self.assertTrue(scoring["percent_label"].endswith("%"))
        self.assertEqual(scoring["percent_label"], scoring["percent_of_max_label"])

    def test_build_trait_scoring_payload_labels_all_skipped_percent_as_na(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"skipped": True, "selected_signal_ids": []},
                    "trait_b": {"skipped": True, "selected_signal_ids": []},
                    "trait_c": {"skipped": True, "selected_signal_ids": []},
                },
            )

        self.assertEqual(scoring["configured_max_weighted_total"], 200)
        self.assertEqual(scoring["max_weighted_total_included_traits"], 0)
        self.assertEqual(scoring["max_weighted_total"], 200)
        self.assertEqual(scoring["percent_denominator"], 200)
        self.assertEqual(scoring["skipped_traits_count"], 3)
        self.assertEqual(scoring["scored_traits_count"], 0)
        self.assertEqual(scoring["percent_of_max"], 0.0)
        self.assertEqual(scoring["percent_label"], "N/A (all questions skipped)")
        self.assertEqual(scoring["percent_of_max_label"], "N/A (all questions skipped)")

    def test_private_helpers_cover_signal_ref_and_summary_paths(self):
        self.assertTrue(_has_trait_definition_overlap([{"trait_id": "trait_a"}], {"trait_a": {}}))
        self.assertEqual(_positive_signal_refs(TRAIT_DEFINITION), ["P1", "P2"])
        self.assertEqual(_negative_signal_refs(TRAIT_DEFINITION), ["N1", "N2"])
        self.assertEqual(_signal_refs_by_weight(TRAIT_DEFINITION, positive=True), ["P1", "P2"])
        self.assertEqual(len(_iter_trait_signals(TRAIT_DEFINITION)), 4)
        self.assertEqual(_select_signal_refs_for_state(TRAIT_DEFINITION, {"selected_signal_ids": ["P1", "P2"]}), ["P1", "P2"])
        self.assertEqual(_select_signal_refs_for_state(TRAIT_DEFINITION, {"selected_signal_ids": ["N1", "N2"]}), ["N1", "N2"])
        self.assertEqual(_select_signal_refs_for_state(TRAIT_DEFINITION, {"skipped": True, "selected_signal_ids": ["P1"]}), [])
        self.assertEqual(_build_trait_selections([TRAIT_DEFINITION], {"trait_a": {"selected_signal_ids": ["P1"]}}), {"trait_a": ["P1"]})

        summary = _build_summary(
            {
                "weighted_total": 10,
                "configured_max_weighted_total": 20,
                "max_weighted_total_included_traits": 15,
                "percent_of_max": 66.67,
                "outcome": "Hire",
            }
        )
        self.assertEqual(summary["percent_denominator"], 15)
        self.assertEqual(_resolve_scoring_denominator({}, 15, 20), 15)
        self.assertEqual(_resolve_percent_label({}, 0), "N/A (all questions skipped)")
        self.assertEqual(_resolve_percent_denominator(0, 20), 20)
        self.assertEqual(_percent_of_max(15, 20), 75.0)
        self.assertEqual(_percent_label(75.0, 20), "75.0%")

    def test_private_helpers_cover_row_and_runtime_paths(self):
        row = {
            "trait_id": "trait_a",
            "trait_name": "Trait A",
            "priority": "critical",
            "weight": 5,
            "primary_question": "Q1",
            "raw_score": 4,
            "raw_score_math": 4,
            "weighted_score": 7,
            "skipped": False,
            "absolute_disqualifier": True,
            "no_example_after_followups": False,
            "verbatim_notes": "quoted",
            "question_notes": "",
            "trait_notes": "",
            "signal_counts": {"core": 1, "extended": 2},
            "session_trait_outcome": "hire",
        }
        mapped = _map_trait_row(row)
        self.assertEqual(mapped["score"]["weighted"], 7)
        self.assertEqual(mapped["signal_counts"]["extended"], 2)
        self.assertTrue(_is_invalid_raw_score_input("not-a-score"))
        self.assertTrue(_normalize_bool("yes"))
        self.assertEqual(_load_yaml(Path("missing-file.yaml")), {})
        self.assertEqual(_load_runtime_bundle(Path("missing-runtime.yaml")), {})
        self.assertTrue(_load_trait_engine_class(Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")))

        runtime_bundle = {
            "config": {"decision_engine": {"thresholds": {}, "override_rules": {}}},
            "signal_dictionary": {"signals": []},
        }
        with patch("scoring_reporting._load_trait_engine_class", return_value=lambda cfg, sig: {"cfg": cfg, "sig": sig}):
            built = _build_trait_engine(runtime_bundle, Path("contract.yaml"))
        self.assertEqual(built["cfg"], runtime_bundle["config"])

    def test_private_helpers_cover_compatibility_output_paths(self):
        rubric = {
            "tracks": {"general": {"max_weighted_total": 30}},
            "traits": [
                {
                    "id": "trait_a",
                    "name": "Trait A",
                    "priority": "critical",
                    "weight": 3,
                    "primary_question": "Q1",
                    "applicable_tracks": ["general"],
                }
            ],
        }
        normalized_state = {
            "trait_a": {
                "raw_score": 4,
                "skipped": False,
                "verbatim_notes": "note",
                "selected_signal_ids": ["P1"],
                "model_signal_suggestions": [{"signal_id": "P2", "confidence": 0.8, "rationale": "Observed."}],
            }
        }
        session_result = {
            "traits": [{"trait_id": "trait_a", "final_score": 7, "selected_core": [1], "selected_extended": [1, 2]}],
            "totals": {"final": 7},
            "decision": "hire",
            "any_critical_selected": True,
            "triggered_critical": False,
            "locked_rule": None,
            "override_rationale": None,
        }
        compatibility = _build_compatibility_engine_output(
            rubric=rubric,
            track_key="general",
            normalized_state=normalized_state,
            trait_definitions=[TRAIT_DEFINITION],
            runtime_bundle=_trait_runtime_bundle(),
            session_result=session_result,
        )

        self.assertEqual(_rubric_trait_map(rubric, "general")["trait_a"]["name"], "Trait A")
        self.assertEqual(compatibility["weighted_total"], 12)
        self.assertEqual(compatibility["rows"][0]["raw_score"], 4)
        self.assertEqual(compatibility["rows"][0]["system_checkbox_score"], 12)
        self.assertEqual(compatibility["rows"][0]["deepseek_calculated_score"], 9)
        self.assertEqual(_max_trait_final_score(TRAIT_DEFINITION), 4.0)
        self.assertEqual(_max_weighted_total([TRAIT_DEFINITION], normalized_state), 4)
        self.assertEqual(_configured_max_weighted_total(rubric, "general", [TRAIT_DEFINITION]), 30)

    def test_legacy_signal_selections_do_not_override_human_raw_score(self):
        with patch("scoring_reporting._load_runtime_bundle", return_value=_trait_runtime_bundle()), patch(
            "scoring_reporting._load_trait_engine_class", return_value=_FakeTraitEngine
        ):
            positive_scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": 1, "selected_signal_ids": ["P1", "P2"]},
                    "trait_b": {"raw_score": 5, "selected_signal_ids": ["B1"]},
                    "trait_c": {"raw_score": 3, "selected_signal_ids": ["C1"]},
                },
            )
            reduced_scoring = build_trait_scoring_payload(
                build_rubric(),
                "general",
                {
                    "trait_a": {"raw_score": 5, "selected_signal_ids": ["P1"]},
                    "trait_b": {"raw_score": 5, "selected_signal_ids": ["B1"]},
                    "trait_c": {"raw_score": 3, "selected_signal_ids": ["C1"]},
                },
            )

        self.assertEqual(positive_scoring["weighted_total"], 60)
        self.assertEqual(reduced_scoring["weighted_total"], 184)
        self.assertLess(positive_scoring["weighted_total"], reduced_scoring["weighted_total"])

    def test_selection_normalizer_ignores_raw_score_when_legacy_signal_variants_exist(self):
        self.assertEqual(_normalize_selected_signal_ids({"raw_score": 5, "selected_signal_ids": ["P1"]}), ["P1"])
        self.assertEqual(_normalize_selected_signal_ids({"raw_score": 1, "selected_signals": {"N1": True}}), ["N1"])


    def test_select_signal_refs_for_state_supports_runtime_signal_schema(self):
        trait_definition = {
            "trait_id": "trait_10",
            "core_signals": [
                {"id": "Q10_FRAMES_FROM_CHILD_PERSPECTIVE", "base_weight": 2},
                {"id": "Q10_USES_NEGATIVE_LABELING", "base_weight": -2},
            ],
            "extended_signals": [
                {"id": "Q10_VALIDATES_CHILD_FEELINGS", "base_weight": 1},
            ],
        }

        selected = _select_signal_refs_for_state(
            trait_definition,
            {
                "selected_signal_ids": [
                    "Q10_FRAMES_FROM_CHILD_PERSPECTIVE",
                    "Q10_VALIDATES_CHILD_FEELINGS",
                    "INVALID",
                ]
            },
        )

        self.assertEqual(
            selected,
            ["Q10_FRAMES_FROM_CHILD_PERSPECTIVE", "Q10_VALIDATES_CHILD_FEELINGS"],
        )

    def test_select_signal_refs_for_state_supports_runtime_signal_alias_selections(self):
        trait_definition = {
            "trait_id": "trait_10",
            "core_signals": [
                {
                    "id": "Q10_FRAMES_FROM_CHILD_PERSPECTIVE",
                    "maps_to": ["S_CHILD_CENTERED"],
                    "base_weight": 2,
                },
                {
                    "id": "Q10_USES_NEGATIVE_LABELING",
                    "maps_to": ["S_NEGATIVE_LABELING"],
                    "base_weight": -2,
                },
            ],
            "extended_signals": [
                {
                    "id": "Q10_VALIDATES_CHILD_FEELINGS",
                    "maps_to": ["S_VALIDATE"],
                    "base_weight": 1,
                },
            ],
        }

        selected = _select_signal_refs_for_state(
            trait_definition,
            {
                "selected_signal_ids": [
                    "S_CHILD_CENTERED",
                    "S_VALIDATE",
                    "INVALID",
                ]
            },
        )

        self.assertEqual(
            selected,
            ["Q10_FRAMES_FROM_CHILD_PERSPECTIVE", "Q10_VALIDATES_CHILD_FEELINGS"],
        )

    def test_max_trait_final_score_supports_runtime_signal_schema(self):
        trait_definition = {
            "trait_id": "trait_10",
            "core_signals": [
                {"id": "Q10_FRAMES_FROM_CHILD_PERSPECTIVE", "base_weight": 2},
                {"id": "Q10_USES_NEGATIVE_LABELING", "base_weight": -2},
            ],
            "extended_signals": [
                {"id": "Q10_VALIDATES_CHILD_FEELINGS", "base_weight": 1},
                {"id": "Q10_USES_RESPECTFUL_LANGUAGE", "base_weight": 1},
            ],
        }

        self.assertEqual(_max_trait_final_score(trait_definition), 4.0)


if __name__ == "__main__":
    unittest.main()


