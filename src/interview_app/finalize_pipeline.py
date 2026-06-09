from __future__ import annotations

import queue
import traceback
from pathlib import Path
from typing import Any
from tkinter import messagebox
from uuid import uuid4

from app_content import is_valid_date_yyyy_mm_dd
from interview_scoring import score_interview
from reporting import ReportingValidationError
from ui_feedback import TRANSCRIPTION_PARTIAL_WARNING_COPY
from ux_metrics import EVENT_INTERVIEW_FINALIZED

from .finalize_context import build_finalize_context
from .finalize_gateways import FinalizeGateways
from .types import FinalizePipelineResult, FinalizeTranscriptMetadata


PENDING_TRANSCRIPTION_WARNING = TRANSCRIPTION_PARTIAL_WARNING_COPY


class FinalizePipelineController:
    def __init__(self, app: Any, shared_state: Any, gateways: FinalizeGateways | None = None) -> None:
        self.app = app
        self.shared_state = shared_state
        self.gateways = gateways or FinalizeGateways()

    def finalize_interview(self) -> None:
        try:
            self._dispatch_finalize_work()
        except ReportingValidationError as exc:
            messagebox.showerror("Finalize Error", str(exc))
        except Exception as exc:
            messagebox.showerror("Finalize Error", f"{exc}\n\n{traceback.format_exc()}")

    def _dispatch_finalize_work(self) -> None:
        self.app.validate_before_finalize()
        self._warn_if_finalize_starts_with_pending_transcriptions()
        self.app.current_finalize_correlation_id = uuid4().hex
        self.app._show_finalize_progress()
        self._start_finalize_worker_non_blocking(attempt=1)
        self.app.show_start_screen()

    def _start_finalize_worker_non_blocking(self, attempt: int) -> None:
        self.app._start_finalize_worker(attempt=attempt)
        self.app._close_finalize_progress()
        self._restore_main_window_focus()

    def _warn_if_finalize_starts_with_pending_transcriptions(self) -> None:
        if not self._pending_transcription_indices():
            return
        self.app._show_finalize_partial_transcript_warning(PENDING_TRANSCRIPTION_WARNING)

    def _restore_main_window_focus(self) -> None:
        root = self.app.winfo_toplevel() if hasattr(self.app, "winfo_toplevel") else self.app
        if hasattr(root, "lift"):
            root.lift()
        if hasattr(root, "focus_force"):
            root.focus_force()

    def run_finalize_pipeline(self) -> FinalizePipelineResult:
        scoring = score_interview(self.app._rubric_with_question_overrides(), self.app.state.track, self.app.state.trait_inputs)
        warnings: list[str] = []
        pending_snapshot = self._pending_transcription_snapshot()

        recording_flow_idx = self.app._safe_attr("recording_flow_idx")
        if recording_flow_idx is not None:
            self.app._finalize_current_question_audio_and_doc(recording_flow_idx)

        warnings.extend(self.app._collect_transcription_health_warnings())
        transcript_metadata = self._build_transcript_metadata(pending_snapshot)
        if not transcript_metadata["transcript_complete"]:
            warnings.append(PENDING_TRANSCRIPTION_WARNING)
        self.app._hydrate_state_from_session_store()

        context = build_finalize_context(self.app, scoring, warnings, transcript_metadata)
        out_path = self.gateways.export_report(self.app, context)
        integration_path = self.gateways.export_integration(self.app, context)
        integration_path_str = Path(integration_path).as_posix()
        director_packet, comm_log_path = self.gateways.send_referral(self.app, context, out_path, integration_path)
        self.gateways.persist_finalize_history(self.app, context, out_path)
        return {
            "scoring": scoring,
            "out_path": out_path,
            "integration_path": integration_path_str,
            "transcript_path": context.transcript_path,
            "director_packet": director_packet,
            "warnings": warnings,
            "communication_log_path": str(comm_log_path) if comm_log_path else None,
            "transcript_complete": transcript_metadata["transcript_complete"],
            "transcript_completeness_status": transcript_metadata["transcript_completeness_status"],
            "remaining_question_indices": transcript_metadata["remaining_question_indices"],
        }

    def _build_transcript_metadata(self, pending_snapshot: dict[str, int | list[int]]) -> FinalizeTranscriptMetadata:
        pending_indices = list(pending_snapshot.get("indices", []))
        is_complete = int(pending_snapshot.get("count", 0)) == 0
        status = "complete" if is_complete else "partial"
        return {
            "transcript_complete": is_complete,
            "transcript_completeness_status": status,
            "remaining_question_indices": pending_indices,
        }

    def _pending_transcription_indices(self) -> list[int]:
        """Return pending question indices as 1-based values for operator-facing metadata."""
        pending_flow_indices = self._collect_pending_flow_indices()
        return sorted(idx + 1 for idx in pending_flow_indices)

    def _collect_pending_flow_indices(self) -> set[int]:
        """Capture pending flow indices from runtime queue/session snapshots without blocking."""
        queue_state = getattr(self.app, "_transcription_queue_state", None)
        shared_transcription = getattr(self.shared_state, "transcription", None)
        flow_indices = set(self._safe_int_indices(getattr(queue_state, "_pending_flow_transcriptions", set())))
        flow_indices.update(self._safe_int_indices(getattr(shared_transcription, "pending_flow_transcriptions", set())))
        return flow_indices

    @staticmethod
    def _safe_int_indices(values: Any) -> list[int]:
        if not values:
            return []
        normalized: list[int] = []
        for value in values:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        return normalized

    def _pending_transcription_snapshot(self) -> dict[str, int | list[int]]:
        pending_indices = self._pending_transcription_indices()
        return {"count": len(pending_indices), "indices": pending_indices}

    def poll_finalize_worker(self, q: queue.Queue[dict[str, Any]]) -> None:
        try:
            status = q.get_nowait()
        except queue.Empty:
            self.app._refresh_finalize_processing_state()
            self.app.after(150, lambda: self.poll_finalize_worker(q))
            return
        self.app._finalize_worker_running = False
        if status.get("ok"):
            self._handle_finalize_success(status)
            return
        self._handle_finalize_failure(status)

    def _handle_finalize_success(self, status: dict[str, Any]) -> None:
        result = status["result"]
        self.app.last_finalize_result = result
        self.app.metrics_logger.log_ux_completion(app="interview", surface="finalize", outcome="completed", track=self.app.state.track)
        self.app.metrics_logger.log_event(EVENT_INTERVIEW_FINALIZED, track=self.app.state.track)
        scoring = result["scoring"]
        warnings = result.get("warnings", [])
        if result.get("transcript_completeness_status") == "partial":
            self.app._show_finalize_partial_transcript_warning(PENDING_TRANSCRIPTION_WARNING)
        warning_text = "\n\nWarnings:\n- " + "\n- ".join(str(w) for w in warnings) if warnings else ""
        self.app._prompt_resume_if_outcome_requires_it(scoring)
        messagebox.showinfo("Finalized", f"Outcome: {scoring['outcome']}\nWeighted Total: {scoring['weighted_total']}/{scoring['max_weighted_total']}\nPercent: {scoring.get('percent_of_max_label', str(scoring['percent_of_max']) + '%')}\nSkipped scored questions: {scoring.get('skipped_traits_count', 0)}\n\nReport saved to:\n{result['out_path']}\n\nJSON export saved to:\n{result['integration_path']}{warning_text}")
        transcript_path = str(result.get("transcript_path") or "").strip()
        if transcript_path:
            self.app._open_path_in_default_app(transcript_path)
        self.app._delete_interview_recording_artifacts()
        self.app.current_finalize_correlation_id = ""
        self.app.show_start_screen()

    def _handle_finalize_failure(self, status: dict[str, Any]) -> None:
        err = status.get("error")
        if int(status.get("attempt", 1)) == 1:
            self._start_finalize_worker_non_blocking(attempt=2)
            return
        if isinstance(err, Exception) and self.app.recording_session is not None:
            self.app.recording_session = None
            self.app.recording_base_name = ""
        if isinstance(err, ReportingValidationError):
            self.app.current_finalize_correlation_id = ""
            messagebox.showerror("Finalize Error", str(err))
            return
        should_retry = messagebox.askretrycancel("Finalize Error", f"{err}\n\n{status.get('tb', '')}")
        if should_retry:
            self._start_finalize_worker_non_blocking(attempt=1)
            return
        self.app.current_finalize_correlation_id = ""


def validate_before_finalize(app: Any) -> None:
    if not app.state.candidate_name.strip():
        raise ValueError("Candidate Name is required.")
    if not is_valid_date_yyyy_mm_dd(app.state.interview_date.strip()):
        raise ValueError("Interview Date must be valid YYYY-MM-DD.")
    if not app.state.school.strip():
        raise ValueError("School selection is required.")
    if not app.state.track:
        raise ValueError("Track selection is required.")
    qualification = app.state.qualification
    if qualification.has_degree is None:
        raise ValueError("Please confirm whether the candidate has a degree.")
    if qualification.ece_units_completed is None and not qualification.degree_in_ece:
        raise ValueError("ECE units completed is required unless degree in ECE is checked.")
    if qualification.has_degree and not qualification.degree_type:
        raise ValueError("Degree type is required when a degree is reported.")
    if (not qualification.has_degree) and qualification.total_units_completed is None:
        raise ValueError("Total units completed is required when no degree is reported.")
    for trait in app.rubric_loader.get_traits_for_track(app.state.track):
        tid = trait["id"]
        tstate = app.state.trait_inputs.get(tid)
        if not tstate:
            raise ValueError(f"Missing state for trait: {trait['name']}")
        skipped = bool(tstate.get("skipped", False))
        dq_on = bool(tstate.get("absolute_disqualifier"))
        raw = tstate.get("raw_score")
        if skipped:
            continue
        if not dq_on and raw not in {1, 2, 3, 4, 5}:
            raise ValueError(f"Missing raw score for trait: {trait['name']}")
        if dq_on and not (tstate.get("verbatim_notes") or "").strip():
            raise ValueError(f"Trait '{trait['name']}' has disqualifier checked but no verbatim notes.")
