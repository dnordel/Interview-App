from pathlib import Path

from interview_runtime import AudioRuntimeController, RuntimeConfig


class _AppStub:
    def _current_whisper_transcription_settings(self):
        return {"beam_size": 1}


def test_start_recording_session_uses_resolved_windows_system_device(tmp_path: Path):
    captured = {}

    def _start_recording(**kwargs):
        captured.update(kwargs)
        return object()

    controller = AudioRuntimeController(
        app=_AppStub(),
        shared_state=object(),
        microphone_resolver=lambda: "Microphone (Realtek USB Audio)",
        system_resolver=lambda: "VB-Audio Virtual Cable (CABLE Input)",
    )
    runtime_config = RuntimeConfig(model="small", device="cpu", compute_type="int8")

    controller.start_recording_session(
        _start_recording,
        base_dir=tmp_path,
        base_name="case",
        runtime_config=runtime_config,
    )

    assert captured["win_sys_device"] == "VB-Audio Virtual Cable (CABLE Input)"
    assert captured["win_mic_device"] == "Microphone (Realtek USB Audio)"
