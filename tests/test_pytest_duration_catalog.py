from __future__ import annotations

import ast
from pathlib import Path

from tools import pytest_duration_catalog


FOCUSED_PYSIDE_GUI_WORKFLOW_EXCEPTIONS = {
    "test_pyside_offer_screen_uses_guided_offer_widgets_and_editable_positions",
    "test_pyside_offer_screen_uses_part_time_template_and_shows_success_actions",
    "test_pyside_history_grid_shows_failed_retry_for_failed_deepseek_row",
    "test_pyside_failed_retry_button_requeues_deepseek_job",
    "test_pyside_retry_uses_persisted_deepseek_job_path",
    "test_pyside_admin_layout_uses_font_metrics_for_windows_text_scaling",
    "test_pyside_initial_window_fits_available_screen_after_display_scaling",
    "test_pyside_admin_rubrics_editor_matches_mockup_and_saves_draft",
    "test_pyside_history_offer_prefills_editable_shift_from_director_staffing_v2",
    "test_pyside_history_offer_generation_blocks_missing_template_without_status_change",
    "test_pyside_staffing_v2_notifications_dirty_exit_choices_use_real_dialog",
    "test_pyside_staffing_v2_notifications_named_delete_confirmation_controls_deletion",
    "test_pyside_staffing_v2_notifications_grid_collapses_and_restores_actual_columns",
    "test_pyside_staffing_v2_notifications_manual_payload_uses_attachment_picker_and_unsaved_draft",
    "test_pyside_staffing_v2_classrooms_dashboard_uses_new_shell_and_db_rows",
    "test_pyside_staffing_v2_add_person_dialog_creates_person_through_service",
    "test_pyside_staffing_v2_assignment_history_dashboard_renders_history_from_db",
    "test_pyside_staffing_v2_validation_dashboard_and_filter_drawer_use_existing_staffing_data",
    "test_pyside_history_offer_actions_advance_generated_and_approved_rows",
    "test_pyside_director_staffing_mode_uses_school_specific_db_when_other_school_locked",
    "test_interview_finalize_queues_director_referral_without_staffing_db_write",
    "test_director_staffing_poll_imports_queued_referral_and_refreshes_gui",
    "test_director_staffing_poll_imports_review_score_dismissal_and_removes_pending_referral",
    "test_pyside_staffing_v2_director_candidates_follow_admin_school_selector",
    "test_staffing_v2_director_interviews_backfill_passed_history_rows",
    "test_director_staffing_launch_queues_history_backfill_when_edit_lock_exists",
    "test_staffing_v2_director_interviews_delete_checked_pending_rows",
}

REQUIRED_PYSIDE_GUI_SCENARIO_SURFACES = {
    "staffing_v2_notifications_manager": "test_pyside_staffing_v2_notifications_manager_dashboard_scenario",
}


def test_determine_placement_uses_duration_and_gui_weight() -> None:
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=31.0, gui_heavy=True) == "gui_wave_1"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=21.0, gui_heavy=True) == "gui_wave_2"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=1.0, gui_heavy=True) == "gui_wave_3"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=16.0, gui_heavy=False) == "non_gui_tail"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=6.0, gui_heavy=False) == "non_gui_middle"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=0.5, gui_heavy=False) == "fast"


def test_duration_catalog_covers_collected_tests() -> None:
    nodeids = set(pytest_duration_catalog.collect_nodeids())
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()

    assert not nodeids.difference(entries)
    assert not set(entries).difference(nodeids)
    for entry in entries.values():
        assert isinstance(entry["duration_seconds_n2"], (int, float))
        assert isinstance(entry["gui_heavy"], bool)
        assert entry["placement"] == pytest_duration_catalog.determine_placement(
            duration_seconds_n2=float(entry["duration_seconds_n2"]),
            gui_heavy=entry["gui_heavy"],
        )


def test_qt_gui_tests_are_cataloged_as_gui_scenarios() -> None:
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()
    offenders: list[str] = []
    gui_surface_tokens = ("QApplication", "PySideInterviewWindow", "StaffingDashboardV2Page")
    test_paths = sorted(Path("tests").glob("test_*.py"))

    for path in test_paths:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if path.name == "test_pytest_duration_catalog.py" and node.name == "test_qt_gui_tests_are_cataloged_as_gui_scenarios":
                continue
            source = ast.get_source_segment(text, node) or ""
            if not any(token in source for token in gui_surface_tokens):
                continue
            nodeid = f"{path.as_posix()}::{node.name}"
            if not bool(entries.get(nodeid, {}).get("gui_heavy", False)):
                offenders.append(nodeid)

    assert offenders == [], "Qt GUI tests must be cataloged with gui_heavy: true:\n" + "\n".join(offenders)


def test_gui_scenario_catalog_entries_have_measured_scheduler_durations() -> None:
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()
    offenders = [
        nodeid
        for nodeid, entry in entries.items()
        if bool(entry.get("gui_heavy", False)) and entry.get("duration_source") != "measured"
    ]

    assert offenders == [], "GUI scenario tests must have measured scheduler durations:\n" + "\n".join(offenders)


def _decorator_texts(node: ast.FunctionDef) -> list[str]:
    return [ast.unparse(decorator) for decorator in node.decorator_list]


def test_marked_pyside_gui_tests_are_scenarios_or_focused_exceptions() -> None:
    path = Path("tests/test_pyside_interview_redesign.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        if not any("pyside_gui" in decorator for decorator in _decorator_texts(node)):
            continue
        base_name = node.name.split("[", 1)[0]
        if "scenario" in base_name or base_name in FOCUSED_PYSIDE_GUI_WORKFLOW_EXCEPTIONS:
            continue
        offenders.append(f"{path.as_posix()}::{node.name}")

    assert offenders == [], (
        "Marked PySide GUI tests must extend a named scenario first, or be listed as focused workflow exceptions:\n"
        + "\n".join(offenders)
    )


def test_required_pyside_gui_surfaces_have_named_scenarios() -> None:
    path = Path("tests/test_pyside_interview_redesign.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    test_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    missing = [
        f"{surface}: {test_name}"
        for surface, test_name in REQUIRED_PYSIDE_GUI_SCENARIO_SURFACES.items()
        if test_name not in test_names
    ]

    assert missing == [], "Required PySide GUI surfaces need named scenario tests:\n" + "\n".join(missing)
