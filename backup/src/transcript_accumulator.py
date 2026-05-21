from __future__ import annotations

from typing import Any


def append_candidate_segment_text(
    existing_text: str,
    segments: list[Any],
    *,
    candidate_label: str,
) -> str:
    """Append candidate-only segment text to an existing transcript blob."""
    chunks: list[str] = []
    for seg in segments:
        speaker = str(getattr(seg, "speaker", "") or (seg.get("speaker") if isinstance(seg, dict) else ""))
        if speaker != candidate_label:
            continue

        text = str(getattr(seg, "text", "") or (seg.get("text") if isinstance(seg, dict) else "")).strip()
        if text:
            chunks.append(text)

    if not chunks:
        return (existing_text or "").strip()

    prefix = (existing_text or "").strip()
    addition = " ".join(chunks).strip()
    if not prefix:
        return addition
    return f"{prefix} {addition}".strip()

