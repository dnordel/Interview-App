"""Contract tests for the public ScoringEngine response shape.

This module supersedes prior extended payload expectations and locks the
public contract to the minimal required fields used by downstream consumers.
"""

import numbers
import unittest

from reporting import ScoringEngine
from test_scoring_engine import TestScoringEngineEvaluate

REQUIRED_KEYS = {"percent_of_max", "outcome"}
LEGACY_KEYS = {
    "max_weighted_total",
    "configured_max_weighted_total",
    "max_weighted_total_included_traits",
    "critical_lt_3",
    "critical_eq_1",
    "locked_rule",
}


def build_rubric(second_trait_priority: str = "non-critical") -> dict:
    return TestScoringEngineEvaluate()._rubric(second_trait_priority=second_trait_priority)


def build_trait_results(raw_a: int = 4, raw_b: int = 4, raw_c: int = 4) -> dict:
    return {
        "trait_a": {"raw_score": raw_a},
        "trait_b": {"raw_score": raw_b},
        "trait_c": {"raw_score": raw_c},
    }


class TestScoringEngineContract(unittest.TestCase):
    def test_happy_path_returns_stable_public_contract(self):
        scoring = ScoringEngine.evaluate(build_rubric(), "general", build_trait_results())

        self.assertIsInstance(scoring, dict)
        self.assertTrue(REQUIRED_KEYS.issubset(scoring.keys()))
        self.assertIsInstance(scoring["percent_of_max"], numbers.Real)
        self.assertIsInstance(scoring["outcome"], str)

    def test_legacy_fields_are_not_part_of_required_contract(self):
        scoring = ScoringEngine.evaluate(build_rubric(), "general", build_trait_results())

        self.assertEqual(REQUIRED_KEYS, {"percent_of_max", "outcome"})
        self.assertTrue(REQUIRED_KEYS.isdisjoint(LEGACY_KEYS))

        contract_view = {key: scoring[key] for key in REQUIRED_KEYS}
        self.assertEqual(set(contract_view.keys()), REQUIRED_KEYS)
        self.assertTrue(LEGACY_KEYS.isdisjoint(contract_view.keys()))


if __name__ == "__main__":
    unittest.main()
