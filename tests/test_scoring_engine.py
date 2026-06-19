import unittest

from scoring_reporting import ReportingValidationError, ScoringEngine


class TestScoringEngineEvaluate(unittest.TestCase):
    def _rubric(self, second_trait_priority: str = "non-critical"):
        return {
            "traits": [
                {
                    "id": "trait_a",
                    "name": "Trait A",
                    "priority": "non-critical",
                    "weight": 31,
                    "applicable_tracks": ["all"],
                    "primary_question": "Q1",
                },
                {
                    "id": "trait_b",
                    "name": "Trait B",
                    "priority": second_trait_priority,
                    "weight": 1,
                    "applicable_tracks": ["all"],
                    "primary_question": "Q2",
                },
                {
                    "id": "trait_c",
                    "name": "Trait C",
                    "priority": "non-critical",
                    "weight": 8,
                    "applicable_tracks": ["all"],
                    "primary_question": "Q3",
                },
            ],
            "tracks": {
                "general": {
                    "label": "General",
                    "max_weighted_total": 200,
                }
            },
        }

    def _evaluate(
        self,
        raw_a: int,
        raw_b: int,
        raw_c: int,
        second_trait_priority: str = "non-critical",
    ):
        rubric = self._rubric(second_trait_priority=second_trait_priority)
        trait_results = {
            "trait_a": {"raw_score": raw_a},
            "trait_b": {"raw_score": raw_b},
            "trait_c": {"raw_score": raw_c},
        }
        return ScoringEngine.evaluate(rubric, "general", trait_results)

    def test_hiring_threshold_regression_table(self):
        # Expectations are intentionally pinned to the current ScoringEngine.evaluate
        # threshold algorithm behavior for this rubric fixture.
        cases = [
            {
                "name": "79.0 remains borderline below hire cutoff",
                "raw_a": 4,
                "raw_b": 2,
                "raw_c": 4,
                "priority": "non-critical",
                "expected_percent": 79.0,
                "expected_outcome": "Borderline",
            },
            {
                "name": "79.5 remains borderline below hire cutoff",
                "raw_a": 4,
                "raw_b": 3,
                "raw_c": 4,
                "priority": "non-critical",
                "expected_percent": 79.5,
                "expected_outcome": "Borderline",
            },
            {
                "name": "80.0 becomes hire at threshold",
                "raw_a": 4,
                "raw_b": 4,
                "raw_c": 4,
                "priority": "non-critical",
                "expected_percent": 80.0,
                "expected_outcome": "Hire",
            },
            {
                "name": "critical < 3 prevents hire even at 94.5%",
                "raw_a": 5,
                "raw_b": 2,
                "raw_c": 4,
                "priority": "critical",
                "expected_percent": 94.5,
                "expected_outcome": "No Hire",
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                scoring = self._evaluate(
                    raw_a=case["raw_a"],
                    raw_b=case["raw_b"],
                    raw_c=case["raw_c"],
                    second_trait_priority=case["priority"],
                )

                self.assertEqual(scoring["percent_of_max"], case["expected_percent"])
                self.assertEqual(scoring["outcome"], case["expected_outcome"])

    def test_trait_b_score_changes_shift_percent_of_max(self):
        scoring_raw_b_2 = self._evaluate(raw_a=4, raw_b=2, raw_c=4)
        scoring_raw_b_3 = self._evaluate(raw_a=4, raw_b=3, raw_c=4)
        scoring_raw_b_4 = self._evaluate(raw_a=4, raw_b=4, raw_c=4)

        self.assertEqual(scoring_raw_b_2["weighted_total"], 158)
        self.assertEqual(scoring_raw_b_3["weighted_total"], 159)
        self.assertEqual(scoring_raw_b_4["weighted_total"], 160)
        self.assertEqual(
            [
                scoring_raw_b_2["percent_of_max"],
                scoring_raw_b_3["percent_of_max"],
                scoring_raw_b_4["percent_of_max"],
            ],
            [79.0, 79.5, 80.0],
        )

        rows_by_trait = {row["trait_id"]: row for row in scoring_raw_b_3["rows"]}
        self.assertEqual(rows_by_trait["trait_a"]["raw_score_math"], 4)
        self.assertEqual(rows_by_trait["trait_b"]["raw_score_math"], 3)
        self.assertEqual(rows_by_trait["trait_c"]["raw_score_math"], 4)

    def test_critical_lt_3_blocks_hire(self):
        scoring = self._evaluate(raw_a=5, raw_b=2, raw_c=4, second_trait_priority="critical")

        self.assertEqual(scoring["percent_of_max"], 94.5)
        self.assertEqual(scoring["outcome"], "No Hire")

    def test_critical_eq_1_still_returns_public_scoring_fields(self):
        scoring = self._evaluate(raw_a=4, raw_b=1, raw_c=4, second_trait_priority="critical")

        self.assertEqual(scoring["outcome"], "No Hire")
        self.assertEqual(scoring["locked_rule"], "Any Critical trait raw score = 1 => Immediate NO HIRE")

    def test_skipped_critical_trait_does_not_trigger_critical_flags(self):
        rubric = self._rubric(second_trait_priority="critical")
        trait_results = {
            "trait_a": {"raw_score": 5},
            "trait_b": {"raw_score": 1, "skipped": True},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, "general", trait_results)

        self.assertFalse(scoring["critical_eq_1"])
        self.assertFalse(scoring["critical_lt_3"])

    def test_scored_critical_lt_3_sets_locked_rule_when_hire_threshold_met(self):
        scoring = self._evaluate(raw_a=5, raw_b=2, raw_c=4, second_trait_priority="critical")

        self.assertTrue(scoring["critical_lt_3"])
        self.assertEqual(scoring["locked_rule"], "Any Critical trait raw score < 3 => Cannot assign HIRE")

    def test_payload_contains_current_required_keys(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {"raw_score": 4, "skipped": True},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, "general", trait_results)

        self.assertEqual(scoring["weighted_total"], 156)
        self.assertEqual(scoring["max_weighted_total_included_traits"], 195)
        self.assertEqual(scoring["configured_max_weighted_total"], 200)
        self.assertEqual(scoring["percent_of_max"], 78.0)
        self.assertEqual(scoring["outcome"], "Hire")

        rows_by_trait = {row["trait_id"]: row for row in scoring["rows"]}
        self.assertEqual(rows_by_trait["trait_b"]["weighted_score"], 0)
        self.assertTrue(rows_by_trait["trait_b"]["skipped"])


    def test_skipped_scored_question_is_excluded_from_determination_denominator(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {"raw_score": 1, "skipped": True},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, "general", trait_results)

        self.assertEqual(scoring["weighted_total"], 156)
        self.assertEqual(scoring["max_weighted_total_included_traits"], 195)
        self.assertEqual(scoring["percent_of_max"], 78.0)
        self.assertEqual(scoring["outcome"], "Hire")

    def test_invalid_track_key_uses_current_fallback_behavior(self):
        # Invalid track_key is intentionally non-fatal under the current validation policy.
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {"raw_score": 4},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, "stale-track", trait_results)

        self.assertEqual(scoring["percent_of_max"], 80.0)
        self.assertEqual(scoring["outcome"], "Hire")

    def test_non_string_track_key_uses_current_fallback_behavior(self):
        # Invalid track_key is intentionally non-fatal under the current validation policy.
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {"raw_score": 4},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, ["general"], trait_results)

        self.assertEqual(scoring["percent_of_max"], 80.0)
        self.assertEqual(scoring["outcome"], "Hire")

    def test_invalid_track_keys_match_general_track_expectations(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {"raw_score": 4},
            "trait_c": {"raw_score": 4},
        }

        expected = ScoringEngine.evaluate(rubric, "general", trait_results)
        stale_track = ScoringEngine.evaluate(rubric, "stale-track", trait_results)
        non_string_track = ScoringEngine.evaluate(rubric, ["general"], trait_results)

        self.assertEqual(stale_track["percent_of_max"], expected["percent_of_max"])
        self.assertEqual(stale_track["outcome"], expected["outcome"])
        self.assertEqual(non_string_track["percent_of_max"], expected["percent_of_max"])
        self.assertEqual(non_string_track["outcome"], expected["outcome"])

    def test_percent_rounding_uses_explicit_half_up_strategy(self):
        pct, pct_rounded = ScoringEngine._calculate_percent(318, 400)

        self.assertIsNotNone(pct)
        self.assertEqual(float(pct), 79.5)
        self.assertEqual(pct_rounded, 79.5)

    def test_percent_rounding_is_stable_for_fractional_values(self):
        _pct, pct_rounded = ScoringEngine._calculate_percent(319, 400)

        self.assertEqual(pct_rounded, 79.75)

    def test_missing_applicable_trait_score_returns_incomplete(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4},
            "trait_b": {},
            "trait_c": {"raw_score": 4},
        }

        scoring = ScoringEngine.evaluate(rubric, "general", trait_results)

        self.assertEqual(scoring["outcome"], "Incomplete")
        self.assertEqual(scoring["locked_rule"], "One or more applicable traits are missing final raw scores")

    def test_deepseek_auto_no_hire_signal_overrides_human_hire_score(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {
                "raw_score": 5,
                "model_signal_suggestions": [
                    {
                        "signal_id": "AUTO",
                        "confidence": 1.0,
                        "evidence_quote": "Unsafe quote.",
                        "rationale": "Automatic no-hire.",
                    }
                ],
            },
            "trait_b": {"raw_score": 5},
            "trait_c": {"raw_score": 5},
        }

        def fake_advisory(_trait_id, _suggestions):
            return {
                "net_signal_score": 0,
                "suggested_raw_score": 2,
                "auto_no_hire_signal_ids": ["AUTO"],
                "auto_no_hire_reasons": ["Automatic no-hire."],
                "auto_no_hire_quotes": ["Unsafe quote."],
            }

        original = __import__("scoring_reporting")._deepseek_signal_advisory
        try:
            __import__("scoring_reporting")._deepseek_signal_advisory = fake_advisory
            scoring = ScoringEngine.evaluate(rubric, "general", trait_results)
        finally:
            __import__("scoring_reporting")._deepseek_signal_advisory = original

        self.assertEqual(scoring["outcome"], "No Hire")
        self.assertTrue(scoring["auto_no_hire_present"])
        self.assertEqual(scoring["rows"][0]["auto_no_hire_signal_ids"], ["AUTO"])

    def test_explicit_suggested_score_adjustment_requires_reason(self):
        rubric = self._rubric()
        trait_results = {
            "trait_a": {"raw_score": 4, "suggested_raw_score": 5},
            "trait_b": {"raw_score": 4},
            "trait_c": {"raw_score": 4},
        }

        with self.assertRaisesRegex(ReportingValidationError, "adjustment_reason"):
            ScoringEngine.evaluate(rubric, "general", trait_results)


if __name__ == "__main__":
    unittest.main()
