from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any, Callable

from onboarding_service import OnboardingService
from onboarding_package_editor import DocumentPackageDraftEditor
from onboarding_pdf_fill import detect_acroform_fields
from onboarding_pdf_mapper_v2 import OnboardingPdfMapperCanvas
from staffing_dashboard_v2 import configure_v2_scroll_areas


class _MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


class OnboardingDashboardV2Workspace:
    """Lazy Onboarding pages composed inside the shared Staffing v2 shell."""

    _PAGES = (
        ("onboarding_tasks", "Tasks", "tasks"),
        ("onboarding_overview", "Overview", "overview"),
        ("onboarding_employees", "Employees", "employees"),
        ("onboarding_templates", "Templates", "templates"),
        ("onboarding_communications", "Communications", "communications"),
    )

    def __init__(
        self,
        *,
        QtCore: Any,
        QtWidgets: Any,
        service: OnboardingService,
        reminder_recipient_resolver: Callable[[str, str], str] | None = None,
        admin_fallback_email: str | Callable[[], str] = "",
        reminder_sender: Callable[[Any], None] | None = None,
        reminder_config_revision: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        file_opener: Callable[[Path], None] | None = None,
        settings: Any | None = None,
        migration_backup_dir: Path | None = None,
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.service = service
        self.reminder_recipient_resolver = reminder_recipient_resolver or (lambda _school, _role: "")
        self.admin_fallback_email_provider = (
            admin_fallback_email if callable(admin_fallback_email)
            else lambda: str(admin_fallback_email or "").strip()
        )
        self.admin_fallback_email = str(self.admin_fallback_email_provider() or "").strip()
        self.reminder_sender = reminder_sender or (lambda _message: None)
        self.reminder_config_revision = reminder_config_revision or (lambda: "")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.file_opener = file_opener or (lambda path: os.startfile(path))
        self.migration_backup_dir = (
            Path(migration_backup_dir)
            if migration_backup_dir is not None
            else self.service.store.path.parent / "migration_backups"
        )
        self.settings = settings or (
            self.QtCore.QSettings("LPL", "InterviewTool")
            if hasattr(self.QtCore, "QSettings") else _MemorySettings()
        )
        self.density = str(self.settings.value("onboarding/v2/density", "compact") or "compact")
        if self.density not in {"compact", "comfortable"}:
            self.density = "compact"
        self._built_pages: list[Any] = []
        self._dirty = False
        self._dirty_sessions: set[str] = set()
        self._edit_sessions: dict[str, tuple[Callable[[], bool], Callable[[], None], Callable[[], None]]] = {}
        self.dashboard: Any | None = None
        self._sensitive_labels: list[Any] = []
        self._task_filter_request: dict[str, str] = {}
        self._page_factories: dict[str, Callable[[], Any]] = {
            "tasks": self._build_tasks_page,
            "overview": self._build_overview_page,
            "employees": self._build_employees_page,
            "templates": self._build_templates_page,
            "communications": self._build_communications_page,
        }

    def register_with(self, dashboard: Any) -> None:
        self.dashboard = dashboard
        dashboard.register_external_section("onboarding", "ONBOARDING")
        for page_id, label, factory_key in self._PAGES:
            if factory_key == "templates" and self.service.access.role != "admin":
                continue
            dashboard.register_external_page(
                "onboarding",
                page_id,
                label,
                provider=self._page_factories[factory_key],
                before_leave=self.request_navigation_away,
            )

    def request_navigation_away(self) -> bool:
        self._remask_sensitive_values()
        for _save, _discard, cleanup in self._edit_sessions.values():
            cleanup()
        if not self._dirty and not self._dirty_sessions:
            return True
        box = self.QtWidgets.QMessageBox()
        box.setWindowTitle("Unsaved Onboarding Changes")
        box.setText("Save changes before leaving this Onboarding page?")
        save = box.addButton("Save", self.QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", self.QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Stay", self.QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            for key in tuple(self._dirty_sessions):
                session_save = self._edit_sessions.get(key, (lambda: True, lambda: None, lambda: None))[0]
                if not session_save():
                    return False
            self._dirty = False
            self._dirty_sessions.clear()
            return True
        if clicked is discard:
            for key in tuple(self._dirty_sessions):
                self._edit_sessions.get(key, (lambda: True, lambda: None, lambda: None))[1]()
            self._dirty = False
            self._dirty_sessions.clear()
            return True
        return False

    def request_close(self) -> bool:
        return self.request_navigation_away()

    def refresh_after_handoff(self) -> None:
        if self.dashboard is None or not hasattr(self.dashboard, "external_pages"):
            return
        current_page_id = str(getattr(self.dashboard, "current_page_id", "") or "")
        page_stack = getattr(self.dashboard, "page_stack", None)
        for page_id, _label, _factory_key in self._PAGES:
            widget = self.dashboard.external_pages.get(page_id)
            if widget is None or page_id == current_page_id:
                continue
            if page_stack is not None:
                page_stack.removeWidget(widget)
            if widget in self._built_pages:
                self._built_pages.remove(widget)
            widget.deleteLater()
            self.dashboard.external_pages[page_id] = None

    def register_edit_session(
        self,
        key: str,
        *,
        save: Callable[[], bool],
        discard: Callable[[], None],
        cleanup: Callable[[], None],
    ) -> None:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("Onboarding edit session key is required.")
        self._edit_sessions[clean_key] = (save, discard, cleanup)

    def mark_dirty(self, session_key: str = "") -> None:
        clean_key = str(session_key or "").strip()
        if clean_key:
            if clean_key not in self._edit_sessions:
                raise ValueError("Onboarding edit session is not registered.")
            self._dirty_sessions.add(clean_key)
            return
        self._dirty = True

    def _build_tasks_page(self) -> Any:
        page, layout = self._page("Onboarding Tasks", "Prioritized onboarding work across authorized employees.", "Tasks")
        filters = self.QtWidgets.QHBoxLayout()
        search = self.QtWidgets.QLineEdit()
        search.setObjectName("OnboardingV2TaskSearch")
        search.setAccessibleName("Search onboarding tasks")
        search.setPlaceholderText("Search employee, task, school, owner…")
        status_filter = self.QtWidgets.QComboBox()
        status_filter.setObjectName("OnboardingV2TaskStatusFilter")
        status_filter.setAccessibleName("Filter onboarding tasks by status")
        status_filter.addItems(["All statuses", "Open", "Blocked", "Completed", "Cancelled"])
        owner_filter = self.QtWidgets.QComboBox()
        owner_filter.setObjectName("OnboardingV2TaskOwnerFilter")
        owner_filter.setAccessibleName("Filter onboarding tasks by owner")
        owner_filter.addItem("All owners")
        owner_filter.addItems(sorted({task.owner_role for task in self.service.list_tasks()}, key=str.casefold))
        watcher_filter = self.QtWidgets.QComboBox()
        watcher_filter.setObjectName("OnboardingV2TaskWatcherFilter")
        watcher_filter.setAccessibleName("Filter onboarding tasks by watcher")
        watcher_filter.addItem("All watchers")
        watcher_filter.addItems(sorted({role for task in self.service.list_tasks() for role in task.watcher_roles}, key=str.casefold))
        school_filter = self.QtWidgets.QComboBox()
        school_filter.setObjectName("OnboardingV2TaskSchoolFilter")
        school_filter.setAccessibleName("Filter onboarding tasks by school")
        school_filter.addItem("All schools")
        school_filter.addItems(sorted({task.school for task in self.service.list_tasks()}, key=str.casefold))
        employee_filter = self.QtWidgets.QComboBox()
        employee_filter.setObjectName("OnboardingV2TaskEmployeeFilter")
        employee_filter.setAccessibleName("Filter onboarding tasks by employee")
        employee_filter.addItem("All employees", "")
        for employee in self.service.list_employees():
            employee_filter.addItem(employee.preferred_name or employee.legal_name, employee.id)
        urgency_filter = self.QtWidgets.QComboBox()
        urgency_filter.setObjectName("OnboardingV2TaskUrgencyFilter")
        urgency_filter.setAccessibleName("Filter onboarding tasks by urgency")
        urgency_filter.addItems(["All urgency", "Critical", "Overdue", "Due today", "Upcoming"])
        blocked_filter = self.QtWidgets.QComboBox()
        blocked_filter.setObjectName("OnboardingV2TaskBlockedFilter")
        blocked_filter.setAccessibleName("Filter onboarding tasks by blocked state")
        blocked_filter.addItems(["All work", "Blocked", "Unblocked"])
        due_from = self.QtWidgets.QLineEdit()
        due_from.setObjectName("OnboardingV2TaskDueFromFilter")
        due_from.setAccessibleName("Filter onboarding tasks due on or after date")
        due_from.setPlaceholderText("Due from YYYY-MM-DD")
        due_to = self.QtWidgets.QLineEdit()
        due_to.setObjectName("OnboardingV2TaskDueToFilter")
        due_to.setAccessibleName("Filter onboarding tasks due on or before date")
        due_to.setPlaceholderText("Due to YYYY-MM-DD")
        filters.addWidget(search, 1)
        filters.addWidget(status_filter)
        filters.addWidget(owner_filter)
        filters.addWidget(watcher_filter)
        filters.addWidget(school_filter)
        filters.addWidget(employee_filter)
        filters.addWidget(urgency_filter)
        filters.addWidget(blocked_filter)
        filters.addWidget(due_from)
        filters.addWidget(due_to)
        layout.addLayout(filters)

        actions = self.QtWidgets.QHBoxLayout()
        refresh_button = self.QtWidgets.QPushButton("Refresh")
        refresh_button.setObjectName("OnboardingV2RefreshTasks")
        refresh_button.setAccessibleName("Refresh onboarding task queue")
        complete_button = self.QtWidgets.QPushButton("Complete selected")
        complete_button.setObjectName("OnboardingV2CompleteTask")
        complete_button.setAccessibleName("Complete selected onboarding task")
        edit_button = self.QtWidgets.QPushButton("Edit selected")
        edit_button.setObjectName("OnboardingV2EditTask")
        edit_button.setAccessibleName("Edit selected onboarding task")
        actions.addWidget(refresh_button)
        actions.addWidget(complete_button)
        actions.addWidget(edit_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        create_task_form = self.QtWidgets.QHBoxLayout()
        create_task_employee = self.QtWidgets.QComboBox()
        create_task_employee.setObjectName("OnboardingV2CreateTaskEmployee")
        for employee in self.service.list_employees():
            create_task_employee.addItem(employee.preferred_name or employee.legal_name, employee.id)
        create_task_title = self.QtWidgets.QLineEdit()
        create_task_title.setObjectName("OnboardingV2CreateTaskTitle")
        create_task_title.setPlaceholderText("New task title")
        create_task_owner = self.QtWidgets.QLineEdit()
        create_task_owner.setObjectName("OnboardingV2CreateTaskOwner")
        create_task_owner.setPlaceholderText("Owner role")
        create_task_due = self.QtWidgets.QLineEdit()
        create_task_due.setObjectName("OnboardingV2CreateTaskDueDate")
        create_task_due.setPlaceholderText("Due YYYY-MM-DD")
        create_task_button = self.QtWidgets.QPushButton("Create task")
        create_task_button.setObjectName("OnboardingV2CreateTask")
        create_task_button.setAccessibleName("Create top-level onboarding task")
        for control in (create_task_employee, create_task_title, create_task_owner, create_task_due, create_task_button):
            create_task_form.addWidget(control)
        layout.addLayout(create_task_form)

        table = self.QtWidgets.QTableWidget(0, 7)
        table.setObjectName("OnboardingV2TaskQueue")
        table.setAccessibleName("Onboarding task queue")
        table.setHorizontalHeaderLabels(["Employee", "School", "Task", "Owner", "Due", "Status", "Urgency"])
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        table.horizontalHeader().setStretchLastSection(True)
        work_area = self.QtWidgets.QWidget()
        work_layout = self.QtWidgets.QHBoxLayout(work_area)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.addWidget(table, 1)
        task_cards = self.QtWidgets.QWidget()
        task_cards.setObjectName("OnboardingV2TaskCards")
        task_cards.setAccessibleName("Onboarding tasks stacked card view")
        task_cards_layout = self.QtWidgets.QVBoxLayout(task_cards)
        task_cards.hide()
        work_layout.addWidget(task_cards, 1)
        drawer = self.QtWidgets.QFrame()
        drawer.setObjectName("OnboardingV2TaskDrawer")
        drawer.setAccessibleName("Selected onboarding task detail drawer")
        drawer.setMinimumWidth(360)
        drawer.setMaximumWidth(520)
        drawer_layout = self.QtWidgets.QVBoxLayout(drawer)
        close_drawer = self.QtWidgets.QPushButton("Close")
        close_drawer.setObjectName("OnboardingV2CloseTaskDrawer")
        close_drawer.setAccessibleName("Close onboarding task detail drawer")
        drawer_layout.addWidget(close_drawer)
        work_layout.addWidget(drawer)
        drawer.hide()
        layout.addWidget(work_area, 1)
        detail = self.QtWidgets.QLabel("Select a task to view ownership, dependencies, notes, and audit summary.")
        detail.setObjectName("OnboardingV2TaskDetail")
        detail.setAccessibleName("Selected onboarding task details")
        detail.setWordWrap(True)
        drawer_layout.addWidget(detail)
        subtask_table = self.QtWidgets.QTableWidget(0, 3)
        subtask_table.setObjectName("OnboardingV2TaskSubtasks")
        subtask_table.setAccessibleName("Selected task subtasks")
        subtask_table.setHorizontalHeaderLabels(["Subtask", "Required", "Status"])
        subtask_table.horizontalHeader().setStretchLastSection(True)
        drawer_layout.addWidget(subtask_table)
        subtask_actions = self.QtWidgets.QHBoxLayout()
        new_subtask = self.QtWidgets.QLineEdit()
        new_subtask.setObjectName("OnboardingV2NewSubtaskTitle")
        new_subtask.setPlaceholderText("New subtask")
        required_subtask = self.QtWidgets.QCheckBox("Required")
        required_subtask.setObjectName("OnboardingV2SubtaskRequired")
        required_subtask.setChecked(True)
        add_subtask = self.QtWidgets.QPushButton("Add subtask")
        add_subtask.setObjectName("OnboardingV2AddSubtask")
        add_subtask.setAccessibleName("Add subtask to selected onboarding task")
        complete_subtask = self.QtWidgets.QPushButton("Complete subtask")
        complete_subtask.setObjectName("OnboardingV2CompleteSubtask")
        complete_subtask.setAccessibleName("Complete selected onboarding subtask")
        subtask_actions.addWidget(new_subtask, 1)
        subtask_actions.addWidget(required_subtask)
        subtask_actions.addWidget(add_subtask)
        subtask_actions.addWidget(complete_subtask)
        drawer_layout.addLayout(subtask_actions)
        comment_history = self.QtWidgets.QLabel("No comments.")
        comment_history.setObjectName("OnboardingV2TaskCommentHistory")
        comment_history.setAccessibleName("Selected task comment history")
        comment_history.setWordWrap(True)
        comment_actions = self.QtWidgets.QHBoxLayout()
        new_comment = self.QtWidgets.QLineEdit()
        new_comment.setObjectName("OnboardingV2NewTaskComment")
        new_comment.setAccessibleName("New onboarding task comment")
        new_comment.setPlaceholderText("Add a comment")
        add_comment = self.QtWidgets.QPushButton("Add comment")
        add_comment.setObjectName("OnboardingV2AddTaskComment")
        add_comment.setAccessibleName("Add comment to selected onboarding task")
        comment_actions.addWidget(new_comment, 1)
        comment_actions.addWidget(add_comment)
        drawer_layout.addWidget(comment_history)
        drawer_layout.addLayout(comment_actions)
        comment_edit = self.QtWidgets.QHBoxLayout()
        comment_selector = self.QtWidgets.QComboBox()
        comment_selector.setObjectName("OnboardingV2TaskCommentSelector")
        comment_selector.setAccessibleName("Select task comment")
        edit_comment_text = self.QtWidgets.QLineEdit()
        edit_comment_text.setObjectName("OnboardingV2EditTaskCommentText")
        edit_comment_text.setPlaceholderText("Revised comment")
        edit_comment = self.QtWidgets.QPushButton("Edit comment")
        edit_comment.setObjectName("OnboardingV2EditTaskComment")
        edit_comment.setAccessibleName("Edit selected authored task comment")
        comment_edit.addWidget(comment_selector)
        comment_edit.addWidget(edit_comment_text, 1)
        comment_edit.addWidget(edit_comment)
        redact_comment = None
        redact_reason = None
        if self.service.access.role == "admin":
            redact_reason = self.QtWidgets.QLineEdit()
            redact_reason.setObjectName("OnboardingV2CommentRedactionReason")
            redact_reason.setPlaceholderText("Redaction reason")
            redact_comment = self.QtWidgets.QPushButton("Redact comment")
            redact_comment.setObjectName("OnboardingV2RedactTaskComment")
            redact_comment.setAccessibleName("Redact selected task comment with reason")
            comment_edit.addWidget(redact_reason)
            comment_edit.addWidget(redact_comment)
        drawer_layout.addLayout(comment_edit)
        comment_revisions = self.QtWidgets.QLabel("Select a comment to view revisions.")
        comment_revisions.setObjectName("OnboardingV2CommentRevisionHistory")
        comment_revisions.setAccessibleName("Selected task comment revision history")
        comment_revisions.setWordWrap(True)
        drawer_layout.addWidget(comment_revisions)
        attachment_table = self.QtWidgets.QTableWidget(0, 3)
        attachment_table.setObjectName("OnboardingV2TaskAttachments")
        attachment_table.setAccessibleName("Selected task encrypted attachments")
        attachment_table.setHorizontalHeaderLabels(["File", "Scan", "Size"])
        attachment_table.horizontalHeader().setStretchLastSection(True)
        drawer_layout.addWidget(attachment_table)
        attachment_actions = self.QtWidgets.QHBoxLayout()
        attach_file = self.QtWidgets.QPushButton("Attach file")
        attach_file.setObjectName("OnboardingV2AttachTaskFile")
        attach_file.setAccessibleName("Attach encrypted file to selected onboarding task")
        open_attachment = self.QtWidgets.QPushButton("Open attachment")
        open_attachment.setObjectName("OnboardingV2OpenTaskAttachment")
        open_attachment.setAccessibleName("Open selected encrypted task attachment")
        attachment_actions.addWidget(attach_file)
        attachment_actions.addWidget(open_attachment)
        drawer_layout.addLayout(attachment_actions)
        audit_summary = self.QtWidgets.QLabel("No task audit events.")
        audit_summary.setObjectName("OnboardingV2TaskAuditSummary")
        audit_summary.setAccessibleName("Selected task audit summary")
        audit_summary.setWordWrap(True)
        drawer_layout.addWidget(audit_summary)
        open_temp_paths: list[Path] = []

        editor = self.QtWidgets.QWidget()
        editor.setObjectName("OnboardingV2TaskEditor")
        editor.setAccessibleName("Onboarding task editor")
        editor_layout = self.QtWidgets.QFormLayout(editor)
        edit_title = self.QtWidgets.QLineEdit()
        edit_title.setObjectName("OnboardingV2TaskEditTitle")
        edit_owner = self.QtWidgets.QComboBox()
        edit_owner.setObjectName("OnboardingV2TaskEditOwner")
        edit_watchers = self.QtWidgets.QLineEdit()
        edit_watchers.setObjectName("OnboardingV2TaskEditWatchers")
        edit_due_date = self.QtWidgets.QLineEdit()
        edit_due_date.setObjectName("OnboardingV2TaskEditDueDate")
        edit_critical = self.QtWidgets.QCheckBox("Critical")
        edit_critical.setObjectName("OnboardingV2TaskEditCritical")
        edit_notes = self.QtWidgets.QTextEdit()
        edit_notes.setObjectName("OnboardingV2TaskEditNotes")
        edit_notes.setMaximumHeight(90)
        save_task = self.QtWidgets.QPushButton("Save task")
        save_task.setObjectName("OnboardingV2SaveTask")
        save_task.setAccessibleName("Save onboarding task changes")
        editor_layout.addRow("Title", edit_title)
        editor_layout.addRow("Owner", edit_owner)
        editor_layout.addRow("Watchers", edit_watchers)
        editor_layout.addRow("Due date", edit_due_date)
        editor_layout.addRow("", edit_critical)
        editor_layout.addRow("Notes", edit_notes)
        editor_layout.addRow("", save_task)
        editor.hide()
        drawer_layout.addWidget(editor)

        def apply_filters() -> None:
            needle = search.text().strip().casefold()
            status = status_filter.currentText().replace("All statuses", "").strip().casefold()
            owner = owner_filter.currentText().replace("All owners", "").strip().casefold()
            watcher = watcher_filter.currentText().replace("All watchers", "").strip().casefold()
            school = school_filter.currentText().replace("All schools", "").strip().casefold()
            employee_id = str(employee_filter.currentData() or "")
            urgency = urgency_filter.currentText().replace("All urgency", "").strip().casefold().replace(" ", "_")
            blocked_choice = blocked_filter.currentText()
            due_start = due_from.text().strip()
            due_end = due_to.text().strip()
            today = date.today().isoformat()
            for row in range(table.rowCount()):
                text = " ".join(table.item(row, column).text() for column in range(table.columnCount())).casefold()
                row_status = table.item(row, 5).text().casefold()
                row_owner = table.item(row, 3).text().casefold()
                task_id = str(table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
                task = self.service.get_task(task_id)
                is_blocked = task.status == "blocked" or any(
                    self.service.get_task(dependency).status != "completed" for dependency in task.dependency_ids
                )
                urgency_mismatch = (
                    (urgency == "critical" and not task.critical)
                    or (urgency == "overdue" and not (task.due_date < today and task.status not in {"completed", "cancelled"}))
                    or (urgency == "due_today" and task.due_date != today)
                    or (urgency == "upcoming" and not (task.due_date > today and task.status not in {"completed", "cancelled"}))
                )
                hidden = bool(
                    (needle and not all(token in text for token in needle.split()))
                    or (status and status != row_status)
                    or (owner and owner != row_owner)
                    or (watcher and watcher not in {role.casefold() for role in task.watcher_roles})
                    or (school and school != task.school.casefold())
                    or (employee_id and employee_id != task.employee_id)
                    or (blocked_choice == "Blocked" and not is_blocked)
                    or (blocked_choice == "Unblocked" and is_blocked)
                    or urgency_mismatch
                    or (due_start and task.due_date < due_start)
                    or (due_end and task.due_date > due_end)
                )
                table.setRowHidden(row, hidden)

        def refresh() -> None:
            tasks = self.service.list_tasks()
            table.setSortingEnabled(False)
            table.setRowCount(len(tasks))
            for row_index, task in enumerate(tasks):
                employee = self.service.get_employee(task.employee_id)
                blocked = task.status == "blocked" or any(
                    self.service.get_task(dependency).status != "completed" for dependency in task.dependency_ids
                )
                urgency = "Blocked" if blocked else ("Critical" if task.critical else "Standard")
                values = [
                    employee.preferred_name or employee.legal_name,
                    task.school,
                    task.title,
                    task.owner_role,
                    task.due_date,
                    task.status.replace("_", " ").title(),
                    urgency,
                ]
                for column, value in enumerate(values):
                    item = self.QtWidgets.QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, task.id)
                    table.setItem(row_index, column, item)
            table.setSortingEnabled(True)
            while task_cards_layout.count():
                item = task_cards_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            for task in tasks:
                employee = self.service.get_employee(task.employee_id)
                card = self.QtWidgets.QPushButton(
                    f"{employee.preferred_name or employee.legal_name}\n{task.title}\n"
                    f"{task.owner_role} · {task.due_date} · {task.status.replace('_', ' ').title()}"
                )
                card.setObjectName("OnboardingV2TaskCard")
                card.setAccessibleName(f"{task.title}, {task.status}, due {task.due_date}")
                card.clicked.connect(lambda _checked=False, task_id=task.id: activate_task_card(task_id))
                task_cards_layout.addWidget(card)
            task_cards_layout.addStretch(1)
            apply_filters()

        def selected_task_id() -> str:
            row = table.currentRow()
            return "" if row < 0 or table.item(row, 0) is None else str(table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")

        def activate_task_card(task_id: str) -> None:
            for row in range(table.rowCount()):
                if str(table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "") == task_id:
                    table.selectRow(row)
                    show_detail()
                    return

        def create_top_level_task() -> None:
            try:
                self.service.create_task(
                    employee_id=str(create_task_employee.currentData() or ""),
                    title=create_task_title.text(), owner_role=create_task_owner.text(),
                    due_date=create_task_due.text(),
                )
            except (ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Task could not be created", str(exc))
                return
            create_task_title.clear()
            refresh()

        def show_detail() -> None:
            task_id = selected_task_id()
            if not task_id:
                drawer.hide()
                return
            task = self.service.get_task(task_id)
            dependency_tasks = [self.service.get_task(item) for item in task.dependency_ids]
            dependencies = ", ".join(
                f"{item.title} ({item.status.replace('_', ' ')})" for item in dependency_tasks
            ) or "None"
            blocked = any(item.status != "completed" for item in dependency_tasks)
            detail.setText(
                f"{task.title} · Status: {task.status.replace('_', ' ').title()}"
                f" · {'Blocked' if blocked else 'Actionable'}\n"
                f"Owner: {task.owner_role} · Watchers: {', '.join(task.watcher_roles) or 'None'}\n"
                f"Dependencies: {dependencies}\nNotes: {task.notes or 'None'}"
            )
            comments = self.service.list_task_comments(task.id)
            comment_history.setText(
                "\n".join(f"{comment.author}: {comment.body}" for comment in comments)
                or "No comments."
            )
            comment_selector.clear()
            for comment in comments:
                comment_selector.addItem(f"{comment.author} v{comment.version}", comment.id)
            subtasks = [item for item in self.service.list_tasks() if item.parent_task_id == task.id]
            subtask_table.setRowCount(len(subtasks))
            for row_index, subtask in enumerate(subtasks):
                for column, value in enumerate((subtask.title, "Yes" if subtask.required else "No", subtask.status.title())):
                    item = self.QtWidgets.QTableWidgetItem(value)
                    if column == 0:
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, subtask.id)
                    subtask_table.setItem(row_index, column, item)
            attachments = self.service.list_task_attachments(task.id)
            attachment_table.setRowCount(len(attachments))
            for row_index, attachment in enumerate(attachments):
                for column, value in enumerate((attachment.name, attachment.scan_status.title(), str(attachment.size_bytes))):
                    item = self.QtWidgets.QTableWidgetItem(value)
                    if column == 0:
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, attachment.id)
                    attachment_table.setItem(row_index, column, item)
            events = self.service.list_task_audit_events(task.id)
            audit_summary.setText("\n".join(str(event["action"]) for event in events[-5:]) or "No task audit events.")
            drawer.show()

        def add_selected_subtask() -> None:
            task_id = selected_task_id()
            if not task_id:
                return
            parent = self.service.get_task(task_id)
            try:
                self.service.create_task(
                    employee_id=parent.employee_id, title=new_subtask.text(),
                    owner_role=parent.owner_role, watcher_roles=list(parent.watcher_roles),
                    due_date=parent.due_date, parent_task_id=parent.id,
                    required=required_subtask.isChecked(),
                )
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Subtask could not be added", str(exc))
                return
            new_subtask.clear()
            refresh()
            show_detail()

        def attach_selected_file() -> None:
            task_id = selected_task_id()
            if not task_id:
                return
            selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
                page, "Attach onboarding file", "",
                "Allowed files (*.pdf *.docx *.xlsx *.csv *.txt *.png *.jpg *.jpeg)",
            )
            if not selected:
                return
            try:
                self.service.add_task_attachment(task_id, Path(selected))
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Attachment could not be added", str(exc))
                return
            show_detail()

        def open_selected_attachment() -> None:
            row = attachment_table.currentRow()
            if row < 0 or attachment_table.item(row, 0) is None:
                return
            attachment_id = str(attachment_table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
            try:
                opened = self.service.open_task_attachment(attachment_id)
                open_temp_paths.append(opened)
                self.file_opener(opened)
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Attachment could not be opened", str(exc))

        def cleanup_task_temps() -> None:
            for opened in tuple(open_temp_paths):
                self.service.close_task_attachment(opened)
                open_temp_paths.remove(opened)

        def close_task_drawer() -> None:
            if not self.request_navigation_away():
                return
            editor.hide()
            drawer.hide()

        def add_selected_comment() -> None:
            task_id = selected_task_id()
            if not task_id:
                return
            try:
                self.service.add_task_comment(task_id, body=new_comment.text())
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Comment could not be added", str(exc))
                return
            new_comment.clear()
            show_detail()

        def edit_selected_comment() -> None:
            comment_id = str(comment_selector.currentData() or "")
            if not comment_id:
                return
            try:
                self.service.edit_task_comment(comment_id, body=edit_comment_text.text())
            except (ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Comment could not be edited", str(exc))
                return
            edit_comment_text.clear()
            show_detail()

        def show_comment_revisions() -> None:
            comment_id = str(comment_selector.currentData() or "")
            if not comment_id:
                comment_revisions.setText("Select a comment to view revisions.")
                return
            revisions = self.service.list_task_comment_revisions(comment_id)
            comment_revisions.setText("\n".join(
                f"v{item.version} · {item.editor}: {item.body}" for item in revisions
            ))

        def redact_selected_comment() -> None:
            comment_id = str(comment_selector.currentData() or "")
            if not comment_id:
                return
            try:
                self.service.redact_task_comment(comment_id, reason=redact_reason.text())
            except (ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Comment could not be redacted", str(exc))
                return
            redact_reason.clear()
            show_detail()

        def complete_selected_subtask() -> None:
            row = subtask_table.currentRow()
            if row < 0 or subtask_table.item(row, 0) is None:
                return
            subtask_id = str(subtask_table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
            try:
                self.service.complete_task(subtask_id)
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Subtask cannot be completed", str(exc))
                return
            show_detail()

        def complete_selected() -> None:
            task_id = selected_task_id()
            if not task_id:
                return
            try:
                self.service.complete_task(task_id)
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Task cannot be completed", str(exc))
            refresh()
            show_detail()

        def edit_selected() -> None:
            task_id = selected_task_id()
            if not task_id:
                return
            task = self.service.get_task(task_id)
            edit_title.setText(task.title)
            edit_owner.clear()
            roles = [item.role for item in self.service.list_owner_roles(school=task.school)]
            if task.owner_role not in roles:
                roles.append(task.owner_role)
            edit_owner.addItems(sorted(set(roles), key=str.casefold))
            edit_owner.setCurrentText(task.owner_role)
            edit_watchers.setText(", ".join(task.watcher_roles))
            edit_due_date.setText(task.due_date)
            edit_critical.setChecked(task.critical)
            edit_notes.setPlainText(task.notes)
            editor.setProperty("task_id", task.id)
            editor.setProperty("task_version", task.version)
            editor.show()

        def save_selected() -> bool:
            task_id = str(editor.property("task_id") or "")
            if not task_id:
                return True
            try:
                updated = self.service.update_task(
                    task_id,
                    expected_version=int(editor.property("task_version")),
                    changes={
                        "title": edit_title.text(),
                        "owner_role": edit_owner.currentText(),
                        "watcher_roles": [value.strip() for value in edit_watchers.text().split(",") if value.strip()],
                        "due_date": edit_due_date.text(),
                        "critical": edit_critical.isChecked(),
                        "notes": edit_notes.toPlainText(),
                    },
                )
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Task could not be saved", str(exc))
                return False
            editor.setProperty("task_version", updated.version)
            editor.hide()
            self._dirty_sessions.discard("task_drawer")
            refresh()
            return True

        search.textChanged.connect(apply_filters)
        status_filter.currentTextChanged.connect(apply_filters)
        owner_filter.currentTextChanged.connect(apply_filters)
        watcher_filter.currentTextChanged.connect(apply_filters)
        school_filter.currentTextChanged.connect(apply_filters)
        employee_filter.currentIndexChanged.connect(apply_filters)
        urgency_filter.currentTextChanged.connect(apply_filters)
        blocked_filter.currentTextChanged.connect(apply_filters)
        due_from.editingFinished.connect(apply_filters)
        due_to.editingFinished.connect(apply_filters)
        table.itemSelectionChanged.connect(show_detail)
        refresh_button.clicked.connect(refresh)
        create_task_button.clicked.connect(create_top_level_task)
        complete_button.clicked.connect(complete_selected)
        edit_button.clicked.connect(edit_selected)
        save_task.clicked.connect(save_selected)
        add_comment.clicked.connect(add_selected_comment)
        add_subtask.clicked.connect(add_selected_subtask)
        complete_subtask.clicked.connect(complete_selected_subtask)
        attach_file.clicked.connect(attach_selected_file)
        open_attachment.clicked.connect(open_selected_attachment)
        close_drawer.clicked.connect(close_task_drawer)
        edit_comment.clicked.connect(edit_selected_comment)
        comment_selector.currentIndexChanged.connect(show_comment_revisions)
        if redact_comment is not None:
            redact_comment.clicked.connect(redact_selected_comment)
        self.register_edit_session(
            "task_drawer", save=save_selected, discard=lambda: editor.hide(), cleanup=cleanup_task_temps
        )
        for control in (edit_title, edit_watchers, edit_due_date):
            control.textEdited.connect(
                lambda _text: self.mark_dirty("task_drawer") if editor.isVisible() else None
            )
        edit_owner.currentTextChanged.connect(
            lambda _text: self.mark_dirty("task_drawer") if editor.isVisible() else None
        )
        edit_critical.toggled.connect(
            lambda _checked: self.mark_dirty("task_drawer") if editor.isVisible() else None
        )
        edit_notes.textChanged.connect(
            lambda: self.mark_dirty("task_drawer") if editor.isVisible() else None
        )
        requested = dict(self._task_filter_request)
        if requested.get("status"):
            status_filter.setCurrentText(requested["status"])
        if requested.get("blocked"):
            blocked_filter.setCurrentText(requested["blocked"])
        if requested.get("urgency"):
            urgency_filter.setCurrentText(requested["urgency"])
        refresh()
        self._install_responsive_switch(page, table, task_cards)
        return page

    def _build_overview_page(self) -> Any:
        page, layout = self._page("Onboarding Overview", "Health, blocked work, reminders, and next action.", "Overview")
        metrics = self.service.task_metrics(as_of=date.today().isoformat())
        summary = self.QtWidgets.QLabel(
            f"Open {metrics.open}  ·  Blocked {metrics.blocked}  ·  Blocked overdue {metrics.blocked_overdue}  ·  "
            f"Actionable overdue {metrics.actionable_overdue}  ·  Completed {metrics.completed}  ·  "
            f"Data revision {self.service.store.data_revision()}"
        )
        summary.setObjectName("OnboardingV2OverviewKpis")
        summary.setAccessibleName("Onboarding overview metrics")
        layout.addWidget(summary)
        kpis = self.QtWidgets.QHBoxLayout()
        kpi_specs = (
            ("Open", metrics.open, "OnboardingV2KpiOpen", {"status": "Open"}),
            ("Blocked", metrics.blocked, "OnboardingV2KpiBlocked", {"blocked": "Blocked"}),
            ("Blocked Overdue", metrics.blocked_overdue, "OnboardingV2KpiBlockedOverdue", {"blocked": "Blocked", "urgency": "Overdue"}),
            ("Actionable Overdue", metrics.actionable_overdue, "OnboardingV2KpiActionableOverdue", {"blocked": "Unblocked", "urgency": "Overdue"}),
            ("Completed", metrics.completed, "OnboardingV2KpiCompleted", {"status": "Completed"}),
        )

        def open_filtered_tasks(filters: dict[str, str]) -> None:
            self._task_filter_request = dict(filters)
            if self.dashboard is not None:
                self.dashboard.show_external_page("onboarding_tasks")

        for label, value, object_name, request in kpi_specs:
            button = self.QtWidgets.QPushButton(f"{label}\n{value}")
            button.setObjectName(object_name)
            button.setAccessibleName(f"View {value} {label.casefold()} onboarding tasks")
            button.clicked.connect(lambda _checked=False, filters=request: open_filtered_tasks(filters))
            kpis.addWidget(button)
        layout.addLayout(kpis)
        health = self.service.scheduler_health()
        history = self.service.list_reminder_run_history(limit=5)
        next_action = (
            "Resolve blocked overdue work" if metrics.blocked_overdue
            else "Complete actionable overdue work" if metrics.actionable_overdue
            else "Review open onboarding work" if metrics.open
            else "No onboarding action required"
        )
        operational = self.QtWidgets.QLabel(
            f"Next action: {next_action}. Reminder scheduler: "
            f"{health.get('state', 'never run') if health else 'never run'}. "
            f"Recent outcomes: {len(history)}. Sync: {'enabled' if self.service.sync is not None else 'local only'}. "
            f"Vault: {'locked' if self.service.onboarding_locked else 'unlocked'}."
        )
        operational.setObjectName("OnboardingV2OperationalHealth")
        operational.setAccessibleName("Onboarding operational health and next recommended action")
        operational.setWordWrap(True)
        layout.addWidget(operational)
        sync_health = self.service.sync_health()
        conflicts = self.service.list_sync_conflicts()
        sync_detail = self.QtWidgets.QLabel(
            "Sync health: local only."
            if sync_health is None else
            f"Sync health: {sync_health.state}. Issues: {sync_health.issue_count}. "
            f"Deferred conflicts: {len(conflicts)}. "
            f"Categories: {', '.join(sync_health.issue_categories) or 'none'}."
        )
        sync_detail.setObjectName("OnboardingV2SyncHealth")
        sync_detail.setWordWrap(True)
        layout.addWidget(sync_detail)
        density_toggle = self.QtWidgets.QPushButton(
            f"Density: {self.density.title()}"
        )
        density_toggle.setObjectName("OnboardingV2DensityToggle")
        density_toggle.setAccessibleName("Toggle onboarding display density")

        def toggle_density() -> None:
            self.density = "comfortable" if self.density == "compact" else "compact"
            self.settings.setValue("onboarding/v2/density", self.density)
            density_toggle.setText(f"Density: {self.density.title()}")
            for built_page in self._built_pages:
                self._apply_density(built_page)

        density_toggle.clicked.connect(toggle_density)
        layout.addWidget(density_toggle)
        view_tasks = self.QtWidgets.QPushButton("View task queue")
        view_tasks.setObjectName("OnboardingV2ViewTasks")
        view_tasks.setAccessibleName("Open onboarding task queue")
        view_tasks.clicked.connect(
            lambda: self.dashboard.show_external_page("onboarding_tasks") if self.dashboard is not None else None
        )
        layout.addWidget(view_tasks)
        security_actions = self.QtWidgets.QHBoxLayout()
        lock_onboarding = self.QtWidgets.QPushButton("Lock Onboarding")
        lock_onboarding.setObjectName("OnboardingV2LockOnboarding")
        lock_onboarding.setAccessibleName("Lock onboarding until application restart")
        forget_device = self.QtWidgets.QPushButton("Forget This Device")
        forget_device.setObjectName("OnboardingV2ForgetDevice")
        forget_device.setAccessibleName("Forget onboarding encryption key on this Windows device")
        security_actions.addWidget(lock_onboarding)
        security_actions.addWidget(forget_device)
        security_actions.addStretch(1)
        layout.addLayout(security_actions)

        def lock_now() -> None:
            self._remask_sensitive_values()
            self.service.lock_onboarding()
            self.QtWidgets.QMessageBox.information(
                page,
                "Onboarding Locked",
                "Onboarding is locked until the application restarts.",
            )

        def forget_now() -> None:
            answer = self.QtWidgets.QMessageBox.question(
                page,
                "Forget This Device",
                "Remove this Windows device key cache and lock Onboarding? Organization passphrase will be required next time.",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self._remask_sensitive_values()
            self.service.forget_device()
            self.QtWidgets.QMessageBox.information(
                page,
                "Device Forgotten",
                "Device key cache removed. Restart application to unlock with organization passphrase.",
            )

        lock_onboarding.clicked.connect(lock_now)
        forget_device.clicked.connect(forget_now)
        layout.addStretch(1)
        return page

    def _build_employees_page(self) -> Any:
        page, layout = self._page("Onboarding Employees", "Secure employee lifecycle and progress roster.", "Employees")
        employees = self.service.list_employees()
        create_form = self.QtWidgets.QHBoxLayout()
        create_name = self.QtWidgets.QLineEdit()
        create_name.setObjectName("OnboardingV2CreateEmployeeName")
        create_name.setPlaceholderText("Legal name")
        create_role = self.QtWidgets.QLineEdit()
        create_role.setObjectName("OnboardingV2CreateEmployeeRole")
        create_role.setPlaceholderText("Role")
        create_school = self.QtWidgets.QComboBox()
        create_school.setObjectName("OnboardingV2CreateEmployeeSchool")
        if self.service.access.role == "director":
            create_school.addItem(self.service.access.school_scope)
            create_school.setEnabled(False)
        else:
            create_school.addItems(["Palmdale", "Hawthorne", "North Long Beach"])
        create_acceptance = self.QtWidgets.QLineEdit()
        create_acceptance.setObjectName("OnboardingV2CreateEmployeeAcceptanceDate")
        create_acceptance.setPlaceholderText("Accepted YYYY-MM-DD")
        create_start = self.QtWidgets.QLineEdit()
        create_start.setObjectName("OnboardingV2CreateEmployeeStartDate")
        create_start.setPlaceholderText("Starts YYYY-MM-DD")
        create_employee_button = self.QtWidgets.QPushButton("Create employee")
        create_employee_button.setObjectName("OnboardingV2CreateEmployee")
        create_employee_button.setAccessibleName("Create authorized onboarding employee")
        for control in (create_name, create_role, create_school, create_acceptance, create_start, create_employee_button):
            create_form.addWidget(control)
        layout.addLayout(create_form)
        table = self.QtWidgets.QTableWidget(len(employees), 5)
        table.setObjectName("OnboardingV2EmployeeRoster")
        table.setAccessibleName("Onboarding employee roster")
        table.setHorizontalHeaderLabels(["Employee", "School", "Role", "Start Date", "Status"])
        for row_index, employee in enumerate(employees):
            values = [employee.preferred_name or employee.legal_name, employee.school, employee.role, employee.start_date, employee.status]
            for column, value in enumerate(values):
                table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(str(value)))
        table.setSortingEnabled(True)
        table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        employee_search = self.QtWidgets.QLineEdit()
        employee_search.setObjectName("OnboardingV2EmployeeSearch")
        employee_search.setAccessibleName("Search onboarding employees")
        employee_search.setPlaceholderText("Search employee, school, role, or status")
        layout.addWidget(employee_search)
        layout.addWidget(table, 1)
        employee_cards = self.QtWidgets.QWidget()
        employee_cards.setObjectName("OnboardingV2EmployeeCards")
        employee_cards.setAccessibleName("Onboarding employees stacked card view")
        employee_cards_layout = self.QtWidgets.QVBoxLayout(employee_cards)
        for employee in employees:
            card = self.QtWidgets.QPushButton(
                f"{employee.preferred_name or employee.legal_name}\n{employee.school} · {employee.role}\n"
                f"Start {employee.start_date} · {employee.status.title()}"
            )
            card.setObjectName("OnboardingV2EmployeeCard")
            card.clicked.connect(lambda _checked=False, employee_id=employee.id: activate_employee_card(employee_id))
            employee_cards_layout.addWidget(card)
        employee_cards_layout.addStretch(1)
        employee_cards.hide()
        layout.addWidget(employee_cards, 1)
        employee_search.textChanged.connect(lambda text: self._filter_table(table, text))
        for row_index, employee in enumerate(employees):
            table.item(row_index, 0).setData(self.QtCore.Qt.ItemDataRole.UserRole, employee.id)
        profile = self.QtWidgets.QWidget()
        profile.setObjectName("OnboardingV2EmployeeProfileDrawer")
        profile.setAccessibleName("Selected employee secure profile")
        profile_form = self.QtWidgets.QFormLayout(profile)
        profile_controls: dict[str, Any] = {}
        for field_name, label, object_name in (
            ("legal_name", "Legal name", "OnboardingV2EmployeeLegalName"),
            ("preferred_name", "Preferred name", "OnboardingV2EmployeePreferredName"),
            ("role", "Role", "OnboardingV2EmployeeRole"),
            ("acceptance_date", "Acceptance date", "OnboardingV2EmployeeAcceptanceDate"),
            ("start_date", "Start date", "OnboardingV2EmployeeStartDate"),
            ("personal_email", "Personal email", "OnboardingV2EmployeePersonalEmail"),
            ("work_email", "Work email", "OnboardingV2EmployeeWorkEmail"),
            ("phone", "Phone", "OnboardingV2EmployeePhone"),
            ("address_line1", "Address", "OnboardingV2EmployeeAddress"),
            ("city", "City", "OnboardingV2EmployeeCity"),
            ("state", "State", "OnboardingV2EmployeeState"),
            ("postal_code", "Postal code", "OnboardingV2EmployeePostalCode"),
        ):
            control = self.QtWidgets.QLineEdit()
            control.setObjectName(object_name)
            control.setAccessibleName(label)
            profile_controls[field_name] = control
            profile_form.addRow(label, control)
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("OnboardingV2EmployeeNotes")
        notes.setAccessibleName("Employee secure notes")
        notes.setMaximumHeight(90)
        save_profile = self.QtWidgets.QPushButton("Save profile")
        save_profile.setObjectName("OnboardingV2SaveEmployeeProfile")
        save_profile.setAccessibleName("Save selected onboarding employee profile")
        profile_form.addRow("Notes", notes)
        profile_form.addRow("", save_profile)
        progress = self.QtWidgets.QLabel("Select an employee")
        progress.setObjectName("OnboardingV2EmployeeProgress")
        progress.setWordWrap(True)
        director_attribution = self.QtWidgets.QLabel("Current Director: unavailable")
        director_attribution.setObjectName("OnboardingV2EmployeeDirectorAttribution")
        director_attribution.setWordWrap(True)
        employee_audit = self.QtWidgets.QLabel("No employee audit events.")
        employee_audit.setObjectName("OnboardingV2EmployeeAuditSummary")
        employee_audit.setWordWrap(True)
        profile_form.addRow("Progress", progress)
        profile_form.addRow("Director", director_attribution)
        profile_form.addRow("Audit", employee_audit)
        artifact_table = self.QtWidgets.QTableWidget(0, 4)
        artifact_table.setObjectName("OnboardingV2EmployeeFilledArtifacts")
        artifact_table.setHorizontalHeaderLabels(["Kind", "Created", "Package", "ID"])
        artifact_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        artifact_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        open_artifact = self.QtWidgets.QPushButton("Open filled package")
        open_artifact.setObjectName("OnboardingV2OpenFilledArtifact")
        export_artifact = self.QtWidgets.QPushButton("Export filled package")
        export_artifact.setObjectName("OnboardingV2ExportFilledArtifact")
        artifact_actions = self.QtWidgets.QHBoxLayout()
        artifact_actions.addWidget(open_artifact)
        artifact_actions.addWidget(export_artifact)
        profile_form.addRow("Filled packages", artifact_table)
        profile_form.addRow("", artifact_actions)
        did_not_start_reason = self.QtWidgets.QComboBox()
        did_not_start_reason.setObjectName("OnboardingV2DidNotStartReason")
        did_not_start_reason.addItems(["candidate_withdrew", "offer_rescinded", "no_show", "other"])
        did_not_start_notes = self.QtWidgets.QLineEdit()
        did_not_start_notes.setObjectName("OnboardingV2DidNotStartNotes")
        mark_did_not_start = self.QtWidgets.QPushButton("Did Not Start")
        mark_did_not_start.setObjectName("OnboardingV2MarkDidNotStart")
        mark_did_not_start.setAccessibleName("Archive selected employee as Did Not Start")
        profile_form.addRow("Did Not Start reason", did_not_start_reason)
        profile_form.addRow("Did Not Start notes", did_not_start_notes)
        profile_form.addRow("", mark_did_not_start)
        final_day = self.QtWidgets.QLineEdit()
        final_day.setObjectName("OnboardingV2LastWorkingDay")
        final_day.setPlaceholderText("YYYY-MM-DD")
        departure_category = self.QtWidgets.QComboBox()
        departure_category.setObjectName("OnboardingV2DepartureCategory")
        departure_category.addItems([
            "voluntary_resignation", "involuntary_termination", "job_abandonment",
            "transfer", "end_of_temporary_or_contract_role", "other",
        ])
        departure_director_id = self.QtWidgets.QLineEdit()
        departure_director_id.setObjectName("OnboardingV2DepartureDirectorId")
        departure_director_name = self.QtWidgets.QLineEdit()
        departure_director_name.setObjectName("OnboardingV2DepartureDirectorName")
        mark_ended = self.QtWidgets.QPushButton("Mark Employment Ended")
        mark_ended.setObjectName("OnboardingV2MarkEmploymentEnded")
        mark_ended.setAccessibleName("Mark selected employee employment ended")
        profile_form.addRow("Last working day", final_day)
        profile_form.addRow("Departure category", departure_category)
        profile_form.addRow("Departure Director ID", departure_director_id)
        profile_form.addRow("Departure Director name", departure_director_name)
        profile_form.addRow("", mark_ended)
        transfer_school = archive_reason = delete_confirmation = purge_as_of = None
        transfer_employee = archive_employee = delete_employee = preview_purge = purge_employee = None
        if self.service.access.role == "admin":
            transfer_school = self.QtWidgets.QComboBox()
            transfer_school.setObjectName("OnboardingV2TransferSchool")
            transfer_school.addItems(["Palmdale", "Hawthorne", "North Long Beach"])
            transfer_employee = self.QtWidgets.QPushButton("Transfer employee")
            transfer_employee.setObjectName("OnboardingV2TransferEmployee")
            archive_reason = self.QtWidgets.QComboBox()
            archive_reason.setObjectName("OnboardingV2ArchiveCorrectionReason")
            archive_reason.addItems(["duplicate", "test_record", "cancelled_before_start"])
            archive_employee = self.QtWidgets.QPushButton("Archive correction")
            archive_employee.setObjectName("OnboardingV2ArchiveEmployee")
            delete_confirmation = self.QtWidgets.QLineEdit()
            delete_confirmation.setObjectName("OnboardingV2DeleteConfirmation")
            delete_confirmation.setPlaceholderText("DELETE employee-uuid")
            delete_employee = self.QtWidgets.QPushButton("Permanently delete")
            delete_employee.setObjectName("OnboardingV2DeleteEmployee")
            purge_as_of = self.QtWidgets.QLineEdit(date.today().isoformat())
            purge_as_of.setObjectName("OnboardingV2PurgeAsOf")
            preview_purge = self.QtWidgets.QPushButton("Preview retention purge")
            preview_purge.setObjectName("OnboardingV2PreviewRetentionPurge")
            purge_employee = self.QtWidgets.QPushButton("Purge selected eligible employee")
            purge_employee.setObjectName("OnboardingV2PurgeEmployee")
            profile_form.addRow("Transfer school", transfer_school)
            profile_form.addRow("", transfer_employee)
            profile_form.addRow("Correction reason", archive_reason)
            profile_form.addRow("", archive_employee)
            profile_form.addRow("Delete confirmation", delete_confirmation)
            profile_form.addRow("", delete_employee)
            profile_form.addRow("Purge as of", purge_as_of)
            profile_form.addRow("", preview_purge)
            profile_form.addRow("", purge_employee)
        profile.hide()
        layout.addWidget(profile)
        secure = self.QtWidgets.QHBoxLayout()
        ssn_value = self.QtWidgets.QLabel("Select an employee")
        ssn_value.setObjectName("OnboardingV2SsnValue")
        ssn_value.setAccessibleName("Masked employee Social Security number")
        reveal = self.QtWidgets.QPushButton("Reveal SSN")
        reveal.setObjectName("OnboardingV2RevealSsn")
        reveal.setAccessibleName("Reveal selected employee Social Security number for 60 seconds")
        self._sensitive_labels.append(ssn_value)
        secure.addWidget(ssn_value)
        secure.addWidget(reveal)
        secure.addStretch(1)
        layout.addLayout(secure)

        def selected_employee_id() -> str:
            row = table.currentRow()
            return "" if row < 0 or table.item(row, 0) is None else str(table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")

        def activate_employee_card(employee_id: str) -> None:
            for row in range(table.rowCount()):
                if str(table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or "") == employee_id:
                    table.selectRow(row)
                    show_profile()
                    return

        def create_employee_record() -> None:
            try:
                employee = self.service.create_employee(
                    legal_name=create_name.text(), school=create_school.currentText(),
                    role=create_role.text(), acceptance_date=create_acceptance.text(),
                    start_date=create_start.text(),
                )
            except (ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Employee could not be created", str(exc))
                return
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate((employee.legal_name, employee.school, employee.role, employee.start_date, employee.status)):
                item = self.QtWidgets.QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(self.QtCore.Qt.ItemDataRole.UserRole, employee.id)
                table.setItem(row, column, item)
            card = self.QtWidgets.QPushButton(
                f"{employee.legal_name}\n{employee.school} · {employee.role}\n"
                f"Start {employee.start_date} · {employee.status.title()}"
            )
            card.setObjectName("OnboardingV2EmployeeCard")
            card.clicked.connect(lambda _checked=False, employee_id=employee.id: activate_employee_card(employee_id))
            employee_cards_layout.insertWidget(max(0, employee_cards_layout.count() - 1), card)
            create_name.clear()
            create_role.clear()
            create_acceptance.clear()
            create_start.clear()

        def mask_selected() -> None:
            employee_id = selected_employee_id()
            ssn_value.setText(self.service.masked_ssn(employee_id) if employee_id else "Select an employee")

        def show_profile() -> None:
            employee_id = selected_employee_id()
            if not employee_id:
                profile.hide()
                return
            employee = self.service.get_employee(employee_id)
            profile.setProperty("loading", True)
            for field_name, control in profile_controls.items():
                control.setText(str(getattr(employee, field_name)))
            notes.setPlainText(employee.notes)
            profile.setProperty("employee_id", employee.id)
            profile.setProperty("employee_version", employee.version)
            tasks = [task for task in self.service.list_tasks() if task.employee_id == employee.id]
            completed = sum(task.status == "completed" for task in tasks)
            packages = len({task.package_version_id for task in tasks if task.package_version_id})
            progress.setText(f"Tasks {completed}/{len(tasks)} completed · Packages assigned {packages}")
            try:
                current = self.service.current_director(employee.id)
                director_attribution.setText(
                    f"Current: {current.name} ({current.person_id}) · Hiring: {employee.hiring_director_name or 'Unknown'} · "
                    f"Departure: {employee.departure_director_name or 'Not recorded'}"
                )
            except ValueError as exc:
                director_attribution.setText(f"Current Director unavailable: {exc}")
            audits = self.service.list_employee_audit_events(employee.id)
            employee_audit.setText("\n".join(str(event["action"]) for event in audits[-5:]) or "No employee audit events.")
            artifacts = self.service.list_employee_filled_artifacts(employee.id)
            artifact_table.setRowCount(len(artifacts))
            for artifact_row, artifact in enumerate(artifacts):
                for column, value in enumerate((artifact.kind, artifact.created_at, artifact.package_version_id, artifact.id)):
                    item = self.QtWidgets.QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, artifact.id)
                    artifact_table.setItem(artifact_row, column, item)
            profile.setProperty("loading", False)
            profile.show()

        def selected_artifact_id() -> str:
            row = artifact_table.currentRow()
            item = artifact_table.item(row, 0) if row >= 0 else None
            return "" if item is None else str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or "")

        def open_selected_artifact() -> None:
            artifact_id = selected_artifact_id()
            if not artifact_id:
                return
            answer = self.QtWidgets.QMessageBox.question(
                page, "Open sensitive package",
                "This package contains sensitive employee data. Open authorized temporary copy?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                artifact = next(
                    item for item in self.service.list_employee_filled_artifacts(selected_employee_id())
                    if item.id == artifact_id
                )
                opened = self.service.open_filled_artifact(
                    employee_id=selected_employee_id(), artifact_id=artifact_id,
                    suffix=artifact.suffix,
                )
                self.file_opener(opened)
            except (OSError, StopIteration, ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Package could not be opened", str(exc))

        def export_selected_artifact() -> None:
            artifact_id = selected_artifact_id()
            if not artifact_id:
                return
            target, _selected_filter = self.QtWidgets.QFileDialog.getSaveFileName(
                page, "Export sensitive package", "", "PDF files (*.pdf);;JSON files (*.json)"
            )
            if not target:
                return
            answer = self.QtWidgets.QMessageBox.question(
                page, "Export sensitive package",
                "Export creates a decrypted copy containing sensitive employee data. Continue?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                self.service.export_filled_artifact(
                    employee_id=selected_employee_id(), artifact_id=artifact_id,
                    destination=Path(target), confirmed_sensitive=True,
                )
            except (OSError, ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Package could not be exported", str(exc))

        def run_lifecycle(action: Callable[[str], Any]) -> None:
            employee_id = selected_employee_id()
            if not employee_id:
                return
            try:
                action(employee_id)
            except (OSError, ValueError, PermissionError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Employee action failed", str(exc))
                return
            row = table.currentRow()
            remaining = {item.id: item for item in self.service.list_employees()}
            if employee_id not in remaining:
                if row >= 0:
                    table.removeRow(row)
                profile.hide()
                return
            employee = remaining[employee_id]
            if row >= 0:
                table.item(row, 1).setText(employee.school)
                table.item(row, 4).setText(employee.status)
            show_profile()

        def confirm_lifecycle(title: str, text: str, action: Callable[[], None]) -> None:
            answer = self.QtWidgets.QMessageBox.question(
                page, title, text,
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer == self.QtWidgets.QMessageBox.StandardButton.Yes:
                action()

        def did_not_start_selected() -> None:
            confirm_lifecycle("Confirm Did Not Start", "Cancel remaining tasks and archive this employee?", lambda: run_lifecycle(
                lambda employee_id: self.service.mark_did_not_start(
                    employee_id, reason=did_not_start_reason.currentText(), notes=did_not_start_notes.text()
                )
            ))

        def end_selected_employment() -> None:
            confirm_lifecycle("Confirm Employment End", "Cancel remaining tasks and archive this employee?", lambda: run_lifecycle(
                lambda employee_id: self.service.mark_employment_ended(
                    employee_id, last_working_day=final_day.text(),
                    departure_category=departure_category.currentText(),
                    departure_director_id=departure_director_id.text(),
                    departure_director_name=departure_director_name.text(), notes=notes.toPlainText(),
                )
            ))

        def save_selected_profile() -> bool:
            employee_id = str(profile.property("employee_id") or "")
            if not employee_id:
                return True
            changes = {name: control.text() for name, control in profile_controls.items()}
            changes["notes"] = notes.toPlainText()
            try:
                updated = self.service.update_employee(
                    employee_id,
                    expected_version=int(profile.property("employee_version")),
                    changes=changes,
                )
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Profile could not be saved", str(exc))
                return False
            profile.setProperty("employee_version", updated.version)
            row = table.currentRow()
            if row >= 0:
                table.item(row, 0).setText(updated.preferred_name or updated.legal_name)
                table.item(row, 2).setText(updated.role)
                table.item(row, 3).setText(updated.start_date)
            self._dirty = False
            self._dirty_sessions.discard("employee_profile")
            return True

        def reveal_selected() -> None:
            employee_id = selected_employee_id()
            if not employee_id:
                return
            reason, accepted = self.QtWidgets.QInputDialog.getText(page, "Reveal SSN", "Business reason:")
            if not accepted:
                return
            ssn_value.setText(self.service.reveal_ssn(employee_id, reason=reason))
            self.QtCore.QTimer.singleShot(60_000, mask_selected)

        table.itemSelectionChanged.connect(mask_selected)
        table.itemSelectionChanged.connect(show_profile)
        create_employee_button.clicked.connect(create_employee_record)
        reveal.clicked.connect(reveal_selected)
        save_profile.clicked.connect(save_selected_profile)
        open_artifact.clicked.connect(open_selected_artifact)
        export_artifact.clicked.connect(export_selected_artifact)
        mark_did_not_start.clicked.connect(did_not_start_selected)
        mark_ended.clicked.connect(end_selected_employment)
        if self.service.access.role == "admin":
            transfer_employee.clicked.connect(lambda: confirm_lifecycle(
                "Confirm Transfer", f"Transfer this employee to {transfer_school.currentText()}?",
                lambda: run_lifecycle(lambda employee_id: self.service.transfer_employee(
                    employee_id, new_school=transfer_school.currentText()
                )),
            ))
            archive_employee.clicked.connect(lambda: confirm_lifecycle(
                "Confirm Correction Archive", "Archive this correction record?",
                lambda: run_lifecycle(lambda employee_id: self.service.archive_correction(
                    employee_id, reason=archive_reason.currentText()
                )),
            ))
            delete_employee.clicked.connect(lambda: confirm_lifecycle(
                "Final Permanent Delete", "Permanently delete this typed-confirmed employee record? This cannot be undone.",
                lambda: run_lifecycle(lambda employee_id: self.service.permanently_delete_employee(
                    employee_id, confirmation=delete_confirmation.text()
                )),
            ))

            def show_purge_preview() -> None:
                candidates = self.service.preview_retention_purge(as_of=purge_as_of.text())
                self.QtWidgets.QMessageBox.information(
                    page, "Retention purge preview", f"Eligible employees: {len(candidates)}"
                )

            preview_purge.clicked.connect(show_purge_preview)
            purge_employee.clicked.connect(lambda: confirm_lifecycle(
                "Confirm Retention Purge", "Permanently purge eligible employee PII and files?",
                lambda: run_lifecycle(lambda employee_id: self.service.purge_retained_employee(
                    employee_id, as_of=purge_as_of.text(), confirmation=f"PURGE {employee_id}"
                )),
            ))
        self.register_edit_session(
            "employee_profile", save=save_selected_profile, discard=show_profile,
            cleanup=self._remask_sensitive_values,
        )
        for control in profile_controls.values():
            control.textEdited.connect(lambda _text: self.mark_dirty("employee_profile"))
        notes.textChanged.connect(
            lambda: self.mark_dirty("employee_profile")
            if profile.isVisible() and not bool(profile.property("loading")) else None
        )
        if not employees:
            self._add_empty_state(layout, "No employees in this authorized scope.")
        self._install_responsive_switch(page, table, employee_cards)
        return page

    def _build_templates_page(self) -> Any:
        if self.service.access.role != "admin":
            raise PermissionError("Onboarding Templates are admin-only.")
        page, layout = self._page("Onboarding Templates", "Checklist versions, document packages, fields, and PDF mapping.", "Templates")
        summary = self.QtWidgets.QLabel()
        summary.setObjectName("OnboardingV2TemplateSummary")
        summary.setWordWrap(True)
        migration_path = self.QtWidgets.QLineEdit()
        migration_path.setObjectName("OnboardingV2LegacyMigrationPath")
        migration_path.setReadOnly(True)
        select_migration = self.QtWidgets.QPushButton("Select legacy JSON")
        select_migration.setObjectName("OnboardingV2SelectLegacyMigration")
        preview_migration = self.QtWidgets.QPushButton("Preview legacy migration")
        preview_migration.setObjectName("OnboardingV2PreviewLegacyMigration")
        migration_confirmation = self.QtWidgets.QLineEdit()
        migration_confirmation.setObjectName("OnboardingV2LegacyMigrationConfirmation")
        migration_confirmation.setPlaceholderText("Type IMPORT after reviewing preview")
        import_migration = self.QtWidgets.QPushButton("Import legacy data")
        import_migration.setObjectName("OnboardingV2ImportLegacyMigration")
        migration_status = self.QtWidgets.QLabel("Select a legacy onboarding JSON file.")
        migration_status.setObjectName("OnboardingV2LegacyMigrationStatus")
        migration_status.setWordWrap(True)
        migration_preview: dict[str, Any] = {}
        template_table = self.QtWidgets.QTableWidget(0, 7)
        template_table.setObjectName("OnboardingV2TaskTemplateVersions")
        template_table.setAccessibleName("Onboarding task template versions")
        template_table.setHorizontalHeaderLabels(
            ["Key", "Title", "School", "Version", "Status", "Owner", "Package"]
        )
        package_table = self.QtWidgets.QTableWidget(0, 6)
        package_table.setObjectName("OnboardingV2PackageVersions")
        package_table.setAccessibleName("Onboarding document package versions")
        package_table.setHorizontalHeaderLabels(
            ["Key", "Title", "School", "Version", "Status", "Documents"]
        )
        field_table = self.QtWidgets.QTableWidget(0, 5)
        field_table.setObjectName("OnboardingV2FieldLibrary")
        field_table.setAccessibleName("Reusable onboarding intake field library")
        field_table.setHorizontalHeaderLabels(["Stable ID", "Label", "Type", "Sensitivity", "State"])
        for table in (template_table, package_table, field_table):
            table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)

        field_form = self.QtWidgets.QFormLayout()
        field_stable_id = self.QtWidgets.QLineEdit()
        field_stable_id.setObjectName("OnboardingV2FieldStableId")
        field_stable_id.setAccessibleName("Stable intake field ID")
        field_label = self.QtWidgets.QLineEdit()
        field_label.setObjectName("OnboardingV2FieldLabel")
        field_label.setAccessibleName("Intake field label")
        field_type = self.QtWidgets.QComboBox()
        field_type.setObjectName("OnboardingV2FieldType")
        field_type.addItems([
            "short_text", "long_text", "date", "email", "phone", "ssn", "number",
            "yes_no", "single_choice", "multiple_choice", "signature", "initials",
        ])
        field_sensitivity = self.QtWidgets.QComboBox()
        field_sensitivity.setObjectName("OnboardingV2FieldSensitivity")
        field_sensitivity.addItems(["personal", "sensitive", "standard"])
        field_aliases = self.QtWidgets.QLineEdit()
        field_aliases.setObjectName("OnboardingV2FieldAliases")
        field_aliases.setPlaceholderText("Comma-separated aliases")
        create_field = self.QtWidgets.QPushButton("Create reusable field")
        create_field.setObjectName("OnboardingV2CreateField")
        create_field.setAccessibleName("Create reusable onboarding intake field")
        field_form.addRow("Stable ID", field_stable_id)
        field_form.addRow("Label", field_label)
        field_form.addRow("Type", field_type)
        field_form.addRow("Sensitivity", field_sensitivity)
        field_form.addRow("Aliases", field_aliases)
        field_form.addRow("", create_field)
        template_form = self.QtWidgets.QFormLayout()
        template_key = self.QtWidgets.QLineEdit()
        template_key.setObjectName("OnboardingV2TemplateKey")
        template_title = self.QtWidgets.QLineEdit()
        template_title.setObjectName("OnboardingV2TemplateTitle")
        template_school = self.QtWidgets.QComboBox()
        template_school.setObjectName("OnboardingV2TemplateSchool")
        template_school.addItems(["*", "Palmdale", "Hawthorne", "North Long Beach"])
        template_owner = self.QtWidgets.QComboBox()
        template_owner.setObjectName("OnboardingV2TemplateOwner")
        template_owner.addItems(["Director", "Office Manager", "Payroll", "Benefits", "IT"])
        template_due = self.QtWidgets.QSpinBox()
        template_due.setObjectName("OnboardingV2TemplateDueOffset")
        template_due.setRange(-365, 365)
        template_package_key = self.QtWidgets.QLineEdit()
        template_package_key.setObjectName("OnboardingV2TemplatePackageKey")
        template_watchers = self.QtWidgets.QLineEdit()
        template_watchers.setObjectName("OnboardingV2TemplateWatchers")
        template_watchers.setPlaceholderText("Comma-separated watcher roles")
        template_content = self.QtWidgets.QTextEdit()
        template_content.setObjectName("OnboardingV2TemplateContent")
        template_content.setMaximumHeight(90)
        template_critical = self.QtWidgets.QCheckBox("Critical")
        template_critical.setObjectName("OnboardingV2TemplateCritical")
        template_base_id = self.QtWidgets.QLineEdit()
        template_base_id.setObjectName("OnboardingV2TemplateBaseId")
        template_base_id.setPlaceholderText("Published global template ID")
        template_override_fields = self.QtWidgets.QLineEdit()
        template_override_fields.setObjectName("OnboardingV2TemplateOverrideFields")
        template_override_fields.setPlaceholderText("Fields overridden for school")
        create_template = self.QtWidgets.QPushButton("Create template draft")
        create_template.setObjectName("OnboardingV2CreateTemplateDraft")
        publish_template = self.QtWidgets.QPushButton("Publish selected template")
        publish_template.setObjectName("OnboardingV2PublishTemplate")
        deprecate_template = self.QtWidgets.QPushButton("Deprecate selected template")
        deprecate_template.setObjectName("OnboardingV2DeprecateTemplate")
        template_attachments = self.QtWidgets.QListWidget()
        template_attachments.setObjectName("OnboardingV2TemplateAttachments")
        add_template_attachment = self.QtWidgets.QPushButton("Add attachment to selected draft")
        add_template_attachment.setObjectName("OnboardingV2AddTemplateAttachment")
        template_form.addRow("Template key", template_key)
        template_form.addRow("Title", template_title)
        template_form.addRow("School", template_school)
        template_form.addRow("Owner", template_owner)
        template_form.addRow("Due offset days", template_due)
        template_form.addRow("Package key", template_package_key)
        template_form.addRow("Watchers", template_watchers)
        template_form.addRow("Instructions", template_content)
        template_form.addRow("", template_critical)
        template_form.addRow("Global base", template_base_id)
        template_form.addRow("Override fields", template_override_fields)
        template_form.addRow("Attachments", template_attachments)
        template_form.addRow("", add_template_attachment)
        template_form.addRow("", create_template)
        template_form.addRow("", publish_template)
        template_form.addRow("", deprecate_template)

        package_editor = DocumentPackageDraftEditor()
        package_form = self.QtWidgets.QFormLayout()
        package_key = self.QtWidgets.QLineEdit()
        package_key.setObjectName("OnboardingV2PackageKey")
        package_title = self.QtWidgets.QLineEdit()
        package_title.setObjectName("OnboardingV2PackageTitle")
        package_school = self.QtWidgets.QComboBox()
        package_school.setObjectName("OnboardingV2PackageSchool")
        package_school.addItems(["Palmdale", "Hawthorne", "North Long Beach"])
        package_documents = self.QtWidgets.QListWidget()
        package_documents.setObjectName("OnboardingV2PackageDocuments")
        add_package_document = self.QtWidgets.QPushButton("Add PDF")
        add_package_document.setObjectName("OnboardingV2AddPackageDocument")
        replace_package_document = self.QtWidgets.QPushButton("Replace PDF")
        replace_package_document.setObjectName("OnboardingV2ReplacePackageDocument")
        remove_package_document = self.QtWidgets.QPushButton("Remove PDF")
        remove_package_document.setObjectName("OnboardingV2RemovePackageDocument")
        move_package_up = self.QtWidgets.QPushButton("Move up")
        move_package_up.setObjectName("OnboardingV2MovePackageDocumentUp")
        move_package_down = self.QtWidgets.QPushButton("Move down")
        move_package_down.setObjectName("OnboardingV2MovePackageDocumentDown")
        create_package = self.QtWidgets.QPushButton("Create package draft")
        create_package.setObjectName("OnboardingV2CreatePackageDraft")
        validate_package = self.QtWidgets.QPushButton("Validate selected package")
        validate_package.setObjectName("OnboardingV2ValidatePackage")
        publish_package = self.QtWidgets.QPushButton("Publish selected package")
        publish_package.setObjectName("OnboardingV2PublishPackage")
        package_buttons = self.QtWidgets.QHBoxLayout()
        for button in (add_package_document, replace_package_document, remove_package_document, move_package_up, move_package_down):
            package_buttons.addWidget(button)
        package_form.addRow("Package key", package_key)
        package_form.addRow("Title", package_title)
        package_form.addRow("School", package_school)
        package_form.addRow("Documents", package_documents)
        package_form.addRow("", package_buttons)
        package_form.addRow("", create_package)
        package_form.addRow("", validate_package)
        package_form.addRow("", publish_package)

        field_search = self.QtWidgets.QLineEdit()
        field_search.setObjectName("OnboardingV2FieldSearch")
        field_search.setPlaceholderText("Search fields")
        deprecate_field = self.QtWidgets.QPushButton("Deprecate selected field")
        deprecate_field.setObjectName("OnboardingV2DeprecateField")
        mapper_canvas = OnboardingPdfMapperCanvas()
        mapper_canvas.setObjectName("OnboardingV2PdfMapperCanvas")
        mapper_canvas.setMinimumHeight(420)
        load_mapper_pdf = self.QtWidgets.QPushButton("Load PDF in mapper")
        load_mapper_pdf.setObjectName("OnboardingV2LoadMapperPdf")
        mapper_field = self.QtWidgets.QComboBox()
        mapper_field.setObjectName("OnboardingV2MapperField")
        mapper_field.setEditable(True)
        mapper_required = self.QtWidgets.QCheckBox("Required")
        mapper_required.setObjectName("OnboardingV2MapperRequired")
        mapper_font_size = self.QtWidgets.QDoubleSpinBox()
        mapper_font_size.setObjectName("OnboardingV2MapperFontSize")
        mapper_font_size.setRange(4, 72)
        mapper_font_size.setValue(10)
        mapper_alignment = self.QtWidgets.QComboBox()
        mapper_alignment.setObjectName("OnboardingV2MapperAlignment")
        mapper_alignment.addItems(["left", "center", "right"])
        mapper_multiline = self.QtWidgets.QCheckBox("Multiline")
        mapper_multiline.setObjectName("OnboardingV2MapperMultiline")
        mapper_casing = self.QtWidgets.QComboBox()
        mapper_casing.setObjectName("OnboardingV2MapperCasing")
        mapper_casing.addItems(["", "upper", "lower", "title"])
        mapper_mask = self.QtWidgets.QLineEdit()
        mapper_mask.setObjectName("OnboardingV2MapperMask")
        mapper_mask.setPlaceholderText("phone or ssn")
        mapper_date_pattern = self.QtWidgets.QLineEdit()
        mapper_date_pattern.setObjectName("OnboardingV2MapperDatePattern")
        mapper_date_pattern.setPlaceholderText("Date pattern, e.g. MM/DD/YYYY")
        mapper_true_value = self.QtWidgets.QLineEdit()
        mapper_true_value.setObjectName("OnboardingV2MapperTrueValue")
        mapper_true_value.setPlaceholderText("Checked value")
        mapper_false_value = self.QtWidgets.QLineEdit()
        mapper_false_value.setObjectName("OnboardingV2MapperFalseValue")
        mapper_false_value.setPlaceholderText("Unchecked value")
        mapper_choice_values = self.QtWidgets.QLineEdit()
        mapper_choice_values.setObjectName("OnboardingV2MapperChoiceValues")
        mapper_choice_values.setPlaceholderText("Choices: source=PDF value, ...")
        save_mapping = self.QtWidgets.QPushButton("Save selected mapping")
        save_mapping.setObjectName("OnboardingV2SavePdfMapping")
        preview_mapping = self.QtWidgets.QPushButton("Synthetic preview")
        preview_mapping.setObjectName("OnboardingV2PreviewPdfMapping")
        preview_mapping.setAccessibleName("Generate synthetic PDF mapping preview and overflow results")
        acroform_table = self.QtWidgets.QTableWidget(0, 4)
        acroform_table.setObjectName("OnboardingV2AcroFormFields")
        acroform_table.setHorizontalHeaderLabels(["Field", "Type", "Page", "Bounds"])
        acroform_table.horizontalHeader().setStretchLastSection(True)
        mapper_status = self.QtWidgets.QLabel("Load PDF; draw or select box; choose reusable field.")
        mapper_status.setObjectName("OnboardingV2MapperStatus")
        mapper_path: dict[str, Path] = {}
        upgrade_employees = self.QtWidgets.QListWidget()
        upgrade_employees.setObjectName("OnboardingV2UpgradeEmployees")
        upgrade_employees.setAccessibleName("Select employees for template or package upgrade")
        upgrade_employees.setSelectionMode(
            self.QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        for employee in self.service.list_employees():
            item = self.QtWidgets.QListWidgetItem(employee.preferred_name or employee.legal_name)
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, employee.id)
            upgrade_employees.addItem(item)
        preview_template_upgrade = self.QtWidgets.QPushButton("Preview template upgrade")
        preview_template_upgrade.setObjectName("OnboardingV2PreviewTemplateUpgrade")
        apply_template_upgrade = self.QtWidgets.QPushButton("Apply template upgrade")
        apply_template_upgrade.setObjectName("OnboardingV2ApplyTemplateUpgrade")
        preview_package_upgrade = self.QtWidgets.QPushButton("Preview package upgrade")
        preview_package_upgrade.setObjectName("OnboardingV2PreviewPackageUpgrade")
        apply_package_upgrade = self.QtWidgets.QPushButton("Apply package upgrade")
        apply_package_upgrade.setObjectName("OnboardingV2ApplyPackageUpgrade")
        refresh_templates = self.QtWidgets.QPushButton("Refresh template library")
        refresh_templates.setObjectName("OnboardingV2RefreshTemplates")
        refresh_templates.setAccessibleName("Refresh onboarding templates, packages, and field library")
        def refresh() -> None:
            templates = self.service.list_task_template_versions()
            packages = self.service.list_document_package_versions()
            fields = self.service.list_intake_fields()
            summary.setText(
                f"Checklist versions: {len(templates)} · Package versions: {len(packages)} · "
                f"Reusable fields: {len(fields)}. Published versions remain immutable."
            )
            rows = (
                (template_table, [[item.template_key, item.title, item.school, item.version, item.status,
                    item.owner_role, item.package_key or "—"] for item in templates]),
                (package_table, [[item.package_key, item.title, item.school, item.version, item.status,
                    len(item.documents)] for item in packages]),
                (field_table, [[item.stable_id, item.label, item.field_type, item.sensitivity,
                    "Deprecated" if item.deprecated else "Active"] for item in fields]),
            )
            for table, values in rows:
                table.setRowCount(len(values))
                for row_index, row_values in enumerate(values):
                    for column, value in enumerate(row_values):
                        item = self.QtWidgets.QTableWidgetItem(str(value))
                        source = templates if table is template_table else packages if table is package_table else fields
                        if column == 0:
                            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, source[row_index].id)
                        table.setItem(row_index, column, item)
            mapper_field.clear()
            for field in fields:
                if not field.deprecated:
                    mapper_field.addItem(field.label, field.id)

        def create_reusable_field() -> None:
            aliases = [value.strip() for value in field_aliases.text().split(",") if value.strip()]
            similar = self.service.suggest_similar_intake_fields(field_label.text(), aliases=aliases)
            if similar:
                labels = ", ".join(item.label for item in similar[:5])
                answer = self.QtWidgets.QMessageBox.question(
                    page, "Similar fields found",
                    f"Similar reusable fields already exist: {labels}. Create a new field anyway?",
                    self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                    self.QtWidgets.QMessageBox.StandardButton.No,
                )
                if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                    return
            try:
                self.service.create_intake_field(
                    stable_id=field_stable_id.text(), label=field_label.text(),
                    field_type=field_type.currentText(), sensitivity=field_sensitivity.currentText(),
                    aliases=aliases,
                )
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "Field could not be created", str(exc))
                return
            field_stable_id.clear()
            field_label.clear()
            field_aliases.clear()
            refresh()
        def selected_id(table: Any) -> str:
            row = table.currentRow()
            return "" if row < 0 or table.item(row, 0) is None else str(
                table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole) or ""
            )

        def selected_employee_ids() -> list[str]:
            return [
                str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
                for item in upgrade_employees.selectedItems()
            ]

        def run_template_action(action: Callable[[], Any], title: str) -> None:
            try:
                action()
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, title, str(exc))
                return
            refresh()

        def create_template_draft() -> None:
            run_template_action(lambda: self.service.create_task_template_draft(
                template_key=template_key.text(), school=template_school.currentText(),
                title=template_title.text(), owner_role=template_owner.currentText(),
                due_offset_days=template_due.value(), package_key=template_package_key.text(),
                watcher_roles=[value.strip() for value in template_watchers.text().split(",") if value.strip()],
                critical=template_critical.isChecked(), content=template_content.toPlainText(),
                base_template_id=template_base_id.text(),
                override_fields=[value.strip() for value in template_override_fields.text().split(",") if value.strip()],
            ), "Template draft could not be created")

        def refresh_template_attachments() -> None:
            template_attachments.clear()
            template_id = selected_id(template_table)
            if not template_id:
                return
            template_attachments.addItems([
                item.name for item in self.service.list_task_template_attachments(template_id)
            ])

        def add_selected_template_attachment() -> None:
            template_id = selected_id(template_table)
            if not template_id:
                return
            selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
                page, "Select task template attachment", "",
                "Supported files (*.pdf *.docx *.xlsx *.csv *.txt *.png *.jpg *.jpeg)",
            )
            if not selected:
                return
            run_template_action(
                lambda: self.service.add_task_template_attachment(template_id, Path(selected)),
                "Template attachment could not be added",
            )
            refresh_template_attachments()

        def refresh_package_documents() -> None:
            package_documents.clear()
            package_documents.addItems([path.name for path in package_editor.paths])

        def choose_pdf() -> Path | None:
            selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(page, "Select PDF", "", "PDF (*.pdf)")
            return Path(selected) if selected else None

        def add_document() -> None:
            selected = choose_pdf()
            if selected is None:
                return
            try:
                package_editor.add(selected)
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(page, "PDF could not be added", str(exc))
                return
            refresh_package_documents()

        def replace_document() -> None:
            selected = choose_pdf()
            row = package_documents.currentRow()
            if selected is None or row < 0:
                return
            package_editor.replace(row, selected)
            refresh_package_documents()

        def remove_document() -> None:
            row = package_documents.currentRow()
            if row < 0:
                return
            package_editor.remove(row)
            refresh_package_documents()

        def move_document(offset: int) -> None:
            row = package_documents.currentRow()
            target = row + offset
            if row < 0 or target < 0 or target >= len(package_editor.paths):
                return
            package_editor.move(row, target)
            refresh_package_documents()
            package_documents.setCurrentRow(target)

        def create_package_draft() -> None:
            run_template_action(lambda: self.service.create_document_package_draft(
                package_key=package_key.text(), school=package_school.currentText(),
                title=package_title.text(), document_paths=list(package_editor.paths),
            ), "Package draft could not be created")
            package_editor.clear()
            refresh_package_documents()

        def validate_selected_package() -> None:
            package_id = selected_id(package_table)
            if not package_id:
                return
            issues = self.service.validate_document_package(package_id)
            self.QtWidgets.QMessageBox.information(
                page, "Package validation", "Valid package" if not issues else "\n".join(issues)
            )

        def apply_field_search() -> None:
            needle = field_search.text().strip().casefold()
            for row in range(field_table.rowCount()):
                field_table.setRowHidden(row, needle not in " ".join(
                    field_table.item(row, column).text() for column in range(field_table.columnCount())
                ).casefold())

        def load_mapper() -> None:
            selected = choose_pdf()
            if selected is None:
                return
            try:
                mapper_canvas.load_pdf(selected)
                detected = detect_acroform_fields(selected)
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "PDF could not be loaded", str(exc))
                return
            mapper_path["path"] = selected
            acroform_table.setRowCount(len(detected))
            for row, field in enumerate(detected):
                for column, value in enumerate((field.name, field.field_type, field.page_number, field.rect)):
                    item = self.QtWidgets.QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, field)
                    acroform_table.setItem(row, column, item)
            mapper_status.setText(f"Detected AcroForm fields: {len(detected)}. Synthetic preview only.")

        def select_acroform_field() -> None:
            row = acroform_table.currentRow()
            if row < 0:
                return
            field = acroform_table.item(row, 0).data(self.QtCore.Qt.ItemDataRole.UserRole)
            if field.page_number == mapper_canvas.page_number + 1:
                mapper_canvas.add_pdf_box(field.rect)

        def preview_pdf_mapping() -> None:
            if "path" not in mapper_path:
                return
            result = self.service.preview_pdf_mapping(mapper_path["path"])
            if result.overflow_errors:
                mapper_status.setText("Preview blocked: " + "\n".join(result.overflow_errors))
                return
            mapper_status.setText(
                f"Synthetic preview ready: {result.output_path}. "
                f"Required signatures: {', '.join(result.required_signatures) or 'none'}."
            )

        def save_selected_mapping() -> None:
            selected = mapper_canvas.scene().selectedItems()
            if not selected or "path" not in mapper_path:
                return
            choice_values: dict[str, str] = {}
            for item in mapper_choice_values.text().split(","):
                source, separator, target = item.partition("=")
                if separator and source.strip():
                    choice_values[source.strip()] = target.strip()
            formatting = {
                "casing": mapper_casing.currentText(),
                "mask": mapper_mask.text(),
                "date_pattern": mapper_date_pattern.text(),
                "true_value": mapper_true_value.text(),
                "false_value": mapper_false_value.text(),
                "choice_values": choice_values,
            }
            run_template_action(lambda: self.service.create_pdf_mapping(
                document_key=mapper_path["path"].stem, page_number=mapper_canvas.page_number + 1,
                rect=mapper_canvas.pdf_rect(selected[0]), field_id=str(mapper_field.currentData() or ""),
                required=mapper_required.isChecked(), font_size=mapper_font_size.value(),
                alignment=mapper_alignment.currentText(), multiline=mapper_multiline.isChecked(),
                formatting=formatting,
            ), "PDF mapping could not be saved")
            mapper_status.setText("Mapping saved. Overflow validation runs during package generation.")

        def preview_selected_template_upgrade() -> None:
            previews = self.service.preview_task_template_upgrade(
                selected_id(template_table), employee_ids=selected_employee_ids()
            )
            self.QtWidgets.QMessageBox.information(
                page, "Template upgrade preview", f"Tasks changing: {len(previews)}"
            )

        def apply_selected_template_upgrade() -> None:
            changed = self.service.apply_task_template_upgrade(
                selected_id(template_table), employee_ids=selected_employee_ids()
            )
            self.QtWidgets.QMessageBox.information(
                page, "Template upgrade applied", f"Tasks changed: {len(changed)}"
            )

        def selected_package() -> Any:
            package_id = selected_id(package_table)
            return next(item for item in self.service.list_document_package_versions() if item.id == package_id)

        def preview_selected_package_upgrade() -> None:
            package = selected_package()
            impacted = self.service.preview_employee_package_upgrade(
                package_key=package.package_key, package_version_id=package.id,
                employee_ids=selected_employee_ids(),
            )
            self.QtWidgets.QMessageBox.information(
                page, "Package upgrade preview", f"Employees changing: {len(impacted)}"
            )

        def apply_selected_package_upgrade() -> None:
            package = selected_package()
            changed = self.service.upgrade_employee_package(
                package_key=package.package_key, package_version_id=package.id,
                employee_ids=selected_employee_ids(),
            )
            self.QtWidgets.QMessageBox.information(
                page, "Package upgrade applied", f"Tasks changed: {changed}"
            )

        def select_legacy_migration() -> None:
            selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
                page, "Select legacy onboarding JSON", "", "JSON (*.json)"
            )
            if not selected:
                return
            migration_path.setText(str(Path(selected).resolve()))
            migration_preview.clear()
            migration_confirmation.clear()
            migration_status.setText("File selected. Preview required before import.")

        def preview_legacy_migration() -> None:
            try:
                preview = self.service.preview_legacy_import(Path(migration_path.text()))
            except (OSError, ValueError, PermissionError) as exc:
                migration_preview.clear()
                migration_status.setText(f"Validation failed: {exc}")
                return
            migration_preview["value"] = preview
            warnings = "\n".join(preview.warnings) or "None"
            migration_status.setText(
                f"Employees: {preview.employee_count} · Tasks: {preview.task_count}\n"
                f"SHA-256: {preview.source_sha256}\nWarnings: {warnings}"
            )

        def import_legacy_migration() -> None:
            preview = migration_preview.get("value")
            if preview is None:
                migration_status.setText("Preview required before import.")
                return
            try:
                result = self.service.import_legacy_data(
                    Path(migration_path.text()),
                    backup_dir=self.migration_backup_dir,
                    expected_sha256=preview.source_sha256,
                    confirmation=migration_confirmation.text(),
                )
            except (OSError, ValueError, PermissionError) as exc:
                migration_status.setText(f"Import failed: {exc}")
                return
            migration_status.setText(
                f"Imported employees: {result.imported_employees} · Imported tasks: {result.imported_tasks}\n"
                f"Skipped employees: {result.preview.employee_count - result.imported_employees} · "
                f"Skipped tasks: {result.preview.task_count - result.imported_tasks}\n"
                f"Backup: {result.backup_path}"
            )
        refresh_templates.clicked.connect(refresh)
        create_field.clicked.connect(create_reusable_field)
        create_template.clicked.connect(create_template_draft)
        template_table.itemSelectionChanged.connect(refresh_template_attachments)
        add_template_attachment.clicked.connect(add_selected_template_attachment)
        publish_template.clicked.connect(lambda: run_template_action(
            lambda: self.service.publish_task_template(selected_id(template_table)), "Template could not be published"
        ))
        deprecate_template.clicked.connect(lambda: run_template_action(
            lambda: self.service.deprecate_task_template(selected_id(template_table)), "Template could not be deprecated"
        ))
        add_package_document.clicked.connect(add_document)
        replace_package_document.clicked.connect(replace_document)
        remove_package_document.clicked.connect(remove_document)
        move_package_up.clicked.connect(lambda: move_document(-1))
        move_package_down.clicked.connect(lambda: move_document(1))
        create_package.clicked.connect(create_package_draft)
        validate_package.clicked.connect(validate_selected_package)
        publish_package.clicked.connect(lambda: run_template_action(
            lambda: self.service.publish_document_package(selected_id(package_table)), "Package could not be published"
        ))
        field_search.textChanged.connect(apply_field_search)
        deprecate_field.clicked.connect(lambda: run_template_action(
            lambda: self.service.deprecate_intake_field(selected_id(field_table)), "Field could not be deprecated"
        ))
        load_mapper_pdf.clicked.connect(load_mapper)
        acroform_table.itemSelectionChanged.connect(select_acroform_field)
        save_mapping.clicked.connect(save_selected_mapping)
        preview_mapping.clicked.connect(preview_pdf_mapping)
        preview_template_upgrade.clicked.connect(lambda: run_template_action(
            preview_selected_template_upgrade, "Template upgrade preview failed"
        ))
        apply_template_upgrade.clicked.connect(lambda: run_template_action(
            apply_selected_template_upgrade, "Template upgrade failed"
        ))
        preview_package_upgrade.clicked.connect(lambda: run_template_action(
            preview_selected_package_upgrade, "Package upgrade preview failed"
        ))
        apply_package_upgrade.clicked.connect(lambda: run_template_action(
            apply_selected_package_upgrade, "Package upgrade failed"
        ))
        select_migration.clicked.connect(select_legacy_migration)
        preview_migration.clicked.connect(preview_legacy_migration)
        import_migration.clicked.connect(import_legacy_migration)
        layout.addWidget(summary)
        layout.addWidget(self.QtWidgets.QLabel("Legacy onboarding migration"))
        migration_actions = self.QtWidgets.QHBoxLayout()
        for control in (migration_path, select_migration, preview_migration):
            migration_actions.addWidget(control)
        layout.addLayout(migration_actions)
        migration_confirm_actions = self.QtWidgets.QHBoxLayout()
        migration_confirm_actions.addWidget(migration_confirmation)
        migration_confirm_actions.addWidget(import_migration)
        layout.addLayout(migration_confirm_actions)
        layout.addWidget(migration_status)
        layout.addWidget(self.QtWidgets.QLabel("Task template versions"))
        layout.addWidget(template_table)
        layout.addLayout(template_form)
        layout.addWidget(self.QtWidgets.QLabel("Document package versions"))
        layout.addWidget(package_table)
        layout.addLayout(package_form)
        layout.addWidget(self.QtWidgets.QLabel("Reusable field library"))
        layout.addWidget(field_table)
        layout.addWidget(field_search)
        layout.addWidget(deprecate_field)
        layout.addLayout(field_form)
        layout.addWidget(self.QtWidgets.QLabel("Visual PDF mapper"))
        layout.addWidget(load_mapper_pdf)
        layout.addWidget(acroform_table)
        layout.addWidget(mapper_canvas)
        mapper_controls = self.QtWidgets.QHBoxLayout()
        for control in (
            mapper_field, mapper_required, mapper_font_size, mapper_alignment,
            mapper_multiline, mapper_casing, mapper_mask, mapper_date_pattern,
            mapper_true_value, mapper_false_value, mapper_choice_values, save_mapping, preview_mapping,
        ):
            mapper_controls.addWidget(control)
        layout.addLayout(mapper_controls)
        layout.addWidget(mapper_status)
        layout.addWidget(self.QtWidgets.QLabel("Apply published changes to selected active employees"))
        layout.addWidget(upgrade_employees)
        upgrade_actions = self.QtWidgets.QHBoxLayout()
        for control in (
            preview_template_upgrade, apply_template_upgrade,
            preview_package_upgrade, apply_package_upgrade,
        ):
            upgrade_actions.addWidget(control)
        layout.addLayout(upgrade_actions)
        layout.addWidget(refresh_templates)
        layout.addStretch(1)
        refresh()
        return page

    def _build_communications_page(self) -> Any:
        page, layout = self._page("Onboarding Communications", "Preview reminders, scheduler health, run history, and retries.", "Communications")
        summary = self.QtWidgets.QLabel()
        summary.setObjectName("OnboardingV2CommunicationsSummary")
        summary.setWordWrap(True)
        preview_table = self.QtWidgets.QTableWidget(0, 5)
        preview_table.setObjectName("OnboardingV2ReminderPreviewTable")
        preview_table.setAccessibleName("Exact onboarding reminder preview recipients")
        preview_table.setHorizontalHeaderLabels(["School", "Role", "Recipient", "Tasks", "Warnings"])
        preview_table.horizontalHeader().setStretchLastSection(True)
        history_table = self.QtWidgets.QTableWidget(0, 5)
        history_table.setObjectName("OnboardingV2ReminderRunHistory")
        history_table.setAccessibleName("Onboarding reminder run history")
        history_table.setHorizontalHeaderLabels(["Run", "School", "Role", "State", "Created"])
        history_table.horizontalHeader().setStretchLastSection(True)
        preview_reminders = self.QtWidgets.QPushButton("Preview due reminders")
        preview_reminders.setObjectName("OnboardingV2PreviewReminders")
        preview_reminders.setAccessibleName("Preview exact onboarding reminder recipients and task counts")
        send_preview = self.QtWidgets.QPushButton("Send previewed reminders")
        send_preview.setObjectName("OnboardingV2SendReminders")
        send_preview.setAccessibleName("Confirm and send the current onboarding reminder preview")
        send_preview.setEnabled(False)
        retry_failed = self.QtWidgets.QPushButton("Retry failed messages")
        retry_failed.setObjectName("OnboardingV2RetryFailedReminders")
        retry_failed.setAccessibleName("Retry only failed onboarding reminder messages")
        retry_failed.setEnabled(False)
        refresh_communications = self.QtWidgets.QPushButton("Refresh communications status")
        refresh_communications.setObjectName("OnboardingV2RefreshCommunications")
        refresh_communications.setAccessibleName("Refresh onboarding reminder and scheduler status")
        open_notifications = self.QtWidgets.QPushButton("Open Notifications editor")
        open_notifications.setObjectName("OnboardingV2OpenNotifications")
        open_notifications.setAccessibleName("Open shared Staffing Notifications rule editor")
        state: dict[str, Any] = {"preview": None, "last_run_id": ""}

        def refresh() -> None:
            health = self.service.scheduler_health()
            history = self.service.list_reminder_run_history(limit=25)
            health_text = "never run" if not health else f"{health['local_day']} — {health['state']}"
            summary.setText(
                f"Data revision {self.service.store.data_revision()}. Scheduler: {health_text}. "
                f"Recent message outcomes: {len(history)}. Generate a fresh preview before any live send."
            )
            history_table.setRowCount(len(history))
            for row_index, item in enumerate(history):
                values = (
                    item.get("run_id", ""), item.get("school", ""), item.get("role", ""),
                    item.get("state", item.get("status", "")), item.get("created_at", ""),
                )
                for column, value in enumerate(values):
                    history_table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(str(value)))

        def preview() -> None:
            admin_fallback_email = str(self.admin_fallback_email_provider() or "").strip()
            if not admin_fallback_email:
                self.QtWidgets.QMessageBox.warning(page, "Reminder Preview", "Admin fallback email is not configured.")
                return
            try:
                current = self.service.preview_reminders(
                    recipient_resolver=self.reminder_recipient_resolver,
                    admin_fallback_email=admin_fallback_email,
                    now=self.clock(),
                    config_revision=self.reminder_config_revision(),
                )
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Reminder Preview", str(exc))
                return
            state["preview"] = current
            lines = [
                f"Preview expires {current.expires_at.isoformat()}.",
                *(f"{item.school} — {item.role}: {item.recipient} ({len(item.task_ids)} tasks)" for item in current.messages),
                *current.warnings,
            ]
            summary.setText("\n".join(lines))
            preview_table.setRowCount(len(current.messages))
            for row_index, item in enumerate(current.messages):
                values = (item.school, item.role, item.recipient, len(item.task_ids), "\n".join(current.warnings))
                for column, value in enumerate(values):
                    preview_table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(str(value)))
            send_preview.setEnabled(True)

        def send() -> None:
            current = state.get("preview")
            if current is None:
                return
            answer = self.QtWidgets.QMessageBox.question(
                page,
                "Send Onboarding Reminders",
                "Send the exact recipients and counts shown in this fresh preview?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
            override_reason = ""
            try:
                result = self.service.send_reminder_preview(
                    current.token,
                    sender=self.reminder_sender,
                    now=self.clock(),
                    confirmed=True,
                    config_revision=self.reminder_config_revision(),
                )
            except ValueError as exc:
                if self.service.access.role != "admin" or "override requires a reason" not in str(exc):
                    self.QtWidgets.QMessageBox.warning(page, "Reminder Send", str(exc))
                    return
                override_reason, accepted = self.QtWidgets.QInputDialog.getText(
                    page, "Duplicate Send Override", "Required override reason:"
                )
                if not accepted or not str(override_reason or "").strip():
                    return
                result = self.service.send_reminder_preview(
                    current.token,
                    sender=self.reminder_sender,
                    now=self.clock(),
                    confirmed=True,
                    admin_override_reason=override_reason,
                    config_revision=self.reminder_config_revision(),
                )
            state["preview"] = None
            state["last_run_id"] = result.run_id
            send_preview.setEnabled(False)
            retry_failed.setEnabled(result.failed_count > 0)
            summary.setText(
                f"Send complete: {result.sent_count} sent, {result.failed_count} failed, "
                f"{result.skipped_count} skipped."
            )

        def retry() -> None:
            run_id = str(state.get("last_run_id") or "")
            if not run_id:
                return
            answer = self.QtWidgets.QMessageBox.question(
                page,
                "Retry Failed Reminders",
                "Retry only failed messages from the last run?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                result = self.service.retry_failed_reminders(
                    run_id,
                    sender=self.reminder_sender,
                    now=self.clock(),
                    confirmed=True,
                )
            except (OSError, ValueError) as exc:
                self.QtWidgets.QMessageBox.warning(page, "Reminder Retry", str(exc))
                return
            state["last_run_id"] = result.run_id
            retry_failed.setEnabled(result.failed_count > 0)
            summary.setText(f"Retry complete: {result.sent_count} sent, {result.failed_count} failed.")

        preview_reminders.clicked.connect(preview)
        send_preview.clicked.connect(send)
        retry_failed.clicked.connect(retry)
        refresh_communications.clicked.connect(refresh)
        open_notifications.clicked.connect(lambda: (
            self.dashboard.show_notifications_view()
            if self.dashboard is not None and hasattr(self.dashboard, "show_notifications_view")
            else self.dashboard._show_notifications_view()
            if self.dashboard is not None and hasattr(self.dashboard, "_show_notifications_view")
            else None
        ))
        layout.addWidget(summary)
        layout.addWidget(preview_table)
        layout.addWidget(history_table)
        layout.addWidget(preview_reminders)
        layout.addWidget(send_preview)
        layout.addWidget(retry_failed)
        layout.addWidget(refresh_communications)
        layout.addWidget(open_notifications)
        layout.addStretch(1)
        refresh()
        return page

    def _remask_sensitive_values(self) -> None:
        for label in self._sensitive_labels:
            label.setText("Masked")

    def _apply_density(self, root: Any) -> None:
        row_height = 40 if self.density == "comfortable" else 28
        for table in root.findChildren(self.QtWidgets.QTableWidget):
            table.verticalHeader().setDefaultSectionSize(row_height)
        root.setProperty("onboardingDensity", self.density)
        root.style().unpolish(root)
        root.style().polish(root)

    def _filter_table(self, table: Any, text: str) -> None:
        needle = str(text).strip().casefold()
        for row in range(table.rowCount()):
            haystack = " ".join(
                table.item(row, column).text()
                for column in range(table.columnCount())
                if table.item(row, column) is not None
            ).casefold()
            table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _finalize_page_accessibility(self, root: Any) -> None:
        widget_types = (
            self.QtWidgets.QPushButton, self.QtWidgets.QLineEdit,
            self.QtWidgets.QComboBox, self.QtWidgets.QCheckBox,
            self.QtWidgets.QTextEdit, self.QtWidgets.QTableWidget,
            self.QtWidgets.QListWidget,
        )
        for widget_type in widget_types:
            for widget in root.findChildren(widget_type):
                if not widget.accessibleName():
                    label = widget.text() if hasattr(widget, "text") else ""
                    widget.setAccessibleName(str(label or widget.objectName()).replace("V2", " V2 "))
                widget.setFocusPolicy(self.QtCore.Qt.FocusPolicy.StrongFocus)

    def _install_responsive_switch(self, page: Any, table: Any, cards: Any) -> None:
        original_resize = page.resizeEvent

        def resize_event(event: Any) -> None:
            narrow = page.width() < 900
            table.setVisible(not narrow)
            cards.setVisible(narrow)
            original_resize(event)

        page.resizeEvent = resize_event

    def _page(self, title: str, subtitle: str, object_suffix: str) -> tuple[Any, Any]:
        page = self.QtWidgets.QWidget()
        page.setObjectName(f"OnboardingV2{object_suffix}Page")
        outer = self.QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = self.QtWidgets.QScrollArea()
        scroll.setObjectName(f"OnboardingV2{object_suffix}Scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)
        heading = self.QtWidgets.QLabel(title)
        heading.setObjectName("StaffingV2PageTitle")
        heading.setAccessibleName(title)
        description = self.QtWidgets.QLabel(subtitle)
        description.setObjectName("StaffingV2PageSubtitle")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._built_pages.append(page)
        self._apply_density(page)
        self.QtCore.QTimer.singleShot(
            0, lambda root=page: (
                self._finalize_page_accessibility(root),
                configure_v2_scroll_areas(self.QtWidgets, root, self.QtCore),
            )
        )
        return page, layout

    def _add_empty_state(self, layout: Any, text: str) -> None:
        label = self.QtWidgets.QLabel(text)
        label.setObjectName("OnboardingV2EmptyState")
        label.setWordWrap(True)
        layout.addWidget(label)
