import json
from pathlib import Path

from data_store import QuestionOverridesStore, RubricLoader
from scoring_reporting import ScoringEngine, load_trait_signal_ui_definition


ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = ROOT / "config" / "rubric.json"
QUESTION_OVERRIDES_PATH = ROOT / "config" / "question_overrides.json"


def load_rubric() -> dict:
    return json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


def test_bss_track_uses_only_bss_specific_traits() -> None:
    loader = RubricLoader(RUBRIC_PATH)

    bss_traits = loader.get_traits_for_track("behavior_support_specialist")
    infant_traits = loader.get_traits_for_track("infant_toddler")
    preschool_traits = loader.get_traits_for_track("preschool")

    assert len(bss_traits) == 13
    assert {trait["id"] for trait in bss_traits} == {f"bss_trait_{index}" for index in range(1, 14)}
    for trait_id in [f"trait_{index}" for index in range(1, 8)]:
        assert trait_id in {trait["id"] for trait in infant_traits}
        assert trait_id in {trait["id"] for trait in preschool_traits}
        assert trait_id not in {trait["id"] for trait in bss_traits}


def test_bss_scoring_uses_configured_150_point_max() -> None:
    rubric = load_rubric()
    trait_inputs = {f"bss_trait_{index}": {"raw_score": 5} for index in range(1, 14)}

    scoring = ScoringEngine.evaluate(rubric, "behavior_support_specialist", trait_inputs)

    assert scoring["configured_max_weighted_total"] == 150
    assert scoring["max_weighted_total"] == 150
    assert scoring["weighted_total"] == 150
    assert scoring["percent_of_max"] == 100.0
    assert scoring["outcome"] == "Hire"


def test_bss_critical_score_one_is_no_hire() -> None:
    rubric = load_rubric()
    trait_inputs = {f"bss_trait_{index}": {"raw_score": 5} for index in range(1, 14)}
    trait_inputs["bss_trait_1"] = {"raw_score": 1}

    scoring = ScoringEngine.evaluate(rubric, "behavior_support_specialist", trait_inputs)

    assert scoring["outcome"] == "No Hire"
    assert scoring["critical_eq_1"] is True
    assert scoring["locked_rule"] == "Any Critical trait raw score = 1 => Immediate NO HIRE"


def test_bss_signal_ui_definition_loads_from_weighted_signal_bundle() -> None:
    definition = load_trait_signal_ui_definition("bss_trait_1")

    assert definition["trait_id"] == "bss_trait_1"
    assert definition["valid_signal_ids"]
    assert "BSS_T1_FRAMES_BEHAVIOR_AS_COMMUNICATION" in definition["valid_signal_ids"]


def test_bss_flow_uses_teacher_custom_questions_before_and_after_scored_traits() -> None:
    store = QuestionOverridesStore(QUESTION_OVERRIDES_PATH)

    bss_custom = store.list_custom_questions("behavior_support_specialist")
    preschool_custom = store.list_custom_questions("preschool")
    bss_flow = store.get_question_flow_raw("behavior_support_specialist")

    assert bss_custom == preschool_custom
    assert bss_flow[:2] == [
        {"type": "custom", "id": "Why-ECE"},
        {"type": "custom", "id": "Why-LPL"},
    ]
    assert bss_flow[2:15] == [{"type": "trait", "id": f"bss_trait_{index}"} for index in range(1, 14)]
    assert bss_flow[15:] == [
        {"type": "custom", "id": "FT-or-PT"},
        {"type": "custom", "id": "Not-Avail"},
        {"type": "custom", "id": "Pay"},
        {"type": "custom", "id": "Start"},
    ]


def test_bss_json_flow_routes_from_custom_questions_into_first_scored_trait() -> None:
    loader = RubricLoader(RUBRIC_PATH)
    store = QuestionOverridesStore(QUESTION_OVERRIDES_PATH)

    traits = loader.get_traits_for_track("behavior_support_specialist")
    custom_questions = store.list_custom_questions("behavior_support_specialist")
    flow = store.ensure_flow(
        "behavior_support_specialist",
        [trait["id"] for trait in traits],
        [question["id"] for question in custom_questions],
    )

    assert flow[0] == {"type": "custom", "id": "Why-ECE"}
    assert flow[1] == {"type": "custom", "id": "Why-LPL"}
    assert flow[2] == {"type": "trait", "id": "bss_trait_1"}
    assert any(trait["id"] == "bss_trait_1" for trait in traits)


def test_new_json_track_flow_works_without_code_specific_track_logic(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    overrides_path = tmp_path / "question_overrides.json"
    rubric_path.write_text(
        json.dumps(
            {
                "metadata": {"version": "test"},
                "scoring": {},
                "tracks": {"new_track": {"label": "New Track", "max_weighted_total": 5}},
                "absolute_disqualifiers": [],
                "traits": [
                    {
                        "id": "new_trait",
                        "name": "New Trait",
                        "priority": "Medium",
                        "weight": 1,
                        "applicable_tracks": ["new_track"],
                        "primary_question": "New scored prompt?",
                        "descriptors": {"1": "Low", "2": "Two", "3": "Mid", "4": "Four", "5": "High"},
                        "sample_answers": {"1": "Low", "3": "Mid", "5": "High"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {"new_track": [{"id": "intro", "text": "Intro?", "order": 1}]},
                "track_question_flow": {
                    "new_track": [{"type": "custom", "id": "intro"}, {"type": "trait", "id": "new_trait"}]
                },
            }
        ),
        encoding="utf-8",
    )

    loader = RubricLoader(rubric_path)
    store = QuestionOverridesStore(overrides_path)
    traits = loader.get_traits_for_track("new_track")
    custom_questions = store.list_custom_questions("new_track")

    assert [trait["id"] for trait in traits] == ["new_trait"]
    assert store.ensure_flow("new_track", ["new_trait"], [question["id"] for question in custom_questions]) == [
        {"type": "custom", "id": "intro"},
        {"type": "trait", "id": "new_trait"},
    ]
