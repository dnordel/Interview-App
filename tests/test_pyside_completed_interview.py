from __future__ import annotations

import os
from pathlib import Path
import queue

import pytest

import pyside_interview_app
from pyside_interview_app import PySideInterviewSession, build_interview_redesign_model
from pyside_completed_interview import (
    CompletionState,
    build_completed_interview_view_model,
    build_completed_transcript_export,
)
from visual_test_support import (
    TYPOGRAPHY_STRESS_TEXT,
    VisualTestDatabaseRegistry,
    assert_no_large_unpainted_region,
    assert_table_headers_fit,
    assert_vertical_text_fits,
    assert_widget_text_glyphs_supported,
    configure_visual_test_app,
)


def _completed_session(
    tmp_path: Path,
    *,
    history_path: Path | None = None,
) -> tuple[object, PySideInterviewSession]:
    model = build_interview_redesign_model(
        history_path=history_path or tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Sofia Ramirez", school="Hawthorne", track_key="infant_toddler")
    for index, item in enumerate(session._workflow_items()):
        session.answers[item.question_id] = {
            "kind": item.kind,
            "title": item.title,
            "prompt": item.prompt,
            "notes": "Saved evidence" if item.kind != "intro" else "",
            "score": "4" if item.kind == "trait" else "",
            "quick_actions": [],
        }
        if item.kind != "intro":
            session.flow_candidate_transcripts[index] = f"Candidate response for {item.title}."
    session.current_index = len(session._workflow_items())
    session.save_draft()
    scoring = pyside_interview_app.ScoringEngine.evaluate(
        model.rubric,
        session.track_key,
        {
            item.question_id: {"raw_score": 4}
            for item in session._workflow_items()
            if item.kind == "trait"
        },
    )
    pyside_interview_app.InterviewHistoryStore(model.history_path).append(
        {
            "history_id": "completed-overview-fixture",
            "candidate_name": session.candidate_name,
            "school": session.school,
            "position": model.flows[session.track_key].label,
            "interview_date": "2026-07-15",
            "outcome": scoring["outcome"],
            "score": scoring["percent_of_max_label"],
            "answers": session.answers,
            "flow_candidate_transcripts": {
                str(index): text
                for index, text in session.flow_candidate_transcripts.items()
            },
            "scoring": scoring,
        }
    )
    return model, session


def test_completed_overview_derives_dynamic_scores_and_review_items(tmp_path: Path) -> None:
    model, session = _completed_session(tmp_path)
    workflow = session._workflow_items()
    traits = [item for item in workflow if item.kind == "trait"]
    session.answers[traits[0].question_id]["score"] = "5"
    session.answers[traits[0].question_id]["quick_actions"] = ["Needs follow-up"]
    session.answers[traits[1].question_id]["score"] = "3"
    session.answers[traits[2].question_id].update({"score": "", "skipped": True})
    session.answers[traits[3].question_id]["score"] = ""
    scoring = pyside_interview_app.ScoringEngine.evaluate(
        model.rubric,
        session.track_key,
        {
            item.question_id: {
                "raw_score": int(session.answers[item.question_id]["score"])
                if session.answers[item.question_id]["score"]
                else None,
                "skipped": bool(session.answers[item.question_id].get("skipped")),
            }
            for item in traits
        },
    )

    view = build_completed_interview_view_model(
        candidate_name=session.candidate_name,
        school=session.school,
        position=model.flows[session.track_key].label,
        workflow=workflow,
        answers=session.answers,
        transcripts=session.flow_candidate_transcripts,
        scoring=scoring,
        completion_state=CompletionState.COMPLETE,
    )

    assert view.total_steps == len([item for item in workflow if item.kind != "intro"])
    assert all(row.kind != "intro" for row in view.question_rows)
    assert view.weighted_total == scoring["weighted_total"]
    assert view.max_weighted_total == scoring["max_weighted_total"]
    assert view.percent_of_max == scoring["percent_of_max"]
    assert view.scored_count == len([row for row in view.trait_rows if row.rating is not None])
    assert view.skipped_count == 1
    assert any(traits[0].title in strength for strength in view.strengths)
    assert any("3 / 5" in item for item in view.review_items)
    assert any("Skipped" in item for item in view.review_items)
    assert any("Missing rating" in item for item in view.review_items)
    assert view.can_finish is False

    session.answers[traits[3].question_id]["score"] = "4"
    complete = build_completed_interview_view_model(
        candidate_name=session.candidate_name,
        school=session.school,
        position=model.flows[session.track_key].label,
        workflow=workflow,
        answers=session.answers,
        transcripts=session.flow_candidate_transcripts,
        scoring=scoring,
        completion_state=CompletionState.COMPLETE,
    )
    assert complete.can_finish is True


def test_completed_overview_all_fours_has_no_score_based_strengths_or_review_items(tmp_path: Path) -> None:
    model, session = _completed_session(tmp_path)
    view = build_completed_interview_view_model(
        candidate_name=session.candidate_name,
        school=session.school,
        position=model.flows[session.track_key].label,
        workflow=session._workflow_items(),
        answers=session.answers,
        transcripts=session.flow_candidate_transcripts,
        scoring=pyside_interview_app.ScoringEngine.evaluate(
            model.rubric,
            session.track_key,
            {
                item.question_id: {"raw_score": 4}
                for item in session._workflow_items()
                if item.kind == "trait"
            },
        ),
        completion_state=CompletionState.COMPLETE,
    )

    assert view.strengths == ()
    assert view.review_items == ()


def test_completed_transcript_export_excludes_intro_and_marks_skipped(tmp_path: Path) -> None:
    model, session = _completed_session(tmp_path)
    workflow = session._workflow_items()
    skipped = next(item for item in workflow if item.kind == "custom")
    trait = next(item for item in workflow if item.kind == "trait")
    session.answers[trait.question_id]["quick_actions"] = ["Needs follow-up"]
    session.answers[skipped.question_id]["skipped"] = True
    view = build_completed_interview_view_model(
        candidate_name=session.candidate_name,
        school=session.school,
        position=model.flows[session.track_key].label,
        workflow=workflow,
        answers=session.answers,
        transcripts=session.flow_candidate_transcripts,
        scoring={},
        completion_state=CompletionState.COMPLETE,
    )

    exported = build_completed_transcript_export(view)

    assert "Introduction Script" not in exported
    assert skipped.title in exported
    assert "Question Skipped" in exported
    assert "Candidate response for" in exported


@pytest.mark.pyside_gui
@pytest.mark.visual_inspection
def test_pyside_completed_overview_processing_to_complete_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    visual_test_databases: VisualTestDatabaseRegistry,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    history_path = visual_test_databases.database("completed_interview_history.sqlite3")
    visual_test_databases.expect_seeded(history_path, table="interview_history")
    model, session = _completed_session(tmp_path, history_path=history_path)
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._pyside_finalize_running = True
    window._render_review_page()
    app.processEvents()

    page = window.interview_tabs.widget(3)
    assert page.findChild(qt_widgets.QLabel, "CompletedInterviewTitle").text() == "Interview Complete"
    assert page.findChild(qt_widgets.QLabel, "CompletedInterviewStatus").text() == "Finalizing interview"
    assert page.findChild(qt_widgets.QProgressBar, "CompletedInterviewProgress").value() < 100
    for name in ("CompletedInterviewBack", "CompletedInterviewReport", "CompletedInterviewExport", "CompletedInterviewFinish"):
        assert not page.findChild(qt_widgets.QPushButton, name).isEnabled()

    retried: list[bool] = []
    monkeypatch.setattr(window, "_generate_interview_notes_from_session", lambda: retried.append(True))
    window._pyside_finalize_running = False
    window._completed_finalize_error = "Canonical transcript could not be saved."
    window._render_review_page()
    app.processEvents()
    assert page.findChild(qt_widgets.QLabel, "CompletedInterviewStatus").text() == "Finalization failed"
    assert "Canonical transcript" in page.findChild(qt_widgets.QLabel, "CompletedInterviewWarning").text()
    page.findChild(qt_widgets.QPushButton, "CompletedInterviewRetry").click()
    assert retried == [True]

    window._pyside_finalize_running = False
    window._completed_finalize_error = ""
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    app.processEvents()

    assert page.findChild(qt_widgets.QLabel, "CompletedInterviewStatus").text() == "Interview complete"
    assert page.findChild(qt_widgets.QProgressBar, "CompletedInterviewProgress").value() == 100
    assert page.findChild(qt_widgets.QPushButton, "CompletedInterviewBack").isEnabled()
    assert sum(
        label.text() == "None identified based on scores."
        for label in page.findChildren(qt_widgets.QLabel)
    ) == 2
    window.window.show()
    window.interview_tabs.setCurrentIndex(3)
    window._show_hiring_closeout()
    app.processEvents()
    original_font = app.font()
    render_cases = (
        (1672, 941, 1.0),
        (1366, 768, 1.0),
        (1366, 768, 1.25),
        (1366, 768, 1.5),
    )
    for width, height, scale in render_cases:
        scaled_font = type(original_font)(original_font)
        scaled_font.setPointSizeF(max(8.0, 10.0 * scale))
        app.setFont(scaled_font)
        window.window.resize(width, height)
        narrow = ((width - 300) / scale) < 1180
        app.processEvents()
        window._apply_responsive_layout()
        app.processEvents()
        root = window.completed_interview_page.root
        adaptive = root.findChild(qt_widgets.QWidget, "CompletedInterviewAdaptiveContent")
        assert adaptive.property("layoutMode") == ("narrow" if narrow else "desktop")
        scroll = page.findChild(qt_widgets.QScrollArea, "CompletedInterviewScroll")
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.widget().width() >= scroll.viewport().width() - 20
        assert window.hiring_v2_router.interview_widget.horizontalScrollBar().maximum() == 0
        assert_widget_text_glyphs_supported(root)
        assert_table_headers_fit(root.findChild(qt_widgets.QTableWidget, "CompletedInterviewTraitTable"))
        rendered = window.window.grab()
        assert_no_large_unpainted_region(rendered)
        assert rendered.save(str(tmp_path / f"completed-{width}-{height}-{scale}.png"))
        if (width, height, scale) == (1672, 941, 1.0):
            finish = root.findChild(qt_widgets.QPushButton, "CompletedInterviewFinish")
            finish_bottom = finish.mapTo(window.window, finish.rect().bottomLeft()).y()
            assert finish_bottom <= window.window.height()
    app.setFont(original_font)
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.visual_inspection
def test_pyside_completed_typography_stress_visual_scenario(
    tmp_path: Path,
    visual_test_databases: VisualTestDatabaseRegistry,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    history_path = visual_test_databases.database("completed_interview_typography.sqlite3")
    visual_test_databases.expect_seeded(history_path, table="interview_history")
    model, session = _completed_session(tmp_path, history_path=history_path)
    session.candidate_name = TYPOGRAPHY_STRESS_TEXT
    for index, item in enumerate(session._workflow_items()):
        if item.kind == "intro":
            continue
        session.flow_candidate_transcripts[index] = f"{TYPOGRAPHY_STRESS_TEXT} candidate response."
        session.answers[item.question_id]["notes"] = f"{TYPOGRAPHY_STRESS_TEXT} interviewer notes."
    pyside_interview_app.InterviewHistoryStore(history_path).update_row(
        "completed-overview-fixture",
        {
            "candidate_name": TYPOGRAPHY_STRESS_TEXT,
            "answers": session.answers,
            "flow_candidate_transcripts": {str(index): text for index, text in session.flow_candidate_transcripts.items()},
        },
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)
    window._show_hiring_closeout()
    window.window.resize(1672, 941)
    window.window.show()
    app.processEvents()
    window._apply_responsive_layout()
    app.processEvents()
    root = window.completed_interview_page.root
    candidate = root.findChild(qt_widgets.QLabel, "CompletedInterviewCandidateName")
    assert_vertical_text_fits(candidate, TYPOGRAPHY_STRESS_TEXT)
    assert_widget_text_glyphs_supported(root)
    assert_table_headers_fit(root.findChild(qt_widgets.QTableWidget, "CompletedInterviewTraitTable"))
    rendered = window.window.grab()
    assert_no_large_unpainted_region(rendered)
    assert rendered.save(str(tmp_path / "completed-typography.png"))
    window.window.close()
    app.processEvents()


def test_pyside_completed_overview_failure_wins_after_partial_history_persistence(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._pyside_finalize_running = False
    window._completed_finalize_error = "Candidate report snapshot could not be saved."

    window._render_review_page()
    app.processEvents()

    page = window.interview_tabs.widget(3)
    assert page.findChild(qt_widgets.QLabel, "CompletedInterviewStatus").text() == "Finalization failed"
    assert page.findChild(qt_widgets.QPushButton, "CompletedInterviewRetry") is not None
    assert not page.findChild(qt_widgets.QPushButton, "CompletedInterviewFinish").isEnabled()
    window.window.close()
    app.processEvents()


def test_pyside_completed_finalize_failure_sanitizes_private_exception_text(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    results: queue.Queue[dict[str, object]] = queue.Queue()
    private_text = "candidate said private captured speech"
    results.put({"ok": False, "error": RuntimeError(private_text)})
    timer = type("Timer", (), {"stop": lambda _self: None, "deleteLater": lambda _self: None})()

    window._poll_pyside_finalize_worker(results, timer)
    app.processEvents()

    assert private_text not in window._completed_finalize_error
    assert "saved draft" in window._completed_finalize_error
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_transcript_browser_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    model, session = _completed_session(tmp_path)
    workflow = session._workflow_items()
    skipped = next(item for item in workflow if item.kind == "custom")
    trait = next(item for item in workflow if item.kind == "trait")
    session.answers[trait.question_id]["quick_actions"] = ["Needs follow-up"]
    session.answers[skipped.question_id]["skipped"] = True
    session.answers[skipped.question_id]["score"] = ""
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)
    window.window.show()
    app.processEvents()

    page = window.interview_tabs.widget(3)
    cards = page.findChildren(qt_widgets.QFrame, "CompletedTranscriptCard")
    assert len(cards) == len([item for item in workflow if item.kind != "intro"])
    skipped_card = next(card for card in cards if card.property("questionId") == skipped.question_id)
    assert "Question Skipped" in " ".join(label.text() for label in skipped_card.findChildren(qt_widgets.QLabel))
    assert skipped_card.findChild(qt_widgets.QToolButton, "CompletedTranscriptToggle") is None

    first = next(card for card in cards if card.property("questionId") == trait.question_id)
    excerpt = first.findChild(qt_widgets.QLabel, "CompletedTranscriptExcerpt")
    assert excerpt.maximumHeight() <= excerpt.fontMetrics().lineSpacing() * 2 + 8
    full = first.findChild(qt_widgets.QLabel, "CompletedTranscriptFullText")
    assert full.isHidden()
    first.findChild(qt_widgets.QToolButton, "CompletedTranscriptToggle").click()
    app.processEvents()
    assert not full.isHidden()
    assert first.findChild(qt_widgets.QLabel, "CompletedTranscriptExpandedNotes").text() == "Saved evidence"
    assert "Needs follow-up" in first.findChild(qt_widgets.QLabel, "CompletedTranscriptExpandedFlags").text()
    assert "4 / 5" in first.findChild(qt_widgets.QLabel, "CompletedTranscriptExpandedRating").text()

    filter_box = page.findChild(qt_widgets.QComboBox, "CompletedTranscriptFilter")
    filter_box.setCurrentText("Scored")
    app.processEvents()
    assert all(card.isHidden() or card.property("category") == "Scored" for card in cards)

    filter_box.setCurrentText("All questions")
    search = page.findChild(qt_widgets.QLineEdit, "CompletedTranscriptSearch")
    target = next(card for card in cards if not card.property("skipped"))
    search.setText(str(target.property("questionId")))
    app.processEvents()
    assert sum(not card.isHidden() for card in cards) == 1
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_detail_edit_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    model, session = _completed_session(tmp_path)
    trait = next(item for item in session._workflow_items() if item.kind == "trait")
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)
    app.processEvents()
    page = window.interview_tabs.widget(3)
    card = next(
        card
        for card in page.findChildren(qt_widgets.QFrame, "CompletedTranscriptCard")
        if card.property("questionId") == trait.question_id
    )

    def edit_dialog() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "CompletedQuestionDetailDialog" and widget.isVisible())
        dialog.findChild(qt_widgets.QTextEdit, "CompletedQuestionTranscriptEdit").setPlainText("Corrected final transcript.")
        dialog.findChild(qt_widgets.QTextEdit, "CompletedQuestionNotesEdit").setPlainText("Corrected evidence.")
        dialog.findChild(qt_widgets.QSpinBox, "CompletedQuestionRatingEdit").setValue(5)
        dialog.findChild(qt_widgets.QCheckBox, "CompletedQuestionNeedsFollowUp").setChecked(True)
        dialog.findChild(qt_widgets.QPushButton, "CompletedQuestionSave").click()

    qt_core.QTimer.singleShot(0, edit_dialog)
    card.findChild(qt_widgets.QPushButton, "CompletedTranscriptDetail").click()
    app.processEvents()

    assert session.live_transcript(trait.flow_index if hasattr(trait, "flow_index") else session._workflow_items().index(trait)) == "Corrected final transcript."
    assert session.answers[trait.question_id]["notes"] == "Corrected evidence."
    assert session.answers[trait.question_id]["score"] == "5"
    assert "Needs follow-up" in session.answers[trait.question_id]["quick_actions"]
    for _attempt in range(400):
        if not window._pyside_finalize_running:
            break
        qt_test.QTest.qWait(25)
        app.processEvents()
    assert not window._pyside_finalize_running
    rows = pyside_interview_app.InterviewHistoryStore(model.history_path).load()
    assert len(rows) == 1
    assert rows[0]["review_scores"][trait.question_id] == "5"
    assert "5 / 5" in page.findChild(qt_widgets.QTableWidget, "CompletedInterviewTraitTable").item(0, 1).text()
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_non_scored_detail_preserves_mark_important(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    question = next(item for item in session._workflow_items() if item.kind == "custom")
    session.answers[question.question_id]["quick_actions"] = ["Mark as important"]
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)

    card = next(
        card
        for card in window.interview_tabs.widget(3).findChildren(qt_widgets.QFrame, "CompletedTranscriptCard")
        if card.property("questionId") == question.question_id
    )

    def cancel_once() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "CompletedQuestionDetailDialog" and widget.isVisible())
        dialog.findChild(qt_widgets.QTextEdit, "CompletedQuestionTranscriptEdit").setPlainText("Discarded response.")
        dialog.findChild(qt_widgets.QPushButton, "CompletedQuestionCancel").click()

    qt_core.QTimer.singleShot(0, cancel_once)
    card.findChild(qt_widgets.QPushButton, "CompletedTranscriptDetail").click()
    app.processEvents()
    assert session.live_transcript(session._workflow_items().index(question)) != "Discarded response."

    def inspect_and_save() -> None:
        dialog = next(widget for widget in app.topLevelWidgets() if widget.objectName() == "CompletedQuestionDetailDialog" and widget.isVisible())
        assert dialog.findChild(qt_widgets.QSpinBox, "CompletedQuestionRatingEdit") is None
        assert dialog.findChild(qt_widgets.QCheckBox, "CompletedQuestionNeedsFollowUp") is None
        important = dialog.findChild(qt_widgets.QCheckBox, "CompletedQuestionMarkImportant")
        assert important.isChecked()
        dialog.findChild(qt_widgets.QTextEdit, "CompletedQuestionTranscriptEdit").setPlainText("Updated non-scored response.")
        dialog.findChild(qt_widgets.QPushButton, "CompletedQuestionSave").click()

    qt_core.QTimer.singleShot(0, inspect_and_save)
    card.findChild(qt_widgets.QPushButton, "CompletedTranscriptDetail").click()
    app.processEvents()
    assert session.answers[question.question_id]["quick_actions"] == ["Mark as important"]
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_actions_and_finish_scenario(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    configure_visual_test_app(app)
    model, session = _completed_session(tmp_path)
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    opened: list[tuple[str, str]] = []
    window.staffing_v2_host = type(
        "ReportHost",
        (),
        {"open_candidate_report": lambda _self, history_id, school: opened.append((history_id, school))},
    )()
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)
    app.processEvents()
    page = window.interview_tabs.widget(3)

    page.findChild(qt_widgets.QPushButton, "CompletedInterviewReport").click()
    assert opened == [("completed-overview-fixture", "Hawthorne")]

    page.findChild(qt_widgets.QPushButton, "CompletedInterviewExport").click()
    app.processEvents()
    menu = next(widget for widget in app.topLevelWidgets() if isinstance(widget, qt_widgets.QMenu) and widget.objectName() == "CompletedInterviewExportMenu")
    assert [action.text() for action in menu.actions()] == ["Word report (.docx)", "PDF report (.pdf)", "Transcript (.txt)"]
    menu.close()

    page.findChild(qt_widgets.QPushButton, "CompletedInterviewBack").click()
    app.processEvents()
    assert window.interview_tabs.currentIndex() == 2
    assert session.current_index == len(session._workflow_items()) - 1
    assert window.recording_session is None

    session.current_index = len(session._workflow_items())
    session.save_draft()
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.setCurrentIndex(3)
    app.processEvents()
    window.interview_tabs.widget(3).findChild(qt_widgets.QPushButton, "CompletedInterviewFinish").click()
    app.processEvents()
    assert window.session is None
    assert window.interview_tabs.currentIndex() == 0
    assert not session.draft_path.exists()
    assert window.home_candidate_input.text() == ""
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_export_actions_write_docx_pdf_and_utf8_txt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    source_docx = tmp_path / "canonical-report.docx"
    source_docx.write_bytes(b"docx fixture")
    generated_pdf = tmp_path / "canonical-report.pdf"
    generated_pdf.write_bytes(b"pdf fixture")
    pyside_interview_app.InterviewHistoryStore(model.history_path).update_row(
        "completed-overview-fixture", {"saved_report_path": str(source_docx)}
    )
    non_intro = next(index for index, item in enumerate(session._workflow_items()) if item.kind != "intro")
    session.flow_candidate_transcripts[non_intro] = "Typography Å É y g j h p q candidate response."
    destinations = {
        "Export Word Interview Report": str(tmp_path / "exported.docx"),
        "Export PDF Interview Report": str(tmp_path / "exported.pdf"),
        "Export Candidate Transcript": str(tmp_path / "exported.txt"),
    }
    monkeypatch.setattr(
        qt_widgets.QFileDialog,
        "getSaveFileName",
        lambda _parent, title, *_args: (destinations[title], ""),
    )
    monkeypatch.setattr(pyside_interview_app, "_ensure_offer_pdf_path", lambda _path: str(generated_pdf))
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    page = window.interview_tabs.widget(3)
    page.findChild(qt_widgets.QPushButton, "CompletedInterviewExport").click()
    app.processEvents()
    menu = window.completed_export_menu
    for action in menu.actions():
        action.trigger()
        app.processEvents()
    assert (tmp_path / "exported.docx").read_bytes() == b"docx fixture"
    assert (tmp_path / "exported.pdf").read_bytes() == b"pdf fixture"
    transcript = (tmp_path / "exported.txt").read_text(encoding="utf-8")
    assert "Å É y g j h p q" in transcript
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_export_cancel_and_pdf_failure_are_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    source = tmp_path / "report.docx"
    source.write_bytes(b"report")
    pyside_interview_app.InterviewHistoryStore(model.history_path).update_row(
        "completed-overview-fixture", {"saved_report_path": str(source)}
    )
    warnings: list[str] = []
    monkeypatch.setattr(qt_widgets.QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    monkeypatch.setattr(pyside_interview_app, "_ensure_offer_pdf_path", lambda _path: None)
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    export = window.interview_tabs.widget(3).findChild(qt_widgets.QPushButton, "CompletedInterviewExport")
    export.click()
    app.processEvents()
    actions = {action.text(): action for action in window.completed_export_menu.actions()}
    actions["Word report (.docx)"].trigger()
    actions["PDF report (.pdf)"].trigger()
    app.processEvents()
    assert warnings == ["PDF conversion failed. Confirm Microsoft Word is installed."]
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_pyside_completed_finish_surfaces_draft_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model, session = _completed_session(tmp_path)
    warnings: list[str] = []
    original_unlink = Path.unlink

    def fail_draft_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == session.draft_path:
            raise OSError("private filesystem detail")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_draft_unlink)
    monkeypatch.setattr(qt_widgets.QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session
    window._review_history_id = "completed-overview-fixture"
    window._render_review_page()
    window.interview_tabs.widget(3).findChild(qt_widgets.QPushButton, "CompletedInterviewFinish").click()
    app.processEvents()
    assert window.session is session
    assert warnings == ["The completed interview was saved, but its local draft could not be removed. Try Save & Finish again."]
    window.window.close()
    app.processEvents()
