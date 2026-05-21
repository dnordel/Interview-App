from pathlib import Path

from question_settings_service import QuestionSettingsService


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
        "id": "trait_new",
        "name": "New",
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
