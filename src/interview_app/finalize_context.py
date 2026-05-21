from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import FinalizeTranscriptMetadata


@dataclass(slots=True)
class FinalizeContext:
    payload: dict[str, Any]
    scoring: dict[str, Any]
    flow_transcript: list[dict[str, Any]]
    recording_metadata: list[dict[str, Any]]
    transcript_path: str
    transcript_metadata: FinalizeTranscriptMetadata
    transcript_complete: bool
    remaining_question_indices: list[int]


def build_finalize_context(
    app: Any,
    scoring: dict[str, Any],
    warnings: list[str],
    transcript_metadata: FinalizeTranscriptMetadata,
) -> FinalizeContext:
    payload = app.state.to_dict()
    recording_metadata = app._serialize_flow_audio_recordings()
    payload["flow_recordings"] = app.state.flow_recordings
    payload["audio_recording"] = recording_metadata
    if not app.state.flow_recordings:
        warnings.append("Recording/transcription did not complete. Interview was finalized without transcript text.")

    payload["custom_answers"] = app._ordered_custom_answers()
    flow_tx = app._build_flow_transcript()
    app._apply_candidate_transcripts_to_flow(flow_tx)
    app._rewrite_live_transcript_docx_from_flow(flow_tx)
    payload["flow_transcript"] = flow_tx
    payload["transcript_metadata"] = transcript_metadata
    payload["transcript_complete"] = transcript_metadata["transcript_complete"]
    payload["remaining_question_indices"] = transcript_metadata["remaining_question_indices"]

    transcript_path = Path(str(app._safe_attr("live_transcript_docx") or "").strip()).as_posix().strip()
    app.state.referral_packet["transcript_path"] = transcript_path

    return FinalizeContext(
        payload=payload,
        scoring=scoring,
        flow_transcript=flow_tx,
        recording_metadata=recording_metadata,
        transcript_path=transcript_path,
        transcript_metadata=transcript_metadata,
        transcript_complete=transcript_metadata["transcript_complete"],
        remaining_question_indices=transcript_metadata["remaining_question_indices"],
    )
