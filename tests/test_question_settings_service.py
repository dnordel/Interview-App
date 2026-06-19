from pathlib import Path

import pytest

from ui_composition import QuestionSettingsService


def _rubric() -> dict:
    return {
        "tracks": {"preschool": {"label": "Preschool"}},
        "traits": [
            {
                "id": "trait_1",
                "name": "Trait 1",
                "priority": "critical",
                "weight": 3,
                "applicable_tracks": ["preschool"],
                "primary_question": "Q?",
                "descriptors": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"},
                "sample_answers": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"},
            }
        ],
    }


def test_add_and_undo_trait(tmp_path: Path):
    service = QuestionSettingsService(tmp_path / "rubric.json", _rubric())
    baseline = _rubric()
    service.checkpoint(baseline)
    updated = service.add_trait(baseline, {
        "id": "trait_2",
        "name": "New",
        "priority": "non-critical",
        "weight": 1,
        "applicable_tracks": ["preschool"],
        "primary_question": "New question?",
        "descriptors": {"1": "", "2": "", "3": "", "4": "", "5": ""},
        "sample_answers": {"1": "", "2": "", "3": "", "4": "", "5": ""},
    })
    assert len(updated["traits"]) == 2
    undone = service.undo()
    assert undone is not None
    assert len(undone["traits"]) == 1


def test_restore_defaults(tmp_path: Path):
    source = _rubric()
    service = QuestionSettingsService(tmp_path / "rubric.json", source)
    source["traits"][0]["name"] = "changed"
    restored = service.restore_defaults()
    assert restored["traits"][0]["name"] == "Trait 1"


def test_trait_crud_requires_canonical_trait_ids(tmp_path: Path):
    service = QuestionSettingsService(tmp_path / "rubric.json", _rubric())

    with pytest.raises(ValueError, match="trait_<number>"):
        service.add_trait(_rubric(), {"id": "custom", "name": "Invalid"})

    with pytest.raises(ValueError, match="trait_<number>"):
        service.update_trait(_rubric(), "custom", {"name": "Invalid"})

    with pytest.raises(ValueError, match="trait_<number>"):
        service.delete_trait(_rubric(), "custom")


def test_trait_crud_validates_descriptors_samples_and_track_membership(tmp_path: Path):
    service = QuestionSettingsService(tmp_path / "rubric.json", _rubric())
    valid_trait = {
        "id": "trait_2",
        "name": "New",
        "priority": "non-critical",
        "weight": 1,
        "applicable_tracks": ["preschool"],
        "primary_question": "New question?",
        "descriptors": {"1": "", "2": "", "3": "", "4": "", "5": ""},
        "sample_answers": {"1": "", "2": "", "3": "", "4": "", "5": ""},
    }

    with pytest.raises(ValueError, match="descriptors"):
        service.add_trait(_rubric(), {**valid_trait, "descriptors": {"1": ""}})

    with pytest.raises(ValueError, match="sample_answers"):
        service.add_trait(_rubric(), {**valid_trait, "sample_answers": []})

    with pytest.raises(ValueError, match="Unknown applicable track"):
        service.add_trait(_rubric(), {**valid_trait, "applicable_tracks": ["unknown"]})

    updated = service.update_trait(_rubric(), "trait_1", {"applicable_tracks": ["all"]})
    assert updated["traits"][0]["applicable_tracks"] == ["all"]
