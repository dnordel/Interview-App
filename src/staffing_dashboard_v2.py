from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any

from notification_models import NotificationRecipient, NotificationRule, NotificationTestPayload
from notification_service import EXECUTIVE_DIRECTOR_EMAIL, HIRING_MANAGER_EMAIL, NotificationService
from notification_store import NotificationStore
from notification_templates import (
    NOTIFICATION_TEMPLATE_FIELD_CATALOG,
    NOTIFICATION_TEMPLATE_FIELDS,
    notification_template_fields,
    render_notification_templates,
    validate_notification_rule,
)
from staffing_models import (
    StaffingClassroom,
    StaffingDirectorCandidate,
    StaffingDirectorInterview,
    StaffingHistoryRecord,
    StaffingMetricRow,
    StaffingPerson,
)
from staffing_service import StaffingService, staffing_notification_payload
from staffing_store import StaffingStore


def apply_staffing_v2_light_theme(QtWidgets: Any, QtGui: Any, app: Any | None = None) -> None:
    """Force Staffing v2 onto a stable light Qt palette instead of host OS colors."""
    application = app or QtWidgets.QApplication.instance()
    if application is None:
        return

    style_factory = getattr(QtWidgets, "QStyleFactory", None)
    if style_factory is not None:
        try:
            if "Fusion" in style_factory.keys():
                application.setStyle("Fusion")
        except RuntimeError:
            pass

    color_role = QtGui.QPalette.ColorRole
    color_group = QtGui.QPalette.ColorGroup
    color = QtGui.QColor
    palette = QtGui.QPalette()
    for group in (color_group.Active, color_group.Inactive):
        palette.setColor(group, color_role.Window, color("#f8fafc"))
        palette.setColor(group, color_role.WindowText, color("#0f172a"))
        palette.setColor(group, color_role.Base, color("#ffffff"))
        palette.setColor(group, color_role.AlternateBase, color("#f1f5f9"))
        palette.setColor(group, color_role.ToolTipBase, color("#ffffff"))
        palette.setColor(group, color_role.ToolTipText, color("#0f172a"))
        palette.setColor(group, color_role.Text, color("#0f172a"))
        palette.setColor(group, color_role.Button, color("#ffffff"))
        palette.setColor(group, color_role.ButtonText, color("#0f172a"))
        palette.setColor(group, color_role.BrightText, color("#ffffff"))
        palette.setColor(group, color_role.Link, color("#2563eb"))
        palette.setColor(group, color_role.Highlight, color("#2563eb"))
        palette.setColor(group, color_role.HighlightedText, color("#ffffff"))
        if hasattr(color_role, "PlaceholderText"):
            palette.setColor(group, color_role.PlaceholderText, color("#64748b"))
    palette.setColor(color_group.Disabled, color_role.Window, color("#f8fafc"))
    palette.setColor(color_group.Disabled, color_role.WindowText, color("#94a3b8"))
    palette.setColor(color_group.Disabled, color_role.Base, color("#f1f5f9"))
    palette.setColor(color_group.Disabled, color_role.Text, color("#94a3b8"))
    palette.setColor(color_group.Disabled, color_role.Button, color("#f1f5f9"))
    palette.setColor(color_group.Disabled, color_role.ButtonText, color("#94a3b8"))
    application.setPalette(palette)
    style_hints = application.styleHints()
    if hasattr(style_hints, "setColorScheme") and hasattr(QtGui, "Qt"):
        try:
            style_hints.setColorScheme(QtGui.Qt.ColorScheme.Light)
        except (AttributeError, RuntimeError, TypeError):
            pass
    application.setProperty("_staffing_v2_forced_light_theme", True)


def configure_v2_scroll_areas(QtWidgets: Any, root: Any, QtCore: Any | None = None) -> None:
    """Apply v2 per-pixel wheel/scrollbar behavior under a widget root."""
    for scroll_area in root.findChildren(QtWidgets.QAbstractScrollArea):
        if isinstance(scroll_area, QtWidgets.QAbstractItemView):
            scroll_area.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
            scroll_area.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        scroll_area.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        vertical_bar = scroll_area.verticalScrollBar()
        horizontal_bar = scroll_area.horizontalScrollBar()
        vertical_bar.setSingleStep(24)
        horizontal_bar.setSingleStep(24)
        vertical_bar.setPageStep(max(80, scroll_area.viewport().height() - 48))
        horizontal_bar.setPageStep(max(80, scroll_area.viewport().width() - 48))
        if QtCore is not None:
            _install_v2_wheel_relay(QtCore, QtWidgets, root, scroll_area)
    if QtCore is not None:
        _install_v2_application_wheel_router(QtCore, QtWidgets, root)


def _install_v2_application_wheel_router(QtCore: Any, QtWidgets: Any, root: Any) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    routers = getattr(app, "_staffing_v2_application_wheel_routers", None)
    if routers is None:
        routers = {}
        setattr(app, "_staffing_v2_application_wheel_routers", routers)
    router_key = id(root)
    router = routers.get(router_key)
    if router is None:
        router = _StaffingV2ApplicationWheelRouter(QtCore, QtWidgets, root)
        routers[router_key] = router
        app.installEventFilter(router)
    setattr(root, "_staffing_v2_application_wheel_router", router)


def _install_v2_wheel_relay(QtCore: Any, QtWidgets: Any, root: Any, scroll_area: Any) -> None:
    content = scroll_area.widget() if hasattr(scroll_area, "widget") else None
    viewport = scroll_area.viewport() if hasattr(scroll_area, "viewport") else None
    target_roots = [target for target in (root, content, viewport) if target is not None]
    if not target_roots:
        return

    relays = getattr(root, "_staffing_v2_wheel_relays", None)
    if relays is None:
        relays = {}
        setattr(root, "_staffing_v2_wheel_relays", relays)
    relay_key = id(scroll_area)
    relay = relays.get(relay_key)
    if relay is None:
        relay = _StaffingV2WheelRelay(QtCore, QtWidgets, root, scroll_area)
        relays[relay_key] = relay

    targets: list[Any] = []
    for target_root in target_roots:
        targets.extend([target_root, *target_root.findChildren(QtWidgets.QWidget)])
    for target in targets:
        if isinstance(target, QtWidgets.QAbstractScrollArea):
            continue
        installed = getattr(target, "_staffing_v2_wheel_relay_ids", set())
        if relay_key in installed:
            continue
        target.installEventFilter(relay)
        setattr(target, "_staffing_v2_wheel_relay_ids", {*installed, relay_key})


class _StaffingV2WheelRelay:
    def __new__(cls, QtCore: Any, QtWidgets: Any, root: Any, scroll_area: Any) -> Any:
        class WheelRelay(QtCore.QObject):
            def __init__(self) -> None:
                super().__init__(root)
                self.QtCore = QtCore
                self.QtWidgets = QtWidgets
                self.scroll_area = scroll_area

            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
                try:
                    if event.type() != self.QtCore.QEvent.Type.Wheel:
                        return False
                    if not self.scroll_area.isVisible() or not self._contains_watched(watched, event):
                        return False
                    return self._scroll(event)
                except RuntimeError:
                    return False

            def _contains_watched(self, watched: Any, event: Any) -> bool:
                content = self.scroll_area.widget() if hasattr(self.scroll_area, "widget") else None
                viewport = self.scroll_area.viewport() if hasattr(self.scroll_area, "viewport") else None
                current = watched
                while current is not None:
                    if current is self.scroll_area:
                        return True
                    if current is content or current is viewport:
                        return True
                    current = current.parentWidget()
                if viewport is None:
                    return False
                global_position = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                local_position = viewport.mapFromGlobal(global_position)
                return viewport.rect().contains(local_position)

            def _scroll(self, event: Any) -> bool:
                return _scroll_v2_area_from_wheel(self.scroll_area, event)

        return WheelRelay()


class _StaffingV2ApplicationWheelRouter:
    def __new__(cls, QtCore: Any, QtWidgets: Any, root: Any) -> Any:
        class ApplicationWheelRouter(QtCore.QObject):
            def __init__(self) -> None:
                super().__init__(root)
                self.QtWidgets = QtWidgets
                self.root = root

            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
                try:
                    if event.type() != QtCore.QEvent.Type.Wheel or not self.root.isVisible():
                        return False
                    global_position = _v2_wheel_global_position(event)
                    if not self.root.rect().contains(self.root.mapFromGlobal(global_position)):
                        return False
                    for scroll_area in self._scroll_areas_at(global_position):
                        if _scroll_v2_area_from_wheel(scroll_area, event):
                            return True
                    return False
                except RuntimeError:
                    return False

            def _scroll_areas_at(self, global_position: Any) -> list[Any]:
                candidates = []
                for scroll_area in self.root.findChildren(self.QtWidgets.QAbstractScrollArea):
                    if not scroll_area.isVisible():
                        continue
                    viewport = scroll_area.viewport() if hasattr(scroll_area, "viewport") else None
                    if viewport is None:
                        continue
                    if not viewport.rect().contains(viewport.mapFromGlobal(global_position)):
                        continue
                    candidates.append(scroll_area)
                return sorted(candidates, key=_v2_scroll_area_viewport_area)

        return ApplicationWheelRouter()


def _v2_scroll_area_viewport_area(scroll_area: Any) -> int:
    viewport = scroll_area.viewport()
    return max(1, viewport.width()) * max(1, viewport.height())


def _v2_wheel_global_position(event: Any) -> Any:
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


def _scroll_v2_area_from_wheel(scroll_area: Any, event: Any) -> bool:
    bar = scroll_area.verticalScrollBar()
    if bar.maximum() <= bar.minimum():
        return False
    before = bar.value()
    pixel_delta = event.pixelDelta()
    angle_delta = event.angleDelta()
    if pixel_delta.y():
        delta = pixel_delta.y()
    elif angle_delta.y():
        delta = int(angle_delta.y() / 120 * bar.singleStep() * 3)
    else:
        return False
    bar.setValue(before - delta)
    if bar.value() == before:
        return False
    event.accept()
    return True


APP_QSS = """
QWidget#PySideStaffingV2Page {
    background-color: #f8fafc;
    color: #0f172a;
}
QWidget#PySideStaffingV2Page QAbstractScrollArea,
QWidget#PySideStaffingV2Page QAbstractScrollArea > QWidget,
QWidget#PySideStaffingV2Page QAbstractScrollArea > QWidget > QWidget,
QWidget#PySideStaffingV2Page QTableWidget,
QWidget#PySideStaffingV2Page QTableView {
    background-color: #ffffff;
    color: #0f172a;
    alternate-background-color: #f8fafc;
    gridline-color: #e2e8f0;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
}
QWidget#PySideStaffingV2Page QHeaderView,
QWidget#PySideStaffingV2Page QHeaderView::section {
    background-color: #f8fafc;
    color: #334155;
    border: 1px solid #e2e8f0;
    font-weight: 800;
}
QWidget#PySideStaffingV2Page QLineEdit,
QWidget#PySideStaffingV2Page QComboBox,
QWidget#PySideStaffingV2Page QTextEdit,
QWidget#PySideStaffingV2Page QPlainTextEdit {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
}
QWidget#PySideStaffingV2Page QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #0f172a;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
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
QFrame#StaffingV2ClassroomsDetailMetricCard,
QFrame#StaffingV2ClassroomsHealthCard,
QFrame#StaffingV2NotificationEditor,
QFrame#StaffingV2NotificationPanel,
QFrame#StaffingV2NotificationValidationPanel,
QFrame#StaffingV2NotificationVariablesPanel,
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
QFrame#StaffingV2ClassroomsHealthCard[staffingV2HealthVariant="success"] {
    background-color: #f0fdf4;
    border: 1px solid #bbf7d0;
}
QFrame#StaffingV2ClassroomsHealthCard[staffingV2HealthVariant="warning"] {
    background-color: #fff7ed;
    border: 1px solid #fed7aa;
}
QFrame#StaffingV2ClassroomsHealthCard[staffingV2HealthVariant="danger"] {
    background-color: #fef2f2;
    border: 1px solid #fecaca;
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
QDialog#StaffingV2AddPositionDialog,
QDialog#StaffingV2AddPersonDialog,
QDialog#StaffingV2AddClassroomDialog,
QDialog#StaffingV2ClassroomsExportDialog,
QDialog#StaffingV2ValidationRulesDialog,
QDialog#StaffingV2ValidationExportDialog,
QDialog#StaffingV2HistoryExportDialog,
QDialog#StaffingV2HistoryExportRecordDialog,
QDialog#StaffingV2DashboardExportDialog {
    background-color: #ffffff;
    color: #0f172a;
}
QFrame#StaffingV2DialogSection,
QFrame#StaffingV2DialogInfo,
QFrame#StaffingV2DialogWarning,
QFrame#StaffingV2AddPositionStatusCard,
QFrame#StaffingV2ManagePermitCard,
QFrame#StaffingV2ManageReplaceCard,
QFrame#StaffingV2HistoryValidationCheckRow,
QFrame#StaffingV2HistoryLifecycleEventRow {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QFrame#StaffingV2HistoryValidationCheckRow,
QFrame#StaffingV2HistoryLifecycleEventRow {
    border: none;
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
QFrame#StaffingV2NeutralChip,
QFrame#StaffingV2HistoryAssignmentIdChip {
    background-color: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 700;
}
QLabel#StaffingV2ChipText {
    background-color: transparent;
    font-weight: 700;
}
QLabel#StaffingV2CardIcon,
QLabel#StaffingV2ChipIcon {
    background-color: transparent;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2NeedNowChip {
    background-color: #fee2e2;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ReplaceChip {
    background-color: #ffedd5;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ComingChip {
    background-color: #fef3c7;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2FilledChip {
    background-color: #dcfce7;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2NeutralChip {
    background-color: #f1f5f9;
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
QFrame#StaffingV2ClassroomsFilterDrawer,
QFrame#StaffingV2PeopleFilterDrawer {
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
QFrame#StaffingV2ClassroomsFilterDrawer,
QFrame#StaffingV2PeopleFilterDrawer {
    border-left: 1px solid #e2e8f0;
}
QTableWidget#StaffingV2HistoryTable {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: #e2e8f0;
    selection-background-color: #eaf2ff;
}
QListWidget#StaffingV2NotificationsRuleList {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 6px;
}
QListWidget#StaffingV2NotificationsRuleList::item {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin: 4px;
    padding: 10px;
}
QListWidget#StaffingV2NotificationsRuleList::item:selected {
    border: 2px solid #2563eb;
    background-color: #eff6ff;
    color: #0f172a;
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
    border: 2px solid #2563eb;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ClassroomListItem[staffingV2StatusFill="need_now"] {
    background-color: #fee2e2;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ClassroomListItem[staffingV2StatusFill="replace"] {
    background-color: #ffedd5;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ClassroomListItem[staffingV2StatusFill="coming"] {
    background-color: #fef3c7;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ClassroomListItem[staffingV2StatusFill="filled"] {
    background-color: #dcfce7;
}
QWidget#PySideStaffingV2Page QFrame#StaffingV2ClassroomListItem[staffingV2StatusFill="dont_need_now"] {
    background-color: #f1f5f9;
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
DirectorReferralDismissalCallback = Callable[[list[StaffingDirectorCandidate], str, str], None]
CandidateReportOpenCallback = Callable[[str, str], None]
NotificationTestPayloadProvider = Callable[[str], list[NotificationTestPayload]]


class _StaffingV2OverlayPanel:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtWidgets: Any,
        parent: Any,
        object_name: str,
        width: int,
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.parent = parent
        self.width = width
        self.frame = QtWidgets.QFrame(parent)
        self.frame.setObjectName(object_name)
        self.frame.setFixedWidth(width)
        self.frame.setMinimumHeight(0)
        self.frame.hide()

        root = QtWidgets.QVBoxLayout(self.frame)
        root.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setObjectName(f"{object_name}Scroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(0)
        self.scroll_area.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Ignored,
        )
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.body = QtWidgets.QWidget()
        self.body.setObjectName(f"{object_name}Body")
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 12, 14, 12)
        self.body_layout.setSpacing(8)
        self.body_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.body)
        root.addWidget(self.scroll_area, 1)

        self.footer = QtWidgets.QWidget()
        self.footer.setObjectName(f"{object_name}Footer")
        self.footer.setMinimumWidth(0)
        self.footer.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.footer_layout = QtWidgets.QVBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(14, 8, 14, 12)
        self.footer_layout.setSpacing(8)
        self.footer_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.footer)

        class ResizeFilter(QtCore.QObject):
            def __init__(self, overlay: "_StaffingV2OverlayPanel") -> None:
                super().__init__(parent)
                self.overlay = overlay

            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
                if watched is self.overlay.parent and event.type() == QtCore.QEvent.Type.Resize:
                    self.overlay.reposition()
                return False

        self._resize_filter = ResizeFilter(self)
        parent.installEventFilter(self._resize_filter)
        self.reposition()

    def clear(self) -> None:
        self._clear_layout(self.body_layout)
        self._clear_layout(self.footer_layout)

    def add_header(
        self,
        *,
        title: str,
        title_object_name: str,
        close_object_name: str,
        close_icon: Any | None = None,
        subtitle: str = "",
        subtitle_object_name: str = "StaffingV2Muted",
    ) -> Any:
        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_label = self.QtWidgets.QLabel(title)
        title_label.setObjectName(title_object_name)
        title_column.addWidget(title_label)
        if subtitle:
            subtitle_label = self.QtWidgets.QLabel(subtitle)
            subtitle_label.setObjectName(subtitle_object_name)
            title_column.addWidget(subtitle_label)
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName(close_object_name)
        if close_icon is not None:
            close.setIcon(close_icon)
            close.setIconSize(self.QtCore.QSize(18, 18))
        close.setFixedSize(32, 32)
        close.clicked.connect(self.hide)
        header.addWidget(close)
        self.body_layout.addLayout(header)
        return close

    def hide(self) -> None:
        self.frame.hide()

    def show_overlay(self) -> None:
        self.reposition()
        self._sync_body_width()
        self.frame.show()
        self.frame.raise_()
        self._sync_body_width()
        self.body.adjustSize()
        configure_v2_scroll_areas(self.QtWidgets, self.frame, self.QtCore)

    def reposition(self) -> None:
        height = max(self.parent.height(), 1)
        width = min(self.width, max(320, self.parent.width()))
        x = max(0, self.parent.width() - width)
        self.frame.setFixedWidth(width)
        self.frame.setMaximumHeight(height)
        self.frame.setGeometry(x, 0, width, height)
        self._sync_body_width()

    def _sync_body_width(self) -> None:
        viewport_width = self.scroll_area.viewport().width()
        if viewport_width > 0:
            self.body.setFixedWidth(viewport_width)

    def _clear_layout(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


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
        notification_store_path: Path | None = None,
        notification_service_factory: Callable[[], NotificationService] | None = None,
        notification_test_payload_provider: NotificationTestPayloadProvider | None = None,
        director_referral_dismissal_callback: DirectorReferralDismissalCallback | None = None,
        candidate_report_open_callback: CandidateReportOpenCallback | None = None,
        director_referral_removal_actor: str = "admin",
        director_referral_removal_source: str = "admin_staffing_dashboard",
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        apply_staffing_v2_light_theme(QtWidgets, QtGui)
        self.store = store
        self.service_factory = service_factory
        self.actions = actions or {}
        self.school_filter = str(school_filter or "").strip()
        self.notification_store_path = (
            Path(notification_store_path) if notification_store_path is not None else Path("notification_rules.sqlite3")
        )
        self.notification_service_factory = notification_service_factory
        self.notification_test_payload_provider = notification_test_payload_provider
        self.director_referral_dismissal_callback = director_referral_dismissal_callback
        self.candidate_report_open_callback = candidate_report_open_callback
        self.director_referral_removal_actor = str(director_referral_removal_actor or "admin").strip() or "admin"
        self.director_referral_removal_source = (
            str(director_referral_removal_source or "admin_staffing_dashboard").strip() or "admin_staffing_dashboard"
        )
        self.rows: list[StaffingMetricRow] = []
        self.visible_rows: list[StaffingMetricRow] = []
        self.classroom_rows: dict[str, list[StaffingMetricRow]] = {}
        self.notification_rules: list[NotificationRule] = []
        self.visible_notification_rules: list[NotificationRule] = []
        self.selected_notification_rule_id: int | None = None
        self.notification_selected_recipients: list[NotificationRecipient] = []
        self.notification_test_payloads: list[NotificationTestPayload] = []
        self.people: list[StaffingPerson] = []
        self.visible_people: list[StaffingPerson] = []
        self.history_records: list[StaffingHistoryRecord] = []
        self.visible_history_records: list[StaffingHistoryRecord] = []
        self.pending_director_candidates: list[StaffingDirectorCandidate] = []
        self.completed_director_interviews: list[StaffingDirectorInterview] = []
        self.classroom_management_rows: dict[str, list[StaffingMetricRow]] = {}
        self.visible_classroom_management: list[tuple[str, list[StaffingMetricRow]]] = []
        self.classrooms_current_page = 1
        self.selected_classroom_management_key = ""
        self.classrooms_applied_filter_state: dict[str, Any] = self._default_classrooms_filter_state()
        self.dashboard_classroom_filter_state: dict[str, Any] = self._default_dashboard_classroom_filter_state()
        self.validation_issues: list[dict[str, str]] = []
        self.visible_validation_issues: list[dict[str, str]] = []
        self._lazy_views_built: set[str] = set()
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName("PySideStaffingV2Page")
        self.widget.setStyleSheet(APP_QSS)
        self._dashboard_scroll_widgets: list[Any] = []
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
        self.notifications_nav_button.clicked.connect(self._show_notifications_view)
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
        self.page_stack.setSizePolicy(
            self.QtWidgets.QSizePolicy.Policy.Ignored,
            self.QtWidgets.QSizePolicy.Policy.Expanding,
        )
        content_layout.addWidget(self.page_stack, 1)
        shell_layout.addWidget(content, 1)

        self.dashboard_view = self.QtWidgets.QWidget()
        self.dashboard_view.setObjectName("StaffingV2Dashboard")
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
        self.export_button.clicked.connect(self._open_dashboard_export_dialog)
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
        list_filter.clicked.connect(self._open_dashboard_classroom_filter_drawer)
        list_filter.setFixedSize(34, 34)
        list_header.addWidget(list_filter)
        classroom_layout.addLayout(list_header)
        self.classroom_list = self.QtWidgets.QListWidget()
        self.classroom_list.setObjectName("StaffingV2ClassroomList")
        self.classroom_list.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.classroom_list.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.classroom_list.setVerticalScrollMode(self.QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.classroom_list.setSizeAdjustPolicy(self.QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.classroom_list.setWordWrap(True)
        self.classroom_list.setSizePolicy(
            self.QtWidgets.QSizePolicy.Policy.Expanding,
            self.QtWidgets.QSizePolicy.Policy.Ignored,
        )
        self.classroom_list.currentRowChanged.connect(self._select_classroom)
        classroom_layout.addWidget(self.classroom_list, 1)
        self.classroom_list_footer = self._label("", "StaffingV2ClassroomListFooter")
        classroom_layout.addWidget(self.classroom_list_footer)
        main.addWidget(self.classroom_panel)

        self.detail_panel, detail_outer_layout = self._panel()
        self.detail_panel.setMinimumWidth(620)
        self.detail_scroll = self.QtWidgets.QScrollArea()
        self.detail_scroll.setObjectName("StaffingV2DashboardDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        self.detail_scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.detail_scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_content = self.QtWidgets.QWidget()
        self.detail_content.setObjectName("StaffingV2DashboardDetailContent")
        detail_layout = self.QtWidgets.QVBoxLayout(self.detail_content)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(14)
        detail_layout.setSizeConstraint(self.QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.detail_scroll.setWidget(self.detail_content)
        detail_outer_layout.addWidget(self.detail_scroll, 1)
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
        self.positions_table.horizontalHeader().setStretchLastSection(False)
        self.positions_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Fixed)
        for column, width in enumerate([44, 130, 130, 170, 112, 112, 205, 210]):
            self.positions_table.horizontalHeader().setSectionResizeMode(column, self.QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.positions_table.setColumnWidth(column, width)
        self.positions_table.cellClicked.connect(self._open_position_drawer_from_table)
        self.positions_table.setMinimumHeight(150)
        self.positions_table.setMaximumHeight(220)
        detail_layout.addWidget(self.positions_table)
        detail_layout.addWidget(self._add_position_drop_zone())
        self.director_interview_panel = self._director_interview_panel()
        detail_layout.addWidget(self.director_interview_panel)
        detail_layout.addStretch(1)
        detail_layout.addWidget(self._status_key())
        self._dashboard_scroll_widgets = [self.detail_scroll, self.classroom_list]
        main.addWidget(self.detail_panel)
        self.drawer_panel = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.dashboard_view,
            object_name="StaffingV2PositionDrawer",
            width=480,
        )
        self.drawer = self.drawer_panel.frame
        self.drawer_layout = self.drawer_panel.body_layout
        self.drawer_footer_layout = self.drawer_panel.footer_layout
        main.setSizes([380, 920])
        dashboard_root.addWidget(main, 1)
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
        if not self._can_leave_notifications_view():
            return
        self._set_active_nav(self.dashboard_nav_button)
        self.page_stack.setCurrentWidget(self.dashboard_view)

    def _show_classrooms_view(self) -> None:
        if not self._can_leave_notifications_view():
            return
        self._ensure_lazy_view("classrooms")
        self._set_active_nav(self.classrooms_nav_button)
        self._refresh_classrooms()
        self.page_stack.setCurrentWidget(self.classrooms_view)

    def _show_validation_view(self) -> None:
        if not self._can_leave_notifications_view():
            return
        self._ensure_lazy_view("validation")
        self._set_active_nav(self.validation_nav_button)
        self._refresh_validation()
        self.page_stack.setCurrentWidget(self.validation_view)

    def _ensure_lazy_view(self, name: str) -> None:
        if name in self._lazy_views_built:
            return
        builders = {
            "classrooms": self._build_classrooms_view,
            "people": self._build_people_view,
            "history": self._build_history_view,
            "notifications": self._build_notifications_view,
            "validation": self._build_validation_view,
        }
        builder = builders.get(name)
        if builder is None:
            return
        builder()
        self._lazy_views_built.add(name)

    def _refresh_built_lazy_views(self) -> None:
        if "classrooms" in self._lazy_views_built:
            self._refresh_classrooms()
        if "people" in self._lazy_views_built:
            self._refresh_people()
        if "history" in self._lazy_views_built:
            self._refresh_history()
        if "notifications" in self._lazy_views_built:
            self._refresh_notifications()
        if "validation" in self._lazy_views_built:
            self._refresh_validation()

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
        export.clicked.connect(self._open_classrooms_export_dialog)
        add_classroom = self.QtWidgets.QPushButton("Add Classroom")
        add_classroom.setObjectName("StaffingV2ClassroomsAddButton")
        self._set_button_icon(add_classroom, "add")
        add_classroom.clicked.connect(self._open_add_classroom_dialog)
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
        self.classrooms_more_filters_button = self.QtWidgets.QPushButton("Filters")
        self.classrooms_more_filters_button.setObjectName("StaffingV2ClassroomsMoreFilters")
        self._set_button_icon(self.classrooms_more_filters_button, "filter")
        self.classrooms_more_filters_button.clicked.connect(self._open_classrooms_filter_drawer)
        clear = self.QtWidgets.QPushButton("Clear")
        clear.setObjectName("StaffingV2ClassroomsClear")
        clear.clicked.connect(self._clear_classrooms_filters)
        filters.addWidget(self.classrooms_more_filters_button)
        filters.addWidget(clear)
        filters_panel_layout.addLayout(filters)
        classrooms_root.addWidget(filters_panel)

        body = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setObjectName("StaffingV2ClassroomsBodySplitter")
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
        self.classrooms_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Interactive)
        for column, width in {
            0: 120,
            1: 110,
            2: 110,
            3: 132,
            4: 118,
            5: 74,
            6: 74,
            7: 136,
            8: 86,
            9: 108,
        }.items():
            self.classrooms_table.setColumnWidth(column, width)
        self.classrooms_table.verticalHeader().setVisible(False)
        self.classrooms_table.setAlternatingRowColors(False)
        self.classrooms_table.currentCellChanged.connect(
            lambda row, _column, _prev_row, _prev_column: self._select_classroom_management(row)
        )
        left_layout.addWidget(self.classrooms_table, 1)
        classrooms_footer = self.QtWidgets.QHBoxLayout()
        self.classrooms_result_count = self.QtWidgets.QLabel("Showing 0 to 0 of 0 classrooms")
        self.classrooms_result_count.setObjectName("StaffingV2ClassroomsResultCount")
        classrooms_footer.addWidget(self.classrooms_result_count)
        classrooms_footer.addStretch(1)
        previous_page = self.QtWidgets.QPushButton("‹")
        previous_page.setObjectName("StaffingV2ClassroomsPreviousPage")
        previous_page.clicked.connect(self._previous_classrooms_page)
        self.classrooms_previous_page = previous_page
        classrooms_footer.addWidget(previous_page)
        current_page = self.QtWidgets.QPushButton("1")
        current_page.setObjectName("StaffingV2ClassroomsCurrentPage")
        current_page.setEnabled(False)
        self.classrooms_current_page_button = current_page
        classrooms_footer.addWidget(current_page)
        next_page = self.QtWidgets.QPushButton("›")
        next_page.setObjectName("StaffingV2ClassroomsNextPage")
        next_page.clicked.connect(self._next_classrooms_page)
        self.classrooms_next_page = next_page
        classrooms_footer.addWidget(next_page)
        self.classrooms_rows_per_page = self.QtWidgets.QComboBox()
        self.classrooms_rows_per_page.setObjectName("StaffingV2ClassroomsRowsPerPage")
        self.classrooms_rows_per_page.addItems(["10 / page", "25 / page", "50 / page"])
        self.classrooms_rows_per_page.currentIndexChanged.connect(self._classrooms_rows_per_page_changed)
        classrooms_footer.addWidget(self.classrooms_rows_per_page)
        left_layout.addLayout(classrooms_footer)
        self.classrooms_validation_panel, self.classrooms_validation_layout = self._panel("StaffingV2ClassroomsValidationPanel")
        left_layout.addWidget(self.classrooms_validation_panel)
        body.addWidget(left)
        self.classrooms_detail_overlay = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.classrooms_view,
            object_name="StaffingV2ClassroomsDetailPanel",
            width=440,
        )
        self.classrooms_detail_panel = self.classrooms_detail_overlay.frame
        self.classrooms_detail_scroll = self.classrooms_detail_overlay.scroll_area
        self.classrooms_detail_scroll.setObjectName("StaffingV2ClassroomsDetailScroll")
        self.classrooms_detail_layout = self.classrooms_detail_overlay.body_layout
        self.classrooms_detail_footer_layout = self.classrooms_detail_overlay.footer_layout
        self.classrooms_detail_overlay.body.setObjectName("StaffingV2ClassroomsDetailContent")
        self.classrooms_detail_overlay.footer.setObjectName("StaffingV2ClassroomsDetailFooter")
        body.setSizes([860])
        classrooms_root.addWidget(body, 1)
        self.classrooms_filter_drawer_panel = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.classrooms_view,
            object_name="StaffingV2ClassroomsFilterDrawer",
            width=460,
        )
        self.classrooms_filter_drawer = self.classrooms_filter_drawer_panel.frame
        self.classrooms_filter_drawer_layout = self.classrooms_filter_drawer_panel.body_layout
        self.classrooms_filter_drawer_footer_layout = self.classrooms_filter_drawer_panel.footer_layout
        self._build_classrooms_filter_drawer()

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
        close.clicked.connect(self._cancel_classrooms_filter_drawer)
        header.addWidget(reset)
        header.addWidget(close)
        self.classrooms_filter_drawer_layout.addLayout(header)
        self.classrooms_filter_school = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterSchool", ["All Schools"]
        )
        self.classrooms_filter_drawer_layout.addLayout(self._labeled_control("School", self.classrooms_filter_school))
        self.classrooms_filter_program = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterProgram", ["All Programs"]
        )
        self.classrooms_filter_drawer_layout.addLayout(self._labeled_control("Program", self.classrooms_filter_program))
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
        ):
            checkbox.setChecked(True)
        self.classrooms_filter_dont_need.setChecked(True)
        for checkbox in (
            self.classrooms_filter_need_now,
            self.classrooms_filter_coming,
            self.classrooms_filter_filled,
            self.classrooms_filter_dont_need,
        ):
            checkbox.setProperty("staffingV2DrawerDraft", True)
            self.classrooms_filter_drawer_layout.addWidget(self._classrooms_status_filter_row(checkbox))
        self.classrooms_filter_drawer_layout.addWidget(self._label("Open Positions", "StaffingV2SectionTitle"))
        self.classrooms_filter_open_positions = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterOpenPositions", ["All", "Has Open Positions", "No Open Positions"]
        )
        self.classrooms_filter_drawer_layout.addWidget(self.classrooms_filter_open_positions)
        self.classrooms_filter_drawer_layout.addWidget(self._label("Days Open", "StaffingV2SectionTitle"))
        self.classrooms_filter_days_open = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterDaysOpen", ["All", "Over 7 Days", "No Open Date", "Custom Range"]
        )
        self.classrooms_filter_drawer_layout.addWidget(self.classrooms_filter_days_open)
        days_range = self.QtWidgets.QHBoxLayout()
        self.classrooms_filter_days_from = self.QtWidgets.QLineEdit()
        self.classrooms_filter_days_from.setObjectName("StaffingV2ClassroomsFilterDaysFrom")
        self.classrooms_filter_days_from.setPlaceholderText("From")
        self.classrooms_filter_days_to = self.QtWidgets.QLineEdit()
        self.classrooms_filter_days_to.setObjectName("StaffingV2ClassroomsFilterDaysTo")
        self.classrooms_filter_days_to.setPlaceholderText("To")
        days_range.addLayout(self._labeled_control("From", self.classrooms_filter_days_from))
        days_range.addLayout(self._labeled_control("To", self.classrooms_filter_days_to))
        self.classrooms_filter_drawer_layout.addLayout(days_range)
        self.classrooms_filter_permit = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterPermit",
            ["All Permit Statuses", "Teacher Permit", "Permit in Process", "Unknown", "No Units Needed", "No Permit"],
        )
        self.classrooms_filter_drawer_layout.addLayout(self._labeled_control("Permit Status", self.classrooms_filter_permit))
        self.classrooms_filter_assigned_staff = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterAssignedStaff", ["All Staff", "Assigned", "Unassigned"]
        )
        self.classrooms_filter_drawer_layout.addLayout(self._labeled_control("Assigned Staff", self.classrooms_filter_assigned_staff))
        self.classrooms_filter_sort_by = self._classrooms_drawer_combo(
            "StaffingV2ClassroomsFilterSortBy",
            ["Default Order", "Days Open (High to Low)", "Classroom (A to Z)", "Open Positions (High to Low)"],
        )
        self.classrooms_filter_drawer_layout.addLayout(self._labeled_control("Sort By", self.classrooms_filter_sort_by))
        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2ClassroomsFilterCancel")
        cancel.clicked.connect(self._cancel_classrooms_filter_drawer)
        self.classrooms_filter_apply_button = self.QtWidgets.QPushButton("Apply Filters")
        self.classrooms_filter_apply_button.setObjectName("StaffingV2ClassroomsFilterApply")
        self._set_button_icon(self.classrooms_filter_apply_button, "filter")
        self.classrooms_filter_apply_button.clicked.connect(self._apply_classrooms_filter_drawer)
        footer.addWidget(cancel)
        footer.addWidget(self.classrooms_filter_apply_button)
        self.classrooms_filter_drawer_footer_layout.addLayout(footer)

    def _classrooms_status_filter_row(self, checkbox: Any) -> Any:
        row = self.QtWidgets.QFrame()
        row.setObjectName("StaffingV2ClassroomsFilterStatusRow")
        layout = self.QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(checkbox)
        dot = self.QtWidgets.QFrame()
        dot.setObjectName("StaffingV2ClassroomStatusDot")
        dot.setProperty("staffingV2Status", _status_from_label(checkbox.text()))
        layout.addWidget(dot)
        layout.addStretch(1)
        return row

    def _classrooms_drawer_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        return combo

    def _default_classrooms_filter_state(self) -> dict[str, Any]:
        return {
            "school": "All Schools",
            "program": "All Programs",
            "need_now": True,
            "coming": True,
            "filled": True,
            "dont_need": True,
            "open_positions": "All",
            "days_open": "All",
            "days_from": "",
            "days_to": "",
            "permit": "All Permit Statuses",
            "assigned_staff": "All Staff",
            "sort_by": "Default Order",
        }

    def _open_classrooms_filter_drawer(self) -> None:
        self._sync_classrooms_filter_drawer_from_state()
        self.classrooms_filter_drawer_panel.show_overlay()

    def _position_classrooms_filter_drawer(self) -> None:
        if not hasattr(self, "classrooms_filter_drawer"):
            return
        self.classrooms_filter_drawer_panel.reposition()

    def _reset_classrooms_filter_drawer(self) -> None:
        state = self._default_classrooms_filter_state()
        self._set_classrooms_filter_drawer_state(state)

    def _cancel_classrooms_filter_drawer(self) -> None:
        self._sync_classrooms_filter_drawer_from_state()
        self.classrooms_filter_drawer.hide()

    def _sync_classrooms_filter_drawer_from_state(self) -> None:
        self._set_classrooms_filter_drawer_state(self.classrooms_applied_filter_state)

    def _set_classrooms_filter_drawer_state(self, state: dict[str, Any]) -> None:
        if not hasattr(self, "classrooms_filter_need_now"):
            return
        self.classrooms_filter_school.setCurrentText(str(state.get("school", "All Schools")))
        self.classrooms_filter_program.setCurrentText(str(state.get("program", "All Programs")))
        for checkbox, key in (
            (self.classrooms_filter_need_now, "need_now"),
            (self.classrooms_filter_coming, "coming"),
            (self.classrooms_filter_filled, "filled"),
        ):
            checkbox.setChecked(bool(state.get(key, True)))
        self.classrooms_filter_dont_need.setChecked(bool(state.get("dont_need", True)))
        self.classrooms_filter_open_positions.setCurrentText(str(state.get("open_positions", "All")))
        self.classrooms_filter_days_open.setCurrentText(str(state.get("days_open", "All")))
        self.classrooms_filter_days_from.setText(str(state.get("days_from", "")))
        self.classrooms_filter_days_to.setText(str(state.get("days_to", "")))
        self.classrooms_filter_permit.setCurrentText(str(state.get("permit", "All Permit Statuses")))
        self.classrooms_filter_assigned_staff.setCurrentText(str(state.get("assigned_staff", "All Staff")))
        self.classrooms_filter_sort_by.setCurrentText(str(state.get("sort_by", "Default Order")))

    def _classrooms_filter_state_from_drawer(self) -> dict[str, Any]:
        return {
            "school": self.classrooms_filter_school.currentText(),
            "program": self.classrooms_filter_program.currentText(),
            "need_now": self.classrooms_filter_need_now.isChecked(),
            "coming": self.classrooms_filter_coming.isChecked(),
            "filled": self.classrooms_filter_filled.isChecked(),
            "dont_need": self.classrooms_filter_dont_need.isChecked(),
            "open_positions": self.classrooms_filter_open_positions.currentText(),
            "days_open": self.classrooms_filter_days_open.currentText(),
            "days_from": self.classrooms_filter_days_from.text().strip(),
            "days_to": self.classrooms_filter_days_to.text().strip(),
            "permit": self.classrooms_filter_permit.currentText(),
            "assigned_staff": self.classrooms_filter_assigned_staff.currentText(),
            "sort_by": self.classrooms_filter_sort_by.currentText(),
        }

    def _apply_classrooms_filter_drawer(self) -> None:
        self.classrooms_applied_filter_state = self._classrooms_filter_state_from_drawer()
        self.classrooms_current_page = 1
        self._refresh_classrooms_filters()
        self.classrooms_filter_drawer.hide()

    def _open_add_classroom_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2AddClassroomDialog")
        dialog.setWindowTitle("Add Classroom")
        dialog.setModal(True)
        dialog.resize(520, 430)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Add Classroom", "StaffingV2DrawerTitle"))
        title_column.addWidget(
            self._label("Create a classroom record before adding staffing positions.", "StaffingV2Muted")
        )
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2AddClassroomClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        form, form_layout = self._dialog_section("StaffingV2DialogSection")
        school = self.QtWidgets.QComboBox()
        school.setObjectName("StaffingV2AddClassroomSchool")
        schools = sorted({row.school for row in self.rows if row.school})
        try:
            schools.extend(classroom.school for classroom in self.store.list_classrooms() if classroom.school)
        except (OSError, ValueError):
            pass
        school.addItems(sorted(set(schools)) or ["Hawthorne"])
        form_layout.addLayout(self._labeled_control("School *", school))
        name = self.QtWidgets.QLineEdit()
        name.setObjectName("StaffingV2AddClassroomName")
        name.setPlaceholderText("Classroom name")
        form_layout.addLayout(self._labeled_control("Classroom Name *", name))
        program = self.QtWidgets.QComboBox()
        program.setObjectName("StaffingV2AddClassroomProgram")
        program.addItems(["Preschool", "Infant", "Toddler", "Pre-K", "Other"])
        form_layout.addLayout(self._labeled_control("Program", program))
        capacity = self.QtWidgets.QLineEdit()
        capacity.setObjectName("StaffingV2AddClassroomCapacity")
        capacity.setPlaceholderText("Licensed capacity")
        form_layout.addLayout(self._labeled_control("Licensed Capacity", capacity))
        status = self._label("", "StaffingV2NeedNowChip")
        status.setObjectName("StaffingV2AddClassroomStatus")
        form_layout.addWidget(status)
        layout.addWidget(form)

        info, info_layout = self._dialog_section("StaffingV2DialogInfo")
        info_layout.addWidget(self._label("What happens on save", "StaffingV2SectionTitle"))
        info_layout.addWidget(self._label("Classroom record is created or updated"))
        info_layout.addWidget(self._label("No staffing position is created"))
        info_layout.addWidget(self._label("Add positions separately from the Staffing Dashboard"))
        layout.addWidget(info)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2SecondaryButton")
        cancel.clicked.connect(dialog.reject)
        save = self.QtWidgets.QPushButton("Add Classroom")
        save.setObjectName("StaffingV2AddClassroomSave")
        self._set_button_icon(save, "add")

        def save_classroom() -> None:
            try:
                capacity_value = None
                if capacity.text().strip():
                    capacity_value = int(capacity.text().strip())
                self.service_factory().add_classroom(
                    school=school.currentText(),
                    name=name.text(),
                    program=program.currentText(),
                    licensed_capacity=capacity_value,
                )
            except ValueError as exc:
                status.setText(str(exc))
                return
            if hasattr(self, "classrooms_filter_dont_need"):
                self.classrooms_filter_dont_need.setChecked(True)
            self.classrooms_applied_filter_state["dont_need"] = True
            self.classrooms_current_page = 1
            self.refresh_all()
            dialog.accept()

        save.clicked.connect(save_classroom)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)
        dialog.show()

    def _open_classrooms_export_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2ClassroomsExportDialog")
        dialog.setWindowTitle("Export Classroom Management")
        dialog.setModal(True)
        dialog.resize(560, 460)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Export Classroom Management", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Preview the currently filtered classroom records.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ClassroomsExportClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        groups = [rows for _key, rows in getattr(self, "visible_classroom_management", []) if rows]
        total_positions = sum(len(rows) for rows in groups)
        open_positions = sum(1 for rows in groups for row in rows if row.status in {"need_now", "replace"})
        capacities = [rows[0].classroom_capacity for rows in groups if rows[0].classroom_capacity is not None]
        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        for label, value in [
            ("Total classrooms", str(len(groups))),
            ("Total positions", str(total_positions)),
            ("Open positions", str(open_positions)),
            ("Avg licensed capacity", f"{(sum(capacities) / len(capacities)):.1f}" if capacities else "0.0"),
            ("School filter", self.classrooms_school_filter.currentText()),
            ("Program filter", self.classrooms_program_filter.currentText()),
            ("Status filter", self.classrooms_status_filter.currentText()),
        ]:
            preview_layout.addLayout(self._detail_row(label, value))
        for rows in groups[:8]:
            first = rows[0]
            filled = sum(1 for row in rows if row.status == "filled")
            open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
            summary = f"{first.school} - {first.classroom_program or '-'} - filled {filled} - open {open_count}"
            preview_layout.addLayout(self._detail_row(first.classroom, summary))
        if len(groups) > 8:
            preview_layout.addLayout(self._detail_row("Additional classrooms", str(len(groups) - 8)))
        layout.addWidget(preview)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.setObjectName("StaffingV2SecondaryButton")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.show()

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
        self.classroom_records_by_key: dict[str, StaffingClassroom] = {}
        for classroom in self.store.list_classrooms():
            if self.school_filter and classroom.school != self.school_filter:
                continue
            key = f"{classroom.school}\u241f{classroom.name}"
            self.classroom_records_by_key[key] = classroom
            grouped.setdefault(key, [])
        self.classroom_management_rows = grouped
        groups = list(grouped.values())
        self._sync_combo(
            self.classrooms_school_filter,
            [
                "All Schools",
                *sorted(
                    {
                        self._classroom_group_info(key, rows)["school"]
                        for key, rows in grouped.items()
                        if self._classroom_group_info(key, rows)["school"]
                    }
                ),
            ],
        )
        self._sync_combo(
            self.classrooms_program_filter,
            [
                "All Programs",
                *sorted(
                    {
                        self._classroom_group_info(key, rows)["program"]
                        for key, rows in grouped.items()
                        if self._classroom_group_info(key, rows)["program"]
                    }
                ),
            ],
        )
        if hasattr(self, "classrooms_filter_school"):
            self._sync_combo(
                self.classrooms_filter_school,
                [self.classrooms_school_filter.itemText(index) for index in range(self.classrooms_school_filter.count())],
            )
            self._sync_combo(
                self.classrooms_filter_program,
                [self.classrooms_program_filter.itemText(index) for index in range(self.classrooms_program_filter.count())],
            )
        self._sync_combo(
            self.classrooms_status_filter,
            ["All Statuses", "Need Now", "Replace", "Coming", "Filled", "Don't Need"],
        )
        self._refresh_classrooms_filters()

    def _clear_classrooms_filters(self) -> None:
        self.classrooms_current_page = 1
        self.classrooms_school_filter.setCurrentText("All Schools")
        self.classrooms_program_filter.setCurrentText("All Programs")
        self.classrooms_status_filter.setCurrentText("All Statuses")
        self.classrooms_search.clear()
        if hasattr(self, "classrooms_filter_need_now"):
            self.classrooms_applied_filter_state = self._default_classrooms_filter_state()
            self._reset_classrooms_filter_drawer()
        self._refresh_classrooms_filters()

    def _refresh_classrooms_filters(self) -> None:
        if not hasattr(self, "classrooms_table"):
            return
        self.classrooms_current_page = 1
        school = self.classrooms_school_filter.currentText()
        program = self.classrooms_program_filter.currentText()
        status = self.classrooms_status_filter.currentText()
        search = self.classrooms_search.text().strip().casefold()
        state = self.classrooms_applied_filter_state
        drawer_school = str(state.get("school", "All Schools"))
        drawer_program = str(state.get("program", "All Programs"))
        allowed_statuses = self._classrooms_allowed_statuses()
        open_positions_filter = str(state.get("open_positions", "All"))
        days_open_filter = str(state.get("days_open", "All"))
        permit_filter = str(state.get("permit", "All Permit Statuses"))
        assigned_staff_filter = str(state.get("assigned_staff", "All Staff"))
        sort_by = str(state.get("sort_by", "Default Order"))
        self.visible_classroom_management = []
        for key, rows in self.classroom_management_rows.items():
            info = self._classroom_group_info(key, rows)
            classroom_status = _classroom_priority_status(rows) if rows else "Don't Need"
            if school != "All Schools" and info["school"] != school:
                continue
            if drawer_school != "All Schools" and info["school"] != drawer_school:
                continue
            if program != "All Programs" and info["program"] != program:
                continue
            if drawer_program != "All Programs" and info["program"] != drawer_program:
                continue
            if status != "All Statuses" and classroom_status != status:
                continue
            if classroom_status not in allowed_statuses:
                continue
            haystack = f"{info['school']} {info['classroom']} {info['program']}".casefold()
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
            if days_open_filter == "Custom Range" and not self._classroom_matches_days_range(rows, state):
                continue
            if permit_filter != "All Permit Statuses" and not any(
                _permit_label(row.permit_status) == permit_filter for row in rows
            ):
                continue
            if assigned_staff_filter == "Assigned" and not any(row.person_name for row in rows):
                continue
            if assigned_staff_filter == "Unassigned" and not any(not row.person_name for row in rows):
                continue
            self.visible_classroom_management.append((key, rows))
        self.visible_classroom_management.sort(
            key=lambda item: self._classrooms_sort_key(item[0], item[1], sort_by)
        )
        self._refresh_classrooms_metrics()
        self._refresh_classrooms_table()
        self._refresh_classrooms_filter_count()
        self._refresh_classrooms_validation_panel()

    def _classrooms_rows_per_page_value(self) -> int:
        label = self.classrooms_rows_per_page.currentText() if hasattr(self, "classrooms_rows_per_page") else "10 / page"
        return _parse_int_or_none(label.split("/", 1)[0].strip()) or 10

    def _classrooms_total_pages(self) -> int:
        total = len(self.visible_classroom_management)
        if total == 0:
            return 1
        rows_per_page = max(1, self._classrooms_rows_per_page_value())
        return ((total - 1) // rows_per_page) + 1

    def _classrooms_page_items(self) -> list[tuple[str, list[StaffingMetricRow]]]:
        total_pages = self._classrooms_total_pages()
        self.classrooms_current_page = max(1, min(self.classrooms_current_page, total_pages))
        rows_per_page = max(1, self._classrooms_rows_per_page_value())
        start = (self.classrooms_current_page - 1) * rows_per_page
        return self.visible_classroom_management[start : start + rows_per_page]

    def _refresh_classrooms_pagination(self) -> None:
        if not hasattr(self, "classrooms_result_count"):
            return
        total = len(self.visible_classroom_management)
        total_pages = self._classrooms_total_pages()
        self.classrooms_current_page = max(1, min(self.classrooms_current_page, total_pages))
        rows_per_page = max(1, self._classrooms_rows_per_page_value())
        if total:
            start = ((self.classrooms_current_page - 1) * rows_per_page) + 1
            end = min(start + rows_per_page - 1, total)
            self.classrooms_result_count.setText(f"Showing {start} to {end} of {total} classrooms")
        else:
            self.classrooms_result_count.setText("Showing 0 to 0 of 0 classrooms")
        self.classrooms_current_page_button.setText(str(self.classrooms_current_page))
        self.classrooms_previous_page.setEnabled(self.classrooms_current_page > 1)
        self.classrooms_next_page.setEnabled(self.classrooms_current_page < total_pages)

    def _classrooms_rows_per_page_changed(self) -> None:
        self.classrooms_current_page = 1
        self._refresh_classrooms_table()

    def _previous_classrooms_page(self) -> None:
        if self.classrooms_current_page <= 1:
            return
        self.classrooms_current_page -= 1
        self._refresh_classrooms_table()

    def _next_classrooms_page(self) -> None:
        if self.classrooms_current_page >= self._classrooms_total_pages():
            return
        self.classrooms_current_page += 1
        self._refresh_classrooms_table()

    def _refresh_classrooms_filter_count(self) -> None:
        state = self.classrooms_applied_filter_state
        status_defaults = {"need_now": True, "coming": True, "filled": True, "dont_need": True}
        active_filter_count = 0
        if any(bool(state.get(key, default)) != default for key, default in status_defaults.items()):
            active_filter_count += 1
        if state.get("school", "All Schools") != "All Schools":
            active_filter_count += 1
        if state.get("program", "All Programs") != "All Programs":
            active_filter_count += 1
        if state.get("open_positions", "All") != "All":
            active_filter_count += 1
        if state.get("days_open", "All") != "All":
            active_filter_count += 1
        if state.get("permit", "All Permit Statuses") != "All Permit Statuses":
            active_filter_count += 1
        if state.get("assigned_staff", "All Staff") != "All Staff":
            active_filter_count += 1
        if state.get("sort_by", "Default Order") != "Default Order":
            active_filter_count += 1
        if hasattr(self, "classrooms_more_filters_button"):
            self.classrooms_more_filters_button.setText(f"Filters {active_filter_count}")
            self.classrooms_more_filters_button.setProperty("staffingV2FilterActiveCount", active_filter_count)
            self.classrooms_more_filters_button.style().unpolish(self.classrooms_more_filters_button)
            self.classrooms_more_filters_button.style().polish(self.classrooms_more_filters_button)
        if hasattr(self, "classrooms_filter_apply_button"):
            self.classrooms_filter_apply_button.setText(f"Apply Filters {active_filter_count}")
            self.classrooms_filter_apply_button.setProperty("staffingV2FilterActiveCount", active_filter_count)
            self.classrooms_filter_apply_button.style().unpolish(self.classrooms_filter_apply_button)
            self.classrooms_filter_apply_button.style().polish(self.classrooms_filter_apply_button)

    def _classrooms_allowed_statuses(self) -> set[str]:
        state = self.classrooms_applied_filter_state
        allowed: set[str] = set()
        if state.get("need_now", True):
            allowed.update({"Need Now", "Replace"})
        if state.get("coming", True):
            allowed.add("Coming")
        if state.get("filled", True):
            allowed.add("Filled")
        if state.get("dont_need", True):
            allowed.add("Don't Need")
        return allowed

    def _classroom_matches_days_range(self, rows: list[StaffingMetricRow], state: dict[str, Any]) -> bool:
        lower = _parse_int_or_none(str(state.get("days_from", "")))
        upper = _parse_int_or_none(str(state.get("days_to", "")))
        days = [row.days_open for row in rows if row.days_open is not None and row.status in {"need_now", "replace"}]
        if not days:
            return False
        return any((lower is None or day >= lower) and (upper is None or day <= upper) for day in days)

    def _classrooms_sort_key(self, key: str, rows: list[StaffingMetricRow], sort_by: str) -> tuple[Any, ...]:
        info = self._classroom_group_info(key, rows)
        if sort_by == "Classroom (A to Z)":
            return (str(info["classroom"]).casefold(), str(info["school"]).casefold())
        if sort_by == "Open Positions (High to Low)":
            open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
            return (-open_count, str(info["classroom"]).casefold())
        if sort_by == "Days Open (High to Low)":
            max_days = max((row.days_open or 0 for row in rows if row.status in {"need_now", "replace"}), default=0)
            return (-max_days, str(info["classroom"]).casefold())
        return (str(info["school"]).casefold(), str(info["classroom"]).casefold())

    def _classroom_group_info(self, key: str, rows: list[StaffingMetricRow]) -> dict[str, Any]:
        if rows:
            first = rows[0]
            return {
                "school": first.school,
                "classroom": first.classroom,
                "program": first.classroom_program,
                "capacity": first.classroom_capacity,
            }
        record = getattr(self, "classroom_records_by_key", {}).get(key)
        if record is None:
            school, _separator, classroom = key.partition("\u241f")
            return {"school": school, "classroom": classroom, "program": "", "capacity": None}
        return {
            "school": record.school,
            "classroom": record.name,
            "program": record.program,
            "capacity": record.licensed_capacity,
        }

    def _refresh_classrooms_metrics(self) -> None:
        while self.classrooms_metrics_layout.count():
            item = self.classrooms_metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setObjectName("StaffingV2ClassroomsMetricCardStale")
                widget.deleteLater()
        groups = list(self.classroom_management_rows.items())
        capacities = [
            info["capacity"]
            for key, rows in groups
            for info in [self._classroom_group_info(key, rows)]
            if info["capacity"] is not None
        ]
        total_positions = sum(len(rows) for _key, rows in groups)
        open_positions = sum(1 for _key, rows in groups for row in rows if row.status in {"need_now", "replace"})
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
        self.classrooms_table.horizontalScrollBar().setValue(0)
        page_items = self._classrooms_page_items()
        for key, rows in page_items:
            info = self._classroom_group_info(key, rows)
            row_index = self.classrooms_table.rowCount()
            self.classrooms_table.insertRow(row_index)
            total = len(rows)
            filled = sum(1 for row in rows if row.status == "filled")
            open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
            values = [
                info["classroom"],
                info["school"],
                info["program"] or "-",
                "" if info["capacity"] is None else str(info["capacity"]),
                str(total),
                str(filled),
                str(open_count),
                _classroom_priority_status(rows) if rows else "Don't Need",
                "Yes",
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, key)
                self.classrooms_table.setItem(row_index, column, item)
            status_value = values[7]
            status_chip = self._table_chip(status_value, _status_from_label(status_value))
            self.classrooms_table.setCellWidget(row_index, 7, status_chip)
            active_chip = self._table_chip("Yes", "healthy")
            self.classrooms_table.setCellWidget(row_index, 8, active_chip)
            view = self.QtWidgets.QPushButton("View")
            view.setObjectName("StaffingV2ClassroomsRowView")
            view.setProperty("classroomKey", key)
            self._set_button_icon(view, "info")
            view.clicked.connect(lambda _checked=False, classroom_key=key: self._select_classroom_management_by_key(classroom_key))
            self.classrooms_table.setCellWidget(row_index, 9, view)
        self._refresh_classrooms_pagination()
        if self.classrooms_table.rowCount():
            self.classrooms_table.setCurrentCell(0, 0)
            self.classrooms_table.horizontalScrollBar().setValue(0)
            self.QtCore.QTimer.singleShot(0, lambda: self.classrooms_table.horizontalScrollBar().setValue(0))
            self._select_classroom_management(0)
        else:
            self._render_classroom_management_detail("", [])

    def _table_chip(self, text: str, status: str) -> Any:
        label = self._label(text, _chip_object_name(status))
        label.setWordWrap(False)
        label.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(28)
        label.setToolTip(text)
        return label

    def _select_classroom_management(self, row_index: int) -> None:
        page_items = self._classrooms_page_items()
        if row_index < 0 or row_index >= len(page_items):
            self._render_classroom_management_detail("", [])
            return
        self._render_classroom_management_detail(page_items[row_index][0], page_items[row_index][1])

    def _select_classroom_management_by_key(self, classroom_key: str) -> None:
        for key, rows in self.visible_classroom_management:
            if key == classroom_key:
                self._render_classroom_management_detail(key, rows)
                return
        self._render_classroom_management_detail("", [])

    def _render_classroom_management_detail(self, key: str, rows: list[StaffingMetricRow]) -> None:
        self._mark_layout_widgets_stale(self.classrooms_detail_layout)
        self._mark_layout_widgets_stale(self.classrooms_detail_footer_layout)
        self.classrooms_detail_overlay.clear()
        if not key:
            self.classrooms_detail_layout.addWidget(self._label("No classroom selected", "StaffingV2Muted"))
            self.classrooms_detail_overlay.hide()
            return
        self.selected_classroom_management_key = key
        info = self._classroom_group_info(key, rows)
        record = getattr(self, "classroom_records_by_key", {}).get(key)
        self.classrooms_detail_overlay.add_header(
            title="Classroom Detail",
            title_object_name="StaffingV2SectionTitle",
            close_object_name="StaffingV2ClassroomsDetailClose",
            close_icon=self._standard_icon("close"),
        )
        self.classrooms_detail_layout.addWidget(self._label(str(info["classroom"]), "StaffingV2ClassroomsDetailName"))
        overview, overview_layout = self._detail_panel_card("StaffingV2ClassroomsDetailCard")
        school = self.QtWidgets.QComboBox()
        school.setObjectName("StaffingV2ClassroomsDetailSchoolEdit")
        school.addItems(sorted({classroom.school for classroom in self.store.list_classrooms() if classroom.school}) or [str(info["school"])])
        school.setCurrentText(str(info["school"]))
        overview_layout.addLayout(self._labeled_control("School", school))
        name = self.QtWidgets.QLineEdit(str(info["classroom"]))
        name.setObjectName("StaffingV2ClassroomsDetailNameEdit")
        overview_layout.addLayout(self._labeled_control("Classroom", name))
        program = self.QtWidgets.QComboBox()
        program.setObjectName("StaffingV2ClassroomsDetailProgramEdit")
        program.setEditable(True)
        program.addItems(["", "Preschool", "Infant", "Toddler", "Pre-K", "Support", "Other"])
        program.setCurrentText(str(info["program"] or ""))
        overview_layout.addLayout(self._labeled_control("Program", program))
        capacity = self.QtWidgets.QLineEdit("" if info["capacity"] is None else str(info["capacity"]))
        capacity.setObjectName("StaffingV2ClassroomsDetailCapacityEdit")
        overview_layout.addLayout(self._labeled_control("Licensed Capacity", capacity))
        display_order = self.QtWidgets.QLineEdit(str(record.display_order if record is not None else 0))
        display_order.setObjectName("StaffingV2ClassroomsDetailDisplayOrderEdit")
        overview_layout.addLayout(self._labeled_control("Display Order", display_order))
        overview_layout.addLayout(self._detail_row("Current Priority", _classroom_priority_status(rows)))
        self.classrooms_detail_layout.addWidget(overview)

        total = len(rows)
        filled = sum(1 for row in rows if row.status == "filled")
        open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
        summary, summary_layout = self._detail_panel_card("StaffingV2ClassroomsDetailCard", "Staffing Summary")
        summary_cards = self.QtWidgets.QHBoxLayout()
        for label, value in [
            ("Total Positions", str(total)),
            ("Filled", str(filled)),
            ("Open", str(open_count)),
            ("Avg Days to Fill", _avg_open_days(rows)),
        ]:
            summary_cards.addWidget(
                self._metric_card(label, value, f"{label} {value}", "StaffingV2ClassroomsDetailMetricCard")
            )
        summary_layout.addLayout(summary_cards)
        self.classrooms_detail_layout.addWidget(summary)

        positions, positions_layout = self._detail_panel_card("StaffingV2ClassroomsDetailCard", "Current Positions")
        for row in rows:
            positions_layout.addWidget(
                self._label(f"{row.position_name}    {_display_status(row.status)}    {row.person_name or 'OPEN POSITION'}")
            )
        self.classrooms_detail_layout.addWidget(positions)

        footer = self.QtWidgets.QHBoxLayout()
        status = self._label("", "StaffingV2Muted")
        status.setObjectName("StaffingV2ClassroomsDetailStatus")
        deactivate = self.QtWidgets.QPushButton("Deactivate Classroom")
        deactivate.setObjectName("StaffingV2ClassroomsDeactivateButton")
        self._set_button_icon(deactivate, "status_need")
        save = self.QtWidgets.QPushButton("Save Changes")
        save.setObjectName("StaffingV2ClassroomsSaveButton")
        self._set_button_icon(save, "status_filled")
        save.setEnabled(record is not None)
        deactivate.setEnabled(record is not None)
        save.clicked.connect(lambda _checked=False: self._save_classroom_detail(record, school, name, program, capacity, display_order, status))
        deactivate.clicked.connect(lambda _checked=False: self._deactivate_selected_classroom(record, status))
        self.classrooms_detail_footer_layout.addWidget(status)
        footer.addWidget(deactivate)
        footer.addStretch(1)
        footer.addWidget(save)
        self.classrooms_detail_footer_layout.addLayout(footer)
        self.classrooms_detail_overlay.show_overlay()

    def _save_classroom_detail(
        self,
        record: StaffingClassroom | None,
        school: Any,
        name: Any,
        program: Any,
        capacity: Any,
        display_order: Any,
        status: Any,
    ) -> None:
        if record is None:
            status.setText("Classroom record not found.")
            return
        try:
            capacity_value = None
            if capacity.text().strip():
                capacity_value = int(capacity.text().strip())
            display_order_value = int(display_order.text().strip() or "0")
            updated = self.service_factory().update_classroom(
                classroom_id=record.id,
                school=school.currentText(),
                name=name.text(),
                program=program.currentText(),
                licensed_capacity=capacity_value,
                display_order=display_order_value,
            )
        except ValueError as exc:
            status.setText(str(exc))
            return
        self.refresh_all()
        self._select_classroom_management_by_key(f"{updated.school}\u241f{updated.name}")

    def _deactivate_selected_classroom(self, record: StaffingClassroom | None, status: Any) -> None:
        if record is None:
            status.setText("Classroom record not found.")
            return
        try:
            self.service_factory().deactivate_classroom(record.id)
        except ValueError as exc:
            status.setText(str(exc))
            return
        self.refresh_all()

    def _refresh_classrooms_validation_panel(self) -> None:
        self._mark_layout_widgets_stale(self.classrooms_validation_layout)
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
            card = self._metric_card(label, str(value), f"{label} {value}", "StaffingV2ClassroomsHealthCard")
            variant = "success" if value == 0 else "danger" if "no positions" in label.casefold() else "warning"
            card.setProperty("staffingV2HealthVariant", variant)
            row.addWidget(card)
        self.classrooms_validation_layout.addLayout(row)

    def _mark_layout_widgets_stale(self, layout: Any) -> None:
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child_layout = item.layout()
            widget = item.widget()
            if widget is not None and widget.objectName() in {
                "StaffingV2ClassroomsHealthCard",
                "StaffingV2ClassroomsMetricCard",
                "StaffingV2ClassroomsDetailMetricCard",
            }:
                widget.setObjectName(f"{widget.objectName()}Stale")
            if child_layout is not None:
                self._mark_layout_widgets_stale(child_layout)

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
        export.clicked.connect(self._open_validation_export_dialog)
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
        self.validation_filters_button = self.QtWidgets.QPushButton("Filters")
        self.validation_filters_button.setObjectName("StaffingV2ValidationFiltersButton")
        self._set_button_icon(self.validation_filters_button, "filter")
        self.validation_filters_button.clicked.connect(self._open_filter_drawer)
        controls.addWidget(self.validation_filters_button)
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
        self.validation_right_overlay = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.validation_view,
            object_name="StaffingV2ValidationRightPanel",
            width=360,
        )
        self.validation_right_panel = self.validation_right_overlay.frame
        self.validation_right_layout = self.validation_right_overlay.body_layout
        body.setSizes([900])
        main_layout.addWidget(body, 1)
        validation_footer = self.QtWidgets.QHBoxLayout()
        self.validation_result_count = self._label("Showing 0 to 0 of 0 issues", "StaffingV2Muted")
        self.validation_result_count.setObjectName("StaffingV2ValidationResultCount")
        validation_footer.addWidget(self.validation_result_count)
        validation_footer.addStretch(1)
        previous_page = self.QtWidgets.QPushButton("‹")
        previous_page.setObjectName("StaffingV2ValidationPreviousPage")
        previous_page.setEnabled(False)
        validation_footer.addWidget(previous_page)
        current_page = self.QtWidgets.QPushButton("1")
        current_page.setObjectName("StaffingV2ValidationCurrentPage")
        current_page.setEnabled(False)
        validation_footer.addWidget(current_page)
        next_page = self.QtWidgets.QPushButton("›")
        next_page.setObjectName("StaffingV2ValidationNextPage")
        next_page.setEnabled(False)
        validation_footer.addWidget(next_page)
        self.validation_rows_per_page = self.QtWidgets.QComboBox()
        self.validation_rows_per_page.setObjectName("StaffingV2ValidationRowsPerPage")
        self.validation_rows_per_page.addItems(["10 / page", "25 / page", "50 / page"])
        self.validation_rows_per_page.setEnabled(False)
        validation_footer.addWidget(self.validation_rows_per_page)
        main_layout.addLayout(validation_footer)

        self.filter_drawer_panel = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.validation_view,
            object_name="StaffingV2FilterDrawer",
            width=340,
        )
        self.filter_drawer = self.filter_drawer_panel.frame
        self.filter_drawer_layout = self.filter_drawer_panel.body_layout
        self.filter_drawer_footer_layout = self.filter_drawer_panel.footer_layout
        self._build_filter_drawer_contents()

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
            ["All Types", "Coverage", "Upcoming", "Compliance", "Lifecycle"],
        )
        self.filter_drawer_layout.addLayout(self._labeled_control("Issue Type", self.validation_issue_type_filter))
        self.validation_detected_date_filter = self._validation_filter_combo(
            "StaffingV2FilterDetectedDate",
            ["Last 30 Days", "Last 7 Days", "Today", "All Dates"],
        )
        self.filter_drawer_layout.addLayout(self._labeled_control("Detected Date", self.validation_detected_date_filter))
        self.filter_drawer_layout.addStretch(1)
        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2FilterCancelButton")
        cancel.clicked.connect(self.filter_drawer.hide)
        self.validation_apply_button = self.QtWidgets.QPushButton("Apply Filters")
        self.validation_apply_button.setObjectName("StaffingV2FilterApplyButton")
        self._set_button_icon(self.validation_apply_button, "filter")
        self.validation_apply_button.clicked.connect(self._apply_validation_filters)
        footer.addWidget(cancel)
        footer.addWidget(self.validation_apply_button)
        self.filter_drawer_footer_layout.addLayout(footer)

    def _validation_filter_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        return combo

    def _open_filter_drawer(self) -> None:
        self.filter_drawer_panel.show_overlay()

    def _reset_validation_filters(self) -> None:
        self.validation_school_filter.setCurrentText("All Schools")
        self.validation_program_filter.setCurrentText("All Programs")
        self.validation_issue_type_filter.setCurrentText("All Types")
        self.validation_detected_date_filter.setCurrentText("Last 30 Days")
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
        active_filter_count = len(severities)
        if school != "All Schools":
            active_filter_count += 1
        if program != "All Programs":
            active_filter_count += 1
        if issue_type != "All Types":
            active_filter_count += 1
        if self.validation_detected_date_filter.currentText() != "Last 30 Days":
            active_filter_count += 1
        if search:
            active_filter_count += 1
        if hasattr(self, "validation_filters_button"):
            self.validation_filters_button.setText(f"Filters {active_filter_count}")
            self.validation_filters_button.setProperty("staffingV2FilterActiveCount", active_filter_count)
            self.validation_filters_button.style().unpolish(self.validation_filters_button)
            self.validation_filters_button.style().polish(self.validation_filters_button)
        if hasattr(self, "validation_apply_button"):
            self.validation_apply_button.setText(f"Apply Filters {active_filter_count}")
            self.validation_apply_button.setProperty("staffingV2FilterActiveCount", active_filter_count)
            self.validation_apply_button.style().unpolish(self.validation_apply_button)
            self.validation_apply_button.style().polish(self.validation_apply_button)
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
        if hasattr(self, "validation_result_count"):
            visible_count = len(self.visible_validation_issues)
            if visible_count:
                self.validation_result_count.setText(f"Showing 1 to {visible_count} of {visible_count} issues")
            else:
                self.validation_result_count.setText("Showing 0 to 0 of 0 issues")
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
            view.clicked.connect(
                lambda _checked=False, item=issue["assignment_id"]: self._show_position_drawer(item)
            )
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
            if object_name == "StaffingV2ValidationRunFullButton":
                button.clicked.connect(self.refresh_all)
            elif object_name == "StaffingV2ValidationExportQuickButton":
                button.clicked.connect(self._open_validation_export_dialog)
            elif object_name == "StaffingV2ValidationRulesButton":
                button.clicked.connect(self._open_validation_rules_dialog)
            actions_layout.addWidget(button)
        self.validation_right_layout.addWidget(actions)
        about, about_layout = self._panel("StaffingV2ValidationSideCard")
        about_layout.addWidget(self._label("About Validation", "StaffingV2SectionTitle"))
        about_layout.addWidget(self._label("Validation checks staffing coverage, permit status, position lifecycle, and start-date requirements."))
        self.validation_right_layout.addWidget(about, 1)
        self.validation_right_overlay.show_overlay()

    def _open_validation_rules_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2ValidationRulesDialog")
        dialog.setWindowTitle("Validation Rules")
        dialog.setModal(True)
        dialog.resize(520, 440)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Validation Rules", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Read-only staffing validation checks used by Staffing v2.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ValidationRulesClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        rules, rules_layout = self._dialog_section("StaffingV2DialogInfo")
        for title, detail in [
            ("Coverage", "Need Now and Replace positions must have open-cycle visibility and be counted as open."),
            ("Permit Status", "Filled and Coming assignments should have a known permit status when a person is assigned."),
            ("Upcoming Start Dates", "Coming assignments require a start date before they can be marked filled."),
            ("Lifecycle Integrity", "Open cycles should map to one valid classroom and avoid duplicate active history records."),
        ]:
            rules_layout.addWidget(self._label(title, "StaffingV2SectionTitle"))
            rules_layout.addWidget(self._label(detail, "StaffingV2Muted"))
        layout.addWidget(rules)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        done = self.QtWidgets.QPushButton("Close")
        done.setObjectName("StaffingV2SecondaryButton")
        done.clicked.connect(dialog.accept)
        footer.addWidget(done)
        layout.addLayout(footer)
        dialog.show()

    def _open_validation_export_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2ValidationExportDialog")
        dialog.setWindowTitle("Export Validation Report")
        dialog.setModal(True)
        dialog.resize(560, 460)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Export Validation Report", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Preview the currently filtered validation report.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ValidationExportClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        issues = list(getattr(self, "visible_validation_issues", []))
        critical = sum(1 for issue in issues if issue["severity"] == "Critical")
        warning = sum(1 for issue in issues if issue["severity"] == "Warning")
        info = sum(1 for issue in issues if issue["severity"] == "Info")
        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        for label, value in [
            ("Total issues", str(len(issues))),
            ("Critical", str(critical)),
            ("Warning", str(warning)),
            ("Info", str(info)),
            ("School filter", self.validation_school_filter.currentText()),
            ("Program filter", self.validation_program_filter.currentText()),
        ]:
            preview_layout.addLayout(self._detail_row(label, value))
        for issue in issues[:8]:
            summary = f"{issue['classroom']} - {issue['severity']} - {issue['details']}"
            preview_layout.addLayout(self._detail_row(issue["issue"], summary))
        if len(issues) > 8:
            preview_layout.addLayout(self._detail_row("Additional issues", str(len(issues) - 8)))
        layout.addWidget(preview)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.setObjectName("StaffingV2SecondaryButton")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.show()

    def _build_notifications_view(self) -> None:
        self.notifications_view = self.QtWidgets.QWidget()
        self.notifications_view.setObjectName("StaffingV2NotificationsDashboard")
        outer = self.QtWidgets.QHBoxLayout(self.notifications_view)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        self.page_stack.addWidget(self.notifications_view)

        left, left_layout = self._panel("StaffingV2NotificationPanel")
        left.setMinimumWidth(320)
        outer.addWidget(left, 1)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Notifications", "StaffingV2NotificationsTitle"))
        title_block.addWidget(
            self._label("Manage staffing notification rule cards, recipients, and email previews.", "StaffingV2Muted")
        )
        header.addLayout(title_block, 1)
        self.notifications_rule_count = self._label("0 rules", "StaffingV2NotificationsRuleCount")
        header.addWidget(self.notifications_rule_count)
        left_layout.addLayout(header)

        filters = self.QtWidgets.QHBoxLayout()
        self.notifications_event_filter = self.QtWidgets.QComboBox()
        self.notifications_event_filter.setObjectName("StaffingV2NotificationsEventFilter")
        self.notifications_event_filter.setMinimumWidth(115)
        self.notifications_event_filter.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_event_filter)
        self.notifications_enabled_filter = self.QtWidgets.QComboBox()
        self.notifications_enabled_filter.setObjectName("StaffingV2NotificationsEnabledFilter")
        self.notifications_enabled_filter.addItems(["All statuses", "Enabled", "Disabled"])
        self.notifications_enabled_filter.setMinimumWidth(115)
        self.notifications_enabled_filter.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_enabled_filter)
        self.notifications_timing_filter = self.QtWidgets.QComboBox()
        self.notifications_timing_filter.setObjectName("StaffingV2NotificationsTimingFilter")
        self.notifications_timing_filter.addItems(["All timings", "Event", "Reference date"])
        self.notifications_timing_filter.setMinimumWidth(115)
        self.notifications_timing_filter.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_timing_filter)
        self.notifications_recipients_filter = self.QtWidgets.QComboBox()
        self.notifications_recipients_filter.setObjectName("StaffingV2NotificationsRecipientsFilter")
        self.notifications_recipients_filter.addItems(["All recipients", "Has recipients", "No recipients"])
        self.notifications_recipients_filter.setMinimumWidth(125)
        self.notifications_recipients_filter.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_recipients_filter)
        self.notifications_template_filter = self.QtWidgets.QComboBox()
        self.notifications_template_filter.setObjectName("StaffingV2NotificationsTemplateFilter")
        self.notifications_template_filter.addItems(["All templates", "Complete templates", "Missing subject", "Missing body"])
        self.notifications_template_filter.setMinimumWidth(125)
        self.notifications_template_filter.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_template_filter)
        self.notifications_sort = self.QtWidgets.QComboBox()
        self.notifications_sort.setObjectName("StaffingV2NotificationsSort")
        self.notifications_sort.addItems(["Event sort", "Recipients sort", "Status sort"])
        self.notifications_sort.setMinimumWidth(110)
        self.notifications_sort.currentIndexChanged.connect(self._refresh_notification_filters)
        filters.addWidget(self.notifications_sort)
        self.notifications_view_toggle = self.QtWidgets.QComboBox()
        self.notifications_view_toggle.setObjectName("StaffingV2NotificationsViewToggle")
        self.notifications_view_toggle.addItems(["List", "Grid"])
        self.notifications_view_toggle.setMinimumWidth(75)
        self.notifications_view_toggle.currentIndexChanged.connect(self._set_notification_view_mode)
        filters.addWidget(self.notifications_view_toggle)
        clear_filters = self.QtWidgets.QPushButton("Clear filters")
        clear_filters.setObjectName("StaffingV2NotificationsClearFilters")
        clear_filters.clicked.connect(self._clear_notification_filters)
        filters.addWidget(clear_filters)
        filters.addStretch(1)
        self.notifications_create_button = self.QtWidgets.QPushButton("Create Rule")
        self.notifications_create_button.setObjectName("StaffingV2NotificationsCreateButton")
        self._set_button_icon(self.notifications_create_button, "add")
        self.notifications_create_button.clicked.connect(self._create_notification_rule)
        filters.addWidget(self.notifications_create_button)
        left_layout.addLayout(filters)

        self.notifications_rule_list = self.QtWidgets.QListWidget()
        self.notifications_rule_list.setObjectName("StaffingV2NotificationsRuleList")
        self.notifications_rule_list.currentRowChanged.connect(self._select_notification_rule_from_list)
        self.notifications_rule_list.itemClicked.connect(self._open_selected_notification_rule)
        class NotificationListResizeFilter(self.QtCore.QObject):
            def __init__(self, dashboard: "StaffingDashboardV2Page") -> None:
                super().__init__(dashboard.notifications_rule_list)
                self.dashboard = dashboard

            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
                if event.type() == self.dashboard.QtCore.QEvent.Type.Resize:
                    self.dashboard.QtCore.QTimer.singleShot(0, self.dashboard._set_notification_view_mode)
                return False

        self.notification_list_resize_filter = NotificationListResizeFilter(self)
        self.notifications_rule_list.viewport().installEventFilter(self.notification_list_resize_filter)
        left_layout.addWidget(self.notifications_rule_list, 1)

        self.notification_editor_overlay = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.notifications_view,
            object_name="StaffingV2NotificationEditor",
            width=680,
        )
        right_layout = self.notification_editor_overlay.body_layout
        class NotificationTestSignals(self.QtCore.QObject):
            finished = self.QtCore.Signal(object)

        self.notification_test_signals = NotificationTestSignals(self.notifications_view)
        self.notification_test_signals.finished.connect(self._finish_notification_test_send)
        close = self.notification_editor_overlay.add_header(
            title="Edit Notification Rule",
            title_object_name="StaffingV2NotificationEditorTitle",
            close_object_name="StaffingV2NotificationEditorClose",
            close_icon=self._standard_icon("close"),
        )
        close.clicked.disconnect()
        close.clicked.connect(self._request_close_notification_editor)
        self.notifications_status = self._label("", "StaffingV2NotificationsStatus")
        right_layout.addWidget(self.notifications_status)

        form = self.QtWidgets.QFormLayout()
        self.notification_rule_label = self.QtWidgets.QLineEdit()
        self.notification_rule_label.setObjectName("StaffingV2NotificationRuleLabel")
        self.notification_rule_event = self.QtWidgets.QComboBox()
        self.notification_rule_event.setObjectName("StaffingV2NotificationRuleEvent")
        self.notification_rule_event.setEditable(True)
        self.notification_rule_enabled = self.QtWidgets.QCheckBox("Enabled")
        self.notification_rule_enabled.setObjectName("StaffingV2NotificationEnabled")
        self.notification_rule_timing = self.QtWidgets.QComboBox()
        self.notification_rule_timing.setObjectName("StaffingV2NotificationTiming")
        self.notification_rule_timing.addItems(["Event", "Reference date"])
        self.notification_rule_date_field = self.QtWidgets.QComboBox()
        self.notification_rule_date_field.setObjectName("StaffingV2NotificationDateField")
        self.notification_rule_date_field.setEditable(True)
        self.notification_rule_date_field.addItems(
            ["start_date", "reply_by_date", "interview_date", "generated_date", "date_notice_given", "final_working_day"]
        )
        self.notification_rule_offset_direction = self.QtWidgets.QComboBox()
        self.notification_rule_offset_direction.setObjectName("StaffingV2NotificationOffsetDirection")
        self.notification_rule_offset_direction.addItems(["Before", "On", "After"])
        self.notification_rule_offset = self.QtWidgets.QSpinBox()
        self.notification_rule_offset.setObjectName("StaffingV2NotificationOffsetDays")
        self.notification_rule_offset.setRange(0, 365)
        self.notification_rule_recipients = self.QtWidgets.QLineEdit()
        self.notification_rule_recipients.setObjectName("StaffingV2NotificationRecipients")
        self.notification_rule_recipients.setPlaceholderText("Custom Name <name@example.com>")
        self.notification_rule_subject = self.QtWidgets.QLineEdit()
        self.notification_rule_subject.setObjectName("StaffingV2NotificationSubject")
        self.notification_rule_body = self.QtWidgets.QPlainTextEdit()
        self.notification_rule_body.setObjectName("StaffingV2NotificationBody")
        self.notification_rule_body.setMinimumHeight(150)
        self._notification_variable_target = self.notification_rule_body

        class NotificationEditorFocusFilter(self.QtCore.QObject):
            def __init__(filter_self, owner: "StaffingDashboardV2Page") -> None:
                super().__init__(owner.widget)
                filter_self.owner = owner

            def eventFilter(filter_self, watched: Any, event: Any) -> bool:  # noqa: N802
                if event.type() == filter_self.owner.QtCore.QEvent.Type.FocusIn:
                    filter_self.owner._notification_variable_target = watched
                return False

        self._notification_editor_focus_filter = NotificationEditorFocusFilter(self)
        self.notification_rule_subject.installEventFilter(self._notification_editor_focus_filter)
        self.notification_rule_body.installEventFilter(self._notification_editor_focus_filter)
        self.notification_rule_label.textChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_event.currentTextChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_enabled.toggled.connect(self._sync_notification_rule_validation)
        self.notification_rule_timing.currentTextChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_date_field.currentTextChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_offset_direction.currentTextChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_offset.valueChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_subject.textChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_body.textChanged.connect(self._sync_notification_rule_validation)
        self.notification_rule_subject.cursorPositionChanged.connect(
            lambda _old, _new: setattr(self, "_notification_variable_target", self.notification_rule_subject)
        )
        self.notification_rule_body.cursorPositionChanged.connect(
            lambda: setattr(self, "_notification_variable_target", self.notification_rule_body)
        )
        form.addRow("Label", self.notification_rule_label)
        form.addRow("Event", self.notification_rule_event)
        form.addRow("Enabled", self.notification_rule_enabled)
        form.addRow("Timing", self.notification_rule_timing)
        form.addRow("Date field", self.notification_rule_date_field)
        form.addRow("Send timing", self.notification_rule_offset_direction)
        form.addRow("Offset days", self.notification_rule_offset)
        recipient_row = self.QtWidgets.QHBoxLayout()
        recipient_row.addWidget(self.notification_rule_recipients, 1)
        self.notification_recipient_add = self.QtWidgets.QPushButton("Add")
        self.notification_recipient_add.setObjectName("StaffingV2NotificationRecipientAdd")
        self.notification_recipient_add.clicked.connect(self._add_notification_recipient_from_text)
        recipient_row.addWidget(self.notification_recipient_add)
        self.notification_recipient_picker = self.QtWidgets.QComboBox()
        self.notification_recipient_picker.setObjectName("StaffingV2NotificationRecipientPicker")
        self.notification_recipient_picker.addItems(
            ["Add role recipient…", "Candidate", "Hiring Manager", "Director", "Executive Director"]
        )
        self.notification_recipient_picker.currentIndexChanged.connect(self._add_notification_recipient_from_picker)
        recipient_row.addWidget(self.notification_recipient_picker)
        self.notification_recipient_candidate = self.QtWidgets.QPushButton("Candidate")
        self.notification_recipient_candidate.setObjectName("StaffingV2NotificationRecipientCandidate")
        self.notification_recipient_candidate.clicked.connect(lambda _checked=False: self._add_notification_role_recipient("candidate"))
        self.notification_recipient_candidate.hide()
        self.notification_recipient_hiring_manager = self.QtWidgets.QPushButton("Hiring Manager")
        self.notification_recipient_hiring_manager.setObjectName("StaffingV2NotificationRecipientHiringManager")
        self.notification_recipient_hiring_manager.clicked.connect(
            lambda _checked=False: self._add_notification_role_recipient("hiring_manager")
        )
        self.notification_recipient_hiring_manager.hide()
        self.notification_recipient_director = self.QtWidgets.QPushButton("Director")
        self.notification_recipient_director.setObjectName("StaffingV2NotificationRecipientDirector")
        self.notification_recipient_director.clicked.connect(lambda _checked=False: self._add_notification_role_recipient("director"))
        self.notification_recipient_director.hide()
        self.notification_recipient_executive_director = self.QtWidgets.QPushButton("Executive Director")
        self.notification_recipient_executive_director.setObjectName("StaffingV2NotificationRecipientExecutiveDirector")
        self.notification_recipient_executive_director.clicked.connect(
            lambda _checked=False: self._add_notification_role_recipient("executive_director")
        )
        self.notification_recipient_executive_director.hide()
        form.addRow("Recipients", recipient_row)
        form.addRow("Subject", self.notification_rule_subject)
        form.addRow("Body", self.notification_rule_body)
        right_layout.addLayout(form)
        self.notification_recipient_chips, self.notification_recipient_chips_layout = self._panel("StaffingV2NotificationRecipientChips")
        self.notification_recipient_chips_layout.addWidget(self._label("Recipients", "StaffingV2SectionTitle"))
        right_layout.addWidget(self.notification_recipient_chips)

        subject_tools = self.QtWidgets.QHBoxLayout()
        subject_tools.addWidget(self._label("Subject variables", "StaffingV2Muted"))
        for variable in ("position_name", "person_name", "school", "company_name"):
            button = self.QtWidgets.QPushButton(f"{{{variable}}}")
            button.setObjectName(f"StaffingV2NotificationSubjectVariable_{variable}")
            button.clicked.connect(lambda _checked=False, token=f"{{{variable}}}": self._insert_notification_subject_variable(token))
            subject_tools.addWidget(button)
        subject_tools.addStretch(1)
        right_layout.addLayout(subject_tools)

        body_tools = self.QtWidgets.QHBoxLayout()
        for label, object_name, snippet in (
            ("Bold", "StaffingV2NotificationBodyBold", "**bold text**"),
            ("Italic", "StaffingV2NotificationBodyItalic", "_italic text_"),
            ("Bullets", "StaffingV2NotificationBodyBullets", "\n- list item"),
            ("Link", "StaffingV2NotificationBodyLink", "[link text](https://example.com)"),
            ("Code", "StaffingV2NotificationBodyCode", "`code`"),
            ("Variables", "StaffingV2NotificationBodyVariables", "{position_name}"),
        ):
            button = self.QtWidgets.QPushButton(label)
            button.setObjectName(object_name)
            button.clicked.connect(lambda _checked=False, text=snippet: self._insert_notification_body_text(text))
            body_tools.addWidget(button)
        body_tools.addStretch(1)
        right_layout.addLayout(body_tools)

        self.notification_variables_panel, variables_layout = self._panel("StaffingV2NotificationVariablesPanel")
        variables_layout.addWidget(self._label("Variables Preview", "StaffingV2SectionTitle"))
        self.notification_variables_preview = self._label("", "StaffingV2NotificationVariablesPreview")
        variables_layout.addWidget(self.notification_variables_preview)
        variable_buttons = self.QtWidgets.QGridLayout()
        row = 0
        current_group = ""
        column = 0
        for field in NOTIFICATION_TEMPLATE_FIELD_CATALOG:
            if field.group != current_group:
                if current_group and column:
                    row += 1
                current_group = field.group
                column = 0
                group_label = self._label(current_group, "StaffingV2Muted")
                variable_buttons.addWidget(group_label, row, 0, 1, 3)
                row += 1
            button = self.QtWidgets.QPushButton(f"{{{field.key}}}")
            button.setToolTip(field.label)
            button.setObjectName(f"StaffingV2NotificationVariable_{_safe_object_suffix(field.key)}")
            button.setMinimumWidth(120)
            button.clicked.connect(lambda _checked=False, token=f"{{{field.key}}}": self._insert_notification_variable(token))
            variable_buttons.addWidget(button, row, column)
            column += 1
            if column >= 3:
                column = 0
                row += 1
        variables_layout.addLayout(variable_buttons)
        right_layout.addWidget(self.notification_variables_panel)
        self.notification_validation_panel, validation_layout = self._panel("StaffingV2NotificationValidationPanel")
        validation_layout.addWidget(self._label("Validation", "StaffingV2SectionTitle"))
        self.notification_validation = self._label("", "StaffingV2NotificationValidation")
        validation_layout.addWidget(self.notification_validation)
        right_layout.addWidget(self.notification_validation_panel)
        self.notification_delivery_toggle = self.QtWidgets.QPushButton("Delivery & Testing  ▸")
        self.notification_delivery_toggle.setObjectName("StaffingV2NotificationDeliveryToggle")
        self.notification_delivery_toggle.setCheckable(True)
        right_layout.addWidget(self.notification_delivery_toggle)
        self.notification_audit_panel, audit_layout = self._panel("StaffingV2NotificationAuditPanel")
        self.notification_test_recipient = self.QtWidgets.QLineEdit()
        self.notification_test_recipient.setObjectName("StaffingV2NotificationTestRecipient")
        self.notification_test_recipient.setPlaceholderText("Explicit test recipient email")
        audit_layout.addWidget(self._label("Test recipient", "StaffingV2Muted"))
        audit_layout.addWidget(self.notification_test_recipient)
        self.notification_test_payload_selector = self.QtWidgets.QComboBox()
        self.notification_test_payload_selector.setObjectName("StaffingV2NotificationTestPayload")
        audit_layout.addWidget(self._label("Test payload", "StaffingV2Muted"))
        audit_layout.addWidget(self.notification_test_payload_selector)
        audit_layout.addWidget(self._label("Recent Sends", "StaffingV2SectionTitle"))
        self.notification_audit_summary = self._label("", "StaffingV2NotificationAuditSummary")
        audit_layout.addWidget(self.notification_audit_summary)
        right_layout.addWidget(self.notification_audit_panel)
        self.notification_audit_panel.setVisible(False)
        self.notification_delivery_toggle.toggled.connect(self._toggle_notification_delivery_panel)

        actions = self.QtWidgets.QHBoxLayout()
        self.notification_delete = self.QtWidgets.QPushButton("Delete Rule")
        self.notification_delete.setObjectName("StaffingV2NotificationDelete")
        self.notification_delete.clicked.connect(self._delete_notification_rule)
        actions.addWidget(self.notification_delete)
        actions.addStretch(1)
        self.notification_preview = self.QtWidgets.QPushButton("Preview")
        self.notification_preview.setObjectName("StaffingV2NotificationPreview")
        self.notification_preview.clicked.connect(self._open_notification_preview_dialog)
        actions.addWidget(self.notification_preview)
        self.notification_test_send = self.QtWidgets.QPushButton("Send Test")
        self.notification_test_send.setObjectName("StaffingV2NotificationSendTest")
        self.notification_test_send.clicked.connect(self._send_notification_test)
        actions.addWidget(self.notification_test_send)
        self.notification_cancel = self.QtWidgets.QPushButton("Cancel")
        self.notification_cancel.setObjectName("StaffingV2NotificationCancel")
        self.notification_cancel.clicked.connect(self._request_close_notification_editor)
        actions.addWidget(self.notification_cancel)
        self.notification_save = self.QtWidgets.QPushButton("Save Changes")
        self.notification_save.setObjectName("StaffingV2NotificationSave")
        self._set_button_icon(self.notification_save, "export")
        self.notification_save.clicked.connect(self._save_notification_rule)
        actions.addWidget(self.notification_save)
        self.notification_editor_overlay.footer_layout.addLayout(actions)

    def _toggle_notification_delivery_panel(self, expanded: bool) -> None:
        self.notification_audit_panel.setVisible(expanded)
        self.notification_delivery_toggle.setText(f"Delivery & Testing  {'▾' if expanded else '▸'}")

    def _refresh_notifications(self) -> None:
        if not hasattr(self, "notifications_rule_list"):
            return
        store = self._notification_store()
        if not getattr(self, "_notification_defaults_checked", False):
            self._notification_defaults_checked = True
            if not store.list_rules():
                store.ensure_default_rules()
        self.notification_rules = [
            rule for rule in store.list_rules() if _show_rule_in_staffing_v2_notifications(rule)
        ]
        self._sync_notification_filter_choices()
        self._refresh_notification_filters()

    def _sync_notification_filter_choices(self) -> None:
        current_event = self.notifications_event_filter.currentText() or "All events"
        self.notifications_event_filter.blockSignals(True)
        self.notifications_event_filter.clear()
        self.notifications_event_filter.addItem("All events")
        for event_type in sorted({rule.event_type for rule in self.notification_rules if rule.event_type}):
            self.notifications_event_filter.addItem(event_type)
        index = self.notifications_event_filter.findText(current_event)
        self.notifications_event_filter.setCurrentIndex(index if index >= 0 else 0)
        self.notifications_event_filter.blockSignals(False)

        current_rule_event = self.notification_rule_event.currentText()
        self.notification_rule_event.blockSignals(True)
        self.notification_rule_event.clear()
        for event_type in [
            "staffing.assignment.need_now",
            "staffing.assignment.coming",
            "staffing.assignment.filled",
            "staffing.assignment.replace",
            "staffing.assignment.not_needed",
            "staffing.permit.updated",
        ]:
            self.notification_rule_event.addItem(event_type)
        for event_type in sorted({rule.event_type for rule in self.notification_rules if rule.event_type}):
            if self.notification_rule_event.findText(event_type) < 0:
                self.notification_rule_event.addItem(event_type)
        if current_rule_event:
            self.notification_rule_event.setCurrentText(current_rule_event)
        self.notification_rule_event.blockSignals(False)

    def _refresh_notification_filters(self) -> None:
        selected_event = self.notifications_event_filter.currentText()
        selected_status = self.notifications_enabled_filter.currentText()
        selected_timing = self.notifications_timing_filter.currentText()
        selected_recipients = self.notifications_recipients_filter.currentText()
        selected_template = self.notifications_template_filter.currentText()
        self.visible_notification_rules = []
        for rule in self.notification_rules:
            active_recipients = [recipient for recipient in rule.recipients if recipient.active]
            if selected_event != "All events" and rule.event_type != selected_event:
                continue
            if selected_status == "Enabled" and not rule.active:
                continue
            if selected_status == "Disabled" and rule.active:
                continue
            if selected_timing == "Event" and rule.trigger_timing != "event":
                continue
            if selected_timing == "Reference date" and rule.trigger_timing != "date_offset":
                continue
            if selected_recipients == "Has recipients" and not active_recipients:
                continue
            if selected_recipients == "No recipients" and active_recipients:
                continue
            if selected_template == "Complete templates" and (not rule.subject_template or not rule.body_template):
                continue
            if selected_template == "Missing subject" and rule.subject_template:
                continue
            if selected_template == "Missing body" and rule.body_template:
                continue
            self.visible_notification_rules.append(rule)
        sort_text = self.notifications_sort.currentText()
        if sort_text == "Recipients sort":
            self.visible_notification_rules.sort(key=lambda item: (-len([r for r in item.recipients if r.active]), item.event_type))
        elif sort_text == "Status sort":
            self.visible_notification_rules.sort(key=lambda item: (not item.active, item.event_type, item.label))
        else:
            self.visible_notification_rules.sort(key=lambda item: (item.event_type, item.label))
        self._refresh_notification_rule_list()

    def _refresh_notification_rule_list(self) -> None:
        self.notifications_rule_list.blockSignals(True)
        self.notifications_rule_list.clear()
        for rule in self.visible_notification_rules:
            status = "Enabled" if rule.active else "Disabled"
            timing = "Reference date" if rule.trigger_timing == "date_offset" else "Event"
            recipient_count = sum(1 for recipient in rule.recipients if recipient.active)
            template_status = _notification_validation_text(rule)
            item = self.QtWidgets.QListWidgetItem(
                f"{rule.label}\n{rule.event_type}\n{status} · {timing} · Recipients {recipient_count}\n"
                f"Subject: {rule.subject_template or 'Missing subject template'}\n"
                f"Body preview: {(rule.body_template or 'Missing body template')[:140]}\n{template_status}"
            )
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, rule.id)
            item.setSizeHint(self.QtCore.QSize(440, 150))
            self.notifications_rule_list.addItem(item)
            self.notifications_rule_list.setItemWidget(item, self._notification_rule_card_widget(rule))
        self.notifications_rule_list.blockSignals(False)
        issue_count = sum(bool(validate_notification_rule(rule)) for rule in self.visible_notification_rules)
        self.notifications_rule_count.setText(
            f"{len(self.visible_notification_rules)} rules" + (f" · {issue_count} issues" if issue_count else "")
        )
        selected_index = 0
        if self.selected_notification_rule_id is not None:
            for index, rule in enumerate(self.visible_notification_rules):
                if rule.id == self.selected_notification_rule_id:
                    selected_index = index
                    break
        if self.visible_notification_rules:
            self.notifications_rule_list.setCurrentRow(selected_index)
            self._load_notification_rule(self.visible_notification_rules[selected_index])
        else:
            self._load_notification_rule(None)
        self._set_notification_view_mode()

    def _notification_rule_card_widget(self, rule: NotificationRule) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("StaffingV2NotificationRuleCard")
        layout = self.QtWidgets.QGridLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(4)
        event = self._label(rule.event_type or "Missing event", "StaffingV2NotificationCardEvent")
        event.setWordWrap(True)
        layout.addWidget(event, 0, 0)
        layout.addWidget(self._label(f"ID: {rule.id if rule.id is not None else 'new'}", "StaffingV2Muted"), 1, 0)
        label = self._label(rule.label or "Untitled notification", "StaffingV2SectionTitle")
        label.setWordWrap(True)
        layout.addWidget(label, 0, 1)
        status = "Enabled" if rule.active else "Disabled"
        status_label = self._label(status, "StaffingV2HealthyChip" if rule.active else "StaffingV2NeutralChip")
        layout.addWidget(status_label, 0, 2)
        timing = "Event" if rule.trigger_timing == "event" else _notification_schedule_text(rule)
        recipients = sum(1 for recipient in rule.recipients if recipient.active)
        layout.addWidget(self._label(f"Timing\n{timing}", "StaffingV2Muted"), 1, 1)
        layout.addWidget(self._label(f"Recipients\n{recipients}", "StaffingV2Muted"), 1, 2)
        subject = self._label(f"Subject\n{rule.subject_template or 'Missing subject template'}", "StaffingV2Muted")
        subject.setWordWrap(True)
        layout.addWidget(subject, 0, 3, 2, 1)
        body = self._label(f"Body preview\n{(rule.body_template or 'Missing body template')[:120]}", "StaffingV2Muted")
        body.setWordWrap(True)
        layout.addWidget(body, 0, 4, 2, 1)
        issues = validate_notification_rule(rule)
        if issues:
            blocking = any(issue.blocking for issue in issues)
            issue_label = self._label(
                f"{'Blocked' if blocking else 'Warning'}: {issues[0].message}",
                "StaffingV2NeedNowChip" if blocking else "StaffingV2ComingChip",
            )
            issue_label.setWordWrap(True)
            layout.addWidget(issue_label, 2, 0, 1, 5)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(3, 3)
        layout.setColumnStretch(4, 3)
        return card

    def _select_notification_rule_from_list(self, row: int) -> None:
        if row < 0 or row >= len(self.visible_notification_rules):
            return
        self._load_notification_rule(self.visible_notification_rules[row])

    def _open_selected_notification_rule(self, item: Any) -> None:
        if not self._confirm_notification_editor_switch():
            return
        row = self.notifications_rule_list.row(item)
        if row < 0 or row >= len(self.visible_notification_rules):
            return
        self._load_notification_rule(self.visible_notification_rules[row])
        self.notification_editor_overlay.show_overlay()

    def _load_notification_rule(self, rule: NotificationRule | None) -> None:
        if rule is None:
            self.selected_notification_rule_id = None
            self.notification_rule_label.clear()
            self.notification_rule_event.setCurrentText("staffing.assignment.need_now")
            self.notification_rule_enabled.setChecked(False)
            self.notification_rule_timing.setCurrentText("Event")
            self.notification_rule_date_field.setCurrentText("")
            self.notification_rule_offset_direction.setCurrentText("On")
            self.notification_rule_offset.setValue(0)
            self.notification_rule_recipients.clear()
            self.notification_rule_subject.clear()
            self.notification_rule_body.clear()
            self.notification_selected_recipients = []
            self._render_notification_recipient_chips()
            self.notification_validation.setText("No rule selected.")
            self.notification_variables_preview.setText("")
            self.notification_audit_summary.setText("")
            self.notification_editor_baseline = self._notification_rule_from_editor()
            return
        self.selected_notification_rule_id = rule.id
        self.notification_rule_label.setText(rule.label)
        self.notification_rule_event.setCurrentText(rule.event_type)
        self.notification_rule_enabled.setChecked(rule.active)
        self.notification_rule_timing.setCurrentText("Reference date" if rule.trigger_timing == "date_offset" else "Event")
        self.notification_rule_date_field.setCurrentText(rule.date_field)
        self.notification_rule_offset_direction.setCurrentText(
            "Before" if int(rule.offset_days) < 0 else "After" if int(rule.offset_days) > 0 else "On"
        )
        self.notification_rule_offset.setValue(abs(int(rule.offset_days)))
        self.notification_selected_recipients = [recipient for recipient in rule.recipients if recipient.active]
        self.notification_rule_recipients.setText("")
        self._render_notification_recipient_chips()
        self.notification_rule_subject.setText(rule.subject_template)
        self.notification_rule_body.setPlainText(rule.body_template)
        self._sync_notification_rule_validation()
        self._refresh_notification_audit()
        self._refresh_notification_test_payloads(rule.event_type)
        self.notification_editor_baseline = self._notification_rule_from_editor()

    def _create_notification_rule(self) -> None:
        if not self._confirm_notification_editor_switch():
            return
        self.selected_notification_rule_id = None
        self._load_notification_rule(
            NotificationRule(
                event_type="",
                label="",
                subject_template="",
                body_template="",
                recipients=[],
                active=False,
                id=None,
            )
        )
        self.notifications_status.setText("New notification rule ready.")
        self._render_notification_recipient_chips()
        self.notification_editor_baseline = self._notification_rule_from_editor()
        self.notification_editor_overlay.show_overlay()

    def _save_notification_rule(self) -> None:
        timing = "date_offset" if self.notification_rule_timing.currentText() == "Reference date" else "event"
        offset_days = self.notification_rule_offset.value()
        if self.notification_rule_offset_direction.currentText() == "Before":
            offset_days *= -1
        elif self.notification_rule_offset_direction.currentText() == "On":
            offset_days = 0
        rule = NotificationRule(
            id=self.selected_notification_rule_id,
            event_type=self.notification_rule_event.currentText(),
            label=self.notification_rule_label.text(),
            active=self.notification_rule_enabled.isChecked(),
            trigger_timing=timing,
            date_field=self.notification_rule_date_field.currentText(),
            offset_days=offset_days,
            subject_template=self.notification_rule_subject.text(),
            body_template=self.notification_rule_body.toPlainText(),
            recipients=self._current_notification_recipients(),
        )
        try:
            saved = self._notification_store().save_rule(rule)
        except Exception as exc:  # noqa: BLE001 - surface existing store validation to operator.
            self.notification_validation.setText(_safe_notification_error(exc))
            self.notifications_status.setText("Notification rule was not saved.")
            return
        self.selected_notification_rule_id = saved.id
        self.notifications_status.setText("Notification rule saved.")
        self._refresh_notifications()
        self.notification_editor_overlay.hide()

    def _delete_notification_rule(self) -> None:
        if self.selected_notification_rule_id is None:
            return
        label = self.notification_rule_label.text().strip() or self.notification_rule_event.currentText().strip()
        answer = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Delete Notification Rule",
            f"Delete notification rule '{label}'? This cannot be undone.",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._notification_store().delete_rule(self.selected_notification_rule_id)
        self.selected_notification_rule_id = None
        self.notifications_status.setText("Notification rule deleted.")
        self._refresh_notifications()
        self.notification_editor_overlay.hide()

    def _clear_notification_filters(self) -> None:
        self.notifications_event_filter.setCurrentText("All events")
        self.notifications_enabled_filter.setCurrentText("All statuses")
        self.notifications_timing_filter.setCurrentText("All timings")
        self.notifications_recipients_filter.setCurrentText("All recipients")
        self.notifications_template_filter.setCurrentText("All templates")
        self.notifications_sort.setCurrentText("Event sort")
        self._refresh_notification_filters()

    def _set_notification_view_mode(self) -> None:
        mode = self.notifications_view_toggle.currentText().casefold()
        self.notifications_rule_list.setProperty("staffingV2NotificationViewMode", mode)
        if mode == "grid":
            self.notifications_rule_list.setViewMode(self.QtWidgets.QListView.ViewMode.IconMode)
            self.notifications_rule_list.setResizeMode(self.QtWidgets.QListView.ResizeMode.Adjust)
            self.notifications_rule_list.setWrapping(True)
            viewport_width = self.notifications_rule_list.viewport().width()
            width = max(420, (viewport_width - 28) // 2) if viewport_width >= 868 else max(300, viewport_width - 14)
            grid_size = self.QtCore.QSize(width, 205)
            if self.notifications_rule_list.gridSize() != grid_size:
                self.notifications_rule_list.setGridSize(grid_size)
        else:
            self.notifications_rule_list.setViewMode(self.QtWidgets.QListView.ViewMode.ListMode)
            self.notifications_rule_list.setWrapping(False)
            self.notifications_rule_list.setGridSize(self.QtCore.QSize())
        self.notifications_rule_list.style().unpolish(self.notifications_rule_list)
        self.notifications_rule_list.style().polish(self.notifications_rule_list)

    def _current_notification_recipients(self) -> list[NotificationRecipient]:
        typed = _parse_notification_recipients(self.notification_rule_recipients.text())
        merged = [*self.notification_selected_recipients, *typed]
        deduped: dict[str, NotificationRecipient] = {}
        for recipient in merged:
            key = _notification_recipient_key(recipient)
            if key and key not in deduped:
                deduped[key] = recipient
        return list(deduped.values())

    def _add_notification_recipient_from_text(self) -> None:
        recipients = _parse_notification_recipients(self.notification_rule_recipients.text())
        if not recipients:
            return
        existing = {_notification_recipient_key(recipient) for recipient in self.notification_selected_recipients}
        self.notification_selected_recipients.extend(
            [recipient for recipient in recipients if _notification_recipient_key(recipient) not in existing]
        )
        self.notification_rule_recipients.clear()
        self._render_notification_recipient_chips()
        self._sync_notification_rule_validation()

    def _add_notification_role_recipient(self, role_key: str) -> None:
        recipient = _notification_role_recipient(role_key)
        existing = {_notification_recipient_key(item) for item in self.notification_selected_recipients}
        key = _notification_recipient_key(recipient)
        if key not in existing:
            self.notification_selected_recipients.append(recipient)
        self._render_notification_recipient_chips()
        self._sync_notification_rule_validation()

    def _add_notification_recipient_from_picker(self, index: int) -> None:
        role_key = {
            1: "candidate",
            2: "hiring_manager",
            3: "director",
            4: "executive_director",
        }.get(int(index), "")
        if role_key:
            self._add_notification_role_recipient(role_key)
        self.notification_recipient_picker.blockSignals(True)
        self.notification_recipient_picker.setCurrentIndex(0)
        self.notification_recipient_picker.blockSignals(False)

    def _render_notification_recipient_chips(self) -> None:
        layout = getattr(self, "notification_recipient_chips_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        layout.addWidget(self._label("Recipients", "StaffingV2SectionTitle"))
        recipients = self._current_notification_recipients()
        if not recipients:
            layout.addWidget(self._label("No recipients configured.", "StaffingV2Muted"))
            return
        for recipient in recipients:
            row = self.QtWidgets.QHBoxLayout()
            text = _notification_recipient_display(recipient)
            row.addWidget(self._label(text, "StaffingV2NeutralChip"))
            remove = self.QtWidgets.QPushButton("Remove")
            key = _notification_recipient_key(recipient)
            remove.setObjectName(f"StaffingV2NotificationRecipientRemove_{_notification_recipient_remove_suffix(recipient)}")
            remove.clicked.connect(lambda _checked=False, item_key=key: self._remove_notification_recipient(item_key))
            row.addWidget(remove)
            row.addStretch(1)
            layout.addLayout(row)

    def _remove_notification_recipient(self, recipient_key: str) -> None:
        selected_key = str(recipient_key or "").strip()
        self.notification_selected_recipients = [
            recipient for recipient in self.notification_selected_recipients if _notification_recipient_key(recipient) != selected_key
        ]
        self._render_notification_recipient_chips()
        self._sync_notification_rule_validation()

    def _sync_notification_rule_validation(self) -> None:
        if not hasattr(self, "notification_validation"):
            return
        rule = self._notification_rule_from_editor()
        issues = validate_notification_rule(rule)
        self.notification_validation.setText(
            "No issues found" if not issues else "\n".join(f"{'Blocked' if issue.blocking else 'Warning'}: {issue.message}" for issue in issues)
        )
        self.notification_variables_preview.setText("  ".join(f"{{{field}}}" for field in notification_template_fields(rule)))
        reference_date = rule.trigger_timing == "date_offset"
        self.notification_rule_date_field.setEnabled(reference_date)
        self.notification_rule_offset_direction.setEnabled(reference_date)
        self.notification_rule_offset.setEnabled(reference_date and self.notification_rule_offset_direction.currentText() != "On")
        if hasattr(self, "notification_save"):
            self.notification_save.setEnabled(not any(issue.blocking for issue in issues))

    def _notification_rule_from_editor(self) -> NotificationRule:
        timing = "date_offset" if self.notification_rule_timing.currentText() == "Reference date" else "event"
        offset_days = self.notification_rule_offset.value()
        if self.notification_rule_offset_direction.currentText() == "Before":
            offset_days *= -1
        elif self.notification_rule_offset_direction.currentText() == "On":
            offset_days = 0
        return NotificationRule(
            id=self.selected_notification_rule_id,
            event_type=self.notification_rule_event.currentText(),
            label=self.notification_rule_label.text(),
            active=self.notification_rule_enabled.isChecked(),
            trigger_timing=timing,
            date_field=self.notification_rule_date_field.currentText(),
            offset_days=offset_days,
            subject_template=self.notification_rule_subject.text(),
            body_template=self.notification_rule_body.toPlainText(),
            recipients=self._current_notification_recipients(),
        )

    def _insert_notification_subject_variable(self, token: str) -> None:
        text = self.notification_rule_subject.text()
        position = self.notification_rule_subject.cursorPosition()
        self.notification_rule_subject.setText(text[:position] + token + text[position:])
        self.notification_rule_subject.setCursorPosition(position + len(token))
        self._sync_notification_rule_validation()

    def _insert_notification_body_text(self, text: str) -> None:
        cursor = self.notification_rule_body.textCursor()
        cursor.insertText(str(text or ""))
        self.notification_rule_body.setTextCursor(cursor)
        self._sync_notification_rule_validation()

    def _insert_notification_variable(self, token: str) -> None:
        focused = self.QtWidgets.QApplication.focusWidget()
        target = focused if focused in {self.notification_rule_subject, self.notification_rule_body} else getattr(
            self, "_notification_variable_target", None
        )
        if target is self.notification_rule_subject:
            self._insert_notification_subject_variable(token)
            return
        self._insert_notification_body_text(token)

    def _open_notification_preview_dialog(self) -> None:
        rule = self._notification_rule_from_editor()
        sample = _notification_preview_sample()
        rendered = render_notification_templates(rule, sample)
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2NotificationPreviewDialog")
        dialog.setWindowTitle("Notification Preview")
        dialog.resize(620, 500)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Notification Preview", "StaffingV2DrawerTitle"))
        layout.addWidget(self._label("Subject", "StaffingV2SectionTitle"))
        layout.addWidget(self._label(rendered.subject or "(blank subject)", "StaffingV2Muted"))
        layout.addWidget(self._label("Body", "StaffingV2SectionTitle"))
        body_view = self.QtWidgets.QTextBrowser()
        body_view.setObjectName("StaffingV2NotificationPreviewBody")
        body_view.setHtml(rendered.html_body or "<p>(blank body)</p>")
        body_view.setOpenExternalLinks(False)
        layout.addWidget(body_view, 1)
        if rendered.unresolved_fields:
            layout.addWidget(self._label(f"Unresolved variables: {', '.join(rendered.unresolved_fields)}", "StaffingV2NeedNowChip"))
        else:
            layout.addWidget(self._label("All variables resolved.", "StaffingV2HealthyChip"))
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.show()

    def _send_notification_test(self) -> None:
        recipient = self.notification_test_recipient.text().strip()
        if not recipient:
            self.notifications_status.setText("Enter an explicit test recipient email.")
            return
        payload = self._selected_notification_test_payload()
        if payload is None:
            return
        rule = self._notification_rule_from_editor()
        key = f"staffing-v2-test:{self.selected_notification_rule_id or 'draft'}:{datetime.now(timezone.utc).isoformat()}"
        self.notification_test_send.setEnabled(False)
        self.notifications_status.setText("Sending test notification…")

        def send() -> None:
            try:
                service = self.notification_service_factory() if self.notification_service_factory else NotificationService(store=self._notification_store())
                result: object = service.send_test_preview(rule, payload, recipient, key)
            except Exception as exc:  # noqa: BLE001 - passed through sanitizer on GUI thread.
                result = exc
            try:
                self.notification_test_signals.finished.emit(result)
            except RuntimeError:
                return

        threading.Thread(target=send, name="notification-test-send", daemon=True).start()

    def _finish_notification_test_send(self, result: object) -> None:
        self.notification_test_send.setEnabled(True)
        if isinstance(result, Exception):
            self.notifications_status.setText(_safe_notification_error(result))
        else:
            self.notifications_status.setText(f"Test send {getattr(result, 'status', 'failed')}.")
        self._refresh_notification_audit()

    def _refresh_notification_test_payloads(self, event_type: str) -> None:
        if not hasattr(self, "notification_test_payload_selector"):
            return
        options: list[NotificationTestPayload] = []
        if str(event_type or "").startswith("staffing."):
            for assignment in reversed(self.store.list_assignments()[-10:]):
                options.append(
                    NotificationTestPayload(
                        label=f"{assignment.school} · {assignment.classroom} · {assignment.position_name}",
                        event_type=str(event_type or ""),
                        payload=staffing_notification_payload(assignment),
                        source_kind="staffing",
                    )
                )
        if self.notification_test_payload_provider is not None:
            options.extend(self.notification_test_payload_provider(str(event_type or "")))
        self.notification_test_payloads = options[:10]
        self.notification_test_payload_selector.clear()
        self.notification_test_payload_selector.addItem("Manual payload…", -1)
        for index, option in enumerate(self.notification_test_payloads):
            self.notification_test_payload_selector.addItem(option.label, index)

    def _selected_notification_test_payload(self) -> dict[str, str] | None:
        index = self.notification_test_payload_selector.currentData()
        if isinstance(index, int) and index >= 0 and index < len(self.notification_test_payloads):
            return dict(self.notification_test_payloads[index].payload)
        return self._manual_notification_test_payload()

    def _manual_notification_test_payload(self) -> dict[str, str] | None:
        rule = self._notification_rule_from_editor()
        fields = list(notification_template_fields(rule))
        for attachment_field in _notification_attachment_fields(rule.event_type):
            if attachment_field not in fields:
                fields.append(attachment_field)
        if not fields:
            return _notification_preview_sample()
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2NotificationManualPayloadDialog")
        dialog.setWindowTitle("Manual Test Payload")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Manual Test Payload", "StaffingV2DrawerTitle"))
        form = self.QtWidgets.QFormLayout()
        editors: dict[str, Any] = {}
        sample = _notification_preview_sample()
        attachment_fields = set(_notification_attachment_fields(rule.event_type))
        for field in fields:
            row = self.QtWidgets.QWidget()
            row_layout = self.QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            editor = self.QtWidgets.QLineEdit(sample.get(field, ""))
            editor.setObjectName(f"StaffingV2NotificationManualPayload_{_safe_object_suffix(field)}")
            row_layout.addWidget(editor, 1)
            if field in attachment_fields:
                browse = self.QtWidgets.QPushButton("Browse")
                browse.clicked.connect(
                    lambda _checked=False, target=editor: target.setText(
                        self.QtWidgets.QFileDialog.getOpenFileName(dialog, "Select attachment")[0]
                    )
                )
                row_layout.addWidget(browse)
            form.addRow(field, row)
            editors[field] = editor
        layout.addLayout(form)
        buttons = self.QtWidgets.QDialogButtonBox(
            self.QtWidgets.QDialogButtonBox.StandardButton.Ok | self.QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            self.notifications_status.setText("Test send cancelled.")
            return None
        return {field: editor.text().strip() for field, editor in editors.items()}

    def _refresh_notification_audit(self) -> None:
        if self.selected_notification_rule_id is None or not hasattr(self, "notification_audit_summary"):
            return
        store = self._notification_store()
        audit = store.list_audit(self.selected_notification_rule_id, limit=5)
        scheduled = store.list_scheduled_notifications(self.selected_notification_rule_id, status="pending", limit=5)
        lines = [f"Pending scheduled: {len(scheduled)}"]
        for row in audit:
            safe_error = _safe_notification_error(row.get("error", ""))
            error = f" - {safe_error}" if safe_error else ""
            lines.append(f"{row['created_at']} · {row['status']} · recipients {row['recipient_count']}{error}")
        if len(lines) == 1:
            lines.append("No send attempts yet.")
        self.notification_audit_summary.setText("\n".join(lines))

    def _cancel_notification_rule(self) -> None:
        if self.selected_notification_rule_id is None:
            self._refresh_notifications()
            self.notification_editor_overlay.hide()
            return
        try:
            self._load_notification_rule(self._notification_store().get_rule(self.selected_notification_rule_id))
        except ValueError:
            self._refresh_notifications()
        self.notification_editor_overlay.hide()

    def _request_close_notification_editor(self) -> None:
        if not self._confirm_notification_editor_switch():
            return
        self._cancel_notification_rule()

    def _can_leave_notifications_view(self) -> bool:
        notifications_view = getattr(self, "notifications_view", None)
        if notifications_view is None or self.page_stack.currentWidget() is not notifications_view:
            return True
        overlay = getattr(self, "notification_editor_overlay", None)
        if overlay is None or overlay.frame.isHidden():
            return True
        if not self._confirm_notification_editor_switch():
            return False
        self._cancel_notification_rule()
        return True

    def _confirm_notification_editor_switch(self) -> bool:
        overlay = getattr(self, "notification_editor_overlay", None)
        if overlay is None or overlay.frame.isHidden() or not self._notification_editor_is_dirty():
            return True
        box = self.QtWidgets.QMessageBox(self.widget)
        box.setWindowTitle("Unsaved Notification Changes")
        box.setText("Save changes before leaving this notification rule?")
        save = box.addButton("Save", self.QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", self.QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep Editing", self.QtWidgets.QMessageBox.ButtonRole.RejectRole)
        save.setEnabled(self.notification_save.isEnabled())
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep or clicked is None:
            return False
        if clicked is save:
            self._save_notification_rule()
            return self.notifications_status.text() == "Notification rule saved."
        return clicked is discard

    def _notification_editor_is_dirty(self) -> bool:
        baseline = getattr(self, "notification_editor_baseline", None)
        return baseline is not None and self._notification_rule_from_editor() != baseline

    def _build_people_view(self) -> None:
        self.people_view = self.QtWidgets.QWidget()
        self.people_view.setObjectName("StaffingV2PeopleDashboard")
        people_outer = self.QtWidgets.QHBoxLayout(self.people_view)
        people_outer.setContentsMargins(0, 0, 0, 0)
        people_outer.setSpacing(14)
        people_main = self.QtWidgets.QWidget()
        people_root = self.QtWidgets.QVBoxLayout(people_main)
        people_root.setContentsMargins(0, 0, 0, 0)
        people_root.setSpacing(14)
        people_outer.addWidget(people_main, 1)
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
        add_person.clicked.connect(self._open_add_person_dialog)
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
        self.people_role_filter = self._people_filter_combo("StaffingV2PeopleRoleFilter", ["All", "Director", "Teacher", "Aide"])
        filters.addLayout(self._labeled_control("Role", self.people_role_filter), 1)
        self.people_permit_filter = self._people_filter_combo("StaffingV2PeoplePermitFilter", ["All", "Teacher Permit", "Permit in Process", "Unknown"])
        filters.addLayout(self._labeled_control("Permit Status", self.people_permit_filter), 1)
        more_filters = self.QtWidgets.QPushButton("More Filters")
        more_filters.setObjectName("StaffingV2PeopleMoreFilters")
        self._set_button_icon(more_filters, "filter")
        more_filters.clicked.connect(self._open_people_filter_drawer)
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
        self.people_detail_overlay = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.people_view,
            object_name="StaffingV2PeopleDetailPanel",
            width=420,
        )
        self.people_detail_panel = self.people_detail_overlay.frame
        self.people_detail_layout = self.people_detail_overlay.body_layout
        self.people_detail_footer_layout = self.people_detail_overlay.footer_layout
        body.setSizes([860])
        people_root.addWidget(body, 1)
        people_footer = self.QtWidgets.QHBoxLayout()
        self.people_result_count = self.QtWidgets.QLabel("Showing 0 to 0 of 0 people")
        self.people_result_count.setObjectName("StaffingV2PeopleResultCount")
        people_footer.addWidget(self.people_result_count)
        people_footer.addStretch(1)
        previous_page = self.QtWidgets.QPushButton("‹")
        previous_page.setObjectName("StaffingV2PeoplePreviousPage")
        previous_page.setEnabled(False)
        people_footer.addWidget(previous_page)
        current_page = self.QtWidgets.QPushButton("1")
        current_page.setObjectName("StaffingV2PeopleCurrentPage")
        current_page.setEnabled(False)
        people_footer.addWidget(current_page)
        next_page = self.QtWidgets.QPushButton("›")
        next_page.setObjectName("StaffingV2PeopleNextPage")
        next_page.setEnabled(False)
        people_footer.addWidget(next_page)
        self.people_rows_per_page = self.QtWidgets.QComboBox()
        self.people_rows_per_page.setObjectName("StaffingV2PeopleRowsPerPage")
        self.people_rows_per_page.addItems(["10 / page", "25 / page", "50 / page"])
        self.people_rows_per_page.setEnabled(False)
        people_footer.addWidget(self.people_rows_per_page)
        people_root.addLayout(people_footer)
        self.people_units_filter_value = "All Units"
        self.people_filter_drawer_panel = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.people_view,
            object_name="StaffingV2PeopleFilterDrawer",
            width=340,
        )
        self.people_filter_drawer = self.people_filter_drawer_panel.frame
        self.people_filter_drawer_layout = self.people_filter_drawer_panel.body_layout
        self.people_filter_drawer_footer_layout = self.people_filter_drawer_panel.footer_layout
        self._build_people_filter_drawer()

    def _build_people_filter_drawer(self) -> None:
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Filters", "StaffingV2SectionTitle"), 1)
        reset = self.QtWidgets.QPushButton("Reset")
        reset.setObjectName("StaffingV2PeopleFilterReset")
        self._set_button_icon(reset, "reset")
        reset.clicked.connect(self._reset_people_filter_drawer)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2PeopleFilterClose")
        self._set_button_icon(close, "close")
        close.setFixedSize(32, 32)
        close.clicked.connect(self.people_filter_drawer.hide)
        header.addWidget(reset)
        header.addWidget(close)
        self.people_filter_drawer_layout.addLayout(header)

        self.people_filter_active = self._people_drawer_combo("StaffingV2PeopleFilterActive", ["All", "Active", "Inactive"])
        self.people_filter_drawer_layout.addLayout(self._labeled_control("Active Status", self.people_filter_active))
        self.people_filter_role = self._people_drawer_combo("StaffingV2PeopleFilterRole", ["All", "Teacher", "Aide"])
        self.people_filter_drawer_layout.addLayout(self._labeled_control("Role", self.people_filter_role))
        self.people_filter_permit = self._people_drawer_combo(
            "StaffingV2PeopleFilterPermit",
            ["All", "Teacher Permit", "Permit in Process", "Unknown"],
        )
        self.people_filter_drawer_layout.addLayout(self._labeled_control("Permit Status", self.people_filter_permit))
        self.people_filter_units = self._people_drawer_combo(
            "StaffingV2PeopleFilterUnits",
            ["All Units", "Has Units", "No Units"],
        )
        self.people_filter_drawer_layout.addLayout(self._labeled_control("Units", self.people_filter_units))
        self.people_filter_drawer_layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2PeopleFilterCancel")
        cancel.clicked.connect(self.people_filter_drawer.hide)
        apply = self.QtWidgets.QPushButton("Apply Filters")
        apply.setObjectName("StaffingV2PeopleFilterApply")
        self._set_button_icon(apply, "filter")
        apply.clicked.connect(self._apply_people_filter_drawer)
        footer.addWidget(cancel)
        footer.addWidget(apply)
        self.people_filter_drawer_footer_layout.addLayout(footer)

    def _people_drawer_combo(self, object_name: str, values: list[str]) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.setObjectName(object_name)
        combo.addItems(values)
        return combo

    def _open_people_filter_drawer(self) -> None:
        self.people_filter_active.setCurrentText(self.people_active_filter.currentText())
        self.people_filter_role.setCurrentText(self.people_role_filter.currentText())
        self.people_filter_permit.setCurrentText(self.people_permit_filter.currentText())
        self.people_filter_units.setCurrentText(self.people_units_filter_value)
        self.people_filter_drawer_panel.show_overlay()

    def _reset_people_filter_drawer(self) -> None:
        self.people_filter_active.setCurrentText("All")
        self.people_filter_role.setCurrentText("All")
        self.people_filter_permit.setCurrentText("All")
        self.people_filter_units.setCurrentText("All Units")

    def _apply_people_filter_drawer(self) -> None:
        self.people_active_filter.setCurrentText(self.people_filter_active.currentText())
        self.people_role_filter.setCurrentText(self.people_filter_role.currentText())
        self.people_permit_filter.setCurrentText(self.people_filter_permit.currentText())
        self.people_units_filter_value = self.people_filter_units.currentText()
        self._refresh_people_filters()
        self.people_filter_drawer.hide()

    def _open_add_person_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2AddPersonDialog")
        dialog.setWindowTitle("Add Person")
        dialog.setModal(True)
        dialog.resize(520, 420)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Add Person", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Create an employee record for staffing assignments.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2AddPersonClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        form, form_layout = self._dialog_section("StaffingV2DialogSection")
        name = self.QtWidgets.QLineEdit()
        name.setObjectName("StaffingV2AddPersonName")
        name.setPlaceholderText("Full name")
        form_layout.addLayout(self._labeled_control("Full Name", name))
        role = self.QtWidgets.QComboBox()
        role.setObjectName("StaffingV2AddPersonRole")
        role.addItems(["Director", "Teacher", "Aide"])
        form_layout.addLayout(self._labeled_control("Role", role))
        permit = self.QtWidgets.QComboBox()
        permit.setObjectName("StaffingV2AddPersonPermit")
        permit.addItems(["Unknown", "Permit in Process", "Teacher Permit", "No Units Needed", "No Permit"])
        form_layout.addLayout(self._labeled_control("Permit Status", permit))
        units = self.QtWidgets.QLineEdit()
        units.setObjectName("StaffingV2AddPersonUnits")
        units.setPlaceholderText("Optional units")
        form_layout.addLayout(self._labeled_control("Units", units))
        status = self._label("", "StaffingV2Muted")
        status.setObjectName("StaffingV2AddPersonStatus")
        form_layout.addWidget(status)
        layout.addWidget(form)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2SecondaryButton")
        cancel.clicked.connect(dialog.reject)
        save = self.QtWidgets.QPushButton("Add Person")
        save.setObjectName("StaffingV2AddPersonSave")
        self._set_button_icon(save, "add")

        def save_person() -> None:
            try:
                units_value = None
                if units.text().strip():
                    units_value = float(units.text().strip())
                self.service_factory().add_person(
                    name=name.text(),
                    role=role.currentText(),
                    permit_status=_permit_status_from_label(permit.currentText()),
                    units=units_value,
                )
            except ValueError as exc:
                status.setText(str(exc))
                return
            self._refresh_people()
            dialog.accept()

        save.clicked.connect(save_person)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)
        dialog.show()

    def _show_people_view(self) -> None:
        if not self._can_leave_notifications_view():
            return
        self._ensure_lazy_view("people")
        self._set_active_nav(self.people_nav_button)
        self._refresh_people()
        self.page_stack.setCurrentWidget(self.people_view)

    def _show_history_view(self) -> None:
        if not self._can_leave_notifications_view():
            return
        self._ensure_lazy_view("history")
        self._set_active_nav(self.history_nav_button)
        self._refresh_history()
        self.page_stack.setCurrentWidget(self.history_view)

    def _show_notifications_view(self) -> None:
        self._ensure_lazy_view("notifications")
        self._set_active_nav(self.notifications_nav_button)
        self._refresh_notifications()
        self.page_stack.setCurrentWidget(self.notifications_view)

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

    def refresh(self, *, include_lazy: bool = False) -> None:
        self.store.initialize()
        metrics = self.service_factory().staffing_metrics(today=date.today(), school=self.school_filter)
        self.rows = list(metrics.rows)
        self._sync_selectors()
        self._refresh_filters()
        self._refresh_director_interviews()
        if include_lazy:
            self._refresh_built_lazy_views()
        self._schedule_dashboard_scroll_sync()

    def refresh_all(self) -> None:
        self.refresh(include_lazy=True)

    def _schedule_dashboard_scroll_sync(self) -> None:
        if not self._dashboard_scroll_widgets:
            return
        self.QtCore.QTimer.singleShot(0, self._sync_staffing_v2_scroll_ranges)

    def _sync_staffing_v2_scroll_ranges(self) -> None:
        self._configure_staffing_v2_scroll_areas()
        if not hasattr(self, "detail_content"):
            return
        self.detail_content.setMinimumHeight(0)
        self.detail_content.adjustSize()
        content_height = max(self.detail_content.minimumSizeHint().height(), self.detail_content.sizeHint().height())
        self.detail_content.setMinimumHeight(content_height)
        self.detail_scroll.widget().resize(
            max(self.detail_scroll.viewport().width(), self.detail_content.sizeHint().width()),
            content_height,
        )
        for scroll_widget in self._dashboard_scroll_widgets:
            scroll_widget.verticalScrollBar().setSingleStep(24)
            scroll_widget.verticalScrollBar().setPageStep(max(80, scroll_widget.viewport().height() - 48))

    def _configure_staffing_v2_scroll_areas(self) -> None:
        configure_v2_scroll_areas(self.QtWidgets, self.widget, self.QtCore)

    def _notification_store(self) -> NotificationStore:
        return NotificationStore(self.notification_store_path)

    def _open_dashboard_export_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2DashboardExportDialog")
        dialog.setWindowTitle("Export Staffing Dashboard")
        dialog.setModal(True)
        dialog.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(560, 460)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Export Staffing Dashboard", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Preview the current Staffing v2 dashboard export.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2DashboardExportClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        rows = [row for classroom_rows in getattr(self, "classroom_rows", {}).values() for row in classroom_rows]
        status_filter = str(self.dashboard_classroom_filter_state.get("status", "All Statuses"))
        open_filter_dialog = self.widget.findChild(self.QtWidgets.QDialog, "StaffingV2DashboardClassroomFilterDrawer")
        if open_filter_dialog is not None:
            status_combo = open_filter_dialog.findChild(self.QtWidgets.QComboBox, "StaffingV2DashboardClassroomStatusFilter")
            if status_combo is not None:
                status_filter = status_combo.currentText()
        schools = sorted({row.school for row in rows if row.school})
        open_positions = sum(1 for row in rows if row.status in {"need_now", "replace"})
        filled_positions = sum(1 for row in rows if row.status == "filled")
        permit_issues = sum(1 for row in rows if _row_has_permit_issue(row))
        classrooms = sorted({row.classroom for row in rows if row.classroom})
        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        for label, value in [
            ("Schools", str(len(schools))),
            ("School filter", self.school_selector.currentText() or "-"),
            ("Program filter", self.program_selector.currentText() or "All Programs"),
            ("Search filter", self.search.text().strip() or "-"),
            ("Classroom status filter", status_filter),
            ("Open positions", str(open_positions)),
            ("Filled positions", str(filled_positions)),
            ("Permit issues", str(permit_issues)),
            ("Classrooms", ", ".join(classrooms) if classrooms else "-"),
        ]:
            preview_layout.addLayout(self._detail_row(label, value))
        layout.addWidget(preview)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.setObjectName("StaffingV2SecondaryButton")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.show()

    def _refresh_people(self) -> None:
        self.people = self.store.list_people()
        self._refresh_people_filters()

    def _clear_people_filters(self) -> None:
        self.people_search.clear()
        self.people_active_filter.setCurrentText("All")
        self.people_role_filter.setCurrentText("All")
        self.people_permit_filter.setCurrentText("All")
        if hasattr(self, "people_units_filter_value"):
            self.people_units_filter_value = "All Units"
            if hasattr(self, "people_filter_units"):
                self.people_filter_units.setCurrentText("All Units")
        self._refresh_people_filters()

    def _refresh_people_filters(self) -> None:
        if not hasattr(self, "people_table"):
            return
        search = self.people_search.text().strip().casefold()
        active_filter = self.people_active_filter.currentText()
        role_filter = self.people_role_filter.currentText()
        permit_filter = self.people_permit_filter.currentText()
        units_filter = getattr(self, "people_units_filter_value", "All Units")
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
            if units_filter == "Has Units" and person.units is None:
                continue
            if units_filter == "No Units" and person.units is not None:
                continue
            self.visible_people.append(person)
        self._refresh_people_metrics()
        self._refresh_people_table()
        if hasattr(self, "people_result_count"):
            visible_count = len(self.visible_people)
            if visible_count:
                self.people_result_count.setText(f"Showing 1 to {visible_count} of {visible_count} people")
            else:
                self.people_result_count.setText("Showing 0 to 0 of 0 people")

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
            view.clicked.connect(lambda _checked=False, index=row_index: self._select_person(index))
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
        self.people_detail_overlay.clear()
        if person is None:
            self.people_detail_layout.addWidget(self._label("No employee selected", "StaffingV2Muted"))
            self.people_detail_overlay.hide()
            return

        self.people_detail_overlay.add_header(
            title="Employee Detail",
            title_object_name="StaffingV2SectionTitle",
            close_object_name="StaffingV2PeopleDetailClose",
            close_icon=self._standard_icon("close"),
        )
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
        def select_people_tab(selected_object_name: str) -> None:
            for tab_button in tabs.findChildren(self.QtWidgets.QPushButton):
                tab_button.setProperty("staffingV2ActivePeopleTab", tab_button.objectName() == selected_object_name)
                tab_button.style().unpolish(tab_button)
                tab_button.style().polish(tab_button)

        for object_name, text, is_active in tab_specs:
            tab = self.QtWidgets.QPushButton(text)
            tab.setObjectName(object_name)
            tab.setProperty("staffingV2ActivePeopleTab", is_active)
            tab.clicked.connect(lambda _checked=False, item=object_name: select_people_tab(item))
            tabs_layout.addWidget(tab)
        tabs_layout.addStretch(1)
        self.people_detail_layout.addWidget(tabs)

        info, info_layout = self._detail_panel_card("StaffingV2PeopleDetailCard", "Employee Information")
        info_layout.addLayout(self._detail_row("Role", person.role or "-"))
        info_layout.addLayout(self._detail_row("Permit Status", _permit_label(person.permit_status)))
        info_layout.addLayout(self._detail_row("Units", _format_units(person.units)))
        info_layout.addLayout(self._detail_row("Hire Date", "-"))
        info_layout.addLayout(self._detail_row("Active", "Yes" if person.active else "No"))
        self.people_detail_layout.addWidget(info)

        current, current_layout = self._detail_panel_card("StaffingV2PeopleDetailCard", "Current Assignment")
        current_layout.addWidget(self._label(person.assignment_school or "-", "StaffingV2Muted"))
        current_layout.addWidget(self._label(_assignment_detail(person), "StaffingV2Muted"))
        self.people_detail_layout.addWidget(current)

        employment, employment_layout = self._detail_panel_card("StaffingV2PeopleDetailCard", "Employment Status")
        employment_layout.addLayout(self._detail_row("Notice Given", person.notice_given or "-"))
        employment_layout.addLayout(self._detail_row("Final Working Day", person.final_working_day or "-"))
        employment_layout.addLayout(self._detail_row("Employment Status", "Active" if person.active else "Inactive"))
        employment_layout.addLayout(self._detail_row("Rehire Eligible", "Yes" if person.active else "-"))
        self.people_detail_layout.addWidget(employment)

        additional, additional_layout = self._detail_panel_card("StaffingV2PeopleDetailCard", "Additional Information")
        additional_layout.addLayout(self._detail_row("Permit Effective Date", person.permit_effective_date or "-"))
        additional_layout.addLayout(self._detail_row("Documentation", "Received" if person.permit_documentation_received else "-"))
        additional_layout.addLayout(self._detail_row("Notes", person.permit_notes or "-"))
        self.people_detail_layout.addWidget(additional)

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
        self.people_detail_footer_layout.addLayout(footer)
        self.people_detail_overlay.show_overlay()

    def _detail_row(self, label: str, value: str) -> Any:
        row = self.QtWidgets.QHBoxLayout()
        row.setSpacing(6)
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
        export.clicked.connect(self._open_history_export_list_dialog)
        validation = self.QtWidgets.QPushButton("View Validation")
        validation.setObjectName("StaffingV2HistoryValidationButton")
        self._set_button_icon(validation, "validation")
        validation.clicked.connect(self._show_validation_view)
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
        self.history_date_range_filter.setEnabled(False)
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
        self.history_detail_overlay = _StaffingV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.history_view,
            object_name="StaffingV2HistoryDetailPanel",
            width=400,
        )
        self.history_detail_panel = self.history_detail_overlay.frame
        self.history_detail_layout = self.history_detail_overlay.body_layout
        self.history_detail_footer_layout = self.history_detail_overlay.footer_layout
        body.setSizes([900])
        history_root.addWidget(body, 1)
        history_footer = self.QtWidgets.QHBoxLayout()
        self.history_result_count = self.QtWidgets.QLabel("Showing 0 to 0 of 0 records")
        self.history_result_count.setObjectName("StaffingV2HistoryResultCount")
        history_footer.addWidget(self.history_result_count)
        history_footer.addStretch(1)
        previous_page = self.QtWidgets.QPushButton("‹")
        previous_page.setObjectName("StaffingV2HistoryPreviousPage")
        previous_page.setEnabled(False)
        history_footer.addWidget(previous_page)
        current_page = self.QtWidgets.QPushButton("1")
        current_page.setObjectName("StaffingV2HistoryCurrentPage")
        current_page.setEnabled(False)
        history_footer.addWidget(current_page)
        next_page = self.QtWidgets.QPushButton("›")
        next_page.setObjectName("StaffingV2HistoryNextPage")
        next_page.setEnabled(False)
        history_footer.addWidget(next_page)
        self.history_rows_per_page = self.QtWidgets.QComboBox()
        self.history_rows_per_page.setObjectName("StaffingV2HistoryRowsPerPage")
        self.history_rows_per_page.addItems(["10 / page", "25 / page", "50 / page"])
        self.history_rows_per_page.setEnabled(False)
        history_footer.addWidget(self.history_rows_per_page)
        history_root.addLayout(history_footer)

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
        if hasattr(self, "history_result_count"):
            visible_count = len(self.visible_history_records)
            if visible_count:
                self.history_result_count.setText(f"Showing 1 to {visible_count} of {visible_count} records")
            else:
                self.history_result_count.setText("Showing 0 to 0 of 0 records")

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
            view.clicked.connect(lambda _checked=False, index=row_index: self._select_history_record(index))
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
        self.history_detail_overlay.clear()
        if record is None:
            self.history_detail_layout.addWidget(self._label("No history record selected", "StaffingV2Muted"))
            self.history_detail_overlay.hide()
            return
        self.history_detail_overlay.add_header(
            title="History Record Detail",
            title_object_name="StaffingV2HistoryDetailTitle",
            close_object_name="StaffingV2HistoryDetailClose",
            close_icon=self._standard_icon("close"),
        )
        assignment_id_row = self.QtWidgets.QHBoxLayout()
        assignment_id_row.addWidget(self._label("Assignment ID:", "StaffingV2SectionTitle"))
        assignment_id_chip = self.QtWidgets.QFrame()
        assignment_id_chip.setObjectName("StaffingV2HistoryAssignmentIdChip")
        assignment_id_chip_layout = self.QtWidgets.QHBoxLayout(assignment_id_chip)
        assignment_id_chip_layout.setContentsMargins(8, 2, 8, 2)
        assignment_id_chip_layout.addWidget(self._label(f"A-{record.assignment_id:04d}", "StaffingV2ChipText"))
        assignment_id_row.addWidget(assignment_id_chip)
        assignment_id_row.addStretch(1)
        self.history_detail_layout.addLayout(assignment_id_row)
        overview, overview_layout = self._detail_panel_card("StaffingV2HistoryDetailCard")
        overview_layout.addLayout(self._detail_row("Classroom", record.classroom))
        overview_layout.addLayout(self._detail_row("Position", record.position_name))
        overview_layout.addLayout(self._detail_row("Cycle status", record.cycle_status))
        overview_layout.addLayout(self._detail_row("Opened date", record.opened_date))
        overview_layout.addLayout(self._detail_row("Filled date", record.filled_date or "-"))
        overview_layout.addLayout(self._detail_row("Days to fill", "" if record.days_to_fill is None else str(record.days_to_fill)))
        overview_layout.addLayout(self._detail_row("Filled by / Employee", record.employee))
        overview_layout.addLayout(self._detail_row("School", record.school))
        self.history_detail_layout.addWidget(overview)

        lifecycle, lifecycle_layout = self._detail_panel_card("StaffingV2HistoryDetailCard", "Lifecycle Events")
        lifecycle_events = [("opened", "Position opened", record.opened_date, "add")]
        if record.filled_date:
            lifecycle_events.append(("filled", "Position marked Filled", record.filled_date, "status_filled"))
        else:
            lifecycle_events.append(("open", "Cycle remains open", "", "status_pending"))
        for event_type, title, date_text, icon_key in lifecycle_events:
            event_row = self.QtWidgets.QFrame()
            event_row.setObjectName("StaffingV2HistoryLifecycleEventRow")
            event_row.setProperty("staffingV2LifecycleEventType", event_type)
            event_layout = self.QtWidgets.QHBoxLayout(event_row)
            event_layout.setContentsMargins(0, 2, 0, 2)
            event_layout.setSpacing(8)
            event_layout.addWidget(self._icon_label(icon_key, "StaffingV2ChipIcon"))
            text_column = self.QtWidgets.QVBoxLayout()
            text_column.addWidget(self._label(title))
            if date_text:
                text_column.addWidget(self._label(date_text, "StaffingV2Muted"))
            event_layout.addLayout(text_column, 1)
            lifecycle_layout.addWidget(event_row)
        self.history_detail_layout.addWidget(lifecycle)

        validation, validation_layout = self._detail_panel_card("StaffingV2HistoryDetailCard", "Validation / Integrity")
        check_rows = [
            ("pass" if record.data_integrity == "Healthy" else "warning", f"History status: {record.data_integrity}"),
            ("pass" if record.opened_date else "warning", "Dates valid" if record.opened_date else "Missing opened date"),
            ("warning" if record.data_integrity != "Healthy" else "pass", "Duplicate active cycle" if record.data_integrity != "Healthy" else "No duplicate open cycles"),
        ]
        for status, text in check_rows:
            row = self.QtWidgets.QFrame()
            row.setObjectName("StaffingV2HistoryValidationCheckRow")
            row.setProperty("staffingV2ValidationCheckStatus", status)
            row_layout = self.QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(8)
            row_layout.addWidget(self._icon_label("status_filled" if status == "pass" else "status_need", "StaffingV2ChipIcon"))
            row_layout.addWidget(self._label(text))
            row_layout.addStretch(1)
            validation_layout.addWidget(row)
        self.history_detail_layout.addWidget(validation)

        footer = self.QtWidgets.QHBoxLayout()
        view = self.QtWidgets.QPushButton("View Assignment")
        view.setObjectName("StaffingV2HistoryViewAssignment")
        self._set_button_icon(view, "dashboard")
        view.clicked.connect(lambda _checked=False, item=record.assignment_id: self._open_history_assignment(item))
        employee = self.QtWidgets.QPushButton("Open Employee")
        employee.setObjectName("StaffingV2HistoryOpenEmployee")
        self._set_button_icon(employee, "people")
        if record.employee and record.employee != "OPEN POSITION":
            employee.clicked.connect(lambda _checked=False, name=record.employee: self._open_history_employee(name))
        else:
            employee.setEnabled(False)
        export = self.QtWidgets.QPushButton("Export Record")
        export.setObjectName("StaffingV2HistoryExportRecord")
        self._set_button_icon(export, "export")
        export.clicked.connect(lambda _checked=False, item=record: self._open_history_export_dialog(item))
        footer.addWidget(view)
        footer.addWidget(employee)
        footer.addWidget(export)
        self.history_detail_footer_layout.addLayout(footer)
        self.history_detail_overlay.show_overlay()

    def _open_history_assignment(self, assignment_id: int) -> None:
        self._show_dashboard_view()
        self._show_position_drawer(assignment_id)

    def _open_history_employee(self, employee_name: str) -> None:
        self._show_people_view()
        for row_index, person in enumerate(self.visible_people):
            if person.name == employee_name:
                self.people_table.setCurrentCell(row_index, 0)
                self._select_person(row_index)
                return

    def _open_history_export_list_dialog(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2HistoryExportDialog")
        dialog.setWindowTitle("Export Assignment History")
        dialog.setModal(True)
        dialog.resize(560, 460)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Export Assignment History", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Preview the currently filtered assignment history records.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2HistoryExportClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        records = list(getattr(self, "visible_history_records", []))
        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        preview_layout.addLayout(self._detail_row("Total records", str(len(records))))
        preview_layout.addLayout(self._detail_row("School filter", self.history_school_filter.currentText()))
        preview_layout.addLayout(self._detail_row("Classroom filter", self.history_classroom_filter.currentText()))
        preview_layout.addLayout(self._detail_row("Cycle status filter", self.history_cycle_filter.currentText()))
        for record in records[:8]:
            summary = f"{record.classroom} - {record.position_name} - {record.cycle_status}"
            preview_layout.addLayout(self._detail_row(f"A-{record.assignment_id:04d}", summary))
        if len(records) > 8:
            preview_layout.addLayout(self._detail_row("Additional records", str(len(records) - 8)))
        layout.addWidget(preview)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.setObjectName("StaffingV2SecondaryButton")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.show()

    def _open_history_export_dialog(self, record: StaffingHistoryRecord) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2HistoryExportRecordDialog")
        dialog.setWindowTitle("Export Record")
        dialog.setModal(True)
        dialog.resize(520, 420)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Export Record", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label("Preview the selected assignment history record before export.", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2HistoryExportRecordClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        preview, preview_layout = self._dialog_section("StaffingV2DialogInfo")
        for label, value in [
            ("Assignment ID", f"A-{record.assignment_id:04d}"),
            ("Classroom", record.classroom),
            ("Position", record.position_name),
            ("Cycle status", record.cycle_status),
            ("Opened date", record.opened_date),
            ("Filled date", record.filled_date or "-"),
            ("Days to fill", "" if record.days_to_fill is None else str(record.days_to_fill)),
            ("Employee", record.employee),
            ("Data integrity", record.data_integrity),
        ]:
            preview_layout.addLayout(self._detail_row(label, value))
        layout.addWidget(preview)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.setObjectName("StaffingV2SecondaryButton")
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.show()

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

    def _refresh_selected_school_metrics(self) -> None:
        selected_school = self.school_selector.currentText().strip() if self.school_selector.count() else ""
        metrics = self.service_factory().staffing_metrics(today=date.today(), school=selected_school or self.school_filter)
        school_count = len({row.school for row in metrics.rows if row.school})
        self._refresh_metrics(
            metrics.open_count,
            metrics.avg_days_to_fill,
            metrics.open_over_7_days,
            school_count=school_count,
            rows=metrics.rows,
        )

    def _refresh_metrics(
        self,
        open_count: int,
        avg_days_to_fill: float,
        open_over_7_days: int,
        *,
        school_count: int | None = None,
        rows: list[StaffingMetricRow] | None = None,
    ) -> None:
        while self.metrics_layout.count():
            item = self.metrics_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if school_count is None:
            school_count = len({row.school for row in self.rows if row.school})
        cards = [
            ("Schools", str(school_count), f"Schools: {school_count}"),
            ("Open positions", str(open_count), f"Open positions: {open_count}"),
            ("Avg fill time", f"{avg_days_to_fill:.1f} days", f"Avg fill time: {avg_days_to_fill:.1f} days"),
            ("Open > 7 days", str(open_over_7_days), f"Open > 7 days: {open_over_7_days}"),
            self._dashboard_validation_card(rows if rows is not None else self.rows),
        ]
        for label, value, accessible_text in cards:
            self.metrics_layout.addWidget(self._summary_chip(label, value, accessible_text))
        self.metrics_layout.addStretch(1)

    def _dashboard_validation_card(self, rows: list[StaffingMetricRow]) -> tuple[str, str, str]:
        issue_count = len(_validation_issues_from_rows(rows))
        if issue_count == 0:
            return ("Validation", "healthy", "Validation healthy")
        issue_text = "issue" if issue_count == 1 else "issues"
        return ("Validation", f"{issue_count} {issue_text}", f"Validation: {issue_count} {issue_text}")

    def _default_dashboard_classroom_filter_state(self) -> dict[str, Any]:
        return {
            "status": "All Statuses",
            "permit_issue_only": False,
            "open_over_7_only": False,
        }

    def _open_dashboard_classroom_filter_drawer(self) -> None:
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2DashboardClassroomFilterDrawer")
        dialog.setWindowTitle("Classroom Filters")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(420, 360)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Classroom Filters", "StaffingV2DrawerTitle"), 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2FilterCloseButton")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        status = self.QtWidgets.QComboBox()
        status.setObjectName("StaffingV2DashboardClassroomStatusFilter")
        status.addItems(["All Statuses", "Need Now", "Replace", "Coming", "Filled", "Don't Need"])
        status.setCurrentText(str(self.dashboard_classroom_filter_state.get("status", "All Statuses")))
        permit_issue_only = self.QtWidgets.QCheckBox("Only classrooms with permit issues")
        permit_issue_only.setObjectName("StaffingV2DashboardPermitIssueFilter")
        permit_issue_only.setChecked(bool(self.dashboard_classroom_filter_state.get("permit_issue_only", False)))
        open_over_7_only = self.QtWidgets.QCheckBox("Only classrooms open > 7 days")
        open_over_7_only.setObjectName("StaffingV2DashboardOpenOver7Filter")
        open_over_7_only.setChecked(bool(self.dashboard_classroom_filter_state.get("open_over_7_only", False)))
        layout.addLayout(self._labeled_control("Status", status))
        layout.addWidget(permit_issue_only)
        layout.addWidget(open_over_7_only)
        layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        reset = self.QtWidgets.QPushButton("Reset")
        reset.setObjectName("StaffingV2FilterResetButton")
        self._set_button_icon(reset, "reset")
        apply = self.QtWidgets.QPushButton("Apply Filters")
        apply.setObjectName("StaffingV2FilterApplyButton")
        self._set_button_icon(apply, "filter")
        footer.addWidget(reset)
        footer.addStretch(1)
        footer.addWidget(apply)
        layout.addLayout(footer)

        def reset_filters() -> None:
            status.setCurrentText("All Statuses")
            permit_issue_only.setChecked(False)
            open_over_7_only.setChecked(False)

        def apply_filters() -> None:
            self.dashboard_classroom_filter_state = {
                "status": status.currentText(),
                "permit_issue_only": permit_issue_only.isChecked(),
                "open_over_7_only": open_over_7_only.isChecked(),
            }
            dialog.close()
            self._refresh_filters()

        reset.clicked.connect(reset_filters)
        apply.clicked.connect(apply_filters)
        dialog.show()

    def _refresh_filters(self) -> None:
        self._refresh_selected_school_metrics()
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
        self.classroom_rows = {
            classroom: rows
            for classroom, rows in self.classroom_rows.items()
            if self._dashboard_classroom_matches_filters(rows)
        }
        current = self.classroom_list.currentItem().data(self.QtCore.Qt.ItemDataRole.UserRole) if self.classroom_list.currentItem() else ""
        self.classroom_list.blockSignals(True)
        self.classroom_list.clear()
        for classroom, rows in self.classroom_rows.items():
            item = self.QtWidgets.QListWidgetItem(_classroom_label(classroom, rows))
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, classroom)
            widget = self._classroom_list_item_widget(classroom, rows)
            size_hint = widget.sizeHint()
            if "\n" in _classroom_counts_text(rows):
                size_hint.setHeight(max(size_hint.height() + 24, 136))
            item.setSizeHint(size_hint)
            self.classroom_list.addItem(item)
            self.classroom_list.setItemWidget(item, widget)
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
        self._refresh_director_interviews()

    def _dashboard_classroom_matches_filters(self, rows: list[StaffingMetricRow]) -> bool:
        status = str(self.dashboard_classroom_filter_state.get("status", "All Statuses"))
        status_map = {
            "Need Now": "need_now",
            "Replace": "replace",
            "Coming": "coming",
            "Filled": "filled",
            "Don't Need": "dont_need_now",
        }
        if status != "All Statuses" and not any(row.status == status_map.get(status, "") for row in rows):
            return False
        if self.dashboard_classroom_filter_state.get("permit_issue_only") and not any(_row_has_permit_issue(row) for row in rows):
            return False
        if self.dashboard_classroom_filter_state.get("open_over_7_only") and not any(
            row.days_open is not None and row.days_open > 7 for row in rows
        ):
            return False
        return True

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
        frame.setProperty("staffingV2StatusFill", _classroom_status_key(rows))
        frame.setMinimumHeight(120)
        layout = self.QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
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
        counts.setWordWrap(True)
        counts.setMinimumHeight(max(64, counts.fontMetrics().lineSpacing() * 3 + 6))
        text.addWidget(title)
        text.addWidget(counts)
        layout.addLayout(text, 1)
        chevron = self._label(">", "StaffingV2ClassroomItemChevron")
        chevron.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(chevron)
        for widget in [frame, *frame.findChildren(self.QtWidgets.QWidget)]:
            widget.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return frame

    def _director_interview_panel(self) -> Any:
        panel, layout = self._panel("StaffingV2DirectorInterviewPanel")
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Director Interviews", "StaffingV2SectionTitle"))
        header.addStretch(1)
        self.director_interview_status = self._label("", "StaffingV2Muted")
        self.director_interview_status.setObjectName("StaffingV2DirectorInterviewStatus")
        self.director_interview_status.setWordWrap(False)
        self.director_interview_status.setMinimumWidth(170)
        header.addWidget(self.director_interview_status)
        self.director_interview_delete_selected = self.QtWidgets.QPushButton("Delete Selected")
        self.director_interview_delete_selected.setObjectName("StaffingV2DirectorInterviewDeleteSelected")
        self._set_button_icon(self.director_interview_delete_selected, "delete")
        self.director_interview_delete_selected.setMinimumWidth(170)
        self.director_interview_delete_selected.clicked.connect(self._delete_selected_director_referrals)
        header.addWidget(self.director_interview_delete_selected)
        layout.addLayout(header)

        layout.addWidget(self._label("Pending", "StaffingV2Muted"))
        self.director_interview_pending_table = self.QtWidgets.QTableWidget(0, 7)
        self.director_interview_pending_table.setObjectName("StaffingV2DirectorInterviewPendingTable")
        self.director_interview_pending_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.director_interview_pending_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.director_interview_pending_table.setSelectionMode(self.QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        self.director_interview_pending_table.setHorizontalHeaderLabels(
            ["Candidate", "Outcome", "Score", "Interview\nDate", "Role", "Referral\nDate", "Action"]
        )
        self.director_interview_pending_table.verticalHeader().hide()
        self.director_interview_pending_table.setWordWrap(True)
        self.director_interview_pending_table.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._apply_director_pending_table_column_layout()
        self.director_interview_pending_table.setMaximumHeight(190)
        layout.addWidget(self.director_interview_pending_table)

        layout.addWidget(self._label("Completed", "StaffingV2Muted"))
        self.director_interview_history_table = self.QtWidgets.QTableWidget(0, 8)
        self.director_interview_history_table.setObjectName("StaffingV2DirectorInterviewHistoryTable")
        self.director_interview_history_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.director_interview_history_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.director_interview_history_table.setHorizontalHeaderLabels(
            [
                "Candidate",
                "First Interview\nScore",
                "Date",
                "Director\nRating",
                "Decision",
                "Proposed\nClassroom",
                "Proposed\nShift",
                "Owner\nStatus",
            ]
        )
        self.director_interview_history_table.verticalHeader().hide()
        self.director_interview_history_table.setWordWrap(True)
        self.director_interview_history_table.setHorizontalScrollBarPolicy(
            self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        history_header = self.director_interview_history_table.horizontalHeader()
        history_header.setStretchLastSection(False)
        history_header.setDefaultAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        history_header.setMinimumHeight(54)
        for column, width in enumerate([200, 160, 110, 140, 120, 190, 190, 150]):
            self.director_interview_history_table.setColumnWidth(column, width)
            history_header.setSectionResizeMode(column, self.QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.director_interview_history_table.setMaximumHeight(150)
        layout.addWidget(self.director_interview_history_table)
        return panel

    def _apply_director_pending_table_column_layout(self) -> None:
        table = self.director_interview_pending_table
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        header.setMinimumHeight(54)
        for column, width in enumerate([260, 112, 82, 118, 250, 138, 210]):
            table.setColumnWidth(column, width)
            header.setSectionResizeMode(column, self.QtWidgets.QHeaderView.ResizeMode.Fixed)

    def _refresh_director_interviews(self) -> None:
        if not hasattr(self, "director_interview_pending_table"):
            return
        school = self.school_selector.currentText().strip() if self.school_selector.count() else self.school_filter
        service = self.service_factory()
        self.pending_director_candidates = service.list_pending_director_interviews(school=school)
        self.completed_director_interviews = service.list_completed_director_interviews(school=school)
        self._refresh_director_pending_table()
        self._refresh_director_history_table()
        self.director_interview_status.setText(
            f"{len(self.pending_director_candidates)} pending / {len(self.completed_director_interviews)} completed"
        )
        self.director_interview_delete_selected.setEnabled(bool(self.pending_director_candidates))

    def _refresh_director_pending_table(self) -> None:
        table = self.director_interview_pending_table
        table.setRowCount(0)
        for candidate in self.pending_director_candidates:
            row_index = table.rowCount()
            table.insertRow(row_index)
            values = [
                candidate.candidate_name,
                candidate.interviewer_outcome.title(),
                "" if candidate.interviewer_rating is None else f"{candidate.interviewer_rating:g}",
                candidate.interview_date or "-",
                candidate.position or "-",
                candidate.referral_date or "-",
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, candidate.id)
                item.setToolTip(value)
                table.setItem(row_index, column, item)
                if column == 0:
                    checkbox = self.QtWidgets.QCheckBox(value)
                    candidate_layout = self.QtWidgets.QHBoxLayout(checkbox)
                    candidate_layout.setContentsMargins(22, 0, 4, 0)
                    candidate_layout.setSpacing(6)
                    checkbox.setObjectName("StaffingV2DirectorInterviewCandidateSelect")
                    checkbox.setProperty("directorReferralId", candidate.id)
                    checkbox.setToolTip("Select candidate for deletion")
                    checkbox.setStyleSheet("QCheckBox { color: transparent; }")
                    link = self.QtWidgets.QPushButton(value)
                    link.setObjectName("StaffingV2PendingCandidateReportLink")
                    link.setFlat(True)
                    link.setCursor(self.QtCore.Qt.CursorShape.PointingHandCursor)
                    link.setToolTip(f"Open {value}'s candidate interview report")
                    link.clicked.connect(
                        lambda _checked=False, history_id=candidate.history_id, school=candidate.school:
                        self._open_candidate_report(history_id, school)
                    )
                    candidate_layout.addWidget(link, 1)
                    table.setCellWidget(row_index, column, checkbox)
            button = self.QtWidgets.QPushButton("Record Interview")
            button.setObjectName("StaffingV2DirectorInterviewRecordButton")
            button.setMinimumWidth(144)
            self._set_button_icon(button, "status_filled")
            button.clicked.connect(lambda _checked=False, item=candidate.id: self._open_director_interview_dialog(item))
            table.setCellWidget(row_index, 6, button)
        self._apply_director_pending_table_column_layout()

    def _delete_selected_director_referrals(self) -> None:
        table = self.director_interview_pending_table
        referral_ids = set()
        for row in range(table.rowCount()):
            candidate_item = table.item(row, 0)
            if candidate_item is None:
                continue
            referral_id = candidate_item.data(self.QtCore.Qt.ItemDataRole.UserRole)
            if not referral_id:
                continue
            candidate_selector = table.cellWidget(row, 0)
            is_checked = bool(candidate_selector is not None and candidate_selector.isChecked())
            is_selected = table.selectionModel().isRowSelected(row, self.QtCore.QModelIndex())
            if is_checked or is_selected:
                referral_ids.add(int(referral_id))
        for item in table.selectedItems():
            referral_id = item.data(self.QtCore.Qt.ItemDataRole.UserRole)
            if referral_id:
                referral_ids.add(int(referral_id))
        if not referral_ids:
            self.director_interview_status.setText(
                f"{len(self.pending_director_candidates)} pending / {len(self.completed_director_interviews)} completed"
            )
            return
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Delete Director Referrals",
            f"Delete {len(referral_ids)} pending director referral(s)?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        selected_candidates = [candidate for candidate in self.pending_director_candidates if candidate.id in referral_ids]
        deleted = self.service_factory().delete_pending_director_interviews(
            sorted(referral_ids),
            removed_by=self.director_referral_removal_actor,
            removal_source=self.director_referral_removal_source,
        )
        if deleted and self.director_referral_dismissal_callback is not None:
            self.director_referral_dismissal_callback(
                selected_candidates,
                self.director_referral_removal_actor,
                self.director_referral_removal_source,
            )
        self._refresh_director_interviews()
        self.director_interview_status.setText(
            f"{deleted} deleted / {len(self.pending_director_candidates)} pending / {len(self.completed_director_interviews)} completed"
        )

    def _refresh_director_history_table(self) -> None:
        table = self.director_interview_history_table
        table.setRowCount(0)
        for interview in self.completed_director_interviews:
            row_index = table.rowCount()
            table.insertRow(row_index)
            shift = ""
            if interview.proposed_shift_start and interview.proposed_shift_end:
                shift = f"{interview.proposed_shift_start} - {interview.proposed_shift_end}"
            values = [
                interview.candidate_name,
                "" if interview.interviewer_rating is None else f"{interview.interviewer_rating:g}",
                interview.completed_date,
                f"{interview.rating:g}",
                interview.decision.replace("_", "-").title(),
                interview.proposed_classroom or "-",
                shift or "-",
                interview.owner_approval_status.replace("_", " ").title(),
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem("" if column == 0 else value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, interview.id)
                item.setToolTip(value)
                table.setItem(row_index, column, item)
                if column == 0:
                    link = self.QtWidgets.QPushButton(value)
                    link.setObjectName("StaffingV2CompletedCandidateReportLink")
                    link.setFlat(True)
                    link.setCursor(self.QtCore.Qt.CursorShape.PointingHandCursor)
                    link.setToolTip(f"Open {value}'s candidate interview report")
                    link.clicked.connect(
                        lambda _checked=False, history_id=interview.history_id, school=interview.school:
                        self._open_candidate_report(history_id, school)
                    )
                    table.setCellWidget(row_index, column, link)
    def _open_candidate_report(self, history_id: str, school: str) -> None:
        if self.candidate_report_open_callback is None:
            return
        self.candidate_report_open_callback(str(history_id or ""), str(school or ""))

    def _open_director_interview_dialog(self, referral_id: int) -> None:
        candidate = next((item for item in self.pending_director_candidates if item.id == referral_id), None)
        if candidate is None:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2DirectorInterviewDialog")
        dialog.setWindowTitle("Record Director Interview")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(620, 640)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Record Director Interview", "StaffingV2DrawerTitle"))
        title_column.addWidget(self._label(f"{candidate.candidate_name} · {candidate.school} · {candidate.position}", "StaffingV2Muted"))
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2DirectorInterviewClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        form, form_layout = self._dialog_section("StaffingV2DialogSection")
        director_name = self.QtWidgets.QLineEdit()
        director_name.setObjectName("StaffingV2DirectorInterviewDirectorName")
        completed_date = self.QtWidgets.QLineEdit(date.today().isoformat())
        completed_date.setObjectName("StaffingV2DirectorInterviewDate")
        rating = self.QtWidgets.QDoubleSpinBox()
        rating.setObjectName("StaffingV2DirectorInterviewRating")
        rating.setRange(1.0, 10.0)
        rating.setDecimals(1)
        rating.setSingleStep(0.5)
        rating.setValue(8.0)
        decision = self.QtWidgets.QComboBox()
        decision.setObjectName("StaffingV2DirectorInterviewDecision")
        decision.addItems(["Hire", "No-Hire"])
        shift_start = self.QtWidgets.QLineEdit("8:00 AM")
        shift_start.setObjectName("StaffingV2DirectorInterviewShiftStartText")
        shift_end = self.QtWidgets.QLineEdit("5:00 PM")
        shift_end.setObjectName("StaffingV2DirectorInterviewShiftEndText")
        classroom = self.QtWidgets.QComboBox()
        classroom.setObjectName("StaffingV2DirectorInterviewClassroom")
        classroom.setEditable(True)
        classroom_names = sorted({row.classroom for row in self.rows if row.school == candidate.school and row.classroom})
        classroom.addItems(classroom_names or [""])
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2DirectorInterviewNotes")
        notes.setPlaceholderText("Required decision notes")
        notes.setMaximumHeight(100)
        follow_up = self.QtWidgets.QCheckBox("Follow-up needed")
        follow_up.setObjectName("StaffingV2DirectorInterviewFollowUp")
        form_layout.addLayout(self._labeled_control("Director", director_name))
        form_layout.addLayout(self._labeled_control("Interview Date", completed_date))
        form_layout.addLayout(self._labeled_control("Rating", rating))
        form_layout.addLayout(self._labeled_control("Decision", decision))
        hire_only_fields: list[Any] = []
        for object_name, label, control in (
            ("StaffingV2DirectorInterviewShiftStartRow", "Proposed Shift Start", shift_start),
            ("StaffingV2DirectorInterviewShiftEndRow", "Proposed Shift End", shift_end),
            ("StaffingV2DirectorInterviewClassroomRow", "Proposed Classroom", classroom),
        ):
            row = self.QtWidgets.QWidget()
            row.setObjectName(object_name)
            row.setLayout(self._labeled_control(label, control))
            form_layout.addWidget(row)
            hire_only_fields.append(row)
        form_layout.addWidget(follow_up)
        form_layout.addLayout(self._labeled_control("Decision Notes", notes))
        layout.addWidget(form)

        def sync_hire_only_fields() -> None:
            is_hire = decision.currentText() == "Hire"
            for field in hire_only_fields:
                field.setVisible(is_hire)

        decision.currentTextChanged.connect(sync_hire_only_fields)
        sync_hire_only_fields()

        info, info_layout = self._dialog_section("StaffingV2DialogInfo")
        info_layout.addWidget(self._label("Hire decisions store proposed classroom and shift only."))
        info_layout.addWidget(self._label("Position status and classroom assignment stay unchanged until offer approval/acceptance."))
        layout.addWidget(info)

        error = self._label("", "StaffingV2NeedNowChip")
        error.setObjectName("StaffingV2DirectorInterviewError")
        error.hide()
        layout.addWidget(error)
        layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2DirectorInterviewCancel")
        cancel.clicked.connect(dialog.close)
        save = self.QtWidgets.QPushButton("Save")
        save.setObjectName("StaffingV2DirectorInterviewSave")
        self._set_button_icon(save, "status_filled")
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

        def save_interview() -> None:
            error.hide()
            try:
                self.service_factory().record_director_interview(
                    referral_id,
                    director_name=director_name.text(),
                    completed_date=completed_date.text(),
                    rating=rating.value(),
                    decision="hire" if decision.currentText() == "Hire" else "no_hire",
                    decision_notes=notes.toPlainText(),
                    proposed_shift_start=shift_start.text() if decision.currentText() == "Hire" else "",
                    proposed_shift_end=shift_end.text() if decision.currentText() == "Hire" else "",
                    proposed_classroom=classroom.currentText() if decision.currentText() == "Hire" else "",
                    follow_up_needed=follow_up.isChecked(),
                )
            except Exception as exc:  # noqa: BLE001 - service validation is user-facing here.
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()

        save.clicked.connect(save_interview)
        dialog.show()

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
                if widget.objectName() == "StaffingV2OverviewCard":
                    widget.setObjectName("StaffingV2OverviewCardStale")
                widget.deleteLater()
        total = len(rows)
        filled = sum(1 for row in rows if row.status == "filled")
        open_count = sum(1 for row in rows if row.status in {"need_now", "replace"})
        program = next((row.classroom_program for row in rows if row.classroom_program), "-")
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
                _display_date(row.start_date),
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
        for column, width in enumerate([44, 130, 130, 170, 112, 112, 205, 210]):
            self.positions_table.setColumnWidth(column, width)

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
        self.drawer_panel.clear()
        self.drawer_panel.add_header(
            title="Position Detail",
            title_object_name="StaffingV2DrawerTitle",
            subtitle=f"{assignment.classroom} · {assignment.school} · Assignment ID #{assignment.id}",
            close_object_name="StaffingV2DrawerClose",
            close_icon=self._standard_icon("close"),
        )

        summary, summary_layout = self._panel("StaffingV2DrawerSection")
        summary_row = self.QtWidgets.QHBoxLayout()
        summary_row.addWidget(self._chip(_display_status(assignment.status), assignment.status))
        position_column = self.QtWidgets.QVBoxLayout()
        position_column.addWidget(self._label(assignment.position_name, "StaffingV2DrawerPositionName"))
        position_column.addWidget(
            self._label(
                f"Classroom {assignment.classroom}   School {assignment.school}   Program {assignment.classroom_program or '-'}   Position Type {assignment.position_type}",
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
                        f"Start date: {_display_date(assignment.start_date)}",
                        f"Permit status: {_display_permit(assignment.permit_status or 'unknown')}",
                        f"Current opened date: {_display_date(assignment.current_opened_date)}",
                        f"Current filled date: {_display_date(assignment.current_filled_date)}",
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
        self.drawer_footer_layout.addWidget(footer)
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
        self.drawer_footer_layout.addLayout(actions)
        self.drawer_panel.show_overlay()

    def _action_button(self, row: StaffingMetricRow) -> Any:
        action_key, label = _primary_action(row.status)
        button = self.QtWidgets.QToolButton()
        button.setText(label)
        button.setObjectName("StaffingV2ActionButton")
        button.setProperty("staffingAssignmentId", row.assignment_id)
        button.setProperty("staffingAction", action_key)
        button.setMinimumHeight(34)
        button.setMinimumWidth(198)
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
        if action_key == "open_position":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_position_from_drawer(item))
            return
        if action_key == "mark_coming":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_coming_dialog(item))
            return
        if action_key == "mark_dont_need":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._mark_not_needed_from_drawer(item))
            return
        if action_key == "mark_filled":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_filled_dialog(item))
            return
        if action_key == "revert_coming":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._revert_coming_from_drawer(item))
            return
        if action_key == "manage_filled":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_manage_filled_dialog(item))
            return
        if action_key == "replace_employee":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_replace_employee_dialog(item))
            return
        if action_key == "update_permit":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_update_permit_dialog(item))
            return
        if action_key == "clear_replacement":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_mark_need_now_dialog(item))
            return
        if action_key == "delete_position":
            action.triggered.connect(lambda _checked=False, item=assignment_id: self._open_delete_position_dialog(item))
            return
        if action_key == "view_history":
            action.triggered.connect(lambda _checked=False: self._show_history_view())
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
        if action_key == "open_position":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_position_from_drawer(item))
            return
        if action_key == "mark_coming":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_coming_dialog(item))
            return
        if action_key == "mark_dont_need":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._mark_not_needed_from_drawer(item))
            return
        if action_key == "mark_filled":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_filled_dialog(item))
            return
        if action_key == "revert_coming":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._revert_coming_from_drawer(item))
            return
        if action_key == "manage_filled":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_manage_filled_dialog(item))
            return
        if action_key == "replace_employee":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_replace_employee_dialog(item))
            return
        if action_key == "update_permit":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_update_permit_dialog(item))
            return
        if action_key == "clear_replacement":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_mark_need_now_dialog(item))
            return
        if action_key == "delete_position":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_delete_position_dialog(item))
            return
        if action_key == "view_history":
            button.clicked.connect(lambda _checked=False: self._show_history_view())
            return
        if action_key == "view_details":
            button.clicked.connect(lambda _checked=False, item=assignment_id: self._open_position_edit_dialog(item))
            return
        callback = self.actions.get(action_key)
        if callback is None:
            button.setEnabled(False)
            button.setToolTip("Action dialog will be implemented in a later mockup slice.")
            return
        button.clicked.connect(lambda _checked=False, item=assignment_id, cb=callback: cb(item))

    def _open_position_from_drawer(self, assignment_id: int) -> None:
        self._run_position_transition(
            assignment_id,
            lambda service: service.open_position(assignment_id),
        )

    def _revert_coming_from_drawer(self, assignment_id: int) -> None:
        self._run_position_transition(
            assignment_id,
            lambda service: service.revert_coming(assignment_id),
        )

    def _mark_not_needed_from_drawer(self, assignment_id: int) -> None:
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Mark Position Not Needed",
            "Mark this position not needed?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._run_position_transition(
            assignment_id,
            lambda service: service.mark_not_needed(assignment_id, confirmed=True),
        )

    def _run_position_transition(self, assignment_id: int, action: Callable[[StaffingService], Any]) -> None:
        try:
            result = action(self.service_factory())
        except Exception:
            self._show_position_drawer(assignment_id)
            return
        refreshed_id = int(getattr(result, "assignment_id", assignment_id) or assignment_id)
        self.refresh_all()
        self._show_position_drawer(refreshed_id)

    def _open_position_edit_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2EditPositionDialog")
        dialog.setWindowTitle("Edit Position")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(680, 560)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Edit Position", "StaffingV2DrawerTitle"))
        title_column.addWidget(
            self._label(
                f"{assignment.classroom} · {assignment.school} · Assignment ID #{assignment.id}",
                "StaffingV2Muted",
            )
        )
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2EditPositionClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        form = self.QtWidgets.QFormLayout()
        classroom = self.QtWidgets.QComboBox()
        classroom.setObjectName("StaffingV2EditPositionClassroom")
        classroom_values = sorted({row.classroom for row in self.rows if row.school == assignment.school and row.classroom})
        classroom.addItems(classroom_values or [assignment.classroom])
        classroom.setEditable(True)
        classroom.setCurrentText(assignment.classroom)
        program = self.QtWidgets.QComboBox()
        program.setObjectName("StaffingV2EditPositionProgram")
        program_values = [assignment.classroom_program, "Preschool", "Infant", "Toddler", "School Age", "Support"]
        program.addItems([value for index, value in enumerate(program_values) if value and value not in program_values[:index]])
        program.setEditable(True)
        program.setCurrentText(assignment.classroom_program or "Preschool")
        position_name = self.QtWidgets.QLineEdit(assignment.position_name)
        position_name.setObjectName("StaffingV2EditPositionName")
        position_type = self.QtWidgets.QComboBox()
        position_type.setObjectName("StaffingV2EditPositionType")
        type_values = [assignment.position_type, "Director", "Teacher", "Aide", "Floater", "Chef", "Other"]
        position_type.addItems([value for index, value in enumerate(type_values) if value and value not in type_values[:index]])
        position_type.setEditable(True)
        position_type.setCurrentText(assignment.position_type)
        status = self.QtWidgets.QComboBox()
        status.setObjectName("StaffingV2EditPositionStatus")
        for value, label in (
            ("dont_need_now", "Don't Need"),
            ("need_now", "Need Now"),
            ("coming", "Coming"),
            ("filled", "Filled"),
            ("replace", "Replace"),
        ):
            status.addItem(label, value)
        status_index = status.findData(assignment.status)
        status.setCurrentIndex(max(0, status_index))
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("StaffingV2EditPositionNotes")
        notes.setPlainText(assignment.notes)
        notes.setMaximumHeight(110)
        form.addRow("Classroom *", classroom)
        form.addRow("Program", program)
        form.addRow("Position Label / Name *", position_name)
        form.addRow("Position Type *", position_type)
        form.addRow("Status", status)
        form.addRow("Notes", notes)
        layout.addLayout(form)

        error = self._label("", "StaffingV2NeedNowChip")
        error.setObjectName("StaffingV2EditPositionError")
        error.hide()
        layout.addWidget(error)
        layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2EditPositionCancel")
        cancel.clicked.connect(dialog.close)
        submit = self.QtWidgets.QPushButton("Save Changes")
        submit.setObjectName("StaffingV2EditPositionSubmit")
        footer.addWidget(cancel)
        footer.addWidget(submit)
        layout.addLayout(footer)

        def save() -> None:
            error.hide()
            try:
                result = self.service_factory().update_assignment_details(
                    assignment.id,
                    classroom=classroom.currentText(),
                    classroom_program=program.currentText(),
                    position_name=position_name.text(),
                    position_type=position_type.currentText(),
                    status=str(status.currentData() or assignment.status),
                    person_name=assignment.person_name,
                    start_date=assignment.start_date,
                    shift_start=assignment.shift_start,
                    shift_end=assignment.shift_end,
                    permit_status=assignment.permit_status or "unknown",
                    notes=notes.toPlainText(),
                )
            except Exception as exc:  # noqa: BLE001 - show service/store validation to user.
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()
            self._show_position_drawer(int(getattr(result, "assignment_id", assignment.id) or assignment.id))

        submit.clicked.connect(save)
        dialog.show()

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
        position_type.addItems(["Director", "Teacher", "Aide", "Floater", "Chef", "Other"])
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
        error.setObjectName("StaffingV2NeedNowError")
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
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            assignment = self.store.get_assignment(result.assignment_id)
            self.dashboard_classroom_filter_state = self._default_dashboard_classroom_filter_state()
            self.search.clear()
            if assignment.school:
                self.school_selector.setCurrentText(assignment.school)
            if assignment.classroom_program:
                self.program_selector.setCurrentText(assignment.classroom_program)
            self.refresh_all()
            for index in range(self.classroom_list.count()):
                if self.classroom_list.item(index).data(self.QtCore.Qt.ItemDataRole.UserRole) == assignment.classroom:
                    self.classroom_list.setCurrentRow(index)
                    break
            self._show_position_drawer(result.assignment_id)

        school.currentIndexChanged.connect(sync_classrooms)
        initial_status.currentIndexChanged.connect(sync_status_card)
        sync_classrooms()
        sync_status_card()
        submit.clicked.connect(save)
        dialog.show()

    def _open_delete_position_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2DeletePositionDialog")
        dialog.setWindowTitle("Delete Position")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(560, 300)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        title_column.addWidget(self._label("Delete Position", "StaffingV2DrawerTitle"))
        title_column.addWidget(
            self._label(f"{assignment.position_name} - {assignment.classroom} - {assignment.school}", "StaffingV2Muted")
        )
        header.addLayout(title_column, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2DeletePositionClose")
        self._set_button_icon(close, "close")
        close.clicked.connect(dialog.close)
        header.addWidget(close)
        layout.addLayout(header)

        summary, summary_layout = self._dialog_section("StaffingV2DialogWarning")
        summary_layout.addWidget(self._label("This removes the position from the active staffing dashboard."))
        summary_layout.addWidget(self._label("Only unassigned mistaken positions can be deleted."))
        layout.addWidget(summary)

        error = self._label("", "StaffingV2NeedNowChip")
        error.setObjectName("StaffingV2DeletePositionError")
        error.hide()
        layout.addWidget(error)
        layout.addStretch(1)

        footer = self.QtWidgets.QHBoxLayout()
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2DeletePositionCancel")
        cancel.clicked.connect(dialog.close)
        confirm = self.QtWidgets.QPushButton("Delete Position")
        confirm.setObjectName("StaffingV2DeletePositionConfirm")
        self._set_button_icon(confirm, "close")
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(confirm)
        layout.addLayout(footer)

        def delete_position() -> None:
            confirm.setEnabled(False)
            try:
                self.service_factory().delete_position(assignment_id, confirmed=True)
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(_safe_staffing_error(exc))
                error.show()
                confirm.setEnabled(True)
                return
            dialog.close()
            self.drawer.hide()
            self.refresh_all()

        confirm.clicked.connect(delete_position)
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
        role.addItems(["Director", "Teacher", "Aide", "Floater", "Chef"])
        supported_roles = {"Director", "Teacher", "Aide", "Floater", "Chef"}
        role.setCurrentText(assignment.position_type if assignment.position_type in supported_roles else "Teacher")
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
        select_existing.clicked.connect(lambda _checked=False: (people_search.setFocus(), people_search.selectAll()))
        create_new.clicked.connect(lambda _checked=False: (full_name.clear(), full_name.setFocus()))
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
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()
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
            dialog.close()
            if action_key == "update_permit":
                self._open_update_permit_dialog(assignment_id)
                return
            self._open_replace_employee_dialog(assignment_id)

        permit_action.clicked.connect(lambda _checked=False: permit_option.setChecked(True))
        replace_action.clicked.connect(lambda _checked=False: replace_option.setChecked(True))
        continue_button.clicked.connect(run_selected)
        dialog.show()

    def _open_replace_employee_dialog(self, assignment_id: int) -> None:
        try:
            assignment = self.store.get_assignment(assignment_id)
        except ValueError:
            return
        if assignment.person_id is None:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingV2ReplaceEmployeeDialog")
        dialog.setWindowTitle("Replace Employee")
        dialog.setModal(False)
        dialog.setStyleSheet(APP_QSS)
        dialog.resize(760, 660)

        root = self.QtWidgets.QVBoxLayout(dialog)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = self.QtWidgets.QHBoxLayout()
        title_block = self.QtWidgets.QVBoxLayout()
        title_block.addWidget(self._label("Replace Employee", "StaffingV2DrawerTitle"))
        title_block.addWidget(
            self._label("Mark the current employee as leaving and reopen this position.", "StaffingV2Muted")
        )
        header.addLayout(title_block, 1)
        close = self.QtWidgets.QPushButton("")
        close.setObjectName("StaffingV2ReplaceClose")
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
        form_section, form_layout = self._dialog_section()
        form_layout.addWidget(self._label("Replacement Details", "StaffingV2SectionTitle"))
        form = self.QtWidgets.QGridLayout()

        class ReplaceDateEdit(self.QtWidgets.QDateEdit):
            def _open_calendar(date_self: Any) -> None:
                calendar = date_self.calendarWidget()
                calendar.setSelectedDate(date_self.date())
                popup = calendar.parentWidget()
                target = popup or calendar
                target.move(date_self.mapToGlobal(self.QtCore.QPoint(0, date_self.height())))
                target.show()
                target.raise_()

            def mousePressEvent(date_self: Any, event: Any) -> None:  # noqa: N802 - Qt override.
                super(ReplaceDateEdit, date_self).mousePressEvent(event)
                if event.button() != self.QtCore.Qt.MouseButton.LeftButton or not date_self.calendarPopup():
                    return
                self.QtCore.QTimer.singleShot(0, date_self._open_calendar)

        today = self.QtCore.QDate.currentDate()
        notice = ReplaceDateEdit()
        notice.setObjectName("StaffingV2ReplaceNotice")
        notice.setCalendarPopup(True)
        notice.setDisplayFormat("yyyy-MM-dd")
        notice.setDate(today)
        final_day = ReplaceDateEdit()
        final_day.setObjectName("StaffingV2ReplaceFinalDay")
        final_day.setCalendarPopup(True)
        final_day.setDisplayFormat("yyyy-MM-dd")
        final_day.setDate(today)
        reason = self.QtWidgets.QComboBox()
        reason.setObjectName("StaffingV2ReplaceReason")
        reason.addItems(["Resignation", "Termination", "Leave of absence", "Transfer", "Other"])
        fields = [
            ("Notice Given *", notice, 0),
            ("Final Working Day *", final_day, 1),
            ("Reason (optional)", reason, 2),
        ]
        for label, widget, row in fields:
            wrap = self.QtWidgets.QVBoxLayout()
            wrap.addWidget(self._label(label, "StaffingV2Muted"))
            wrap.addWidget(widget)
            form.addLayout(wrap, row, 0)
        form_layout.addLayout(form)
        body.addWidget(form_section, 2)

        right = self.QtWidgets.QVBoxLayout()
        validation, validation_layout = self._dialog_section()
        validation_layout.addWidget(self._label("Validation / Requirements", "StaffingV2SectionTitle"))
        for line in (
            "✓ Notice Given is required",
            "✓ Final Working Day is required",
            "✓ Employee record found",
            "✓ Position status will become Replace",
        ):
            validation_layout.addWidget(self._label(line))
        right.addWidget(validation)
        happens, happens_layout = self._dialog_section("StaffingV2DialogInfo")
        happens_layout.addWidget(self._label("What will happen on save", "StaffingV2SectionTitle"))
        for line in (
            "People.Active will be set to false",
            "Notice Given and Final Working Day will save to People",
            "Assignment status changes to Replace",
            "A new AssignmentHistory open cycle is created",
        ):
            happens_layout.addWidget(self._label(f"✓ {line}"))
        right.addWidget(happens)
        right.addStretch(1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        warning, warning_layout = self._dialog_section("StaffingV2DialogWarning")
        warning_layout.addWidget(
            self._label("This action opens a replacement need while preserving the current employee on the record.")
        )
        root.addWidget(warning)
        error = self._label("", "StaffingV2NeedNowChip")
        error.setObjectName("StaffingV2ReplaceError")
        error.hide()
        root.addWidget(error)

        footer = self.QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("StaffingV2ReplaceCancel")
        cancel.clicked.connect(dialog.close)
        submit = self.QtWidgets.QPushButton("Confirm Replace")
        submit.setObjectName("StaffingV2ReplaceSubmit")
        footer.addWidget(cancel)
        footer.addWidget(submit)
        root.addLayout(footer)

        def save() -> None:
            try:
                self.service_factory().mark_replacing(
                    assignment_id,
                    notice_given=notice.date().toString("yyyy-MM-dd"),
                    final_working_day=final_day.date().toString("yyyy-MM-dd"),
                )
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
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
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()
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
            submit.setEnabled(False)
            if not clear_person.isChecked():
                error.setText("Clear assigned person is required for this transition.")
                error.show()
                submit.setEnabled(True)
                return
            try:
                self.service_factory().clear_replacement(assignment_id)
            except Exception as exc:  # noqa: BLE001 - show service validation error in dialog.
                try:
                    current = self.store.get_assignment(assignment_id)
                except Exception:
                    current = None
                if current is not None and current.status == "need_now":
                    dialog.close()
                    self.refresh_all()
                    self._show_position_drawer(assignment_id)
                    return
                error.setText(_safe_staffing_error(exc))
                error.show()
                submit.setEnabled(True)
                return
            dialog.close()
            self.refresh_all()
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
                error.setText(_safe_staffing_error(exc))
                error.show()
                return
            dialog.close()
            self.refresh_all()
            self._show_position_drawer(assignment_id)

        submit.clicked.connect(save)
        dialog.show()

    def _metric_card(self, label: str, value: str, accessible_text: str, object_name: str = "StaffingV2MetricCard") -> Any:
        card, layout = self._panel(object_name)
        card.setAccessibleName(accessible_text)
        icon_row = self.QtWidgets.QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_object_name = "StaffingV2ClassroomsMetricIcon" if object_name == "StaffingV2ClassroomsMetricCard" else "StaffingV2CardIcon"
        icon_row.addWidget(self._icon_label(_metric_icon_key(label), icon_object_name))
        icon_row.addWidget(self._label(label, "StaffingV2Muted"), 1)
        layout.addLayout(icon_row)
        value_widget = self._label(value, "StaffingV2MetricValue")
        layout.addWidget(value_widget)
        return card

    def _summary_chip(self, label: str, value: str, accessible_text: str) -> Any:
        card, layout = self._panel("StaffingV2MetricCard")
        card.setAccessibleName(accessible_text)
        normalized_accessible = accessible_text.casefold()
        variant = (
            "success"
            if "validation healthy" in normalized_accessible
            else "danger"
            if "open > 7" in normalized_accessible or "validation:" in normalized_accessible
            else "info"
        )
        card.setProperty("staffingV2SummaryVariant", variant)
        if label == "Validation":
            card.setCursor(self.QtCore.Qt.CursorShape.PointingHandCursor)
            card.mousePressEvent = lambda _event: self._show_validation_view()  # type: ignore[method-assign]
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
        frame.setProperty("staffingV2StatusFill", status)
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

    def _detail_panel_card(self, object_name: str, title: str = "") -> tuple[Any, Any]:
        frame, layout = self._panel(object_name)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        frame.setSizePolicy(
            self.QtWidgets.QSizePolicy.Policy.Preferred,
            self.QtWidgets.QSizePolicy.Policy.Maximum,
        )
        if title:
            layout.addWidget(self._label(title, "StaffingV2SectionTitle"))
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
                widget.setParent(None)
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
    return f"Need {need} · Replace {replace}\nComing {coming} · Filled {filled}\nDon't Need {dont_need}"


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
            ("StaffingV2DrawerMarkDontNeed", "Mark Not Needed", "mark_dont_need"),
            ("StaffingV2DrawerDeletePosition", "Delete Position", "delete_position"),
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
        ("StaffingV2DrawerDeletePosition", "Delete Position", "delete_position"),
        ("StaffingV2DrawerEditPosition", "Edit Position", "view_details"),
        ("StaffingV2DrawerViewHistory", "View Full History", "view_history"),
    ]


def _drawer_action_icon_key(action_key: str) -> str:
    if action_key in {"mark_coming", "mark_filled", "revert_coming"}:
        return "status_pending"
    if action_key in {"mark_dont_need", "clear_replacement", "open_position", "delete_position"}:
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
        return "Filled"
    if statuses and statuses <= {"dont_need_now"}:
        return "Don't Need"
    if "filled" in statuses:
        return "Filled"
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
        if row.status in {"need_now", "replace"} and _is_placeholder_date(row.start_date):
            issues.append(
                {
                    **base,
                    "issue": "Placeholder start date",
                    "type": "Lifecycle",
                    "severity": "Warning",
                    "details": f"{row.position_name} has a seed placeholder date",
                }
            )
        if row.person_name and row.permit_status in {"", "unknown", "no_permit_or_application"} and row.status in {"coming", "filled", "replace"}:
            issues.append(
                {
                    **base,
                    "issue": "Permit status needs review",
                    "type": "Compliance",
                    "severity": "Warning" if row.permit_status == "no_permit_or_application" else "Info",
                    "details": f"{row.position_name} permit status needs review",
                }
            )
    severity_order = {"Critical": 0, "Warning": 1, "Info": 2}
    issue_order = {
        "Unfilled Need Now position": 0,
        "Coming position missing start date": 1,
        "Placeholder start date": 2,
        "Permit status needs review": 3,
    }
    return sorted(
        issues,
        key=lambda issue: (
            severity_order.get(issue["severity"], 3),
            issue["classroom"],
            issue_order.get(issue["issue"], 99),
            issue["issue"],
        ),
    )


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


def _permit_status_from_label(label: str) -> str:
    return {
        "Unknown": "unknown",
        "No Permit": "no_permit_or_application",
        "Permit in Process": "permit_in_process",
        "Teacher Permit": "teacher_permit_approved",
        "No Units Needed": "no_units_needed",
    }.get(str(label or "").strip(), "unknown")


def _permit_chip_status(status: str) -> str:
    return {
        "permit_in_process": "coming",
        "teacher_permit_approved": "filled",
        "no_units_needed": "filled",
        "no_permit_or_application": "replace",
    }.get(status or "unknown", "dont_need_now")


def _row_has_permit_issue(row: StaffingMetricRow) -> bool:
    if not row.person_name:
        return False
    return row.permit_status in {"", "unknown", "no_permit_or_application"}


def _parse_int_or_none(value: str) -> int | None:
    stripped = str(value or "").strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def _format_notification_recipients(recipients: list[NotificationRecipient]) -> str:
    parts: list[str] = []
    for recipient in recipients:
        email = str(recipient.email or "").strip()
        if not email:
            continue
        label = str(recipient.role_label or recipient.name or "").strip()
        parts.append(f"{label} <{email}>" if label else email)
    return ", ".join(parts)


def _parse_notification_recipients(value: str) -> list[NotificationRecipient]:
    recipients: list[NotificationRecipient] = []
    for raw_part in str(value or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        name = ""
        email = part
        if "<" in part and part.endswith(">"):
            name, email = part[:-1].split("<", 1)
            name = name.strip()
            email = email.strip()
        recipients.append(NotificationRecipient(email=email, name=name, role_label=name))
    return recipients


def _notification_role_recipient(role_key: str) -> NotificationRecipient:
    normalized = str(role_key or "").strip()
    if normalized == "candidate":
        return NotificationRecipient(
            name="Candidate",
            role_label="Candidate",
            recipient_type="role",
            role_key="candidate",
        )
    if normalized == "hiring_manager":
        return NotificationRecipient(
            email="",
            name="Hiring Manager",
            role_label="Hiring Manager",
            recipient_type="role",
            role_key="hiring_manager",
        )
    if normalized == "executive_director":
        return NotificationRecipient(
            name="Executive Director",
            role_label="Executive Director",
            recipient_type="role",
            role_key="executive_director",
        )
    return NotificationRecipient(
        email="",
        name="Director",
        role_label="Director",
        recipient_type="role",
        role_key="director",
    )


def _notification_recipient_key(recipient: NotificationRecipient) -> str:
    recipient_type = str(recipient.recipient_type or "email").strip() or "email"
    if recipient_type == "role":
        return f"role:{str(recipient.role_key or '').strip()}"
    return f"email:{str(recipient.email or '').strip().casefold()}"


def _notification_recipient_display(recipient: NotificationRecipient) -> str:
    if str(recipient.recipient_type or "email") == "role":
        if recipient.role_key == "candidate":
            return "Candidate (payload email)"
        if recipient.role_key == "hiring_manager":
            return f"Hiring Manager <{HIRING_MANAGER_EMAIL}>"
        if recipient.role_key == "executive_director":
            return f"Executive Director <{EXECUTIVE_DIRECTOR_EMAIL}>"
        return "Director (school-based)"
    label = str(recipient.role_label or recipient.name or "Recipient").strip()
    return f"{label} <{recipient.email}>" if label else str(recipient.email or "").strip()


def _notification_recipient_remove_suffix(recipient: NotificationRecipient) -> str:
    if str(recipient.recipient_type or "email") == "role":
        return _safe_object_suffix(f"role_{recipient.role_key}")
    return _safe_object_suffix(str(recipient.email or ""))


def _show_rule_in_staffing_v2_notifications(rule: NotificationRule) -> bool:
    hidden_placeholder_events = {"offer.welcome_email_sent"}
    hidden_placeholder_prefixes = ("onboarding.",)
    event_type = str(rule.event_type or "")
    if event_type not in hidden_placeholder_events and not event_type.startswith(hidden_placeholder_prefixes):
        return True
    return bool(rule.subject_template or rule.body_template or rule.recipients)


def _safe_object_suffix(value: str) -> str:
    suffix = "".join(character if character.isalnum() else "_" for character in str(value or "").casefold())
    return suffix.strip("_") or "item"




def _notification_preview_sample() -> dict[str, str]:
    return {
        "candidate": "Jordan Lee",
        "candidate_email": "jordan@example.org",
        "candidate_name": "Jordan Lee",
        "classroom": "Harmony 1",
        "company_name": "Launch Pad Learning",
        "department": "Preschool",
        "hiring_manager_name": "Alex Morgan",
        "location": "Hawthorne",
        "notice_given": date.today().isoformat(),
        "notice_date": date.today().isoformat(),
        "date_notice_given": date.today().isoformat(),
        "final_working_day": (date.today() + timedelta(days=14)).isoformat(),
        "final_day": (date.today() + timedelta(days=14)).isoformat(),
        "last_working_day": (date.today() + timedelta(days=14)).isoformat(),
        "permit_status": "Permit in Process",
        "permit_effective_date": date.today().isoformat(),
        "permit_documentation_received": "Yes",
        "permit_notes": "Permit file received.",
        "person_name": "Imani Carter",
        "position_name": "Teacher 1",
        "position": "Teacher 1",
        "position_type": "Teacher",
        "program": "Preschool",
        "ece_units": "24",
        "ece_units_completed": "24",
        "degree": "BA",
        "degree_type": "BA",
        "years_experience": "5",
        "experience_years": "5",
        "interview_answer_1": "I use routines and calm redirection.",
        "interview_answers_summary": "Classroom guidance: I use routines and calm redirection.",
        "deepseek_summary": "Strong classroom presence and clear family communication.",
        "deepseek_recommendation": "Recommend hire.",
        "deepseek_concerns": "Needs permit follow-up.",
        "recruiter_name": "Taylor Smith",
        "reply_by_date": (date.today() + timedelta(days=3)).isoformat(),
        "school": "Hawthorne",
        "school_code": "HAW",
        "school_location": "Hawthorne",
        "offer_path": "C:/Offers/Jordan Lee Offer.docx",
        "offer_pdf_path": "C:/Offers/Jordan Lee Offer.pdf",
        "onboarding_guide_path": "C:/Offers/New Employee Onboarding Guide.pdf",
        "start_date": date.today().isoformat(),
    }


def _notification_schedule_text(rule: NotificationRule) -> str:
    if rule.trigger_timing != "date_offset":
        return "Event"
    days = abs(int(rule.offset_days))
    if int(rule.offset_days) < 0:
        direction = "before"
    elif int(rule.offset_days) > 0:
        direction = "after"
    else:
        direction = "on"
    if direction == "on":
        return f"On {rule.date_field or 'reference date'}"
    return f"{days} day{'s' if days != 1 else ''} {direction} {rule.date_field or 'reference date'}"


def _notification_attachment_fields(event_type: str) -> tuple[str, ...]:
    normalized = str(event_type or "").removesuffix(".test")
    if normalized == "offer.approved":
        return ("offer_pdf_path",)
    if normalized == "offer.accepted":
        return ("onboarding_guide_path",)
    return ()




def _safe_notification_error(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    lowered = text.casefold()
    sensitive_markers = ["password", "passwd", "token", "secret", "authorization", "auth"]
    if any(marker in lowered for marker in sensitive_markers):
        return "Error details redacted."
    return text


def _safe_staffing_error(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Could not save staffing change. Check required fields and try again."
    lowered = text.casefold()
    sensitive_markers = ["password", "passwd", "token", "secret", "authorization", "auth", "smtp"]
    if any(marker in lowered for marker in sensitive_markers):
        return "Could not save staffing change. Error details redacted."
    if len(text) > 180:
        return "Could not save staffing change. Check required fields and try again."
    return text


def _notification_validation_text(rule: NotificationRule) -> str:
    issues = validate_notification_rule(rule)
    return "No issues found" if not issues else "; ".join(issue.message for issue in issues)




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
        "healthy": "status_filled",
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
    if not text or _is_placeholder_date(text):
        return "-"
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return text
    return parsed.strftime("%B %d, %Y").replace(" 0", " ")


def _is_placeholder_date(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.startswith("1970-01-01")


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
            ("Mark Not Needed", "mark_dont_need"),
            ("Delete Position", "delete_position"),
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
            ("Delete Position", "delete_position"),
            ("View Details", "view_details"),
        ],
    }.get(status, [("View Details", "view_details")])


def _status_color(status: str) -> str:
    return {
        "need_now": "#fee2e2",
        "replace": "#ffedd5",
        "coming": "#fef3c7",
        "filled": "#dcfce7",
        "healthy": "#dcfce7",
        "dont_need_now": "#f1f5f9",
    }.get(status, "#ffffff")


def _chip_object_name(status: str) -> str:
    return {
        "need_now": "StaffingV2NeedNowChip",
        "replace": "StaffingV2ReplaceChip",
        "coming": "StaffingV2ComingChip",
        "filled": "StaffingV2FilledChip",
        "healthy": "StaffingV2HealthyChip",
        "dont_need_now": "StaffingV2NeutralChip",
    }.get(status, "StaffingV2NeutralChip")
