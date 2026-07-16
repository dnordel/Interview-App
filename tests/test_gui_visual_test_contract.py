from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _decorator_name(node: ast.expr) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _renders_qt_screenshot(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_grab = False
    has_save = False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        has_grab = has_grab or child.func.attr == "grab"
        has_save = has_save or child.func.attr == "save"
    return has_grab and has_save


def _registers_seeded_visual_database(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(child.func, ast.Attribute)
        and child.func.attr == "expect_seeded"
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "visual_test_databases"
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    )


def test_qt_screenshot_tests_require_seeded_visual_test_db() -> None:
    failures: list[str] = []
    rendered_tests = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _renders_qt_screenshot(node):
                continue
            rendered_tests += 1
            decorators = {_decorator_name(value) for value in node.decorator_list}
            parameters = {argument.arg for argument in node.args.args}
            prefix = f"{path.name}::{node.name}"
            if "pytest.mark.pyside_gui" not in decorators:
                failures.append(f"{prefix} missing @pytest.mark.pyside_gui")
            if "pytest.mark.visual_inspection" not in decorators:
                failures.append(f"{prefix} missing @pytest.mark.visual_inspection")
            if "visual_test_databases" not in parameters:
                failures.append(f"{prefix} missing visual_test_databases fixture")
            if not _registers_seeded_visual_database(node):
                failures.append(f"{prefix} does not register a seeded domain DB")
    assert rendered_tests > 0, "No Qt screenshot tests found"
    assert not failures, "\n".join(failures)


def test_mockup_workflow_controls_have_public_interaction_scenarios() -> None:
    coverage = {
        ("test_pyside_interview_redesign.py", "test_pyside_new_interview_setup_scenario"): (
            'external_nav_buttons["interviews"]',
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_new_interview_setup_begins_first_interview_without_contact_fields"): (
            "home_begin_button",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_manual_audio_preflight_updates_setup_without_starting_interview"): (
            "home_test_audio_button",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_new_interview_cancel_discards_changes_and_returns_dashboard"): (
            "HiringV2SetupCancel",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_candidates_resume_button_routes_saved_draft_into_live_interview"): (
            "tabs.tabBar()",
            "candidate_continue_draft_button",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_home_import_indeed_transcript_opens_rating_flow"): (
            "ImportIndeedTranscriptButton",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_home_delete_saved_draft_requires_confirmation"): (
            "candidate_delete_draft_button",
        ),
        ("test_pyside_interview_redesign.py", "test_pyside_exit_live_interview_saves_draft_and_returns_fresh_setup"): (
            "LiveInterviewExit",
        ),
        ("test_pyside_live_interview.py", "test_pyside_live_introduction_screen_scenario"): (
            'buttons["next"]',
        ),
        ("test_pyside_live_interview.py", "test_pyside_live_back_button_saves_current_controls_without_advancing"): (
            'pyside_live_footer_action") == "back"',
        ),
        ("test_pyside_live_interview.py", "test_pyside_live_non_scored_transcript_and_audio_scenario"): (
            "LiveTranscriptEdit",
            "LiveTranscriptEditorSave",
            'button.text() == "Cancel"',
        ),
        ("test_pyside_live_interview.py", "test_pyside_live_scored_rating_and_anchor_scenario"): (
            "LiveRatingAnchor",
            "LiveRatingOption",
            "LiveFlagNeedsFollowUp",
            "LiveFlagNoExample",
            "LiveFlagDisqualifier",
            "LiveInterviewSkipRating",
        ),
        ("test_pyside_live_interview.py", "test_pyside_live_availability_page_uses_non_scored_controls_and_public_next"): (
            "LiveInterviewPrimaryAction",
            '"Finalize"',
        ),
        ("test_pyside_completed_interview.py", "test_pyside_completed_overview_processing_to_complete_scenario"): (
            "CompletedInterviewRetry",
        ),
        ("test_pyside_completed_interview.py", "test_pyside_completed_transcript_browser_scenario"): (
            "CompletedTranscriptToggle",
            "CompletedTranscriptFilter",
            "CompletedTranscriptSearch",
        ),
        ("test_pyside_completed_interview.py", "test_pyside_completed_non_scored_detail_preserves_mark_important"): (
            "CompletedQuestionCancel",
            "CompletedQuestionSave",
            "CompletedTranscriptDetail",
        ),
        ("test_pyside_completed_interview.py", "test_pyside_completed_actions_and_finish_scenario"): (
            "CompletedInterviewBack",
            "CompletedInterviewReport",
            "CompletedInterviewExport",
            "CompletedInterviewFinish",
        ),
        ("test_pyside_completed_interview.py", "test_pyside_completed_export_actions_write_docx_pdf_and_utf8_txt"): (
            "menu.actions()",
            ".trigger()",
        ),
    }
    failures: list[str] = []
    for (filename, function_name), tokens in coverage.items():
        path = ROOT / "tests" / filename
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        function = next(
            (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name),
            None,
        )
        if function is None:
            failures.append(f"{filename}::{function_name} missing")
            continue
        body = ast.get_source_segment(source, function) or ""
        missing = [token for token in tokens if token not in body]
        if missing:
            failures.append(f"{filename}::{function_name} missing interaction tokens: {missing}")
        if not any(marker in body for marker in (".click(", "mouseClick(", ".trigger(", ".setText(", ".setCurrentText(")):
            failures.append(f"{filename}::{function_name} has no public Qt interaction")
    assert not failures, "\n".join(failures)
