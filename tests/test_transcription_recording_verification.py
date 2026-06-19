from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import wave

import pytest

sys.path.append(str(Path(__file__).resolve().parent))

from transcription_verification_utils import wav_header_duration_seconds


FIXTURE_DIR = Path(__file__).resolve().parent / "Test Question Recordings for Transcription Verificaiton"
MAX_SEGMENT_DURATION_SECONDS = 5 * 60

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
InterviewApp = interview_app.InterviewApp


def _segment_wav_files() -> list[Path]:
    return sorted(path for path in FIXTURE_DIR.glob("*.wav") if not path.name.endswith("_full.wav"))


def test_fixture_segments_do_not_exceed_five_minutes_each() -> None:
    wav_files = _segment_wav_files()
    assert wav_files, f"No .wav fixture files found in {FIXTURE_DIR}"

    placeholder_wav_files = [wav_file for wav_file in wav_files if wav_file.stat().st_size == 0]
    verifiable_wav_files = [wav_file for wav_file in wav_files if wav_file.stat().st_size > 0]

    assert verifiable_wav_files, "No non-empty .wav fixtures were found for duration checks"

    for wav_file in verifiable_wav_files:
        try:
            duration = wav_header_duration_seconds(wav_file)
        except (wave.Error, EOFError, OSError, ValueError) as exc:
            message = f"{wav_file.name}: unreadable/corrupt WAV header ({type(exc).__name__}: {exc})"
            pytest.fail(message)

        assert duration <= MAX_SEGMENT_DURATION_SECONDS, (
            f"{wav_file.name}: computed duration {duration:.2f}s exceeds "
            f"{MAX_SEGMENT_DURATION_SECONDS}s"
        )

    assert all(path.stat().st_size == 0 for path in placeholder_wav_files)


def test_recording_fixture_filenames_are_parseable_and_unique() -> None:
    wav_files = _segment_wav_files()
    assert wav_files, f"No .wav fixture files found in {FIXTURE_DIR}"

    question_numbers: list[int] = []
    unparsable: list[str] = []

    for wav_file in wav_files:
        match = re.search(r"_Q(?P<question_number>\d+)_", wav_file.name)
        if match is None:
            unparsable.append(wav_file.name)
            continue

        question_numbers.append(int(match.group("question_number")))

    assert not unparsable, "Could not parse question numbers from: " + ", ".join(unparsable)

    unique_question_numbers = set(question_numbers)
    assert len(question_numbers) == len(unique_question_numbers), (
        "Duplicate parsed question numbers found: "
        f"{sorted(question_numbers)}"
    )

    expected_question_numbers = set(range(2, 16))
    missing_questions = sorted(expected_question_numbers - unique_question_numbers)
    unexpected_questions = sorted(unique_question_numbers - expected_question_numbers)

    assert question_numbers, "No question numbers were parsed from fixture filenames"
    assert not missing_questions and not unexpected_questions, (
        "Fixture question coverage mismatch. "
        f"Missing indices: {missing_questions or 'none'}. "
        f"Unexpected indices: {unexpected_questions or 'none'}."
    )
    assert 1 not in question_numbers, "Fixture set should not include a Q1 recording segment"


def _build_interview_app_for_flow(tmp_path: Path) -> InterviewApp:
    app = InterviewApp.__new__(InterviewApp)
    app.live_transcript_docx = None
    app.transcript_available = True
    app.transcript_warning = ""
    app.active_traits = [{"id": "trait-1", "name": "Classroom Culture", "primary_question": "How do you build trust with children?"}]
    app.custom_questions = []
    app.state = SimpleNamespace(
        candidate_name="Jordan Rivera",
        interview_date="2026-03-01",
        school="Little Oak Preschool",
        flow_recordings={},
        flow_candidate_transcripts={},
    )
    return app


def test_flow_recording_payload_maps_candidate_transcripts_to_matching_question(tmp_path: Path) -> None:
    app = _build_interview_app_for_flow(tmp_path)
    jsonl_path = tmp_path / "q2_segments.jsonl"
    entries = [
        {"speaker": "INTERVIEWER", "text": "Question prompt"},
        {"speaker": "CANDIDATE", "text": "I partner with families through weekly updates."},
        {"speaker": "CANDIDATE", "text": "I document each child's progress."},
    ]
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for item in entries:
            fh.write(json.dumps(item) + "\n")

    app.state.flow_recordings = {
        0: {
            "flow_index": 0,
            "base_name": "Candidate_Jordan_Q1",
            "transcript_jsonl": str(tmp_path / "missing_q1.jsonl"),
            "candidate_label": "CANDIDATE",
            "candidate_transcript": "I use visual routines and calm transitions.",
        },
        1: {
            "flow_index": 1,
            "base_name": "Candidate_Jordan_Q2",
            "transcript_jsonl": str(jsonl_path),
            "candidate_label": "CANDIDATE",
            "candidate_transcript": "",
        },
    }
    flow_transcript = [
        {"type": "trait", "id": "trait-1", "title": "Classroom Culture", "question": "How do you build trust with children?"},
        {"type": "custom", "id": "custom-1", "question": "How do you collaborate with families?"},
    ]

    app._apply_candidate_transcripts_to_flow(flow_transcript)
    app._rewrite_live_transcript_docx_from_flow(flow_transcript)

    assert flow_transcript[0]["candidate_transcript"] == "I use visual routines and calm transitions."
    assert flow_transcript[1]["candidate_transcript"] == (
        "I partner with families through weekly updates. I document each child's progress."
    )
    assert app.live_transcript_docx is None
