import unittest
from pathlib import Path
from unittest.mock import patch

from interview_audio_recorder import RecordingSession


class _ProcStub:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return None


class TestRecordingSessionProcessCleanup(unittest.TestCase):
    def _session(self) -> RecordingSession:
        return RecordingSession(
            os_name="linux",
            mic_wav=Path("/tmp/mic.wav"),
            sys_wav=Path("/tmp/sys.wav"),
            mic_label="MIC",
            sys_label="SYS",
            mic_offset=0.0,
            sys_offset=0.0,
            whisper_model="small",
            whisper_device="cpu",
            whisper_compute_type="int8",
        )

    def test_linux_start_uses_parent_death_preexec(self):
        session = self._session()
        popen_kwargs = session._popen_kwargs()
        self.assertIn("preexec_fn", popen_kwargs)
        self.assertTrue(callable(popen_kwargs["preexec_fn"]))

    def test_start_cleans_up_started_processes_on_partial_failure(self):
        session = self._session()
        first_proc = _ProcStub()

        with patch("subprocess.Popen", side_effect=[first_proc, RuntimeError("boom")]):
            with self.assertRaises(RuntimeError):
                session.start(["ffmpeg", "mic"], ["ffmpeg", "sys"])

        self.assertTrue(first_proc.terminated)
        self.assertFalse(session.is_running)


if __name__ == "__main__":
    unittest.main()
