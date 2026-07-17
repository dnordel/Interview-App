from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from pyside_interview_components import AdaptiveTwoColumn, build_candidate_identity


class CompletionState(str, Enum):
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class CompletedQuestionRow:
    flow_index: int
    question_id: str
    kind: str
    category: str
    title: str
    prompt: str
    transcript: str
    notes: str
    flags: tuple[str, ...]
    skipped: bool
    rating: int | None = None
    priority: str = ""
    weight: float = 0.0

    @property
    def weighted_score(self) -> float:
        return float(self.rating or 0) * self.weight

    @property
    def weighted_max(self) -> float:
        return 5.0 * self.weight


@dataclass(frozen=True)
class CompletedInterviewCallbacks:
    back: Callable[[], None]
    open_report: Callable[[], None]
    export: Callable[[], None]
    finish: Callable[[], None]
    retry: Callable[[], None]
    edit_question: Callable[[str], None]


@dataclass(frozen=True)
class CompletedInterviewViewModel:
    candidate_name: str
    school: str
    position: str
    total_steps: int
    completion_state: CompletionState
    warning: str = ""
    interview_type: str = "First Interview"
    can_finish: bool = False
    weighted_total: float = 0.0
    max_weighted_total: float = 0.0
    percent_of_max: float = 0.0
    outcome: str = "Incomplete"
    scored_count: int = 0
    skipped_count: int = 0
    trait_rows: tuple[CompletedQuestionRow, ...] = ()
    question_rows: tuple[CompletedQuestionRow, ...] = ()
    strengths: tuple[str, ...] = ()
    review_items: tuple[str, ...] = ()


def build_completed_interview_view_model(
    *,
    candidate_name: str,
    school: str,
    position: str,
    workflow: Sequence[Any],
    answers: Mapping[str, Mapping[str, Any]],
    transcripts: Mapping[int, str],
    scoring: Mapping[str, Any],
    completion_state: CompletionState,
    warning: str = "",
) -> CompletedInterviewViewModel:
    question_rows: list[CompletedQuestionRow] = []
    strengths: list[str] = []
    review_items: list[str] = []
    missing_rating = False
    for flow_index, question in enumerate(workflow):
        if str(getattr(question, "kind", "")) == "intro":
            continue
        question_id = str(getattr(question, "question_id", "") or "")
        kind = str(getattr(question, "kind", "") or "")
        answer = answers.get(question_id, {}) or {}
        score_text = str(answer.get("score", "") or "").strip()
        rating = int(score_text) if score_text.isdigit() and 1 <= int(score_text) <= 5 else None
        skipped = bool(answer.get("skipped", False))
        flags = tuple(str(value) for value in answer.get("quick_actions", []) or [] if str(value).strip())
        title = str(getattr(question, "title", "") or getattr(question, "prompt", "") or question_id)
        row = CompletedQuestionRow(
            flow_index=flow_index,
            question_id=question_id,
            kind=kind,
            category="Scored" if kind == "trait" else "Non-scored",
            title=title,
            prompt=str(getattr(question, "prompt", "") or ""),
            transcript=str(transcripts.get(flow_index, "") or "").strip(),
            notes=str(answer.get("notes", "") or "").strip(),
            flags=flags,
            skipped=skipped,
            rating=rating,
            priority=str(getattr(question, "priority", "") or ""),
            weight=float(getattr(question, "weight", 0.0) or 0.0),
        )
        question_rows.append(row)
        if kind != "trait":
            continue
        if skipped:
            review_items.append(f"{title}: Question Skipped")
        elif rating is None:
            missing_rating = True
            review_items.append(f"{title}: Missing rating")
        elif rating == 5:
            strengths.append(f"{title}: {rating} / 5")
        elif rating <= 3:
            review_items.append(f"{title}: {rating} / 5")
        review_items.extend(f"{title}: {flag}" for flag in flags)
    trait_rows = tuple(row for row in question_rows if row.kind == "trait")
    return CompletedInterviewViewModel(
        candidate_name=candidate_name,
        school=school,
        position=position,
        total_steps=len(question_rows),
        completion_state=completion_state,
        warning=warning,
        can_finish=completion_state is CompletionState.COMPLETE and not missing_rating,
        weighted_total=float(scoring.get("weighted_total", 0.0) or 0.0),
        max_weighted_total=float(scoring.get("max_weighted_total", 0.0) or 0.0),
        percent_of_max=float(scoring.get("percent_of_max", 0.0) or 0.0),
        outcome=str(scoring.get("outcome", "Incomplete") or "Incomplete"),
        scored_count=sum(1 for row in trait_rows if row.rating is not None),
        skipped_count=sum(1 for row in trait_rows if row.skipped),
        trait_rows=trait_rows,
        question_rows=tuple(question_rows),
        strengths=tuple(strengths),
        review_items=tuple(review_items),
    )


def build_completed_transcript_export(model: CompletedInterviewViewModel) -> str:
    lines = [
        f"Candidate: {model.candidate_name}",
        f"School: {model.school}",
        f"Position: {model.position}",
        "",
    ]
    for index, row in enumerate(model.question_rows, start=1):
        lines.append(f"{index}. {row.title}")
        lines.append("Question Skipped" if row.skipped else row.transcript or "No candidate transcript captured.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class CompletedInterviewPage:
    def __init__(self, *, QtCore: Any, QtWidgets: Any, callbacks: CompletedInterviewCallbacks) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.callbacks = callbacks
        self.root: Any | None = None
        self._adaptive: AdaptiveTwoColumn | None = None
        self._search: Any | None = None
        self._filter: Any | None = None
        self._transcript_cards: list[Any] = []

    def render(self, model: CompletedInterviewViewModel) -> Any:
        QtWidgets = self.QtWidgets
        self._completion_state = model.completion_state
        root = QtWidgets.QWidget()
        root.setObjectName("CompletedInterviewPage")
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Interview Complete")
        title.setObjectName("CompletedInterviewTitle")
        subtitle = QtWidgets.QLabel("Review the score and captured transcripts before finishing.")
        subtitle.setObjectName("CompletedInterviewSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        header = QtWidgets.QFrame()
        header.setObjectName("CompletedInterviewHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.addWidget(
            build_candidate_identity(
                QtWidgets=QtWidgets,
                candidate_name=model.candidate_name,
                school=model.school,
                position=model.position,
                interview_type=model.interview_type,
                object_prefix="CompletedInterview",
            ),
            1,
        )
        status_box = QtWidgets.QWidget()
        status_layout = QtWidgets.QVBoxLayout(status_box)
        status_text = {
            CompletionState.PROCESSING: "Finalizing interview",
            CompletionState.COMPLETE: "Interview complete",
            CompletionState.FAILED: "Finalization failed",
        }[model.completion_state]
        status = QtWidgets.QLabel(status_text)
        status.setObjectName("CompletedInterviewStatus")
        status_layout.addWidget(status)
        progress = QtWidgets.QProgressBar()
        progress.setObjectName("CompletedInterviewProgress")
        progress.setRange(0, 100)
        progress.setValue(100 if model.completion_state is CompletionState.COMPLETE else 65 if model.completion_state is CompletionState.PROCESSING else 0)
        status_layout.addWidget(progress)
        status_layout.addWidget(QtWidgets.QLabel(f"{model.total_steps} of {model.total_steps} steps completed"))
        header_layout.addWidget(status_box, 2)
        layout.addWidget(header)

        warning = QtWidgets.QLabel(model.warning)
        warning.setObjectName("CompletedInterviewWarning")
        warning.setWordWrap(True)
        warning.setVisible(bool(model.warning))
        layout.addWidget(warning)

        left = QtWidgets.QFrame()
        left.setObjectName("CompletedInterviewSummaryColumn")
        left_layout = QtWidgets.QVBoxLayout(left)
        score_heading = QtWidgets.QLabel("Score Summary")
        score_heading.setObjectName("CompletedInterviewScoreHeading")
        left_layout.addWidget(score_heading)
        score = QtWidgets.QLabel(f"{model.weighted_total:g} / {model.max_weighted_total:g}")
        score.setObjectName("CompletedInterviewWeightedScore")
        left_layout.addWidget(score)
        percent = QtWidgets.QLabel(f"{model.percent_of_max:g}%")
        percent.setObjectName("CompletedInterviewPercent")
        left_layout.addWidget(percent)
        outcome = QtWidgets.QLabel(f"Calculated: {model.outcome}")
        outcome.setObjectName("CompletedInterviewOutcome")
        left_layout.addWidget(outcome)
        counts = QtWidgets.QLabel(f"{model.scored_count} scored questions  |  {model.skipped_count} skipped")
        counts.setObjectName("CompletedInterviewScoreCounts")
        left_layout.addWidget(counts)
        trait_table = QtWidgets.QTableWidget(len(model.trait_rows), 4)
        trait_table.setObjectName("CompletedInterviewTraitTable")
        trait_table.setHorizontalHeaderLabels(["Trait", "Rating", "Weight", "Weighted"])
        trait_table.verticalHeader().setVisible(False)
        trait_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for table_row, trait in enumerate(model.trait_rows):
            values = (
                trait.title,
                "Skipped" if trait.skipped else f"{trait.rating} / 5" if trait.rating is not None else "Missing",
                f"{trait.weight:g}x",
                "-" if trait.skipped or trait.rating is None else f"{trait.weighted_score:g} / {trait.weighted_max:g}",
            )
            for column, value in enumerate(values):
                trait_table.setItem(table_row, column, QtWidgets.QTableWidgetItem(value))
        header_view = trait_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for column in range(1, 4):
            header_view.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            header_text = trait_table.horizontalHeaderItem(column).text()
            trait_table.setColumnWidth(column, max(trait_table.columnWidth(column), trait_table.fontMetrics().horizontalAdvance(header_text) + 22))
        table_height = header_view.sizeHint().height() + trait_table.verticalHeader().defaultSectionSize() * len(model.trait_rows) + 4
        trait_table.setFixedHeight(table_height)
        left_layout.addWidget(trait_table)
        summaries = QtWidgets.QHBoxLayout()
        for heading, values, object_name in (
            ("Strengths", model.strengths, "CompletedInterviewStrengths"),
            ("Review Items", model.review_items, "CompletedInterviewReviewItems"),
        ):
            card = QtWidgets.QFrame()
            card.setObjectName(object_name)
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.addWidget(QtWidgets.QLabel(heading))
            empty = not values
            for value in values or ("None identified based on scores.",):
                label = QtWidgets.QLabel(value if empty else f"-  {value}")
                if empty:
                    label.setObjectName("CompletedInterviewEmptySummary")
                label.setWordWrap(True)
                card_layout.addWidget(label)
            card_layout.addStretch(1)
            summaries.addWidget(card)
        left_layout.addLayout(summaries)
        right = QtWidgets.QFrame()
        right.setObjectName("CompletedInterviewTranscriptColumn")
        right_layout = QtWidgets.QVBoxLayout(right)
        transcript_heading = QtWidgets.QLabel("Captured Transcripts")
        transcript_heading.setObjectName("CompletedInterviewTranscriptHeading")
        right_layout.addWidget(transcript_heading)
        controls = QtWidgets.QHBoxLayout()
        search = QtWidgets.QLineEdit()
        search.setObjectName("CompletedTranscriptSearch")
        search.setPlaceholderText("Search questions or transcripts")
        controls.addWidget(search, 1)
        filter_box = QtWidgets.QComboBox()
        filter_box.setObjectName("CompletedTranscriptFilter")
        filter_box.addItems(["All questions", "Non-scored", "Scored", "Flagged"])
        controls.addWidget(filter_box)
        right_layout.addLayout(controls)
        captured_count = sum(1 for row in model.question_rows if row.transcript and not row.skipped)
        captured = QtWidgets.QLabel(f"{captured_count} responses captured")
        captured.setObjectName("CompletedTranscriptCapturedCount")
        right_layout.addWidget(captured)
        transcript_scroll = QtWidgets.QScrollArea()
        transcript_scroll.setObjectName("CompletedTranscriptListScroll")
        transcript_scroll.setWidgetResizable(True)
        transcript_scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        transcript_scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        transcript_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        transcript_content = QtWidgets.QWidget()
        transcript_content.setObjectName("CompletedTranscriptListContent")
        transcript_layout = QtWidgets.QVBoxLayout(transcript_content)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        transcript_layout.setSpacing(8)
        self._transcript_cards = []
        for display_index, row in enumerate(model.question_rows, start=1):
            card = self._build_transcript_card(display_index, row)
            self._transcript_cards.append(card)
            transcript_layout.addWidget(card)
        transcript_layout.addStretch(1)
        transcript_scroll.setWidget(transcript_content)
        transcript_scroll.setMinimumHeight(320)
        transcript_scroll.setMaximumHeight(500)
        right_layout.addWidget(transcript_scroll, 1)
        self._search = search
        self._filter = filter_box
        search.textChanged.connect(self._apply_transcript_filter)
        filter_box.currentTextChanged.connect(self._apply_transcript_filter)
        self._adaptive = AdaptiveTwoColumn(
            QtWidgets=QtWidgets,
            object_name="CompletedInterviewAdaptiveContent",
            left=left,
            right=right,
            left_stretch=5,
            right_stretch=7,
        )
        layout.addWidget(self._adaptive.widget, 1)

        actions = QtWidgets.QHBoxLayout()
        enabled = model.completion_state is CompletionState.COMPLETE
        back = QtWidgets.QPushButton("Back to Last Question")
        back.setObjectName("CompletedInterviewBack")
        back.setEnabled(enabled)
        back.clicked.connect(self.callbacks.back)
        actions.addWidget(back)
        report = QtWidgets.QPushButton("Open Full Report")
        report.setObjectName("CompletedInterviewReport")
        report.setEnabled(enabled)
        report.clicked.connect(self.callbacks.open_report)
        actions.addWidget(report)
        export = QtWidgets.QPushButton("Export")
        export.setObjectName("CompletedInterviewExport")
        export.setEnabled(enabled)
        export.clicked.connect(self.callbacks.export)
        actions.addWidget(export)
        if model.completion_state is CompletionState.FAILED:
            retry = QtWidgets.QPushButton("Retry Finalization")
            retry.setObjectName("CompletedInterviewRetry")
            retry.clicked.connect(self.callbacks.retry)
            actions.addWidget(retry)
        finish = QtWidgets.QPushButton("Save && Finish")
        finish.setAccessibleName("Save & Finish")
        finish.setObjectName("CompletedInterviewFinish")
        finish.setEnabled(enabled and model.can_finish)
        finish.clicked.connect(self.callbacks.finish)
        actions.addWidget(finish)
        layout.addLayout(actions)

        root.setStyleSheet(self._stylesheet())
        self.root = root
        return root

    def set_narrow(self, narrow: bool) -> None:
        if self._adaptive is not None:
            self._adaptive.set_narrow(narrow)

    def _build_transcript_card(self, display_index: int, row: CompletedQuestionRow) -> Any:
        QtWidgets = self.QtWidgets
        card = QtWidgets.QFrame()
        card.setObjectName("CompletedTranscriptCard")
        card.setProperty("questionId", row.question_id)
        card.setProperty("category", row.category)
        card.setProperty("flagged", bool(row.flags))
        card.setProperty("skipped", row.skipped)
        card.setProperty(
            "searchText",
            " ".join((row.question_id, row.title, row.prompt, row.transcript, row.notes, " ".join(row.flags))).casefold(),
        )
        layout = QtWidgets.QVBoxLayout(card)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(QtWidgets.QLabel(str(display_index)))
        badge = QtWidgets.QLabel(row.priority or row.category)
        badge.setObjectName("CompletedTranscriptBadge")
        heading.addWidget(badge)
        title = QtWidgets.QLabel(row.title)
        title.setObjectName("CompletedTranscriptTitle")
        title.setWordWrap(True)
        heading.addWidget(title, 1)
        if row.skipped:
            heading.addWidget(QtWidgets.QLabel("Question Skipped"))
            layout.addLayout(heading)
            skipped = QtWidgets.QLabel("Question Skipped")
            skipped.setObjectName("CompletedTranscriptSkipped")
            layout.addWidget(skipped)
            return card
        status = "Captured" if row.transcript else "Missing"
        if row.rating is not None:
            status = f"Rating {row.rating} / 5"
        heading.addWidget(QtWidgets.QLabel(status))
        toggle = QtWidgets.QToolButton()
        toggle.setObjectName("CompletedTranscriptToggle")
        toggle.setText("v")
        toggle.setCheckable(True)
        heading.addWidget(toggle)
        layout.addLayout(heading)
        excerpt_text = row.transcript or "No candidate transcript captured."
        excerpt = QtWidgets.QLabel(excerpt_text[:180] + ("..." if len(excerpt_text) > 180 else ""))
        excerpt.setObjectName("CompletedTranscriptExcerpt")
        excerpt.setWordWrap(True)
        excerpt.setMaximumHeight(excerpt.fontMetrics().lineSpacing() * 2 + 8)
        layout.addWidget(excerpt)
        full = QtWidgets.QLabel(row.transcript or "No candidate transcript captured.")
        full.setObjectName("CompletedTranscriptFullText")
        full.setWordWrap(True)
        full.hide()
        layout.addWidget(full)
        expanded: list[Any] = [full]
        notes = QtWidgets.QLabel(row.notes or "No interviewer notes.")
        notes.setObjectName("CompletedTranscriptExpandedNotes")
        notes.setWordWrap(True)
        notes.hide()
        layout.addWidget(notes)
        expanded.append(notes)
        flags = QtWidgets.QLabel(", ".join(row.flags) if row.flags else "No flags.")
        flags.setObjectName("CompletedTranscriptExpandedFlags")
        flags.setWordWrap(True)
        flags.hide()
        layout.addWidget(flags)
        expanded.append(flags)
        rating = QtWidgets.QLabel(f"Rating {row.rating} / 5" if row.rating is not None else "Non-scored response")
        rating.setObjectName("CompletedTranscriptExpandedRating")
        rating.hide()
        layout.addWidget(rating)
        expanded.append(rating)
        for widget in expanded:
            toggle.toggled.connect(widget.setVisible)
        toggle.toggled.connect(excerpt.setHidden)
        toggle.toggled.connect(lambda checked, button=toggle: button.setText("^" if checked else "v"))
        detail = QtWidgets.QPushButton("View Full Transcript")
        detail.setObjectName("CompletedTranscriptDetail")
        detail.setEnabled(self._completion_state is CompletionState.COMPLETE)
        detail.clicked.connect(lambda _checked=False, question_id=row.question_id: self.callbacks.edit_question(question_id))
        layout.addWidget(detail, 0, self.QtCore.Qt.AlignmentFlag.AlignRight)
        return card

    def _apply_transcript_filter(self, _value: str = "") -> None:
        query = self._search.text().strip().casefold() if self._search is not None else ""
        selected = self._filter.currentText() if self._filter is not None else "All questions"
        for card in self._transcript_cards:
            category_match = (
                selected == "All questions"
                or selected == card.property("category")
                or selected == "Flagged" and bool(card.property("flagged"))
            )
            query_match = not query or query in str(card.property("searchText") or "")
            card.setVisible(category_match and query_match)

    @staticmethod
    def _stylesheet() -> str:
        return """
            #CompletedInterviewTitle { font-size: 27px; font-weight: 700; color: #102044; }
            #CompletedInterviewSubtitle { color: #536789; }
            #CompletedInterviewHeader, #CompletedInterviewSummaryColumn, #CompletedInterviewTranscriptColumn {
                background: white; border: 1px solid #d7dfec; border-radius: 8px;
            }
            #CompletedInterviewCandidateName { font-size: 21px; font-weight: 700; color: #102044; }
            #CompletedInterviewStatus { font-weight: 700; color: #15963a; }
            #CompletedInterviewWarning { color: #a13d00; background: #fff7ed; padding: 8px; }
            #CompletedTranscriptCard { background: white; border: 1px solid #d7dfec; border-radius: 7px; }
        """
