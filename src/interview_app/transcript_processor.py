from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_label(value: Any) -> str:
    return str(value or "").strip().upper()


def extract_candidate_text_from_jsonl(jsonl_path: Path, candidate_label: str) -> str:
    segments = load_candidate_segments(jsonl_path, candidate_label)
    parts = [segment["text"] for segment in segments]
    return " ".join(parts).strip()


def load_candidate_segments(jsonl_path: Path, candidate_label: str) -> list[dict[str, Any]]:
    normalized_label = normalize_label(candidate_label)
    results: list[dict[str, Any]] = []
    for segment in _iter_jsonl_dict_segments(jsonl_path):
        if normalize_label(segment.get("speaker")) != normalized_label:
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start_seconds = _safe_float(segment.get("start"), default=0.0)
        results.append({"start": start_seconds, "text": text})
    return results


def load_jsonl_segments_for_merge(jsonl_path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl_dict_segments(jsonl_path))


def build_flow_time_windows(flow_time_marks: list[dict[str, Any]]) -> list[tuple[int, float, float]]:
    ordered_marks = sorted(flow_time_marks or [], key=lambda mark: _safe_float(mark.get("t"), default=0.0))
    windows: list[tuple[int, float, float]] = []
    for index, mark in enumerate(ordered_marks):
        start_seconds = _safe_float(mark.get("t"), default=0.0)
        end_seconds = _resolve_end_seconds(mark, ordered_marks, index, start_seconds)
        flow_index = int(_safe_float(mark.get("flow_index"), default=index))
        windows.append((flow_index, start_seconds, end_seconds))
    return windows


def map_segments_to_flow_indices(
    segments: list[dict[str, Any]],
    windows: list[tuple[int, float, float]],
) -> dict[int, str]:
    by_flow_index: dict[int, str] = {}
    for segment in segments:
        start_seconds = _safe_float(segment.get("start"), default=0.0)
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        flow_index = _match_window_index(start_seconds, windows)
        if flow_index is None:
            continue
        previous = by_flow_index.get(flow_index, "")
        by_flow_index[flow_index] = f"{previous} {text}".strip()
    return by_flow_index


def write_merged_timestamped_transcript(
    transcript_path: Path,
    segments: list[dict[str, Any]],
) -> Path:
    ordered_segments = sorted(
        segments,
        key=lambda segment: (
            _safe_float(segment.get("start"), default=0.0),
            _safe_float(segment.get("end"), default=_safe_float(segment.get("start"), default=0.0)),
        ),
    )
    with transcript_path.open("w", encoding="utf-8") as handle:
        handle.write("TIMESTAMPED INTERLEAVED TRANSCRIPT\n\n")
        for segment in ordered_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            timestamp = format_seconds_for_transcript(segment.get("start"))
            speaker = str(segment.get("speaker") or "UNKNOWN").strip() or "UNKNOWN"
            handle.write(f"[{timestamp}] {speaker}: {text}\n")
    return transcript_path


def format_seconds_for_transcript(seconds: Any) -> str:
    value = _safe_float(seconds, default=0.0)
    whole_seconds = int(max(0.0, value) + 0.5)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    remaining_seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def _iter_jsonl_dict_segments(jsonl_path: Path) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    try:
        raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    parsed_segments: list[dict[str, Any]] = []
    for line in raw_lines:
        payload = _parse_segment(line)
        if payload is None:
            continue
        parsed_segments.append(payload)
    return parsed_segments


def _resolve_end_seconds(
    mark: dict[str, Any],
    ordered_marks: list[dict[str, Any]],
    index: int,
    start_seconds: float,
) -> float:
    explicit_end = mark.get("end_t")
    if explicit_end is not None:
        return _safe_float(explicit_end, default=start_seconds)
    if index + 1 >= len(ordered_marks):
        return 1e12
    return _safe_float(ordered_marks[index + 1].get("t"), default=1e12)


def _parse_segment(line: str) -> dict[str, Any] | None:
    cleaned = line.strip()
    if not cleaned:
        return None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _match_window_index(start_seconds: float, windows: list[tuple[int, float, float]]) -> int | None:
    for flow_index, window_start, window_end in windows:
        if window_start <= start_seconds < window_end:
            return flow_index
    return None
