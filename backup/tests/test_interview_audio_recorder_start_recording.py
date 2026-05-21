from pathlib import Path
from unittest.mock import patch

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
    )

    assert isinstance(session, _SessionSpy)
    mic_cmd, sys_cmd = session.started_with
    assert mic_cmd is None
    assert sys_cmd is not None
    assert "audio=CABLE Output (VB-Audio Virtual Cable)" in sys_cmd
    assert mock_session_cls.called


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
