from scoring_reporting import DEFAULT_CANDIDATE_TITLE, TITLE_OPTIONS, normalize_candidate_title


def test_normalize_candidate_title_accepts_allowed_values() -> None:
    assert normalize_candidate_title(TITLE_OPTIONS[0]) == "Mr."
    assert normalize_candidate_title(TITLE_OPTIONS[1]) == "Ms."


def test_normalize_candidate_title_defaults_unknown_values() -> None:
    assert normalize_candidate_title("") == DEFAULT_CANDIDATE_TITLE
    assert normalize_candidate_title("Dr.") == DEFAULT_CANDIDATE_TITLE
