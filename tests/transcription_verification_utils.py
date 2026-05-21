"""Utilities for validating transcription recording fixtures."""

from __future__ import annotations

from pathlib import Path
import wave

_MIN_PCM_WAV_HEADER_BYTES = 44


def _max_frames_from_file_size(file_size: int, bytes_per_frame: int) -> int:
    if file_size <= _MIN_PCM_WAV_HEADER_BYTES:
        return 0

    payload_bytes = file_size - _MIN_PCM_WAV_HEADER_BYTES
    return payload_bytes // bytes_per_frame


def wav_header_duration_seconds(file_path: Path) -> float:
    """Return WAV duration in seconds using robust header-derived metadata.

    Uses header values when they are consistent with file size, and falls back to
    a file-size-constrained frame count for placeholder fixtures.
    """

    with wave.open(str(file_path), "rb") as wav_file:
        nframes = wav_file.getnframes()
        framerate = wav_file.getframerate()
        nchannels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()

    if framerate <= 0:
        raise ValueError(f"invalid framerate in WAV header: {framerate}")

    bytes_per_frame = nchannels * sample_width
    if bytes_per_frame <= 0:
        raise ValueError(
            "invalid channel/sample width combination in WAV header: "
            f"channels={nchannels}, sample_width={sample_width}"
        )

    max_frames = _max_frames_from_file_size(file_path.stat().st_size, bytes_per_frame)
    safe_nframes = min(nframes, max_frames) if max_frames > 0 else nframes
    return safe_nframes / framerate
