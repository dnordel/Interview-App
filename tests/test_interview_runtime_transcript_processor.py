from __future__ import annotations

from pathlib import Path

from interview_runtime import (
    build_flow_time_windows,
    extract_candidate_text_from_jsonl,
    load_candidate_segments,
    map_segments_to_flow_indices,
    write_merged_timestamped_transcript,
)


def test_load_candidate_segments_skips_malformed_jsonl(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "segments.jsonl"
    jsonl_path.write_text(
        '{"speaker":"CANDIDATE","start":0.0,"text":"hello"}\n'
        'not-json\n'
        '{"speaker":"CANDIDATE","start":1.0,"text":"world"}\n',
        encoding="utf-8",
    )

    segments = load_candidate_segments(jsonl_path, "candidate")

    assert segments == [{"start": 0.0, "text": "hello"}, {"start": 1.0, "text": "world"}]


def test_extract_candidate_text_from_jsonl_filters_speakers(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "segments.jsonl"
    jsonl_path.write_text(
        '{"speaker":"INTERVIEWER","start":0.0,"text":"prompt"}\n'
        '{"speaker":"candidate","start":1.0,"text":"answer one"}\n'
        '{"speaker":"CANDIDATE","start":2.0,"text":"answer two"}\n',
        encoding="utf-8",
    )

    merged = extract_candidate_text_from_jsonl(jsonl_path, "CANDIDATE")

    assert merged == "answer one answer two"


def test_extract_candidate_text_from_jsonl_empty_input_returns_empty(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "empty.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    assert extract_candidate_text_from_jsonl(jsonl_path, "CANDIDATE") == ""


def test_build_flow_time_windows_prefers_explicit_end_for_overlap() -> None:
    marks = [
        {"flow_index": 0, "t": 0.0, "end_t": 5.0},
        {"flow_index": 1, "t": 4.0, "end_t": 8.0},
    ]

    windows = build_flow_time_windows(marks)

    assert windows == [(0, 0.0, 5.0), (1, 4.0, 8.0)]


def test_map_segments_to_flow_indices_uses_first_matching_window() -> None:
    windows = [(0, 0.0, 5.0), (1, 4.0, 8.0)]
    segments = [
        {"start": 4.5, "text": "overlap"},
        {"start": 6.0, "text": "second"},
    ]

    mapped = map_segments_to_flow_indices(segments, windows)

    assert mapped == {0: "overlap", 1: "second"}


def test_write_merged_timestamped_transcript_formats_output(tmp_path: Path) -> None:
    output_path = tmp_path / "merged.txt"
    segments = [
        {"start": 2.4, "speaker": "CANDIDATE", "text": "answer"},
        {"start": 0.4, "speaker": "INTERVIEWER", "text": "prompt"},
    ]

    path = write_merged_timestamped_transcript(output_path, segments)
    content = path.read_text(encoding="utf-8")

    assert "TIMESTAMPED INTERLEAVED TRANSCRIPT" in content
    assert "[00:00:00] INTERVIEWER: prompt" in content
    assert "[00:00:02] CANDIDATE: answer" in content
