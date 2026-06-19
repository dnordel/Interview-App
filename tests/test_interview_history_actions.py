import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
HistoryActionsService = interview_app.HistoryActionsService


class _HistoryStoreStub:
    def __init__(self, row_key: str = "row-1") -> None:
        self.row_key = row_key
        self.offer_updates: list[tuple[str, str, str]] = []
        self.row_updates: list[tuple[str, dict[str, object]]] = []

    def build_row_key(self, _row):
        return self.row_key

    def update_offer_state(self, row_key, status, offer_path):
        self.offer_updates.append((row_key, status, offer_path))
        return True

    def update_row(self, row_key, payload):
        self.row_updates.append((row_key, payload))
        return True


class _AppStub:
    def __init__(self, row_key: str = "row-1") -> None:
        self.history_store = _HistoryStoreStub(row_key=row_key)
        self.refresh_count = 0
        self.draft_ok = True
        self.opened_onboarding = 0
        self.offer_window_opened = 0

    def _refresh_history_tree(self):
        self.refresh_count += 1

    def _open_offer_generator(self, _row):
        self.offer_window_opened += 1

    def _open_onboarding_tracker(self):
        self.opened_onboarding += 1
        return True

    def _draft_offer_email_for_transition(self, _status, _row):
        return self.draft_ok


class TestInterviewHistoryActions(unittest.TestCase):
    def test_offer_status_transitions(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        row = {"offer_status": "generated"}

        with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
            service.handle_offer_action_for_row(row)

        self.assertEqual(app.history_store.offer_updates, [("row-1", "approved", "")])
        self.assertEqual(app.refresh_count, 1)
        showinfo.assert_called_once_with("Offer Workflow", "Offer marked as approved.")

    def test_retranscribe_history_action_is_no_longer_available(self):
        app = _AppStub()
        service = HistoryActionsService(app)

        with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
            service.handle_retranscribe_for_row({"flow_recordings": [{"flow_index": 0}]})

        self.assertEqual(app.history_store.row_updates, [])
        self.assertEqual(app.refresh_count, 0)
        showinfo.assert_called_once_with(
            "Transcription",
            "Retranscription is no longer available from interview history.",
        )


if __name__ == "__main__":
    unittest.main()
