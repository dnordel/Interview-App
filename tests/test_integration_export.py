import json
import tempfile
import unittest
from pathlib import Path

from scoring_reporting import (
    build_integration_payload,
    normalize_outcome_label,
    serialize_integration_payload,
)


class TestIntegrationExport(unittest.TestCase):
    def test_normalize_outcome_label(self):
        self.assertEqual(normalize_outcome_label("Hire"), "hire")
        self.assertEqual(normalize_outcome_label("No Hire"), "no_hire")
        self.assertEqual(normalize_outcome_label("unknown"), "borderline")

    def test_build_payload_includes_expected_fields(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "school": "PS 10",
                "track": "lead",
                "qualification": {
                    "has_degree": True,
                    "degree_type": "BA",
                    "degree_in_ece": False,
                    "ece_units_completed": 18,
                    "infant_toddler_class_completed": True,
                    "total_units_completed": None,
                    "years_experience": 7,
                },
            },
            "executive_summary": "Strong classroom routines.",
            "interview_highlights": ["Uses visuals.", "Keeps family communication clear."],
            "answer_summaries": [{"flow_index": 1, "summary": "Gave a concrete example."}],
            "summary_status": "generated",
            "summary_warnings": [],
            "custom_answers": [{"id": "c1", "answer": "Example"}],
            "flow_transcript": [
                {
                    "type": "trait",
                    "id": "t1",
                    "question": "Tell me about...",
                    "candidate_transcript": "I handled conflict...",
                }
            ],
            "referral_packet": {
                "resume_path": "resume.pdf",
                "interview_notes_path": "notes.docx",
                "transcript_path": "transcript.txt",
            },
            "communication_log": [{"event": "director_referral_sent"}],
        }
        scoring = {
            "percent_of_max": 82.5,
            "outcome": "Hire",
            "rows": [
                {
                    "trait_id": "t1",
                    "trait_name": "Teamwork",
                    "raw_score": 4,
                    "question_notes": "good",
                    "trait_notes": "solid",
                    "verbatim_notes": "quoted",
                }
            ],
        }

        out = build_integration_payload(payload, scoring)

        self.assertEqual(out["candidate"]["name"], "Ada")
        self.assertEqual(out["percent_of_max"], 82.5)
        self.assertEqual(out["candidate"]["qualification"]["degree_type"], "BA")
        self.assertEqual(out["decision"], "hire")
        self.assertEqual(out["candidate"]["qualification"]["years_experience"], 7)
        self.assertEqual(len(out["interview_notes"]["traits"]), 1)
        self.assertEqual(len(out["flow_transcript_slices"]), 1)
        self.assertEqual(out["referral_packet"]["resume_path"], "resume.pdf")
        self.assertEqual(len(out["communication_log"]), 1)
        self.assertEqual(out["executive_summary"], "Strong classroom routines.")
        self.assertEqual(out["interview_highlights"], ["Uses visuals.", "Keeps family communication clear."])
        self.assertEqual(out["answer_summaries"], [{"flow_index": 1, "summary": "Gave a concrete example."}])
        self.assertEqual(out["summary_status"], "generated")

    def test_serializer_writes_json_under_base_dir(self):
        export_payload = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            out_path = serialize_integration_payload(Path(td), export_payload, candidate_name="Ada")
            self.assertTrue(out_path.exists())
            self.assertIn("integration_exports", str(out_path))
            with out_path.open("r", encoding="utf-8") as f:
                stored = json.load(f)
            self.assertEqual(stored, export_payload)


if __name__ == "__main__":
    unittest.main()
