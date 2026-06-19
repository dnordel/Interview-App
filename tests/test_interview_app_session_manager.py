from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from interview_app.session_manager import (
    InterviewSessionManager,
    ResumeInstruction,
    SessionPayloadValidationError,
)
from interview_runtime import InterviewSessionStore, InterviewState
from scoring_reporting import DraftManager


class _FrozenToday:
    def __call__(self) -> date:
        return date(2026, 3, 11)


def _build_manager(tmp_path: Path) -> InterviewSessionManager:
    draft_manager = DraftManager(tmp_path)
    session_store = InterviewSessionStore(tmp_path)
    return InterviewSessionManager(
        draft_manager=draft_manager,
        session_store=session_store,
        today_provider=_FrozenToday(),
    )


def test_resume_instruction_normal_flow_target(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    state = InterviewState(track="preschool", current_index=2)

    instruction = manager.build_resume_instruction(state, flow_length=5)

    assert instruction == ResumeInstruction(target="flow_screen", flow_index=1)


def test_hydrate_state_supports_empty_partial_payload_defaults(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    payload = {
        "candidate": {"name": "Jane Doe"},
        "flow_candidate_transcripts": {"1": "  hello  "},
        "flow_recordings": {"1": {"base_name": "q1"}},
    }

    state = manager.hydrate_state(payload)

    assert state.candidate_name == "Jane Doe"
    assert state.interview_date == "2026-03-11"
    assert state.flow_candidate_transcripts == {1: "hello"}
    assert len(state.flow_recordings[1]["attempts"]) == 1


def test_load_session_payload_normalizes_stale_schema_shapes(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    store = InterviewSessionStore(tmp_path)
    path = store.session_path("id-1", "Jane", "2026-03-01")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "interview": {
                    "interview_id": "id-1",
                    "candidate_name": "Jane",
                    "interview_date": "2026-03-01",
                },
                "questions": {
                    "abc": {"flow_idx": "2", "item_type": "trait", "item_id": "tid", "notes": "bad"},
                    "4": {"item_type": "custom", "item_id": "cid", "notes": {"q": "n"}},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = manager.load_session_payload(interview_id="id-1", candidate_name="Jane", interview_date="2026-03-01")
    state = InterviewState()
    manager.hydrate_state_from_session_payload(state, payload)

    assert "2" in payload["questions"]
    assert payload["questions"]["2"]["notes"] == {}
    assert state.trait_inputs["tid"] == {}
    assert state.custom_inputs["cid"] == {"q": "n"}


def test_hydrate_state_rejects_invalid_payload(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)

    with pytest.raises(SessionPayloadValidationError):
        manager.hydrate_state({"candidate": {"name": ""}})
