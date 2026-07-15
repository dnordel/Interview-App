from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class AudioPreflightResult:
    microphone_ready: bool
    system_audio_ready: bool
    transcription_ready: bool
    warning: str

    @property
    def ready(self) -> bool:
        return self.microphone_ready and self.system_audio_ready and self.transcription_ready


def recent_wav_signal_level(wav_path: Path, *, tail_seconds: float = 0.5) -> float:
    try:
        with wave.open(str(Path(wav_path)), "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            channels = max(1, wav_file.getnchannels())
            frame_count = min(
                wav_file.getnframes(),
                max(1, int(wav_file.getframerate() * max(0.05, float(tail_seconds)))),
            )
            wav_file.setpos(max(0, wav_file.getnframes() - frame_count))
            raw = wav_file.readframes(frame_count)
    except (OSError, ValueError, wave.Error):
        return 0.0
    if sample_width != 2 or not raw:
        return 1.0 if raw else 0.0
    sample_count = len(raw) // 2
    if sample_count <= 0:
        return 0.0
    total = sum(
        abs(int.from_bytes(raw[index : index + 2], byteorder="little", signed=True))
        for index in range(0, len(raw) - 1, 2 * channels)
    )
    channel_samples = max(1, sample_count // channels)
    return min(1.0, (total / channel_samples) / 32768.0)


def evaluate_audio_preflight(
    *,
    microphone_wav: Path,
    system_audio_wav: Path,
    transcript_segments: Sequence[Any],
    candidate_label: str,
    min_average_abs: float = 8.0,
) -> AudioPreflightResult:
    microphone_ready = _wav_has_detectable_signal(microphone_wav, min_average_abs=min_average_abs)
    system_audio_ready = _wav_has_detectable_signal(system_audio_wav, min_average_abs=min_average_abs)
    normalized_candidate = str(candidate_label or "").strip().casefold()
    transcription_ready = any(
        str(getattr(segment, "speaker", "") or "").strip().casefold() == normalized_candidate
        and bool(str(getattr(segment, "text", "") or "").strip())
        for segment in transcript_segments
    )
    failed: list[str] = []
    if not microphone_ready:
        failed.append("microphone audio")
    if not system_audio_ready:
        failed.append("candidate/system audio")
    if not transcription_ready:
        failed.append("candidate transcription")
    warning = ""
    if failed:
        warning = (
            f"Audio check did not verify {', '.join(failed)}. Check audio settings and confirm "
            "Zoom/Windows output is routed to VB-CABLE. Record the interview in Zoom as a backup."
        )
    return AudioPreflightResult(
        microphone_ready=microphone_ready,
        system_audio_ready=system_audio_ready,
        transcription_ready=transcription_ready,
        warning=warning,
    )


def _wav_has_detectable_signal(wav_path: Path, *, min_average_abs: float) -> bool:
    try:
        with wave.open(str(Path(wav_path)), "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            frame_count = min(wav_file.getnframes(), max(wav_file.getframerate(), 8_000))
            raw = wav_file.readframes(frame_count)
    except (OSError, wave.Error):
        return False
    if sample_width != 2 or not raw:
        return bool(raw)
    sample_count = len(raw) // 2
    if sample_count <= 0:
        return False
    total = sum(
        abs(int.from_bytes(raw[index : index + 2], byteorder="little", signed=True))
        for index in range(0, len(raw) - 1, 2)
    )
    return (total / sample_count) >= min_average_abs
