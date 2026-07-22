from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import pyside_interview_app
from pyside_interview_app import (
    _INTERVIEW_LIVE_TAB_INDEX,
    _INTERVIEW_REVIEW_TAB_INDEX,
    PySideInterviewSession,
    build_interview_redesign_model,
)
from pyside_live_interview import LiveQuestionSpec, derive_live_stages
from visual_test_support import (
    TYPOGRAPHY_STRESS_TEXT,
    VisualTestDatabaseRegistry,
    assert_no_large_unpainted_region,
    assert_single_line_text_fits,
    assert_vertical_text_fits,
    assert_wrapped_text_fits,
    assert_widget_text_glyphs_supported,
    configure_visual_test_app,
)


def test_live_stage_model_derives_dynamic_ranges_from_active_flow() -> None:
    questions = [
        LiveQuestionSpec("intro_script", "intro"),
        LiveQuestionSpec("Why-ECE", "qualification"),
        LiveQuestionSpec("Why-LPL", "custom"),
        LiveQuestionSpec("trait_1", "trait"),
        LiveQuestionSpec("trait_2", "trait"),
        LiveQuestionSpec("FT-or-PT", "custom"),
        LiveQuestionSpec("Pay", "custom"),
    ]

    stages = derive_live_stages(questions, current_index=3)

    assert [(stage.label, stage.range_label, stage.state) for stage in stages] == [
        ("Introduction", "Step 1 of 7", "complete"),
        ("Candidate Qualifications", "Step 2 of 7", "complete"),
        ("Non-Scored Questions", "Step 3 of 7", "complete"),
        ("Scored Questions", "Steps 4-5 of 7", "active"),
        ("Availability & Pay", "Steps 6-7 of 7", "upcoming"),
    ]


@pytest.mark.pyside_gui
def test_pyside_live_introduction_screen_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    window.session = session
    window.session_track_key = session.track_key
    window.session_index = session.current_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    app.processEvents()

    page = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX)
    assert page.findChild(qt_widgets.QLabel, "LiveInterviewPageTitle").text() == "Live Interview Introduction Script"
    assert page.findChild(qt_widgets.QLabel, "LiveInterviewCandidateName").text() == "Sofia Ramirez"
    assert page.findChild(qt_widgets.QTextEdit, "LiveInterviewerNotes") is None
    assert page.findChild(qt_widgets.QProgressBar, "LiveInterviewProgress").maximum() == 16
    rail = page.findChild(qt_widgets.QFrame, "LiveInterviewStageRail")
    assert [label.text() for label in rail.findChildren(qt_widgets.QLabel, "LiveInterviewStageLabel")] == [
        "Introduction",
        "Candidate Qualifications",
        "Non-Scored Questions",
        "Scored Questions",
        "Availability & Pay",
    ]

    main_read = page.findChild(qt_widgets.QCheckBox, "LiveIntroReadMain")
    step_read = page.findChild(qt_widgets.QCheckBox, "LiveIntroReadStep")
    main_read.click()
    app.processEvents()
    assert step_read.isChecked()
    step_read.click()
    app.processEvents()
    assert not main_read.isChecked()
    main_read.click()

    buttons = {
        button.property("pyside_live_footer_action"): button
        for button in page.findChildren(qt_widgets.QPushButton)
        if button.property("pyside_live_footer_action")
    }
    assert set(buttons) == {"back", "next", "exit"}
    assert not buttons["back"].isEnabled()
    assert buttons["next"].isEnabled()
    buttons["next"].click()
    app.processEvents()

    assert session.active_question().kind == "qualification"
    assert session.answers["intro_script"]["quick_actions"] == ["Mark as read"]
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.visual_inspection
def test_pyside_live_typography_stress_visual_scenario(
    tmp_path: Path,
    visual_test_databases: VisualTestDatabaseRegistry,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    history_path = visual_test_databases.database("live_interview_typography.sqlite3")
    visual_test_databases.expect_seeded(history_path, table="interview_history")
    model = build_interview_redesign_model(history_path=history_path, school_options=["Hawthorne"])
    pyside_interview_app.InterviewHistoryStore(history_path).append(
        {
            "history_id": "live-typography-fixture",
            "candidate_name": TYPOGRAPHY_STRESS_TEXT,
            "school": "Hawthorne",
            "position": "Infant/Toddler Teacher",
            "interview_date": "2026-07-15",
            "outcome": "In Progress",
            "score": "0.0%",
        }
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "typography-draft.json")
    session.start(candidate_name=TYPOGRAPHY_STRESS_TEXT, school="Hawthorne", track_key="infant_toddler")
    for index, item in enumerate(session._workflow_items()):
        transcript = f"{TYPOGRAPHY_STRESS_TEXT} response for {item.title}."
        session.flow_candidate_transcripts[index] = transcript
        session.flow_live_transcripts[index] = transcript
        session.answers[item.question_id] = {
            "kind": item.kind,
            "title": item.title,
            "prompt": item.prompt,
            "notes": TYPOGRAPHY_STRESS_TEXT if item.kind != "intro" else "",
            "score": "4" if item.kind == "trait" else "",
            "quick_actions": [],
        }
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window.session_track_key = session.track_key
    window.staffing_v2_dashboard.show_external_page("interviews")
    window.hiring_v2_router.show_interview()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    window.window.resize(1672, 941)
    window.window.show()
    for screen_name, flow_index in (("intro", 0), ("non-scored", 2), ("scored", 3)):
        session.current_index = flow_index
        window.session_index = flow_index
        window._render_live_question_page()
        app.processEvents()
        window._apply_responsive_layout()
        app.processEvents()
        root = window.live_page.root
        candidate = root.findChild(qt_widgets.QLabel, "LiveInterviewCandidateName")
        assert_vertical_text_fits(candidate, TYPOGRAPHY_STRESS_TEXT)
        assert_widget_text_glyphs_supported(root)
        rendered = window.window.grab()
        assert_no_large_unpainted_region(rendered)
        assert rendered.save(str(tmp_path / f"live-{screen_name}-typography.png"))
    window.window.close()
    app.processEvents()


def test_pyside_live_back_button_saves_current_controls_without_advancing(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    session.current_index = 2
    window.session = session
    window.session_index = session.current_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    page = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX)
    page.findChild(qt_widgets.QTextEdit, "LiveInterviewerNotes").setPlainText("Saved before going back.")
    page.findChild(qt_widgets.QCheckBox, "LiveMarkImportant").setChecked(True)

    back = next(
        button
        for button in page.findChildren(qt_widgets.QPushButton)
        if button.property("pyside_live_footer_action") == "back"
    )
    back.click()
    app.processEvents()

    assert session.current_index == 1
    assert session.answers["Why-LPL"]["notes"] == "Saved before going back."
    assert session.answers["Why-LPL"]["quick_actions"] == ["Mark as important"]
    window.window.close()
    app.processEvents()


def test_session_live_transcript_keeps_manual_edit_and_appends_new_segments(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")

    session.append_live_transcript(2, "Initial machine text.")
    session.replace_live_transcript(2, "Corrected interviewer text.")
    session.append_live_transcript(2, "New candidate sentence.")

    assert session.live_transcript(2) == "Corrected interviewer text. New candidate sentence."
    resumed = PySideInterviewSession.load(model=model, draft_path=tmp_path / "draft.json")
    assert resumed.live_transcript(2) == "Corrected interviewer text. New candidate sentence."


def test_session_canonical_transcript_preserves_manual_override_and_live_failure_fallback(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    session.append_live_transcript(2, "Provisional fallback.")
    session.replace_live_transcript(3, "Manual correction.")

    session.apply_canonical_transcripts({2: "Canonical final.", 3: "Canonical should lose."})

    assert session.flow_candidate_transcripts[2] == "Canonical final."
    assert session.flow_candidate_transcripts[3] == "Manual correction."
    session.apply_canonical_transcripts({})
    assert session.flow_candidate_transcripts[2] == "Provisional fallback."
    assert session.flow_candidate_transcripts[3] == "Manual correction."


@pytest.mark.pyside_gui
def test_pyside_live_non_scored_transcript_and_audio_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    session.current_index = 2
    session.append_live_transcript(2, "I want to build long-term relationships with children and families.")
    window.session = session
    window.session_track_key = session.track_key
    window.session_index = session.current_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    app.processEvents()

    page = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX)
    assert page.findChild(qt_widgets.QLabel, "LiveInterviewPageTitle").text() == "Live Interview Non-Scored Questions"
    transcript = page.findChild(qt_widgets.QLabel, "LiveTranscriptText")
    assert "long-term relationships" in transcript.text()
    assert page.findChild(qt_widgets.QCheckBox, "LiveMarkImportant") is not None

    original_text = transcript.text()

    def cancel_edit_dialog() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "LiveTranscriptEditor" and widget.isVisible())
        dialog.findChild(qt_widgets.QTextEdit, "LiveTranscriptEditorText").setPlainText("Discard this edit.")
        next(button for button in dialog.findChildren(qt_widgets.QPushButton) if button.text() == "Cancel").click()

    qt_core.QTimer.singleShot(0, cancel_edit_dialog)
    page.findChild(qt_widgets.QPushButton, "LiveTranscriptEdit").click()
    app.processEvents()
    assert transcript.text() == original_text

    def edit_open_dialog() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "LiveTranscriptEditor" and widget.isVisible())
        dialog.findChild(qt_widgets.QTextEdit, "LiveTranscriptEditorText").setPlainText("Corrected candidate response.")
        dialog.findChild(qt_widgets.QPushButton, "LiveTranscriptEditorSave").click()

    qt_core.QTimer.singleShot(0, edit_open_dialog)
    page.findChild(qt_widgets.QPushButton, "LiveTranscriptEdit").click()
    app.processEvents()
    assert transcript.text() == "Corrected candidate response."
    assert session.flow_transcript_overrides[2] == "Corrected candidate response."

    window.live_page.update_audio(0.8, True)
    assert page.findChild(qt_widgets.QLabel, "LiveCandidateAudioStatus").text() == "Candidate audio detected"

    session.flow_time_marks = [{"flow_index": 2, "t": 0.0}]
    window.recording_candidate_label = "CANDIDATE"
    window.recording_session = SimpleNamespace(
        sys_wav=tmp_path / "candidate.wav",
        transcribe_new_segments=lambda **_kwargs: [
            SimpleNamespace(speaker="INTERVIEWER", text="Interviewer prompt", start=1.0, end=2.0),
            SimpleNamespace(speaker="CANDIDATE", text="Candidate-only response", start=2.0, end=3.0),
        ],
    )
    window._run_live_transcript_async()
    qt_test.QTest.qWait(250)
    app.processEvents()
    assert "Candidate-only response" in transcript.text()
    assert "Interviewer prompt" not in transcript.text()
    window.recording_session = None

    notes = page.findChild(qt_widgets.QTextEdit, "LiveInterviewerNotes")
    notes.setPlainText("Strong values alignment.")
    page.findChild(qt_widgets.QCheckBox, "LiveMarkImportant").setChecked(True)
    next_button = page.findChild(qt_widgets.QPushButton, "LiveInterviewPrimaryAction")
    next_button.click()
    app.processEvents()

    assert session.active_question().kind == "trait"
    assert session.answers["Why-LPL"]["notes"] == "Strong values alignment."
    assert session.answers["Why-LPL"]["quick_actions"] == ["Mark as important"]
    window.window.close()
    app.processEvents()


def test_pyside_live_asr_failure_warning_never_exposes_exception_speech(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    session.current_index = 2
    window.session = session
    window.session_index = session.current_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    private_text = "candidate said private family details"
    window.recording_session = type(
        "FailingRecorder",
        (),
        {"transcribe_new_segments": lambda _self, **_kwargs: (_ for _ in ()).throw(RuntimeError(private_text))},
    )()

    window._run_live_transcript_async()
    for _attempt in range(100):
        qt_test.QTest.qWait(10)
        app.processEvents()
        if window._live_transcript_queue is None:
            break

    warning = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX).findChild(
        qt_widgets.QLabel, "PySideRecordingWarning"
    )
    assert not warning.isHidden()
    assert "temporarily unavailable" in warning.text()
    assert private_text not in warning.text()
    assert private_text not in caplog.text
    window.recording_session = None
    window.window.close()
    app.processEvents()


def test_scored_flow_exposes_priority_weight_and_score_specific_sample_anchors(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    question = next(item for item in model.flows["infant_toddler"].items if item.kind == "trait")

    assert question.priority == "Critical"
    assert question.weight == 3.0
    assert question.score_cards[4].sample_answer.startswith("A child was sobbing")


@pytest.mark.pyside_gui
def test_pyside_live_scored_rating_and_anchor_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    session.current_index = 3
    session.append_live_transcript(3, "I stayed nearby and helped the child name her feelings.")
    window.session = session
    window.session_track_key = session.track_key
    window.session_index = session.current_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    app.processEvents()

    page = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX)
    assert page.findChild(qt_widgets.QLabel, "LiveInterviewPageTitle").text() == "Live Interview Scored Question and Rating"
    assert page.findChild(qt_widgets.QLabel, "LiveQuestionPriority").text() == "Critical"
    assert page.findChild(qt_widgets.QLabel, "LiveQuestionWeight").text() == "Weight 3x"
    ratings = page.findChildren(qt_widgets.QRadioButton, "LiveRatingOption")
    anchors = page.findChildren(qt_widgets.QPushButton, "LiveRatingAnchor")
    assert len(ratings) == len(anchors) == 5
    primary = page.findChild(qt_widgets.QPushButton, "LiveInterviewPrimaryAction")
    assert not primary.isEnabled()

    anchor_text: list[str] = []

    def inspect_anchor() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "LiveRatingAnchorDialog" and widget.isVisible())
        anchor_text.extend(label.text() for label in dialog.findChildren(qt_widgets.QLabel))
        dialog.accept()

    for anchor in anchors:
        qt_core.QTimer.singleShot(0, inspect_anchor)
        anchor.click()
        app.processEvents()
    assert any("Highly empathetic" in text for text in anchor_text)
    assert any("A child was sobbing" in text for text in anchor_text)

    for expected_score, rating in enumerate(ratings, start=1):
        rating.click()
        app.processEvents()
        assert page.findChild(qt_widgets.QLabel, "LiveWeightedPoints").text() == (
            f"{expected_score * 3} weighted points"
        )
    ratings[3].click()
    page.findChild(qt_widgets.QCheckBox, "LiveFlagNeedsFollowUp").setChecked(True)
    page.findChild(qt_widgets.QCheckBox, "LiveFlagNoExample").setChecked(True)
    page.findChild(qt_widgets.QCheckBox, "LiveFlagDisqualifier").setChecked(True)
    app.processEvents()
    assert primary.isEnabled()
    assert page.findChild(qt_widgets.QLabel, "LiveWeightedPoints").text() == "12 weighted points"
    primary.click()
    app.processEvents()

    first_trait = session._workflow_items()[3]
    assert session.answers[first_trait.question_id]["score"] == "4"
    assert session.answers[first_trait.question_id]["quick_actions"] == [
        "Needs follow-up",
        "Candidate gave no example",
        "Disqualifier observed",
    ]
    skip = page.findChild(qt_widgets.QPushButton, "LiveInterviewSkipRating")
    assert skip is not None
    skip.click()
    app.processEvents()
    second_trait = session._workflow_items()[4]
    assert session.answers[second_trait.question_id]["skipped"] is True
    window.window.close()
    app.processEvents()


def test_pyside_live_availability_page_uses_non_scored_controls_and_public_next(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    workflow = session._workflow_items()
    last_scored_index = max(index for index, item in enumerate(workflow) if item.kind == "trait")
    availability_index = max(
        index for index, item in enumerate(workflow) if index > last_scored_index and item.kind == "custom"
    )
    session.current_index = availability_index
    window.session = session
    window.session_index = availability_index
    window._render_live_question_page()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    page = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX)
    app.processEvents()

    assert page.findChild(qt_widgets.QLabel, "LiveInterviewPageTitle").text() == "Live Interview Availability & Pay"
    assert page.findChild(qt_widgets.QTextEdit, "LiveInterviewerNotes") is not None
    assert page.findChild(qt_widgets.QCheckBox, "LiveMarkImportant") is not None
    next_button = page.findChild(qt_widgets.QPushButton, "LiveInterviewPrimaryAction")
    assert next_button.text() == "Finalize"
    window._generate_interview_notes_from_session = lambda: None
    next_button.click()
    app.processEvents()

    assert session.current_index == availability_index + 1
    assert window.interview_tabs.currentIndex() == _INTERVIEW_REVIEW_TAB_INDEX
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.visual_inspection
def test_pyside_live_screens_responsive_render_scenario(
    tmp_path: Path,
    visual_test_databases: VisualTestDatabaseRegistry,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    history_path = visual_test_databases.database("live_interview_history.sqlite3")
    visual_test_databases.expect_seeded(history_path, table="interview_history")
    model = build_interview_redesign_model(
        history_path=history_path,
        school_options=["Hawthorne"],
    )
    pyside_interview_app.InterviewHistoryStore(history_path).append(
        {
            "history_id": "live-visual-fixture",
            "candidate_name": "Sofia Ramirez",
            "school": "Hawthorne",
            "position": "Infant/Toddler Teacher",
            "interview_date": "2026-07-15",
            "outcome": "In Progress",
            "score": "0.0%",
        }
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    window.session = session
    window.session_track_key = session.track_key
    window.staffing_v2_dashboard.show_external_page("interviews")
    window.hiring_v2_router.show_interview()
    window.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
    window.window.show()
    app.processEvents()
    original_font = app.font()

    renders = 0
    for screen_name, flow_index in (("intro", 0), ("non_scored", 2), ("scored", 3)):
        session.current_index = flow_index
        window.session_index = flow_index
        window._render_live_question_page()
        for width, height, scale in (
            (1672, 941, 1.0),
            (1366, 768, 1.0),
            (1366, 768, 1.25),
            (1366, 768, 1.5),
        ):
            scaled_font = type(original_font)(original_font)
            scaled_font.setPointSizeF(max(8.0, 10.0 * scale))
            app.setFont(scaled_font)
            window.window.resize(width, height)
            narrow = ((width - 300) / scale) < 1180
            app.processEvents()
            window._apply_responsive_layout()
            app.processEvents()
            adaptive = window.live_page._adaptive_content
            assert adaptive.property("layoutMode") == ("narrow" if narrow else "desktop")
            scroll = window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX).findChild(
                qt_widgets.QScrollArea, "LiveInterviewScroll"
            )
            assert scroll.horizontalScrollBar().maximum() == 0
            assert_widget_text_glyphs_supported(window.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX))
            main = window.live_page._main_panel
            side = window.live_page._side_panel
            primary = window.live_page._primary_button
            assert window.live_page.root.width() >= scroll.viewport().width() - 60
            assert adaptive.width() >= scroll.viewport().width() - 310
            assert primary.width() >= 220
            assert primary.height() >= primary.fontMetrics().height() + 8
            if not narrow:
                geometry = (
                    f"window={window.window.width()} viewport={scroll.viewport().width()} "
                    f"adaptive={adaptive.width()} main={main.width()} side={side.width()}"
                )
                assert main.width() >= 360, geometry
                assert side.width() >= 280, geometry
                if screen_name == "scored":
                    for rating in window.live_page.root.findChildren(qt_widgets.QRadioButton, "LiveRatingOption"):
                        assert_single_line_text_fits(rating)
                    for description in window.live_page.root.findChildren(qt_widgets.QLabel, "LiveRatingDescription"):
                        assert_wrapped_text_fits(description)
            else:
                assert main.width() >= scroll.viewport().width() - 330
                assert side.width() >= scroll.viewport().width() - 330
            output = tmp_path / f"{screen_name}-{width}x{height}-{scale}.png"
            rendered = window.window.grab()
            assert_no_large_unpainted_region(rendered)
            assert rendered.save(str(output))
            renders += 1

    assert renders == 12
    app.setFont(original_font)
    window.window.close()
    app.processEvents()
