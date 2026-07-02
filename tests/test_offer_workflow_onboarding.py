import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
InterviewApp = interview_app.InterviewApp


class _MemoryStore:
    def __init__(self, initial):
        self.data = dict(initial)

    def load(self):
        return dict(self.data)

    def save(self, value):
        self.data = dict(value)


class TestOfferWorkflowOnboarding(unittest.TestCase):
    def test_offer_action_label_for_welcome_email_sent_is_onboarding(self):
        label = InterviewApp._history_offer_action_label({"offer_status": "welcome_email_sent"})
        self.assertEqual(label, "Onboarding")

    def test_offer_action_welcome_email_sent_opens_onboarding_app(self):
        app = InterviewApp.__new__(InterviewApp)
        called: list[str] = []
        app._open_onboarding_tracker = lambda: called.append("opened") or True

        app._handle_offer_action_for_row({"offer_status": "welcome_email_sent"})

        self.assertEqual(called, ["opened"])

    def test_school_email_template_resolution_prefers_school_override(self):
        app = InterviewApp.__new__(InterviewApp)
        app.settings = {
            "offer_email_to": "global@example.org",
            "offer_approval_subject_template": "Global subject",
            "offer_approval_body_template": "Global body",
            "offer_acceptance_subject_template": "Accepted",
            "offer_acceptance_body_template": "Accepted body",
            "welcome_email_subject_template": "Welcome",
            "welcome_email_body_template": "Welcome body",
            "director_email_subject_template": "Director",
            "director_email_body_template": "Director body",
            "director_email_to": "director-global@example.org",
        }
        app.school_email_template_store = _MemoryStore({
            "North Long Beach": {
                "offer_email_to": "school@example.org",
                "offer_approval_subject_template": "School subject",
            }
        })

        resolved = app._resolve_school_email_templates("North Long Beach")

        self.assertEqual(resolved["offer_email_to"], "school@example.org")
        self.assertEqual(resolved["offer_approval_subject_template"], "School subject")
        self.assertEqual(resolved["offer_approval_body_template"], "Global body")

    def test_save_school_email_template_config_persists_expected_keys(self):
        app = InterviewApp.__new__(InterviewApp)
        app.school_email_template_store = _MemoryStore({})

        app.save_school_email_template_config("Hawthorne", {"offer_email_to": "school@example.org"})

        saved = app.school_email_template_store.load()["Hawthorne"]
        self.assertEqual(saved["offer_email_to"], "school@example.org")
        self.assertIn("welcome_email_body_template", saved)

    def test_school_email_template_resolution_falls_back_to_global_values(self):
        app = InterviewApp.__new__(InterviewApp)
        app.settings = {
            "offer_email_to": "global@example.org",
            "offer_approval_subject_template": "Global subject",
            "offer_approval_body_template": "Global body",
            "offer_acceptance_subject_template": "Accepted",
            "offer_acceptance_body_template": "Accepted body",
            "welcome_email_subject_template": "Welcome",
            "welcome_email_body_template": "Welcome body",
            "director_email_subject_template": "Director",
            "director_email_body_template": "Director body",
            "director_email_to": "director-global@example.org",
        }
        app.school_email_template_store = _MemoryStore({"North Long Beach": {"offer_email_to": ""}})

        resolved = app._resolve_school_email_templates("North Long Beach")

        self.assertEqual(resolved["offer_email_to"], "global@example.org")
        self.assertEqual(resolved["director_referral_subject_template"], "Director")

    def test_offer_status_update_emits_notification_event(self):
        app = InterviewApp.__new__(InterviewApp)

        class Store:
            def build_row_key(self, row):
                return "row-1"

            def update_offer_state(self, row_key, status, offer_path=""):
                return True

        class Notifications:
            def __init__(self):
                self.events = []

            def emit_event(self, event_type, payload, idempotency_key):
                self.events.append((event_type, payload, idempotency_key))
                return []

        notifications = Notifications()
        app.history_store = Store()
        app.notification_service = notifications
        app._refresh_history_tree = lambda: None

        app._history_actions_service().update_history_offer_status(
            {"candidate_name": "Jane Doe", "school": "Hawthorne", "position": "Teacher"},
            "accepted",
        )

        self.assertEqual(notifications.events[0][0], "offer.accepted")
        self.assertEqual(notifications.events[0][1]["candidate_name"], "Jane Doe")
        self.assertEqual(notifications.events[0][2], "row-1:offer.accepted")

    def test_failed_offer_status_update_does_not_emit_notification_event(self):
        app = InterviewApp.__new__(InterviewApp)

        class Store:
            def build_row_key(self, row):
                return "row-1"

            def update_offer_state(self, row_key, status, offer_path=""):
                return False

        class Notifications:
            def __init__(self):
                self.events = []

            def emit_event(self, event_type, payload, idempotency_key):
                self.events.append((event_type, payload, idempotency_key))
                return []

        notifications = Notifications()
        app.history_store = Store()
        app.notification_service = notifications
        app._refresh_history_tree = lambda: None

        result = app._history_actions_service().update_history_offer_status(
            {"candidate_name": "Jane Doe", "school": "Hawthorne", "position": "Teacher"},
            "accepted",
        )

        self.assertFalse(result)
        self.assertEqual(notifications.events, [])


if __name__ == "__main__":
    unittest.main()
