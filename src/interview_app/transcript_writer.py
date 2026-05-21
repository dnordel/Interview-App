from __future__ import annotations

from typing import Any


class TranscriptWriterController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def append_live_segment(self, flow_idx: int, segment_text: str) -> None:
        self.app._append_live_transcript_for_flow(flow_idx, segment_text)

    def rewrite_from_flow(self) -> None:
        flow_tx = self.app._build_flow_transcript()
        self.app._rewrite_live_transcript_docx_from_flow(flow_tx)
