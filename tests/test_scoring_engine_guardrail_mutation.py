from unittest.mock import patch

import scoring_reporting as reporting


def test_deliberate_scoring_engine_mutation() -> None:
    with patch.object(
        reporting.ScoringEngine,
        "evaluate",
        new=staticmethod(lambda *args, **kwargs: {"mutated": True}),
    ):
        mutated_result = reporting.ScoringEngine.evaluate({}, "", {})
        assert mutated_result == {"mutated": True}
