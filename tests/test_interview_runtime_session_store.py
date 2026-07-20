import json
import tempfile
import unittest
from pathlib import Path

from interview_runtime import CURRENT_SCHEMA_VERSION, InterviewSessionStore


class TestInterviewSessionStore(unittest.TestCase):
    def test_save_snapshot_includes_schema_and_question_payload(self):
        with tempfile.TemporaryDirectory() as td:
            store = InterviewSessionStore(Path(td))
            store.save_question_snapshot(
                interview_id="iv-1",
                candidate_name="Jane Doe",
                interview_date="2026-02-06",
                flow_idx=0,
                item_type="trait",
                item_id="trait-1",
                notes={"question_notes": "Strong transition example."},
                candidate_transcript="Candidate transcript.",
            )
            payload = store.load("iv-1", "Jane Doe", "2026-02-06")
            self.assertEqual(payload.get("schema_version"), CURRENT_SCHEMA_VERSION)
            question = payload.get("questions", {}).get("0", {})
            self.assertEqual(question.get("item_type"), "trait")
            self.assertEqual(question.get("candidate_transcript"), "Candidate transcript.")

    def test_load_migrates_legacy_payload_without_schema_version(self):
        with tempfile.TemporaryDirectory() as td:
            store = InterviewSessionStore(Path(td))
            path = store.session_path("iv-legacy", "Jane Doe", "2026-02-06")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"questions": {"0": {"item_type": "trait", "item_id": "trait-1"}}}), encoding="utf-8")

            payload = store.load("iv-legacy", "Jane Doe", "2026-02-06")

            self.assertEqual(payload.get("schema_version"), CURRENT_SCHEMA_VERSION)
            self.assertIn("interview", payload)
            self.assertIn("0", payload.get("questions", {}))


if __name__ == "__main__":
    unittest.main()
