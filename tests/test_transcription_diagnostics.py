import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interview_audio_recorder import (
    RecordingSession,
    Segment,
    _resolve_openvino_model_path,
    _segments_from_whisper_cpp_json,
    transcribe_existing_recordings,
)
from interview_runtime import (
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

    def test_transcribe_existing_recordings_uses_openvino_backend_when_requested(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mic = root / "mic.wav"
            mic.write_bytes(b"mic-bytes")

            class _OpenVinoBackend:
                def transcribe_segments(self, **kwargs):
                    self.kwargs = kwargs
                    return [Segment(start=1.0, end=2.0, speaker=kwargs["speaker"], text="OpenVINO text")]

            backend = _OpenVinoBackend()
            with patch("interview_audio_recorder._get_or_create_backend", return_value=backend):
                result = transcribe_existing_recordings(
                    output_dir=root,
                    base_name="sample",
                    mic_wav=mic,
                    sys_wav=None,
                    whisper_backend="openvino_genai",
                    whisper_device="GPU",
                    whisper_model="OpenVINO/whisper-small-int8-ov",
                )

            self.assertEqual(result.segment_count, 1)
            self.assertIn("OpenVINO text", result.transcript_txt.read_text(encoding="utf-8"))
            self.assertEqual(backend.kwargs["language"], "en")

    def test_openvino_model_resolution_rejects_unapproved_repo_ids(self):
        with self.assertRaises(RuntimeError):
            _resolve_openvino_model_path("some-user/unknown-whisper")

    def test_whisper_cpp_json_segments_are_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "segments.json"
            path.write_text(
                json.dumps(
                    {
                        "transcription": [
                            {
                                "timestamps": {"from": "00:00:01.000", "to": "00:00:02.500"},
                                "text": " hello ",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            segments = _segments_from_whisper_cpp_json(
                json_path=path,
                speaker="CANDIDATE",
                offset_sec=10.0,
                min_start_sec=0.0,
            )
        self.assertEqual(segments, [Segment(start=11.0, end=12.5, speaker="CANDIDATE", text="hello")])

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
