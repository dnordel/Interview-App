from __future__ import annotations

from tkinter import messagebox
from typing import Any

from interview_runtime import HistoryRowKey, OfferTransitionResult


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

    def _row_key(self, row: dict[str, Any]) -> HistoryRowKey:
        return str(self.app.history_store.build_row_key(row)).strip()
