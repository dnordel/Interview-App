from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PySide6 import QtCore, QtGui, QtWidgets

from candidate_report import (
    CandidateReportPermissionError,
    CandidateReportRecord,
    CandidateReportRepository,
    CandidateReportStaleError,
    CandidateReportValidationError,
    recalculate_candidate_report,
    export_candidate_report_audit_csv,
    validate_candidate_report,
)
from platform_services import Document, sanitize_filename
from staffing_store import StaffingStaleRevisionError


REPORT_QSS = """
QDialog#CandidateInterviewReportDialog { background: #f8fafc; color: #172033; }
QFrame#CandidateReportRail { background: #ffffff; border-right: 1px solid #dfe5ee; }
QListWidget#CandidateReportSectionNavigation { border: 0; background: transparent; outline: 0; font-size: 14px; }
QListWidget#CandidateReportSectionNavigation::item { padding: 12px 14px; margin: 3px 8px; border-radius: 7px; }
QListWidget#CandidateReportSectionNavigation::item:selected { background: #1464f4; color: white; }
QFrame[reportCard="true"] { background: white; border: 1px solid #dfe5ee; border-radius: 7px; }
QLabel#CandidateReportName { font-size: 22px; font-weight: 700; color: #142038; }
QLabel#CandidateReportCalculatedOutcome { font-size: 20px; font-weight: 700; color: #142038; }
QLabel[reportMetric="true"] { font-size: 20px; font-weight: 700; color: #142038; }
QLabel[reportMuted="true"] { color: #5f6d82; }
QPushButton { min-height: 34px; padding: 0 14px; border: 1px solid #cbd5e1; border-radius: 6px; background: white; }
QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QListWidget:focus, QTableWidget:focus { border: 2px solid #1464f4; }
QPushButton[primary="true"] { background: #1464f4; color: white; border-color: #1464f4; font-weight: 600; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
QLineEdit, QPlainTextEdit, QSpinBox { border: 1px solid #cbd5e1; border-radius: 5px; padding: 6px; background: white; }
QTableWidget { border: 1px solid #dfe5ee; background: white; gridline-color: #e6ebf2; }
"""


def generate_basic_candidate_notes_document(snapshot: dict[str, Any], output_path: Path) -> Path:
    """Write a basic Word interview-notes document from a structured report snapshot."""
    path = Path(output_path)
    if path.suffix.casefold() != ".docx":
        raise ValueError("Interview notes output must be a .docx file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), dict) else {}
    scoring = snapshot.get("scoring") if isinstance(snapshot.get("scoring"), dict) else {}
    questions = snapshot.get("questions") if isinstance(snapshot.get("questions"), list) else []
    summaries = snapshot.get("summaries") if isinstance(snapshot.get("summaries"), dict) else {}

    document = Document()
    document.add_heading("Interview Notes", level=0)
    for label, value in (
        ("Candidate", candidate.get("candidate_name")),
        ("Interview Date", candidate.get("interview_date")),
        ("School", candidate.get("school")),
        ("Role / Track", candidate.get("track")),
        ("Score", scoring.get("percent_of_max")),
        ("Outcome", scoring.get("outcome")),
    ):
        text = str(value if value is not None else "").strip()
        if text:
            document.add_paragraph(f"{label}: {text}")

    document.add_heading("Interview Questions and Notes", level=1)
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        prompt = str(question.get("prompt") or question.get("title") or f"Question {index}").strip()
        document.add_heading(f"{index}. {prompt}", level=2)
        transcript = str(question.get("transcript") or "").strip()
        notes = str(question.get("interviewer_notes") or "").strip()
        document.add_paragraph(f"Candidate response: {transcript or 'Not captured'}")
        document.add_paragraph(f"Interviewer notes: {notes or 'Not captured'}")
        rating = question.get("rating")
        if rating not in (None, ""):
            document.add_paragraph(f"Rating: {rating}")

    document.add_heading("Summary", level=1)
    summary = str(summaries.get("executive_summary") or "").strip()
    document.add_paragraph(summary or "No summary captured.")

    temporary_path = path.with_name(f".{path.stem}.{uuid4().hex}.tmp.docx")
    try:
        document.save(temporary_path)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return path.resolve()


class CandidateInterviewReportDialog(QtWidgets.QDialog):
    """Mockup-driven structured candidate report for Staffing v2."""

    def __init__(
        self,
        *,
        QtCore: Any = QtCore,
        QtGui: Any = QtGui,
        QtWidgets: Any = QtWidgets,
        repository: CandidateReportRepository,
        history_id: str,
        role: str,
        actor: str,
        school_scope: str = "",
        rubric: dict[str, Any] | None = None,
        director_interview: Any | None = None,
        director_service: Any | None = None,
        open_document: Callable[[Path], None] | None = None,
        finalized_callback: Callable[[CandidateReportRecord], None] | None = None,
        app_version: str = "",
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.repository = repository
        self.history_id = str(history_id or "").strip()
        self.role = "director" if str(role).strip().lower() == "director" else "admin"
        self.actor = str(actor or "unknown").strip() or "unknown"
        self.school_scope = str(school_scope or "").strip()
        self.rubric = dict(rubric or {})
        self.director_interview = director_interview
        self.director_saved_snapshot = director_interview
        self.director_service = director_service
        self.open_document = open_document or self._default_open_document
        self.finalized_callback = finalized_callback
        self.app_version = str(app_version or "")
        self.record = repository.load_visible_version(
            self.history_id,
            role=self.role,
            school_scope=self.school_scope if self.role == "director" else "",
        )
        self.working_snapshot = copy.deepcopy(self.record.snapshot)
        self.saved_snapshot = copy.deepcopy(self.record.snapshot)
        self.dirty = False
        self._field_widgets: dict[str, Any] = {}
        self._field_markers: dict[str, Any] = {}
        self._field_revert_buttons: dict[str, Any] = {}
        self._question_widgets: list[dict[str, Any]] = []
        self._summary_widgets: dict[str, Any] = {}

        self.setObjectName("CandidateInterviewReportDialog")
        self.setWindowTitle("Candidate Interview Report")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setWindowFlags(
            self.windowFlags()
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(REPORT_QSS)
        self.setMinimumSize(980, 680)
        self.resize(1220, 780)
        self._scrim = self._create_scrim(parent)
        self._build()
        self._install_shortcuts()
        application = self.QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self._restore_geometry()

    @property
    def editing_initial(self) -> bool:
        return self.role == "admin" and self.record.state == "reopened"

    def _build(self) -> None:
        root = self.layout()
        if root is None:
            root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QtWidgets.QFrame()
        rail.setObjectName("CandidateReportRail")
        rail.setFixedWidth(245)
        rail_layout = QtWidgets.QVBoxLayout(rail)
        rail_layout.setContentsMargins(8, 14, 8, 14)
        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("CandidateReportSectionNavigation")
        self.navigation.setAccessibleName("Candidate report sections")
        rail_layout.addWidget(self.navigation)
        body.addWidget(rail)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(26, 16, 26, 12)
        content_layout.setSpacing(12)
        content_layout.addLayout(self._header())
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("CandidateReportStatusMessage")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Report status")
        content_layout.addWidget(self.status_label)
        self.readiness_panel = QtWidgets.QFrame()
        self.readiness_panel.setObjectName("CandidateReportReadinessPanel")
        self.readiness_panel.setStyleSheet("background: #fffaf0; border: 1px solid #edc46b; border-radius: 6px;")
        self.readiness_layout = QtWidgets.QVBoxLayout(self.readiness_panel)
        self.readiness_panel.setVisible(False)
        content_layout.addWidget(self.readiness_panel)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("CandidateReportSectionStack")
        content_layout.addWidget(self.stack, 1)
        body.addWidget(content, 1)
        root.addLayout(body, 1)
        root.addWidget(self._footer())

        sections: list[tuple[str, Any]] = [
            ("Overview", self._overview_page()),
            ("Candidate Details", self._candidate_details_page()),
            ("Scores & Traits", self._scores_page()),
            ("Interview Answers", self._answers_page()),
            ("Summary & Outcome", self._summary_page()),
        ]
        if self.director_interview is not None:
            sections.append(("Director Interview", self._director_page()))
        sections.append(("Audit History", self._audit_page()))
        for label, page in sections:
            self.navigation.addItem(label)
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self._update_actions()

    def _header(self) -> Any:
        candidate = self.working_snapshot.get("candidate") if isinstance(self.working_snapshot.get("candidate"), dict) else {}
        row = QtWidgets.QHBoxLayout()
        labels = QtWidgets.QVBoxLayout()
        name = QtWidgets.QLabel(str(candidate.get("candidate_name") or candidate.get("name") or "Candidate"))
        name.setObjectName("CandidateReportName")
        labels.addWidget(name)
        metadata = QtWidgets.QLabel(
            "  •  ".join(
                value for value in [str(candidate.get("track") or ""), str(candidate.get("school") or ""), str(candidate.get("interview_date") or "")] if value
            )
        )
        metadata.setProperty("reportMuted", True)
        labels.addWidget(metadata)
        row.addLayout(labels, 1)
        state = QtWidgets.QLabel(self.record.state.title())
        state.setObjectName("CandidateReportStateBadge")
        state.setStyleSheet("padding: 7px 18px; color: #1455c0; background: #eaf2ff; border: 1px solid #9fc0ff; border-radius: 6px;")
        row.addWidget(state)
        return row

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[Any] = []
        bindings = [
            ("Ctrl+S", self.save_draft, self.role == "admin"),
            ("Ctrl+Shift+S", self.save_changes, self.role == "admin"),
            ("Ctrl+F", self._focus_answer_search, True),
            ("Escape", self.close, True),
        ]
        for sequence, callback, enabled in bindings:
            action = self.QtGui.QAction(self)
            action.setShortcut(self.QtGui.QKeySequence(sequence))
            action.setShortcutContext(self.QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
            self.addAction(action)
            self._shortcuts.append(action)
        for index in range(7):
            action = self.QtGui.QAction(self)
            action.setShortcut(self.QtGui.QKeySequence(f"Alt+{index + 1}"))
            action.setShortcutContext(self.QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda _checked=False, section=index: self._navigate_to_section(section))
            self.addAction(action)
            self._shortcuts.append(action)

    def _navigate_to_section(self, index: int) -> None:
        if 0 <= index < self.navigation.count():
            self.navigation.setCurrentRow(index)

    def _focus_answer_search(self) -> None:
        labels = [self.navigation.item(index).text() for index in range(self.navigation.count())]
        if "Interview Answers" not in labels:
            return
        self.navigation.setCurrentRow(labels.index("Interview Answers"))
        if hasattr(self, "answer_search"):
            self.answer_search.setFocus()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched is self.parentWidget() and event.type() == self.QtCore.QEvent.Type.Resize:
            if self._scrim is not None:
                self._scrim.setGeometry(watched.rect())
            return False
        if event.type() != self.QtCore.QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if watched is not self and not self.isAncestorOf(watched):
            return super().eventFilter(watched, event)
        modifiers = event.modifiers()
        key = event.key()
        control = self.QtCore.Qt.KeyboardModifier.ControlModifier
        shift = self.QtCore.Qt.KeyboardModifier.ShiftModifier
        alt = self.QtCore.Qt.KeyboardModifier.AltModifier
        if key == self.QtCore.Qt.Key.Key_S and modifiers == control and self.role == "admin":
            self.save_draft()
            return True
        if key == self.QtCore.Qt.Key.Key_S and modifiers == control | shift and self.role == "admin":
            self.save_changes()
            return True
        if key == self.QtCore.Qt.Key.Key_F and modifiers == control:
            self._focus_answer_search()
            return True
        if modifiers == alt and self.QtCore.Qt.Key.Key_1 <= key <= self.QtCore.Qt.Key.Key_7:
            self._navigate_to_section(key - self.QtCore.Qt.Key.Key_1)
            return True
        if key == self.QtCore.Qt.Key.Key_Escape and modifiers == self.QtCore.Qt.KeyboardModifier.NoModifier:
            self.close()
            return True
        return super().eventFilter(watched, event)

    def _create_scrim(self, parent: Any | None) -> Any | None:
        if parent is None:
            return None
        scrim = self.QtWidgets.QFrame(parent)
        scrim.setObjectName("CandidateReportDashboardScrim")
        scrim.setStyleSheet("background: rgba(15, 23, 42, 110);")
        scrim.setGeometry(parent.rect())
        scrim.show()
        scrim.raise_()
        parent.installEventFilter(self)
        return scrim

    def _page(self) -> tuple[Any, Any]:
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("CandidateReportContentScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 8, 12)
        layout.setSpacing(12)
        scroll.setWidget(widget)
        return scroll, layout

    def _card(self, title: str = "") -> tuple[Any, Any]:
        card = QtWidgets.QFrame()
        card.setProperty("reportCard", True)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        if title:
            heading = QtWidgets.QLabel(title)
            heading.setStyleSheet("font-weight: 700; font-size: 14px;")
            layout.addWidget(heading)
        return card, layout

    def _overview_page(self) -> Any:
        page, layout = self._page()
        metrics = QtWidgets.QHBoxLayout()
        scoring = self.working_snapshot.get("scoring") if isinstance(self.working_snapshot.get("scoring"), dict) else {}
        values = [
            ("Interview Score", self._score_text(scoring)),
            ("Percentage", self._percent_text(scoring)),
            ("Calculated Recommendation", str(scoring.get("outcome") or "Incomplete")),
        ]
        for title, value in values:
            card, card_layout = self._card(title)
            label = QtWidgets.QLabel(value)
            label.setProperty("reportMetric", True)
            if title == "Calculated Recommendation":
                label.setObjectName("CandidateReportCalculatedOutcome")
            card_layout.addWidget(label)
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        candidate = self.working_snapshot.get("candidate") if isinstance(self.working_snapshot.get("candidate"), dict) else {}
        qualification = candidate.get("qualification") if isinstance(candidate.get("qualification"), dict) else {}
        card, card_layout = self._card("Candidate Qualifications")
        summary = "   •   ".join(
            [
                f"ECE units: {qualification.get('ece_units_completed', 'Not provided')}",
                f"Experience: {qualification.get('years_experience', 'Not provided')}",
                f"Earliest start: {qualification.get('earliest_start_date', 'Not provided')}",
                f"Pay: {qualification.get('pay_expectation', 'Not provided')}",
            ]
        )
        qualification_label = QtWidgets.QLabel(summary)
        qualification_label.setWordWrap(True)
        card_layout.addWidget(qualification_label)
        layout.addWidget(card)
        layout.addWidget(self._trait_preview_card())
        layout.addStretch(1)
        return page

    def _trait_preview_card(self) -> Any:
        card, layout = self._card("Trait Performance (Lowest Concerns First)")
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Trait", "Rating", "Priority", "Status"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        traits = [item for item in questions if isinstance(item, dict) and str(item.get("type") or "") == "trait"]
        table.setRowCount(len(traits))
        for row, item in enumerate(sorted(traits, key=lambda value: value.get("rating") or 0)):
            values = [
                str(item.get("title") or item.get("question_id") or ""),
                "Skipped" if item.get("skipped") else str(item.get("rating") or "—"),
                str(item.get("priority") or ""),
                "Disqualifier" if item.get("absolute_disqualifier") else "Included",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(min(330, 55 + max(1, len(traits)) * 34))
        layout.addWidget(table)
        return card

    def _candidate_details_page(self) -> Any:
        page, layout = self._page()
        candidate = self.working_snapshot.get("candidate") if isinstance(self.working_snapshot.get("candidate"), dict) else {}
        card, form_layout = self._card("Candidate & Interview")
        form = QtWidgets.QFormLayout()
        for key, label in [
            ("candidate_name", "Candidate Name"), ("interview_date", "Interview Date"),
            ("school", "School / Location"), ("track", "Position / Track"),
        ]:
            editor = QtWidgets.QLineEdit(str(candidate.get(key) or ""))
            editor.setObjectName(f"CandidateReportField_{key}")
            editor.setReadOnly(not self.editing_initial)
            editor.textChanged.connect(lambda _text, field_key=key: self._field_changed(field_key))
            self._field_widgets[key] = editor
            form.addRow(label, self._editable_field_row(key, editor))
        form_layout.addLayout(form)
        layout.addWidget(card)

        qualification = candidate.get("qualification") if isinstance(candidate.get("qualification"), dict) else {}
        q_card, q_layout = self._card("Education, Qualifications & Availability")
        q_form = QtWidgets.QFormLayout()
        for key in sorted(qualification):
            editor = QtWidgets.QLineEdit(str(qualification.get(key) if qualification.get(key) is not None else ""))
            editor.setReadOnly(not self.editing_initial)
            field_key = f"qualification.{key}"
            editor.textChanged.connect(lambda _text, current_key=field_key: self._field_changed(current_key))
            self._field_widgets[field_key] = editor
            q_form.addRow(key.replace("_", " ").title(), self._editable_field_row(field_key, editor))
        q_layout.addLayout(q_form)
        layout.addWidget(q_card)
        layout.addStretch(1)
        return page

    def _scores_page(self) -> Any:
        page, layout = self._page()
        scoring = self.working_snapshot.get("scoring") if isinstance(self.working_snapshot.get("scoring"), dict) else {}
        summary, summary_layout = self._card("Score Summary")
        score_summary = QtWidgets.QLabel(f"{self._score_text(scoring)}   •   {self._percent_text(scoring)}   •   Calculated: {scoring.get('outcome', 'Incomplete')}")
        score_summary.setObjectName("CandidateReportScoreSummary")
        summary_layout.addWidget(score_summary)
        layout.addWidget(summary)
        table = QtWidgets.QTableWidget(0, 7)
        table.setObjectName("CandidateReportTraitScoreTable")
        table.setHorizontalHeaderLabels(["Trait", "Priority", "Weight", "Rating", "Weighted", "Status", "Flags"])
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        traits = [(index, item) for index, item in enumerate(questions) if isinstance(item, dict) and str(item.get("type") or "") == "trait"]
        table.setRowCount(len(traits))
        for row, (index, item) in enumerate(traits):
            values = [
                str(item.get("title") or item.get("question_id") or ""), str(item.get("priority") or ""),
                str(item.get("weight") or "—"), "", str(item.get("weighted_score") or "—"),
                "Skipped" if item.get("skipped") else "Included",
                "Disqualifier" if item.get("absolute_disqualifier") else "",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
            rating = QtWidgets.QSpinBox()
            rating.setRange(1, 5)
            rating.setValue(int(item.get("rating") or 1))
            rating.setEnabled(self.editing_initial and not bool(item.get("skipped")))
            rating.valueChanged.connect(lambda value, question_index=index: self._rating_changed(question_index, value))
            table.setCellWidget(row, 3, rating)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(min(520, 60 + max(1, len(traits)) * 38))
        layout.addWidget(table)
        layout.addStretch(1)
        return page

    def _answers_page(self) -> Any:
        page, layout = self._page()
        search = QtWidgets.QLineEdit()
        search.setObjectName("CandidateReportAnswerSearch")
        search.setPlaceholderText("Search questions or transcripts")
        search.setAccessibleName("Search interview answers")
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(search, 2)
        answer_filter = QtWidgets.QComboBox()
        answer_filter.setObjectName("CandidateReportAnswerFilter")
        answer_filter.addItems(["All", "Scored", "Skipped", "Flagged", "Low Score"])
        toolbar.addWidget(answer_filter)
        expand_all = QtWidgets.QPushButton("Expand All")
        expand_all.setObjectName("CandidateReportExpandAll")
        expand_all.clicked.connect(lambda: self._set_all_questions_expanded(True))
        toolbar.addWidget(expand_all)
        collapse_all = QtWidgets.QPushButton("Collapse All")
        collapse_all.setObjectName("CandidateReportCollapseAll")
        collapse_all.clicked.connect(lambda: self._set_all_questions_expanded(False))
        toolbar.addWidget(collapse_all)
        layout.addLayout(toolbar)
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                continue
            card, card_layout = self._card()
            card.setObjectName(f"CandidateReportQuestionCard_{index}")
            header = QtWidgets.QHBoxLayout()
            heading = QtWidgets.QLabel(f"Q{index + 1}  {question.get('title') or question.get('question_id') or 'Interview Question'}")
            heading.setStyleSheet("font-weight: 700; font-size: 14px;")
            header.addWidget(heading, 1)
            toggle = QtWidgets.QPushButton("Collapse")
            toggle.setAccessibleName(f"Toggle question {index + 1}")
            header.addWidget(toggle)
            card_layout.addLayout(header)
            body = QtWidgets.QWidget()
            body.setObjectName(f"CandidateReportQuestionBody_{index}")
            question_layout = QtWidgets.QVBoxLayout(body)
            question_layout.setContentsMargins(0, 0, 0, 0)
            question_layout.setSpacing(8)
            card_layout.addWidget(body)
            toggle.clicked.connect(lambda _checked=False, target=body, button=toggle: self._toggle_question(target, button))
            prompt = QtWidgets.QLabel(str(question.get("prompt") or ""))
            prompt.setWordWrap(True)
            prompt.setStyleSheet("font-weight: 600;")
            question_layout.addWidget(prompt)
            transcript = QtWidgets.QPlainTextEdit(str(question.get("transcript") or ""))
            transcript.setObjectName(f"CandidateReportTranscript_{index}")
            transcript.setReadOnly(True)
            transcript.setAccessibleName(f"Transcript for question {index + 1}")
            transcript.setMaximumHeight(130)
            question_layout.addWidget(transcript)
            notes = QtWidgets.QPlainTextEdit(str(question.get("interviewer_notes") or ""))
            notes.setPlaceholderText("Interviewer notes")
            notes.setReadOnly(not self.editing_initial)
            notes.setMaximumHeight(90)
            notes.textChanged.connect(self._mark_dirty)
            question_layout.addWidget(notes)
            evaluation_options = QtWidgets.QHBoxLayout()
            skipped = QtWidgets.QCheckBox("Skipped / N/A")
            skipped.setObjectName(f"CandidateReportSkipped_{index}")
            skipped.setChecked(bool(question.get("skipped", False)))
            skipped.setEnabled(self.editing_initial)
            skipped.toggled.connect(lambda checked, question_index=index: self._evaluation_changed(question_index, "skipped", checked))
            evaluation_options.addWidget(skipped)
            disqualifier = QtWidgets.QCheckBox("Absolute disqualifier")
            disqualifier.setObjectName(f"CandidateReportDisqualifier_{index}")
            disqualifier.setChecked(bool(question.get("absolute_disqualifier", False)))
            disqualifier.setEnabled(self.editing_initial)
            disqualifier.toggled.connect(lambda checked, question_index=index: self._evaluation_changed(question_index, "absolute_disqualifier", checked))
            evaluation_options.addWidget(disqualifier)
            no_example = QtWidgets.QCheckBox("No example after follow-ups")
            no_example.setObjectName(f"CandidateReportNoExample_{index}")
            no_example.setChecked(bool(question.get("no_example_after_followups", False)))
            no_example.setEnabled(self.editing_initial)
            no_example.toggled.connect(lambda checked, question_index=index: self._evaluation_changed(question_index, "no_example_after_followups", checked))
            evaluation_options.addWidget(no_example)
            evaluation_options.addStretch(1)
            question_layout.addLayout(evaluation_options)
            skip_reason = QtWidgets.QLineEdit(str(question.get("skip_reason") or ""))
            skip_reason.setObjectName(f"CandidateReportSkipReason_{index}")
            skip_reason.setPlaceholderText("Reason required when skipped")
            skip_reason.setReadOnly(not self.editing_initial)
            skip_reason.textChanged.connect(self._mark_dirty)
            skip_reason.setVisible(bool(question.get("skipped", False)) or self.editing_initial)
            question_layout.addWidget(skip_reason)
            correct = QtWidgets.QPushButton("Correct Transcript")
            correct.setVisible(self.editing_initial)
            correct.clicked.connect(lambda _checked=False, question_index=index: self._correct_transcript(question_index))
            question_layout.addWidget(correct, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
            self._question_widgets.append(
                {
                    "index": index,
                    "transcript": transcript,
                    "notes": notes,
                    "skipped": skipped,
                    "skip_reason": skip_reason,
                    "disqualifier": disqualifier,
                    "no_example": no_example,
                    "card": card,
                    "body": body,
                    "toggle": toggle,
                    "question": question,
                }
            )
            layout.addWidget(card)
        layout.addStretch(1)
        self.answer_search = search
        self.answer_filter = answer_filter
        search.textChanged.connect(self._filter_answer_cards)
        answer_filter.currentTextChanged.connect(self._filter_answer_cards)
        return page

    def _summary_page(self) -> Any:
        page, layout = self._page()
        scoring = self.working_snapshot.get("scoring") if isinstance(self.working_snapshot.get("scoring"), dict) else {}
        decision, decision_layout = self._card("Calculated Recommendation")
        outcome = QtWidgets.QLabel(str(scoring.get("outcome") or "Incomplete"))
        outcome.setObjectName("CandidateReportCalculatedOutcomeSummary")
        outcome.setProperty("reportMetric", True)
        decision_layout.addWidget(outcome)
        decision_layout.addWidget(QtWidgets.QLabel(f"{self._score_text(scoring)}   •   {self._percent_text(scoring)}   •   No manual outcome override"))
        layout.addWidget(decision)
        summaries = self.working_snapshot.get("summaries") if isinstance(self.working_snapshot.get("summaries"), dict) else {}
        for key, title in [
            ("executive_summary", "Executive Summary"), ("strengths", "Strengths"),
            ("concerns", "Concerns & Risks"), ("follow_up_items", "Follow-up Items"),
            ("recommendation_rationale", "Recommendation Rationale"),
        ]:
            card, card_layout = self._card(title)
            value = summaries.get(key, "")
            text = "\n".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
            editor = QtWidgets.QPlainTextEdit(text)
            editor.setReadOnly(not self.editing_initial)
            editor.setMaximumHeight(120)
            editor.textChanged.connect(self._mark_dirty)
            self._summary_widgets[key] = editor
            card_layout.addWidget(editor)
            layout.addWidget(card)
        if summaries.get("review_needed"):
            warning = QtWidgets.QLabel("Narratives may be stale after score or answer changes. Review recommended before finalizing.")
            warning.setStyleSheet("padding: 10px; color: #8a4b00; background: #fff7df; border: 1px solid #edc46b;")
            warning.setWordWrap(True)
            layout.insertWidget(1, warning)
        layout.addStretch(1)
        return page

    def _director_page(self) -> Any:
        page, layout = self._page()
        interview = self.director_interview
        card, card_layout = self._card("Director Interview")
        if str(getattr(interview, "state", "finalized")) == "reopened" and self.director_service is not None:
            self._build_director_editor(card_layout)
            layout.addWidget(card)
            save = QtWidgets.QPushButton("Save Director Changes")
            save.setObjectName("CandidateReportSaveDirectorButton")
            save.setProperty("primary", True)
            save.clicked.connect(self._save_director_changes)
            layout.addWidget(save, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
            layout.addStretch(1)
            return page
        form = QtWidgets.QFormLayout()
        values = [
            ("Director", getattr(interview, "director_name", "")),
            ("Interview Date", getattr(interview, "completed_date", "")),
            ("Rating", getattr(interview, "rating", "")),
            ("Decision", str(getattr(interview, "decision", "")).replace("_", " ").title()),
            ("Decision Notes", getattr(interview, "decision_notes", "")),
            ("Proposed Classroom", getattr(interview, "proposed_classroom", "")),
            ("Proposed Shift", f"{getattr(interview, 'proposed_shift_start', '')} - {getattr(interview, 'proposed_shift_end', '')}".strip(" -")),
        ]
        for label, value in values:
            display = QtWidgets.QLabel(str(value or "—"))
            display.setWordWrap(True)
            form.addRow(label, display)
        card_layout.addLayout(form)
        if getattr(interview, "initial_report_amended", False):
            warning = QtWidgets.QLabel("Initial interview score or recommendation changed after director submission.")
            warning.setWordWrap(True)
            warning.setStyleSheet("padding: 10px; color: #8a4b00; background: #fff7df; border: 1px solid #edc46b;")
            card_layout.addWidget(warning)
        layout.addWidget(card)
        if self.director_service is not None:
            reopen = QtWidgets.QPushButton("Reopen Director Interview")
            reopen.setObjectName("CandidateReportReopenDirectorButton")
            reopen.clicked.connect(self._prompt_reopen_director)
            layout.addWidget(reopen, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addStretch(1)
        return page

    def _build_director_editor(self, layout: Any) -> None:
        interview = self.director_interview
        form = QtWidgets.QFormLayout()
        self.director_name_editor = QtWidgets.QLineEdit(str(getattr(interview, "director_name", "")))
        self.director_date_editor = QtWidgets.QLineEdit(str(getattr(interview, "completed_date", "")))
        self.director_rating_editor = QtWidgets.QDoubleSpinBox()
        self.director_rating_editor.setRange(1, 10)
        self.director_rating_editor.setValue(float(getattr(interview, "rating", 1) or 1))
        self.director_decision_editor = QtWidgets.QComboBox()
        self.director_decision_editor.addItem("Hire", "hire")
        self.director_decision_editor.addItem("No Hire", "no_hire")
        decision_index = self.director_decision_editor.findData(str(getattr(interview, "decision", "no_hire")))
        self.director_decision_editor.setCurrentIndex(max(0, decision_index))
        self.director_notes_editor = QtWidgets.QPlainTextEdit(str(getattr(interview, "decision_notes", "")))
        self.director_notes_editor.setObjectName("CandidateReportDirectorNotes")
        self.director_notes_editor.setMaximumHeight(110)
        self.director_classroom_editor = QtWidgets.QLineEdit(str(getattr(interview, "proposed_classroom", "")))
        self.director_shift_start_editor = QtWidgets.QLineEdit(str(getattr(interview, "proposed_shift_start", "")))
        self.director_shift_end_editor = QtWidgets.QLineEdit(str(getattr(interview, "proposed_shift_end", "")))
        for label, editor in [
            ("Director", self.director_name_editor), ("Interview Date", self.director_date_editor),
            ("Rating", self.director_rating_editor), ("Decision", self.director_decision_editor),
            ("Decision Notes", self.director_notes_editor), ("Proposed Classroom", self.director_classroom_editor),
            ("Shift Start", self.director_shift_start_editor), ("Shift End", self.director_shift_end_editor),
        ]:
            form.addRow(label, editor)
        layout.addLayout(form)

    def _save_director_changes(self) -> None:
        if self.director_service is None or self.director_interview is None:
            return
        try:
            self.director_interview = self.director_service.revise_director_interview(
                self.director_interview.id,
                expected_row_version=self.director_interview.row_version,
                director_name=self.director_name_editor.text(),
                completed_date=self.director_date_editor.text(),
                rating=self.director_rating_editor.value(),
                decision=str(self.director_decision_editor.currentData() or ""),
                decision_notes=self.director_notes_editor.toPlainText(),
                proposed_shift_start=self.director_shift_start_editor.text(),
                proposed_shift_end=self.director_shift_end_editor.text(),
                proposed_classroom=self.director_classroom_editor.text(),
                reason=str(getattr(self.director_interview, "reopen_reason", "")),
                actor=self.actor,
                actor_role=self.role,
            )
        except StaffingStaleRevisionError:
            self._show_director_stale_dialog()
            return
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._rebuild()

    def _director_local_record(self) -> Any:
        return replace(
            self.director_interview,
            director_name=self.director_name_editor.text(),
            completed_date=self.director_date_editor.text(),
            rating=self.director_rating_editor.value(),
            decision=str(self.director_decision_editor.currentData() or ""),
            decision_notes=self.director_notes_editor.toPlainText(),
            proposed_shift_start=self.director_shift_start_editor.text(),
            proposed_shift_end=self.director_shift_end_editor.text(),
            proposed_classroom=self.director_classroom_editor.text(),
        )

    def _show_director_stale_dialog(self) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Director Interview Changed")
        box.setText("A newer Director Interview version was saved.")
        reload_button = box.addButton("Reload Latest", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        compare_button = box.addButton("Compare Changes", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Continue Editing", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is reload_button:
            self.director_interview = self.director_service.load_director_interview(self.director_interview.id)
            self.director_saved_snapshot = self.director_interview
            self._rebuild()
        elif box.clickedButton() is compare_button:
            differences = self.director_service.compare_director_interview_version(
                self._director_local_record(), saved=self.director_saved_snapshot,
            )
            self._show_comparison_dialog(
                [(item.field_name, item.saved_value, item.current_value, item.local_value) for item in differences],
                title="Compare Director Interview Changes",
            )

    def _audit_page(self) -> Any:
        page, layout = self._page()
        version_by_revision = {item.revision_id: item.version_number for item in self.repository.list_versions(self.history_id)}
        events: list[dict[str, Any]] = [
            {
                "revision_id": event.revision_id,
                "version": version_by_revision.get(event.revision_id, ""),
                "created_at": event.created_at,
                "actor": event.actor,
                "actor_role": event.actor_role,
                "action": event.action,
                "field_path": event.field_path or "Report",
                "old_value": event.old_value,
                "new_value": event.new_value,
                "reason": event.reason,
                "source": event.source,
            }
            for event in self.repository.list_audit_events(self.history_id)
        ]
        if self.director_interview is not None and self.director_service is not None:
            for event in self.director_service.list_director_interview_audit(self.director_interview.id):
                events.append(
                    {
                        "revision_id": str(event.get("revision_id") or ""),
                        "version": (event.get("new_value") or {}).get("version_number", "") if isinstance(event.get("new_value"), dict) else "",
                        "created_at": str(event.get("created_at") or ""),
                        "actor": str(event.get("actor") or ""),
                        "actor_role": str(event.get("actor_role") or ""),
                        "action": str(event.get("action") or "director_interview_changed"),
                        "field_path": "Director Interview",
                        "old_value": event.get("old_value"),
                        "new_value": event.get("new_value"),
                        "reason": str(event.get("reason") or ""),
                        "source": "Director Interview",
                    }
                )
        events.sort(key=lambda event: event["created_at"], reverse=True)
        self._audit_events = events
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addStretch(1)
        export_button = QtWidgets.QPushButton("Export Audit Log")
        export_button.setObjectName("CandidateReportExportAuditButton")
        export_button.clicked.connect(self._export_audit_log)
        toolbar.addWidget(export_button)
        layout.addLayout(toolbar)
        table = QtWidgets.QTableWidget(len(events), 7)
        table.setObjectName("CandidateReportAuditTable")
        table.setHorizontalHeaderLabels(["Version", "Date & Time", "User", "Action", "Section / Field", "Reason", "Source"])
        for row, event in enumerate(events):
            values = [event["version"] or event["revision_id"][:8], event["created_at"], event["actor"], event["action"].replace("_", " ").title(), event["field_path"], event["reason"] or "—", event["source"]]
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.currentCellChanged.connect(lambda row, _column, _old_row, _old_column: self._show_audit_event_details(row))
        layout.addWidget(table)
        details, details_layout = self._card("Change Details")
        details.setObjectName("CandidateReportAuditDetails")
        self.audit_details_text = QtWidgets.QPlainTextEdit()
        self.audit_details_text.setObjectName("CandidateReportAuditDetailsText")
        self.audit_details_text.setReadOnly(True)
        details_layout.addWidget(self.audit_details_text)
        layout.addWidget(details)
        if events:
            table.setCurrentCell(0, 0)
        layout.addStretch(1)
        return page

    def _show_audit_event_details(self, row: int) -> None:
        if not hasattr(self, "audit_details_text") or not 0 <= row < len(getattr(self, "_audit_events", [])):
            return
        event = self._audit_events[row]
        def display(value: Any) -> str:
            if value is None:
                return "Not captured"
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            return str(value)
        self.audit_details_text.setPlainText(
            f"Field: {event['field_path']}\nOld Value:\n{display(event.get('old_value'))}\n\n"
            f"New Value:\n{display(event.get('new_value'))}\n\nReason: {event.get('reason') or '—'}\n"
            f"Revision ID: {event.get('revision_id') or '—'}"
        )

    def _export_audit_log(self) -> None:
        destination, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Candidate Report Audit", "candidate-report-audit.csv", "CSV Files (*.csv)"
        )
        if not destination:
            return
        path = Path(destination)
        if path.suffix.casefold() != ".csv":
            path = path.with_suffix(".csv")
        try:
            export_candidate_report_audit_csv(self._audit_events, path)
        except (OSError, ValueError) as exc:
            self.status_label.setText(f"Audit export failed: {exc}")
            return
        self.status_label.setText("Audit log exported.")

    def _footer(self) -> Any:
        footer = QtWidgets.QFrame()
        footer.setObjectName("CandidateReportActionFooter")
        footer.setStyleSheet("background: white; border-top: 1px solid #dfe5ee;")
        row = QtWidgets.QHBoxLayout(footer)
        row.setContentsMargins(24, 10, 24, 10)
        row.addStretch(1)
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        self.open_word_button = QtWidgets.QPushButton("Open Word")
        self.open_word_button.setObjectName("CandidateReportOpenWordButton")
        self.open_word_button.clicked.connect(self._open_word)
        row.addWidget(self.open_word_button)
        self.reopen_button = QtWidgets.QPushButton("Reopen Report")
        self.reopen_button.setObjectName("CandidateReportReopenButton")
        self.reopen_button.clicked.connect(self._prompt_reopen)
        row.addWidget(self.reopen_button)
        self.revert_all_button = QtWidgets.QPushButton("Revert")
        self.revert_all_button.setObjectName("CandidateReportRevertAllButton")
        self.revert_all_button.clicked.connect(self.revert_unsaved_changes)
        row.addWidget(self.revert_all_button)
        self.save_draft_button = QtWidgets.QPushButton("Save Draft")
        self.save_draft_button.setObjectName("CandidateReportSaveDraftButton")
        self.save_draft_button.clicked.connect(self.save_draft)
        row.addWidget(self.save_draft_button)
        self.save_button = QtWidgets.QPushButton("Save Changes")
        self.save_button.setObjectName("CandidateReportSaveChangesButton")
        self.save_button.clicked.connect(self.save_changes)
        row.addWidget(self.save_button)
        self.finalize_button = QtWidgets.QPushButton("Finalize Report")
        self.finalize_button.setObjectName("CandidateReportFinalizeButton")
        self.finalize_button.setProperty("primary", True)
        self.finalize_button.clicked.connect(self.finalize_report)
        row.addWidget(self.finalize_button)
        return footer

    def reopen_initial(self, reason: str) -> None:
        self.record = self.repository.reopen(
            self.history_id,
            expected_row_version=self.record.row_version,
            reason=reason,
            actor=self.actor,
            role="admin",
            app_version=self.app_version,
        )
        self._rebuild()

    def save_draft(self) -> None:
        self._capture_widgets()
        try:
            self.record = self.repository.save_draft(
                self.history_id,
                self.working_snapshot,
                expected_row_version=self.record.row_version,
                actor=self.actor,
                role=self.role,
                app_version=self.app_version,
            )
        except CandidateReportStaleError:
            self._show_stale_dialog()
            return
        except CandidateReportPermissionError as exc:
            self.status_label.setText(str(exc))
            return
        self.saved_snapshot = copy.deepcopy(self.working_snapshot)
        self.dirty = False
        self._reset_field_change_indicators()
        self.status_label.setText(f"Draft saved as version {self.record.version_number}.")
        self._update_actions()

    def save_changes(self) -> None:
        self._capture_widgets()
        if self.rubric:
            self._refresh_score_preview()
        try:
            self.record = self.repository.save_changes(
                self.history_id,
                self.working_snapshot,
                expected_row_version=self.record.row_version,
                actor=self.actor,
                role=self.role,
                app_version=self.app_version,
            )
        except CandidateReportValidationError as exc:
            self.status_label.setText(" • ".join(issue.message for issue in exc.issues))
            self._show_validation_issues(exc.issues)
            return
        except CandidateReportStaleError:
            self._show_stale_dialog()
            return
        except CandidateReportPermissionError as exc:
            self.status_label.setText(str(exc))
            return
        self.saved_snapshot = copy.deepcopy(self.working_snapshot)
        self.dirty = False
        self._reset_field_change_indicators()
        self.status_label.setText(f"Validated changes saved as version {self.record.version_number}.")
        self._show_validation_issues(validate_candidate_report(self.working_snapshot))
        self._update_actions()

    def finalize_report(self) -> None:
        self._capture_widgets()
        if self.rubric:
            track = str((self.working_snapshot.get("candidate") or {}).get("track") or "")
            self.working_snapshot = recalculate_candidate_report(self.working_snapshot, rubric=self.rubric, track_key=track)
        try:
            self.record = self.repository.finalize(
                self.history_id,
                self.working_snapshot,
                expected_row_version=self.record.row_version,
                actor=self.actor,
                role=self.role,
                app_version=self.app_version,
            )
        except CandidateReportValidationError as exc:
            self.status_label.setText(" • ".join(issue.message for issue in exc.issues))
            self._show_validation_issues(exc.issues)
            return
        except CandidateReportStaleError:
            self._show_stale_dialog()
            return
        if self.finalized_callback is not None:
            self.finalized_callback(self.record)
        self.saved_snapshot = copy.deepcopy(self.record.snapshot)
        self.working_snapshot = copy.deepcopy(self.record.snapshot)
        self.dirty = False
        self._rebuild()

    def _capture_widgets(self) -> None:
        candidate = self.working_snapshot.setdefault("candidate", {})
        for key, widget in self._field_widgets.items():
            if key.startswith("qualification."):
                q_key = key.split(".", 1)[1]
                candidate.setdefault("qualification", {})[q_key] = widget.text()
            else:
                candidate[key] = widget.text()
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        for widgets in self._question_widgets:
            index = int(widgets["index"])
            if index < len(questions) and isinstance(questions[index], dict):
                questions[index]["interviewer_notes"] = widgets["notes"].toPlainText()
                questions[index]["transcript"] = widgets["transcript"].toPlainText()
                questions[index]["skipped"] = widgets["skipped"].isChecked()
                questions[index]["skip_reason"] = widgets["skip_reason"].text()
                questions[index]["absolute_disqualifier"] = widgets["disqualifier"].isChecked()
                questions[index]["no_example_after_followups"] = widgets["no_example"].isChecked()
        summaries = self.working_snapshot.setdefault("summaries", {})
        for key, editor in self._summary_widgets.items():
            text = editor.toPlainText()
            summaries[key] = [line.strip() for line in text.splitlines() if line.strip()] if key in {"strengths", "concerns", "follow_up_items"} else text

    def _editable_field_row(self, key: str, editor: Any) -> Any:
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(editor, 1)
        token = key.replace(".", "_")
        marker = QtWidgets.QLabel("Edited")
        marker.setObjectName(f"CandidateReportEdited_{token}")
        marker.setStyleSheet("color: #1455c0; background: #eaf2ff; padding: 3px 6px; border-radius: 4px;")
        marker.setVisible(False)
        row.addWidget(marker)
        revert = QtWidgets.QPushButton("Revert")
        revert.setObjectName(f"CandidateReportRevert_{token}")
        revert.setVisible(False)
        revert.clicked.connect(lambda _checked=False, field_key=key: self._revert_field(field_key))
        row.addWidget(revert)
        self._field_markers[key] = marker
        self._field_revert_buttons[key] = revert
        return container

    def _saved_field_value(self, key: str) -> str:
        candidate = self.saved_snapshot.get("candidate") if isinstance(self.saved_snapshot.get("candidate"), dict) else {}
        if key.startswith("qualification."):
            qualification = candidate.get("qualification") if isinstance(candidate.get("qualification"), dict) else {}
            value = qualification.get(key.split(".", 1)[1])
        else:
            value = candidate.get(key)
        return str(value if value is not None else "")

    def _field_changed(self, key: str) -> None:
        if not self.editing_initial or key not in self._field_widgets:
            return
        edited = self._field_widgets[key].text() != self._saved_field_value(key)
        self._field_markers[key].setVisible(edited)
        self._field_revert_buttons[key].setVisible(edited)
        candidate = self.working_snapshot.setdefault("candidate", {})
        if key.startswith("qualification."):
            candidate.setdefault("qualification", {})[key.split(".", 1)[1]] = self._field_widgets[key].text()
        else:
            candidate[key] = self._field_widgets[key].text()
        self.dirty = self.working_snapshot != self.saved_snapshot
        self.status_label.setText("Unsaved changes" if self.dirty else "")
        self._update_actions()

    def _reset_field_change_indicators(self) -> None:
        for marker in self._field_markers.values():
            marker.setVisible(False)
        for button in self._field_revert_buttons.values():
            button.setVisible(False)

    def _revert_field(self, key: str) -> None:
        widget = self._field_widgets.get(key)
        if widget is not None:
            widget.setText(self._saved_field_value(key))

    def revert_unsaved_changes(self) -> None:
        self.working_snapshot = copy.deepcopy(self.saved_snapshot)
        self.dirty = False
        self._rebuild()

    def _rating_changed(self, question_index: int, value: int) -> None:
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        if question_index >= len(questions) or not isinstance(questions[question_index], dict):
            return
        questions[question_index]["rating"] = int(value)
        self.working_snapshot.setdefault("summaries", {})["review_needed"] = True
        self._refresh_score_preview()
        self._filter_answer_cards()
        self._mark_dirty()

    def _refresh_score_preview(self) -> None:
        if not self.rubric:
            return
        candidate = self.working_snapshot.get("candidate") if isinstance(self.working_snapshot.get("candidate"), dict) else {}
        self.working_snapshot = recalculate_candidate_report(
            self.working_snapshot,
            rubric=self.rubric,
            track_key=str(candidate.get("track") or ""),
        )
        scoring = self.working_snapshot.get("scoring") if isinstance(self.working_snapshot.get("scoring"), dict) else {}
        summary = self.findChild(self.QtWidgets.QLabel, "CandidateReportScoreSummary")
        if summary is not None:
            summary.setText(f"{self._score_text(scoring)}   •   {self._percent_text(scoring)}   •   Calculated: {scoring.get('outcome', 'Incomplete')}")
        for object_name in ("CandidateReportCalculatedOutcome", "CandidateReportCalculatedOutcomeSummary"):
            outcome = self.findChild(self.QtWidgets.QLabel, object_name)
            if outcome is not None:
                outcome.setText(str(scoring.get("outcome") or "Incomplete"))

    def _evaluation_changed(self, question_index: int, field: str, checked: bool) -> None:
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        if question_index >= len(questions) or not isinstance(questions[question_index], dict):
            return
        questions[question_index][field] = bool(checked)
        self.working_snapshot.setdefault("summaries", {})["review_needed"] = True
        self._refresh_score_preview()
        self._mark_dirty()

    def _show_validation_issues(self, issues: list[Any]) -> None:
        self._clear_layout(self.readiness_layout)
        for widgets in self._question_widgets:
            widgets["skip_reason"].setStyleSheet("")
        if not issues:
            self.readiness_panel.setVisible(False)
            return
        blocking = sum(1 for issue in issues if issue.severity == "blocking")
        warnings = sum(1 for issue in issues if issue.severity == "warning")
        title = QtWidgets.QLabel(f"Finalization Readiness — {blocking} blocking, {warnings} warnings")
        title.setStyleSheet("font-weight: 700;")
        self.readiness_layout.addWidget(title)
        for index, issue in enumerate(issues):
            button = QtWidgets.QPushButton(f"{issue.severity.title()}: {issue.message}")
            button.setObjectName(f"CandidateReportValidationIssue_{index}")
            button.setAccessibleName(f"Go to {issue.field_path or 'report'}")
            button.clicked.connect(lambda _checked=False, path=issue.field_path: self._navigate_to_issue(path))
            self.readiness_layout.addWidget(button)
            target = self._validation_widget_for_path(issue.field_path)
            if target is not None and issue.severity == "blocking":
                target.setStyleSheet("border: 2px solid #dc2626; background: #fff7f7;")
        self.readiness_panel.setVisible(True)

    def _validation_widget_for_path(self, field_path: str) -> Any | None:
        parts = str(field_path or "").split(".")
        if len(parts) >= 3 and parts[0] == "questions" and parts[1].isdigit():
            widgets = next((item for item in self._question_widgets if item["index"] == int(parts[1])), None)
            if widgets is not None:
                return widgets.get("skip_reason" if parts[2] == "skip_reason" else "notes")
        if parts and parts[0] == "candidate":
            return self._field_widgets.get(".".join(parts[1:]))
        return None

    def _navigate_to_issue(self, field_path: str) -> None:
        path = str(field_path or "")
        if path.startswith("candidate"):
            self.navigation.setCurrentRow(1)
            key = path.removeprefix("candidate.")
            widget = self._field_widgets.get(key)
        elif path.startswith("questions."):
            self.navigation.setCurrentRow(3)
            parts = path.split(".")
            index = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
            field = parts[2] if len(parts) > 2 else ""
            widgets = next((item for item in self._question_widgets if item["index"] == index), None)
            widget = widgets.get("skip_reason" if field == "skip_reason" else "notes") if widgets else None
        elif path.startswith("summaries"):
            self.navigation.setCurrentRow(4)
            widget = next(iter(self._summary_widgets.values()), None)
        else:
            self.navigation.setCurrentRow(0)
            widget = None
        if widget is not None:
            widget.setFocus()

    def _correct_transcript(self, question_index: int) -> None:
        questions = self.working_snapshot.get("questions") if isinstance(self.working_snapshot.get("questions"), list) else []
        if question_index >= len(questions) or not isinstance(questions[question_index], dict):
            return
        current = str(questions[question_index].get("transcript") or "")
        text, accepted = QtWidgets.QInputDialog.getMultiLineText(self, "Correct Transcript", "Corrected transcript:", current)
        if not accepted or text == current:
            return
        reason, reason_ok = QtWidgets.QInputDialog.getText(self, "Transcript Correction Reason", "Reason for correction:")
        if not reason_ok or not str(reason).strip():
            self.status_label.setText("Transcript correction reason is required.")
            return
        questions[question_index]["transcript"] = text
        questions[question_index]["transcript_correction_reason"] = str(reason).strip()
        for widgets in self._question_widgets:
            if int(widgets["index"]) == question_index:
                widgets["transcript"].setPlainText(text)
        self._mark_dirty()

    def _filter_answer_cards(self, _value: str = "") -> None:
        needle = str(self.answer_search.text() if hasattr(self, "answer_search") else "").strip().casefold()
        selected = str(self.answer_filter.currentText() if hasattr(self, "answer_filter") else "All")
        for widgets in self._question_widgets:
            transcript = widgets["transcript"]
            question = widgets["question"]
            text = f"{question.get('title', '')} {question.get('prompt', '')} {transcript.toPlainText()} {widgets['notes'].toPlainText()}".casefold()
            skipped = widgets["skipped"].isChecked()
            flagged = widgets["disqualifier"].isChecked() or widgets["no_example"].isChecked()
            try:
                low_score = not skipped and int(question.get("rating") or 0) <= 2
            except (TypeError, ValueError):
                low_score = False
            state_match = {
                "All": True, "Scored": not skipped, "Skipped": skipped,
                "Flagged": flagged, "Low Score": low_score,
            }.get(selected, True)
            widgets["card"].setVisible(state_match and (not needle or needle in text))

    def _toggle_question(self, body: Any, button: Any) -> None:
        expanded = body.isHidden()
        body.setVisible(expanded)
        button.setText("Collapse" if expanded else "Expand")

    def _set_all_questions_expanded(self, expanded: bool) -> None:
        for widgets in self._question_widgets:
            widgets["body"].setVisible(expanded)
            widgets["toggle"].setText("Collapse" if expanded else "Expand")

    def _mark_dirty(self, *_args: Any) -> None:
        if not self.editing_initial:
            return
        self.dirty = True
        self.status_label.setText("Unsaved changes")
        self._update_actions()

    def _update_actions(self) -> None:
        self.open_word_button.setEnabled(True)
        self.reopen_button.setVisible(self.role == "admin" and self.record.state == "finalized")
        self.revert_all_button.setVisible(self.editing_initial)
        self.revert_all_button.setEnabled(self.dirty)
        for button in (self.save_draft_button, self.save_button, self.finalize_button):
            button.setVisible(self.role == "admin")
            button.setEnabled(self.editing_initial and (self.dirty or button is self.finalize_button))

    def _prompt_reopen(self) -> None:
        reason, accepted = QtWidgets.QInputDialog.getText(self, "Reopen Candidate Report", "Reason for reopening:")
        if accepted and str(reason).strip():
            self.reopen_initial(str(reason).strip())

    def _prompt_reopen_director(self) -> None:
        reason, accepted = QtWidgets.QInputDialog.getText(self, "Reopen Director Interview", "Reason for reopening:")
        if not accepted or not str(reason).strip() or self.director_service is None:
            return
        self.director_interview = self.director_service.reopen_director_interview(
            self.director_interview.id,
            expected_row_version=self.director_interview.row_version,
            reason=str(reason).strip(),
            actor=self.actor,
            actor_role=self.role,
        )
        self.director_saved_snapshot = self.director_interview
        self._rebuild()

    def _show_stale_dialog(self) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Report Changed")
        box.setText("A newer report version was saved. Local edits were preserved in this window.")
        reload_button = box.addButton("Reload Latest", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        compare_button = box.addButton("Compare Changes", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        force_button = None
        if self.role == "admin":
            force_button = box.addButton("Save as New Version", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Continue Editing", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is reload_button:
            self.record = self.repository.load_visible_version(self.history_id, role=self.role)
            self._rebuild()
        elif box.clickedButton() is compare_button:
            differences = self.repository.compare_version(
                self.history_id, self.working_snapshot, role=self.role, saved_snapshot=self.saved_snapshot,
            )
            self._show_comparison_dialog(
                [(item.field_path, item.saved_value, item.current_value, item.local_value) for item in differences]
            )
        elif force_button is not None and box.clickedButton() is force_button:
            reason, accepted = QtWidgets.QInputDialog.getText(self, "Save New Version", "Reason for preserving local edits:")
            if accepted and str(reason).strip():
                self.record = self.repository.save_draft(
                    self.history_id, self.working_snapshot, expected_row_version=self.record.row_version,
                    actor=self.actor, role="admin", reason=str(reason).strip(), force=True, app_version=self.app_version,
                )
                self._rebuild()

    def _show_comparison_dialog(self, rows: list[tuple[str, Any, Any, Any]], *, title: str = "Compare Report Changes") -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setObjectName("CandidateReportComparisonDialog")
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(900, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["Field", "Last Saved", "Current Database", "Local"])
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                display = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value if value is not None else "Not captured")
                table.setItem(row_index, column, QtWidgets.QTableWidgetItem(display))
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        self.comparison_dialog = dialog
        dialog.show()

    def _open_word(self) -> None:
        path = Path(str(self.working_snapshot.get("report_path") or ""))
        if path.suffix.casefold() != ".docx":
            candidate = self.working_snapshot.get("candidate")
            candidate = candidate if isinstance(candidate, dict) else {}
            filename = sanitize_filename(
                " - ".join(
                    part
                    for part in (
                        str(candidate.get("interview_date") or "").strip(),
                        str(candidate.get("school") or "").strip(),
                        str(candidate.get("candidate_name") or "Candidate").strip(),
                        "Basic Interview Notes",
                    )
                    if part
                )
            )
            path = self.repository.db_path.parent / "Indeed Interview Notes" / f"{filename}.docx"
        try:
            if not path.is_file():
                path = generate_basic_candidate_notes_document(self.working_snapshot, path)
                self.record = self.repository.sync_report_path(
                    self.history_id,
                    path,
                    app_version=self.app_version,
                )
                self.working_snapshot["report_path"] = str(path)
                self.saved_snapshot["report_path"] = str(path)
            self.open_document(path.resolve())
        except (OSError, ValueError, CandidateReportValidationError) as exc:
            self.status_label.setText(f"Saved Word report could not be opened: {exc}")

    @staticmethod
    def _default_open_document(path: Path) -> None:
        os.startfile(str(path))

    def _rebuild(self) -> None:
        self.record = self.repository.load_visible_version(
            self.history_id, role=self.role, school_scope=self.school_scope if self.role == "director" else ""
        )
        self.working_snapshot = copy.deepcopy(self.record.snapshot)
        self.saved_snapshot = copy.deepcopy(self.record.snapshot)
        self.dirty = False
        geometry = self.saveGeometry()
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            if child_layout is not None:
                self._clear_layout(child_layout)
        self._field_widgets = {}
        self._field_markers = {}
        self._field_revert_buttons = {}
        self._question_widgets = []
        self._summary_widgets = {}
        self._build()
        self.restoreGeometry(geometry)

    @classmethod
    def _clear_layout(cls, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
                item.widget().deleteLater()
            if item.layout() is not None:
                cls._clear_layout(item.layout())

    @staticmethod
    def _score_text(scoring: dict[str, Any]) -> str:
        earned = scoring.get("weighted_total", scoring.get("earned_total", "—"))
        possible = scoring.get("max_weighted_total", scoring.get("adjusted_maximum", "—"))
        return f"{earned} / {possible}"

    @staticmethod
    def _percent_text(scoring: dict[str, Any]) -> str:
        try:
            return f"{float(scoring.get('percent_of_max', 0)):.1f}%"
        except (TypeError, ValueError):
            return "0.0%"

    def _restore_geometry(self) -> None:
        settings = QtCore.QSettings("LaunchPadLearning", "CandidateInterviewReport")
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        parent = self.parentWidget()
        if parent is not None:
            maximum_width = max(self.minimumWidth(), parent.width() - 32)
            maximum_height = max(self.minimumHeight(), parent.height() - 32)
            self.resize(min(self.width(), maximum_width), min(self.height(), maximum_height))
        screens = self.QtWidgets.QApplication.screens()
        if screens and not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in screens):
            target = self.parentWidget().screen().availableGeometry() if self.parentWidget() is not None else screens[0].availableGeometry()
            self.resize(min(1220, target.width()), min(780, target.height()))
            self.move(target.center() - self.rect().center())

    def showEvent(self, event: Any) -> None:
        if self._scrim is not None:
            self._scrim.setGeometry(self.parentWidget().rect())
            self._scrim.show()
            self._scrim.raise_()
        super().showEvent(event)

    def closeEvent(self, event: Any) -> None:
        if self.dirty:
            response = QtWidgets.QMessageBox.question(
                self,
                "Unsaved Changes",
                "Save report draft before closing?",
                QtWidgets.QMessageBox.StandardButton.Save
                | QtWidgets.QMessageBox.StandardButton.Discard
                | QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if response == QtWidgets.QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if response == QtWidgets.QMessageBox.StandardButton.Save:
                self.save_draft()
                if self.dirty:
                    event.ignore()
                    return
        if not self.isMaximized():
            QtCore.QSettings("LaunchPadLearning", "CandidateInterviewReport").setValue("geometry", self.saveGeometry())
        if self._scrim is not None:
            self._scrim.hide()
        application = self.QtWidgets.QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        event.accept()
