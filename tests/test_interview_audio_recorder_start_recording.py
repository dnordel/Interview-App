from pathlib import Path
from unittest.mock import patch

import pytest

from interview_audio_recorder import start_recording


class _SessionSpy:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started_with = None

    def start(self, mic_cmd, sys_cmd):
        self.started_with = (mic_cmd, sys_cmd)


@patch("interview_audio_recorder._require_ffmpeg", return_value="ffmpeg")
@patch("interview_audio_recorder.RecordingSession", side_effect=lambda **kwargs: _SessionSpy(**kwargs))
def test_start_recording_windows_allows_system_only(mock_session_cls, _mock_require_ffmpeg, tmp_path: Path):
    session = start_recording(
        os_name="windows",
        output_dir=tmp_path,
        base_name="sample",
        win_mic_device=None,
        win_sys_device="CABLE Output (VB-Audio Virtual Cable)",
        require_local_mic_backup=False,
    )

    assert isinstance(session, _SessionSpy)
    mic_cmd, sys_cmd = session.started_with
    assert mic_cmd is None
    assert sys_cmd is not None
    assert "audio=CABLE Output (VB-Audio Virtual Cable)" in sys_cmd
    assert mock_session_cls.called


@patch("interview_audio_recorder._require_ffmpeg", return_value="ffmpeg")
@patch("interview_audio_recorder._resolve_windows_microphone_backup_device", return_value="Microphone Array (Intel Smart Sound Technology)")
@patch("interview_audio_recorder.RecordingSession", side_effect=lambda **kwargs: _SessionSpy(**kwargs))
def test_start_recording_windows_adds_local_mic_backup_when_only_system_requested(
    mock_session_cls,
    _mock_resolve_mic,
    _mock_require_ffmpeg,
    tmp_path: Path,
):
    session = start_recording(
        os_name="windows",
        output_dir=tmp_path,
        base_name="sample",
        win_mic_device=None,
        win_sys_device="CABLE Output (VB-Audio Virtual Cable)",
        require_local_mic_backup=True,
    )

    mic_cmd, sys_cmd = session.started_with
    assert "audio=Microphone Array (Intel Smart Sound Technology)" in mic_cmd
    assert "audio=CABLE Output (VB-Audio Virtual Cable)" in sys_cmd
    assert mock_session_cls.called


def test_recording_session_raises_when_requested_process_exits_immediately(monkeypatch, tmp_path: Path):
    from interview_audio_recorder import RecordingSession

    class _DeadProcess:
        def poll(self):
            return 1

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr("interview_audio_recorder.subprocess.Popen", lambda *_args, **_kwargs: _DeadProcess())
    monkeypatch.setattr("interview_audio_recorder.time.sleep", lambda _seconds: None)

    session = RecordingSession(
        os_name="windows",
        mic_wav=tmp_path / "mic.wav",
        sys_wav=tmp_path / "sys.wav",
        mic_label="INTERVIEWER",
        sys_label="CANDIDATE",
        mic_offset=0.0,
        sys_offset=0.0,
        whisper_model="small",
        whisper_device="cpu",
        whisper_compute_type="int8",
    )

    with pytest.raises(RuntimeError, match="Recording process exited immediately"):
        session.start(["ffmpeg", "mic"], ["ffmpeg", "sys"])


@patch("interview_audio_recorder._require_ffmpeg", return_value="ffmpeg")
def test_start_recording_windows_requires_at_least_one_device(_mock_require_ffmpeg, tmp_path: Path):
    try:
        start_recording(
            os_name="windows",
            output_dir=tmp_path,
            base_name="sample",
            win_mic_device=None,
            win_sys_device=None,
        )
    except ValueError as exc:
        assert "at least one audio device" in str(exc)
        return

    raise AssertionError("Expected ValueError when both windows devices are missing")
