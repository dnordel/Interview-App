import reporting


def test_scoring_engine_evaluate_is_restored_after_mutation() -> None:
    rubric = {
        "tracks": {"default": {"max_weighted_total": 5}},
        "traits": [
            {
                "id": "trait-1",
                "name": "Trait 1",
                "priority": "Standard",
                "weight": 1,
                "applicable_tracks": ["all"],
                "primary_question": "Question",
            }
        ],
    }
    trait_results = {"trait-1": {"raw_score": 5, "skipped": False}}

    result = reporting.ScoringEngine.evaluate(rubric, "default", trait_results)

    assert result["weighted_total"] == 5
    assert result["outcome"] == "Hire"
