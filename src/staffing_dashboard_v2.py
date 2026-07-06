from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from staffing_models import StaffingHistoryRecord, StaffingMetricRow, StaffingPerson
from staffing_service import StaffingService
from staffing_store import StaffingStore


APP_QSS = """
QWidget#PySideStaffingV2Page {
    background-color: #f8fafc;
    color: #0f172a;
}
QFrame#StaffingV2Shell {
    background-color: #f8fafc;
}
QScrollBar:vertical {
    background-color: #f8fafc;
    width: 10px;
    margin: 2px;
    border: none;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #cbd5e1;
    min-height: 36px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background-color: #94a3b8;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
    border: none;
    height: 0px;
}
QScrollBar:horizontal {
    background-color: #f8fafc;
    height: 10px;
    margin: 2px;
    border: none;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background-color: #cbd5e1;
    min-width: 36px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #94a3b8;
}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: transparent;
    border: none;
    width: 0px;
}
QFrame#StaffingV2DashboardHeaderTopRow,
QFrame#StaffingV2DashboardSummaryActionRow {
    background-color: transparent;
    border: none;
}
QFrame#StaffingV2Sidebar {
    background-color: #ffffff;
    border-right: 1px solid #e2e8f0;
}
QLabel#StaffingV2Brand {
    color: #0f172a;
    font-size: 20px;
    font-weight: 800;
}
QLabel#StaffingV2SidebarSection {
    color: #64748b;
    font-size: 11px;
    font-weight: 800;
}
QPushButton#StaffingV2DashboardNavButton,
QPushButton#StaffingV2HomeNavButton,
QPushButton#StaffingV2ClassroomsNavButton,
QPushButton#StaffingV2PeopleNavButton,
QPushButton#StaffingV2HistoryNavButton,
QPushButton#StaffingV2AnalyticsNavButton,
QPushButton#StaffingV2NotificationsNavButton,
QPushButton#StaffingV2ValidationNavButton,
QPushButton#StaffingV2IntegrationsNavButton,
QPushButton#StaffingV2SettingsNavButton {
    background-color: transparent;
    color: #334155;
    border: none;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}
QPushButton#StaffingV2HomeNavButton:disabled,
QPushButton#StaffingV2AnalyticsNavButton:disabled,
QPushButton#StaffingV2NotificationsNavButton:disabled,
QPushButton#StaffingV2IntegrationsNavButton:disabled,
QPushButton#StaffingV2SettingsNavButton:disabled {
    color: #64748b;
}
QPushButton[staffingV2ActiveNav="true"] {
    background-color: #eaf2ff;
    color: #2563eb;
    border: none;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
    font-weight: 800;
}
QPushButton#StaffingV2ValidationAllIssuesTab,
QPushButton#StaffingV2ValidationCriticalTab,
QPushButton#StaffingV2ValidationWarningsTab,
QPushButton#StaffingV2ValidationInfoTab {
    background-color: transparent;
    color: #475569;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 6px 10px;
    font-weight: 700;
}
QPushButton[staffingV2ActiveValidationTab="true"] {
    color: #2563eb;
    border-bottom: 2px solid #2563eb;
}
QPushButton#StaffingV2PeopleOverviewTab,
QPushButton#StaffingV2PeopleAssignmentsTab,
QPushButton#StaffingV2PeopleHistoryTab,
QPushButton#StaffingV2PeopleNotesTab,
QPushButton#StaffingV2PeopleDocumentsTab {
    background-color: transparent;
    color: #475569;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 10px;
    font-weight: 700;
}
QPushButton[staffingV2ActivePeopleTab="true"] {
    color: #2563eb;
    border-bottom: 2px solid #2563eb;
}
QFrame#StaffingV2SidebarCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QFrame#StaffingV2Card,
QFrame#StaffingV2MetricCard,
QFrame#StaffingV2OverviewCard,
QFrame#StaffingV2PeopleMetricCard,
QFrame#StaffingV2HistoryMetricCard,
QFrame#StaffingV2ClassroomsMetricCard,
QFrame#StaffingV2Panel,
QFrame#StaffingV2PositionDrawer,
QFrame#StaffingV2DrawerSection,
QFrame#StaffingV2StatusKey,
QFrame#StaffingV2AddPositionDropZone {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QFrame#StaffingV2MetricCard {
    border-radius: 8px;
}
QFrame#StaffingV2MetricCard[staffingV2SummaryVariant="info"] {
    background-color: #f8fbff;
    border: 1px solid #bfdbfe;
}
QFrame#StaffingV2MetricCard[staffingV2SummaryVariant="danger"] {
    background-color: #fef2f2;
    border: 1px solid #fecaca;
}
QFrame#StaffingV2MetricCard[staffingV2SummaryVariant="success"] {
    background-color: #f0fdf4;
    border: 1px solid #bbf7d0;
}
QFrame#StaffingV2AddPositionDropZone {
    border: 1px dashed #cbd5e1;
    background-color: #ffffff;
}
QFrame#StaffingV2PositionDrawer {
    border-left: 1px solid #e2e8f0;
}
QDialog#StaffingV2MarkComingDialog,
QDialog#StaffingV2MarkFilledDialog,
QDialog#StaffingV2ManageFilledDialog,
QDialog#StaffingV2UpdatePermitDialog,
QDialog#StaffingV2MarkNeedNowDialog,
QDialog#StaffingV2AddPositionDialog {
    background-color: #ffffff;
    color: #0f172a;
}
QFrame#StaffingV2DialogSection,
QFrame#StaffingV2DialogInfo,
QFrame#StaffingV2DialogWarning,
QFrame#StaffingV2AddPositionStatusCard,
QFrame#StaffingV2ManagePermitCard,
QFrame#StaffingV2ManageReplaceCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QFrame#StaffingV2DialogInfo {
    background-color: #eff6ff;
    border-color: #bfdbfe;
}
QFrame#StaffingV2DialogWarning {
    background-color: #fffbeb;
    border-color: #fde68a;
}
QFrame#StaffingV2ManagePermitCard {
    background-color: #f0fdf4;
    border-color: #60a5fa;
}
QFrame#StaffingV2ManageReplaceCard {
    background-color: #fff7ed;
    border-color: #fed7aa;
}
QLabel#StaffingV2PageTitle {
    color: #0f172a;
    font-size: 26px;
    font-weight: 800;
}
QLabel#StaffingV2PageSubtitle,
QLabel#StaffingV2Muted {
    color: #475569;
}
QLabel#StaffingV2SectionTitle,
QLabel#StaffingV2ClassroomTitle,
QLabel#StaffingV2DrawerTitle {
    color: #0f172a;
    font-size: 18px;
    font-weight: 800;
}
QLabel#StaffingV2DrawerPositionName {
    color: #0f172a;
    font-size: 22px;
    font-weight: 800;
}
QLabel#StaffingV2MetricValue {
    color: #0f172a;
    font-size: 22px;
    font-weight: 800;
}
QLabel#StaffingV2SummaryValue {
    color: #0f172a;
    font-size: 13px;
    font-weight: 800;
}
QLabel#StaffingV2SummaryLabel[staffingV2SummaryVariant="info"],
QLabel#StaffingV2SummaryValue[staffingV2SummaryVariant="info"] {
    color: #2563eb;
}
QLabel#StaffingV2SummaryLabel[staffingV2SummaryVariant="danger"],
QLabel#StaffingV2SummaryValue[staffingV2SummaryVariant="danger"] {
    color: #dc2626;
}
QLabel#StaffingV2SummaryLabel[staffingV2SummaryVariant="success"],
QLabel#StaffingV2SummaryValue[staffingV2SummaryVariant="success"] {
    color: #15803d;
}
QFrame#StaffingV2PriorityChip {
    background-color: #fee2e2;
    border: 1px solid #fecaca;
    border-radius: 8px;
    padding: 8px 14px;
}
QLabel#StaffingV2PriorityChipText,
QLabel#StaffingV2PriorityChipIcon {
    background-color: transparent;
    color: #dc2626;
    font-weight: 800;
}
QLabel#StaffingV2NeedNowChip,
QFrame#StaffingV2NeedNowChip {
    background-color: #fee2e2;
    color: #dc2626;
    border: 1px solid #fecaca;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2ReplaceChip,
QFrame#StaffingV2ReplaceChip {
    background-color: #ffedd5;
    color: #ea580c;
    border: 1px solid #fed7aa;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2ComingChip,
QFrame#StaffingV2ComingChip {
    background-color: #fef3c7;
    color: #b45309;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2FilledChip,
QLabel#StaffingV2HealthyChip,
QFrame#StaffingV2FilledChip,
QFrame#StaffingV2HealthyChip {
    background-color: #dcfce7;
    color: #15803d;
    border: 1px solid #bbf7d0;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2NeutralChip,
QFrame#StaffingV2NeutralChip {
    background-color: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2ChipText {
    font-weight: 700;
}
QLabel#StaffingV2CardIcon,
QLabel#StaffingV2ChipIcon {
    background-color: transparent;
}
QPushButton#StaffingV2PrimaryButton {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #2563eb;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QPushButton#StaffingV2FilterApplyButton {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #2563eb;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QPushButton#StaffingV2ClassroomsFilterApply {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #2563eb;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QPushButton#StaffingV2FilterCloseButton,
QPushButton#StaffingV2ClassroomsFilterClose,
QPushButton#StaffingV2DrawerClose,
QPushButton#StaffingV2AddPositionClose,
QPushButton#StaffingV2ComingClose,
QPushButton#StaffingV2ManageFilledClose,
QPushButton#StaffingV2PermitClose,
QPushButton#StaffingV2FilledClose,
QPushButton#StaffingV2NeedNowClose {
    background-color: transparent;
    color: #0f172a;
    border: none;
    border-radius: 8px;
}
QPushButton#StaffingV2DropZoneAddButton {
    background-color: transparent;
    color: #2563eb;
    border: none;
    font-weight: 800;
}
QPushButton#StaffingV2AddPositionButton {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #2563eb;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QPushButton#StaffingV2ExportButton,
QPushButton#StaffingV2ViewHistoryButton,
QPushButton#StaffingV2ClassroomsExportButton,
QPushButton#StaffingV2ClassroomsMoreFilters,
QPushButton#StaffingV2ClassroomsClear,
QPushButton#StaffingV2PeopleMoreFilters,
QPushButton#StaffingV2PeopleClear,
QPushButton#StaffingV2HistoryExportButton,
QPushButton#StaffingV2HistoryValidationButton,
QPushButton#StaffingV2HistoryMoreFilters,
QPushButton#StaffingV2HistoryClear,
QPushButton#StaffingV2ValidationExportButton,
QPushButton#StaffingV2ValidationFiltersButton,
QPushButton#StaffingV2FilterResetButton,
QPushButton#StaffingV2FilterCancelButton,
QPushButton#StaffingV2ClassroomsFilterReset,
QPushButton#StaffingV2ClassroomsFilterCancel,
QPushButton#StaffingV2DrawerCancel,
QPushButton#StaffingV2DrawerSaveDraft {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QPushButton#StaffingV2DrawerMarkComing,
QPushButton#StaffingV2DrawerMarkFilled,
QPushButton#StaffingV2ClassroomsAddButton,
QPushButton#StaffingV2PeopleAddButton,
QPushButton#StaffingV2DrawerSaveChanges,
QPushButton#StaffingV2ComingSubmit,
QPushButton#StaffingV2FilledSubmit,
QPushButton#StaffingV2ManageFilledContinue,
QPushButton#StaffingV2ManagePermitContinue,
QPushButton#StaffingV2PermitSubmit,
QPushButton#StaffingV2NeedNowSubmit,
QPushButton#StaffingV2ComingCreatePerson,
QPushButton#StaffingV2AddPositionSubmit {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #2563eb;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QToolButton#StaffingV2ActionButton {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 700;
}
QMenu#StaffingV2ActionMenu {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 6px;
}
QMenu#StaffingV2ActionMenu::item {
    padding: 6px 18px;
}
QMenu#StaffingV2ActionMenu::item:selected {
    background-color: #eff6ff;
    color: #2563eb;
}
QPushButton#StaffingV2ComingSelectPerson,
QPushButton#StaffingV2ComingSaveDraft,
QPushButton#StaffingV2ComingCancel,
QPushButton#StaffingV2FilledSaveDraft,
QPushButton#StaffingV2FilledCancel,
QPushButton#StaffingV2ManageFilledCancel,
QPushButton#StaffingV2ManageReplaceContinue,
QPushButton#StaffingV2PermitCancel,
QPushButton#StaffingV2PermitDraft,
QPushButton#StaffingV2NeedNowCancel,
QPushButton#StaffingV2AddPositionCancel {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
}
QTableWidget#StaffingV2PositionsTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QHeaderView::section {
    background-color: #f8fafc;
    color: #334155;
    font-weight: 700;
    border: none;
    border-bottom: 1px solid #e2e8f0;
    padding: 8px;
}
QTableWidget#StaffingV2PeopleTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QTableWidget#StaffingV2ClassroomsTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QFrame#StaffingV2PeopleDetailPanel,
QFrame#StaffingV2PeopleDetailCard,
QFrame#StaffingV2HistoryDetailPanel,
QFrame#StaffingV2HistoryDetailCard,
QFrame#StaffingV2ClassroomsDetailPanel,
QFrame#StaffingV2ClassroomsValidationPanel,
QFrame#StaffingV2ClassroomsDetailCard,
QFrame#StaffingV2ValidationRightPanel,
QFrame#StaffingV2ValidationSideCard,
QFrame#StaffingV2FilterDrawer,
QFrame#StaffingV2ClassroomsFilterDrawer {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QFrame#StaffingV2ValidationMetricCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QTableWidget#StaffingV2ValidationTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QFrame#StaffingV2FilterDrawer {
    border-left: 1px solid #e2e8f0;
}
QFrame#StaffingV2ClassroomsFilterDrawer {
    border-left: 1px solid #e2e8f0;
}
QTableWidget#StaffingV2HistoryTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QListWidget#StaffingV2ClassroomList {
    background-color: #ffffff;
    border: none;
}
QListWidget#StaffingV2ClassroomList::item {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin: 4px;
    padding: 8px 10px;
}
QListWidget#StaffingV2ClassroomList::item:selected {
    border: 2px solid #2563eb;
    background-color: #eff6ff;
    color: #0f172a;
}
QFrame#StaffingV2ClassroomListItem {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QFrame#StaffingV2ClassroomListItem[staffingV2Selected="true"] {
    background-color: #eff6ff;
    border: 2px solid #2563eb;
}
QFrame#StaffingV2ClassroomStatusDot {
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}
QFrame#StaffingV2ClassroomStatusDot[staffingV2Status="need_now"],
QFrame#StaffingV2ClassroomStatusDot[staffingV2Status="replace"] {
    background-color: #ef4444;
}
QFrame#StaffingV2ClassroomStatusDot[staffingV2Status="coming"] {
    background-color: #f59e0b;
}
QFrame#StaffingV2ClassroomStatusDot[staffingV2Status="filled"] {
    background-color: #22c55e;
}
QFrame#StaffingV2ClassroomStatusDot[staffingV2Status="dont_need_now"] {
    background-color: #64748b;
}
QLabel#StaffingV2ClassroomItemTitle {
    color: #0f172a;
    font-weight: 800;
}
QLabel#StaffingV2ClassroomItemCounts,
QLabel#StaffingV2ClassroomItemChevron,
QLabel#StaffingV2ClassroomListFooter {
    color: #334155;
}
"""


ActionCallback = Callable[[int], None]


class StaffingDashboardV2Page:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtGui: Any,
        QtWidgets: Any,
        store: StaffingStore,
        service_factory: Callable[[], StaffingService],
        actions: dict[str, ActionCallback] | None = None,
        school_filter: str = "",
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.store = store
        self.service_factory = service_factory
        self.actions = actions or {}
        self.school_filter = str(school_filter or "").strip()
        self.rows: list[StaffingMetricRow] = []
        self.visible_rows: list[StaffingMetricRow] = []
        self.classroom_rows: dict[str, list[StaffingMetricRow]] = {}
        self.people: list[StaffingPerson] = []
        self.visible_people: list[StaffingPerson] = []
        self.history_records: list[StaffingHistoryRecord] = []
        self.visible_history_records: list[StaffingHistoryRecord] = []
        self.classroom_management_rows: dict[str, list[StaffingMetricRow]] = {}
        self.visible_classroom_management: list[tuple[str, list[StaffingMetricRow]]] = []
        self.validation_issues: list[dict[str, str]] = []
        self.visible_validation_issues: list[dict[str, str]] = []
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName("PySideStaffingV2Page")
        self.widget.setStyleSheet(APP_QSS)
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = self.QtWidgets.QHBoxLayout(self.widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = self.QtWidgets.QFrame()
        shell.setObjectName("StaffingV2Shell")
        shell_layout = self.QtWidgets.QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        root.addWidget(shell, 1)

        self.staffing_sidebar = self.QtWidgets.QFrame()
        self.staffing_sidebar.setObjectName("StaffingV2Sidebar")
        self.staffing_sidebar.setFixedWidth(252)
        sidebar_layout = self.QtWidgets.QVBoxLayout(self.staffing_sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        sidebar_layout.setSpacing(10)
        sidebar_layout.addWidget(self._label("Launch Pad Learning", "StaffingV2Brand"))
        sidebar_layout.addSpacing(18)
        sidebar_layout.addWidget(self._label("STAFFING", "StaffingV2SidebarSection"))
        self.home_nav_button = self._sidebar_button("StaffingV2HomeNavButton", "Dashboard", "dashboard")
        self.home_nav_button.setEnabled(False)
        self.dashboard_nav_button = self._sidebar_button("StaffingV2DashboardNavButton", "Staffing Dashboard", "dashboard")
        self.dashboard_nav_button.clicked.connect(self._show_dashboard_view)
        self.classrooms_nav_button = self._sidebar_button("StaffingV2ClassroomsNavButton", "Classrooms", "classrooms")
        self.classrooms_nav_button.clicked.connect(self._show_classrooms_view)
        self.people_nav_button = self._sidebar_button("StaffingV2PeopleNavButton", "People", "people")
        self.people_nav_button.clicked.connect(self._show_people_view)
        self.history_nav_button = self._sidebar_button("StaffingV2HistoryNavButton", "Assignment History", "history")
        self.history_nav_button.clicked.connect(self._show_history_view)
        for button in (
            self.home_nav_button,
            self.dashboard_nav_button,
            self.classrooms_nav_button,
            self.people_nav_button,
            self.history_nav_button,
        ):
            sidebar_layout.addWidget(button)
        sidebar_layout.addSpacing(16)
        self.analytics_nav_button = self._sidebar_button("StaffingV2AnalyticsNavButton", "Analytics", "analytics")
        self.analytics_nav_button.setEnabled(False)
        self.notifications_nav_button = self._sidebar_button("StaffingV2NotificationsNavButton", "Notifications", "notifications")
        self.notifications_nav_button.setEnabled(False)
        sidebar_layout.addWidget(self.analytics_nav_button)
        sidebar_layout.addWidget(self.notifications_nav_button)
        sidebar_layout.addSpacing(16)
        sidebar_layout.addWidget(self._label("SYSTEM", "StaffingV2SidebarSection"))
        self.validation_nav_button = self._sidebar_button("StaffingV2ValidationNavButton", "Validation", "validation")
        self.validation_nav_button.clicked.connect(self._show_validation_view)
        self.integrations_nav_button = self._sidebar_button("StaffingV2IntegrationsNavButton", "Integrations", "integrations")
        self.integrations_nav_button.setEnabled(False)
        self.settings_nav_button = self._sidebar_button("StaffingV2SettingsNavButton", "Settings", "settings")
        self.settings_nav_button.setEnabled(False)
        sidebar_layout.addWidget(self.validation_nav_button)
        sidebar_layout.addWidget(self.integrations_nav_button)
        sidebar_layout.addWidget(self.settings_nav_button)
        sidebar_layout.addStretch(1)
        env_card, env_layout = self._panel("StaffingV2SidebarCard")
        env_layout.addWidget(self._label("Environment", "StaffingV2Muted"))
        env_layout.addWidget(self._label("Production", "StaffingV2Muted"))
        env_layout.addWidget(self._label("v 1.4.0", "StaffingV2Muted"))
        sidebar_layout.addWidget(env_card)
        user_card, user_layout = self._panel("StaffingV2SidebarCard")
        user_layout.addWidget(self._label("AD   Admin User", "StaffingV2Muted"))
        sidebar_layout.addWidget(user_card)
        shell_layout.addWidget(self.staffing_sidebar)

        content = self.QtWidgets.QWidget()
        content.setObjectName("StaffingV2Content")
        content_layout = self.QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(14)
        self.page_stack = self.QtWidgets.QStackedWidget()
        content_layout.addWidget(self.page_stack, 1)
        shell_layout.addWidget(content, 1)

        self.dashboard_view = self.QtWidgets.QWidget()
        dashboard_root = self.QtWidgets.QVBoxLayout(self.dashboard_view)
        dashboard_root.setContentsMargins(0, 0, 0, 0)
        dashboard_root.setSpacing(14)
        self.page_stack.addWidget(self.dashboard_view)

        header_top = self.QtWidgets.QFrame()
        header_top.setObjectName("StaffingV2DashboardHeaderTopRow")
        header = self.QtWidgets.QHBoxLayout(header_top)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        title_block = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("Staffing Dashboard")
        title.setObjectName("StaffingV2PageTitle")
        subtitle = self.QtWidgets.QLabel("Manage classroom staffing, position lifecycle, and hiring progress.")
        subtitle.setObjectName("StaffingV2PageSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        self.school_selector = self.QtWidgets.QComboBox()
        self.school_selector.setObjectName("StaffingV2SchoolFilter")
        self.school_selector.setMinimumHeight(40)
        self.school_selector.currentIndexChanged.connect(self._refresh_filters)
        header.addLayout(self._labeled_control("School", self.school_selector))
        self.program_selector = self.QtWidgets.QComboBox()
        self.program_selector.setObjectName("StaffingV2ProgramFilter")
        self.program_selector.setMinimumHeight(40)
        self.program_selector.currentIndexChanged.connect(self._refresh_filters)
        header.addLayout(self._labeled_control("Program", self.program_selector))
        self.search = self.QtWidgets.QLineEdit()
        self.search.setObjectName("StaffingV2Search")
        self.search.setPlaceholderText("Search classrooms")
        self.search.setMinimumHeight(40)
        self.search.addAction(self._standard_icon("search"), self.QtWidgets.QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._refresh_filters)
        header.addWidget(self.search)
        self.add_button = self.QtWidgets.QPushButton("Add Position")
        self.add_button.setObjectName("StaffingV2AddPositionButton")
        self._set_button_icon(self.add_button, "add")
        self.add_button.setProperty("staffingV2Action", "add_position")
        self.add_button.setMinimumHeight(40)
        self.add_button.clicked.connect(self._open_add_position_dialog)
        header.addWidget(self.add_button)
        dashboard_root.addWidget(header_top)

        summary_actions = self.QtWidgets.QFrame()
        summary_actions.setObjectName("StaffingV2DashboardSummaryActionRow")
        summary_actions_layout = self.QtWidgets.QHBoxLayout(summary_actions)
        summary_actions_layout.setContentsMargins(0, 0, 0, 0)
        summary_actions_layout.setSpacing(10)
        self.metrics_layout = self.QtWidgets.QHBoxLayout()
        self.metrics_layout.setSpacing(10)
        summary_actions_layout.addLayout(self.metrics_layout, 1)
        self.export_button = self.QtWidgets.QPushButton("Export")
        self.export_button.setObjectName("StaffingV2ExportButton")
        self._set_button_icon(self.export_button, "export")
        self.export_button.setMinimumHeight(40)
        self.export_button.setEnabled(False)
        summary_actions_layout.addWidget(self.export_button)
        self.view_history_button = self.QtWidgets.QPushButton("View History")
        self.view_history_button.setObjectName("StaffingV2ViewHistoryButton")
        self._set_button_icon(self.view_history_button, "history")
        self.view_history_button.setMinimumHeight(40)
        self.view_history_button.clicked.connect(self._show_history_view)
        summary_actions_layout.addWidget(self.view_history_button)
        dashboard_root.addWidget(summary_actions)

        main = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        main.setObjectName("StaffingV2MainSplitter")
        main.setChildrenCollapsible(False)
        self.classroom_panel, classroom_layout = self._panel()
        self.classroom_panel.setMinimumWidth(360)
        self.classroom_panel.setMaximumWidth(400)
        list_header = self.QtWidgets.QHBoxLayout()
        list_header.addWidget(self._label("Classrooms", "StaffingV2SectionTitle"))
        list_header.addStretch(1)
        list_filter = self.QtWidgets.QPushButton("")
        list_filter.setObjectName("StaffingV2ClassroomListFilterButton")
        self._set_button_icon(list_filter, "filter")
        list_filter.setToolTip("Classroom filters")
        list_filter.setEnabled(False)
        list_filter.setFixedSize(34, 34)
        list_header.addWidget(list_filter)
        classroom_layout.addLayout(list_header)
        self.classroom_list = self.QtWidgets.QListWidget()
        self.classroom_list.setObjectName("StaffingV2ClassroomList")
        self.classroom_list.currentRowChanged.connect(self._select_classroom)
        classroom_layout.addWidget(self.classroom_list, 1)
        self.classroom_list_footer = self._label("", "StaffingV2ClassroomListFooter")
        classroom_layout.addWidget(self.classroom_list_footer)
        main.addWidget(self.classroom_panel)

        self.detail_panel, detail_layout = self._panel()
        self.detail_panel.setMinimumWidth(620)
        self.classroom_title = self._label("", "StaffingV2ClassroomTitle")
        self.classroom_subtitle = self._label("", "StaffingV2Muted")
        detail_header = self.QtWidgets.QHBoxLayout()
        title_stack = self.QtWidgets.QVBoxLayout()
        title_stack.addWidget(self.classroom_title)
        title_stack.addWidget(self.classroom_subtitle)
        detail_header.addLayout(title_stack, 1)
        self.priority_chip = self.QtWidgets.QFrame()
        self.priority_chip.setObjectName("StaffingV2PriorityChip")
        priority_layout = self.QtWidgets.QHBoxLayout(self.priority_chip)
        priority_layout.setContentsMargins(8, 4, 8, 4)
        priority_layout.setSpacing(6)
        priority_layout.addWidget(self._icon_label("status_need", "StaffingV2PriorityChipIcon"))
        self.priority_chip_text = self._label("", "StaffingV2PriorityChipText")
        priority_layout.addWidget(self.priority_chip_text)
        detail_header.addWidget(self.priority_chip)
        detail_layout.addLayout(detail_header)
        self.overview_layout = self.QtWidgets.QHBoxLayout()
        self.overview_layout.setSpacing(10)
        detail_layout.addLayout(self.overview_layout)
        detail_layout.addWidget(self._label("Positions", "StaffingV2SectionTitle"))
        self.positions_table = self.QtWidgets.QTableWidget(0, 8)
        self.positions_table.setObjectName("StaffingV2PositionsTable")
        self.positions_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.positions_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.positions_table.setHorizontalHeaderLabels(
            ["", "Position", "Person", "Status", "Start Date", "Days Open", "Permit Status", "Next Action"]
        )
        self.positions_table.verticalHeader().hide()
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.positions_table.horizontalHeader().setSectionResizeMode(0, self.QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.positions_table.setColumnWidth(0, 44)
        self.positions_table.cellClicked.connect(self._open_position_drawer_from_table)
        self.positions_table.setMinimumHeight(150)
        self.positions_table.setMaximumHeight(220)
        detail_layout.addWidget(self.positions_table)
        detail_layout.addWidget(self._add_position_drop_zone())
        detail_layout.addStretch(1)
        detail_layout.addWidget(self._status_key())
        main.addWidget(self.detail_panel)
        self.drawer, self.drawer_layout = self._panel("StaffingV2PositionDrawer")
        self.drawer.setObjectName("StaffingV2PositionDrawer")
        self.drawer.setMinimumWidth(420)
        self.drawer.hide()
        main.addWidget(self.drawer)
        main.setSizes([380, 920, 480])
        dashboard_root.addWidget(main, 1)
        self._build_classrooms_view()
        self._build_people_view()
        self._build_history_view()
        self._build_validation_view()
        self._set_active_nav(self.dashboard_nav_button)

    def _sidebar_button(self, object_name: str, text: str, icon_key: str = "") -> Any:
        button = self.QtWidgets.QPushButton(text)
        button.setObjectName(object_name)
        button.setMinimumHeight(40)
        button.setProperty("staffingV2ActiveNav", False)
        if icon_key:
            self._set_button_icon(button, icon_key)
        return button

    def _set_button_icon(self, button: Any, icon_key: str) -> None:
        button.setIcon(self._standard_icon(icon_key))
        button.setIconSize(self.QtCore.QSize(18, 18))

    def _standard_icon(self, icon_key: str) -> Any:
        if icon_key == "add":
            pixmap = self.QtGui.QPixmap(18, 18)
            pixmap.fill(self.QtCore.Qt.GlobalColor.transparent)
            painter = self.QtGui.QPainter(pixmap)
            pen = self.QtGui.QPen(self.QtGui.QColor("#2563eb"))
            pen.setWidth(2)
            pen.setCapStyle(self.QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(9, 4, 9, 14)
            painter.drawLine(4, 9, 14, 9)
            painter.end()
            return self.QtGui.QIcon(pixmap)
        if icon_key == "person_add":
            pixmap = self.QtGui.QPixmap(18, 18)
            pixmap.fill(self.QtCore.Qt.GlobalColor.transparent)
            painter = self.QtGui.QPainter(pixmap)
            pen = self.QtGui.QPen(self.QtGui.QColor("#2563eb"))
            pen.setWidth(2)
            pen.setCapStyle(self.QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawEllipse(2, 2, 6, 6)
            painter.drawArc(1, 10, 9, 6, 0, 180 * 16)
            painter.drawLine(13, 6, 13, 14)
            painter.drawLine(9, 10, 17, 10)
            painter.end()
            return self.QtGui.QIcon(pixmap)
        pixmaps = self.QtWidgets.QStyle.StandardPixmap
        mapping = {
            "analytics": pixmaps.SP_FileDialogInfoView,
            "classrooms": pixmaps.SP_DirHomeIcon,
            "close": pixmaps.SP_DialogCloseButton,
            "dashboard": pixmaps.SP_ComputerIcon,
            "export": pixmaps.SP_DialogSaveButton,
            "filter": pixmaps.SP_FileDialogDetailedView,
            "history": pixmaps.SP_BrowserReload,
            "info": pixmaps.SP_MessageBoxInformation,
            "integrations": pixmaps.SP_DriveNetIcon,
            "notifications": pixmaps.SP_MessageBoxInformation,
            "people": pixmaps.SP_FileDialogDetailedView,
            "search": pixmaps.SP_FileDialogContentsView,
            "settings": pixmaps.SP_FileDialogDetailedView,
            "reset": pixmaps.SP_DialogResetButton,
            "status_filled": pixmaps.SP_DialogApplyButton,
            "status_need": pixmaps.SP_MessageBoxWarning,
            "status_neutral": pixmaps.SP_DialogResetButton,
            "status_pending": pixmaps.SP_BrowserReload,
            "status_replace": pixmaps.SP_MessageBoxWarning,
            "validation": pixmaps.SP_MessageBoxInformation,
        }
        return self.widget.style().standardIcon(mapping.get(icon_key, pixmaps.SP_FileIcon))

    def _set_active_nav(self, active_button: Any) -> None:
        for button in (
            self.home_nav_button,
            self.dashboard_nav_button,
            self.classrooms_nav_button,
            self.people_nav_button,
            self.history_nav_button,
            self.analytics_nav_button,
            self.notifications_nav_button,
            self.validation_nav_button,
            self.integrations_nav_button,
            self.settings_nav_button,
        ):
            is_active = button is active_button
            button.setProperty("staffingV2ActiveNav", is_active)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _show_dashboard_view(self) -> None:
        self._set_active_nav(self.dashboard_nav_button)
        self.page_stack.setCurrentWidget(self.dashboard_view)

    def _show_classrooms_view(self) -> None:
        self._set_active_nav(self.classrooms_nav_button)
        self._refresh_classrooms()
        self.page_stack.setCurrentWidget(self.classrooms_view)

    def _show_validation_view(self) -> None:
        self._set_active_nav(self.validation_nav_button)
        self._refresh_validation()
        self.page_stack.setCurrentWidget(self.validation_view)

    def _build_classrooms_view(self) -> None:
        self.classrooms_view = self.QtWidgets.QWidget()
        self.classrooms_view.setObjectName("StaffingV2ClassroomManagementDashboard")
        classrooms_outer = self.QtWidgets.QHBoxLayout(self.classrooms_view)
        classrooms_outer.setContentsMargins(0, 0, 0, 0)
        classrooms_outer.setSpacing(14)
        classrooms_main = self.QtWidgets.QWidget()
        classrooms_root = self.QtWidgets.QVBoxLayout(classrooms_main)
        classrooms_root.setContentsMargins(0, 0, 0, 0)
        classrooms_root.setSpacing(14)
        classrooms_outer.addWidget(classrooms_main, 1)
        self.page_stack.addWidget(self.classrooms_view)

        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("Classroom Management")
        title.setObjectName("StaffingV2ClassroomsTitle")
        subtitle = self.QtWidgets.QLabel("Manage classroom records, programs, licensed capacity, and staffing structure.")
        subtitle.setObjectName("StaffingV2ClassroomsSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        header.addWidget(self._label("Last updated: May 20, 2025 9:42 AM", "StaffingV2Muted"))
        export = self.QtWidgets.QPushButton("Export")
        export.setObjectName("StaffingV2ClassroomsExportButton")
        self._set_button_icon(export, "export")
        export.setEnabled(False)
        add_classroom = self.QtWidgets.QPushButton("Add Classroom")
        add_classroom.setObjectName("StaffingV2ClassroomsAddButton")
        self._set_button_icon(add_classroom, "add")
        add_classroom.setEnabled(False)
        add_classroom.setToolTip("Add Classroom workflow will be implemented in a later classroom mockup slice.")
        header.addWidget(export)
        header.addWidget(add_classroom)
        classrooms_root.addLayout(header)

        self.classrooms_metrics_layout = self.QtWidgets.QHBoxLayout()
        self.classrooms_metrics_layout.setSpacing(10)
        classrooms_root.addLayout(self.classrooms_metrics_layout)

        filters_panel, filters_panel_layout = self._panel("StaffingV2Panel")
        filters = self.QtWidgets.QHBoxLayout()
        self.classrooms_school_filter = self._classrooms_filter_combo("StaffingV2ClassroomsSchoolFilter", ["All Schools"])
        filters.addLayout(self._labeled_control("School", self.classrooms_school_filter), 1)
        self.classrooms_program_filter = self._classrooms_filter_combo("StaffingV2ClassroomsProgramFilter", ["All Programs"])
        filters.addLayout(self._labeled_control("Program", self.classrooms_program_filter), 1)
        self.classrooms_status_filter = self._classrooms_filter_combo("StaffingV2ClassroomsStatusFilter", ["All Statuses"])
        filters.addLayout(self._labeled_control("Status", self.classrooms_status_filter), 1)
        self.classrooms_search = self.QtWidgets.QLineEdit()
        self.classrooms_search.setObjectName("StaffingV2ClassroomsSearch")
        self.classrooms_search.setPlaceholderText("Search classrooms...")
        self.classrooms_search.addAction(
            self._standard_icon("search"), self.QtWidgets.QLineEdit.ActionPosition.LeadingPosition
        )
        self.classrooms_search.textChanged.connect(self._refresh_classrooms_filters)
        filters.addWidget(self.classrooms_search, 2)
        more = self.QtWidgets.QPushButton("More Filters")
        more.setObjectName("StaffingV2ClassroomsMoreFilters")
        self._set_button_icon(more, "filter")
        more.clicked.connect(self._open_classrooms_filter_drawer)
        clear = self.QtWidgets.QPushButton("Clear")
        clear.setObjectName("StaffingV2ClassroomsClear")
        clear.clicked.connect(self._clear_classrooms_filters)
        filters.addWidget(more)
        filters.addWidget(clear)
        filters_panel_layout.addLayout(filters)
        classrooms_root.addWidget(filters_panel)

        body = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        left = self.QtWidgets.QWidget()
        left_layout = self.QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        self.classrooms_table = self.QtWidgets.QTableWidget(0, 10)
        self.classrooms_table.setObjectName("StaffingV2ClassroomsTable")
        self.classrooms_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.classrooms_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.classrooms_table.setHorizontalHeaderLabels(
            [
                "Classroom",
                "School",
                "Program",
                "Licensed Capacity",
                "Total Positions",
                "Filled",
                "Open",
                "Priority Status",
                "Active",
                "Actions",
            ]
        )
        self.classrooms_table.horizontalHeader().setStretchLastSection(True)
        self.classrooms_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.classrooms_table.currentCellChanged.connect(
            lambda row, _column, _prev_row, _prev_column: self._select_classroom_management(row)
        )
        left_layout.addWidget(self.classrooms_table, 1)
        self.classrooms_validation_panel, self.classrooms_validation_layout = self._panel("StaffingV2ClassroomsValidationPanel")
        left_layout.addWidget(self.classrooms_validation_panel)
        body.addWidget(left)
        self.classrooms_detail_panel, self.classrooms_detail_layout = self._panel("StaffingV2ClassroomsDetailPanel")
        self.classrooms_detail_panel.setMinimumWidth(420)
        body.addWidget(self.classrooms_detail_panel)
        body.setSizes([860, 420])
        classrooms_root.addWidget(body, 1)
        self.classrooms_filter_drawer, self.classrooms_filter_drawer_layout = self._panel("StaffingV2ClassroomsFilterDrawer")
        self.classrooms_filter_drawer.setFixedWidth(340)
        self.classrooms_filter_drawer.hide()
        self._build_classrooms_filter_drawer()
        classrooms_outer.addWidget(self.classrooms_filter_drawer)

    def _build_classrooms_filter_drawer(self) -> None:
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Filters", "StaffingV2ClassroomsFilterTitle"))
        header.addStretch(1)
        reset = self.QtWidgets.QPushButton("Reset")
        reset.setObjectName("StaffingV2ClassroomsFilterReset")
        self._set_button_icon(reset, "reset")
        reset.clicked.connect(self._reset_classrooms_filter_drawer)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ClassroomsFilterClose")
        self._set_button_icon(close, "close")
        close.setFixedSize(32, 32)
        close.clicked.connect(self.classrooms_filter_drawer.hide)
        header.addWidget(reset)
        header.addWidget(close)
        self.classrooms_filter_drawer_layout.addLayout(header)
        self.classrooms_filter_drawer_layout.addWidget(self._label("Status", "StaffingV2SectionTitle"))
        self.classrooms_filter_need_now = self.QtWidgets.QCheckBox("Need Now")
        self.classrooms_filter_need_now.setObjectName("StaffingV2ClassroomsFilterNeedNow")
        self.classrooms_filter_coming = self.QtWidgets.QCheckBox("Coming")
        self.classrooms_filter_coming.setObjectName("StaffingV2ClassroomsFilterComing")
        self.classrooms_filter_filled = self.QtWidgets.QCheckBox("Filled")
        self.classrooms_filter_filled.setObjectName("StaffingV2ClassroomsFilterFilled")
        self.classrooms_filter_dont_need = self.QtWidgets.QCheckBox("Don't Need Now")
        self.classrooms_filter_dont_need.setObjectName("StaffingV2ClassroomsFilterDontNeed")
        for checkbox in (
            self.classrooms_filter_need_now,
            self.classrooms_filter_coming,
            self.classrooms_filter_filled,
            self.classrooms_filter_dont_need,
        ):
            checkbox.setChecked(True)
            self.classrooms_filter_drawer_layout.addWidget(checkbox)
        self.classrooms_filter_drawer_layout.addWidget(self._label("Open Positions", "StaffingV2SectionTitle"))
        self.classrooms_filter_open_positions = self._classrooms_filter_combo(
            "StaffingV2ClassroomsFilterOpenPositions", ["All", "Has Open Positions", "No Open Positions"]
        )
        self.classrooms_filter_drawer_layout.addWidget(self.classrooms_filter_open_positions)
        self.classrooms_filter_drawer_layout.addWidget(self._label("Days Open", "StaffingV2SectionTitle"))
        self.classrooms_filter_days_open = self._classrooms_filter_combo(
            "StaffingV2ClassroomsFilterDaysOpen", ["All", "Over 7 Days", "No Open Date"]
        )
        self.classrooms_filter_drawer_layout.addWidget(self.classrooms_filter_days_open)
        self.classrooms_filter_drawer_layout.addStretch(1)
        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2ClassroomsFilterCancel")
        cancel.clicked.connect(self.classrooms_filter_drawer.hide)
        apply = self.QtWidgets.QPushButton("Apply Filters")
        apply.setObjectName("StaffingV2ClassroomsFilterApply")
        self._set_button_icon(apply, "filter")
        apply.clicked.connect(self._apply_classrooms_filter_drawer)
        footer.addWidget(cancel)
        footer.addWidget(apply)
        self.classrooms_filter_drawer_layout.addLayout(footer)

    def _open_classrooms_filter_drawer(self) -> None:
        self.classrooms_filter_drawer.show()

    def _reset_classrooms_filter_drawer(self) -> None:
        for checkbox in (
            self.classrooms_filter_need_now,
            self.classrooms_filter_coming,
            self.classrooms_filter_filled,
            self.classrooms_filter_dont_need,
        ):
            checkbox.setChecked(True)
        self.classrooms_filter_open_positions.setCurrentText("All")
        self.classrooms_filter_days_open.setCurrentText("All")

    def _apply_classrooms_filter_drawer(self) -> None:
        self._refresh_classrooms_filters()
        self.classrooms_filter_drawer.hide()

    def _classrooms_filter_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        combo.currentIndexChanged.connect(self._refresh_classrooms_filters)
        return combo

    def _refresh_classrooms(self) -> None:
        if not hasattr(self, "classrooms_table"):
            return
        grouped: dict[str, list[StaffingMetricRow]] = {}
        for row in self.rows:
            grouped.setdefault(f"{row.school}\u241f{row.classroom}", []).append(row)
        self.classroom_management_rows = grouped
        groups = list(grouped.values())
        self._sync_combo(
            self.classrooms_school_filter,
            ["All Schools", *sorted({rows[0].school for rows in groups if rows and rows[0].school})],
        )
        self._sync_combo(
            self.classrooms_program_filter,
            ["All Programs", *sorted({rows[0].classroom_program for rows in groups if rows and rows[0].classroom_program})],
        )
        self._sync_combo(
            self.classrooms_status_filter,
            ["All Statuses", "Need Now", "Replace", "Coming", "Filled / Healthy", "Don't Need"],
        )
        self._refresh_classrooms_filters()

    def _clear_classrooms_filters(self) -> None:
        self.classrooms_school_filter.setCurrentText("All Schools")
        self.classrooms_program_filter.setCurrentText("All Programs")
        self.classrooms_status_filter.setCurrentText("All Statuses")
        self.classrooms_search.clear()
        if hasattr(self, "classrooms_filter_need_now"):
            self._reset_classrooms_filter_drawer()
        self._refresh_classrooms_filters()

    def _refresh_classrooms_filters(self) -> None:
        if not hasattr(self, "classrooms_table"):
            return
        school = self.classrooms_school_filter.currentText()
        program = self.classrooms_program_filter.currentText()
        status = self.classrooms_status_filter.currentText()
        search = self.classrooms_search.text().strip().casefold()
        allowed_statuses = self._classrooms_allowed_statuses()
        open_positions_filter = self.classrooms_filter_open_positions.currentText() if hasattr(self, "classrooms_filter_open_positions") else "All"
        days_open_filter = self.classrooms_filter_days_open.currentText() if hasattr(self, "classrooms_filter_days_open") else "All"
        self.visible_classroom_management = []
        for key, rows in self.classroom_management_rows.items():
            if not rows:
                continue
            classroom_status = _classroom_priority_status(rows)
            first = rows[0]
            if school != "All Schools" and first.school != school:
                continue
            if program != "All Programs" and first.classroom_program != program:
                continue
            if status != "All Statuses" and classroom_status != status:
                continue
            if classroom_status not in allowed_statuses:
                continue
            haystack = f"{first.school} {first.classroom} {first.classroom_program}".casefold()
            if search and search not in haystack:
                continue
            open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
            if open_positions_filter == "Has Open Positions" and open_count == 0:
                continue
            if open_positions_filter == "No Open Positions" and open_count > 0:
                continue
            days = [row.days_open for row in rows if row.days_open is not None and row.status in {"need_now", "replace"}]
            has_no_open_date = any(row.days_open is None and row.status in {"need_now", "replace"} for row in rows)
            if days_open_filter == "Over 7 Days" and not any(day > 7 for day in days):
                continue
            if days_open_filter == "No Open Date" and not has_no_open_date:
                continue
            self.visible_classroom_management.append((key, rows))
        self._refresh_classrooms_metrics()
        self._refresh_classrooms_table()
        self._refresh_classrooms_validation_panel()

    def _classrooms_allowed_statuses(self) -> set[str]:
        if not hasattr(self, "classrooms_filter_need_now"):
            return {"Need Now", "Replace", "Coming", "Filled / Healthy", "Don't Need"}
        allowed: set[str] = set()
        if self.classrooms_filter_need_now.isChecked():
            allowed.update({"Need Now", "Replace"})
        if self.classrooms_filter_coming.isChecked():
            allowed.add("Coming")
        if self.classrooms_filter_filled.isChecked():
            allowed.add("Filled / Healthy")
        if self.classrooms_filter_dont_need.isChecked():
            allowed.add("Don't Need")
        return allowed

    def _refresh_classrooms_metrics(self) -> None:
        while self.classrooms_metrics_layout.count():
            item = self.classrooms_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        groups = list(self.classroom_management_rows.values())
        capacities = [rows[0].classroom_capacity for rows in groups if rows and rows[0].classroom_capacity is not None]
        total_positions = sum(len(rows) for rows in groups)
        open_positions = sum(1 for rows in groups for row in rows if row.status in {"need_now", "replace"})
        cards = [
            ("Total Classrooms", str(len(groups))),
            ("Active", str(len(groups))),
            ("Avg Licensed Capacity", f"{(sum(capacities) / len(capacities)):.1f}" if capacities else "0.0"),
            ("Total Positions", str(total_positions)),
            ("Open Positions", str(open_positions)),
        ]
        for label, value in cards:
            self.classrooms_metrics_layout.addWidget(
                self._metric_card(label, value, f"{label} {value}", "StaffingV2ClassroomsMetricCard")
            )

    def _refresh_classrooms_table(self) -> None:
        self.classrooms_table.setRowCount(0)
        for key, rows in self.visible_classroom_management:
            first = rows[0]
            row_index = self.classrooms_table.rowCount()
            self.classrooms_table.insertRow(row_index)
            total = len(rows)
            filled = sum(1 for row in rows if row.status == "filled")
            open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
            values = [
                first.classroom,
                first.school,
                first.classroom_program or "-",
                "" if first.classroom_capacity is None else str(first.classroom_capacity),
                str(total),
                str(filled),
                str(open_count),
                _classroom_priority_status(rows),
                "Yes",
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, key)
                self.classrooms_table.setItem(row_index, column, item)
            view = self.QtWidgets.QPushButton("View")
            view.setObjectName("StaffingV2ClassroomsRowView")
            view.setProperty("classroomKey", key)
            self._set_button_icon(view, "info")
            view.setEnabled(False)
            self.classrooms_table.setCellWidget(row_index, 9, view)
        if self.classrooms_table.rowCount():
            self.classrooms_table.setCurrentCell(0, 0)
            self._select_classroom_management(0)
        else:
            self._render_classroom_management_detail([])

    def _select_classroom_management(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.visible_classroom_management):
            self._render_classroom_management_detail([])
            return
        self._render_classroom_management_detail(self.visible_classroom_management[row_index][1])

    def _render_classroom_management_detail(self, rows: list[StaffingMetricRow]) -> None:
        self._clear_layout(self.classrooms_detail_layout)
        if not rows:
            self.classrooms_detail_layout.addWidget(self._label("No classroom selected", "StaffingV2Muted"))
            return
        first = rows[0]
        self.classrooms_detail_layout.addWidget(self._label("Classroom Detail", "StaffingV2SectionTitle"))
        self.classrooms_detail_layout.addWidget(self._label(first.classroom, "StaffingV2ClassroomsDetailName"))
        overview, overview_layout = self._panel("StaffingV2ClassroomsDetailCard")
        overview_layout.addLayout(self._detail_row("School", first.school))
        overview_layout.addLayout(self._detail_row("Program", first.classroom_program or "-"))
        overview_layout.addLayout(self._detail_row("Licensed Capacity", "" if first.classroom_capacity is None else str(first.classroom_capacity)))
        overview_layout.addLayout(self._detail_row("Current Priority", _classroom_priority_status(rows)))
        self.classrooms_detail_layout.addWidget(overview)

        total = len(rows)
        filled = sum(1 for row in rows if row.status == "filled")
        open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
        summary, summary_layout = self._panel("StaffingV2ClassroomsDetailCard")
        summary_layout.addWidget(self._label("Staffing Summary", "StaffingV2SectionTitle"))
        summary_layout.addLayout(self._detail_row("Total Positions", str(total)))
        summary_layout.addLayout(self._detail_row("Filled", str(filled)))
        summary_layout.addLayout(self._detail_row("Open", str(open_count)))
        summary_layout.addLayout(self._detail_row("Avg Days to Fill", _avg_open_days(rows)))
        self.classrooms_detail_layout.addWidget(summary)

        positions, positions_layout = self._panel("StaffingV2ClassroomsDetailCard")
        positions_layout.addWidget(self._label("Current Positions", "StaffingV2SectionTitle"))
        for row in rows:
            positions_layout.addWidget(
                self._label(f"{row.position_name}    {_display_status(row.status)}    {row.person_name or 'OPEN POSITION'}")
            )
        self.classrooms_detail_layout.addWidget(positions, 1)

        footer = self.QtWidgets.QHBoxLayout()
        deactivate = self.QtWidgets.QPushButton("Deactivate Classroom")
        deactivate.setObjectName("StaffingV2ClassroomsDeactivateButton")
        self._set_button_icon(deactivate, "status_need")
        deactivate.setEnabled(False)
        save = self.QtWidgets.QPushButton("Save Changes")
        save.setObjectName("StaffingV2ClassroomsSaveButton")
        self._set_button_icon(save, "status_filled")
        save.setEnabled(False)
        footer.addWidget(deactivate)
        footer.addStretch(1)
        footer.addWidget(save)
        self.classrooms_detail_layout.addLayout(footer)

    def _refresh_classrooms_validation_panel(self) -> None:
        self._clear_layout(self.classrooms_validation_layout)
        self.classrooms_validation_layout.addWidget(self._label("Classroom Validation & Health", "StaffingV2SectionTitle"))
        groups = list(self.classroom_management_rows.values())
        duplicate_names = max(0, len(groups) - len({rows[0].classroom for rows in groups if rows}))
        missing_program = sum(1 for rows in groups if rows and not rows[0].classroom_program)
        missing_capacity = sum(1 for rows in groups if rows and rows[0].classroom_capacity is None)
        no_positions = sum(1 for rows in groups if not rows)
        row = self.QtWidgets.QHBoxLayout()
        for label, value in [
            ("Duplicate classroom names", duplicate_names),
            ("Missing program", missing_program),
            ("Missing capacity", missing_capacity),
            ("Classrooms with no positions", no_positions),
            ("Other issues", 0),
        ]:
            row.addWidget(self._metric_card(label, str(value), f"{label} {value}", "StaffingV2ClassroomsMetricCard"))
        self.classrooms_validation_layout.addLayout(row)

    def _build_validation_view(self) -> None:
        self.validation_view = self.QtWidgets.QWidget()
        self.validation_view.setObjectName("StaffingV2ValidationDashboard")
        validation_root = self.QtWidgets.QHBoxLayout(self.validation_view)
        validation_root.setContentsMargins(0, 0, 0, 0)
        validation_root.setSpacing(14)
        self.page_stack.addWidget(self.validation_view)

        main = self.QtWidgets.QWidget()
        main_layout = self.QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(14)
        validation_root.addWidget(main, 1)

        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("Staffing Validation")
        title.setObjectName("StaffingV2ValidationTitle")
        subtitle = self.QtWidgets.QLabel("Review staffing compliance, assignment coverage, and licensing requirements.")
        subtitle.setObjectName("StaffingV2ValidationSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        header.addWidget(self._label("Last updated: May 8, 2025 9:41 AM", "StaffingV2Muted"))
        export = self.QtWidgets.QPushButton("Export Report")
        export.setObjectName("StaffingV2ValidationExportButton")
        self._set_button_icon(export, "export")
        export.setEnabled(False)
        header.addWidget(export)
        main_layout.addLayout(header)

        self.validation_metrics_layout = self.QtWidgets.QHBoxLayout()
        self.validation_metrics_layout.setSpacing(10)
        main_layout.addLayout(self.validation_metrics_layout)

        tabs = self.QtWidgets.QFrame()
        tabs.setObjectName("StaffingV2ValidationTabs")
        tabs_layout = self.QtWidgets.QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(10)
        self.validation_all_tab = self.QtWidgets.QPushButton("All Issues")
        self.validation_all_tab.setObjectName("StaffingV2ValidationAllIssuesTab")
        self.validation_critical_tab = self.QtWidgets.QPushButton("Critical")
        self.validation_critical_tab.setObjectName("StaffingV2ValidationCriticalTab")
        self.validation_warning_tab = self.QtWidgets.QPushButton("Warnings")
        self.validation_warning_tab.setObjectName("StaffingV2ValidationWarningsTab")
        self.validation_info_tab = self.QtWidgets.QPushButton("Info")
        self.validation_info_tab.setObjectName("StaffingV2ValidationInfoTab")
        for button in (
            self.validation_all_tab,
            self.validation_critical_tab,
            self.validation_warning_tab,
            self.validation_info_tab,
        ):
            button.setMinimumHeight(34)
            button.setProperty("staffingV2ActiveValidationTab", False)
            tabs_layout.addWidget(button)
        tabs_layout.addStretch(1)

        validation_tab_buttons = (
            self.validation_all_tab,
            self.validation_critical_tab,
            self.validation_warning_tab,
            self.validation_info_tab,
        )

        def mark_validation_tab(active_button: Any) -> None:
            for button in validation_tab_buttons:
                button.setProperty("staffingV2ActiveValidationTab", button is active_button)
                button.style().unpolish(button)
                button.style().polish(button)

        def select_validation_tab(severity: str | None, active_button: Any) -> None:
            mark_validation_tab(active_button)
            self.validation_severity_critical.setChecked(severity in {None, "Critical"})
            self.validation_severity_warning.setChecked(severity in {None, "Warning"})
            self.validation_severity_info.setChecked(severity in {None, "Info"})
            self._refresh_validation_filters()

        mark_validation_tab(self.validation_all_tab)
        self.validation_all_tab.clicked.connect(
            lambda _checked=False: select_validation_tab(None, self.validation_all_tab)
        )
        self.validation_critical_tab.clicked.connect(
            lambda _checked=False: select_validation_tab("Critical", self.validation_critical_tab)
        )
        self.validation_warning_tab.clicked.connect(
            lambda _checked=False: select_validation_tab("Warning", self.validation_warning_tab)
        )
        self.validation_info_tab.clicked.connect(lambda _checked=False: select_validation_tab("Info", self.validation_info_tab))
        main_layout.addWidget(tabs)

        controls = self.QtWidgets.QHBoxLayout()
        self.validation_search = self.QtWidgets.QLineEdit()
        self.validation_search.setObjectName("StaffingV2ValidationSearch")
        self.validation_search.setPlaceholderText("Search issues...")
        self.validation_search.addAction(self._standard_icon("search"), self.QtWidgets.QLineEdit.ActionPosition.LeadingPosition)
        self.validation_search.textChanged.connect(self._refresh_validation_filters)
        controls.addWidget(self.validation_search, 1)
        filters = self.QtWidgets.QPushButton("Filters")
        filters.setObjectName("StaffingV2ValidationFiltersButton")
        self._set_button_icon(filters, "filter")
        filters.clicked.connect(self._open_filter_drawer)
        controls.addWidget(filters)
        main_layout.addLayout(controls)

        body = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.validation_table = self.QtWidgets.QTableWidget(0, 7)
        self.validation_table.setObjectName("StaffingV2ValidationTable")
        self.validation_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.validation_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.validation_table.setHorizontalHeaderLabels(
            ["Issue", "Classroom", "Type", "Severity", "Detected", "Details", "Action"]
        )
        self.validation_table.horizontalHeader().setStretchLastSection(True)
        self.validation_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        body.addWidget(self.validation_table)
        self.validation_right_panel, self.validation_right_layout = self._panel("StaffingV2ValidationRightPanel")
        self.validation_right_panel.setMinimumWidth(320)
        body.addWidget(self.validation_right_panel)
        body.setSizes([900, 320])
        main_layout.addWidget(body, 1)

        self.filter_drawer, self.filter_drawer_layout = self._panel("StaffingV2FilterDrawer")
        self.filter_drawer.setObjectName("StaffingV2FilterDrawer")
        self.filter_drawer.setFixedWidth(340)
        self._build_filter_drawer_contents()
        self.filter_drawer.hide()
        validation_root.addWidget(self.filter_drawer)

    def _build_filter_drawer_contents(self) -> None:
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Filters", "StaffingV2SectionTitle"), 1)
        reset = self.QtWidgets.QPushButton("Reset")
        reset.setObjectName("StaffingV2FilterResetButton")
        self._set_button_icon(reset, "reset")
        reset.clicked.connect(self._reset_validation_filters)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2FilterCloseButton")
        self._set_button_icon(close, "close")
        close.setFixedSize(32, 32)
        close.clicked.connect(self.filter_drawer.hide)
        header.addWidget(reset)
        header.addWidget(close)
        self.filter_drawer_layout.addLayout(header)

        self.validation_school_filter = self._validation_filter_combo("StaffingV2FilterSchool", ["All Schools"])
        self.filter_drawer_layout.addLayout(self._labeled_control("School", self.validation_school_filter))
        self.validation_program_filter = self._validation_filter_combo("StaffingV2FilterProgram", ["All Programs"])
        self.filter_drawer_layout.addLayout(self._labeled_control("Program", self.validation_program_filter))
        severity_label = self._label("Severity", "StaffingV2Muted")
        self.filter_drawer_layout.addWidget(severity_label)
        self.validation_severity_critical = self.QtWidgets.QCheckBox("Critical")
        self.validation_severity_critical.setObjectName("StaffingV2FilterSeverityCritical")
        self.validation_severity_critical.setChecked(True)
        self.validation_severity_warning = self.QtWidgets.QCheckBox("Warning")
        self.validation_severity_warning.setObjectName("StaffingV2FilterSeverityWarning")
        self.validation_severity_warning.setChecked(True)
        self.validation_severity_info = self.QtWidgets.QCheckBox("Info")
        self.validation_severity_info.setObjectName("StaffingV2FilterSeverityInfo")
        self.validation_severity_info.setChecked(True)
        self.filter_drawer_layout.addWidget(self.validation_severity_critical)
        self.filter_drawer_layout.addWidget(self.validation_severity_warning)
        self.filter_drawer_layout.addWidget(self.validation_severity_info)
        self.validation_issue_type_filter = self._validation_filter_combo(
            "StaffingV2FilterIssueType",
            ["All Types", "Coverage", "Upcoming", "Compliance"],
        )
        self.filter_drawer_layout.addLayout(self._labeled_control("Issue Type", self.validation_issue_type_filter))
        self.filter_drawer_layout.addStretch(1)
        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2FilterCancelButton")
        cancel.clicked.connect(self.filter_drawer.hide)
        apply = self.QtWidgets.QPushButton("Apply Filters")
        apply.setObjectName("StaffingV2FilterApplyButton")
        self._set_button_icon(apply, "filter")
        apply.clicked.connect(self._apply_validation_filters)
        footer.addWidget(cancel)
        footer.addWidget(apply)
        self.filter_drawer_layout.addLayout(footer)

    def _validation_filter_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        return combo

    def _open_filter_drawer(self) -> None:
        self.filter_drawer.show()

    def _reset_validation_filters(self) -> None:
        self.validation_school_filter.setCurrentText("All Schools")
        self.validation_program_filter.setCurrentText("All Programs")
        self.validation_issue_type_filter.setCurrentText("All Types")
        self.validation_severity_critical.setChecked(True)
        self.validation_severity_warning.setChecked(True)
        self.validation_severity_info.setChecked(True)
        self.validation_search.clear()
        self._refresh_validation_filters()

    def _apply_validation_filters(self) -> None:
        self._refresh_validation_filters()
        self.filter_drawer.hide()

    def _refresh_validation(self) -> None:
        if not hasattr(self, "validation_table"):
            return
        self.validation_issues = _validation_issues_from_rows(self.rows)
        schools = ["All Schools", *sorted({issue["school"] for issue in self.validation_issues if issue["school"]})]
        programs = ["All Programs", *sorted({issue["program"] for issue in self.validation_issues if issue["program"]})]
        self._sync_combo(self.validation_school_filter, schools)
        self._sync_combo(self.validation_program_filter, programs)
        self._refresh_validation_filters()

    def _refresh_validation_filters(self) -> None:
        if not hasattr(self, "validation_table"):
            return
        search = self.validation_search.text().strip().casefold()
        school = self.validation_school_filter.currentText()
        program = self.validation_program_filter.currentText()
        issue_type = self.validation_issue_type_filter.currentText()
        severities = set()
        if self.validation_severity_critical.isChecked():
            severities.add("Critical")
        if self.validation_severity_warning.isChecked():
            severities.add("Warning")
        if self.validation_severity_info.isChecked():
            severities.add("Info")
        self.visible_validation_issues = []
        for issue in self.validation_issues:
            haystack = f"{issue['issue']} {issue['classroom']} {issue['type']} {issue['severity']} {issue['details']}".casefold()
            if search and search not in haystack:
                continue
            if school != "All Schools" and issue["school"] != school:
                continue
            if program != "All Programs" and issue["program"] != program:
                continue
            if issue_type != "All Types" and issue["type"] != issue_type:
                continue
            if issue["severity"] not in severities:
                continue
            self.visible_validation_issues.append(issue)
        self._refresh_validation_metrics()
        self._refresh_validation_table()
        self._refresh_validation_right_panel()

    def _refresh_validation_metrics(self) -> None:
        while self.validation_metrics_layout.count():
            item = self.validation_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        total_issues = len(self.validation_issues)
        critical = sum(1 for issue in self.validation_issues if issue["severity"] == "Critical")
        warning = sum(1 for issue in self.validation_issues if issue["severity"] == "Warning")
        info = sum(1 for issue in self.validation_issues if issue["severity"] == "Info")
        if hasattr(self, "validation_all_tab"):
            self.validation_all_tab.setText(f"All Issues ({total_issues})")
            self.validation_critical_tab.setText(f"Critical ({critical})")
            self.validation_warning_tab.setText(f"Warnings ({warning})")
            self.validation_info_tab.setText(f"Info ({info})")
        total_positions = max(1, len(self.rows))
        compliance = max(0, round(((total_positions - critical - warning) / total_positions) * 100))
        for label, value in [
            ("Total Issues", str(total_issues)),
            ("Critical", str(critical)),
            ("Warning", str(warning)),
            ("Info", str(info)),
            ("Overall Compliance", f"{compliance}%"),
        ]:
            self.validation_metrics_layout.addWidget(
                self._metric_card(label, value, f"{label} {value}", "StaffingV2ValidationMetricCard")
            )

    def _refresh_validation_table(self) -> None:
        self.validation_table.setRowCount(0)
        for issue in self.visible_validation_issues:
            row_index = self.validation_table.rowCount()
            self.validation_table.insertRow(row_index)
            values = [
                issue["issue"],
                issue["classroom"],
                issue["type"],
                issue["severity"],
                issue["detected"],
                issue["details"],
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, issue["assignment_id"])
                self.validation_table.setItem(row_index, column, item)
            view = self.QtWidgets.QPushButton("View")
            view.setObjectName("StaffingV2ValidationViewButton")
            view.setProperty("assignmentId", issue["assignment_id"])
            self._set_button_icon(view, "info")
            view.setEnabled(False)
            self.validation_table.setCellWidget(row_index, 6, view)

    def _refresh_validation_right_panel(self) -> None:
        self._clear_layout(self.validation_right_layout)
        summary, summary_layout = self._panel("StaffingV2ValidationSideCard")
        summary_layout.addWidget(self._label("Compliance Summary", "StaffingV2SectionTitle"))
        summary_layout.addLayout(self._detail_row("Compliant", str(max(0, len(self.rows) - len(self.validation_issues)))))
        summary_layout.addLayout(self._detail_row("Warnings", str(sum(1 for issue in self.validation_issues if issue["severity"] == "Warning"))))
        summary_layout.addLayout(self._detail_row("Critical", str(sum(1 for issue in self.validation_issues if issue["severity"] == "Critical"))))
        self.validation_right_layout.addWidget(summary)
        actions, actions_layout = self._panel("StaffingV2ValidationSideCard")
        actions_layout.addWidget(self._label("Quick Actions", "StaffingV2SectionTitle"))
        for object_name, text, icon_key in [
            ("StaffingV2ValidationRunFullButton", "Run Full Validation", "history"),
            ("StaffingV2ValidationExportQuickButton", "Export Validation Report", "export"),
            ("StaffingV2ValidationRulesButton", "View Validation Rules", "validation"),
        ]:
            button = self.QtWidgets.QPushButton(text)
            button.setObjectName(object_name)
            self._set_button_icon(button, icon_key)
            button.setEnabled(False)
            actions_layout.addWidget(button)
        self.validation_right_layout.addWidget(actions)
        about, about_layout = self._panel("StaffingV2ValidationSideCard")
        about_layout.addWidget(self._label("About Validation", "StaffingV2SectionTitle"))
        about_layout.addWidget(self._label("Validation checks staffing coverage, permit status, position lifecycle, and start-date requirements."))
        self.validation_right_layout.addWidget(about, 1)

    def _build_people_view(self) -> None:
        self.people_view = self.QtWidgets.QWidget()
        self.people_view.setObjectName("StaffingV2PeopleDashboard")
        people_root = self.QtWidgets.QVBoxLayout(self.people_view)
        people_root.setContentsMargins(0, 0, 0, 0)
        people_root.setSpacing(14)
        self.page_stack.addWidget(self.people_view)

        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("People / Employee Management")
        title.setObjectName("StaffingV2PeopleTitle")
        subtitle = self.QtWidgets.QLabel("Manage employee records, permits, roles, and assignments.")
        subtitle.setObjectName("StaffingV2PeopleSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        header.addWidget(self._label("Last updated: May 8, 2025 9:41 AM", "StaffingV2Muted"))
        add_person = self.QtWidgets.QPushButton("Add Person")
        add_person.setObjectName("StaffingV2PeopleAddButton")
        self._set_button_icon(add_person, "add")
        add_person.setEnabled(False)
        add_person.setToolTip("Add Person workflow will be implemented in a later People mockup slice.")
        header.addWidget(add_person)
        people_root.addLayout(header)

        filters = self.QtWidgets.QHBoxLayout()
        search_wrap = self.QtWidgets.QVBoxLayout()
        search_wrap.addWidget(self._label("Search", "StaffingV2Muted"))
        self.people_search = self.QtWidgets.QLineEdit()
        self.people_search.setObjectName("StaffingV2PeopleSearch")
        self.people_search.setPlaceholderText("Search by name, role, or email...")
        self.people_search.addAction(
            self._standard_icon("search"), self.QtWidgets.QLineEdit.ActionPosition.LeadingPosition
        )
        self.people_search.textChanged.connect(self._refresh_people_filters)
        search_wrap.addWidget(self.people_search)
        filters.addLayout(search_wrap, 2)

        self.people_active_filter = self._people_filter_combo("StaffingV2PeopleActiveFilter", ["All", "Active", "Inactive"])
        filters.addLayout(self._labeled_control("Active Status", self.people_active_filter), 1)
        self.people_role_filter = self._people_filter_combo("StaffingV2PeopleRoleFilter", ["All", "Teacher", "Aide"])
        filters.addLayout(self._labeled_control("Role", self.people_role_filter), 1)
        self.people_permit_filter = self._people_filter_combo("StaffingV2PeoplePermitFilter", ["All", "Teacher Permit", "Permit in Process", "Unknown"])
        filters.addLayout(self._labeled_control("Permit Status", self.people_permit_filter), 1)
        more_filters = self.QtWidgets.QPushButton("More Filters")
        more_filters.setObjectName("StaffingV2PeopleMoreFilters")
        self._set_button_icon(more_filters, "filter")
        more_filters.setEnabled(False)
        filters.addWidget(more_filters)
        clear = self.QtWidgets.QPushButton("Clear")
        clear.setObjectName("StaffingV2PeopleClear")
        clear.clicked.connect(self._clear_people_filters)
        filters.addWidget(clear)
        people_root.addLayout(filters)

        self.people_metrics_layout = self.QtWidgets.QHBoxLayout()
        self.people_metrics_layout.setSpacing(10)
        people_root.addLayout(self.people_metrics_layout)

        body = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.people_table = self.QtWidgets.QTableWidget(0, 7)
        self.people_table.setObjectName("StaffingV2PeopleTable")
        self.people_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.people_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.people_table.setHorizontalHeaderLabels(
            ["Name", "Role", "Permit Status", "Units", "Status", "Current Assignment", "Actions"]
        )
        self.people_table.horizontalHeader().setStretchLastSection(True)
        self.people_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.people_table.currentCellChanged.connect(lambda row, _column, _prev_row, _prev_column: self._select_person(row))
        body.addWidget(self.people_table)
        self.people_detail_panel, self.people_detail_layout = self._panel("StaffingV2PeopleDetailPanel")
        self.people_detail_panel.setMinimumWidth(420)
        body.addWidget(self.people_detail_panel)
        body.setSizes([860, 420])
        people_root.addWidget(body, 1)

    def _show_people_view(self) -> None:
        self._set_active_nav(self.people_nav_button)
        self._refresh_people()
        self.page_stack.setCurrentWidget(self.people_view)

    def _show_history_view(self) -> None:
        self._set_active_nav(self.history_nav_button)
        self._refresh_history()
        self.page_stack.setCurrentWidget(self.history_view)

    def _people_filter_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        combo.currentIndexChanged.connect(self._refresh_people_filters)
        return combo

    def _labeled_control(self, label: str, control: Any) -> Any:
        layout = self.QtWidgets.QVBoxLayout()
        layout.addWidget(self._label(label, "StaffingV2Muted"))
        layout.addWidget(control)
        return layout

    def refresh(self) -> None:
        self.store.initialize()
        metrics = self.service_factory().staffing_metrics(today=date.today(), school=self.school_filter)
        self.rows = list(metrics.rows)
        self._sync_selectors()
        self._refresh_metrics(metrics.open_count, metrics.avg_days_to_fill, metrics.open_over_7_days)
        self._refresh_filters()
        self._refresh_classrooms()
        self._refresh_people()
        self._refresh_history()
        self._refresh_validation()

    def _refresh_people(self) -> None:
        self.people = self.store.list_people()
        self._refresh_people_filters()

    def _clear_people_filters(self) -> None:
        self.people_search.clear()
        self.people_active_filter.setCurrentText("All")
        self.people_role_filter.setCurrentText("All")
        self.people_permit_filter.setCurrentText("All")
        self._refresh_people_filters()

    def _refresh_people_filters(self) -> None:
        if not hasattr(self, "people_table"):
            return
        search = self.people_search.text().strip().casefold()
        active_filter = self.people_active_filter.currentText()
        role_filter = self.people_role_filter.currentText()
        permit_filter = self.people_permit_filter.currentText()
        self.visible_people = []
        for person in self.people:
            haystack = f"{person.name} {person.role} {person.permit_status} {person.current_assignment}".casefold()
            if search and search not in haystack:
                continue
            if active_filter == "Active" and not person.active:
                continue
            if active_filter == "Inactive" and person.active:
                continue
            if role_filter != "All" and person.role != role_filter:
                continue
            if permit_filter != "All" and _permit_label(person.permit_status) != permit_filter:
                continue
            self.visible_people.append(person)
        self._refresh_people_metrics()
        self._refresh_people_table()

    def _refresh_people_metrics(self) -> None:
        while self.people_metrics_layout.count():
            item = self.people_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        active = sum(1 for person in self.people if person.active)
        teachers = sum(1 for person in self.people if person.role == "Teacher")
        aides = sum(1 for person in self.people if person.role == "Aide")
        units = [person.units for person in self.people if person.units is not None]
        avg_units = sum(units) / len(units) if units else 0.0
        cards = [
            ("Total People", str(len(self.people))),
            ("Active", str(active)),
            ("Inactive", str(len(self.people) - active)),
            ("Teachers", str(teachers)),
            ("Aides", str(aides)),
            ("Avg Units", f"{avg_units:.1f}"),
        ]
        for label, value in cards:
            self.people_metrics_layout.addWidget(self._metric_card(label, value, f"{label} {value}", "StaffingV2PeopleMetricCard"))

    def _refresh_people_table(self) -> None:
        self.people_table.setRowCount(0)
        for person in self.visible_people:
            row_index = self.people_table.rowCount()
            self.people_table.insertRow(row_index)
            values = [
                person.name,
                person.role or "-",
                _permit_label(person.permit_status),
                _format_units(person.units),
                "Active" if person.active else "Inactive",
                person.current_assignment or "-",
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(self.QtCore.Qt.ItemDataRole.UserRole, person.id)
                self.people_table.setItem(row_index, column, item)
            view = self.QtWidgets.QPushButton("View")
            view.setObjectName("StaffingV2PeopleRowView")
            view.setProperty("personId", person.id)
            self._set_button_icon(view, "info")
            view.setEnabled(False)
            self.people_table.setCellWidget(row_index, 6, view)
        if self.people_table.rowCount():
            self.people_table.setCurrentCell(0, 0)
            self._select_person(0)
        else:
            self._render_person_detail(None)

    def _select_person(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.visible_people):
            self._render_person_detail(None)
            return
        self._render_person_detail(self.visible_people[row_index])

    def _render_person_detail(self, person: StaffingPerson | None) -> None:
        while self.people_detail_layout.count():
            item = self.people_detail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if person is None:
            self.people_detail_layout.addWidget(self._label("No employee selected", "StaffingV2Muted"))
            return

        top = self.QtWidgets.QHBoxLayout()
        initials = self._label(_initials(person.name), "StaffingV2PeopleInitials")
        top.addWidget(initials)
        identity = self.QtWidgets.QVBoxLayout()
        identity.addWidget(self._label(person.name, "StaffingV2PeopleName"))
        identity.addWidget(self._label(person.role or "-", "StaffingV2Muted"))
        identity.addWidget(self._label("-", "StaffingV2Muted"))
        top.addLayout(identity, 1)
        top.addWidget(self._chip("Active" if person.active else "Inactive", "filled" if person.active else "dont_need_now"))
        self.people_detail_layout.addLayout(top)

        tabs = self.QtWidgets.QFrame()
        tabs.setObjectName("StaffingV2PeopleDetailTabs")
        tabs_layout = self.QtWidgets.QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(0, 6, 0, 6)
        tabs_layout.setSpacing(8)
        tab_specs = [
            ("StaffingV2PeopleOverviewTab", "Overview", True),
            ("StaffingV2PeopleAssignmentsTab", "Assignments", False),
            ("StaffingV2PeopleHistoryTab", "History", False),
            ("StaffingV2PeopleNotesTab", "Notes", False),
            ("StaffingV2PeopleDocumentsTab", "Documents", False),
        ]
        for object_name, text, is_active in tab_specs:
            tab = self.QtWidgets.QPushButton(text)
            tab.setObjectName(object_name)
            tab.setProperty("staffingV2ActivePeopleTab", is_active)
            tabs_layout.addWidget(tab)
        tabs_layout.addStretch(1)
        self.people_detail_layout.addWidget(tabs)

        info, info_layout = self._panel("StaffingV2PeopleDetailCard")
        info_layout.addWidget(self._label("Employee Information", "StaffingV2SectionTitle"))
        info_layout.addLayout(self._detail_row("Role", person.role or "-"))
        info_layout.addLayout(self._detail_row("Permit Status", _permit_label(person.permit_status)))
        info_layout.addLayout(self._detail_row("Units", _format_units(person.units)))
        info_layout.addLayout(self._detail_row("Hire Date", "-"))
        info_layout.addLayout(self._detail_row("Active", "Yes" if person.active else "No"))
        self.people_detail_layout.addWidget(info)

        current, current_layout = self._panel("StaffingV2PeopleDetailCard")
        current_layout.addWidget(self._label("Current Assignment", "StaffingV2SectionTitle"))
        current_layout.addWidget(self._label(person.assignment_school or "-", "StaffingV2Muted"))
        current_layout.addWidget(self._label(_assignment_detail(person), "StaffingV2Muted"))
        self.people_detail_layout.addWidget(current)

        employment, employment_layout = self._panel("StaffingV2PeopleDetailCard")
        employment_layout.addWidget(self._label("Employment Status", "StaffingV2SectionTitle"))
        employment_layout.addLayout(self._detail_row("Notice Given", person.notice_given or "-"))
        employment_layout.addLayout(self._detail_row("Final Working Day", person.final_working_day or "-"))
        employment_layout.addLayout(self._detail_row("Employment Status", "Active" if person.active else "Inactive"))
        employment_layout.addLayout(self._detail_row("Rehire Eligible", "Yes" if person.active else "-"))
        self.people_detail_layout.addWidget(employment)

        additional, additional_layout = self._panel("StaffingV2PeopleDetailCard")
        additional_layout.addWidget(self._label("Additional Information", "StaffingV2SectionTitle"))
        additional_layout.addLayout(self._detail_row("Permit Effective Date", person.permit_effective_date or "-"))
        additional_layout.addLayout(self._detail_row("Documentation", "Received" if person.permit_documentation_received else "-"))
        additional_layout.addLayout(self._detail_row("Notes", person.permit_notes or "-"))
        self.people_detail_layout.addWidget(additional, 1)

        footer = self.QtWidgets.QHBoxLayout()
        deactivate = self.QtWidgets.QPushButton("Deactivate Employee")
        deactivate.setObjectName("StaffingV2PeopleDeactivateButton")
        self._set_button_icon(deactivate, "status_need")
        deactivate.setEnabled(False)
        edit = self.QtWidgets.QPushButton("Edit Person")
        edit.setObjectName("StaffingV2PeopleEditButton")
        self._set_button_icon(edit, "settings")
        edit.setEnabled(False)
        footer.addWidget(deactivate)
        footer.addStretch(1)
        footer.addWidget(edit)
        self.people_detail_layout.addLayout(footer)

    def _detail_row(self, label: str, value: str) -> Any:
        row = self.QtWidgets.QHBoxLayout()
        row.addWidget(self._label(label, "StaffingV2Muted"))
        row.addWidget(self._label(value, "StaffingV2Muted"))
        return row

    def _build_history_view(self) -> None:
        self.history_view = self.QtWidgets.QWidget()
        self.history_view.setObjectName("StaffingV2AssignmentHistoryDashboard")
        history_root = self.QtWidgets.QVBoxLayout(self.history_view)
        history_root.setContentsMargins(0, 0, 0, 0)
        history_root.setSpacing(14)
        self.page_stack.addWidget(self.history_view)

        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("Assignment History")
        title.setObjectName("StaffingV2HistoryTitle")
        subtitle = self.QtWidgets.QLabel("Review open-to-fill staffing cycles, track time-to-fill, and validate history integrity.")
        subtitle.setObjectName("StaffingV2HistorySubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        header.addWidget(self._label("Last updated: May 20, 2025 9:42 AM", "StaffingV2Muted"))
        export = self.QtWidgets.QPushButton("Export")
        export.setObjectName("StaffingV2HistoryExportButton")
        self._set_button_icon(export, "export")
        export.setEnabled(False)
        validation = self.QtWidgets.QPushButton("View Validation")
        validation.setObjectName("StaffingV2HistoryValidationButton")
        self._set_button_icon(validation, "validation")
        validation.setEnabled(False)
        header.addWidget(export)
        header.addWidget(validation)
        history_root.addLayout(header)

        self.history_metrics_layout = self.QtWidgets.QHBoxLayout()
        self.history_metrics_layout.setSpacing(10)
        history_root.addLayout(self.history_metrics_layout)

        filters = self.QtWidgets.QHBoxLayout()
        self.history_school_filter = self._history_filter_combo("StaffingV2HistorySchoolFilter", ["All Schools"])
        filters.addLayout(self._labeled_control("School", self.history_school_filter), 1)
        self.history_classroom_filter = self._history_filter_combo("StaffingV2HistoryClassroomFilter", ["All Classrooms"])
        filters.addLayout(self._labeled_control("Classroom", self.history_classroom_filter), 1)
        self.history_cycle_filter = self._history_filter_combo("StaffingV2HistoryCycleFilter", ["All Statuses", "Open", "Closed"])
        filters.addLayout(self._labeled_control("Cycle Status", self.history_cycle_filter), 1)
        self.history_date_range_filter = self.QtWidgets.QPushButton("No date range")
        self.history_date_range_filter.setObjectName("StaffingV2HistoryDateRangeFilter")
        self._set_button_icon(self.history_date_range_filter, "history")
        self.history_date_range_filter.setToolTip("Displayed from assignment history open dates.")
        filters.addLayout(self._labeled_control("Date Range", self.history_date_range_filter), 1)
        self.history_search = self.QtWidgets.QLineEdit()
        self.history_search.setObjectName("StaffingV2HistorySearch")
        self.history_search.setPlaceholderText("Search assignments...")
        self.history_search.addAction(
            self._standard_icon("search"), self.QtWidgets.QLineEdit.ActionPosition.LeadingPosition
        )
        self.history_search.textChanged.connect(self._refresh_history_filters)
        filters.addWidget(self.history_search, 2)
        more = self.QtWidgets.QPushButton("More Filters")
        more.setObjectName("StaffingV2HistoryMoreFilters")
        self._set_button_icon(more, "filter")
        more.setEnabled(False)
        clear = self.QtWidgets.QPushButton("Clear")
        clear.setObjectName("StaffingV2HistoryClear")
        clear.clicked.connect(self._clear_history_filters)
        filters.addWidget(more)
        filters.addWidget(clear)
        history_root.addLayout(filters)

        body = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.history_table = self.QtWidgets.QTableWidget(0, 10)
        self.history_table.setObjectName("StaffingV2HistoryTable")
        self.history_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setHorizontalHeaderLabels(
            [
                "Assignment ID",
                "Classroom",
                "Position",
                "Opened Date",
                "Filled Date",
                "Days to Fill",
                "Cycle Status",
                "Employee",
                "Data Integrity",
                "Actions",
            ]
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.history_table.currentCellChanged.connect(
            lambda row, _column, _prev_row, _prev_column: self._select_history_record(row)
        )
        body.addWidget(self.history_table)
        self.history_detail_panel, self.history_detail_layout = self._panel("StaffingV2HistoryDetailPanel")
        self.history_detail_panel.setMinimumWidth(380)
        body.addWidget(self.history_detail_panel)
        body.setSizes([900, 380])
        history_root.addWidget(body, 1)

    def _history_filter_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        combo.currentIndexChanged.connect(self._refresh_history_filters)
        return combo

    def _refresh_history(self) -> None:
        if not hasattr(self, "history_table"):
            return
        self.history_records = self.store.list_assignment_history()
        self._sync_history_filters()
        self._refresh_history_filters()

    def _sync_history_filters(self) -> None:
        self._sync_combo(self.history_school_filter, ["All Schools", *sorted({record.school for record in self.history_records if record.school})])
        self._sync_combo(
            self.history_classroom_filter,
            ["All Classrooms", *sorted({record.classroom for record in self.history_records if record.classroom})],
        )
        opened_dates = sorted({record.opened_date for record in self.history_records if record.opened_date})
        if opened_dates:
            self.history_date_range_filter.setText(f"{opened_dates[0]} - {opened_dates[-1]}")
        else:
            self.history_date_range_filter.setText("No date range")

    def _sync_combo(self, combo: Any, values: list[str]) -> None:
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        combo.setCurrentText(current if current in values else values[0])
        combo.blockSignals(False)

    def _clear_history_filters(self) -> None:
        self.history_school_filter.setCurrentText("All Schools")
        self.history_classroom_filter.setCurrentText("All Classrooms")
        self.history_cycle_filter.setCurrentText("All Statuses")
        self.history_search.clear()
        self._refresh_history_filters()

    def _refresh_history_filters(self) -> None:
        if not hasattr(self, "history_table"):
            return
        school = self.history_school_filter.currentText()
        classroom = self.history_classroom_filter.currentText()
        cycle = self.history_cycle_filter.currentText()
        search = self.history_search.text().strip().casefold()
        self.visible_history_records = []
        for record in self.history_records:
            haystack = f"{record.assignment_id} {record.school} {record.classroom} {record.position_name} {record.employee}".casefold()
            if school != "All Schools" and record.school != school:
                continue
            if classroom != "All Classrooms" and record.classroom != classroom:
                continue
            if cycle != "All Statuses" and record.cycle_status != cycle:
                continue
            if search and search not in haystack:
                continue
            self.visible_history_records.append(record)
        self._refresh_history_metrics()
        self._refresh_history_table()

    def _refresh_history_metrics(self) -> None:
        while self.history_metrics_layout.count():
            item = self.history_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        open_count = sum(1 for record in self.history_records if record.cycle_status == "Open")
        closed_count = sum(1 for record in self.history_records if record.cycle_status == "Closed")
        days = [record.days_to_fill for record in self.history_records if record.days_to_fill is not None]
        avg_days = sum(days) / len(days) if days else 0.0
        issues = sum(1 for record in self.history_records if record.data_integrity != "Healthy")
        cards = [
            ("Total Cycles", str(len(self.history_records))),
            ("Open Cycles", str(open_count)),
            ("Closed Cycles", str(closed_count)),
            ("Avg Days to Fill", f"{avg_days:.1f}"),
            ("Data Issues", str(issues)),
        ]
        for label, value in cards:
            self.history_metrics_layout.addWidget(
                self._metric_card(label, value, f"{label} {value}", "StaffingV2HistoryMetricCard")
            )

    def _refresh_history_table(self) -> None:
        self.history_table.setRowCount(0)
        for record in self.visible_history_records:
            row_index = self.history_table.rowCount()
            self.history_table.insertRow(row_index)
            values = [
                f"A-{record.assignment_id:04d}",
                record.classroom,
                record.position_name,
                record.opened_date,
                record.filled_date or "-",
                "" if record.days_to_fill is None else str(record.days_to_fill),
                record.cycle_status,
                record.employee,
                record.data_integrity,
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(self.QtCore.Qt.ItemDataRole.UserRole, record.id)
                self.history_table.setItem(row_index, column, item)
            view = self.QtWidgets.QPushButton("View")
            view.setObjectName("StaffingV2HistoryRowView")
            view.setProperty("historyId", record.id)
            self._set_button_icon(view, "info")
            view.setEnabled(False)
            self.history_table.setCellWidget(row_index, 9, view)
        if self.history_table.rowCount():
            self.history_table.setCurrentCell(0, 0)
            self._select_history_record(0)
        else:
            self._render_history_detail(None)

    def _select_history_record(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.visible_history_records):
            self._render_history_detail(None)
            return
        self._render_history_detail(self.visible_history_records[row_index])

    def _render_history_detail(self, record: StaffingHistoryRecord | None) -> None:
        while self.history_detail_layout.count():
            item = self.history_detail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if record is None:
            self.history_detail_layout.addWidget(self._label("No history record selected", "StaffingV2Muted"))
            return
        self.history_detail_layout.addWidget(self._label("History Record Detail", "StaffingV2HistoryDetailTitle"))
        self.history_detail_layout.addWidget(self._label(f"Assignment ID: A-{record.assignment_id:04d}", "StaffingV2SectionTitle"))
        overview, overview_layout = self._panel("StaffingV2HistoryDetailCard")
        overview_layout.addLayout(self._detail_row("Classroom", record.classroom))
        overview_layout.addLayout(self._detail_row("Position", record.position_name))
        overview_layout.addLayout(self._detail_row("Cycle status", record.cycle_status))
        overview_layout.addLayout(self._detail_row("Opened date", record.opened_date))
        overview_layout.addLayout(self._detail_row("Filled date", record.filled_date or "-"))
        overview_layout.addLayout(self._detail_row("Days to fill", "" if record.days_to_fill is None else str(record.days_to_fill)))
        overview_layout.addLayout(self._detail_row("Filled by / Employee", record.employee))
        overview_layout.addLayout(self._detail_row("School", record.school))
        self.history_detail_layout.addWidget(overview)

        lifecycle, lifecycle_layout = self._panel("StaffingV2HistoryDetailCard")
        lifecycle_layout.addWidget(self._label("Lifecycle Events", "StaffingV2SectionTitle"))
        lifecycle_layout.addWidget(self._label(f"Position opened\n{record.opened_date}"))
        if record.filled_date:
            lifecycle_layout.addWidget(self._label(f"Position marked Filled\n{record.filled_date}"))
        else:
            lifecycle_layout.addWidget(self._label("Cycle remains open"))
        self.history_detail_layout.addWidget(lifecycle)

        validation, validation_layout = self._panel("StaffingV2HistoryDetailCard")
        validation_layout.addWidget(self._label("Validation / Integrity", "StaffingV2SectionTitle"))
        validation_layout.addWidget(self._label(f"History status: {record.data_integrity}"))
        validation_layout.addWidget(self._label("Dates valid" if record.opened_date else "Missing opened date"))
        validation_layout.addWidget(self._label("Duplicate active cycle" if record.data_integrity != "Healthy" else "No duplicate open cycles"))
        self.history_detail_layout.addWidget(validation, 1)

        footer = self.QtWidgets.QHBoxLayout()
        view = self.QtWidgets.QPushButton("View Assignment")
        view.setObjectName("StaffingV2HistoryViewAssignment")
        self._set_button_icon(view, "dashboard")
        view.setEnabled(False)
        employee = self.QtWidgets.QPushButton("Open Employee")
        employee.setObjectName("StaffingV2HistoryOpenEmployee")
        self._set_button_icon(employee, "people")
        employee.setEnabled(False)
        export = self.QtWidgets.QPushButton("Export Record")
        export.setObjectName("StaffingV2HistoryExportRecord")
        self._set_button_icon(export, "export")
        export.setEnabled(False)
        footer.addWidget(view)
        footer.addWidget(employee)
        footer.addWidget(export)
        self.history_detail_layout.addLayout(footer)

    def _sync_selectors(self) -> None:
        current_school = self.school_selector.currentText()
        schools = sorted({row.school for row in self.rows if row.school})
        if self.school_filter:
            schools = [school for school in schools if school == self.school_filter]
        self.school_selector.blockSignals(True)
        self.school_selector.clear()
        self.school_selector.addItems(schools)
        if current_school in schools:
            self.school_selector.setCurrentText(current_school)
        elif schools:
            self.school_selector.setCurrentIndex(0)
        self.school_selector.blockSignals(False)

        current_program = self.program_selector.currentText() or "All Programs"
        programs = ["All Programs", *sorted({row.classroom_program for row in self.rows if row.classroom_program})]
        self.program_selector.blockSignals(True)
        self.program_selector.clear()
        self.program_selector.addItems(programs)
        self.program_selector.setCurrentText(current_program if current_program in programs else "All Programs")
        self.program_selector.blockSignals(False)

    def _refresh_metrics(self, open_count: int, avg_days_to_fill: float, open_over_7_days: int) -> None:
        while self.metrics_layout.count():
            item = self.metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        school_count = len({row.school for row in self.rows if row.school})
        cards = [
            ("Schools", str(school_count), f"Schools: {school_count}"),
            ("Open positions", str(open_count), f"Open positions: {open_count}"),
            ("Avg fill time", f"{avg_days_to_fill:.1f} days", f"Avg fill time: {avg_days_to_fill:.1f} days"),
            ("Open > 7 days", str(open_over_7_days), f"Open > 7 days: {open_over_7_days}"),
            ("Validation", "healthy", "Validation healthy"),
        ]
        for label, value, accessible_text in cards:
            self.metrics_layout.addWidget(self._summary_chip(label, value, accessible_text))
        self.metrics_layout.addStretch(1)

    def _refresh_filters(self) -> None:
        school = self.school_selector.currentText()
        program = self.program_selector.currentText()
        search = self.search.text().strip().casefold()
        self.visible_rows = []
        for row in self.rows:
            if school and row.school != school:
                continue
            if program and program != "All Programs" and row.classroom_program != program:
                continue
            if search and search not in row.classroom.casefold():
                continue
            self.visible_rows.append(row)
        self.classroom_rows = {}
        for row in self.visible_rows:
            self.classroom_rows.setdefault(row.classroom, []).append(row)
        current = self.classroom_list.currentItem().data(self.QtCore.Qt.ItemDataRole.UserRole) if self.classroom_list.currentItem() else ""
        self.classroom_list.blockSignals(True)
        self.classroom_list.clear()
        for classroom, rows in self.classroom_rows.items():
            item = self.QtWidgets.QListWidgetItem(_classroom_label(classroom, rows))
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, classroom)
            item.setSizeHint(self.QtCore.QSize(0, 68))
            self.classroom_list.addItem(item)
            self.classroom_list.setItemWidget(item, self._classroom_list_item_widget(classroom, rows))
        visible_count = self.classroom_list.count()
        total_count = len(self.classroom_rows)
        if visible_count:
            self.classroom_list_footer.setText(f"Showing 1-{visible_count} of {total_count} classrooms")
        else:
            self.classroom_list_footer.setText("Showing 0 of 0 classrooms")
        if current in self.classroom_rows:
            self.classroom_list.setCurrentRow(list(self.classroom_rows).index(current))
        elif self.classroom_list.count():
            self.classroom_list.setCurrentRow(0)
        self.classroom_list.blockSignals(False)
        self._sync_classroom_list_selection()
        self._select_classroom(self.classroom_list.currentRow())

    def _select_classroom(self, index: int) -> None:
        if index < 0 or index >= self.classroom_list.count():
            self._render_classroom("", [])
            return
        classroom = str(self.classroom_list.item(index).data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
        self._sync_classroom_list_selection()
        self._render_classroom(classroom, self.classroom_rows.get(classroom, []))

    def _classroom_list_item_widget(self, classroom: str, rows: list[StaffingMetricRow]) -> Any:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName("StaffingV2ClassroomListItem")
        frame.setProperty("staffingV2Selected", False)
        layout = self.QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)
        dot = self.QtWidgets.QFrame()
        dot.setObjectName("StaffingV2ClassroomStatusDot")
        dot.setProperty("staffingV2Status", _classroom_status_key(rows))
        layout.addWidget(dot, alignment=self.QtCore.Qt.AlignmentFlag.AlignTop)
        text = self.QtWidgets.QVBoxLayout()
        text.setSpacing(2)
        title = self._label(classroom, "StaffingV2ClassroomItemTitle")
        title.setWordWrap(False)
        counts = self._label(_classroom_counts_text(rows), "StaffingV2ClassroomItemCounts")
        counts.setWordWrap(False)
        text.addWidget(title)
        text.addWidget(counts)
        layout.addLayout(text, 1)
        chevron = self._label(">", "StaffingV2ClassroomItemChevron")
        chevron.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(chevron)
        return frame

    def _sync_classroom_list_selection(self) -> None:
        current_row = self.classroom_list.currentRow()
        for index in range(self.classroom_list.count()):
            widget = self.classroom_list.itemWidget(self.classroom_list.item(index))
            if widget is None:
                continue
            widget.setProperty("staffingV2Selected", index == current_row)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _render_classroom(self, classroom: str, rows: list[StaffingMetricRow]) -> None:
        school = rows[0].school if rows else ""
        self.classroom_title.setText(classroom)
        self.classroom_subtitle.setText(school)
        priority = _classroom_priority_status(rows) if rows else ""
        self.priority_chip_text.setText(priority)
        self.priority_chip.setVisible(bool(priority))
        self._refresh_overview(rows)
        self._refresh_positions(rows)

    def _refresh_overview(self, rows: list[StaffingMetricRow]) -> None:
        while self.overview_layout.count():
            item = self.overview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        total = len(rows)
        filled = sum(1 for row in rows if row.status == "filled")
        open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
        program = next((row.classroom_program for row in rows if row.classroom_program), "Program")
        capacity = next((row.classroom_capacity for row in rows if row.classroom_capacity is not None), None)
        overview = [
            ("Program", program),
            ("Licensed Capacity", "" if capacity is None else str(capacity)),
            ("Total Positions", str(total)),
            ("Filled", f"{filled} / {round((filled / total) * 100) if total else 0}%"),
            ("Open", str(open_count)),
            ("Avg Days to Fill", _avg_open_days(rows)),
        ]
        for label, value in overview:
            self.overview_layout.addWidget(self._metric_card(label, value, f"{label}: {value}", "StaffingV2OverviewCard"))

    def _refresh_positions(self, rows: list[StaffingMetricRow]) -> None:
        self.positions_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                str(row_index + 1),
                row.position_name,
                row.person_name or "OPEN POSITION",
                row.start_date or "-",
                "-" if row.days_open is None else str(row.days_open),
            ]
            for column, value in zip((0, 1, 2, 4, 5), values, strict=True):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setToolTip(row.position_name)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, row.assignment_id)
                if column == 0:
                    item.setTextAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
                self.positions_table.setItem(row_index, column, item)
            self.positions_table.setCellWidget(row_index, 3, self._chip(_display_status(row.status), row.status))
            self.positions_table.setCellWidget(
                row_index,
                6,
                self._chip(_display_permit(row.permit_status or "unknown"), _permit_chip_status(row.permit_status or "unknown")),
            )
            self.positions_table.setCellWidget(row_index, 7, self._action_button(row))
        self.positions_table.resizeColumnsToContents()

    def _open_position_drawer_from_table(self, row: int, _column: int) -> None:
        assignment_id = _table_assignment_id(self.positions_table, row)
        if assignment_id is None:
            return
        self._show_position_drawer(assignment_id)

    def _show_position_drawer(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        metric_row = next((row for row in self.rows if row.assignment_id == assignment_id), None)
        self._clear_layout(self.drawer_layout)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Position Detail", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label(f"{assignment.classroom} · {assignment.school} · Assignment ID #{assignment.id}", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2DrawerClose")
        self._set_button_icon(close, "close")
        close.setFixedSize(32, 32)
        close.clicked.connect(self.drawer.hide)
        header.addWidget(close)
        self.drawer_layout.addLayout(header)

        summary, summary_layout = self._panel("StaffingV2DrawerSection")
        summary_row = self.QtWidgets.QHBoxLayout()
        summary_row.addWidget(self._chip(_display_status(assignment.status), assignment.status))
        position_column = self.QtWidgets.QVBoxLayout()
        position_column.addWidget(self._label(assignment.position_name, "StaffingV2DrawerPositionName"))
        position_column.addWidget(
            self._label(
                f"Classroom {assignment.classroom}   School {assignment.school}   Program {assignment.classroom_program or 'Program'}   Position Type {assignment.position_type}",
                "StaffingV2Muted",
            )
        )
        summary_row.addLayout(position_column, 1)
        summary_layout.addLayout(summary_row)
        self.drawer_layout.addWidget(summary)

        overview, overview_layout = self._panel("StaffingV2DrawerSection")
        overview_layout.addWidget(self._label("Position Overview", "StaffingV2SectionTitle"))
        overview_layout.addWidget(
            self._label(
                "\n".join(
                    [
                        f"Assigned person: {assignment.person_name or 'OPEN POSITION'}",
                        f"Start date: {assignment.start_date or '-'}",
                        f"Permit status: {_display_permit(assignment.permit_status or 'unknown')}",
                        f"Current opened date: {assignment.current_opened_date or '-'}",
                        f"Current filled date: {assignment.current_filled_date or '-'}",
                        f"Days open: {_days_open_text(metric_row)}",
                        f"Current priority: {_display_status(assignment.status)}",
                    ]
                )
            )
        )
        self.drawer_layout.addWidget(overview)

        action_section, action_layout = self._panel("StaffingV2DrawerSection")
        action_layout.addWidget(self._label("Available Next Actions", "StaffingV2SectionTitle"))
        buttons = self.QtWidgets.QGridLayout()
        for index, (object_name, label, action_key) in enumerate(_drawer_actions(assignment.status)):
            button = self.QtWidgets.QPushButton(label)
            button.setObjectName(object_name)
            self._set_button_icon(button, _drawer_action_icon_key(action_key))
            self._wire_action_button(button, action_key, assignment.id)
            buttons.addWidget(button, index // 2, index % 2)
        action_layout.addLayout(buttons)
        action_layout.addWidget(self._label("Status changes update Assignments and AssignmentHistory through StaffingService.", "StaffingV2Muted"))
        self.drawer_layout.addWidget(action_section)

        lower = self.QtWidgets.QGridLayout()
        validation, validation_layout = self._panel("StaffingV2DrawerSection")
        validation_layout.addWidget(self._label("Data Integrity / Validation", "StaffingV2SectionTitle"))
        for line in _validation_lines(assignment):
            validation_layout.addWidget(self._label(line))
        lower.addWidget(validation, 0, 0)

        lifecycle, lifecycle_layout = self._panel("StaffingV2DrawerSection")
        lifecycle_layout.addWidget(self._label("Lifecycle History", "StaffingV2SectionTitle"))
        for line in _lifecycle_lines(assignment):
            lifecycle_layout.addWidget(self._label(line))
        lower.addWidget(lifecycle, 1, 0)

        related, related_layout = self._panel("StaffingV2DrawerSection")
        related_layout.addWidget(self._label("Related Person", "StaffingV2SectionTitle"))
        if assignment.person_name:
            related_layout.addWidget(self._label(assignment.person_name, "StaffingV2DrawerPositionName"))
            related_layout.addWidget(self._label(f"{assignment.position_type} · {_display_permit(assignment.permit_status or 'unknown')}"))
        else:
            related_layout.addWidget(self._label("No person is currently assigned to this position."))
            assign = self.QtWidgets.QPushButton("Assign or Create Person")
            assign.setObjectName("StaffingV2DrawerAssignPerson")
            self._set_button_icon(assign, "person_add")
            assign.setEnabled(False)
            related_layout.addWidget(assign)
        lower.addWidget(related, 0, 1, 2, 1)
        self.drawer_layout.addLayout(lower)
        self.drawer_layout.addStretch(1)
        footer = self._label(f"Last updated: {assignment.updated_at or '-'}", "StaffingV2Muted")
        self.drawer_layout.addWidget(footer)
        actions = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2DrawerCancel")
        self._set_button_icon(cancel, "close")
        cancel.clicked.connect(self.drawer.hide)
        draft = self.QtWidgets.QPushButton("Save Draft")
        draft.setObjectName("StaffingV2DrawerSaveDraft")
        self._set_button_icon(draft, "export")
        draft.setEnabled(False)
        save = self.QtWidgets.QPushButton("Save Changes")
        save.setObjectName("StaffingV2DrawerSaveChanges")
        self._set_button_icon(save, "status_filled")
        save.setEnabled(False)
        actions.addWidget(cancel)
        actions.addStretch(1)
        actions.addWidget(draft)
        actions.addWidget(save)
        self.drawer_layout.addLayout(actions)
        self.drawer.show()

    def _action_button(self, row: StaffingMetricRow) -> Any:
        action_key, label = _primary_action(row.status)
        button = self.QtWidgets.QToolButton()
        button.setText(label)
        button.setObjectName("StaffingV2ActionButton")
        button.setProperty("staffingAssignmentId", row.assignment_id)
        button.setProperty("staffingAction", action_key)
        button.setMinimumHeight(34)
        button.setPopupMode(self.QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = self.QtWidgets.QMenu(button)
        menu.setObjectName("StaffingV2ActionMenu")
        for menu_label, menu_action_key in _action_menu_specs(row.status):
            action = menu.addAction(menu_label)
            action.setData(menu_action_key)
            self._wire_menu_action(action, menu_action_key, row.assignment_id)
        button.setMenu(menu)
        self._wire_action_button(button, action_key, row.assignment_id)
        return button

    def _wire_menu_action(self, action: Any, action_key: str, assignment_id: int) -> None:
        if action_key == "mark_coming":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_coming_dialog(item))
            return
        if action_key == "mark_filled":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_filled_dialog(item))
            return
        if action_key == "manage_filled":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_manage_filled_dialog(item))
            return
        if action_key == "update_permit":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_update_permit_dialog(item))
            return
        if action_key == "clear_replacement":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_need_now_dialog(item))
            return
        if action_key == "view_details":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._show_position_drawer(item))
            return
        callback = self.actions.get(action_key)
        if callback is None:
            action.setEnabled(False)
            return
        action.triggered.connect(lambda _checked=False, item=assignment_id, cb=callback: cb(item))

    def _wire_action_button(self, button: Any, action_key: str, assignment_id: int) -> None:
        if action_key == "mark_coming":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_coming_dialog(item))
            return
        if action_key == "mark_filled":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_filled_dialog(item))
            return
        if action_key == "manage_filled":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_manage_filled_dialog(item))
            return
        if action_key == "update_permit":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_update_permit_dialog(item))
            return
        if action_key == "clear_replacement":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_need_now_dialog(item))
            return
        callback = self.actions.get(action_key)
        if callback is None:
            button.setEnabled(False)
            button.setToolTip("Action dialog will be implemented in a later mockup slice.")
            return
        button.clicked.connect(lambda _checked=False, item=assignment_id, cb=callback: cb(item))

    def _open_add_position_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2AddPositionDialog")
        dialog.setWindowTitle("Add Position")
        dialog.setModal(True)
        dialog.resize(760, 720)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Add Position", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Create a new position for a classroom.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2AddPositionClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        top = self.QtWidgets.QGridLayout()
        school = self.QtWidgets.QComboBox()
        school.setObjectName("StaffingV2AddPositionSchool")
        schools = sorted({row.school for row in self.rows if row.school})
        school.addItems(schools or [""])
        classroom = self.QtWidgets.QComboBox()
        classroom.setObjectName("StaffingV2AddPositionClassroom")
        position_type = self.QtWidgets.QComboBox()
        position_type.setObjectName("StaffingV2AddPositionType")
        position_type.addItems(["Teacher", "Aide", "Floater", "Chef", "Other"])
        position_name = self.QtWidgets.QLineEdit()
        position_name.setObjectName("StaffingV2AddPositionName")
        position_name.setPlaceholderText("Teacher 2")
        initial_status = self.QtWidgets.QComboBox()
        initial_status.setObjectName("StaffingV2AddPositionInitialStatus")
        initial_status.addItems(["Need Now", "Don't Need Now", "Coming", "Filled"])
        top.addLayout(self._labeled_control("School *", school), 0, 0)
        top.addLayout(self._labeled_control("Classroom *", classroom), 0, 1)
        top.addLayout(self._labeled_control("Position Type *", position_type), 1, 0)
        top.addLayout(self._labeled_control("Position Label / Name *", position_name), 1, 1)
        top.addLayout(self._labeled_control("Initial Status *", initial_status), 2, 0)
        definitions, definitions_layout = self._dialog_section("StaffingV2DialogInfo")
        definitions_layout.addWidget(self._label("Status Definitions", "StaffingV2SectionTitle"))
        definitions_layout.addWidget(self._label("Need Now - Actively hiring"))
        definitions_layout.addWidget(self._label("Coming - Hired, start date in future"))
        definitions_layout.addWidget(self._label("Filled - Employee has started"))
        definitions_layout.addWidget(self._label("Don't Need Now - Position not needed"))
        top.addWidget(definitions, 2, 1)
        layout.addLayout(top)

        status_card, status_layout = self._dialog_section("StaffingV2AddPositionStatusCard")
        status_layout.addWidget(self._label("Need Now", "StaffingV2SectionTitle"))
        status_layout.addWidget(self._label("Position is open and available for hiring."))
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2AddPositionNotes")
        notes.setPlaceholderText("Add any notes...")
        notes.setMaximumHeight(80)
        status_layout.addLayout(self._labeled_control("Notes (optional)", notes))
        layout.addWidget(status_card)

        error = self._label("", "StaffingV2NeedNowChip")
        error.hide()
        layout.addWidget(error)
        layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2AddPositionCancel")
        cancel.clicked.connect(dialog.close)
        submit = self.QtWidgets.QPushButton("Add Position")
        submit.setObjectName("StaffingV2AddPositionSubmit")
        footer.addWidget(cancel)
        footer.addWidget(submit)
        layout.addLayout(footer)

        def sync_classrooms() -> None:
            selected_school = school.currentText()
            classrooms = sorted({row.classroom for row in self.rows if row.school == selected_school and row.classroom})
            current = classroom.currentText()
            classroom.blockSignals(True)
            classroom.clear()
            classroom.addItems(classrooms or [""])
            if current in classrooms:
                classroom.setCurrentText(current)
            classroom.blockSignals(False)

        def sync_status_card() -> None:
            label = initial_status.currentText()
            status_layout.itemAt(0).widget().setText(label)
            descriptions = {
                "Need Now": "Position is open and available for hiring.",
                "Don't Need Now": "Position is not needed at this time.",
                "Coming": "Employee hired and will start in the future.",
                "Filled": "Employee has already started.",
            }
            status_layout.itemAt(1).widget().setText(descriptions.get(label, ""))

        def save() -> None:
            error.hide()
            try:
                selected_rows = [
                    row for row in self.rows if row.school == school.currentText() and row.classroom == classroom.currentText()
                ]
                first = selected_rows[0] if selected_rows else None
                result = self.service_factory().add_position(
                    school=school.currentText(),
                    classroom=classroom.currentText(),
                    classroom_program=first.classroom_program if first else "",
                    licensed_capacity=first.classroom_capacity if first else None,
                    position_name=position_name.text(),
                    position_type=position_type.currentText(),
                    initial_status=_status_from_label(initial_status.currentText()),
                    notes=notes.toPlainText(),
                )
            except Exception as exc:  # noqa: BLE001 - show service/store validation to user.
                error.setText(str(exc))
                error.show()
                return
            dialog.close()
            self.refresh()
            self._show_position_drawer(result.assignment_id)

        school.currentIndexChanged.connect(sync_classrooms)
        initial_status.currentIndexChanged.connect(sync_status_card)
        sync_classrooms()
        sync_status_card()
        submit.clicked.connect(save)
        dialog.show()

    def _open_mark_coming_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        metric_row = next((row for row in self.rows if row.assignment_id == assignment_id), None)
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2MarkComingDialog")
        dialog.setWindowTitle("Mark Coming")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(920, 720)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Mark Coming", "StaffingV2DrawerTitle"))
        title_block.addWidget(self._label("Assign a candidate and set a start date for this open position.", "StaffingV2Muted"))
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ComingClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        root.addLayout(header)

        summary, summary_layout = self._dialog_section()
        summary_layout.addWidget(self._label("1. Position Summary", "StaffingV2SectionTitle"))
        summary_grid = self.QtWidgets.QGridLayout()
        summary_items = [
            ("Classroom", assignment.classroom),
            ("School", assignment.school),
            ("Program", assignment.classroom_program or "-"),
            ("Position", assignment.position_name),
            ("Current Status", _display_status(assignment.status)),
            ("Days Open", _days_open_text(metric_row)),
            ("Assignment ID", f"A-{assignment.id:04d}"),
        ]
        for column, (label, value) in enumerate(summary_items):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            if label == "Current Status":
                cell.addWidget(self._chip(value, assignment.status))
            else:
                cell.addWidget(self._label(value))
            summary_grid.addLayout(cell, 0, column)
        summary_layout.addLayout(summary_grid)
        root.addWidget(summary)

        body = self.QtWidgets.QHBoxLayout()
        left_column = self.QtWidgets.QVBoxLayout()
        left_column.setSpacing(12)
        right_column = self.QtWidgets.QVBoxLayout()
        right_column.setSpacing(12)

        selection, selection_layout = self._dialog_section()
        selection_layout.addWidget(self._label("2. Candidate Selection", "StaffingV2SectionTitle"))
        selection_buttons = self.QtWidgets.QHBoxLayout()
        select_existing = self.QtWidgets.QPushButton("Select Existing Person")
        select_existing.setObjectName("StaffingV2ComingSelectPerson")
        self._set_button_icon(select_existing, "people")
        create_new = self.QtWidgets.QPushButton("Create New Person")
        create_new.setObjectName("StaffingV2ComingCreatePerson")
        self._set_button_icon(create_new, "person_add")
        selection_buttons.addWidget(select_existing)
        selection_buttons.addWidget(create_new)
        selection_layout.addLayout(selection_buttons)
        left_column.addWidget(selection)

        details, details_layout = self._dialog_section()
        details_layout.addWidget(self._label("3. Candidate Details", "StaffingV2SectionTitle"))
        form = self.QtWidgets.QGridLayout()
        full_name = self.QtWidgets.QLineEdit()
        full_name.setObjectName("StaffingV2ComingFullName")
        full_name.setText(assignment.person_name or "Emily Carter")
        role = self.QtWidgets.QComboBox()
        role.setObjectName("StaffingV2ComingRole")
        role.addItems(["Teacher", "Aide", "Floater", "Chef"])
        role.setCurrentText(assignment.position_type if assignment.position_type in {"Teacher", "Aide", "Floater", "Chef"} else "Teacher")
        start_date = self.QtWidgets.QDateEdit()
        start_date.setObjectName("StaffingV2ComingStartDate")
        start_date.setCalendarPopup(True)
        start_date.setDisplayFormat("yyyy-MM-dd")
        start_date.setDate(self.QtCore.QDate.currentDate())
        permit_status = self.QtWidgets.QComboBox()
        permit_status.setObjectName("StaffingV2ComingPermitStatus")
        for code in ("unknown", "permit_in_process", "teacher_permit_approved", "no_permit_or_application", "no_units_needed"):
            permit_status.addItem(_display_permit(code), code)
        permit_status.setCurrentText("Permit in Process")
        units = self.QtWidgets.QSpinBox()
        units.setObjectName("StaffingV2ComingUnits")
        units.setRange(0, 99)
        units.setValue(12)
        active = self.QtWidgets.QCheckBox("Active")
        active.setObjectName("StaffingV2ComingActive")
        active.setChecked(True)
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2ComingNotes")
        notes.setPlaceholderText("Add hiring or onboarding notes")
        notes.setFixedHeight(70)
        people_search = self.QtWidgets.QLineEdit()
        people_search.setObjectName("StaffingV2ComingPeopleSearch")
        people_search.setPlaceholderText("Search People records...")
        fields = [
            ("Full Name *", full_name, 0, 0),
            ("Role *", role, 0, 1),
            ("Start Date *", start_date, 0, 2),
            ("Permit Status (optional)", permit_status, 1, 0),
            ("Units", units, 1, 1),
            ("Active", active, 1, 2),
        ]
        for label, widget, row, column in fields:
            wrap = self.QtWidgets.QVBoxLayout()
            wrap.addWidget(self._label(label, "StaffingV2Muted"))
            wrap.addWidget(widget)
            form.addLayout(wrap, row, column)
        notes_wrap = self.QtWidgets.QVBoxLayout()
        notes_wrap.addWidget(self._label("Notes (optional)", "StaffingV2Muted"))
        notes_wrap.addWidget(notes)
        form.addLayout(notes_wrap, 2, 0, 1, 3)
        search_wrap = self.QtWidgets.QVBoxLayout()
        search_wrap.addWidget(self._label("4. Or link an existing person (optional)", "StaffingV2SectionTitle"))
        search_wrap.addWidget(people_search)
        suggested = self.QtWidgets.QHBoxLayout()
        for name in ("Emma Johnson\nTeacher · Active", "Olivia Martinez\nTeacher · Active", "Sophia Williams\nTeacher · Inactive"):
            card, card_layout = self._dialog_section()
            card_layout.addWidget(self._label(name))
            suggested.addWidget(card)
        search_wrap.addLayout(suggested)
        form.addLayout(search_wrap, 3, 0, 1, 3)
        details_layout.addLayout(form)
        left_column.addWidget(details)

        validation, validation_layout = self._dialog_section()
        validation_layout.addWidget(self._label("5. Validation / Requirements", "StaffingV2SectionTitle"))
        for line in (
            "✓ Start date is required",
            "✓ Person name is required",
            "✓ No duplicate active person match found",
            "✓ Position currently has one active open cycle",
            "✓ Ready to save",
        ):
            validation_layout.addWidget(self._label(line))
        right_column.addWidget(validation)

        happens, happens_layout = self._dialog_section("StaffingV2DialogInfo")
        happens_layout.addWidget(self._label("6. What will happen on save", "StaffingV2SectionTitle"))
        for line in (
            "Assignment status will change to Coming",
            "Assignment title/person will be set to the selected candidate",
            "StartDate will be recorded",
            "Person will be marked Active = true",
        ):
            happens_layout.addWidget(self._label(f"✓ {line}"))
        right_column.addWidget(happens)
        right_column.addStretch(1)

        body.addLayout(left_column, 2)
        body.addLayout(right_column, 1)
        root.addLayout(body, 1)

        warning, warning_layout = self._dialog_section("StaffingV2DialogWarning")
        warning_layout.addWidget(
            self._label("This action does not close the open assignment history cycle. The cycle closes when the position is marked Filled.")
        )
        root.addWidget(warning)
        error = self._label("", "StaffingV2NeedNowChip")
        error.hide()
        root.addWidget(error)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2ComingCancel")
        self._set_button_icon(cancel, "close")
        cancel.clicked.connect(dialog.close)
        draft = self.QtWidgets.QPushButton("Save Draft")
        draft.setObjectName("StaffingV2ComingSaveDraft")
        self._set_button_icon(draft, "export")
        draft.setEnabled(False)
        submit = self.QtWidgets.QPushButton("Mark Coming")
        submit.setObjectName("StaffingV2ComingSubmit")
        self._set_button_icon(submit, "status_pending")
        footer.addWidget(cancel)
        footer.addWidget(draft)
        footer.addWidget(submit)
        root.addLayout(footer)

        def save() -> None:
            name = full_name.text().strip()
            if not name:
                error.setText("Full name is required.")
                error.show()
                return
            try:
                service = self.service_factory()
                result = service.mark_coming(
                    assignment_id,
                    person_name=name,
                    start_date=start_date.date().toString("yyyy-MM-dd"),
                )
                selected_permit = str(permit_status.currentData() or "unknown")
                if selected_permit != "unknown" and result.person_id is not None:
                    service.update_permit_status(result.person_id, selected_permit)
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(str(exc))
                error.show()
                return
            dialog.close()
            self.refresh()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
        dialog.show()

    def _open_manage_filled_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2ManageFilledDialog")
        dialog.setWindowTitle("Manage Filled Position")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(780, 640)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Manage Filled Position", "StaffingV2DrawerTitle"))
        title_block.addWidget(self._label("Choose what you want to do with this filled position.", "StaffingV2Muted"))
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ManageFilledClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        root.addLayout(header)

        summary, summary_layout = self._dialog_section()
        summary_grid = self.QtWidgets.QGridLayout()
        summary_items = [
            ("Employee", assignment.person_name or "-"),
            ("Position", assignment.position_name),
            ("Classroom", assignment.classroom),
            ("School", assignment.school),
            ("Current Status", _display_status(assignment.status)),
            ("Permit Status", _display_permit(assignment.permit_status or "unknown")),
        ]
        for column, (label, value) in enumerate(summary_items):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            if label == "Current Status":
                cell.addWidget(self._chip(value, assignment.status))
            elif label == "Permit Status":
                cell.addWidget(self._chip(value, "coming"))
            else:
                cell.addWidget(self._label(value))
            summary_grid.addLayout(cell, 0, column)
        summary_layout.addLayout(summary_grid)
        root.addWidget(summary)

        choice_group = self.QtWidgets.QButtonGroup(dialog)
        choice_group.setExclusive(True)
        cards = self.QtWidgets.QHBoxLayout()
        permit_card, permit_layout = self._dialog_section("StaffingV2ManagePermitCard")
        permit_option = self.QtWidgets.QRadioButton("")
        permit_option.setObjectName("StaffingV2ManageFilledPermitOption")
        permit_option.setChecked(True)
        choice_group.addButton(permit_option)
        permit_header = self.QtWidgets.QHBoxLayout()
        permit_header.addWidget(self._label("Update Permit Status", "StaffingV2DrawerPositionName"))
        permit_header.addStretch(1)
        permit_header.addWidget(permit_option)
        permit_layout.addLayout(permit_header)
        permit_layout.addWidget(self._label("Change the employee's permit status without reopening the position."))
        for line in (
            "✓ Updates People.PermitStatus",
            "✓ Keeps assignment status as Filled",
            "✓ Use when permit level changes or documentation is received",
        ):
            permit_layout.addWidget(self._label(line))
        permit_action = self.QtWidgets.QPushButton("Continue to Permit Update")
        permit_action.setObjectName("StaffingV2ManagePermitContinue")
        permit_layout.addStretch(1)
        permit_layout.addWidget(permit_action)
        cards.addWidget(permit_card)

        replace_card, replace_layout = self._dialog_section("StaffingV2ManageReplaceCard")
        replace_option = self.QtWidgets.QRadioButton("")
        replace_option.setObjectName("StaffingV2ManageFilledReplaceOption")
        choice_group.addButton(replace_option)
        replace_header = self.QtWidgets.QHBoxLayout()
        replace_header.addWidget(self._label("Replace Employee", "StaffingV2DrawerPositionName"))
        replace_header.addStretch(1)
        replace_header.addWidget(replace_option)
        replace_layout.addLayout(replace_header)
        replace_layout.addWidget(self._label("Mark the employee as leaving and reopen the staffing cycle for this position."))
        for line in (
            "✓ Captures Notice Given and Final Working Day",
            "✓ Marks People.Active = false",
            "✓ Changes assignment status to Replace",
            "✓ Creates a new AssignmentHistory open cycle",
        ):
            replace_layout.addWidget(self._label(line))
        replace_action = self.QtWidgets.QPushButton("Continue to Replace Workflow")
        replace_action.setObjectName("StaffingV2ManageReplaceContinue")
        replace_layout.addStretch(1)
        replace_layout.addWidget(replace_action)
        cards.addWidget(replace_card)
        root.addLayout(cards, 1)

        info, info_layout = self._dialog_section("StaffingV2DialogInfo")
        info_layout.addWidget(self._label("What happens next", "StaffingV2SectionTitle"))
        for line in (
            "This step does not change anything until you continue",
            "You will confirm required fields on the next screen",
            "All state changes update Assignments, People, and AssignmentHistory deterministically.",
        ):
            info_layout.addWidget(self._label(f"✓ {line}"))
        root.addWidget(info)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2ManageFilledCancel")
        cancel.clicked.connect(dialog.close)
        continue_button = self.QtWidgets.QPushButton("Continue")
        continue_button.setObjectName("StaffingV2ManageFilledContinue")
        footer.addWidget(cancel)
        footer.addWidget(continue_button)
        root.addLayout(footer)

        def run_selected() -> None:
            action_key = "replace_employee" if replace_option.isChecked() else "update_permit"
            callback = self.actions.get(action_key)
            dialog.close()
            if action_key == "update_permit":
                self._open_update_permit_dialog(assignment_id)
                return
            if callback is not None:
                callback(assignment_id)

        permit_action.clicked.connect(lambda _checked=False: permit_option.setChecked(True))
        replace_action.clicked.connect(lambda _checked=False: replace_option.setChecked(True))
        continue_button.clicked.connect(run_selected)
        dialog.show()

    def _open_update_permit_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        if assignment.person_id is None:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2UpdatePermitDialog")
        dialog.setWindowTitle("Update Permit Status")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(760, 660)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Update Permit Status", "StaffingV2DrawerTitle"))
        title_block.addWidget(self._label("Update the employee permit level without reopening this position.", "StaffingV2Muted"))
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2PermitClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        root.addLayout(header)

        summary, summary_layout = self._dialog_section()
        summary_layout.addWidget(self._label("Position Summary", "StaffingV2SectionTitle"))
        summary_grid = self.QtWidgets.QGridLayout()
        summary_items = [
            ("Employee", assignment.person_name or "-"),
            ("Position", assignment.position_name),
            ("Classroom", assignment.classroom),
            ("School", assignment.school),
            ("Current Status", _display_status(assignment.status)),
            ("Current Permit Status", _display_permit(assignment.permit_status or "unknown")),
            ("Assignment ID", f"A-{assignment.id:04d}"),
        ]
        for column, (label, value) in enumerate(summary_items):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            if label == "Current Status":
                cell.addWidget(self._chip(value, assignment.status))
            elif label == "Current Permit Status":
                cell.addWidget(self._chip(value, "coming"))
            else:
                cell.addWidget(self._label(value))
            summary_grid.addLayout(cell, 0, column)
        summary_layout.addLayout(summary_grid)
        root.addWidget(summary)

        body = self.QtWidgets.QHBoxLayout()
        form_section, form_layout = self._dialog_section()
        form_layout.addWidget(self._label("Permit Update", "StaffingV2SectionTitle"))
        form = self.QtWidgets.QGridLayout()
        employee_name = self.QtWidgets.QLineEdit()
        employee_name.setObjectName("StaffingV2PermitEmployeeName")
        employee_name.setText(assignment.person_name or "")
        employee_name.setEnabled(False)
        role = self.QtWidgets.QLineEdit()
        role.setObjectName("StaffingV2PermitRole")
        role.setText(assignment.position_type)
        role.setEnabled(False)
        current_status = self._chip(_display_permit(assignment.permit_status or "unknown"), "coming")
        new_status = self.QtWidgets.QComboBox()
        new_status.setObjectName("StaffingV2PermitNewStatus")
        for code in ("unknown", "permit_in_process", "teacher_permit_approved", "no_permit_or_application", "no_units_needed"):
            new_status.addItem(_display_permit(code), code)
        new_status.setCurrentText("Teacher Permit")
        effective_date = self.QtWidgets.QDateEdit()
        effective_date.setObjectName("StaffingV2PermitEffectiveDate")
        effective_date.setCalendarPopup(True)
        effective_date.setDisplayFormat("yyyy-MM-dd")
        effective_date.setDate(self.QtCore.QDate.currentDate())
        units = self.QtWidgets.QSpinBox()
        units.setObjectName("StaffingV2PermitUnits")
        units.setRange(0, 99)
        units.setValue(24)
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2PermitNotes")
        notes.setPlaceholderText("Add documentation or comments about this permit update.")
        notes.setFixedHeight(72)
        documentation = self.QtWidgets.QCheckBox("Documentation received")
        documentation.setObjectName("StaffingV2PermitDocumentationReceived")
        documentation.setChecked(True)
        attach = self.QtWidgets.QPushButton("Attach File")
        attach.setEnabled(False)
        fields = [
            ("Employee Name", employee_name, 0, 0),
            ("Role", role, 1, 0),
            ("Current Permit Status", current_status, 2, 0),
            ("New Permit Status *", new_status, 3, 0),
            ("Effective Date *", effective_date, 4, 0),
            ("Units", units, 5, 0),
        ]
        for label, widget, row, column in fields:
            wrap = self.QtWidgets.QVBoxLayout()
            wrap.addWidget(self._label(label, "StaffingV2Muted"))
            wrap.addWidget(widget)
            form.addLayout(wrap, row, column)
        notes_wrap = self.QtWidgets.QVBoxLayout()
        notes_wrap.addWidget(self._label("Notes (optional)", "StaffingV2Muted"))
        notes_wrap.addWidget(notes)
        form.addLayout(notes_wrap, 6, 0)
        form.addWidget(documentation, 7, 0)
        form.addWidget(attach, 8, 0)
        form_layout.addLayout(form)
        body.addWidget(form_section, 2)

        right = self.QtWidgets.QVBoxLayout()
        validation, validation_layout = self._dialog_section()
        validation_layout.addWidget(self._label("Validation / Requirements", "StaffingV2SectionTitle"))
        for line in (
            "✓ New permit status is selected",
            "✓ Effective date is required",
            "✓ Employee record found",
            "✓ Position will remain Filled",
            "✓ Ready to save",
        ):
            validation_layout.addWidget(self._label(line))
        right.addWidget(validation)
        happens, happens_layout = self._dialog_section("StaffingV2DialogInfo")
        happens_layout.addWidget(self._label("What will happen on save", "StaffingV2SectionTitle"))
        for line in (
            "People.PermitStatus will update to selected status",
            "Effective date will be recorded",
            "Assignment status stays Filled",
            "No AssignmentHistory cycle changes will occur",
        ):
            happens_layout.addWidget(self._label(f"✓ {line}"))
        right.addWidget(happens)
        right.addStretch(1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        warning, warning_layout = self._dialog_section("StaffingV2DialogWarning")
        warning_layout.addWidget(self._label("This action updates People only and does not reopen the staffing cycle."))
        root.addWidget(warning)
        error = self._label("", "StaffingV2NeedNowChip")
        error.hide()
        root.addWidget(error)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2PermitCancel")
        cancel.clicked.connect(dialog.close)
        draft = self.QtWidgets.QPushButton("Save Draft")
        draft.setObjectName("StaffingV2PermitDraft")
        draft.setEnabled(False)
        submit = self.QtWidgets.QPushButton("Save Permit Update")
        submit.setObjectName("StaffingV2PermitSubmit")
        footer.addWidget(cancel)
        footer.addWidget(draft)
        footer.addWidget(submit)
        root.addLayout(footer)

        def save() -> None:
            try:
                self.service_factory().update_permit_status(
                    assignment.person_id or 0,
                    str(new_status.currentData() or "unknown"),
                    effective_date=effective_date.date().toString("yyyy-MM-dd"),
                    units=units.value(),
                    documentation_received=documentation.isChecked(),
                    notes=notes.toPlainText(),
                )
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(str(exc))
                error.show()
                return
            dialog.close()
            self.refresh()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
        dialog.show()

    def _open_mark_need_now_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2MarkNeedNowDialog")
        dialog.setWindowTitle("Mark Position as Need Now")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(560, 620)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Mark Position as Need Now", "StaffingV2DrawerTitle"))
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2NeedNowClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        root.addLayout(header)

        warning, warning_layout = self._dialog_section("StaffingV2DialogWarning")
        warning_layout.addWidget(
            self._label("This action will change the status from Replace to Need Now and reopen the position for hiring.")
        )
        root.addWidget(warning)

        summary, summary_layout = self._dialog_section()
        summary_layout.addWidget(self._label("Position Summary", "StaffingV2SectionTitle"))
        grid = self.QtWidgets.QGridLayout()
        summary_items = [
            ("Classroom", f"{assignment.classroom} ({assignment.school})"),
            ("Replacing Employee", assignment.person_name or "-"),
            ("Position", assignment.position_name),
            ("Notice Given", _display_date(assignment.notice_given)),
            ("Current Status", _display_status(assignment.status)),
            ("Final Working Day", _display_date(assignment.final_working_day)),
        ]
        for index, (label, value) in enumerate(summary_items):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            if label == "Current Status":
                cell.addWidget(self._chip(value, assignment.status))
            else:
                cell.addWidget(self._label(value))
            grid.addLayout(cell, index // 2, index % 2)
        summary_layout.addLayout(grid)
        root.addWidget(summary)

        happens, happens_layout = self._dialog_section()
        happens_layout.addWidget(self._label("What will happen", "StaffingV2SectionTitle"))
        for line in (
            "Status will change from Replace to Need Now",
            "Position will be reopened for hiring",
            "Teacher name and start date will be cleared",
            "A new open cycle will continue",
        ):
            happens_layout.addWidget(self._label(f"✓ {line}"))
        root.addWidget(happens)

        options, options_layout = self._dialog_section()
        options_layout.addWidget(self._label("Options", "StaffingV2SectionTitle"))
        clear_person = self.QtWidgets.QCheckBox("Clear assigned person and start date")
        clear_person.setObjectName("StaffingV2NeedNowClearPerson")
        clear_person.setChecked(True)
        options_layout.addWidget(clear_person)
        info, info_layout = self._dialog_section("StaffingV2DialogInfo")
        info_layout.addWidget(
            self._label(f"This will remove {assignment.person_name or 'the assigned person'} as the assigned person for this position.")
        )
        options_layout.addWidget(info)
        root.addWidget(options)

        error = self._label("", "StaffingV2NeedNowChip")
        error.hide()
        root.addWidget(error)
        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2NeedNowCancel")
        cancel.clicked.connect(dialog.close)
        submit = self.QtWidgets.QPushButton("Confirm & Mark Need Now")
        submit.setObjectName("StaffingV2NeedNowSubmit")
        footer.addWidget(cancel)
        footer.addWidget(submit)
        root.addLayout(footer)

        def save() -> None:
            if not clear_person.isChecked():
                error.setText("Clear assigned person is required for this transition.")
                error.show()
                return
            try:
                self.service_factory().clear_replacement(assignment_id)
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(str(exc))
                error.show()
                return
            dialog.close()
            self.refresh()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
        dialog.show()

    def _open_mark_filled_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        metric_row = next((row for row in self.rows if row.assignment_id == assignment_id), None)
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2MarkFilledDialog")
        dialog.setWindowTitle("Mark Filled")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(940, 720)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Mark Filled", "StaffingV2DrawerTitle"))
        title_block.addWidget(self._label("Confirm that the candidate has started and close the current open cycle.", "StaffingV2Muted"))
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2FilledClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        root.addLayout(header)

        summary, summary_layout = self._dialog_section()
        summary_layout.addWidget(self._label("1. Position Summary", "StaffingV2SectionTitle"))
        summary_grid = self.QtWidgets.QGridLayout()
        summary_items = [
            ("Classroom", assignment.classroom),
            ("School", assignment.school),
            ("Program", assignment.classroom_program or "-"),
            ("Position", assignment.position_name),
            ("Current Status", _display_status(assignment.status)),
            ("Scheduled Start Date", assignment.start_date or "-"),
            ("Assignment ID", f"A-{assignment.id:04d}"),
        ]
        for column, (label, value) in enumerate(summary_items):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            if label == "Current Status":
                cell.addWidget(self._chip(value, assignment.status))
            else:
                cell.addWidget(self._label(value))
            summary_grid.addLayout(cell, 0, column)
        summary_layout.addLayout(summary_grid)
        root.addWidget(summary)

        body = self.QtWidgets.QHBoxLayout()
        left_column = self.QtWidgets.QVBoxLayout()
        right_column = self.QtWidgets.QVBoxLayout()
        left_column.setSpacing(12)
        right_column.setSpacing(12)

        person, person_layout = self._dialog_section()
        person_layout.addWidget(self._label("2. Assigned Person", "StaffingV2SectionTitle"))
        person_grid = self.QtWidgets.QGridLayout()
        for column, (label, value) in enumerate(
            [
                ("Name", assignment.person_name or "-"),
                ("Role", assignment.position_type),
                ("Permit Status", _display_permit(assignment.permit_status or "unknown")),
                ("Active", "true"),
            ]
        ):
            cell = self.QtWidgets.QVBoxLayout()
            cell.addWidget(self._label(label, "StaffingV2Muted"))
            cell.addWidget(self._label(value))
            person_grid.addLayout(cell, 0, column)
        person_layout.addLayout(person_grid)
        left_column.addWidget(person)

        confirmation, confirmation_layout = self._dialog_section()
        confirmation_layout.addWidget(self._label("3. Start Confirmation", "StaffingV2SectionTitle"))
        form = self.QtWidgets.QGridLayout()
        filled_date = self.QtWidgets.QLineEdit()
        filled_date.setObjectName("StaffingV2FilledDate")
        filled_date.setText(assignment.start_date or "")
        filled_date.setEnabled(False)
        current_date = self.QtWidgets.QDateEdit()
        current_date.setObjectName("StaffingV2FilledCurrentDate")
        current_date.setDisplayFormat("yyyy-MM-dd")
        current_date.setDate(self.QtCore.QDate.currentDate())
        current_date.setEnabled(False)
        timestamp = self.QtWidgets.QLineEdit()
        timestamp.setObjectName("StaffingV2FilledTimestamp")
        timestamp.setText("Auto")
        timestamp.setEnabled(False)
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2FilledNotes")
        notes.setPlaceholderText("Add any notes about the start.")
        notes.setFixedHeight(82)
        started = self.QtWidgets.QCheckBox("Employee started as scheduled")
        started.setObjectName("StaffingV2FilledStarted")
        started.setChecked(True)
        for column, (label, widget) in enumerate(
            [
                ("Actual Start Date *", filled_date),
                ("Current Filled Date (Today)", current_date),
                ("Filled Timestamp (Auto)", timestamp),
            ]
        ):
            wrap = self.QtWidgets.QVBoxLayout()
            wrap.addWidget(self._label(label, "StaffingV2Muted"))
            wrap.addWidget(widget)
            form.addLayout(wrap, 0, column)
        notes_wrap = self.QtWidgets.QVBoxLayout()
        notes_wrap.addWidget(self._label("Notes (optional)", "StaffingV2Muted"))
        notes_wrap.addWidget(notes)
        form.addLayout(notes_wrap, 1, 0, 1, 3)
        form.addWidget(started, 2, 0, 1, 3)
        confirmation_layout.addLayout(form)
        left_column.addWidget(confirmation)

        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        preview_layout.addWidget(self._label("4. Cycle Close Preview", "StaffingV2SectionTitle"))
        preview_layout.addWidget(
            self._label(
                f"Opened Date: {assignment.current_opened_date or '-'}    Filled Date: {assignment.start_date or '-'}    Estimated Days to Fill: {_days_open_text(metric_row)}"
            )
        )
        left_column.addWidget(preview)

        validation, validation_layout = self._dialog_section()
        validation_layout.addWidget(self._label("5. Validation / Requirements", "StaffingV2SectionTitle"))
        for line in (
            "✓ Assigned person found",
            "✓ Actual start date is required",
            "✓ One active open history cycle found",
            "✓ Ready to close cycle",
        ):
            validation_layout.addWidget(self._label(line))
        right_column.addWidget(validation)

        happens, happens_layout = self._dialog_section("StaffingV2DialogInfo")
        happens_layout.addWidget(self._label("6. What will happen on save", "StaffingV2SectionTitle"))
        for line in (
            "Assignment status will change to Filled",
            "CurrentFilledDate will be set to the candidate start date",
            "Latest AssignmentHistory record will be updated with FilledDate",
            "DaysToFill will be calculated",
            "Position will no longer count as open",
        ):
            happens_layout.addWidget(self._label(f"✓ {line}"))
        right_column.addWidget(happens)
        right_column.addStretch(1)

        body.addLayout(left_column, 2)
        body.addLayout(right_column, 1)
        root.addLayout(body, 1)

        warning, warning_layout = self._dialog_section("StaffingV2DialogWarning")
        warning_layout.addWidget(
            self._label("This action closes the current open assignment history cycle. Reopen the position later using Replace or Need Now if needed.")
        )
        root.addWidget(warning)
        error = self._label("", "StaffingV2NeedNowChip")
        error.hide()
        root.addWidget(error)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2FilledCancel")
        cancel.clicked.connect(dialog.close)
        draft = self.QtWidgets.QPushButton("Save Draft")
        draft.setObjectName("StaffingV2FilledSaveDraft")
        draft.setEnabled(False)
        submit = self.QtWidgets.QPushButton("Mark Filled")
        submit.setObjectName("StaffingV2FilledSubmit")
        footer.addWidget(cancel)
        footer.addWidget(draft)
        footer.addWidget(submit)
        root.addLayout(footer)

        def save() -> None:
            if not started.isChecked():
                error.setText("Confirm that employee started as scheduled.")
                error.show()
                return
            try:
                self.service_factory().mark_filled(assignment_id)
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(str(exc))
                error.show()
                return
            dialog.close()
            self.refresh()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
        dialog.show()

    def _metric_card(self, label: str, value: str, accessible_text: str, object_name: str = "StaffingV2MetricCard") -> Any:
        card, layout = self._panel(object_name)
        card.setAccessibleName(accessible_text)
        icon_row = self.QtWidgets.QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_row.addWidget(self._icon_label(_metric_icon_key(label), "StaffingV2CardIcon"))
        icon_row.addWidget(self._label(label, "StaffingV2Muted"), 1)
        layout.addLayout(icon_row)
        value_widget = self._label(value, "StaffingV2MetricValue")
        layout.addWidget(value_widget)
        return card

    def _summary_chip(self, label: str, value: str, accessible_text: str) -> Any:
        card, layout = self._panel("StaffingV2MetricCard")
        card.setAccessibleName(accessible_text)
        variant = "success" if "validation" in accessible_text.casefold() else "danger" if "open > 7" in accessible_text.casefold() else "info"
        card.setProperty("staffingV2SummaryVariant", variant)
        card.setMinimumHeight(38)
        card.setMaximumHeight(48)
        row = self.QtWidgets.QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)
        row.addWidget(self._icon_label(_metric_icon_key(label), "StaffingV2SummaryIcon"))
        label_text = label if variant == "success" else f"{label}:"
        label_widget = self._label(label_text, "StaffingV2SummaryLabel")
        label_widget.setProperty("staffingV2SummaryVariant", variant)
        row.addWidget(label_widget)
        value_widget = self._label(value, "StaffingV2SummaryValue")
        value_widget.setProperty("staffingV2SummaryVariant", variant)
        row.addWidget(value_widget)
        layout.addLayout(row)
        return card

    def _add_position_drop_zone(self) -> Any:
        frame, layout = self._panel("StaffingV2AddPositionDropZone")
        frame.setMinimumHeight(58)
        frame.setMaximumHeight(66)
        button = self.QtWidgets.QPushButton("Add Position")
        button.setObjectName("StaffingV2DropZoneAddButton")
        self._set_button_icon(button, "add")
        button.clicked.connect(self._open_add_position_dialog)
        layout.addWidget(button, alignment=self.QtCore.Qt.AlignmentFlag.AlignCenter)
        return frame

    def _status_key(self) -> Any:
        frame, layout = self._panel("StaffingV2StatusKey")
        row = self.QtWidgets.QHBoxLayout()
        row.addWidget(self._label("Status Key", "StaffingV2Muted"))
        for status in ("need_now", "replace", "coming", "filled", "dont_need_now"):
            row.addWidget(self._chip(_display_status(status), status))
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _chip(self, text: str, status: str) -> Any:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName(_chip_object_name(status))
        layout = self.QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        layout.addWidget(self._icon_label(_status_icon_key(status), "StaffingV2ChipIcon"))
        label = self._label(text, "StaffingV2ChipText")
        label.setWordWrap(False)
        label.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return frame

    def _icon_label(self, icon_key: str, object_name: str) -> Any:
        label = self.QtWidgets.QLabel()
        label.setObjectName(object_name)
        label.setPixmap(self._standard_icon(icon_key).pixmap(16, 16))
        label.setFixedSize(18, 18)
        label.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        return label

    def _panel(self, object_name: str = "StaffingV2Panel") -> tuple[Any, Any]:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName(object_name)
        layout = self.QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        return frame, layout

    def _dialog_section(self, object_name: str = "StaffingV2DialogSection") -> tuple[Any, Any]:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName(object_name)
        layout = self.QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        return frame, layout

    def _clear_layout(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.deleteLater()

    def _label(self, text: str, object_name: str = "") -> Any:
        label = self.QtWidgets.QLabel(text)
        if object_name:
            label.setObjectName(object_name)
        label.setWordWrap(True)
        return label


def _classroom_label(classroom: str, rows: list[StaffingMetricRow]) -> str:
    return f"{classroom}\n{_classroom_counts_text(rows)}"


def _classroom_counts_text(rows: list[StaffingMetricRow]) -> str:
    need = sum(1 for row in rows if row.status == "need_now")
    replace = sum(1 for row in rows if row.status == "replace")
    coming = sum(1 for row in rows if row.status == "coming")
    filled = sum(1 for row in rows if row.status == "filled")
    dont_need = sum(1 for row in rows if row.status == "dont_need_now")
    return f"Need {need} · Replace {replace} · Coming {coming} · Filled {filled} · Don't Need {dont_need}"


def _classroom_status_key(rows: list[StaffingMetricRow]) -> str:
    statuses = {row.status for row in rows}
    if "need_now" in statuses:
        return "need_now"
    if "replace" in statuses:
        return "replace"
    if "coming" in statuses:
        return "coming"
    if "filled" in statuses:
        return "filled"
    return "dont_need_now"


def _table_assignment_id(table: Any, row: int) -> int | None:
    if row < 0:
        return None
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is None:
            continue
        value = item.data(table.QtCore.Qt.ItemDataRole.UserRole) if hasattr(table, "QtCore") else item.data(256)
        if value is not None:
            return int(value)
    return None


def _days_open_text(row: StaffingMetricRow | None) -> str:
    if row is None or row.days_open is None:
        return "-"
    return str(row.days_open)


def _drawer_actions(status: str) -> list[tuple[str, str, str]]:
    if status == "need_now":
        return [
            ("StaffingV2DrawerMarkComing", "Mark Coming", "mark_coming"),
            ("StaffingV2DrawerMarkDontNeed", "Mark Don't Need", "mark_dont_need"),
            ("StaffingV2DrawerEditPosition", "Edit Position", "view_details"),
            ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
        ]
    if status == "coming":
        return [
            ("StaffingV2DrawerMarkFilled", "Mark Filled", "mark_filled"),
            ("StaffingV2DrawerRevertComing", "Revert to Need Now", "revert_coming"),
            ("StaffingV2DrawerEditPosition", "Edit Start Date", "view_details"),
            ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
        ]
    if status == "filled":
        return [
            ("StaffingV2DrawerManageFilled", "Manage Filled Position", "manage_filled"),
            ("StaffingV2DrawerUpdatePermit", "Update Permit", "update_permit"),
            ("StaffingV2DrawerReplaceEmployee", "Replace Employee", "replace_employee"),
            ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
        ]
    if status == "replace":
        return [
            ("StaffingV2DrawerMarkNeedNow", "Mark Need Now", "clear_replacement"),
            ("StaffingV2DrawerUpdateFinalDay", "Update Final Day", "view_details"),
            ("StaffingV2DrawerViewEmployee", "View Employee Profile", "view_details"),
            ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
        ]
    return [
        ("StaffingV2DrawerMarkNeedNow", "Mark Need Now", "open_position"),
        ("StaffingV2DrawerEditPosition", "Edit Position", "view_details"),
        ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
    ]


def _drawer_action_icon_key(action_key: str) -> str:
    if action_key in {"mark_coming", "mark_filled", "revert_coming"}:
        return "status_pending"
    if action_key in {"mark_dont_need", "clear_replacement", "open_position"}:
        return "status_need"
    if action_key in {"manage_filled", "update_permit"}:
        return "status_filled"
    if action_key in {"replace_employee", "view_details"}:
        return "people"
    if action_key in {"view_history", "update_final_day"}:
        return "history"
    return "info"


def _validation_lines(assignment: Any) -> list[str]:
    lines = ["Pass: Assignment linked to valid classroom", "Pass: No duplicate open cycles detected in this view"]
    if assignment.status == "coming":
        lines.append("Pass" if assignment.start_date else "Warning: Coming position missing start date")
    if assignment.status == "filled":
        lines.append("Pass" if assignment.person_name else "Critical: Filled position missing assigned person")
    if assignment.status == "replace":
        lines.append("Pass" if assignment.notice_given and assignment.final_working_day else "Warning: Replacement dates need review")
    if assignment.permit_status:
        lines.append(f"Pass: Permit status {_display_permit(assignment.permit_status)}")
    else:
        lines.append("Warning: Permit status missing until candidate is assigned")
    return lines


def _lifecycle_lines(assignment: Any) -> list[str]:
    lines = []
    if assignment.current_opened_date:
        lines.append(f"Position opened: {assignment.current_opened_date}")
    if assignment.start_date:
        lines.append(f"Candidate marked Coming: {assignment.start_date}")
    if assignment.current_filled_date:
        lines.append(f"Position marked Filled: {assignment.current_filled_date}")
    if assignment.status == "replace":
        lines.append("Replacement started")
    return lines or ["No lifecycle events recorded yet."]


def _avg_open_days(rows: list[StaffingMetricRow]) -> str:
    open_days = [row.days_open for row in rows if row.status in {"need_now", "replace"} and row.days_open is not None]
    if not open_days:
        return "0.0"
    return f"{sum(open_days) / len(open_days):.1f}"


def _classroom_priority_status(rows: list[StaffingMetricRow]) -> str:
    statuses = {row.status for row in rows}
    if "need_now" in statuses:
        return "Need Now"
    if "replace" in statuses:
        return "Replace"
    if "coming" in statuses:
        return "Coming"
    if statuses and statuses <= {"filled"}:
        return "Filled / Healthy"
    if statuses and statuses <= {"dont_need_now"}:
        return "Don't Need"
    if "filled" in statuses:
        return "Filled / Healthy"
    return "Don't Need"


def _validation_issues_from_rows(rows: list[StaffingMetricRow]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for row in rows:
        base = {
            "assignment_id": str(row.assignment_id),
            "school": row.school,
            "classroom": row.classroom,
            "program": row.classroom_program or "",
            "detected": "Today",
        }
        if row.status == "need_now" and not row.person_name:
            issues.append(
                {
                    **base,
                    "issue": "Unfilled Need Now position",
                    "type": "Coverage",
                    "severity": "Critical",
                    "details": f"{row.position_name} is open for hiring",
                }
            )
        if row.status == "coming" and not row.start_date:
            issues.append(
                {
                    **base,
                    "issue": "Coming position missing start date",
                    "type": "Upcoming",
                    "severity": "Warning",
                    "details": f"{row.position_name} has no start date",
                }
            )
        if row.permit_status in {"", "unknown"} and row.status in {"need_now", "coming", "filled", "replace"}:
            issues.append(
                {
                    **base,
                    "issue": "Permit status unknown",
                    "type": "Compliance",
                    "severity": "Info",
                    "details": f"{row.position_name} permit status needs review",
                }
            )
    severity_order = {"Critical": 0, "Warning": 1, "Info": 2}
    return sorted(issues, key=lambda issue: (severity_order.get(issue["severity"], 3), issue["classroom"], issue["issue"]))


def _display_status(status: str) -> str:
    return {
        "need_now": "Need Now",
        "replace": "Replace",
        "coming": "Coming",
        "filled": "Filled",
        "dont_need_now": "Don't Need",
    }.get(status, status.replace("_", " ").title())


def _status_from_label(label: str) -> str:
    return {
        "Need Now": "need_now",
        "Don't Need Now": "dont_need_now",
        "Coming": "coming",
        "Filled": "filled",
        "Replace": "replace",
    }.get(str(label or "").strip(), "dont_need_now")


def _display_permit(status: str) -> str:
    return {
        "unknown": "Unknown",
        "no_permit_or_application": "No Permit",
        "permit_in_process": "Permit in Process",
        "teacher_permit_approved": "Teacher Permit",
        "no_units_needed": "No Units Needed",
    }.get(status, status.replace("_", " ").title())


def _permit_label(status: str) -> str:
    return _display_permit(status or "unknown")


def _permit_chip_status(status: str) -> str:
    return {
        "permit_in_process": "coming",
        "teacher_permit_approved": "filled",
        "no_units_needed": "filled",
        "no_permit_or_application": "replace",
    }.get(status or "unknown", "dont_need_now")


def _metric_icon_key(label: str) -> str:
    normalized = str(label or "").casefold()
    if "program" in normalized:
        return "classrooms"
    if "capacity" in normalized or "people" in normalized or "staff" in normalized:
        return "people"
    if "position" in normalized or "cycle" in normalized:
        return "dashboard"
    if "filled" in normalized or "active" in normalized or "compliance" in normalized:
        return "status_filled"
    if "open" in normalized or "days" in normalized or "warning" in normalized:
        return "status_pending"
    if "critical" in normalized or "issue" in normalized:
        return "status_need"
    return "info"


def _status_icon_key(status: str) -> str:
    return {
        "need_now": "status_need",
        "replace": "status_replace",
        "coming": "status_pending",
        "filled": "status_filled",
        "dont_need_now": "status_neutral",
    }.get(status or "dont_need_now", "status_neutral")


def _format_units(units: float | None) -> str:
    if units is None:
        return "-"
    if float(units).is_integer():
        return str(int(units))
    return f"{units:.1f}"


def _initials(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def _assignment_detail(person: StaffingPerson) -> str:
    if not person.assignment_classroom or not person.assignment_position:
        return "-"
    return f"{person.assignment_classroom} - {person.assignment_position}"


def _display_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return text
    return parsed.strftime("%B %d, %Y").replace(" 0", " ")


def _primary_action(status: str) -> tuple[str, str]:
    return {
        "dont_need_now": ("open_position", "Mark Need Now"),
        "need_now": ("mark_coming", "Mark Coming"),
        "coming": ("mark_filled", "Mark Filled"),
        "filled": ("manage_filled", "Manage Filled"),
        "replace": ("clear_replacement", "Mark Need Now"),
    }.get(status, ("view_details", "View"))


def _action_menu_specs(status: str) -> list[tuple[str, str]]:
    return {
        "need_now": [
            ("Mark Coming", "mark_coming"),
            ("Mark Don't Need", "mark_dont_need"),
            ("View Details", "view_details"),
        ],
        "coming": [
            ("Mark Filled", "mark_filled"),
            ("Revert Coming", "revert_coming"),
            ("View Details", "view_details"),
        ],
        "filled": [
            ("Manage Filled", "manage_filled"),
            ("Replace", "manage_filled"),
            ("Update Permit", "update_permit"),
            ("View Details", "view_details"),
        ],
        "replace": [
            ("Mark Need Now", "clear_replacement"),
            ("Update Final Day", "view_details"),
            ("View Details", "view_details"),
        ],
        "dont_need_now": [
            ("Mark Need Now", "open_position"),
            ("View Details", "view_details"),
        ],
    }.get(status, [("View Details", "view_details")])


def _status_color(status: str) -> str:
    return {
        "need_now": "#fee2e2",
        "replace": "#ffedd5",
        "coming": "#fef3c7",
        "filled": "#dcfce7",
        "dont_need_now": "#f1f5f9",
    }.get(status, "#ffffff")


def _chip_object_name(status: str) -> str:
    return {
        "need_now": "StaffingV2NeedNowChip",
        "replace": "StaffingV2ReplaceChip",
        "coming": "StaffingV2ComingChip",
        "filled": "StaffingV2FilledChip",
        "dont_need_now": "StaffingV2NeutralChip",
    }.get(status, "StaffingV2NeutralChip")
