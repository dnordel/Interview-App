from __future__ import annotations

import json
from pathlib import Path

from scoring_reporting import (
    canonical_trait_id,
    load_trait_definitions_from_contract,
    load_trait_definitions_from_dir,
    load_trait_definitions_from_runtime_bundle,
    trait_id_aliases,
)


def test_load_trait_definitions_from_contract_reads_runtime_traits_dir(tmp_path: Path) -> None:
    runtime_contract_path = tmp_path / "trait_based_scoring_contract.yaml"
    signal_dictionary_path = tmp_path / "shared_signal_dictionary.json"
    trait_path = tmp_path / "T1_CustomTrait.json"

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
        json.dumps(
            {
                "trait_id": "trait_a",
                "core_signals": [{"ref": "P1", "weight": 1}],
                "extended_signal_groups": [{"group_id": "group_1", "signals": []}],
            }
        ),
        encoding="utf-8",
    )

    trait_definitions = load_trait_definitions_from_contract(runtime_contract_path)

    assert [item["trait_id"] for item in trait_definitions] == ["trait_a"]
    assert trait_definitions[0]["core_signals"][0]["ref"] == "P1"
    assert trait_definitions[0]["extended_signal_groups"][0]["group_id"] == "group_1"


def test_load_trait_definitions_from_runtime_bundle_prefers_bundled_weighted_traits(tmp_path: Path) -> None:
    resolved_traits_dir = tmp_path / "traits"
    resolved_traits_dir.mkdir()
    (resolved_traits_dir / "T2_CustomTrait.json").write_text(
        json.dumps(
            {
                "trait_id": "trait_b",
                "core_signals": [{"ref": "P2", "weight": 2}],
                "extended_signal_groups": [],
            }
        ),
        encoding="utf-8",
    )

    trait_definitions = load_trait_definitions_from_runtime_bundle(
        {
            "resolved_paths": {"traits_dir": str(resolved_traits_dir)},
            "traits": [{"trait_id": "ignored_trait"}],
        }
    )

    assert [item["trait_id"] for item in trait_definitions] == ["ignored_trait"]


def test_load_trait_definitions_from_runtime_bundle_uses_resolved_traits_dir_without_bundle(tmp_path: Path) -> None:
    resolved_traits_dir = tmp_path / "traits"
    resolved_traits_dir.mkdir()
    (resolved_traits_dir / "T2_CustomTrait.json").write_text(
        json.dumps(
            {
                "trait_id": "trait_b",
                "core_signals": [{"ref": "P2", "weight": 2}],
                "extended_signal_groups": [],
            }
        ),
        encoding="utf-8",
    )

    trait_definitions = load_trait_definitions_from_runtime_bundle(
        {
            "resolved_paths": {"traits_dir": str(resolved_traits_dir)},
        }
    )

    assert [item["trait_id"] for item in trait_definitions] == ["trait_b"]


def test_load_trait_definitions_from_dir_returns_only_trait_json_payloads(tmp_path: Path) -> None:
    (tmp_path / "T3_CustomTrait.json").write_text(
        json.dumps(
            {
                "trait_id": "trait_c",
                "core_signals": [{"ref": "P3", "weight": 3}],
                "extended_signal_groups": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    trait_definitions = load_trait_definitions_from_dir(tmp_path)

    assert [item["trait_id"] for item in trait_definitions] == ["trait_c"]


def test_load_trait_definitions_from_dir_canonicalizes_runtime_trait_ids(tmp_path: Path) -> None:
    (tmp_path / "T10_Behavior.json").write_text(
        json.dumps(
            {
                "trait_id": "T10_Behavior",
                "core_signals": [{"ref": "P10", "weight": 3}],
                "extended_signal_groups": [],
            }
        ),
        encoding="utf-8",
    )

    trait_definitions = load_trait_definitions_from_dir(tmp_path)

    assert [item["trait_id"] for item in trait_definitions] == ["trait_10"]
    assert trait_definitions[0]["trait_aliases"] == ["trait_10", "T10_Behavior"]


def test_trait_id_alias_helpers_support_runtime_and_rubric_formats() -> None:
    assert canonical_trait_id("trait_11") == "trait_11"
    assert canonical_trait_id("T11_Flexibility") == "trait_11"
    assert trait_id_aliases("T11_Flexibility") == ["trait_11", "T11_Flexibility"]
