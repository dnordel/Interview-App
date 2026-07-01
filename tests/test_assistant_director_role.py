import json
from pathlib import Path

from data_store import QuestionOverridesStore, RubricLoader
from scoring_reporting import ScoringEngine, load_trait_signal_ui_definition


ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = ROOT / "config" / "rubric.json"
QUESTION_OVERRIDES_PATH = ROOT / "config" / "question_overrides.json"


def load_rubric() -> dict:
    return json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


def test_assistant_director_track_loads_from_config_without_code_branch() -> None:
    loader = RubricLoader(RUBRIC_PATH)

    traits = loader.get_traits_for_track("assistant_director_enrollment_specialist")

    assert len(traits) == 17
    assert [trait["id"] for trait in traits] == [f"ades_trait_{index}" for index in range(1, 18)]
    assert all(trait["applicable_tracks"] == ["assistant_director_enrollment_specialist"] for trait in traits)


def test_assistant_director_scoring_uses_configured_200_point_max() -> None:
    rubric = load_rubric()
    trait_inputs = {f"ades_trait_{index}": {"raw_score": 5} for index in range(1, 18)}

    scoring = ScoringEngine.evaluate(rubric, "assistant_director_enrollment_specialist", trait_inputs)

    assert scoring["configured_max_weighted_total"] == 200
    assert scoring["max_weighted_total"] == 200
    assert scoring["weighted_total"] == 200
    assert scoring["percent_of_max"] == 100.0
    assert scoring["outcome"] == "Hire"


def test_assistant_director_flow_uses_custom_questions_around_scored_traits() -> None:
    store = QuestionOverridesStore(QUESTION_OVERRIDES_PATH)

    flow = store.get_question_flow_raw("assistant_director_enrollment_specialist")

    assert flow[:2] == [
        {"type": "custom", "id": "Why-ECE"},
        {"type": "custom", "id": "Why-LPL"},
    ]
    assert flow[2:19] == [{"type": "trait", "id": f"ades_trait_{index}"} for index in range(1, 18)]
    assert flow[19:] == [
        {"type": "custom", "id": "FT-or-PT"},
        {"type": "custom", "id": "Not-Avail"},
        {"type": "custom", "id": "Pay"},
        {"type": "custom", "id": "Start"},
    ]


def test_assistant_director_signal_ui_definition_loads_from_weighted_bundle() -> None:
    definition = load_trait_signal_ui_definition("ades_trait_1")

    assert definition["trait_id"] == "ades_trait_1"
    assert "ADES_T1_STRONG_EVIDENCE" in definition["valid_signal_ids"]
    assert "ADES_T1_AUTO_NO_HIRE" in definition["valid_signal_ids"]
