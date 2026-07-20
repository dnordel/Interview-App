from __future__ import annotations

from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_staffing_bridge import DirectorIdentity
from onboarding_store import FilledArtifact, OnboardingStore
from onboarding_vault import EncryptedArtifactVault, OnboardingVault
from onboarding_workspace_v2 import OnboardingDashboardV2Workspace
from datetime import datetime, timezone
import json
import os
import pytest
from pypdf import PdfWriter


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.pyside_gui


class _DashboardSpy:
    def __init__(self) -> None:
        self.sections: list[tuple[str, str]] = []
        self.pages: list[tuple[str, str, str, object, object]] = []

    def register_external_section(self, section_id, label):
        self.sections.append((section_id, label))

    def register_external_page(self, section_id, page_id, label, *, provider, before_leave):
        self.pages.append((section_id, page_id, label, provider, before_leave))

    def show_external_page(self, page_id):
        self.shown_page = page_id

    def show_notifications_view(self):
        self.shown_page = "notifications"


def test_workspace_registers_role_scoped_lazy_onboarding_pages(tmp_path):
    store = OnboardingStore(tmp_path / "onboarding.sqlite3")
    admin = OnboardingDashboardV2Workspace(
        QtCore=object(),
        QtWidgets=object(),
        service=OnboardingService(store, OnboardingAccess("admin", "admin-1")),
    )
    director = OnboardingDashboardV2Workspace(
        QtCore=object(),
        QtWidgets=object(),
        service=OnboardingService(store, OnboardingAccess("director", "director-1", "Palmdale")),
    )
    admin_dashboard = _DashboardSpy()
    director_dashboard = _DashboardSpy()

    admin.register_with(admin_dashboard)
    director.register_with(director_dashboard)

    assert [page[2] for page in admin_dashboard.pages] == [
        "Tasks", "Overview", "Employees", "Templates", "Communications"
    ]
    assert [page[2] for page in director_dashboard.pages] == [
        "Tasks", "Overview", "Employees", "Communications"
    ]
    assert all(callable(page[3]) and callable(page[4]) for page in admin_dashboard.pages)


def test_workspace_task_filters_actions_kpi_and_secure_reveal_have_behavior(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = OnboardingStore(tmp_path / "ui.sqlite3")
    service = OnboardingService(store, OnboardingAccess("admin", "admin-1"))
    employee = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
        ssn="123-45-6789",
    )
    task = service.create_task(
        employee_id=employee.id,
        title="Orientation",
        owner_role="Director",
        due_date="2026-07-20",
    )
    workspace = OnboardingDashboardV2Workspace(QtCore=qt_core, QtWidgets=qt_widgets, service=service)
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    providers = {page[1]: page[3] for page in dashboard.pages}

    tasks_page = providers["onboarding_tasks"]()
    search = tasks_page.findChild(qt_widgets.QLineEdit, "OnboardingV2TaskSearch")
    table = tasks_page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    search.setText("missing")
    app.processEvents()
    assert table.isRowHidden(0)
    search.clear()
    table.selectRow(0)
    tasks_page.findChild(qt_widgets.QPushButton, "OnboardingV2CompleteTask").click()
    app.processEvents()
    assert service.get_task(task.id).status == "completed"
    tasks_page.findChild(qt_widgets.QPushButton, "OnboardingV2RefreshTasks").click()
    assert table.item(0, 5).text() == "Completed"

    overview = providers["onboarding_overview"]()
    assert "Blocked overdue 0" in overview.findChild(
        qt_widgets.QLabel, "OnboardingV2OverviewKpis"
    ).text()
    overview.findChild(qt_widgets.QPushButton, "OnboardingV2ViewTasks").click()
    assert dashboard.shown_page == "onboarding_tasks"

    monkeypatch.setattr(qt_widgets.QInputDialog, "getText", lambda *_args, **_kwargs: ("Payroll", True))
    employees = providers["onboarding_employees"]()
    roster = employees.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster")
    roster.selectRow(0)
    employees.findChild(qt_widgets.QPushButton, "OnboardingV2RevealSsn").click()
    assert employees.findChild(qt_widgets.QLabel, "OnboardingV2SsnValue").text() == "123456789"

    templates = providers["onboarding_templates"]()
    templates.findChild(
        qt_widgets.QPushButton, "OnboardingV2RefreshTemplates"
    ).click()
    communications = providers["onboarding_communications"]()
    communications.findChild(
        qt_widgets.QPushButton, "OnboardingV2RefreshCommunications"
    ).click()


@pytest.mark.pyside_gui
def test_employee_drawer_opens_and_exports_authorized_filled_artifact(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "artifacts-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    employee = service.create_employee(
        legal_name="Artifact User", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    artifact = FilledArtifact(
        id="artifact-1", employee_id=employee.id, submission_id="submission-1",
        package_version_id="package-1", school="Palmdale", kind="merged",
        suffix=".pdf", sha256="digest", created_at="2026-07-20T08:00:00+00:00",
    )
    opened_path = tmp_path / "opened.pdf"
    opened_path.write_bytes(b"opened")
    opened = []
    exported = []
    monkeypatch.setattr(service, "list_employee_filled_artifacts", lambda _employee_id: [artifact])
    monkeypatch.setattr(service, "open_filled_artifact", lambda **_values: opened_path)
    monkeypatch.setattr(service, "export_filled_artifact", lambda **values: exported.append(values) or values["destination"])
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    target = tmp_path / "export.pdf"
    monkeypatch.setattr(qt_widgets.QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(target), ""))
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service,
        file_opener=opened.append,
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    employees = next(page[3] for page in dashboard.pages if page[1] == "onboarding_employees")()
    employees.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster").selectRow(0)
    app.processEvents()
    artifacts = employees.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeFilledArtifacts")
    artifacts.selectRow(0)
    employees.findChild(qt_widgets.QPushButton, "OnboardingV2OpenFilledArtifact").click()
    employees.findChild(qt_widgets.QPushButton, "OnboardingV2ExportFilledArtifact").click()

    assert opened == [opened_path]
    assert exported[0]["employee_id"] == employee.id
    assert exported[0]["destination"] == target


@pytest.mark.pyside_gui
def test_director_creates_school_employee_and_top_level_task_from_v2_pages(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "create-ui.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    workspace = OnboardingDashboardV2Workspace(QtCore=qt_core, QtWidgets=qt_widgets, service=service)

    employees = workspace._build_employees_page()
    employees.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateEmployeeName").setText("Jordan Lee")
    employees.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateEmployeeRole").setText("Teacher")
    employees.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateEmployeeAcceptanceDate").setText("2026-07-01")
    employees.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateEmployeeStartDate").setText("2026-07-20")
    employees.findChild(qt_widgets.QPushButton, "OnboardingV2CreateEmployee").click()
    app.processEvents()
    [employee] = service.list_employees()
    assert employee.school == "Palmdale"
    employees.findChild(qt_widgets.QPushButton, "OnboardingV2EmployeeCard").click()
    app.processEvents()
    assert not employees.findChild(qt_widgets.QWidget, "OnboardingV2EmployeeProfileDrawer").isHidden()

    tasks = workspace._build_tasks_page()
    tasks.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateTaskTitle").setText("Orientation")
    tasks.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateTaskOwner").setText("Director")
    tasks.findChild(qt_widgets.QLineEdit, "OnboardingV2CreateTaskDueDate").setText("2026-07-21")
    tasks.findChild(qt_widgets.QPushButton, "OnboardingV2CreateTask").click()
    app.processEvents()
    [task] = service.list_tasks()
    assert task.employee_id == employee.id
    assert task.title == "Orientation"


def test_task_page_full_filters_and_inline_editor_update_selected_task(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "task-editor-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    employee = service.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director",
        watcher_roles=["IT"], due_date="2026-07-20",
    )
    workspace = OnboardingDashboardV2Workspace(QtCore=qt_core, QtWidgets=qt_widgets, service=service)
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_tasks")()
    page.show()
    app.processEvents()
    table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    table.selectRow(0)

    page.findChild(qt_widgets.QPushButton, "OnboardingV2EditTask").click()
    editor = page.findChild(qt_widgets.QWidget, "OnboardingV2TaskEditor")
    assert editor.isVisible()
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2TaskEditTitle").setText("Updated orientation")
    owner = page.findChild(qt_widgets.QComboBox, "OnboardingV2TaskEditOwner")
    owner.setCurrentText("Office Manager")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2TaskEditWatchers").setText("Director, IT")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2TaskEditDueDate").setText("2026-07-22")
    page.findChild(qt_widgets.QCheckBox, "OnboardingV2TaskEditCritical").setChecked(True)
    page.findChild(qt_widgets.QTextEdit, "OnboardingV2TaskEditNotes").setPlainText("Bring identification")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2SaveTask").click()
    app.processEvents()

    updated = service.get_task(task.id)
    assert updated.title == "Updated orientation"
    assert updated.owner_role == "Office Manager"
    assert updated.watcher_roles == ("Director", "IT")
    assert updated.due_date == "2026-07-22"
    assert updated.critical is True
    assert updated.notes == "Bring identification"
    watcher_filter = page.findChild(qt_widgets.QComboBox, "OnboardingV2TaskWatcherFilter")
    watcher_filter.setCurrentText("IT")
    app.processEvents()
    assert table.rowCount() == 1


def test_overview_lock_and_forget_device_controls_apply_distinct_security_actions(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(qt_widgets.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )

    def overview(name, key):
        cache = tmp_path / f"{name}.dpapi"
        vault = OnboardingVault(key * 32)
        vault.cache_for_device(cache)
        service = OnboardingService(
            OnboardingStore(tmp_path / f"{name}.sqlite3", vault=vault),
            OnboardingAccess("admin", "admin-1"),
            device_cache_path=cache,
        )
        workspace = OnboardingDashboardV2Workspace(QtCore=qt_core, QtWidgets=qt_widgets, service=service)
        dashboard = _DashboardSpy()
        workspace.register_with(dashboard)
        page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_overview")()
        return service, cache, page

    locked, kept_cache, lock_page = overview("lock", b"l")
    lock_page.findChild(qt_widgets.QPushButton, "OnboardingV2LockOnboarding").click()
    assert locked.onboarding_locked is True
    assert kept_cache.exists()

    forgotten, removed_cache, forget_page = overview("forget", b"f")
    forget_page.findChild(qt_widgets.QPushButton, "OnboardingV2ForgetDevice").click()
    assert forgotten.onboarding_locked is True
    assert not removed_cache.exists()


def test_communications_preview_send_and_failed_only_retry_have_behavior(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "communications.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Reminder Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id, title="Due task", owner_role="Director",
        watcher_roles=["Payroll"], due_date="2026-07-20",
    )
    attempts = []

    def sender(message):
        attempts.append(message.role)
        if message.role == "Payroll" and attempts.count("Payroll") == 1:
            raise RuntimeError("smtp unavailable")

    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core,
        QtWidgets=qt_widgets,
        service=service,
        reminder_recipient_resolver=lambda _school, role: f"{role.casefold()}@example.com",
        admin_fallback_email="admin@example.com",
        reminder_sender=sender,
        clock=lambda: datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_communications")()

    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewReminders").click()
    app.processEvents()
    summary = page.findChild(qt_widgets.QLabel, "OnboardingV2CommunicationsSummary")
    assert "director@example.com (1 tasks)" in summary.text()
    assert "payroll@example.com (1 tasks)" in summary.text()
    preview_table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2ReminderPreviewTable")
    assert preview_table.rowCount() == 2

    page.findChild(qt_widgets.QPushButton, "OnboardingV2SendReminders").click()
    assert "1 sent, 1 failed" in summary.text()
    retry = page.findChild(qt_widgets.QPushButton, "OnboardingV2RetryFailedReminders")
    assert retry.isEnabled()
    retry.click()

    assert attempts == ["Director", "Payroll", "Payroll"]
    assert "Retry complete: 1 sent, 0 failed" in summary.text()
    assert not retry.isEnabled()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2OpenNotifications").click()
    assert dashboard.shown_page == "notifications"


def test_workspace_leave_guard_save_discard_and_stay_choices_have_behavior(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    original = qt_widgets.QMessageBox
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])

    class FakeMessageBox:
        ButtonRole = original.ButtonRole
        choice = "Stay"

        def __init__(self):
            self.buttons = {}

        def setWindowTitle(self, _title):
            pass

        def setText(self, _text):
            pass

        def addButton(self, label, _role):
            self.buttons[label] = object()
            return self.buttons[label]

        def exec(self):
            pass

        def clickedButton(self):
            return self.buttons[self.choice]

    monkeypatch.setattr(qt_widgets, "QMessageBox", FakeMessageBox)
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core,
        QtWidgets=qt_widgets,
        service=OnboardingService(
            OnboardingStore(tmp_path / "leave.sqlite3"),
            OnboardingAccess("admin", "admin-1"),
        ),
    )
    outcomes = []
    leave_action = qt_widgets.QPushButton("Leave onboarding")
    leave_action.clicked.connect(lambda: outcomes.append(workspace.request_navigation_away()))

    workspace.mark_dirty()
    FakeMessageBox.choice = "Stay"
    leave_action.click()
    assert outcomes[-1] is False
    workspace.mark_dirty()
    FakeMessageBox.choice = "Save"
    leave_action.click()
    assert outcomes[-1] is True
    assert workspace.request_navigation_away() is True
    workspace.mark_dirty()
    FakeMessageBox.choice = "Discard"
    leave_action.click()
    assert outcomes[-1] is True
    assert workspace.request_navigation_away() is True


def test_templates_page_lists_versions_and_creates_reusable_field_inline(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "template-library-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    service.create_task_template_draft(
        template_key="orientation", school="Palmdale", title="Orientation",
        owner_role="Director", due_offset_days=1,
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_templates")()

    templates = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskTemplateVersions")
    assert templates.rowCount() == 1
    assert templates.item(0, 1).text() == "Orientation"
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2FieldStableId").setText("employee.nickname")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2FieldLabel").setText("Nickname")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2FieldAliases").setText("preferred nickname")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2CreateField").click()
    app.processEvents()

    fields = page.findChild(qt_widgets.QTableWidget, "OnboardingV2FieldLibrary")
    assert fields.rowCount() == 1
    assert fields.item(0, 0).text() == "employee.nickname"
    assert service.list_intake_fields()[0].label == "Nickname"


def test_employee_profile_drawer_saves_authorized_profile_changes(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "employee-profile-ui.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Jordan Lee", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_employees")()
    page.show()
    roster = page.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster")
    roster.selectRow(0)
    app.processEvents()

    drawer = page.findChild(qt_widgets.QWidget, "OnboardingV2EmployeeProfileDrawer")
    assert drawer.isVisible()
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2EmployeePreferredName").setText("Jordy")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2EmployeePersonalEmail").setText("jordy@example.com")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2EmployeePhone").setText("6615551212")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2SaveEmployeeProfile").click()
    app.processEvents()

    updated = service.get_employee(employee.id)
    assert updated.preferred_name == "Jordy"
    assert updated.personal_email == "jordy@example.com"
    assert updated.phone == "6615551212"
    assert roster.item(0, 0).text() == "Jordy"


def test_task_detail_adds_encrypted_comment_and_refreshes_visible_history(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "task-comment-ui.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Comment Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id, title="Orientation", owner_role="Director",
        due_date="2026-07-20",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_tasks")()
    table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    table.selectRow(0)
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2NewTaskComment").setText("Bring identification")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AddTaskComment").click()
    app.processEvents()

    assert service.list_task_comments(task.id)[0].body == "Bring identification"
    history = page.findChild(qt_widgets.QLabel, "OnboardingV2TaskCommentHistory")
    assert "director-1: Bring identification" in history.text()


def test_leave_guard_runs_registered_save_discard_and_cleanup_callbacks(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    original = qt_widgets.QMessageBox
    events = []

    class FakeMessageBox:
        ButtonRole = original.ButtonRole
        choice = "Save"

        def __init__(self):
            self.buttons = {}

        def setWindowTitle(self, _title):
            pass

        def setText(self, _text):
            pass

        def addButton(self, label, _role):
            self.buttons[label] = object()
            return self.buttons[label]

        def exec(self):
            pass

        def clickedButton(self):
            return self.buttons[self.choice]

    monkeypatch.setattr(qt_widgets, "QMessageBox", FakeMessageBox)
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets,
        service=OnboardingService(
            OnboardingStore(tmp_path / "edit-session.sqlite3"),
            OnboardingAccess("admin", "admin-1"),
        ),
    )
    workspace.register_edit_session(
        "employee", save=lambda: events.append("save") or True,
        discard=lambda: events.append("discard"), cleanup=lambda: events.append("cleanup"),
    )
    workspace.mark_dirty("employee")
    assert workspace.request_navigation_away() is True
    assert events == ["cleanup", "save"]

    FakeMessageBox.choice = "Discard"
    workspace.mark_dirty("employee")
    assert workspace.request_navigation_away() is True
    assert events[-2:] == ["cleanup", "discard"]


def test_task_drawer_adds_subtask_and_secure_attachment_then_cleans_temp(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    vault = OnboardingVault(b"d" * 32)
    artifact_vault = EncryptedArtifactVault(
        tmp_path / "sealed", tmp_path / "temporary", vault=vault
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "task-drawer.sqlite3", vault=vault),
        OnboardingAccess("director", "director-1", "Palmdale"),
        attachment_scanner=lambda _path: "clean", artifact_vault=artifact_vault,
    )
    employee = service.create_employee(
        legal_name="Drawer Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    parent = service.create_task(
        employee_id=employee.id, title="Parent task", owner_role="Director",
        due_date="2026-07-20",
    )
    source = tmp_path / "attachment.txt"
    source.write_text("private", encoding="utf-8")
    monkeypatch.setattr(
        qt_widgets.QFileDialog, "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "Text (*.txt)"),
    )
    opened = []
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service,
        file_opener=lambda path: opened.append(path),
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_tasks")()
    page.show()
    table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    table.selectRow(0)
    app.processEvents()
    drawer = page.findChild(qt_widgets.QFrame, "OnboardingV2TaskDrawer")
    assert drawer.isVisible()

    page.findChild(qt_widgets.QLineEdit, "OnboardingV2NewSubtaskTitle").setText("Required child")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AddSubtask").click()
    assert any(task.parent_task_id == parent.id for task in service.list_tasks())
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AttachTaskFile").click()
    attachments = service.list_task_attachments(parent.id)
    assert len(attachments) == 1
    page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskAttachments").selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2OpenTaskAttachment").click()
    assert opened and opened[0].read_text(encoding="utf-8") == "private"

    page.findChild(qt_widgets.QPushButton, "OnboardingV2CloseTaskDrawer").click()
    assert not opened[0].exists()
    assert drawer.isHidden()


def test_overview_kpi_navigates_to_task_queue_with_matching_filter(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "overview-kpi.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    employee = service.create_employee(
        legal_name="KPI Test", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id, title="Open task", owner_role="Director",
        due_date="2026-07-20",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    providers = {item[1]: item[3] for item in dashboard.pages}
    overview = providers["onboarding_overview"]()

    open_kpi = overview.findChild(qt_widgets.QPushButton, "OnboardingV2KpiOpen")
    assert "Open\n1" == open_kpi.text()
    open_kpi.click()
    assert dashboard.shown_page == "onboarding_tasks"
    tasks = providers["onboarding_tasks"]()
    assert tasks.findChild(qt_widgets.QComboBox, "OnboardingV2TaskStatusFilter").currentText() == "Open"


def test_director_employee_drawer_marks_did_not_start_and_hides_admin_actions(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(qt_widgets.QMessageBox, "question", lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    service = OnboardingService(
        OnboardingStore(tmp_path / "employee-lifecycle-ui.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="No Start", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-08-01",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_employees")()
    roster = page.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster")
    roster.selectRow(0)
    page.findChild(qt_widgets.QComboBox, "OnboardingV2DidNotStartReason").setCurrentText("candidate_withdrew")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2MarkDidNotStart").click()
    app.processEvents()

    assert service.get_employee(employee.id).status == "archived"
    assert page.findChild(qt_widgets.QPushButton, "OnboardingV2TransferEmployee") is None
    assert page.findChild(qt_widgets.QPushButton, "OnboardingV2DeleteEmployee") is None


def test_task_drawer_completes_subtask_and_edits_own_comment_revision(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "task-collaboration-ui.sqlite3"),
        OnboardingAccess("director", "director-1", "Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Collaboration", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    parent = service.create_task(
        employee_id=employee.id, title="Parent", owner_role="Director", due_date="2026-07-20"
    )
    child = service.create_task(
        employee_id=employee.id, title="Child", owner_role="Director", due_date="2026-07-20",
        parent_task_id=parent.id,
    )
    comment = service.add_task_comment(parent.id, body="First")
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_tasks")()
    table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    parent_row = next(row for row in range(table.rowCount()) if table.item(row, 2).text() == "Parent")
    table.selectRow(parent_row)
    subtask_table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskSubtasks")
    subtask_table.selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2CompleteSubtask").click()
    assert service.get_task(child.id).status == "completed"
    page.findChild(qt_widgets.QComboBox, "OnboardingV2TaskCommentSelector").setCurrentIndex(0)
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2EditTaskCommentText").setText("Revised")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2EditTaskComment").click()
    app.processEvents()

    assert service.list_task_comments(parent.id)[0].body == "Revised"
    assert len(service.list_task_comment_revisions(comment.id)) == 2


def test_templates_workflow_publishes_template_package_and_visual_mapping(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    source = tmp_path / "welcome.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as file:
        writer.write(file)
    service = OnboardingService(
        OnboardingStore(tmp_path / "templates-workflow.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
        attachment_scanner=lambda _path: "unavailable",
    )
    field = service.create_intake_field(
        stable_id="employee.preferred_name", label="Preferred name",
        field_type="short_text", sensitivity="personal", aliases=["preferred"],
    )
    monkeypatch.setattr(
        qt_widgets.QFileDialog, "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "PDF (*.pdf)"),
    )
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "information", lambda *_args, **_kwargs: None)
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_templates")()

    page.findChild(qt_widgets.QLineEdit, "OnboardingV2TemplateKey").setText("orientation")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2TemplateTitle").setText("Orientation")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2CreateTemplateDraft").click()
    template_table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskTemplateVersions")
    template_table.selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AddTemplateAttachment").click()
    assert service.list_task_template_attachments(
        service.list_task_template_versions()[0].id
    )[0].name == "welcome.pdf"
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PublishTemplate").click()
    assert service.list_task_template_versions()[0].status == "published"

    page.findChild(qt_widgets.QLineEdit, "OnboardingV2PackageKey").setText("teacher-start")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2PackageTitle").setText("Teacher start")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AddPackageDocument").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2AddPackageDocument").click()
    documents = page.findChild(qt_widgets.QListWidget, "OnboardingV2PackageDocuments")
    documents.setCurrentRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ReplacePackageDocument").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2MovePackageDocumentDown").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2MovePackageDocumentUp").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2RemovePackageDocument").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2CreatePackageDraft").click()
    package_table = page.findChild(qt_widgets.QTableWidget, "OnboardingV2PackageVersions")
    package_table.selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ValidatePackage").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PublishPackage").click()
    assert service.list_document_package_versions()[0].status == "published"

    page.findChild(qt_widgets.QPushButton, "OnboardingV2LoadMapperPdf").click()
    app.processEvents()
    canvas = page.findChild(qt_widgets.QGraphicsView, "OnboardingV2PdfMapperCanvas")
    box = canvas.add_pdf_box((72, 650, 180, 20))
    box.setSelected(True)
    field_choice = page.findChild(qt_widgets.QComboBox, "OnboardingV2MapperField")
    field_choice.setCurrentIndex(field_choice.findData(field.id))
    page.findChild(qt_widgets.QComboBox, "OnboardingV2MapperCasing").setCurrentText("upper")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2MapperMask").setText("phone")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2MapperDatePattern").setText("MM/DD/YYYY")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2MapperTrueValue").setText("Yes")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2MapperFalseValue").setText("No")
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2MapperChoiceValues").setText("Teacher=T, Director=D")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2SavePdfMapping").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewPdfMapping").click()
    assert "Preview blocked" in page.findChild(qt_widgets.QLabel, "OnboardingV2MapperStatus").text()
    mapping = service.list_pdf_mappings()[0]
    assert mapping.field_id == field.id
    assert json.loads(mapping.formatting_json) == {
        "casing": "upper", "choice_values": {"Director": "D", "Teacher": "T"},
        "date_pattern": "MM/DD/YYYY", "false_value": "No", "mask": "phone",
        "true_value": "Yes",
    }
    template_table.selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2DeprecateTemplate").click()
    assert service.list_task_template_versions()[0].status == "deprecated"
    fields = page.findChild(qt_widgets.QTableWidget, "OnboardingV2FieldLibrary")
    fields.selectRow(0)
    page.findChild(qt_widgets.QPushButton, "OnboardingV2DeprecateField").click()
    assert service.list_intake_fields()[0].deprecated is True


def test_admin_templates_page_previews_and_idempotently_imports_legacy_json(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"employees": []}), encoding="utf-8")
    service = OnboardingService(
        OnboardingStore(tmp_path / "migration-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    monkeypatch.setattr(
        qt_widgets.QFileDialog, "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "JSON (*.json)"),
    )
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service,
        migration_backup_dir=tmp_path / "backups",
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_templates")()

    page.findChild(qt_widgets.QPushButton, "OnboardingV2SelectLegacyMigration").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewLegacyMigration").click()
    status = page.findChild(qt_widgets.QLabel, "OnboardingV2LegacyMigrationStatus")
    assert "Employees: 0" in status.text()
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2LegacyMigrationConfirmation").setText("IMPORT")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ImportLegacyMigration").click()
    assert "Imported employees: 0" in status.text()
    assert len(list((tmp_path / "backups").glob("*.backup.json"))) == 1
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ImportLegacyMigration").click()
    assert len(list((tmp_path / "backups").glob("*.backup.json"))) == 1


def test_density_persists_and_narrow_tasks_switch_to_stacked_cards(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])

    class Settings:
        values = {}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    service = OnboardingService(
        OnboardingStore(tmp_path / "responsive-ui.sqlite3"), OnboardingAccess("admin", "admin-1")
    )
    employee = service.create_employee(
        legal_name="Responsive", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id, title="Responsive task", owner_role="Director", due_date="2026-07-20"
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service, settings=Settings()
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    providers = {item[1]: item[3] for item in dashboard.pages}
    overview = providers["onboarding_overview"]()
    density = overview.findChild(qt_widgets.QPushButton, "OnboardingV2DensityToggle")
    density.click()
    assert Settings.values["onboarding/v2/density"] == "comfortable"

    tasks = providers["onboarding_tasks"]()
    tasks.show()
    tasks.resize(700, 700)
    app.processEvents()
    table = tasks.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue")
    cards = tasks.findChild(qt_widgets.QWidget, "OnboardingV2TaskCards")
    assert table.isHidden()
    assert cards.isVisible()
    cards.findChild(qt_widgets.QPushButton, "OnboardingV2TaskCard").click()
    app.processEvents()
    assert tasks.findChild(qt_widgets.QFrame, "OnboardingV2TaskDrawer").isVisible()
    tasks.resize(1100, 700)
    app.processEvents()
    assert table.isVisible()
    assert cards.isHidden()


def test_admin_employee_transfer_archive_and_typed_delete_refresh_roster(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "question", lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes)
    service = OnboardingService(
        OnboardingStore(tmp_path / "admin-lifecycle-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
    )
    employee = service.create_employee(
        legal_name="Correction", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-08-01",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_employees")()
    roster = page.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster")
    roster.selectRow(0)
    page.findChild(qt_widgets.QComboBox, "OnboardingV2TransferSchool").setCurrentText("Hawthorne")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2TransferEmployee").click()
    assert service.get_employee(employee.id).school == "Hawthorne"
    page.findChild(qt_widgets.QComboBox, "OnboardingV2ArchiveCorrectionReason").setCurrentText("duplicate")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ArchiveEmployee").click()
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2DeleteConfirmation").setText(f"DELETE {employee.id}")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2DeleteEmployee").click()
    app.processEvents()

    assert service.list_employees() == []
    assert roster.rowCount() == 0


def test_employee_separation_and_retention_preview_purge_actions(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    messages = []
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qt_widgets.QMessageBox, "question", lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "information",
        lambda _parent, title, text, *_args, **_kwargs: messages.append((title, text)),
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "separation-purge-ui.sqlite3"),
        OnboardingAccess("admin", "admin-1"),
        director_resolver=lambda school: DirectorIdentity("director-1", "Current Director", school),
    )
    employee = service.create_employee(
        legal_name="Former Employee", school="Palmdale", role="Teacher",
        acceptance_date="2017-01-01", start_date="2017-02-01",
    )
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_employees")()
    page.findChild(qt_widgets.QTableWidget, "OnboardingV2EmployeeRoster").selectRow(0)
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2LastWorkingDay").setText("2018-01-01")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2MarkEmploymentEnded").click()
    assert service.get_employee(employee.id).status == "archived"
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2PurgeAsOf").setText("2026-07-20")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewRetentionPurge").click()
    assert messages[-1] == ("Retention purge preview", "Eligible employees: 1")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PurgeEmployee").click()
    assert service.list_employees() == []


def test_admin_task_drawer_redacts_selected_comment_with_reason(tmp_path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    service = OnboardingService(
        OnboardingStore(tmp_path / "redaction-ui.sqlite3"), OnboardingAccess("admin", "admin-1")
    )
    employee = service.create_employee(
        legal_name="Redaction", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    task = service.create_task(
        employee_id=employee.id, title="Redact", owner_role="Director", due_date="2026-07-20"
    )
    comment = service.add_task_comment(task.id, body="Sensitive")
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_tasks")()
    page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskQueue").selectRow(0)
    page.findChild(qt_widgets.QLineEdit, "OnboardingV2CommentRedactionReason").setText("Contains PII")
    page.findChild(qt_widgets.QPushButton, "OnboardingV2RedactTaskComment").click()
    assert service.list_task_comments(task.id)[0].redacted is True
    assert len(service.list_task_comment_revisions(comment.id)) == 2


def test_templates_page_previews_and_applies_selected_employee_upgrades(tmp_path, monkeypatch):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(qt_widgets.QMessageBox, "information", lambda *_args, **_kwargs: None)
    source = tmp_path / "upgrade.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as file:
        writer.write(file)
    service = OnboardingService(
        OnboardingStore(tmp_path / "upgrade-ui.sqlite3"), OnboardingAccess("admin", "admin-1")
    )
    package_one = service.publish_document_package(service.create_document_package_draft(
        package_key="teacher-start", school="Palmdale", title="Package one", document_paths=[source]
    ).id)
    template_one = service.publish_task_template(service.create_task_template_draft(
        template_key="orientation", school="Palmdale", title="Orientation one",
        owner_role="Director", due_offset_days=0, package_key="teacher-start",
    ).id)
    employee = service.create_employee(
        legal_name="Upgrade", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    package_two = service.publish_document_package(service.create_document_package_draft(
        package_key="teacher-start", school="Palmdale", title="Package two", document_paths=[source]
    ).id)
    template_two = service.publish_task_template(service.create_task_template_draft(
        template_key="orientation", school="Palmdale", title="Orientation two",
        owner_role="Director", due_offset_days=2, package_key="teacher-start",
    ).id)
    workspace = OnboardingDashboardV2Workspace(
        QtCore=qt_core, QtWidgets=qt_widgets, service=service
    )
    dashboard = _DashboardSpy()
    workspace.register_with(dashboard)
    page = next(item[3] for item in dashboard.pages if item[1] == "onboarding_templates")()
    employees = page.findChild(qt_widgets.QListWidget, "OnboardingV2UpgradeEmployees")
    employees.item(0).setSelected(True)
    packages = page.findChild(qt_widgets.QTableWidget, "OnboardingV2PackageVersions")
    packages.selectRow(next(row for row in range(packages.rowCount()) if packages.item(row, 1).text() == "Package two"))
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewPackageUpgrade").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ApplyPackageUpgrade").click()
    templates = page.findChild(qt_widgets.QTableWidget, "OnboardingV2TaskTemplateVersions")
    templates.selectRow(next(row for row in range(templates.rowCount()) if templates.item(row, 1).text() == "Orientation two"))
    page.findChild(qt_widgets.QPushButton, "OnboardingV2PreviewTemplateUpgrade").click()
    page.findChild(qt_widgets.QPushButton, "OnboardingV2ApplyTemplateUpgrade").click()

    task = next(task for task in service.list_tasks() if task.employee_id == employee.id)
    assert task.package_version_id == package_two.id
    assert task.template_id == template_two.id
    assert task.template_id != template_one.id
    assert package_one.id != package_two.id
