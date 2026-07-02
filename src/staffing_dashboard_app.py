from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from staffing_service import StaffingService
from staffing_store import StaffingStore


APP_TITLE = "Director Staffing Dashboard"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_DIR = REPO_ROOT / "interviews"
STAFFING_DB_PATH = DEFAULT_BASE_DIR / "staffing_dashboard.sqlite3"
STAFFING_SEED_PATH = REPO_ROOT / "config" / "staffing_seed.json"
PERMIT_VALUES = [
    "unknown",
    "no_permit_or_application",
    "permit_in_process",
    "teacher_permit_approved",
    "no_units_needed",
]


def staffing_status_color(status: str) -> str:
    return {
        "dont_need_now": "#dbeafe",
        "need_now": "#fee2e2",
        "coming": "#fef3c7",
        "filled": "#dcfce7",
        "replace": "#fed7aa",
    }.get(status, "#f8fafc")


def permit_color(status: str) -> str:
    return {
        "no_permit_or_application": "#fecaca",
        "permit_in_process": "#fde68a",
        "teacher_permit_approved": "#bbf7d0",
        "no_units_needed": "#bfdbfe",
    }.get(status, "#f8fafc")


def slot_label(row: Any) -> str:
    slot = str(row.slot_group or "").strip()
    return slot.replace("_", " ").title() if slot else row.position_type.replace("_", " ").title()


def school_summary(rows: list[Any]) -> str:
    open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
    coming_count = sum(1 for row in rows if row.status == "coming")
    filled_count = sum(1 for row in rows if row.status == "filled")
    return f"Open: {open_count}    Coming: {coming_count}    Filled: {filled_count}"


def seed_assignment_count(seed_path: Path) -> int:
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    count = 0
    for school in data.get("schools", []):
        for classroom in school.get("classrooms", []):
            count += len(classroom.get("slots", classroom.get("positions", [])))
        for support_row in school.get("support_rows", []):
            count += len(support_row.get("slots", support_row.get("positions", [])))
    return count


class StaffingDashboardWindow:
    def __init__(
        self,
        *,
        db_path: Path = STAFFING_DB_PATH,
        seed_path: Path = STAFFING_SEED_PATH,
    ) -> None:
        self.db_path = Path(db_path)
        self.seed_path = Path(seed_path)
        self.store = StaffingStore(self.db_path)
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(APP_TITLE)
        self.window.resize(1180, 760)
        self.status_label = QtWidgets.QLabel("")
        self.metrics_label = QtWidgets.QLabel("")
        self.school_selector = QtWidgets.QComboBox()
        self.tabs = QtWidgets.QTabWidget()
        self._build()

    def show(self) -> None:
        self.window.show()

    def _build(self) -> None:
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        title = QtWidgets.QLabel("Staffing")
        title.setObjectName("Title")
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addWidget(self.metrics_label)
        self.school_selector.currentIndexChanged.connect(self._select_school_index)
        layout.addWidget(self.school_selector)
        layout.addWidget(self.tabs, 1)
        self.window.setCentralWidget(root)
        self.refresh()

    def refresh(self) -> None:
        self.store.initialize()
        assignments = self.store.list_assignments()
        if self.seed_path.exists() and (not assignments or len(assignments) < seed_assignment_count(self.seed_path)):
            self.store.import_seed_file(self.seed_path)
        metrics = StaffingService(self.store).staffing_metrics(today=date.today())
        self.metrics_label.setText(
            f"Open positions: {metrics.open_count}    "
            f"Average days to fill: {metrics.avg_days_to_fill:.1f}    "
            f"Open > 7 days: {metrics.open_over_7_days}"
        )
        current_school = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else ""
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        rows_by_school: dict[str, list[Any]] = {}
        for row in metrics.rows:
            rows_by_school.setdefault(row.school or "Unassigned", []).append(row)
        for school, rows in rows_by_school.items():
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.addWidget(QtWidgets.QLabel(school_summary(rows)))
            tab_layout.addWidget(self._workbook_table(rows), 1)
            self.tabs.addTab(tab, school)
        self.school_selector.blockSignals(True)
        self.school_selector.clear()
        for index in range(self.tabs.count()):
            self.school_selector.addItem(self.tabs.tabText(index))
        self.school_selector.blockSignals(False)
        if current_school:
            for index in range(self.tabs.count()):
                if self.tabs.tabText(index) == current_school:
                    self.tabs.setCurrentIndex(index)
                    self.school_selector.setCurrentIndex(index)
                    break

    def _workbook_table(self, rows: list[Any]) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(len(rows), 9)
        table.setObjectName("DirectorStaffingWorkbookBoard")
        table.setHorizontalHeaderLabels(["Ratio", "Classroom", "Role", "Position", "Person", "Status", "Capacity", "Notes", "Action"])
        for row_index, row in enumerate(rows):
            values = [
                row.ratio_group,
                row.classroom,
                slot_label(row),
                row.position_name,
                row.person_name or "OPEN POSITION",
                row.status,
                "" if row.classroom_capacity is None else str(row.classroom_capacity),
                row.notes,
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value or ""))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, row.assignment_id)
                if column == 4:
                    item.setBackground(QtGui.QColor(permit_color(row.permit_status) if row.permit_status else staffing_status_color(row.status)))
                if column == 5:
                    item.setBackground(QtGui.QColor(staffing_status_color(row.status)))
                table.setItem(row_index, column, item)
            table.setCellWidget(row_index, 8, self._action_button(row.assignment_id, row.status))
        table.cellDoubleClicked.connect(lambda row, column, widget=table: self._open_details_from_table(widget, row, column))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _action_button(self, assignment_id: int, status: str) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        menu = QtWidgets.QMenu(button)
        if status == "dont_need_now":
            button.setText("Open")
            button.clicked.connect(lambda _checked=False: self._run(lambda service: service.open_position(assignment_id), "Position opened."))
            menu.addAction("Mark Not Needed", lambda: self._mark_not_needed(assignment_id))
        elif status in {"need_now", "replace"}:
            button.setText("Mark Coming")
            button.clicked.connect(lambda _checked=False: self._mark_coming(assignment_id))
            menu.addAction("Mark Not Needed", lambda: self._mark_not_needed(assignment_id))
            if status == "replace":
                menu.addAction("Clear Replacement", lambda: self._run(lambda service: service.clear_replacement(assignment_id), "Replacement cleared."))
        elif status == "coming":
            button.setText("Mark Filled")
            button.clicked.connect(lambda _checked=False: self._run(lambda service: service.mark_filled(assignment_id), "Position filled."))
            menu.addAction("Revert Coming", lambda: self._run(lambda service: service.revert_coming(assignment_id), "Incoming person reverted."))
            menu.addAction("Mark Not Needed", lambda: self._mark_not_needed(assignment_id))
        elif status == "filled":
            button.setText("Replace")
            button.clicked.connect(lambda _checked=False: self._mark_replacing(assignment_id))
            menu.addAction("Update Permit", lambda: self._update_permit(assignment_id))
            menu.addAction("Mark Not Needed", lambda: self._mark_not_needed(assignment_id))
        else:
            button.setText("Review")
        if menu.actions():
            button.setMenu(menu)
            button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.DelayedPopup)
        return button

    def _open_details_from_table(self, table: QtWidgets.QTableWidget, row: int, column: int) -> None:
        if column == 8:
            return
        item = table.item(row, 0) or table.item(row, 1) or table.item(row, 3)
        assignment_id = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        if assignment_id is not None:
            self._open_details(int(assignment_id))

    def _open_details(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except Exception as exc:
            self.status_label.setText(str(exc) or "Staffing assignment not found.")
            return
        dialog = QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("Position Details")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        classroom = QtWidgets.QLineEdit(assignment.classroom)
        shift_start = QtWidgets.QLineEdit(assignment.shift_start)
        shift_end = QtWidgets.QLineEdit(assignment.shift_end)
        permit = QtWidgets.QComboBox()
        permit.addItems(PERMIT_VALUES)
        permit.setCurrentText(assignment.permit_status if assignment.permit_status in PERMIT_VALUES else "unknown")
        form.addRow("Classroom", classroom)
        form.addRow("Position", QtWidgets.QLabel(assignment.position_name))
        form.addRow("Person", QtWidgets.QLabel(assignment.person_name or "OPEN POSITION"))
        form.addRow("Shift start", shift_start)
        form.addRow("Shift end", shift_end)
        form.addRow("Permit status", permit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        permit_status = permit.currentText() if assignment.person_id is not None else None
        self._run(
            lambda service: service.update_assignment_details(
                assignment_id,
                classroom=classroom.text(),
                shift_start=shift_start.text(),
                shift_end=shift_end.text(),
                permit_status=permit_status,
            ),
            "Position details updated.",
        )

    def _mark_coming(self, assignment_id: int) -> None:
        person_name, accepted = QtWidgets.QInputDialog.getText(self.window, "Staffing", "Incoming person name")
        if not accepted:
            return
        start_date, accepted = QtWidgets.QInputDialog.getText(self.window, "Staffing", "Start date (YYYY-MM-DD)")
        if accepted:
            self._run(lambda service: service.mark_coming(assignment_id, person_name=person_name, start_date=start_date), "Incoming person saved.")

    def _mark_replacing(self, assignment_id: int) -> None:
        notice_given, accepted = QtWidgets.QInputDialog.getText(self.window, "Staffing", "Notice date (YYYY-MM-DD)")
        if not accepted:
            return
        final_day, accepted = QtWidgets.QInputDialog.getText(self.window, "Staffing", "Final working day (YYYY-MM-DD)")
        if accepted:
            self._run(lambda service: service.mark_replacing(assignment_id, notice_given=notice_given, final_working_day=final_day), "Replacement need opened.")

    def _mark_not_needed(self, assignment_id: int) -> None:
        confirmed = QtWidgets.QMessageBox.question(
            self.window,
            "Staffing",
            "Mark this position not needed?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirmed == QtWidgets.QMessageBox.StandardButton.Yes:
            self._run(lambda service: service.mark_not_needed(assignment_id, confirmed=True), "Position marked not needed.")

    def _update_permit(self, assignment_id: int) -> None:
        assignment = self.store.get_assignment(assignment_id)
        if assignment.person_id is None:
            self.status_label.setText("No person assigned to update.")
            return
        permit_status, accepted = QtWidgets.QInputDialog.getItem(self.window, "Staffing", "Permit status", PERMIT_VALUES, 0, False)
        if accepted:
            self._run(lambda service: service.update_permit_status(assignment.person_id or 0, permit_status), "Permit status updated.")

    def _run(self, action: Any, success_message: str) -> None:
        try:
            action(StaffingService(self.store))
        except Exception as exc:
            self.status_label.setText(str(exc) or "Staffing action failed.")
            return
        self.status_label.setText(success_message)
        self.refresh()

    def _select_school_index(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)


def apply_styles(app: QtWidgets.QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget { font-family: Segoe UI, Arial, sans-serif; font-size: 10pt; color: #172033; }
        QLabel#Title { font-size: 22pt; font-weight: 700; }
        QTableWidget { background: #ffffff; border: 1px solid #d9dee7; gridline-color: #eef1f5; }
        QHeaderView::section { background: #eef2f7; padding: 6px; border: 0; font-weight: 600; }
        QPushButton, QToolButton { background: #2563eb; color: #ffffff; border: 0; border-radius: 6px; padding: 7px 10px; }
        QComboBox, QLineEdit { background: #ffffff; border: 1px solid #c9ced8; border-radius: 6px; padding: 6px; }
        """
    )


def launch_staffing_dashboard_app() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    apply_styles(app)
    window = StaffingDashboardWindow()
    window.show()
    return app.exec()


def main() -> int:
    return launch_staffing_dashboard_app()


if __name__ == "__main__":
    raise SystemExit(main())
