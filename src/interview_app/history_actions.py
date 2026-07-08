from __future__ import annotations

from datetime import date
from types import SimpleNamespace


messagebox = SimpleNamespace(
    showinfo=lambda *_args, **_kwargs: None,
    askyesno=lambda *_args, **_kwargs: False,
)
from pathlib import Path
from typing import Any

from interview_runtime import HistoryRowKey, OfferTransitionResult
from notification_service import notification_service_from_onboarding


class HistoryActionsService:
    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def offer_transition(status: str) -> OfferTransitionResult | None:
        transitions: dict[str, OfferTransitionResult] = {
            "not_generated": {"next_status": "generated", "done_message": "Offer generated."},
            "generated": {"next_status": "approved", "done_message": "Offer marked as approved."},
            "approved": {"next_status": "accepted", "done_message": "Offer marked as accepted."},
            "accepted": {"next_status": "welcome_email_sent", "done_message": "Welcome email sent."},
        }
        return transitions.get(status)

    def update_history_offer_status(self, row: dict[str, Any], status: str, offer_path: str = "") -> bool:
        row_key = self._row_key(row)
        if not row_key:
            return False
        if not self.app.history_store.update_offer_state(row_key, status, offer_path):
            return False
        self._emit_offer_notification(row, status, row_key)
        self.app._refresh_history_tree()
        return True

    def handle_offer_action_for_row(self, row: dict[str, Any]) -> None:
        status = str(row.get("offer_status", "not_generated")).strip().lower() or "not_generated"
        if status == "not_generated":
            self.app._open_offer_generator(row)
            return
        if status == "welcome_email_sent":
            self.app._open_onboarding_tracker()
            return
        transition = self.offer_transition(status)
        if transition is None:
            return
        if not self.app._draft_offer_email_for_transition(status, row):
            return
        if not self.update_history_offer_status(row, transition["next_status"]):
            return
        messagebox.showinfo("Offer Workflow", transition["done_message"])

    def handle_retranscribe_for_row(self, row: dict[str, Any]) -> None:
        messagebox.showinfo("Transcription", "Retranscription is no longer available from interview history.")

    def handle_delete_for_row(self, row: dict[str, Any]) -> None:
        row_key = self._row_key(row)
        if not row_key:
            return
        candidate = str(row.get("candidate_name") or "this interview").strip() or "this interview"
        if not messagebox.askyesno(
            "Delete History Entry",
            f"Delete history entry for {candidate}?\n\nThis removes the row from interview history.",
        ):
            return
        if not self.app.history_store.delete_row(row_key):
            return
        self.app._refresh_history_tree()

    def _row_key(self, row: dict[str, Any]) -> HistoryRowKey:
        return str(self.app.history_store.build_row_key(row)).strip()

    def _emit_offer_notification(self, row: dict[str, Any], status: str, row_key: str) -> None:
        event_type = {
            "generated": "offer.generated",
            "approved": "offer.approved",
            "accepted": "offer.accepted",
            "welcome_email_sent": "offer.welcome_email_sent",
        }.get(str(status or "").strip().lower())
        if not event_type:
            return
        service = getattr(self.app, "notification_service", None)
        if service is None:
            service = notification_service_from_onboarding(root_dir=Path.cwd())
        payload = {
            "candidate_name": str(row.get("candidate_name") or row.get("candidate") or "").strip(),
            "school": str(row.get("school") or "").strip(),
            "position": str(row.get("position") or row.get("role") or "").strip(),
            "offer_status": str(status or "").strip().lower(),
            "generated_date": date.today().isoformat(),
            "start_date": str(row.get("start_date") or "").strip(),
            "notice_given": str(row.get("notice_given") or row.get("date_notice_given") or "").strip(),
            "date_notice_given": str(row.get("date_notice_given") or row.get("notice_given") or "").strip(),
            "final_working_day": str(row.get("final_working_day") or row.get("last_working_day") or "").strip(),
            "last_working_day": str(row.get("last_working_day") or row.get("final_working_day") or "").strip(),
        }
        try:
            service.emit_event(event_type, payload, f"{row_key}:{event_type}")
        except Exception:
            return
