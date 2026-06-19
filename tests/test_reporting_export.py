import tempfile
import unittest
from pathlib import Path

from docx.shared import Inches, Pt
from docx_compat import Document
from scoring_reporting import DocxExporter, ReportingValidationError


class TestDocxExporterValidation(unittest.TestCase):
    def _rubric(self):
        return {
            "traits": [],
            "tracks": {
                "general": {
                    "label": "General",
                    "max_weighted_total": 10,
                }
            },
            "absolute_disqualifiers": [],
        }

    def _scoring(self):
        return {
            "rows": [],
            "weighted_total": 0,
            "max_weighted_total": 10,
            "percent_of_max": 0.0,
            "critical_eq_1": False,
            "disqualifier_present": False,
            "locked_rule": None,
            "outcome": "No Hire",
        }

    def _trait_scoring(self):
        return {
            "rows": [
                {
                    "trait_name": "Empathy",
                    "priority": "Critical",
                    "weight": 3,
                    "raw_score": 5,
                    "weighted_score": 15,
                    "system_checkbox_score": 15,
                    "net_signal_score": 6,
                    "suggested_raw_score": 4,
                    "final_raw_score": 5,
                    "interviewer_adjusted": True,
                    "adjustment_reason": "Full answer was stronger than signal evidence.",
                    "deepseek_calculated_score": 9,
                    "deepseek_raw_score": 3,
                    "model_trait_score": {
                        "raw_score": 3,
                        "evidence_quote": "Calm, concrete example.",
                        "rationale": "Some descriptor match.",
                        "risks_or_gaps": "Limited detail.",
                    },
                    "primary_question": "How do you support a child in distress?",
                    "no_example_after_followups": False,
                    "question_notes": "",
                    "trait_notes": "",
                    "verbatim_notes": "Calm, concrete example.",
                    "absolute_disqualifier": False,
                    "selected_signal_ids": ["S_CHILD_CENTERED", "S_COREGULATION"],
                    "model_signal_suggestions": [
                        {
                            "signal_id": "S_CHILD_CENTERED",
                            "confidence": 0.8,
                            "evidence_quote": "Calm, concrete example.",
                            "rationale": "Matched calm routine.",
                        },
                        {
                            "signal_id": "S_MODEL_ONLY",
                            "confidence": 0.4,
                            "evidence_quote": "Possible observation.",
                            "rationale": "Possible observation.",
                        },
                    ],
                    "model_signal_override": {
                        "accepted_signal_ids": ["S_CHILD_CENTERED"],
                        "rejected_signal_ids": ["S_MODEL_ONLY"],
                        "manual_only_signal_ids": ["S_COREGULATION"],
                    },
                }
            ],
            "weighted_total": 15,
            "max_weighted_total": 15,
            "percent_of_max": 100.0,
            "percent_of_max_label": "100%",
            "critical_eq_1": False,
            "disqualifier_present": False,
            "locked_rule": None,
            "outcome": "Hire",
        }

    def test_export_falls_back_for_stale_track_key(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "stale-track",
            }
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            path = exporter.export(self._rubric(), payload, self._scoring())
            self.assertTrue(path.exists())

    def test_export_raises_when_candidate_name_missing(self):
        payload = {
            "candidate": {
                "interview_date": "2026-02-20",
                "track": "general",
            }
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            with self.assertRaisesRegex(ReportingValidationError, "required candidate field: 'name'"):
                exporter.export(self._rubric(), payload, self._scoring())

    def test_extract_full_candidate_transcript_prefers_flow_transcript(self):
        payload = {
            "flow_transcript": [
                {"candidate_transcript": "Whisper segment one."},
                {"candidate_transcript": "Whisper segment two."},
            ],
            "audio_recording": [
                {"candidate_transcript": "Stale recording transcript."},
            ],
        }

        transcript = DocxExporter._extract_full_candidate_transcript(payload)

        self.assertEqual(transcript, "Whisper segment one.\n\nWhisper segment two.")

    def test_export_uses_candidate_transcript_for_custom_flow_entry(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "general",
            },
            "flow_transcript": [
                {
                    "type": "custom",
                    "title": "Custom Question",
                    "question": "How do you partner with families?",
                    "candidate_transcript": "I send weekly updates and hold check-ins.",
                    "answer": "Interviewer note that should not replace transcript.",
                }
            ],
            "custom_answers": [],
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            out_path = exporter.export(self._rubric(), payload, self._scoring())
            doc = Document(out_path)
            doc_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
            table_text = "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)

        self.assertIn("Full Candidate Answer (auto-transcribed)", doc_text)
        self.assertIn("I send weekly updates and hold check-ins.", table_text)
        self.assertNotIn("Interviewer note that should not replace transcript.", doc_text + table_text)

    def test_export_places_executive_summary_before_report_sections(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "general",
            },
            "executive_summary": "Candidate uses calm routines and family communication.",
            "interview_highlights": ["Uses visual timers.", "Communicates consistently with families."],
            "answer_summaries": [
                {
                    "flow_index": 1,
                    "summary": "Gave a concrete routine example.",
                    "evidence_quotes": ["songs and visual timers"],
                    "rubric_alignment": "Uses predictable transition supports.",
                    "risks_or_gaps": "",
                }
            ],
            "flow_transcript": [
                {
                    "flow_index": 1,
                    "type": "custom",
                    "title": "Custom Question",
                    "question": "How do you support transitions?",
                    "candidate_transcript": "I use songs and visual timers.",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            out_path = exporter.export(self._rubric(), payload, self._scoring())
            doc = Document(out_path)
            paragraphs = [paragraph.text for paragraph in doc.paragraphs]
            doc_text = "\n".join(paragraphs)
            table_text = "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)

        self.assertLess(paragraphs.index("Executive Summary"), paragraphs.index("Candidate Snapshot"))
        self.assertLess(paragraphs.index("DeepSeek Evidence Summary"), paragraphs.index("Candidate Snapshot"))
        self.assertIn("Candidate uses calm routines and family communication.", doc_text)
        self.assertIn("Uses visual timers.", doc_text)
        self.assertIn("Communicates consistently with families.", doc_text)
        self.assertIn("DeepSeek summary", table_text)
        self.assertIn("Gave a concrete routine example.", table_text)
        self.assertIn("songs and visual timers", table_text)
        self.assertIn("Uses predictable transition supports.", table_text)

    def test_export_uses_interview_notes_template_layout(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "school": "Palmdale",
                "track": "general",
            },
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            out_path = exporter.export(self._rubric(), payload, self._scoring())
            doc = Document(out_path)

        section = doc.sections[0]
        self.assertEqual(section.top_margin, Inches(0.55))
        self.assertEqual(section.right_margin, Inches(0.65))
        self.assertEqual(section.bottom_margin, Inches(0.55))
        self.assertEqual(section.left_margin, Inches(0.65))
        self.assertEqual(doc.paragraphs[0].text, "Structured Behavioral Interview Report")
        self.assertEqual(doc.paragraphs[0].alignment, 1)
        self.assertEqual(doc.paragraphs[0].runs[0].font.name, "Times New Roman")
        self.assertEqual(doc.paragraphs[0].runs[0].font.size, Pt(12))
        self.assertIn("Ada | Palmdale | General | Interview Date: 2026-02-20", doc.paragraphs[1].text)
        self.assertEqual(doc.paragraphs[1].alignment, 1)
        self.assertTrue(any(table.rows[0].cells[0].text.startswith("Recommendation:") for table in doc.tables))
        self.assertTrue(
            any(
                table.rows[0].cells[0].text == "Candidate Name"
                and table.rows[0].cells[1].text == "Ada"
                for table in doc.tables
            )
        )

    def test_export_reports_legacy_selected_signals_and_deepseek_trait_suggestions(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "general",
            },
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            out_path = exporter.export(self._rubric(), payload, self._trait_scoring())
            doc = Document(out_path)
            doc_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
            table_text = "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)

        self.assertIn("Compatibility selected signal IDs:", doc_text)
        self.assertIn("DeepSeek advisory signal observations:", doc_text)
        self.assertIn("Interviewer Score", table_text)
        self.assertIn("Human Weighted Score", table_text)
        self.assertIn("DeepSeek Suggested Score", table_text)
        self.assertIn("Final interviewer raw score", table_text)
        self.assertIn("Human weighted score", table_text)
        self.assertIn("DeepSeek net signal score", table_text)
        self.assertIn("DeepSeek suggested raw score", table_text)
        self.assertIn("Interviewer adjusted from DeepSeek suggestion", table_text)
        self.assertIn("Adjustment reason", table_text)
        self.assertIn("DeepSeek advisory raw score", table_text)
        self.assertIn("DeepSeek suggested weighted score", table_text)
        self.assertIn("DeepSeek score evidence", table_text)
        self.assertIn("S_CHILD_CENTERED, S_COREGULATION", table_text)
        self.assertIn("DeepSeek signal-scored observations", table_text)
        self.assertIn("DeepSeek suggested observations", table_text)
        self.assertIn("Compatibility selected-only observations", table_text)
        self.assertIn("Used by DeepSeek advisory scoring", table_text)
        self.assertIn("DeepSeek suggestion", table_text)


if __name__ == "__main__":
    unittest.main()
