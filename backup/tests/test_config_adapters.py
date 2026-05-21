from pathlib import Path

import pytest

from config_adapters import (
    ConfigValidationError,
    inventory_config_assets,
    load_json_dict,
    normalize_question_overrides_config,
    validate_disqualifier_config,
    validate_rubric_config,
)


def test_inventory_config_assets_lists_known_assets(tmp_path: Path):
    entries = inventory_config_assets(tmp_path)
    names = {item["asset"] for item in entries}
    assert "rubric.json" in names
    assert "question_overrides.json" in names


def test_load_json_dict_rejects_oversized_payload(tmp_path: Path):
    path = tmp_path / "big.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="safe size limit"):
        load_json_dict(path, required=True, context="big.json", max_bytes=1)


def test_validate_rubric_config_rejects_invalid_weight():
    payload = {
        "metadata": {},
        "scoring": {},
        "tracks": {},
        "absolute_disqualifiers": [],
        "traits": [
            {
                "id": "trait_1",
                "name": "Trait",
                "priority": "Critical",
                "weight": 0,
                "primary_question": "Question",
                "descriptors": {},
                "sample_answers": {},
                "applicable_tracks": ["all"],
            }
        ],
    }

    with pytest.raises(ConfigValidationError, match="weight"):
        validate_rubric_config(payload)


def test_validate_disqualifier_config_requires_trait_id():
    with pytest.raises(ConfigValidationError, match="trait_id"):
        validate_disqualifier_config({"questions": [{}]})


def test_normalize_question_overrides_config_drops_invalid_entries():
    payload = {
        "track_trait_order": {"lead": ["trait_1", "", 42]},
        "trait_question_overrides": {"trait_1": "  New prompt  ", "": "bad"},
        "custom_questions": {"lead": [{"id": "cq_1", "text": "Custom", "order": 1}, {"id": "", "text": "skip"}]},
        "track_question_flow": {"lead": [{"type": "TRAIT", "id": "trait_1"}, {"type": "bad", "id": "x"}]},
    }

    normalized = normalize_question_overrides_config(payload)

    assert normalized["track_trait_order"]["lead"] == ["trait_1", "42"]
    assert normalized["trait_question_overrides"] == {"trait_1": "New prompt"}
    assert normalized["custom_questions"]["lead"] == [{"id": "cq_1", "text": "Custom", "order": 1}]
    assert normalized["track_question_flow"]["lead"] == [{"type": "trait", "id": "trait_1"}]
