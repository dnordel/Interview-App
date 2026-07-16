import tempfile
import unittest
from pathlib import Path

from docx_compat import Document
from scoring_reporting import DocxExporter, ReportingValidationError


def _doc_text(doc) -> str:
    paragraphs = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    tables = "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    return f"{paragraphs}\n{tables}"


class TestDocxExporter(unittest.TestCase):
    def _rubric(self):
        return {
            "traits": [],
            "tracks": {"general": {"label": "General", "max_weighted_total": 15}},
            "absolute_disqualifiers": [],
        }

    def _scoring(self):
        return {
            "rows": [
                {
                    "trait_id": "trait_1",
                    "trait_name": "Empathy",
                    "priority": "Critical",
                    "weight": 3,
                    "raw_score": 5,
                    "weighted_score": 15,
                    "primary_question": "How do you support a child in distress?",
                    "no_example_after_followups": False,
                    "absolute_disqualifier": False,
                    "selected_signal_ids": ["S_CHILD_CENTERED"],
                }
            ],
            "weighted_total": 15,
            "max_weighted_total": 15,
            "percent_of_max": 100.0,
            "percent_of_max_label": "100%",
            "skipped_traits_count": 0,
            "critical_eq_1": False,
            "disqualifier_present": False,
            "locked_rule": None,
            "outcome": "Hire",
        }

    def _payload(self):
        return {
            "candidate": {
                "name": "Ada Lovelace",
                "interview_date": "2026-02-20",
                "school": "Palmdale",
                "track": "general",
                "qualification": {
                    "has_degree": True,
                    "degree_type": "BA",
                    "degree_in_ece": False,
                    "ece_units_completed": 24,
                    "infant_toddler_class_completed": False,
                    "total_units_completed": None,
                    "years_experience": 4,
                },
            },
            "flow_transcript": [
                {
                    "flow_index": 0,
                    "type": "intro",
                    "id": "intro_script",
                    "prompt": "Intro script should stay out of notes.",
                    "candidate_transcript": "Company overview.",
                },
                {
                    "flow_index": 1,
                    "type": "trait",
                    "id": "trait_1",
                    "question": "How do you support a child in distress?",
                    "candidate_transcript": "I kneel down and name feelings.",
                    "no_example_after_followups": False,
                },
                {
                    "flow_index": 2,
                    "type": "custom",
                    "id": "pay",
                    "prompt": "What pay are you looking for?",
                    "candidate_transcript": "Noisy transcript",
                    "evaluator_notes": "$24/hour",
                },
            ],
        }

    def test_export_raises_when_candidate_name_missing(self):
        payload = {"candidate": {"interview_date": "2026-02-20", "track": "general"}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ReportingValidationError, "required candidate field: 'name'"):
                DocxExporter(Path(directory)).export(self._rubric(), payload, self._scoring())

    def test_export_uses_standard_human_scored_notes_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = DocxExporter(Path(directory)).export(self._rubric(), self._payload(), self._scoring())
            doc = Document(path)

        text = _doc_text(doc)
        headings = [paragraph.text for paragraph in doc.paragraphs if paragraph.style.name.startswith("Heading")]
        self.assertEqual(
            headings[:4],
            [
                "1. Candidate Snapshot",
                "2. Candidate Education and Experience Summary",
                "3. Score Summary",
                "4. Candidate Answers",
            ],
        )
        self.assertIn("Ada Lovelace", text)
        self.assertIn("Empathy", text)
        self.assertIn("5", text)
        self.assertIn("15", text)
        self.assertIn("I kneel down and name feelings.", text)
        self.assertIn("$24/hour", text)
        self.assertNotIn("Noisy transcript", text)
        self.assertNotIn("Intro script should stay out of notes.", text)

    def test_all_skipped_traits_use_compact_score_summary(self):
        scoring = self._scoring()
        scoring["rows"][0]["skipped"] = True
        scoring["weighted_total"] = 0
        scoring["skipped_traits_count"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = DocxExporter(Path(directory)).export(self._rubric(), self._payload(), scoring)
            doc = Document(path)

        self.assertEqual(doc.tables[2].rows[0].cells[0].text, "Scored Ratings")
        self.assertEqual(doc.tables[2].rows[0].cells[1].text, "No scored trait ratings were recorded.")
        self.assertEqual(doc.tables[2].rows[1].cells[1].text, "1")


if __name__ == "__main__":
    unittest.main()
