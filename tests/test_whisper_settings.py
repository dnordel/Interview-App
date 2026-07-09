import tempfile
import unittest
from pathlib import Path

from data_store import InterviewAppSettingsStore
from interview_audio_recorder import _normalize_whisper_transcribe_settings


class TestWhisperSettings(unittest.TestCase):
    def test_normalize_uses_defaults_when_missing(self):
        self.assertEqual(
            _normalize_whisper_transcribe_settings(None),
            {
                "vad_filter": True,
                "beam_size": 5,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "no_repeat_ngram_size": 3,
                "repetition_penalty": 1.15,
                "hallucination_silence_threshold": 2.0,
            },
        )

    def test_normalize_clamps_invalid_values(self):
        resolved = _normalize_whisper_transcribe_settings(
            {
                "vad_filter": "yes",
                "beam_size": 100,
                "temperature": 9.9,
                "condition_on_previous_text": "no",
                "no_repeat_ngram_size": -1,
                "repetition_penalty": 99.0,
                "hallucination_silence_threshold": -1.0,
            }
        )
        self.assertEqual(
            resolved,
            {
                "vad_filter": True,
                "beam_size": 5,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "no_repeat_ngram_size": 3,
                "repetition_penalty": 1.15,
                "hallucination_silence_threshold": 2.0,
            },
        )

    def test_normalize_accepts_supported_anti_hallucination_values(self):
        resolved = _normalize_whisper_transcribe_settings(
            {
                "condition_on_previous_text": True,
                "compression_ratio_threshold": 2.0,
                "log_prob_threshold": -0.5,
                "no_speech_threshold": 0.4,
                "no_repeat_ngram_size": 4,
                "repetition_penalty": 1.25,
                "hallucination_silence_threshold": 1.5,
            }
        )

        self.assertEqual(resolved["condition_on_previous_text"], True)
        self.assertEqual(resolved["compression_ratio_threshold"], 2.0)
        self.assertEqual(resolved["log_prob_threshold"], -0.5)
        self.assertEqual(resolved["no_speech_threshold"], 0.4)
        self.assertEqual(resolved["no_repeat_ngram_size"], 4)
        self.assertEqual(resolved["repetition_penalty"], 1.25)
        self.assertEqual(resolved["hallucination_silence_threshold"], 1.5)

    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            store = InterviewAppSettingsStore(path)
            payload = {"whisper_beam_size": 4, "whisper_vad_filter": False}
            store.save(payload)
            self.assertEqual(store.load(), payload)


if __name__ == "__main__":
    unittest.main()
