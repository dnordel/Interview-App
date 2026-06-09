import unittest
from unittest.mock import patch

from interview_scoring import score_interview
from reporting import ReportingValidationError
from test_scoring_engine_contract import build_rubric


class TestInterviewScoring(unittest.TestCase):
    def _trait_inputs(self, raw_a=4, raw_b=4, raw_c=4):
        return {
            "trait_a": {"raw_score": raw_a},
            "trait_b": {"raw_score": raw_b},
            "trait_c": {"raw_score": raw_c},
        }

    def test_raw_score_only_uses_legacy_internal_adapter(self):
        scoring = score_interview(build_rubric(), "general", self._trait_inputs())

        self.assertEqual(scoring["weighted_total"], 160)
        self.assertEqual(scoring["percent_of_max"], 80.0)
        self.assertEqual(scoring["outcome"], "Hire")

    def test_selected_signal_inputs_use_trait_signal_adapter(self):
        expected = {"outcome": "Hire", "percent_of_max": 91.0, "rows": []}
        trait_inputs = self._trait_inputs()
        trait_inputs["trait_a"]["selected_signal_ids"] = ["P1"]

        with patch("interview_scoring.build_trait_scoring_payload", return_value=expected) as adapter:
            scoring = score_interview(build_rubric(), "general", trait_inputs)

        self.assertEqual(scoring, expected)
        adapter.assert_called_once_with(build_rubric(), "general", trait_inputs)

    def test_skip_excludes_trait_from_denominator(self):
        trait_inputs = self._trait_inputs(raw_a=4, raw_b=1, raw_c=4)
        trait_inputs["trait_b"]["skipped"] = True

        scoring = score_interview(build_rubric(), "general", trait_inputs)

        self.assertEqual(scoring["skipped_traits_count"], 1)
        self.assertEqual(scoring["max_weighted_total_included_traits"], 195)
        self.assertEqual(scoring["percent_of_max"], 80.0)
        self.assertEqual(scoring["outcome"], "Hire")

    def test_critical_raw_score_one_returns_no_hire(self):
        scoring = score_interview(build_rubric(second_trait_priority="critical"), "general", self._trait_inputs(raw_b=1))

        self.assertTrue(scoring["critical_eq_1"])
        self.assertEqual(scoring["outcome"], "No Hire")
        self.assertEqual(scoring["locked_rule"], "Any Critical trait raw score = 1 => Immediate NO HIRE")

    def test_absolute_disqualifier_returns_no_hire(self):
        trait_inputs = self._trait_inputs()
        trait_inputs["trait_a"]["absolute_disqualifier"] = True
        trait_inputs["trait_a"]["verbatim_notes"] = "Unsafe conduct observed."

        scoring = score_interview(build_rubric(), "general", trait_inputs)

        self.assertTrue(scoring["disqualifier_present"])
        self.assertEqual(scoring["outcome"], "No Hire")
        self.assertEqual(scoring["locked_rule"], "Any Absolute Disqualifier observed => Immediate NO HIRE")

    def test_unknown_track_key_raises(self):
        with self.assertRaises(ReportingValidationError):
            score_interview(build_rubric(), "stale-track", self._trait_inputs())


if __name__ == "__main__":
    unittest.main()
