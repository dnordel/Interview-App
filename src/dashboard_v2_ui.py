from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SEMANTIC_COLORS = {
    "active": ("#dbeafe", "#1d4ed8"),
    "success": ("#dcfce7", "#166534"),
    "warning": ("#fef3c7", "#92400e"),
    "critical": ("#fee2e2", "#991b1b"),
    "neutral": ("#f1f5f9", "#475569"),
}


def display_role(role: str) -> str:
    normalized = str(role or "").strip()
    if not normalized:
        return "-"
    if normalized.casefold() == "assistant_director_enrollment_specialist":
        return "Assistant Director"
    return normalized.replace("_", " ").title()


def role_badge_key(role: str) -> str:
    normalized = str(role or "").strip().replace("_", " ").casefold()
    if "director" in normalized:
        return "director"
    if "support" in normalized or "specialist" in normalized:
        return "support"
    if "infant" in normalized or "toddler" in normalized:
        return "infant_toddler"
    if "preschool" in normalized:
        return "preschool"
    if "teacher" in normalized:
        return "teacher"
    if "aide" in normalized or "assistant" in normalized:
        return "aide"
    return "other"


def apply_dashboard_v2_light_theme(QtWidgets: Any, QtGui: Any, app: Any | None = None) -> None:
    application = app or QtWidgets.QApplication.instance()
    if application is None:
        return
    style_factory = getattr(QtWidgets, "QStyleFactory", None)
    if style_factory is not None and "Fusion" in style_factory.keys():
        application.setStyle("Fusion")
    role = QtGui.QPalette.ColorRole
    group = QtGui.QPalette.ColorGroup
    color = QtGui.QColor
    palette = QtGui.QPalette()
    for color_group in (group.Active, group.Inactive):
        palette.setColor(color_group, role.Window, color("#f8fafc"))
        palette.setColor(color_group, role.WindowText, color("#0f172a"))
        palette.setColor(color_group, role.Base, color("#ffffff"))
        palette.setColor(color_group, role.AlternateBase, color("#f8fafc"))
        palette.setColor(color_group, role.Text, color("#0f172a"))
        palette.setColor(color_group, role.Button, color("#ffffff"))
        palette.setColor(color_group, role.ButtonText, color("#0f172a"))
        palette.setColor(color_group, role.Highlight, color("#2563eb"))
        palette.setColor(color_group, role.HighlightedText, color("#ffffff"))
    application.setPalette(palette)
    application.setProperty("_dashboard_v2_forced_light_theme", True)


def configure_dashboard_v2_scroll_areas(QtWidgets: Any, root: Any) -> None:
    for area in root.findChildren(QtWidgets.QAbstractScrollArea):
        if isinstance(area, QtWidgets.QAbstractItemView):
            area.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
            area.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        area.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        area.verticalScrollBar().setSingleStep(24)
        area.horizontalScrollBar().setSingleStep(24)


@dataclass(frozen=True)
class DashboardV2PageRegistration:
    section_id: str
    page_id: str
    label: str
    icon_key: str


class DashboardV2Shell:
    def __init__(self, *, QtCore: Any, QtGui: Any, QtWidgets: Any) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        apply_dashboard_v2_light_theme(QtWidgets, QtGui)
        self.current_page_id = ""
        self.navigation_locked = False
        self.registrations: dict[str, DashboardV2PageRegistration] = {}
        self.page_buttons: dict[str, Any] = {}
        self.section_layouts: dict[str, Any] = {}
        self._full_button_labels: dict[str, str] = {}

        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName("DashboardV2SharedPage")
        root = QtWidgets.QHBoxLayout(self.widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QtWidgets.QFrame()
        self.sidebar.setObjectName("DashboardV2Sidebar")
        self.sidebar.setFixedWidth(252)
        self.sidebar_layout = QtWidgets.QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(16, 22, 16, 18)
        self.sidebar_layout.setSpacing(8)
        brand = QtWidgets.QLabel("Launch Pad Learning")
        brand.setObjectName("DashboardV2Brand")
        self.sidebar_layout.addWidget(brand)
        self.sidebar_layout.addSpacing(12)
        self.sidebar_layout.addStretch(1)
        root.addWidget(self.sidebar)

        content = QtWidgets.QWidget()
        content.setObjectName("DashboardV2Content")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        self.page_stack = QtWidgets.QStackedWidget()
        self.page_stack.setObjectName("DashboardV2PageStack")
        content_layout.addWidget(self.page_stack, 1)
        root.addWidget(content, 1)
        self.widget.setStyleSheet(_DASHBOARD_V2_QSS)

    def add_section(self, section_id: str, label: str) -> None:
        if section_id in self.section_layouts:
            raise ValueError(f"Dashboard section already exists: {section_id}")
        insert_at = max(0, self.sidebar_layout.count() - 1)
        section = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading = self.QtWidgets.QLabel(label)
        heading.setObjectName("DashboardV2SidebarSection")
        heading.setProperty("fullLabel", label)
        layout.addWidget(heading)
        self.sidebar_layout.insertWidget(insert_at, section)
        self.section_layouts[section_id] = layout

    def add_page(
        self,
        section_id: str,
        page_id: str,
        label: str,
        widget: Any,
        *,
        icon_key: str = "",
    ) -> Any:
        if section_id not in self.section_layouts:
            raise ValueError(f"Unknown dashboard section: {section_id}")
        if page_id in self.registrations:
            raise ValueError(f"Dashboard page already exists: {page_id}")
        button = self.QtWidgets.QPushButton(label)
        button.setObjectName(f"DashboardV2Nav_{page_id}")
        button.setToolTip(label)
        button.setProperty("dashboardV2ActiveNav", False)
        button.clicked.connect(lambda _checked=False, key=page_id: self.show_page(key))
        self.section_layouts[section_id].addWidget(button)
        self.page_stack.addWidget(widget)
        self.registrations[page_id] = DashboardV2PageRegistration(section_id, page_id, label, icon_key)
        self.page_buttons[page_id] = button
        self._full_button_labels[page_id] = label
        if not self.current_page_id:
            self.show_page(page_id)
        return button

    def show_page(self, page_id: str) -> None:
        if page_id not in self.registrations:
            raise ValueError(f"Unknown dashboard page: {page_id}")
        if self.navigation_locked and page_id != self.current_page_id:
            return
        registration = self.registrations[page_id]
        index = list(self.registrations).index(page_id)
        self.page_stack.setCurrentIndex(index)
        self.current_page_id = registration.page_id
        for key, button in self.page_buttons.items():
            button.setProperty("dashboardV2ActiveNav", key == page_id)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_navigation_mode(self, mode: str) -> None:
        if mode not in {"full", "rail"}:
            raise ValueError("Dashboard navigation mode must be full or rail.")
        rail = mode == "rail"
        self.sidebar.setFixedWidth(64 if rail else 252)
        for key, button in self.page_buttons.items():
            label = self._full_button_labels[key]
            button.setText(label[:1] if rail else label)

    def set_navigation_locked(self, locked: bool) -> None:
        self.navigation_locked = bool(locked)
        for key, button in self.page_buttons.items():
            button.setEnabled(not locked or key == self.current_page_id)

    def panel(self, object_name: str = "DashboardV2Panel") -> tuple[Any, Any]:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName(object_name)
        layout = self.QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        return frame, layout

    def semantic_chip(self, text: str, state: str) -> Any:
        if state not in SEMANTIC_COLORS:
            raise ValueError(f"Unknown semantic state: {state}")
        chip = self.QtWidgets.QLabel(text)
        chip.setObjectName("DashboardV2SemanticChip")
        chip.setProperty("semanticState", state)
        chip.setAccessibleName(f"{text}: {state}")
        return chip


class DashboardV2OverlayPanel:
    def __init__(self, *, QtCore: Any, QtWidgets: Any, parent: Any, object_name: str, width: int = 430) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.parent = parent
        self.preferred_width = width
        self.frame = QtWidgets.QFrame(parent)
        self.frame.setObjectName(object_name)
        self.frame.hide()
        root = QtWidgets.QVBoxLayout(self.frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 12, 14, 12)
        self.body_layout.setSpacing(8)
        self.scroll_area.setWidget(self.body)
        root.addWidget(self.scroll_area, 1)
        self.footer = QtWidgets.QWidget()
        self.footer_layout = QtWidgets.QVBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(14, 8, 14, 12)
        root.addWidget(self.footer)
        self.reposition()

    def show_overlay(self) -> None:
        self.reposition()
        self.frame.show()
        self.frame.raise_()
        configure_dashboard_v2_scroll_areas(self.QtWidgets, self.frame)

    def hide(self) -> None:
        self.frame.hide()

    def reposition(self) -> None:
        width = min(self.preferred_width, max(320, self.parent.width()))
        self.frame.setGeometry(max(0, self.parent.width() - width), 0, width, max(1, self.parent.height()))


_DASHBOARD_V2_QSS = """
#DashboardV2SharedPage, #DashboardV2Content { background: #f8fafc; color: #0f172a; }
#DashboardV2Sidebar { background: #ffffff; border-right: 1px solid #e2e8f0; }
#DashboardV2Brand { color: #0f172a; font-size: 20px; font-weight: 800; }
#DashboardV2SidebarSection { color: #64748b; font-size: 11px; font-weight: 800; }
QPushButton[dashboardV2ActiveNav="false"] { background: transparent; color: #334155; border: 0; border-radius: 8px; padding: 10px 12px; text-align: left; font-weight: 600; }
QPushButton[dashboardV2ActiveNav="true"] { background: #eaf2ff; color: #2563eb; border: 0; border-radius: 8px; padding: 10px 12px; text-align: left; font-weight: 800; }
QFrame#DashboardV2Panel, QFrame#DashboardV2MetricCard, QFrame#DashboardV2DetailCard { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; }
QLabel#DashboardV2SemanticChip { border-radius: 10px; padding: 4px 8px; font-weight: 700; }
QLabel#DashboardV2SemanticChip[semanticState="active"] { background: #dbeafe; color: #1d4ed8; }
QLabel#DashboardV2SemanticChip[semanticState="success"] { background: #dcfce7; color: #166534; }
QLabel#DashboardV2SemanticChip[semanticState="warning"] { background: #fef3c7; color: #92400e; }
QLabel#DashboardV2SemanticChip[semanticState="critical"] { background: #fee2e2; color: #991b1b; }
QLabel#DashboardV2SemanticChip[semanticState="neutral"] { background: #f1f5f9; color: #475569; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus, QTableWidget:focus { border: 2px solid #2563eb; }
"""
