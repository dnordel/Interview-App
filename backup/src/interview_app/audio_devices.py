from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Sequence

logger = logging.getLogger(__name__)

_WINDOWS_AUDIO_DEVICE_ALIASES = (
    "VB-Audio Virtual Cable (CABLE Input)",
    "CABLE Output (VB-Audio Virtual Cable)",
    "CABLE Input (VB-Audio Virtual Cable)",
)


def _extract_dshow_audio_device_names(stderr_text: str) -> list[str]:
    names: list[str] = []
    marker = '"'
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        if "DirectShow audio devices" in line:
            continue
        if "Alternative name" in line:
            continue
        if marker not in line:
            continue
        start = line.find(marker)
        end = line.rfind(marker)
        if start < 0 or end <= start:
            continue
        candidate = line[start + 1 : end].strip()
        if candidate:
            names.append(candidate)
    return names


def list_windows_dshow_audio_devices(ffmpeg_exe: str | None = None) -> list[str]:
    ffmpeg = ffmpeg_exe or shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        logger.warning("windows_audio_device_probe_failed")
        return []

    stderr_text = completed.stderr or ""
    devices = _extract_dshow_audio_device_names(stderr_text)
    return devices


def resolve_preferred_windows_audio_device(
    *,
    preferred_name: str,
    aliases: Sequence[str] | None = None,
    available_devices: Sequence[str] | None = None,
) -> str:
    candidates = [preferred_name] + [item for item in (aliases or ()) if item]
    devices = list(available_devices) if available_devices is not None else list_windows_dshow_audio_devices()
    if not devices:
        return preferred_name

    by_folded = {name.casefold(): name for name in devices}
    for candidate in candidates:
        resolved = by_folded.get(candidate.casefold())
        if resolved:
            return resolved

    folded_devices = [(name.casefold(), name) for name in devices]
    for candidate in candidates:
        token = candidate.casefold()
        for folded, original in folded_devices:
            if token in folded or folded in token:
                return original

    return preferred_name


def resolve_default_windows_system_device() -> str:
    preferred = _WINDOWS_AUDIO_DEVICE_ALIASES[0]
    resolved = resolve_preferred_windows_audio_device(
        preferred_name=preferred,
        aliases=_WINDOWS_AUDIO_DEVICE_ALIASES[1:],
    )
    if resolved != preferred:
        logger.info("windows_system_device_fallback_selected", extra={"resolved_device": resolved})
    return resolved
