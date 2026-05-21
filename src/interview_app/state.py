from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import InterviewSessionContext, TranscriptionRuntimeState


@dataclass(slots=True)
class AppSharedState:
    session: InterviewSessionContext = field(default_factory=InterviewSessionContext)
    transcription: TranscriptionRuntimeState = field(default_factory=TranscriptionRuntimeState)
    history_rows: list[dict[str, Any]] = field(default_factory=list)
