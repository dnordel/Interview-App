import tempfile
import unittest
from pathlib import Path

from data_store import QuestionOverridesStore


class TestQuestionOverridesStoreLoadRecovery(unittest.TestCase):
    def test_load_invalid_json_recovers_to_defaults_and_archives_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = Path(temp_dir) / "question_overrides.json"
            overrides_path.write_text('{"broken": ', encoding="utf-8")

            store = QuestionOverridesStore(overrides_path)

            self.assertEqual(
                store.data,
                {
                    "track_trait_order": {},
                    "trait_question_overrides": {},
                    "custom_questions": {},
                    "track_question_flow": {},
                },
            )
            self.assertFalse(overrides_path.exists())
            archived = list(Path(temp_dir).glob("question_overrides.corrupt-*.json"))
            self.assertEqual(len(archived), 1)


if __name__ == "__main__":
    unittest.main()
