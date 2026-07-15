from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

from interview_audio_preflight import evaluate_audio_preflight, recent_wav_signal_level


def _write_signal_wav(path: Path, *, sample: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(int(sample).to_bytes(2, byteorder="little", signed=True) * 8_000)


def test_audio_preflight_reports_both_channels_and_candidate_transcription_ready(tmp_path: Path) -> None:
    microphone = tmp_path / "microphone.wav"
    system_audio = tmp_path / "system.wav"
    _write_signal_wav(microphone, sample=100)
    _write_signal_wav(system_audio, sample=120)

    result = evaluate_audio_preflight(
        microphone_wav=microphone,
        system_audio_wav=system_audio,
        transcript_segments=[
            SimpleNamespace(speaker="INTERVIEWER", text="Welcome."),
            SimpleNamespace(speaker="CANDIDATE", text="Thank you."),
        ],
        candidate_label="CANDIDATE",
    )

    assert result.microphone_ready is True
    assert result.system_audio_ready is True
    assert result.transcription_ready is True
    assert result.warning == ""


def test_audio_preflight_ignores_interviewer_text_and_never_leaks_speech_in_warning(tmp_path: Path) -> None:
    microphone = tmp_path / "microphone.wav"
    system_audio = tmp_path / "system.wav"
    _write_signal_wav(microphone, sample=100)
    _write_signal_wav(system_audio, sample=0)
    sensitive_text = "Private interviewer-only introduction"

    result = evaluate_audio_preflight(
        microphone_wav=microphone,
        system_audio_wav=system_audio,
        transcript_segments=[SimpleNamespace(speaker="INTERVIEWER", text=sensitive_text)],
        candidate_label="CANDIDATE",
    )

    assert result.microphone_ready is True
    assert result.system_audio_ready is False
    assert result.transcription_ready is False
    assert "candidate/system audio" in result.warning
    assert "candidate transcription" in result.warning
    assert sensitive_text not in result.warning


def test_recent_wav_signal_level_reads_bounded_tail_instead_of_old_audio(tmp_path: Path) -> None:
    path = tmp_path / "candidate.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes((0).to_bytes(2, byteorder="little", signed=True) * 16_000)
        handle.writeframes((2_000).to_bytes(2, byteorder="little", signed=True) * 4_000)

    level = recent_wav_signal_level(path, tail_seconds=0.5)

    assert 0.05 < level <= 1.0
