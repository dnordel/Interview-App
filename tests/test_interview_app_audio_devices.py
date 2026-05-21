from interview_app.audio_devices import (
    _extract_dshow_audio_device_names,
    resolve_default_windows_system_device,
    resolve_preferred_windows_audio_device,
)


def test_extract_dshow_audio_device_names_parses_quoted_entries():
    stderr_text = """
[dshow @ 0000] DirectShow audio devices
[dshow @ 0000]   \"VB-Audio Virtual Cable (CABLE Input)\"
[dshow @ 0000]   \"Microphone (USB Audio Device)\"
[dshow @ 0000]     Alternative name \"@device_cm_{GUID}\\wave_{ID}\"
"""
    names = _extract_dshow_audio_device_names(stderr_text)
    assert names == [
        "VB-Audio Virtual Cable (CABLE Input)",
        "Microphone (USB Audio Device)",
    ]


def test_resolve_preferred_windows_audio_device_uses_alias_match():
    resolved = resolve_preferred_windows_audio_device(
        preferred_name="VB-Audio Virtual Cable (CABLE Input)",
        aliases=("CABLE Output (VB-Audio Virtual Cable)",),
        available_devices=["CABLE Output (VB-Audio Virtual Cable)"],
    )
    assert resolved == "CABLE Output (VB-Audio Virtual Cable)"


def test_resolve_default_windows_system_device_falls_back_to_preferred_when_probe_empty(monkeypatch):
    monkeypatch.setattr("interview_app.audio_devices.list_windows_dshow_audio_devices", lambda: [])
    assert resolve_default_windows_system_device() == "VB-Audio Virtual Cable (CABLE Input)"
