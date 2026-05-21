import tempfile
import unittest
from pathlib import Path

from data_store import InterviewAppSettingsStore
from interview_audio_recorder import _normalize_whisper_transcribe_settings


class TestWhisperSettings(unittest.TestCase):
    def test_normalize_uses_defaults_when_missing(self):
        self.assertEqual(
            _normalize_whisper_transcribe_settings(None),
            {"vad_filter": True, "beam_size": 5, "temperature": 0.0},
        )

    def test_normalize_clamps_invalid_values(self):
        resolved = _normalize_whisper_transcribe_settings(
            {"vad_filter": "yes", "beam_size": 100, "temperature": 9.9}
        )
        self.assertEqual(resolved, {"vad_filter": True, "beam_size": 5, "temperature": 0.0})

    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            store = InterviewAppSettingsStore(path)
            payload = {"whisper_beam_size": 4, "whisper_vad_filter": False}
            store.save(payload)
            self.assertEqual(store.load(), payload)


if __name__ == "__main__":
    unittest.main()
