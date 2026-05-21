from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from director_referral_service import append_communication_log, build_director_packet, send_director_packet
from integration_export import build_integration_payload, serialize_integration_payload
from reporting import DocxExporter

from .finalize_context import FinalizeContext


@dataclass(slots=True)
class FinalizeGateways:
    sent_referral_keys: set[str] = field(default_factory=set)

    def export_report(self, app: Any, context: FinalizeContext) -> str:
        finalize_correlation_id = str(getattr(app, "current_finalize_correlation_id", "") or "")
        exporter = DocxExporter(Path(app.settings["base_dir"]) / "Indeed Interview Notes")
        out_path = exporter.export(
            app._rubric_with_question_overrides(),
            context.payload,
            context.scoring,
            include_generated_summaries=False,
        )
        self._schedule_summary_retry(app, context, finalize_correlation_id)
        app.state.referral_packet["interview_notes_path"] = Path(out_path).as_posix().strip()
        return Path(out_path).as_posix()

    def _schedule_summary_retry(self, app: Any, context: FinalizeContext, finalize_correlation_id: str) -> None:
        def worker() -> None:
            exporter = DocxExporter(Path(app.settings["base_dir"]) / "Indeed Interview Notes")
            try:
                updated_path = exporter.export(
                    app._rubric_with_question_overrides(),
                    context.payload,
                    context.scoring,
                    include_generated_summaries=True,
                )
                if str(getattr(app, "current_finalize_correlation_id", "") or "") != finalize_correlation_id:
                    return
                app.state.referral_packet["interview_notes_path"] = Path(updated_path).as_posix().strip()
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def export_integration(self, app: Any, context: FinalizeContext) -> Path:
        integration_payload = build_integration_payload(context.payload, context.scoring, include_flow_slices=True)
        return serialize_integration_payload(
            Path(app.settings["base_dir"]),
            integration_payload,
            candidate_name=app.state.candidate_name,
        )

    def persist_finalize_history(self, app: Any, context: FinalizeContext, out_path: str) -> None:
        payload_candidate = context.payload.get("candidate", {})
        saved_at = datetime.utcnow().isoformat() + "Z"
        history_entry = {
            "history_id": str(uuid4()),
            "interview_date": payload_candidate.get("interview_date", ""),
            "candidate_name": payload_candidate.get("name", ""),
            "interview_score": context.scoring.get("percent_of_max", 0),
            "determination": context.scoring.get("outcome", ""),
            "school": payload_candidate.get("school", ""),
            "track": payload_candidate.get("track", ""),
            "saved_report_path": str(out_path),
            "transcript_path": context.transcript_path,
            "interview_notes_path": app.state.referral_packet.get("interview_notes_path", "") or str(out_path),
            "saved_at": saved_at,
            "offer_status": "not_generated",
            "offer_path": "",
            "offer_letter_path": "",
            "flow_recordings": context.recording_metadata,
        }
        app.history_store.append(history_entry)

    def send_referral(
        self,
        app: Any,
        context: FinalizeContext,
        out_path: str,
        integration_path: Path,
    ) -> tuple[dict[str, Any], Path | None]:
        director_packet = build_director_packet(
            payload=context.payload,
            scoring=context.scoring,
            report_path=out_path,
            integration_path=integration_path,
            referral_packet=app.state.referral_packet,
            generated_transcript_path=app._safe_attr("live_transcript_docx"),
        )
        send_enabled = bool(app.settings.get("send_director_referral_on_finalize", False))
        endpoint = str(app.settings.get("director_referral_endpoint", "")).strip()
        if not send_enabled:
            return director_packet, None

        dedupe_key = self._referral_dedupe_key(director_packet, endpoint)
        if dedupe_key in self.sent_referral_keys:
            return director_packet, None

        send_result = send_director_packet(director_packet, endpoint)
        self.sent_referral_keys.add(dedupe_key)

        log_event = {
            "event": "director_referral_sent",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "endpoint": endpoint,
            "status": send_result.get("status", "unknown"),
        }
        comm_log_path = append_communication_log(Path(app.settings["base_dir"]), log_event)
        return director_packet, comm_log_path

    def _referral_dedupe_key(self, director_packet: dict[str, Any], endpoint: str) -> str:
        packet_json = json.dumps(director_packet, sort_keys=True, default=str)
        return f"{endpoint}:{packet_json}"
