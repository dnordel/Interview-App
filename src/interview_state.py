from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from candidate_profile import CandidateQualification


@dataclass
class InterviewState:
    candidate_name: str = ""
    interview_date: str = ""
    school: str = ""
    track: str = ""
    qualification: CandidateQualification = field(default_factory=CandidateQualification)
    current_index: int = 0  # 1-based index into the mixed flow

    trait_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Optional: timestamps (seconds since recording start) for each flow item when shown.
    # Used to slice the candidate transcript into per-question answers.
    # Each mark can include an end_t when the interviewer clicks Next/Continue/Back/Skip.
    flow_time_marks: list[dict[str, Any]] = field(default_factory=list)
    # Incremental candidate transcripts keyed by flow index, captured when interviewer advances.
    flow_candidate_transcripts: dict[int, str] = field(default_factory=dict)
    # Per-question audio/transcription metadata keyed by flow index.
    flow_recordings: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Director referral packet document paths selected by interviewer.
    referral_packet: dict[str, str] = field(
        default_factory=lambda: {
            "resume_path": "",
            "interview_notes_path": "",
            "transcript_path": "",
        }
    )
    # Session communication events written during finalize/send workflows.
    communication_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": {
                "name": self.candidate_name,
                "interview_date": self.interview_date,
                "school": self.school,
                "track": self.track,
                "qualification": self.qualification.to_dict(),
            },
            "current_index": self.current_index,
            "trait_inputs": self.trait_inputs,
            "custom_inputs": self.custom_inputs,
            "flow_time_marks": self.flow_time_marks,
            "flow_candidate_transcripts": self.flow_candidate_transcripts,
            "flow_recordings": self.flow_recordings,
            "referral_packet": self.referral_packet,
            "communication_log": self.communication_log,
        }
