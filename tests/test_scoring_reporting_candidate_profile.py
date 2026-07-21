import unittest
from decimal import Decimal

from scoring_reporting import CandidateQualification, validate_candidate_qualification


class TestCandidateProfileValidation(unittest.TestCase):
    def test_validate_accepts_fractional_semester_units(self):
        ok, msg, profile = validate_candidate_qualification(
            "no",
            "",
            False,
            "12.50",
            "30.75",
            False,
            "3",
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(profile.ece_units_completed, Decimal("12.50"))
        self.assertEqual(profile.total_units_completed, Decimal("30.75"))

    def test_validate_with_degree(self):
        ok, msg, profile = validate_candidate_qualification(
            "yes",
            "BA",
            True,
            "24",
            "",
            True,
            "6",
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(profile.has_degree, True)
        self.assertEqual(profile.degree_type, "BA")
        self.assertEqual(profile.ece_units_completed, 24)
        self.assertEqual(profile.total_units_completed, None)

    def test_validate_without_degree_requires_total_units(self):
        ok, msg, _profile = validate_candidate_qualification(
            "no",
            "",
            False,
            "12",
            "",
            False,
            "3",
        )

        self.assertFalse(ok)
        self.assertIn("Total units completed", msg)

    def test_validate_degree_in_ece_does_not_require_ece_units(self):
        ok, msg, profile = validate_candidate_qualification(
            "yes",
            "BA",
            True,
            "",
            "120",
            False,
            "10",
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertIsNone(profile.ece_units_completed)

    def test_validate_rejects_degree_in_ece_checkbox_without_degree(self):
        ok, msg, _profile = validate_candidate_qualification(
            "no",
            "",
            True,
            "",
            "12",
            False,
            "2",
        )

        self.assertFalse(ok)
        self.assertIn("only", msg.lower())

    def test_validate_degree_holder_does_not_require_total_units(self):
        ok, msg, _profile = validate_candidate_qualification(
            "yes",
            "BA",
            False,
            "24",
            "",
            False,
            "5",
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_roundtrip_from_dict(self):
        profile = CandidateQualification.from_dict(
            {
                "has_degree": False,
                "degree_type": "",
                "degree_in_ece": False,
                "ece_units_completed": 18,
                "infant_toddler_class_completed": True,
                "total_units_completed": 45,
                "years_experience": 8,
            }
        )
        self.assertEqual(profile.to_dict()["total_units_completed"], 45)
        self.assertEqual(profile.to_dict()["years_experience"], 8)

    def test_validate_requires_years_of_experience(self):
        ok, msg, _profile = validate_candidate_qualification(
            "yes",
            "BA",
            True,
            "24",
            "120",
            True,
            "",
        )

        self.assertFalse(ok)
        self.assertIn("Years of experience", msg)


if __name__ == "__main__":
    unittest.main()
