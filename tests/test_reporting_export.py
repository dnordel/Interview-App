import tempfile
import unittest
from pathlib import Path

from docx_compat import Document
from reporting import DocxExporter, ReportingValidationError


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

        self.assertIn(
            "Candidate Answer (auto-transcribed): I send weekly updates and hold check-ins.",
            doc_text,
        )
        self.assertNotIn("Interviewer note that should not replace transcript.", doc_text)

    def test_export_includes_executive_and_answer_summaries(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "general",
            },
            "flow_transcript": [
                {
                    "type": "trait",
                    "title": "Trait Question",
                    "question": "Tell me about conflict resolution.",
                    "candidate_transcript": "I de-escalate tension and align expectations.",
                }
            ],
            "custom_answers": [],
        }

        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            exporter_summary = exporter.export.__globals__["LocalInterviewSummarizer"]

            class _StubSummarizer:
                def summarize_executive(self, transcript_text):
                    return "Executive eval summary"

                def summarize_answer(self, answer_text, question_text=""):
                    return "Answer eval summary"

            exporter.export.__globals__["LocalInterviewSummarizer"] = _StubSummarizer
            try:
                out_path = exporter.export(self._rubric(), payload, self._scoring())
            finally:
                exporter.export.__globals__["LocalInterviewSummarizer"] = exporter_summary
            doc = Document(out_path)
            doc_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)

        self.assertIn("Executive Candidate Summary", doc_text)
        self.assertIn("Executive eval summary", doc_text)
        self.assertIn("Answer Summary: Answer eval summary", doc_text)

    def test_export_uses_pending_placeholder_when_summary_generation_deferred(self):
        payload = {
            "candidate": {
                "name": "Ada",
                "interview_date": "2026-02-20",
                "track": "general",
            },
            "flow_transcript": [
                {
                    "type": "trait",
                    "title": "Trait Question",
                    "question": "Tell me about conflict resolution.",
                    "candidate_transcript": "I de-escalate tension and align expectations.",
                }
            ],
            "custom_answers": [],
        }
        with tempfile.TemporaryDirectory() as td:
            exporter = DocxExporter(Path(td))
            out_path = exporter.export(self._rubric(), payload, self._scoring(), include_generated_summaries=False)
            doc = Document(out_path)
            doc_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)

        self.assertIn("Summary pending/failed", doc_text)



if __name__ == "__main__":
    unittest.main()
