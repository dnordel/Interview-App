import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interview_audio_recorder import RecordingSession
from transcription_diagnostics import (
    clip_diagnostic_text,
    extract_diagnostic_filename,
    format_transcription_health_summary,
    redact_paths,
    sanitize_transcription_error_reason,
)


class _FailingModel:
    def transcribe(self, *_args, **_kwargs):
        raise RuntimeError("whisper exploded")




class _SilentModel:
    def transcribe(self, *_args, **_kwargs):
        return [], {}


class TestTranscriptionDiagnostics(unittest.TestCase):
    def test_stop_and_transcribe_writes_diagnostic_report_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mic = root / "mic.wav"
            sys = root / "sys.wav"
            mic.write_bytes(b"mic-bytes")
            sys.write_bytes(b"sys-bytes")
            session = RecordingSession(
                os_name="linux",
                mic_wav=mic,
                sys_wav=sys,
                mic_label="INTERVIEWER",
                sys_label="CANDIDATE",
                mic_offset=0.0,
                sys_offset=0.0,
                whisper_model="small",
                whisper_device="cpu",
                whisper_compute_type="int8",
            )
            with patch.object(RecordingSession, "_get_or_create_model", return_value=_FailingModel()):
                with self.assertRaises(RuntimeError) as err:
                    session.stop_and_transcribe(output_dir=root, base_name="sample", language="en")

            message = str(err.exception)
            self.assertIn("Diagnostic log:", message)
            diagnostic_path = Path(message.split("Diagnostic log:", 1)[1].strip())
            self.assertTrue(diagnostic_path.exists())
            self.assertIn("diagnostics", str(diagnostic_path))

    def test_transcribe_existing_recordings_raises_when_all_tracks_missing(self):
        from interview_audio_recorder import transcribe_existing_recordings

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(RecordingSession, "_get_or_create_model", return_value=_SilentModel()):
                with self.assertRaises(RuntimeError) as err:
                    transcribe_existing_recordings(
                        output_dir=root,
                        base_name="sample",
                        mic_wav=root / "missing_mic.wav",
                        sys_wav=root / "missing_sys.wav",
                    )

            self.assertIn("No transcribable audio tracks were found", str(err.exception))

    def test_stop_and_transcribe_missing_tracks_writes_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session = RecordingSession(
                os_name="linux",
                mic_wav=root / "missing_mic.wav",
                sys_wav=root / "missing_sys.wav",
                mic_label="INTERVIEWER",
                sys_label="CANDIDATE",
                mic_offset=0.0,
                sys_offset=0.0,
                whisper_model="small",
                whisper_device="cpu",
                whisper_compute_type="int8",
            )
            with patch.object(RecordingSession, "_get_or_create_model", return_value=_SilentModel()):
                with self.assertRaises(RuntimeError) as err:
                    session.stop_and_transcribe(output_dir=root, base_name="sample", language="en")

            message = str(err.exception)
            self.assertIn("Diagnostic log:", message)
            diagnostic_path = Path(message.split("Diagnostic log:", 1)[1].strip())
            self.assertTrue(diagnostic_path.exists())

    def test_redact_paths_supports_windows_macos_and_linux_patterns(self):
        raw = (
            "win=C:\\Users\\Teacher\\Documents\\run.log "
            "mac=/Users/coach/Desktop/errors.txt "
            "linux=/home/operator/tmp/trace.log"
        )
        redacted = redact_paths(raw)
        self.assertNotIn("Teacher", redacted)
        self.assertNotIn("coach", redacted)
        self.assertNotIn("operator", redacted)
        self.assertGreaterEqual(redacted.count("[path]"), 3)

    def test_extract_diagnostic_filename_parses_marker_value(self):
        raw = "Transcription failed. Diagnostic log: /tmp/some/deep/path/failure-report.json"
        self.assertEqual(extract_diagnostic_filename(raw), "failure-report.json")

    def test_sanitize_transcription_error_reason_keeps_filename_and_clips(self):
        raw = "Error. Diagnostic log: C:\\Users\\dev\\Desktop\\folder\\failure-report.json " + ("x" * 600)
        sanitized = sanitize_transcription_error_reason(raw, max_length=120)
        self.assertIn("failure-report.json", sanitized)
        self.assertNotIn("C:\\Users\\dev", sanitized)
        self.assertLessEqual(len(sanitized), 120)

    def test_clip_diagnostic_text_normalizes_whitespace_and_limits_length(self):
        clipped = clip_diagnostic_text("line1\nline2\tline3", max_length=9)
        self.assertEqual(clipped, "line1 lin")

    def test_format_transcription_health_summary_returns_joined_details_and_hint(self):
        errors = {
            2: "Missing file /home/user/out/q2.wav",
            1: "Transcription failed. Diagnostic log: /tmp/logs/q1-failure.json",
        }
        joined, detail_block, log_hint = format_transcription_health_summary(
            transcription_errors=errors,
            question_labeler=lambda idx: f"Q{idx + 1}",
            log_path=Path("/Users/operator/logs/app.log"),
        )
        self.assertEqual(joined, "Q2, Q3")
        self.assertIn("diagnostic file: q1-failure.json", detail_block)
        self.assertNotIn("/tmp/logs", detail_block)
        self.assertIn("[path]", log_hint)


if __name__ == "__main__":
    unittest.main()
