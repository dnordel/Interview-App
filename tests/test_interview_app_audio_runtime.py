from pathlib import Path

from interview_app.audio_runtime import AudioRuntimeController, RuntimeConfig


class _AppStub:
    def _current_whisper_transcription_settings(self):
        return {"beam_size": 1}


def test_start_recording_session_uses_resolved_windows_system_device(monkeypatch, tmp_path: Path):
    captured = {}

    def _start_recording(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "interview_app.audio_runtime.resolve_default_windows_system_device",
        lambda: "VB-Audio Virtual Cable (CABLE Input)",
    )
    monkeypatch.setattr(
        "interview_app.audio_runtime.resolve_default_windows_microphone_device",
        lambda: "Microphone (Realtek USB Audio)",
    )

    controller = AudioRuntimeController(app=_AppStub(), shared_state=object())
    runtime_config = RuntimeConfig(model="small", device="cpu", compute_type="int8")

    controller.start_recording_session(
        _start_recording,
        base_dir=tmp_path,
        base_name="case",
        runtime_config=runtime_config,
    )

    assert captured["win_sys_device"] == "VB-Audio Virtual Cable (CABLE Input)"
    assert captured["win_mic_device"] == "Microphone (Realtek USB Audio)"
