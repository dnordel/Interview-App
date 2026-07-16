from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from pyside_interview_components import AdaptiveTwoColumn, build_candidate_identity


@dataclass(frozen=True)
class LiveQuestionSpec:
    question_id: str
    kind: str


@dataclass(frozen=True)
class LiveStage:
    key: str
    label: str
    first_index: int
    last_index: int
    total_steps: int
    state: str

    @property
    def range_label(self) -> str:
        first_step = self.first_index + 1
        last_step = self.last_index + 1
        prefix = "Step" if first_step == last_step else "Steps"
        value = str(first_step) if first_step == last_step else f"{first_step}-{last_step}"
        return f"{prefix} {value} of {self.total_steps}"


def _stage_key(question: LiveQuestionSpec, *, first_trait_index: int | None) -> str:
    if question.kind == "intro":
        return "introduction"
    if question.question_id == "Why-ECE" or question.kind == "qualification":
        return "qualifications"
    if question.kind == "trait":
        return "scored"
    if first_trait_index is not None and question.question_id in {"FT-or-PT", "Not-Avail", "Start", "Pay"}:
        return "availability"
    return "non_scored"


def derive_live_stages(
    questions: Sequence[LiveQuestionSpec],
    *,
    current_index: int,
) -> list[LiveStage]:
    labels = {
        "introduction": "Introduction",
        "qualifications": "Candidate Qualifications",
        "non_scored": "Non-Scored Questions",
        "scored": "Scored Questions",
        "availability": "Availability & Pay",
    }
    first_trait_index = next((index for index, item in enumerate(questions) if item.kind == "trait"), None)
    grouped: dict[str, list[int]] = {key: [] for key in labels}
    for index, question in enumerate(questions):
        grouped[_stage_key(question, first_trait_index=first_trait_index)].append(index)

    stages: list[LiveStage] = []
    for key, label in labels.items():
        indices = grouped[key]
        if not indices:
            continue
        if current_index < indices[0]:
            state = "upcoming"
        elif current_index > indices[-1]:
            state = "complete"
        else:
            state = "active"
        stages.append(
            LiveStage(
                key=key,
                label=label,
                first_index=indices[0],
                last_index=indices[-1],
                total_steps=len(questions),
                state=state,
            )
        )
    return stages


@dataclass(frozen=True)
class LiveInterviewCallbacks:
    back: Callable[[], None]
    next: Callable[[], None]
    exit: Callable[[], None]
    skip: Callable[[], None] | None = None
    edit_transcript: Callable[[], None] | None = None
    view_anchor: Callable[[int], None] | None = None


@dataclass(frozen=True)
class LiveInterviewViewModel:
    kind: str
    page_title: str
    page_subtitle: str
    candidate_name: str
    school: str
    position: str
    stage_label: str
    current_index: int
    total_steps: int
    stages: Sequence[LiveStage]
    prompt: str
    question_title: str = ""
    group_question_index: int = 1
    group_question_count: int = 1
    transcript: str = ""
    notes: str = ""
    quick_actions: frozenset[str] = field(default_factory=frozenset)
    structured_widget: Any | None = None
    priority: str = ""
    weight: float = 0.0
    rating_options: Sequence["LiveRatingOption"] = field(default_factory=tuple)
    selected_score: str = ""
    is_last: bool = False
    recording_active: bool = False
    transcript_active: bool = False
    audio_source: str = ""
    warning: str = ""
    intro_actions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class LiveInputSnapshot:
    notes: str = ""
    score: str = ""
    quick_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveRatingOption:
    score: int
    description: str
    sample_answer: str = ""


class LiveInterviewPage:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtWidgets: Any,
        callbacks: LiveInterviewCallbacks,
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.callbacks = callbacks
        self.root: Any | None = None
        self.footer: Any | None = None
        self._intro_read_main: Any | None = None
        self._intro_read_step: Any | None = None
        self._intro_ready: Any | None = None
        self._intro_continue: Any | None = None
        self._transcript_text: Any | None = None
        self._audio_status: Any | None = None
        self._audio_waveform: Any | None = None
        self._warning_label: Any | None = None
        self._notes_editor: Any | None = None
        self._important_check: Any | None = None
        self._rating_group: Any | None = None
        self._weighted_points: Any | None = None
        self._primary_button: Any | None = None
        self._flag_checks: list[tuple[Any, str]] = []
        self._weight = 0.0
        self._kind = ""
        self._adaptive_content: Any | None = None
        self._adaptive_layout: Any | None = None
        self._adaptive: AdaptiveTwoColumn | None = None
        self._main_panel: Any | None = None
        self._side_panel: Any | None = None

    def render(self, model: LiveInterviewViewModel) -> tuple[Any, Any]:
        self._kind = model.kind
        QtWidgets = self.QtWidgets
        root = QtWidgets.QWidget()
        root.setObjectName("LiveInterviewPage")
        root.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title_row = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QWidget()
        title_layout = QtWidgets.QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title = QtWidgets.QLabel(model.page_title)
        title.setObjectName("LiveInterviewPageTitle")
        subtitle = QtWidgets.QLabel(model.page_subtitle)
        subtitle.setObjectName("LiveInterviewPageSubtitle")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_row.addWidget(title_box, 1)
        exit_button = QtWidgets.QPushButton("Exit Interview")
        exit_button.setObjectName("LiveInterviewExit")
        exit_button.setProperty("pyside_live_footer_action", "exit")
        exit_button.clicked.connect(self.callbacks.exit)
        title_row.addWidget(exit_button)
        layout.addLayout(title_row)

        header = QtWidgets.QFrame()
        header.setObjectName("LiveInterviewHeader")
        header.setProperty(
            "captureState",
            "recording" if model.recording_active else "warning" if model.warning else "inactive",
        )
        header_layout = QtWidgets.QHBoxLayout(header)
        candidate_box = build_candidate_identity(
            QtWidgets=QtWidgets,
            candidate_name=model.candidate_name,
            school=model.school,
            position=model.position,
            object_prefix="LiveInterview",
        )
        header_layout.addWidget(candidate_box)

        progress_box = QtWidgets.QWidget()
        progress_layout = QtWidgets.QVBoxLayout(progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_heading = QtWidgets.QHBoxLayout()
        progress_heading.addWidget(QtWidgets.QLabel(f"{model.stage_label}   |   Step {model.current_index + 1} of {model.total_steps}"))
        progress_heading.addStretch(1)
        percent = round(((model.current_index + 1) / max(1, model.total_steps)) * 100)
        progress_heading.addWidget(QtWidgets.QLabel(f"{percent}%"))
        progress_layout.addLayout(progress_heading)
        progress = QtWidgets.QProgressBar()
        progress.setObjectName("LiveInterviewProgress")
        progress.setRange(0, max(1, model.total_steps))
        progress.setValue(model.current_index + 1)
        progress.setTextVisible(False)
        progress_layout.addWidget(progress)
        capture = QtWidgets.QHBoxLayout()
        capture.addWidget(QtWidgets.QLabel("Recording" if model.recording_active else "Recording unavailable"))
        capture.addWidget(QtWidgets.QLabel("Transcript active" if model.transcript_active else "Transcript pending"))
        capture.addWidget(QtWidgets.QLabel("Saved on navigation"))
        capture.addStretch(1)
        capture.addWidget(QtWidgets.QLabel("Audio source"))
        capture.addWidget(QtWidgets.QLabel(model.audio_source or "Default capture device"))
        progress_layout.addLayout(capture)
        header_layout.addWidget(progress_box, 1)
        layout.addWidget(header)

        warning = QtWidgets.QLabel(model.warning)
        warning.setObjectName("PySideRecordingWarning")
        warning.setWordWrap(True)
        warning.setVisible(bool(model.warning))
        layout.addWidget(warning)
        self._warning_label = warning

        body_widget = QtWidgets.QWidget()
        body_widget.setObjectName("LiveInterviewBody")
        body = QtWidgets.QHBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        rail = self._build_stage_rail(model.stages)
        body.addWidget(rail)
        if model.kind == "intro":
            main = self._build_intro_main(model)
        elif model.kind == "trait":
            main = self._build_scored_main(model)
        else:
            main = self._build_non_scored_main(model)
        if model.kind == "intro":
            side = self._build_intro_side(model)
        elif model.kind == "trait":
            side = self._build_scored_side(model)
        else:
            side = self._build_non_scored_side(model)
        adaptive_container = AdaptiveTwoColumn(
            QtWidgets=QtWidgets,
            object_name="LiveInterviewAdaptiveContent",
            left=main,
            right=side,
            left_stretch=5 if model.kind == "trait" else 2,
            right_stretch=7 if model.kind == "trait" else 1,
        )
        adaptive = adaptive_container.widget
        main.setMinimumWidth(360)
        side.setMinimumWidth(460 if model.kind == "trait" else 280)
        body.addWidget(adaptive, 1)
        layout.addWidget(body_widget, 1)
        self._adaptive = adaptive_container
        self._main_panel = main
        self._side_panel = side
        self._adaptive_content = adaptive
        self._adaptive_layout = adaptive_container.layout

        footer = QtWidgets.QFrame()
        footer.setObjectName("LiveInterviewFooter")
        footer_layout = QtWidgets.QHBoxLayout(footer)
        back = QtWidgets.QPushButton("Back")
        back.setObjectName("LiveInterviewBack")
        back.setProperty("pyside_live_footer_action", "back")
        back.setEnabled(model.current_index > 0)
        back.clicked.connect(self.callbacks.back)
        footer_layout.addWidget(back, 1)
        if model.kind == "trait" and self.callbacks.skip is not None:
            skip = QtWidgets.QPushButton("Skip Rating")
            skip.setObjectName("LiveInterviewSkipRating")
            skip.setProperty("pyside_live_footer_action", "skip")
            skip.clicked.connect(self.callbacks.skip)
            footer_layout.addWidget(skip, 1)
        next_text = "Finalize" if model.is_last else {
            "intro": "Next: Candidate Qualifications",
            "trait": "Save Rating && Next",
        }.get(model.kind, "Next Question")
        next_button = QtWidgets.QPushButton(next_text)
        next_button.setObjectName("LiveInterviewPrimaryAction")
        next_button.setProperty("pyside_live_footer_action", "finalize" if model.is_last else "next")
        next_button.clicked.connect(self.callbacks.next)
        if model.kind == "trait":
            next_button.setEnabled(bool(model.selected_score))
        footer_layout.addWidget(next_button, 2)
        self._primary_button = next_button

        root.setStyleSheet(self._stylesheet())
        self.root = root
        self.footer = footer
        return root, footer

    def input_snapshot(self) -> LiveInputSnapshot:
        if self._rating_group is not None:
            checked = self._rating_group.checkedButton()
            score = str(checked.property("score")) if checked is not None else ""
            actions = tuple(canonical for checkbox, canonical in self._flag_checks if checkbox.isChecked())
            notes = self._notes_editor.toPlainText() if self._notes_editor is not None else ""
            return LiveInputSnapshot(notes=notes, score=score, quick_actions=actions)
        if self._kind != "intro" and self._notes_editor is not None:
            actions = ("Mark as important",) if self._important_check is not None and self._important_check.isChecked() else ()
            return LiveInputSnapshot(notes=self._notes_editor.toPlainText(), quick_actions=actions)
        actions: list[str] = []
        if self._intro_read_main is not None and self._intro_read_main.isChecked():
            actions.append("Mark as read")
        if self._intro_ready is not None and self._intro_ready.isChecked():
            actions.append("Candidate ready")
        if self._intro_continue is not None and self._intro_continue.isChecked():
            actions.append("Continue to qualifications")
        notes = self._notes_editor.toPlainText() if self._notes_editor is not None else ""
        return LiveInputSnapshot(notes=notes, quick_actions=tuple(actions))

    @property
    def notes_editor(self) -> Any | None:
        return self._notes_editor

    @property
    def rating_group(self) -> Any | None:
        return self._rating_group

    def update_transcript(self, text: str, _status: str = "active") -> None:
        if self._transcript_text is not None:
            self._transcript_text.setText(str(text or ""))

    def update_warning(self, text: str) -> None:
        if self._warning_label is None:
            return
        self._warning_label.setText(str(text or ""))
        self._warning_label.setVisible(bool(str(text or "").strip()))

    def update_audio(self, level: float, detected: bool) -> None:
        bounded = max(0.0, min(1.0, float(level)))
        active = max(1, round(bounded * 24)) if detected else 0
        if self._audio_waveform is not None:
            self._audio_waveform.setText("|||" * max(1, active // 3) if detected else "-" * 24)
        if self._audio_status is not None:
            self._audio_status.setText("Candidate audio detected" if detected else "Waiting for candidate audio")

    def set_narrow(self, narrow: bool) -> None:
        if self._adaptive is None:
            return
        if self._main_panel is not None:
            self._main_panel.setMinimumWidth(0 if narrow else 360)
        if self._side_panel is not None:
            self._side_panel.setMinimumWidth(0 if narrow else 460 if self._kind == "trait" else 280)
        self._adaptive.set_narrow(narrow)
        self.root.updateGeometry()

    def set_available_width(self, width: int) -> None:
        if self.root is None:
            return
        bounded = max(0, int(width))
        self.root.setMinimumWidth(bounded)
        self.root.setMaximumWidth(bounded)
        self.root.resize(bounded, self.root.height())
        self.root.updateGeometry()

    def _build_stage_rail(self, stages: Sequence[LiveStage]) -> Any:
        rail = self.QtWidgets.QFrame()
        rail.setObjectName("LiveInterviewStageRail")
        rail.setFixedWidth(238)
        layout = self.QtWidgets.QVBoxLayout(rail)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for stage in stages:
            row = self.QtWidgets.QFrame()
            row.setProperty("stageState", stage.state)
            row_layout = self.QtWidgets.QVBoxLayout(row)
            label = self.QtWidgets.QLabel(stage.label)
            label.setObjectName("LiveInterviewStageLabel")
            range_label = self.QtWidgets.QLabel(stage.range_label)
            range_label.setObjectName("LiveInterviewStageRange")
            row_layout.addWidget(label)
            row_layout.addWidget(range_label)
            layout.addWidget(row)
        layout.addStretch(1)
        return rail

    def _build_intro_main(self, model: LiveInterviewViewModel) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("LiveInterviewMainCard")
        layout = self.QtWidgets.QVBoxLayout(card)
        heading = self.QtWidgets.QLabel("Introduction Script")
        heading.setObjectName("LiveInterviewCardTitle")
        layout.addWidget(heading)
        script = self.QtWidgets.QTextBrowser()
        script.setObjectName("LiveIntroScript")
        script.setPlainText(model.prompt)
        layout.addWidget(script, 1)
        read = self.QtWidgets.QCheckBox("Introduction read to candidate")
        read.setObjectName("LiveIntroReadMain")
        read.setChecked("Mark as read" in model.intro_actions)
        layout.addWidget(read)
        self._intro_read_main = read
        return card

    def _build_intro_side(self, model: LiveInterviewViewModel) -> Any:
        container = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        current = self.QtWidgets.QFrame()
        current.setObjectName("LiveInterviewSideCard")
        current_layout = self.QtWidgets.QVBoxLayout(current)
        current_layout.addWidget(self.QtWidgets.QLabel("Current Step"))
        current_layout.addWidget(self.QtWidgets.QLabel("Non-scored"))
        step_read = self.QtWidgets.QCheckBox("Read the introduction")
        step_read.setObjectName("LiveIntroReadStep")
        step_read.setChecked("Mark as read" in model.intro_actions)
        ready = self.QtWidgets.QCheckBox("Confirm the candidate is ready")
        ready.setObjectName("LiveIntroCandidateReady")
        ready.setChecked("Candidate ready" in model.intro_actions)
        cont = self.QtWidgets.QCheckBox("Continue to qualifications")
        cont.setObjectName("LiveIntroContinue")
        cont.setChecked("Continue to qualifications" in model.intro_actions)
        current_layout.addWidget(step_read)
        current_layout.addWidget(ready)
        current_layout.addWidget(cont)
        layout.addWidget(current)
        transcript = self.QtWidgets.QFrame()
        transcript.setObjectName("LiveInterviewSideCard")
        transcript_layout = self.QtWidgets.QVBoxLayout(transcript)
        transcript_layout.addWidget(self.QtWidgets.QLabel("Transcript Status"))
        transcript_layout.addWidget(self.QtWidgets.QLabel("Listening for candidate audio"))
        transcript_layout.addWidget(self.QtWidgets.QLabel("Audio is being recorded and transcribed in real time."))
        transcript_layout.addStretch(1)
        layout.addWidget(transcript, 1)
        self._intro_read_step = step_read
        self._intro_ready = ready
        self._intro_continue = cont

        def sync_from_main(checked: bool) -> None:
            step_read.blockSignals(True)
            step_read.setChecked(checked)
            step_read.blockSignals(False)

        def sync_from_step(checked: bool) -> None:
            if self._intro_read_main is None:
                return
            self._intro_read_main.blockSignals(True)
            self._intro_read_main.setChecked(checked)
            self._intro_read_main.blockSignals(False)

        if self._intro_read_main is not None:
            self._intro_read_main.toggled.connect(sync_from_main)
        step_read.toggled.connect(sync_from_step)
        return container

    def _build_non_scored_main(self, model: LiveInterviewViewModel) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("LiveInterviewMainCard")
        layout = self.QtWidgets.QVBoxLayout(card)
        count = self.QtWidgets.QLabel(
            f"Question {model.group_question_index} of {max(1, model.group_question_count)}"
        )
        count.setObjectName("LiveInterviewQuestionCount")
        layout.addWidget(count)
        prompt = self.QtWidgets.QLabel(model.prompt)
        prompt.setObjectName("LiveInterviewQuestionPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        if model.structured_widget is not None:
            layout.addWidget(model.structured_widget)

        transcript = self.QtWidgets.QFrame()
        transcript.setObjectName("LiveTranscriptCard")
        transcript_layout = self.QtWidgets.QVBoxLayout(transcript)
        transcript_header = self.QtWidgets.QHBoxLayout()
        transcript_header.addWidget(self.QtWidgets.QLabel("Live Transcript"))
        transcript_header.addStretch(1)
        edit = self.QtWidgets.QPushButton("Edit transcript")
        edit.setObjectName("LiveTranscriptEdit")
        if self.callbacks.edit_transcript is not None:
            edit.clicked.connect(self.callbacks.edit_transcript)
        transcript_header.addWidget(edit)
        transcript_layout.addLayout(transcript_header)
        transcript_layout.addWidget(self.QtWidgets.QLabel("Listening"))
        transcript_text = self.QtWidgets.QLabel(model.transcript)
        transcript_text.setObjectName("LiveTranscriptText")
        transcript_text.setWordWrap(True)
        transcript_text.setMinimumHeight(92)
        transcript_layout.addWidget(transcript_text)
        layout.addWidget(transcript, 1)

        notes_card = self.QtWidgets.QFrame()
        notes_card.setObjectName("LiveNotesCard")
        notes_layout = self.QtWidgets.QVBoxLayout(notes_card)
        notes_layout.addWidget(self.QtWidgets.QLabel("Interviewer Notes"))
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("LiveInterviewerNotes")
        notes.setPlaceholderText("Add optional notes or evidence...")
        notes.setPlainText(model.notes)
        notes.setMaximumHeight(130)
        notes_layout.addWidget(notes)
        layout.addWidget(notes_card)
        self._transcript_text = transcript_text
        self._notes_editor = notes
        return card

    def _build_non_scored_side(self, model: LiveInterviewViewModel) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("LiveInterviewSideCard")
        layout = self.QtWidgets.QVBoxLayout(card)
        layout.addWidget(self.QtWidgets.QLabel("Question Status"))
        layout.addWidget(self.QtWidgets.QLabel("Non-scored"))
        important = self.QtWidgets.QCheckBox("Mark as important")
        important.setObjectName("LiveMarkImportant")
        important.setChecked("Mark as important" in model.quick_actions)
        layout.addWidget(important)
        description = self.QtWidgets.QLabel(
            "This response will appear in the completed interview transcript but will not affect the score."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)
        waveform = self.QtWidgets.QLabel("-" * 24)
        waveform.setObjectName("LiveCandidateAudioWaveform")
        status = self.QtWidgets.QLabel("Waiting for candidate audio")
        status.setObjectName("LiveCandidateAudioStatus")
        layout.addWidget(waveform)
        layout.addWidget(status)
        self._important_check = important
        self._audio_waveform = waveform
        self._audio_status = status
        return card

    def _build_scored_main(self, model: LiveInterviewViewModel) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("LiveInterviewMainCard")
        layout = self.QtWidgets.QVBoxLayout(card)
        layout.addWidget(
            self.QtWidgets.QLabel(
                f"Scored Question {model.group_question_index} of {max(1, model.group_question_count)}"
            )
        )
        badges = self.QtWidgets.QHBoxLayout()
        priority = self.QtWidgets.QLabel(model.priority or "Scored")
        priority.setObjectName("LiveQuestionPriority")
        weight_text = int(model.weight) if float(model.weight).is_integer() else model.weight
        weight = self.QtWidgets.QLabel(f"Weight {weight_text}x")
        weight.setObjectName("LiveQuestionWeight")
        badges.addWidget(priority)
        badges.addStretch(1)
        badges.addWidget(weight)
        layout.addLayout(badges)
        prompt = self.QtWidgets.QLabel(model.prompt)
        prompt.setObjectName("LiveInterviewQuestionPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        transcript_and_notes = self._build_transcript_and_notes(model)
        layout.addWidget(transcript_and_notes, 1)
        return card

    def _build_transcript_and_notes(self, model: LiveInterviewViewModel) -> Any:
        container = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        transcript = self.QtWidgets.QFrame()
        transcript.setObjectName("LiveTranscriptCard")
        transcript_layout = self.QtWidgets.QVBoxLayout(transcript)
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self.QtWidgets.QLabel("Live Transcript"))
        header.addStretch(1)
        edit = self.QtWidgets.QPushButton("Edit transcript")
        edit.setObjectName("LiveTranscriptEdit")
        if self.callbacks.edit_transcript is not None:
            edit.clicked.connect(self.callbacks.edit_transcript)
        header.addWidget(edit)
        transcript_layout.addLayout(header)
        transcript_layout.addWidget(self.QtWidgets.QLabel("Listening"))
        transcript_text = self.QtWidgets.QLabel(model.transcript)
        transcript_text.setObjectName("LiveTranscriptText")
        transcript_text.setWordWrap(True)
        transcript_text.setMinimumHeight(86)
        transcript_layout.addWidget(transcript_text)
        layout.addWidget(transcript, 1)
        notes_card = self.QtWidgets.QFrame()
        notes_card.setObjectName("LiveNotesCard")
        notes_layout = self.QtWidgets.QVBoxLayout(notes_card)
        notes_layout.addWidget(self.QtWidgets.QLabel("Interviewer Notes"))
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("LiveInterviewerNotes")
        notes.setPlaceholderText("Add evidence, follow-up responses, or concerns...")
        notes.setPlainText(model.notes)
        notes.setMaximumHeight(120)
        notes_layout.addWidget(notes)
        layout.addWidget(notes_card)
        self._transcript_text = transcript_text
        self._notes_editor = notes
        return container

    def _build_scored_side(self, model: LiveInterviewViewModel) -> Any:
        card = self.QtWidgets.QFrame()
        card.setObjectName("LiveInterviewSideCard")
        layout = self.QtWidgets.QVBoxLayout(card)
        layout.addWidget(self.QtWidgets.QLabel("Rate the Response"))
        group = self.QtWidgets.QButtonGroup(card)
        for option in model.rating_options:
            row = self.QtWidgets.QFrame()
            row.setObjectName("LiveRatingRow")
            row_layout = self.QtWidgets.QHBoxLayout(row)
            radio = self.QtWidgets.QRadioButton(str(option.score))
            radio.setObjectName("LiveRatingOption")
            radio.setProperty("score", option.score)
            radio.toggled.connect(
                lambda checked, button=radio: self._update_weighted_points(button) if checked else None
            )
            group.addButton(radio)
            row_layout.addWidget(radio)
            description = self.QtWidgets.QLabel(option.description.split(".", 1)[0])
            description.setObjectName("LiveRatingDescription")
            description.setWordWrap(True)
            row_layout.addWidget(description, 1)
            anchor = self.QtWidgets.QPushButton("View Anchor")
            anchor.setObjectName("LiveRatingAnchor")
            if self.callbacks.view_anchor is not None:
                anchor.clicked.connect(lambda _checked=False, score=option.score: self.callbacks.view_anchor(score))
            row_layout.addWidget(anchor)
            layout.addWidget(row)
            if str(option.score) == str(model.selected_score):
                radio.setChecked(True)
        self._rating_group = group
        self._weight = float(model.weight)

        flags_row = self.QtWidgets.QHBoxLayout()
        flags = self.QtWidgets.QWidget()
        flags_layout = self.QtWidgets.QVBoxLayout(flags)
        flags_layout.setContentsMargins(0, 0, 0, 0)
        flags_layout.addWidget(self.QtWidgets.QLabel("Quick Flags"))
        definitions = (
            ("LiveFlagNeedsFollowUp", "Needs follow-up", "Needs follow-up"),
            ("LiveFlagNoExample", "No example after follow-ups", "Candidate gave no example"),
            ("LiveFlagDisqualifier", "Absolute disqualifier", "Disqualifier observed"),
        )
        self._flag_checks = []
        for object_name, visible, canonical in definitions:
            checkbox = self.QtWidgets.QCheckBox(visible)
            checkbox.setObjectName(object_name)
            checkbox.setChecked(canonical in model.quick_actions)
            self._flag_checks.append((checkbox, canonical))
            flags_layout.addWidget(checkbox)
        flags_row.addWidget(flags, 1)
        points = self.QtWidgets.QLabel("Select a rating")
        points.setObjectName("LiveWeightedPoints")
        flags_row.addWidget(points)
        layout.addLayout(flags_row)
        layout.addStretch(1)
        self._weighted_points = points
        if model.selected_score:
            self._set_weighted_points(int(model.selected_score))
        return card

    def _update_weighted_points(self, button: Any) -> None:
        score = int(button.property("score") or 0)
        self._set_weighted_points(score)
        if self._primary_button is not None:
            self._primary_button.setEnabled(score > 0)

    def _set_weighted_points(self, score: int) -> None:
        if self._weighted_points is None:
            return
        points = score * self._weight
        text = int(points) if float(points).is_integer() else points
        self._weighted_points.setText(f"{text} weighted points")

    @staticmethod
    def _stylesheet() -> str:
        return """
            QWidget#LiveInterviewPage { color: #0f1f43; background: transparent; }
            QLabel#LiveInterviewPageTitle { font-size: 28px; font-weight: 700; }
            QLabel#LiveInterviewPageSubtitle { color: #465a78; }
            QPushButton#LiveInterviewExit { color: #dc2626; border: 1px solid #ef4444; padding: 8px 14px; border-radius: 6px; }
            QFrame#LiveInterviewHeader, QFrame#LiveInterviewMainCard, QFrame#LiveInterviewSideCard,
            QFrame#LiveInterviewFooter, QFrame#LiveInterviewStageRail { background: #ffffff; border: 1px solid #d6deea; border-radius: 8px; }
            QLabel#LiveInterviewCandidateName { font-size: 23px; font-weight: 700; }
            QLabel#LiveInterviewCandidateMeta, QLabel#LiveInterviewCaption { color: #526784; }
            QLabel#LiveInterviewCardTitle, QLabel#LiveInterviewStageLabel { font-weight: 650; }
            QLabel#LiveInterviewQuestionPrompt { font-size: 18px; font-weight: 650; }
            QLabel#LiveInterviewStageRange { color: #667a99; }
            QFrame[stageState="active"] { background: #eef5ff; border-left: 3px solid #2563eb; }
            QFrame[stageState="complete"] QLabel#LiveInterviewStageLabel { color: #15803d; }
            QProgressBar#LiveInterviewProgress { min-height: 8px; max-height: 8px; border: none; background: #e5eaf2; border-radius: 4px; }
            QProgressBar#LiveInterviewProgress::chunk { background: #1665ed; border-radius: 4px; }
            QPushButton#LiveInterviewPrimaryAction { background: #0864ef; color: white; font-weight: 650; padding: 12px; border-radius: 6px; }
            QFrame#LiveTranscriptCard, QFrame#LiveNotesCard { border: 1px solid #dce3ee; border-radius: 7px; }
            QLabel#LiveCandidateAudioStatus, QLabel#LiveCandidateAudioWaveform { color: #16a34a; }
        """
