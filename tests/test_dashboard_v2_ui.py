from __future__ import annotations

import os

import pytest


@pytest.mark.pyside_gui
def test_dashboard_v2_shell_registers_pages_and_collapses_to_locked_rail() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from dashboard_v2_ui import DashboardV2Shell

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    shell = DashboardV2Shell(QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets)
    staffing = qt_widgets.QLabel("Staffing")
    interviews = qt_widgets.QLabel("Interviews")

    shell.add_section("staffing", "STAFFING")
    shell.add_page("staffing", "staffing", "Staffing Dashboard", staffing, icon_key="dashboard")
    shell.add_section("hiring", "HIRING")
    shell.add_page("hiring", "interviews", "Interviews", interviews, icon_key="people")
    shell.show_page("interviews")
    shell.set_navigation_mode("rail")
    shell.set_navigation_locked(True)
    app.processEvents()

    assert shell.current_page_id == "interviews"
    assert shell.page_stack.currentWidget() is interviews
    assert shell.sidebar.width() == 64
    assert shell.page_buttons["staffing"].isEnabled() is False
    assert shell.page_buttons["interviews"].toolTip() == "Interviews"


@pytest.mark.pyside_gui
def test_staffing_v2_shell_hosts_hiring_pages_and_restores_full_navigation(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from staffing_dashboard_v2 import StaffingDashboardV2Page
    from staffing_service import StaffingService
    from staffing_store import StaffingStore

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
        notification_store_path=tmp_path / "notifications.sqlite3",
    )
    interviews = qt_widgets.QLabel("Interviews")
    candidates = qt_widgets.QLabel("Candidates")
    offers = qt_widgets.QLabel("Offers")

    page.register_external_section("hiring", "HIRING")
    page.register_external_page("hiring", "interviews", "Interviews", interviews, icon_key="people")
    page.register_external_page("hiring", "candidates", "Candidates", candidates, icon_key="people")
    page.register_external_page("hiring", "offers", "Offers", offers, icon_key="history")
    page.show_external_page("interviews")
    page.set_navigation_mode("rail")
    page.set_navigation_locked(True)
    app.processEvents()

    assert page.page_stack.currentWidget() is interviews
    assert page.staffing_sidebar.width() == 64
    assert page.external_nav_buttons["interviews"].isEnabled()
    assert not page.dashboard_nav_button.isEnabled()
    assert page.widget.findChild(qt_widgets.QLabel, "StaffingV2ExternalSection_hiring").text() == "HIRING"

    page.set_navigation_locked(False)
    page.set_navigation_mode("full")
    assert page.staffing_sidebar.width() == 252
    assert page.dashboard_nav_button.isEnabled()
    page.widget.close()
