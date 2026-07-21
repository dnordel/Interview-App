from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dashboard_v2_ui import DashboardV2OverlayPanel, display_role, role_badge_key
from hiring_pipeline import HiringApplication, HiringStage, HiringWorkflowService


STAGE_LABELS = {
    HiringStage.INITIAL_INTERVIEW: "Initial Interview",
    HiringStage.DIRECTOR_REVIEW: "Director Review",
    HiringStage.OFFER_DRAFT: "Offer Draft",
    HiringStage.EXECUTIVE_APPROVAL: "Executive Approval",
    HiringStage.OFFER_SENT: "Offer Sent",
    HiringStage.ACCEPTED: "Accepted",
    HiringStage.CLOSED: "Closed",
}

NEXT_ACTIONS = {
    HiringStage.INITIAL_INTERVIEW: "Resume interview",
    HiringStage.DIRECTOR_REVIEW: "Schedule director review",
    HiringStage.OFFER_DRAFT: "Create offer draft",
    HiringStage.EXECUTIVE_APPROVAL: "Review approval status",
    HiringStage.OFFER_SENT: "Track candidate response",
    HiringStage.ACCEPTED: "Ready for onboarding",
    HiringStage.CLOSED: "No action required",
}


class HiringOfferApprovalDialog:
    """Exact-PDF approval surface with fail-closed readiness gating."""

    def __init__(
        self,
        *,
        QtCore: Any,
        QtPdf: Any,
        QtPdfWidgets: Any,
        QtWidgets: Any,
        parent: Any,
        title: str,
        summary: str,
        rendered_email: str,
        pdf_path: Path,
        hourly_pay: str = "",
        approve_label: str = "Approve and send",
    ) -> None:
        self.QtPdf = QtPdf
        self.dialog = QtWidgets.QDialog(parent)
        self.dialog.setWindowTitle(title)
        self.dialog.resize(920, 720)
        layout = QtWidgets.QVBoxLayout(self.dialog)
        summary_label = QtWidgets.QLabel(summary)
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)
        self.status_label = QtWidgets.QLabel("Loading approved PDF…")
        self.status_label.setObjectName("HiringOfferPdfStatus")
        layout.addWidget(self.status_label)
        self.pdf_document = QtPdf.QPdfDocument(self.dialog)
        self.pdf_view = QtPdfWidgets.QPdfView(self.dialog)
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QtPdfWidgets.QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitToWidth)
        layout.addWidget(self.pdf_view, 1)
        layout.addWidget(QtWidgets.QLabel("Rendered candidate email"))
        self.email_preview = QtWidgets.QPlainTextEdit(rendered_email)
        self.email_preview.setReadOnly(True)
        self.email_preview.setMaximumHeight(110)
        layout.addWidget(self.email_preview)
        layout.addWidget(QtWidgets.QLabel("Hourly pay"))
        self.pay_input = QtWidgets.QLineEdit(str(hourly_pay))
        self.pay_input.setPlaceholderText("Required hourly pay")
        layout.addWidget(self.pay_input)
        self._initial_hourly_pay = self.hourly_pay()
        self._change_pay_requested = False
        layout.addWidget(QtWidgets.QLabel("Approver name"))
        self.approver_input = QtWidgets.QLineEdit()
        self.approver_input.setPlaceholderText("Required approver name")
        layout.addWidget(self.approver_input)
        controls = QtWidgets.QHBoxLayout()
        controls.addStretch(1)
        cancel_button = QtWidgets.QPushButton("Cancel")
        self.change_pay_button = QtWidgets.QPushButton("Change Pay & Approve")
        self.change_pay_button.setEnabled(False)
        self.approve_button = QtWidgets.QPushButton(approve_label)
        self.approve_button.setDefault(True)
        self.approve_button.setEnabled(False)
        controls.addWidget(cancel_button)
        controls.addWidget(self.change_pay_button)
        controls.addWidget(self.approve_button)
        layout.addLayout(controls)
        cancel_button.clicked.connect(self.dialog.reject)
        self.approve_button.clicked.connect(self.dialog.accept)
        self.change_pay_button.clicked.connect(self._accept_pay_change)
        self.approver_input.textChanged.connect(self._sync_readiness)
        self.pay_input.textChanged.connect(self._sync_readiness)
        self.pdf_document.statusChanged.connect(self._sync_readiness)
        self.pdf_document.load(str(Path(pdf_path).resolve()))
        self._sync_readiness()

    def _sync_readiness(self, *_args: Any) -> None:
        ready = (
            self.pdf_document.status() == self.QtPdf.QPdfDocument.Status.Ready
            and self.pdf_document.pageCount() > 0
        )
        status = self.pdf_document.status()
        if ready:
            text = f"Approved PDF ready · {self.pdf_document.pageCount()} page(s)"
        elif status == self.QtPdf.QPdfDocument.Status.Error:
            text = "Approved PDF failed to load"
        else:
            text = "Loading approved PDF…"
        self.status_label.setText(text)
        approver_ready = ready and bool(self.approver_name())
        self.approve_button.setEnabled(approver_ready)
        self.change_pay_button.setEnabled(
            approver_ready
            and self._valid_hourly_pay()
            and self.hourly_pay() != self._initial_hourly_pay
        )

    def _valid_hourly_pay(self) -> bool:
        try:
            return Decimal(self.hourly_pay()) > 0
        except (InvalidOperation, ValueError):
            return False

    def _accept_pay_change(self) -> None:
        self._change_pay_requested = True
        self.dialog.accept()

    def approver_name(self) -> str:
        return self.approver_input.text().strip()

    def hourly_pay(self) -> str:
        return self.pay_input.text().strip()

    def change_pay_requested(self) -> bool:
        return self._change_pay_requested

    def exec(self) -> bool:
        return self.dialog.exec() == self.dialog.DialogCode.Accepted

    def close(self) -> None:
        self.pdf_document.close()
        self.dialog.close()


class HiringInterviewGuidePage:
    """Internal Hiring v2 route owner for pipeline, interview, and closeout views."""

    def __init__(
        self,
        *,
        QtWidgets: Any,
        pipeline_widget: Any | None,
        interview_widget: Any,
        closeout_widget: Any | None = None,
        initial_route: str = "pipeline",
    ) -> None:
        if initial_route not in {"pipeline", "interview"}:
            raise ValueError("Hiring interview initial route must be pipeline or interview.")
        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)
        self.pipeline_widget = pipeline_widget
        self.interview_widget = interview_widget
        self.closeout_widget = closeout_widget or interview_widget
        if self.pipeline_widget is not None:
            self.stack.addWidget(self.pipeline_widget)
        self.stack.addWidget(self.interview_widget)
        if self.closeout_widget is not self.interview_widget:
            self.stack.addWidget(self.closeout_widget)
        self.current_route = "pipeline"
        if initial_route == "interview" or self.pipeline_widget is None:
            self.show_interview()

    def show_pipeline(self) -> None:
        if self.pipeline_widget is None:
            self.show_interview()
            return
        self.current_route = "pipeline"
        self.stack.setCurrentWidget(self.pipeline_widget)

    def show_interview(self) -> None:
        self.current_route = "interview"
        self.stack.setCurrentWidget(self.interview_widget)

    def show_closeout(self) -> None:
        self.current_route = "closeout"
        self.stack.setCurrentWidget(self.closeout_widget)


class HiringWorkspaceV2Page:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtWidgets: Any,
        service: HiringWorkflowService,
        actions: dict[str, Any] | None = None,
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.service = service
        self.actions = actions or {}
        self.applications: list[HiringApplication] = []
        self.visible_applications: list[HiringApplication] = []
        self.selected_application_id = ""
        self.active_stage: HiringStage | None = None
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName("HiringWorkspaceV2Page")
        self.interviews_widget = self.widget
        self._build()
        self.candidates_widget = self._build_candidates_page()
        self.offers_widget = self._build_offers_page()
        self.refresh()

    def _build(self) -> None:
        root = self.QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        header = self.QtWidgets.QHBoxLayout()
        title_box = self.QtWidgets.QVBoxLayout()
        title = self.QtWidgets.QLabel("Hiring workspace")
        title.setObjectName("HiringV2Title")
        subtitle = self.QtWidgets.QLabel("One pipeline from first interview through acceptance")
        subtitle.setObjectName("HiringV2Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        new_button = self.QtWidgets.QPushButton("+ New interview")
        new_button.setObjectName("HiringV2PrimaryAction")
        new_button.clicked.connect(lambda: self._invoke("new_interview"))
        header.addWidget(new_button)
        root.addLayout(header)
        self.action_status = self.QtWidgets.QLabel("Loading hiring workspace…")
        self.action_status.setObjectName("HiringV2ActionStatus")
        self.action_status.setWordWrap(True)
        root.addWidget(self.action_status)

        metrics = self.QtWidgets.QHBoxLayout()
        metrics.setSpacing(10)
        self.interview_metrics: dict[str, Any] = {}
        for key, label, state in (
            ("active", "Active interviews", "active"),
            ("drafts", "Saved drafts", "neutral"),
            ("director", "Director review", "warning"),
            ("attention", "Attention", "critical"),
        ):
            card = self.QtWidgets.QFrame()
            card.setObjectName("DashboardV2MetricCard")
            card.setProperty("semanticState", state)
            card_layout = self.QtWidgets.QVBoxLayout(card)
            value = self.QtWidgets.QLabel("0")
            value.setObjectName("HiringV2MetricValue")
            card_layout.addWidget(value)
            card_layout.addWidget(self.QtWidgets.QLabel(label))
            self.interview_metrics[key] = value
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        stages = self.QtWidgets.QHBoxLayout()
        stages.setSpacing(6)
        self.stage_buttons: dict[HiringStage, Any] = {}
        for stage, label in STAGE_LABELS.items():
            button = self.QtWidgets.QPushButton(label)
            button.setMinimumWidth(0)
            button.setSizePolicy(
                self.QtWidgets.QSizePolicy.Policy.Ignored,
                self.QtWidgets.QSizePolicy.Policy.Fixed,
            )
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=stage: self._select_stage(value, checked))
            self.stage_buttons[stage] = button
            stages.addWidget(button, 1)
        root.addLayout(stages)

        filters = self.QtWidgets.QHBoxLayout()
        self.search = self.QtWidgets.QLineEdit()
        self.search.setMinimumWidth(0)
        self.search.setPlaceholderText("Search candidate, school, or position")
        self.search.textChanged.connect(self._apply_filters)
        self.school_filter = self.QtWidgets.QComboBox()
        self.school_filter.setMinimumWidth(0)
        self.school_filter.addItem("All schools")
        self.school_filter.currentTextChanged.connect(self._apply_filters)
        self.position_filter = self.QtWidgets.QComboBox()
        self.position_filter.setMinimumWidth(0)
        self.position_filter.addItem("All positions")
        self.position_filter.currentTextChanged.connect(self._apply_filters)
        self.attention_filter = self.QtWidgets.QCheckBox("Attention only")
        self.attention_filter.toggled.connect(self._apply_filters)
        filters.addWidget(self.search, 3)
        filters.addWidget(self.school_filter, 1)
        filters.addWidget(self.position_filter, 1)
        filters.addWidget(self.attention_filter)
        root.addLayout(filters)

        self.application_table = self.QtWidgets.QTableWidget(0, 5)
        self.application_table.setObjectName("HiringV2ApplicationList")
        self.application_table.setHorizontalHeaderLabels(
            ["Candidate", "School / Position", "Stage", "Status", "Actions"]
        )
        self.application_table.setSelectionBehavior(
            self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.application_table.setEditTriggers(
            self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.application_table.setHorizontalScrollBarPolicy(
            self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.application_table.verticalHeader().hide()
        self.application_table.horizontalHeader().setSectionResizeMode(
            self.QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.application_table.cellClicked.connect(lambda *_args: self._render_selected())
        root.addWidget(self.application_table, 1)
        self.detail_overlay = DashboardV2OverlayPanel(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            parent=self.widget,
            object_name="HiringV2ApplicationDetailOverlay",
            width=440,
        )
        self.detail_overlay.body_layout.addWidget(self._build_detail())
        close_detail = self.QtWidgets.QPushButton("Close")
        close_detail.setObjectName("HiringV2ApplicationDetailClose")
        close_detail.clicked.connect(self._close_detail)
        self.detail_overlay.footer_layout.addWidget(close_detail)
        self._apply_style()

    def _build_candidates_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        page.setObjectName("HiringV2CandidatesPage")
        root = self.QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        header = self.QtWidgets.QHBoxLayout()
        title = self.QtWidgets.QLabel("Candidates")
        title.setObjectName("HiringV2Title")
        header.addWidget(title)
        header.addStretch(1)
        new_interview = self.QtWidgets.QPushButton("+ New interview")
        new_interview.setObjectName("HiringV2PrimaryAction")
        new_interview.clicked.connect(lambda: self._invoke("new_interview"))
        header.addWidget(new_interview)
        root.addLayout(header)
        self.candidates_search = self.QtWidgets.QLineEdit()
        self.candidates_search.setPlaceholderText("Search candidate, contact, school, or role")
        self.candidates_search.textChanged.connect(self._refresh_candidates_table)
        root.addWidget(self.candidates_search)
        self.candidates_table = self.QtWidgets.QTableWidget(0, 6)
        self.candidates_table.setHorizontalHeaderLabels(
            ["Candidate", "Contact", "Role", "Latest school", "Current stage", "Last activity"]
        )
        self._configure_native_table(self.candidates_table)
        root.addWidget(self.candidates_table, 1)
        page.setStyleSheet(self.widget.styleSheet())
        return page

    def _build_offers_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        page.setObjectName("HiringV2OffersPage")
        root = self.QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        header = self.QtWidgets.QHBoxLayout()
        title = self.QtWidgets.QLabel("Offers")
        title.setObjectName("HiringV2Title")
        header.addWidget(title)
        header.addStretch(1)
        manual_offer = self.QtWidgets.QPushButton("+ Generate offer")
        manual_offer.setObjectName("HiringV2PrimaryAction")
        manual_offer.clicked.connect(self._open_external_offer_dialog)
        header.addWidget(manual_offer)
        root.addLayout(header)
        self.offers_status = self.QtWidgets.QLabel("Draft 0  ·  Approval 0  ·  Sent 0  ·  Accepted 0  ·  Attention 0")
        self.offers_status.setObjectName("HiringV2ActionStatus")
        root.addWidget(self.offers_status)
        pending_title = self.QtWidgets.QLabel("Offers pending approval")
        pending_title.setObjectName("HiringV2SectionTitle")
        root.addWidget(pending_title)
        self.pending_offers_table = self.QtWidgets.QTableWidget(0, 7)
        self.pending_offers_table.setHorizontalHeaderLabels(
            ["Candidate", "School / Role", "Version", "Compensation", "Reply deadline", "State", "Actions"]
        )
        self._configure_native_table(self.pending_offers_table)
        self.pending_offers_table.setVerticalScrollBarPolicy(
            self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root.addWidget(self.pending_offers_table)
        self.offers_table = self.pending_offers_table
        approved_title = self.QtWidgets.QLabel("Approved offers")
        approved_title.setObjectName("HiringV2SectionTitle")
        root.addWidget(approved_title)
        self.approved_offers_table = self.QtWidgets.QTableWidget(0, 7)
        self.approved_offers_table.setHorizontalHeaderLabels(
            ["Candidate", "School / Role", "Version", "Compensation", "Reply deadline", "State", "Actions"]
        )
        self._configure_native_table(self.approved_offers_table)
        root.addWidget(self.approved_offers_table, 1)
        page.setStyleSheet(self.widget.styleSheet())
        return page

    def _configure_native_table(self, table: Any) -> None:
        table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)

    def _build_detail(self) -> Any:
        panel = self.QtWidgets.QFrame()
        panel.setObjectName("HiringV2DetailPanel")
        layout = self.QtWidgets.QVBoxLayout(panel)
        eyebrow = self.QtWidgets.QLabel("NEXT ACTION")
        eyebrow.setObjectName("HiringV2Eyebrow")
        self.detail_next_action = self.QtWidgets.QLabel("Select an application")
        self.detail_next_action.setObjectName("HiringV2NextAction")
        self.detail_status = self.QtWidgets.QLabel("")
        self.detail_status.setWordWrap(True)
        self.detail_attention = self.QtWidgets.QLabel("")
        self.detail_attention.setWordWrap(True)
        self.detail_candidate = self.QtWidgets.QLabel("")
        self.detail_candidate.setWordWrap(True)
        timeline_title = self.QtWidgets.QLabel("Timeline")
        timeline_title.setObjectName("HiringV2SectionTitle")
        self.timeline_list = self.QtWidgets.QListWidget()
        self.timeline_list.setObjectName("HiringV2Timeline")
        self.documents_list = self.QtWidgets.QListWidget()
        self.documents_list.setMaximumHeight(92)
        self.prior_cycles_list = self.QtWidgets.QListWidget()
        self.prior_cycles_list.setMaximumHeight(92)
        layout.addWidget(eyebrow)
        layout.addWidget(self.detail_next_action)
        layout.addWidget(self.detail_status)
        layout.addWidget(self.detail_attention)
        layout.addSpacing(8)
        layout.addWidget(self.detail_candidate)
        layout.addWidget(self.QtWidgets.QLabel("Documents"))
        layout.addWidget(self.documents_list)
        layout.addWidget(self.QtWidgets.QLabel("Prior cycles"))
        layout.addWidget(self.prior_cycles_list)
        layout.addSpacing(8)
        layout.addWidget(timeline_title)
        layout.addWidget(self.timeline_list, 1)
        return panel

    def refresh(self) -> None:
        self._set_action_state("loading", "Loading hiring workspace…")
        self.service.refresh_expired_offer_attention()
        self.applications = self.service.store.list_applications()
        schools = sorted({item.school for item in self.applications})
        positions = sorted({display_role(item.position) for item in self.applications})
        self._reset_combo(self.school_filter, "All schools", schools)
        self._reset_combo(self.position_filter, "All positions", positions)
        counts = Counter(item.stage for item in self.applications)
        for stage, button in self.stage_buttons.items():
            button.setText(f"{STAGE_LABELS[stage]}  {counts[stage]}")
        self.interview_metrics["active"].setText(str(counts[HiringStage.INITIAL_INTERVIEW]))
        self.interview_metrics["drafts"].setText(str(counts[HiringStage.INITIAL_INTERVIEW]))
        self.interview_metrics["director"].setText(str(counts[HiringStage.DIRECTOR_REVIEW]))
        self.interview_metrics["attention"].setText(str(sum(bool(item.attention_code) for item in self.applications)))
        self._apply_filters()
        self._refresh_candidates_table()
        self._refresh_offers_table()
        self._set_action_state(
            "empty" if not self.applications else "ready",
            "No applications yet. Start a new interview." if not self.applications else "Hiring workspace ready.",
        )

    def _apply_filters(self, *_args: Any) -> None:
        needle = self.search.text().strip().casefold()
        school = self.school_filter.currentText()
        position = self.position_filter.currentText()
        visible: list[HiringApplication] = []
        for application in self.applications:
            candidate = self.service.store.get_candidate(application.candidate_id)
            haystack = " ".join(
                (
                    candidate.legal_name,
                    candidate.preferred_name,
                    application.school,
                    application.position,
                    display_role(application.position),
                )
            ).casefold()
            if needle and needle not in haystack:
                continue
            if school != "All schools" and application.school != school:
                continue
            if position != "All positions" and display_role(application.position) != position:
                continue
            if self.active_stage is not None and application.stage is not self.active_stage:
                continue
            if self.attention_filter.isChecked() and not application.attention_code:
                continue
            visible.append(application)
        self.visible_applications = visible
        self._render_table()

    def _render_table(self) -> None:
        self.application_table.setRowCount(len(self.visible_applications))
        for row, application in enumerate(self.visible_applications):
            candidate = self.service.store.get_candidate(application.candidate_id)
            values = [
                candidate.preferred_name or candidate.legal_name,
                f"{application.school}\n{display_role(application.position)}",
                STAGE_LABELS[application.stage],
                application.attention_code.replace("_", " ").title() or "On track",
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, application.application_id)
                self.application_table.setItem(row, column, item)
            menu_button = self.QtWidgets.QToolButton()
            menu_button.setText("•••")
            menu_button.clicked.connect(
                lambda checked=False, item=application: self._open_actions(item)
            )
            self.application_table.setCellWidget(row, 4, menu_button)
            self.application_table.setRowHeight(row, 52)
        self._close_detail()

    def _render_selected(self) -> None:
        row = self.application_table.currentRow()
        if row < 0 or row >= len(self.visible_applications):
            return
        application = self.visible_applications[row]
        candidate = self.service.store.get_candidate(application.candidate_id)
        self.selected_application_id = application.application_id
        self.detail_next_action.setText(NEXT_ACTIONS[application.stage])
        status = STAGE_LABELS[application.stage]
        if application.attention_code:
            status += f" · Attention: {application.attention_code.replace('_', ' ')}"
        self.detail_status.setText(status)
        attention_messages = {
            "migration_conflict": "History records conflict. Verify stage and documents before continuing.",
            "approved_send_failed": "Offer approved, but candidate delivery failed. Retry same version.",
            "offer_overdue": "Reply deadline passed. Offer remains eligible for Admin acceptance.",
        }
        self.detail_attention.setText(attention_messages.get(application.attention_code, ""))
        self.detail_candidate.setText(
            f"{candidate.legal_name}\n{application.school} · {display_role(application.position)}\n"
            f"Application cycle {application.cycle_number}"
        )
        self.timeline_list.clear()
        for event in self.service.store.list_events(application.application_id):
            label = event.event_type.replace("_", " ").title()
            self.timeline_list.addItem(f"{label}\n{event.created_at[:10]} · {event.actor}")
        self.documents_list.clear()
        for version in self.service.store.list_offer_versions(application.application_id):
            for label, path in (("DOCX", version.docx_path), ("PDF", version.pdf_path)):
                if path:
                    self.documents_list.addItem(f"v{version.version_number} {label} · {path}")
        if self.documents_list.count() == 0:
            self.documents_list.addItem("No documents yet")
        self.prior_cycles_list.clear()
        prior = [
            item
            for item in self.service.store.list_applications(include_archived=True)
            if item.candidate_id == application.candidate_id
            and item.application_id != application.application_id
        ]
        for item in sorted(prior, key=lambda value: (value.created_at, value.cycle_number), reverse=True):
            self.prior_cycles_list.addItem(
                f"Cycle {item.cycle_number} · {item.school} · {display_role(item.position)} · {STAGE_LABELS[item.stage]}"
            )
        if not prior:
            self.prior_cycles_list.addItem("No prior cycles")
        self.detail_overlay.show_overlay()

    def _refresh_candidates_table(self, *_args: Any) -> None:
        if not hasattr(self, "candidates_table"):
            return
        needle = self.candidates_search.text().strip().casefold()
        grouped: dict[str, list[HiringApplication]] = {}
        for application in self.applications:
            candidate = self.service.store.get_candidate(application.candidate_id)
            haystack = " ".join(
                (
                    candidate.legal_name,
                    candidate.preferred_name,
                    candidate.email,
                    candidate.phone,
                    application.school,
                    application.position,
                    display_role(application.position),
                )
            ).casefold()
            if needle and needle not in haystack:
                continue
            grouped.setdefault(application.candidate_id, []).append(application)
        self.candidates_table.setRowCount(len(grouped))
        for row, applications in enumerate(grouped.values()):
            latest = max(applications, key=lambda item: item.updated_at)
            candidate = self.service.store.get_candidate(latest.candidate_id)
            role_label = display_role(latest.position)
            values = (
                candidate.preferred_name or candidate.legal_name,
                candidate.email or candidate.phone or "Missing contact",
                role_label,
                latest.school,
                STAGE_LABELS[latest.stage],
                latest.updated_at[:16].replace("T", " "),
            )
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                self.candidates_table.setItem(row, column, item)
                if column == 2:
                    self.candidates_table.setCellWidget(row, column, self._role_badge(latest.position))

    def _role_badge(self, role: str) -> Any:
        label = display_role(role)
        role_kind = role_badge_key(role)
        frame = self.QtWidgets.QFrame()
        frame.setObjectName("HiringV2RoleBadge")
        frame.setProperty("roleKind", role_kind)
        frame.setAccessibleName(f"Role: {label}")
        frame.setToolTip(label)
        layout = self.QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(5)
        icon = self.QtWidgets.QLabel()
        icon.setObjectName("HiringV2RoleBadgeIcon")
        pixmaps = self.QtWidgets.QStyle.StandardPixmap
        icon_kind = {
            "director": pixmaps.SP_DirHomeIcon,
            "teacher": pixmaps.SP_FileDialogInfoView,
            "aide": pixmaps.SP_FileDialogDetailedView,
            "support": pixmaps.SP_MessageBoxInformation,
            "preschool": pixmaps.SP_FileDialogInfoView,
            "infant_toddler": pixmaps.SP_DirHomeIcon,
        }.get(role_kind, pixmaps.SP_FileIcon)
        icon.setPixmap(frame.style().standardIcon(icon_kind).pixmap(16, 16))
        icon.setFixedSize(18, 18)
        text = self.QtWidgets.QLabel(label)
        text.setObjectName("HiringV2RoleBadgeText")
        layout.addWidget(icon)
        layout.addWidget(text)
        layout.addStretch(1)
        return frame

    def _refresh_offers_table(self) -> None:
        if not hasattr(self, "offers_table"):
            return
        pending_rows = []
        approved_rows = []
        counts: Counter[str] = Counter()
        for application in self.applications:
            candidate = self.service.store.get_candidate(application.candidate_id)
            for version in self.service.store.list_offer_versions(application.application_id):
                counts[version.status] += 1
                if version.status == "pending_approval":
                    pending_rows.append((application, candidate, version))
                elif version.status in {"approved", "sent", "accepted"}:
                    approved_rows.append((application, candidate, version))
        self._populate_offers_table(self.pending_offers_table, pending_rows)
        self._resize_pending_offers_table()
        self._populate_offers_table(self.approved_offers_table, approved_rows)
        attention = sum(bool(item.attention_code) for item in self.applications)
        self.offers_status.setText(
            f"Draft {counts['draft']}  ·  Approval {counts['pending_approval']}  ·  "
            f"Sent {counts['sent']}  ·  Accepted {counts['accepted']}  ·  Attention {attention}"
        )

    def _populate_offers_table(self, table: Any, rows: list[tuple[Any, Any, Any]]) -> None:
        table.setRowCount(len(rows))
        for row, (application, candidate, version) in enumerate(rows):
            terms = version.terms
            pay = terms.get("hourly_pay", "")
            hours = terms.get("weekly_hours", terms.get("hours_week", ""))
            values = (
                candidate.preferred_name or candidate.legal_name,
                f"{application.school} / {display_role(application.position)}",
                f"v{version.version_number}",
                f"${pay}/hr · {hours} hrs",
                version.operational_reply_by_date or version.document_reply_by_date or "Not set",
                version.status.replace("_", " ").title(),
            )
            for column, value in enumerate(values):
                table.setItem(row, column, self.QtWidgets.QTableWidgetItem(str(value)))
            actions = self.QtWidgets.QWidget()
            actions_layout = self.QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)
            if version.status == "pending_approval":
                review = self.QtWidgets.QPushButton("Review offer")
                review.setObjectName("HiringV2ReviewOfferAction")
                review.clicked.connect(
                    lambda _checked=False, item=application: self._invoke("review_approval", item)
                )
                actions_layout.addWidget(review, 1)
            elif version.status == "approved" and not candidate.email.strip():
                send = self.QtWidgets.QPushButton("Send offer")
                send.setObjectName("HiringV2SendOfferAction")
                send.clicked.connect(
                    lambda _checked=False, item=application, offer=version: self._invoke(
                        "send_offer", item, offer
                    )
                )
                actions_layout.addWidget(send, 1)
            action = self.QtWidgets.QToolButton()
            action.setObjectName("HiringV2OfferOverflowAction")
            action.setText("•••")
            action.setPopupMode(self.QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            action.setMenu(self._build_offer_menu(application, version, action))
            actions_layout.addWidget(action)
            table.setCellWidget(row, 6, actions)

    def _resize_pending_offers_table(self) -> None:
        table = self.pending_offers_table
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + 2 * table.frameWidth()
        height += sum(table.rowHeight(row) for row in range(table.rowCount()))
        table.setFixedHeight(height)

    def _build_offer_menu(self, application: Any, version: Any, parent: Any) -> Any:
        menu = self.QtWidgets.QMenu(parent)
        action_names: list[tuple[str, str]] = []
        if version.status == "pending_approval":
            action_names.append(("Review offer", "review_approval"))
        elif version.status == "approved" and version.send_status == "failed":
            action_names.append(("Retry send", "retry_send"))
        elif version.status == "sent":
            action_names.extend(
                (
                    ("Revise compensation", "revise_compensation"),
                    ("Extend deadline", "extend_deadline"),
                    ("Mark accepted", "accept_offer"),
                )
            )
        for label, action_name in action_names:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, name=action_name: self._invoke(name, application)
            )
        if action_names:
            menu.addSeparator()
        delete = menu.addAction("Delete offer")
        delete.triggered.connect(
            lambda _checked=False: self._invoke("delete_offer", application, version)
        )
        menu.addSeparator()
        archive = menu.addAction("Archive application")
        archive.triggered.connect(lambda _checked=False: self._invoke("archive", application))
        return menu

    def _select_stage(self, stage: HiringStage, checked: bool) -> None:
        self.active_stage = stage if checked else None
        for item_stage, button in self.stage_buttons.items():
            if item_stage is not stage:
                button.setChecked(False)
        self._apply_filters()

    def _open_actions(self, application: HiringApplication) -> None:
        self.selected_application_id = application.application_id
        action_names = {
            HiringStage.INITIAL_INTERVIEW: [("Resume interview", "resume_interview")],
            HiringStage.DIRECTOR_REVIEW: [("Open director review", "director_review")],
            HiringStage.OFFER_DRAFT: [("Create offer", "create_offer")],
            HiringStage.EXECUTIVE_APPROVAL: [
                ("Review offer", "review_approval"),
                ("Retry send", "retry_send"),
            ],
            HiringStage.OFFER_SENT: [
                ("Revise compensation", "revise_compensation"),
                ("Approve pending revision", "approve_revision"),
                ("Extend deadline", "extend_deadline"),
                ("Mark accepted", "accept_offer"),
            ],
            HiringStage.ACCEPTED: [("View acceptance", "view_acceptance")],
            HiringStage.CLOSED: [("View closeout", "view_closeout")],
        }
        menu = self.QtWidgets.QMenu(self.widget)
        for label, action_name in action_names[application.stage]:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, name=action_name: self._invoke(name, application)
            )
        if application.history_id:
            menu.addSeparator()
            for label, action_name in (
                ("Open interview notes", "open_notes"),
                ("Regenerate interview notes", "regenerate_notes"),
                ("Import transcript", "import_transcript"),
            ):
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda checked=False, name=action_name: self._invoke(name, application)
                )
        menu.addSeparator()
        archive = menu.addAction("Archive application")
        archive.triggered.connect(lambda: self._invoke("archive", application))
        menu.exec(self.widget.mapToGlobal(self.widget.rect().center()))

    def _invoke(self, name: str, *args: Any) -> None:
        callback = self.actions.get(name)
        if callback is not None:
            callback(*args)
            return
        application = args[0] if args else None
        if name == "delete_offer" and application is not None and len(args) > 1:
            self._run_default_offer_delete(application, args[1])
            return
        if application is not None:
            self._run_default_action(name, application)

    def _run_default_offer_delete(self, application: Any, version: Any) -> None:
        answer = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Delete offer",
            f"Permanently delete offer v{version.version_number} and its generated DOCX/PDF files?",
        )
        if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._set_action_state("working", "Deleting offer…")
        try:
            self.service.delete_offer(
                application.application_id,
                version.version_id,
                actor="Admin User",
            )
            self.refresh()
            self._set_action_state("success", "Offer and generated files deleted.")
        except (OSError, ValueError) as exc:
            self._set_action_state("error", f"Delete offer failed: {exc}")
            self.QtWidgets.QMessageBox.warning(self.widget, "Delete offer failed", str(exc))

    def perform_action(
        self,
        name: str,
        application: HiringApplication,
        *,
        actor: str = "Admin User",
        values: dict[str, Any] | None = None,
    ) -> Any:
        payload = values or {}
        if name == "archive":
            result = self.service.archive_application(application.application_id, actor=actor)
        elif name == "accept_offer":
            sent = [
                version
                for version in self.service.store.list_offer_versions(application.application_id)
                if version.status == "sent"
            ]
            if not sent:
                raise ValueError("No sent offer is available.")
            latest = max(sent, key=lambda version: version.version_number)
            result = self.service.accept_offer(application.application_id, latest.version_id, actor=actor)
        elif name == "extend_deadline":
            versions = self.service.store.list_offer_versions(application.application_id)
            sent = [version for version in versions if version.status == "sent"]
            if not sent:
                raise ValueError("No sent offer is available.")
            latest = max(sent, key=lambda version: version.version_number)
            result = self.service.extend_offer_deadline(
                application.application_id,
                latest.version_id,
                reply_by_date=date.fromisoformat(str(payload["reply_by_date"])),
                actor=actor,
            )
        elif name == "retry_send":
            failed = [
                version
                for version in self.service.store.list_offer_versions(application.application_id)
                if version.status == "approved" and version.send_status == "failed"
            ]
            if not failed:
                raise ValueError("No failed approved offer is available.")
            latest = max(failed, key=lambda version: version.version_number)
            result = self.service.retry_offer_send(application.application_id, latest.version_id, actor=actor)
        elif name == "create_offer":
            draft = self.service.create_offer_draft(
                application.application_id,
                terms=dict(payload),
                actor=actor,
            )
            result = self.service.submit_offer_for_approval(
                application.application_id,
                draft.version_id,
                actor=actor,
            )
        elif name == "revise_compensation":
            result = self.service.create_compensation_revision(
                application.application_id,
                hourly_pay=str(payload["hourly_pay"]),
                weekly_hours=str(payload["weekly_hours"]),
                actor=actor,
                actor_role="Admin",
            )
        elif name == "create_external_offer":
            result = self.service.create_external_offer(
                legal_name=str(payload["candidate_name"]),
                email=str(payload.get("candidate_email", "")),
                phone=str(payload.get("candidate_phone", "")),
                honorific=str(payload.get("honorific", "Ms.")),
                school=str(payload["school"]),
                position=str(payload["position"]),
                terms=dict(payload),
                actor=actor,
            )
        else:
            raise ValueError(f"Unsupported hiring action: {name}")
        self.refresh()
        return result

    def _run_default_action(self, name: str, application: HiringApplication) -> None:
        self._set_action_state("working", "Hiring action in progress…")
        try:
            if name in {"create_offer", "revise_compensation"}:
                values = self._offer_editor_values(application, revision=name == "revise_compensation")
                if values is None:
                    self._set_action_state("ready", "Hiring action cancelled.")
                    return
                self.perform_action(name, application, values=values)
                self._set_action_state("success", "Hiring action completed.")
                return
            if name == "extend_deadline":
                value, accepted = self.QtWidgets.QInputDialog.getText(
                    self.widget,
                    "Extend offer deadline",
                    "New reply-by date (YYYY-MM-DD)\nDocument will retain its original deadline:",
                )
                if not accepted:
                    self._set_action_state("ready", "Hiring action cancelled.")
                    return
                self.perform_action(name, application, values={"reply_by_date": value})
                self._set_action_state("success", "Hiring action completed.")
                return
            if name in {"archive", "accept_offer"}:
                answer = self.QtWidgets.QMessageBox.question(
                    self.widget,
                    "Confirm hiring action",
                    "Archive this application?" if name == "archive" else "Mark latest sent offer accepted?",
                )
                if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                    self._set_action_state("ready", "Hiring action cancelled.")
                    return
            self.perform_action(name, application)
            self._set_action_state("success", "Hiring action completed.")
        except (KeyError, TypeError, ValueError) as exc:
            self._set_action_state("error", f"Action failed: {exc}")
            self.QtWidgets.QMessageBox.warning(self.widget, "Hiring action failed", str(exc))

    def _open_external_offer_dialog(self) -> None:
        self._set_action_state("working", "Preparing external offer…")
        values = self._external_offer_editor_values()
        if values is None:
            self._set_action_state("ready", "Hiring action cancelled.")
            return
        try:
            self.perform_action(
                "create_external_offer",
                HiringApplication(
                    application_id="",
                    candidate_id="",
                    history_id="",
                    school=str(values["school"]),
                    position=str(values["position"]),
                    cycle_number=0,
                    stage=HiringStage.OFFER_DRAFT,
                ),
                values=values,
            )
            self._set_action_state("success", "External offer submitted for approval.")
        except (KeyError, TypeError, ValueError) as exc:
            self._set_action_state("error", f"Action failed: {exc}")
            self.QtWidgets.QMessageBox.warning(self.widget, "Generate offer failed", str(exc))

    def _external_offer_editor_values(self) -> dict[str, Any] | None:
        dialog = self.QtWidgets.QDialog(self.offers_widget)
        dialog.setWindowTitle("Generate offer")
        dialog.resize(720, 520)
        root = self.QtWidgets.QVBoxLayout(dialog)
        stack = self.QtWidgets.QStackedWidget()
        root.addWidget(stack, 1)

        identity = self.QtWidgets.QWidget()
        identity_form = self.QtWidgets.QFormLayout(identity)
        candidate_name = self.QtWidgets.QLineEdit()
        candidate_email = self.QtWidgets.QLineEdit()
        candidate_phone = self.QtWidgets.QLineEdit()
        honorific = self.QtWidgets.QComboBox()
        honorific.addItems(["Ms.", "Mr."])
        school = self.QtWidgets.QLineEdit()
        position = self.QtWidgets.QLineEdit()
        identity_form.addRow("Candidate name", candidate_name)
        identity_form.addRow("Email", candidate_email)
        identity_form.addRow("Phone", candidate_phone)
        identity_form.addRow("Honorific", honorific)
        identity_form.addRow("School", school)
        identity_form.addRow("Role", position)
        stack.addWidget(identity)

        compensation = self.QtWidgets.QWidget()
        compensation_form = self.QtWidgets.QFormLayout(compensation)
        start_time = self.QtWidgets.QLineEdit("08:00 AM")
        end_time = self.QtWidgets.QLineEdit("05:00 PM")
        hourly_pay = self.QtWidgets.QDoubleSpinBox()
        hourly_pay.setRange(0.01, 500.0)
        hourly_pay.setValue(20.0)
        weekly_hours = self.QtWidgets.QSpinBox()
        weekly_hours.setRange(1, 168)
        weekly_hours.setValue(40)
        compensation_form.addRow("Start time", start_time)
        compensation_form.addRow("End time", end_time)
        compensation_form.addRow("Hourly pay", hourly_pay)
        compensation_form.addRow("Weekly hours", weekly_hours)
        stack.addWidget(compensation)

        destination = self.QtWidgets.QWidget()
        destination_form = self.QtWidgets.QFormLayout(destination)
        template_path = self.QtWidgets.QLineEdit()
        output_dir = self.QtWidgets.QLineEdit()
        destination_form.addRow("Template", template_path)
        destination_form.addRow("Destination", output_dir)
        destination_form.addRow(
            "Review",
            self.QtWidgets.QLabel("Creates a candidate record and submits offer v1 for approval."),
        )
        stack.addWidget(destination)

        error = self.QtWidgets.QLabel("")
        error.setObjectName("HiringV2ActionStatus")
        error.setProperty("state", "error")
        error.setWordWrap(True)
        root.addWidget(error)

        controls = self.QtWidgets.QHBoxLayout()
        back = self.QtWidgets.QPushButton("Back")
        next_button = self.QtWidgets.QPushButton("Next")
        submit = self.QtWidgets.QPushButton("Submit for approval")
        cancel = self.QtWidgets.QPushButton("Cancel")
        controls.addWidget(back)
        controls.addStretch(1)
        controls.addWidget(cancel)
        controls.addWidget(next_button)
        controls.addWidget(submit)
        root.addLayout(controls)
        back.clicked.connect(lambda: stack.setCurrentIndex(max(0, stack.currentIndex() - 1)))
        next_button.clicked.connect(lambda: stack.setCurrentIndex(min(2, stack.currentIndex() + 1)))
        cancel.clicked.connect(dialog.reject)

        def accept_if_valid() -> None:
            required = {
                "Candidate name": candidate_name.text(),
                "School": school.text(),
                "Role": position.text(),
                "Template": template_path.text(),
                "Destination": output_dir.text(),
            }
            missing = [label for label, value in required.items() if not value.strip()]
            if missing:
                error.setText("Required: " + ", ".join(missing))
                return
            dialog.accept()

        submit.clicked.connect(accept_if_valid)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return {
            "candidate_name": candidate_name.text().strip(),
            "candidate_email": candidate_email.text().strip(),
            "candidate_phone": candidate_phone.text().strip(),
            "honorific": honorific.currentText(),
            "school": school.text().strip(),
            "position": position.text().strip(),
            "start_time": start_time.text().strip(),
            "end_time": end_time.text().strip(),
            "hourly_pay": f"{hourly_pay.value():.2f}",
            "weekly_hours": str(weekly_hours.value()),
            "template_path": template_path.text().strip(),
            "output_dir": output_dir.text().strip(),
        }

    def _set_action_state(self, state: str, message: str) -> None:
        self.action_status.setProperty("state", state)
        self.action_status.setText(message)
        self.action_status.style().unpolish(self.action_status)
        self.action_status.style().polish(self.action_status)

    def _offer_editor_values(
        self,
        application: HiringApplication,
        *,
        revision: bool,
    ) -> dict[str, Any] | None:
        candidate = self.service.store.get_candidate(application.candidate_id)
        prior_versions = self.service.store.list_offer_versions(application.application_id)
        prior_terms = dict(prior_versions[-1].terms) if prior_versions else {}
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setWindowTitle("Revise compensation" if revision else "Create offer")
        dialog.resize(680, 430)
        root = self.QtWidgets.QVBoxLayout(dialog)
        stack = self.QtWidgets.QStackedWidget()
        root.addWidget(stack, 1)

        identity = self.QtWidgets.QWidget()
        identity_form = self.QtWidgets.QFormLayout(identity)
        identity_form.addRow("Candidate", self.QtWidgets.QLabel(candidate.legal_name))
        identity_form.addRow("Email", self.QtWidgets.QLabel(candidate.email or "Missing email"))
        identity_form.addRow("School", self.QtWidgets.QLabel(application.school))
        identity_form.addRow("Position", self.QtWidgets.QLabel(display_role(application.position)))
        stack.addWidget(identity)

        compensation = self.QtWidgets.QWidget()
        compensation_form = self.QtWidgets.QFormLayout(compensation)
        start_time = self.QtWidgets.QLineEdit(str(prior_terms.get("start_time", "08:00 AM")))
        end_time = self.QtWidgets.QLineEdit(str(prior_terms.get("end_time", "05:00 PM")))
        hourly_pay = self.QtWidgets.QDoubleSpinBox()
        hourly_pay.setRange(0.01, 500.0)
        hourly_pay.setValue(float(prior_terms.get("hourly_pay", 20.0)))
        weekly_hours = self.QtWidgets.QSpinBox()
        weekly_hours.setRange(1, 168)
        weekly_hours.setValue(int(float(prior_terms.get("weekly_hours", prior_terms.get("hours_week", 40)))))
        compensation_form.addRow("Start time", start_time)
        compensation_form.addRow("End time", end_time)
        compensation_form.addRow("Hourly pay", hourly_pay)
        compensation_form.addRow("Weekly hours", weekly_hours)
        stack.addWidget(compensation)

        destination = self.QtWidgets.QWidget()
        destination_form = self.QtWidgets.QFormLayout(destination)
        template_path = self.QtWidgets.QLineEdit(str(prior_terms.get("template_path", "")))
        output_dir = self.QtWidgets.QLineEdit(str(prior_terms.get("output_dir", "")))
        destination_form.addRow("Template", template_path)
        destination_form.addRow("Destination", output_dir)
        destination_form.addRow(
            "Review",
            self.QtWidgets.QLabel("Version will be immutable after submission."),
        )
        stack.addWidget(destination)

        controls = self.QtWidgets.QHBoxLayout()
        back = self.QtWidgets.QPushButton("Back")
        next_button = self.QtWidgets.QPushButton("Next")
        submit = self.QtWidgets.QPushButton("Submit revision" if revision else "Submit for approval")
        cancel = self.QtWidgets.QPushButton("Cancel")
        controls.addWidget(back)
        controls.addStretch(1)
        controls.addWidget(cancel)
        controls.addWidget(next_button)
        controls.addWidget(submit)
        root.addLayout(controls)
        back.clicked.connect(lambda: stack.setCurrentIndex(max(0, stack.currentIndex() - 1)))
        next_button.clicked.connect(lambda: stack.setCurrentIndex(min(2, stack.currentIndex() + 1)))
        cancel.clicked.connect(dialog.reject)
        submit.clicked.connect(dialog.accept)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return {
            **prior_terms,
            "candidate_name": candidate.legal_name,
            "candidate_email": candidate.email,
            "school": application.school,
            "position": application.position,
            "start_time": start_time.text().strip(),
            "end_time": end_time.text().strip(),
            "hourly_pay": f"{hourly_pay.value():.2f}",
            "weekly_hours": str(weekly_hours.value()),
            "template_path": template_path.text().strip(),
            "output_dir": output_dir.text().strip(),
        }

    def _clear_detail(self) -> None:
        self.selected_application_id = ""
        self.detail_next_action.setText("No matching applications")
        self.detail_status.clear()
        self.detail_attention.clear()
        self.detail_candidate.clear()
        self.documents_list.clear()
        self.prior_cycles_list.clear()
        self.timeline_list.clear()

    def _close_detail(self) -> None:
        self.application_table.clearSelection()
        self.application_table.setCurrentCell(-1, -1)
        self._clear_detail()
        self.detail_overlay.hide()

    @staticmethod
    def _reset_combo(combo: Any, first: str, values: list[str]) -> None:
        selected = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(first)
        combo.addItems(values)
        index = combo.findText(selected)
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)

    def _apply_style(self) -> None:
        self.widget.setStyleSheet(
            """
            #HiringWorkspaceV2Page { background: #f5f7fb; color: #172033; }
            #HiringV2Title { font-size: 26px; font-weight: 700; }
            #HiringV2Subtitle { color: #667085; }
            #HiringV2ActionStatus { color: #475467; padding: 5px 8px; }
            #HiringV2ActionStatus[state="error"] { color: #b42318; background: #fef3f2; }
            #HiringV2ActionStatus[state="working"], #HiringV2ActionStatus[state="loading"] {
                color: #175cd3; background: #eff8ff; }
            #HiringV2PrimaryAction { background: #2563eb; color: white; border: 0;
                border-radius: 7px; padding: 10px 16px; font-weight: 600; }
            #HiringV2ApplicationList, #HiringV2DetailPanel { background: white;
                border: 1px solid #dfe4ec; border-radius: 10px; }
            #HiringV2Eyebrow { color: #667085; font-size: 11px; font-weight: 700; }
            #HiringV2NextAction { font-size: 20px; font-weight: 700; }
            #HiringV2SectionTitle { font-size: 16px; font-weight: 700; }
            #HiringV2RoleBadge { border-radius: 8px; }
            #HiringV2RoleBadge[roleKind="director"] { background: #f3e8ff; color: #7e22ce;
                border: 1px solid #d8b4fe; }
            #HiringV2RoleBadge[roleKind="teacher"] { background: #dbeafe; color: #1d4ed8;
                border: 1px solid #bfdbfe; }
            #HiringV2RoleBadge[roleKind="aide"] { background: #dcfce7; color: #15803d;
                border: 1px solid #bbf7d0; }
            #HiringV2RoleBadge[roleKind="support"] { background: #ffedd5; color: #c2410c;
                border: 1px solid #fed7aa; }
            #HiringV2RoleBadge[roleKind="preschool"] { background: #dbeafe; color: #1d4ed8;
                border: 1px solid #bfdbfe; }
            #HiringV2RoleBadge[roleKind="infant_toddler"] { background: #ccfbf1; color: #0f766e;
                border: 1px solid #99f6e4; }
            #HiringV2RoleBadge[roleKind="other"] { background: #f1f5f9; color: #475569;
                border: 1px solid #cbd5e1; }
            #HiringV2RoleBadgeIcon, #HiringV2RoleBadgeText { background: transparent; font-weight: 700; }
            QPushButton { min-height: 30px; border: 1px solid #d0d5dd;
                border-radius: 7px; background: white; }
            QPushButton:checked { background: #e8f0ff; color: #174ea6; border-color: #7aa2f7; }
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
            QCheckBox:focus, QTableWidget:focus { border: 2px solid #2563eb; }
            QLineEdit, QComboBox { min-height: 34px; border: 1px solid #d0d5dd;
                border-radius: 7px; background: white; padding: 0 8px; }
            """
        )
