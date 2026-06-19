from __future__ import annotations

import onboarding_app


class _FakeButton:
    def __init__(self) -> None:
        self.config: dict[str, object] = {}

    def configure(self, **kwargs: object) -> None:
        self.config.update(kwargs)


class _FakeLabel:
    def __init__(self) -> None:
        self.config: dict[str, object] = {}

    def configure(self, **kwargs: object) -> None:
        self.config.update(kwargs)


class _FakeStringVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def _build_app() -> onboarding_app.OnboardingTrackerApp:
    app = object.__new__(onboarding_app.OnboardingTrackerApp)
    app.today_kpi_buttons = {"urgent": _FakeButton()}
    app.today_kpi_labels = {"urgent": _FakeStringVar()}
    app.today_dashboard_status_label = _FakeLabel()
    app._active_dashboard_kpi = None
    return app


def test_configure_today_kpi_disables_empty_count() -> None:
    app = _build_app()

    app._configure_today_kpi("urgent", 0, "Urgent", "urgent")

    assert app.today_kpi_labels["urgent"].value == "Urgent: 0"
    assert app.today_kpi_buttons["urgent"].config["state"] == "disabled"
    assert app.today_dashboard_status_label.config["text"] == "Urgent has no matching tasks right now."


def test_configure_today_kpi_preserves_selected_status_text() -> None:
    app = _build_app()
    app._active_dashboard_kpi = "urgent"

    app._configure_today_kpi("urgent", 3, "Urgent", "urgent")

    assert app.today_kpi_labels["urgent"].value == "Urgent: 3 • selected"
    assert app.today_kpi_buttons["urgent"].config["state"] == "normal"
    assert app.today_dashboard_status_label.config["text"] == "Urgent selected. Task filter is 'urgent'."
