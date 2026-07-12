"""Pytest configuration for dependency sanity checks."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from tools.pytest_duration_catalog import CATALOG_PATH, determine_placement, load_catalog


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_DOCX_IMPORT_HELP = (
    "Detected an incompatible 'docx' package in this environment. "
    "This project requires 'python-docx' (import name: docx). "
    "Fix by running: pip uninstall -y docx && pip install -r requirements.txt"
)


_MEASURED_SLOW_PYSIDE_TESTS = {
    "test_pyside_staffing_v2_assignment_history_dashboard_renders_history_from_db",
    "test_pyside_staffing_v2_validation_dashboard_and_filter_drawer_use_existing_staffing_data",
    "test_pyside_initial_window_fits_available_screen_after_display_scaling",
    "test_pyside_admin_layout_uses_font_metrics_for_windows_text_scaling",
    "test_pyside_staffing_v2_add_person_dialog_creates_person_through_service",
    "test_pyside_staffing_v2_classrooms_dashboard_uses_new_shell_and_db_rows",
    "test_pyside_admin_notifications_recipient_chips_remove_and_save",
    "test_pyside_admin_rubrics_editor_matches_mockup_and_saves_draft",
    "test_pyside_admin_notifications_create_button_opens_blank_rule_and_saves_card",
    "test_pyside_admin_notifications_editor_shows_mockup_status_panels",
    "test_pyside_admin_notification_rule_editor_saves_draft",
    "test_pyside_admin_templates_test_write_checks_selected_folder",
    "test_pyside_admin_templates_school_card_actions_use_selected_school",
    "test_pyside_admin_signal_hints_renders_all_hint_groups",
    "test_pyside_admin_advanced_json_expand_opens_read_only_viewer",
    "test_pyside_failed_retry_button_requeues_deepseek_job",
    "test_pyside_admin_signal_hints_uses_cards_without_legacy_table",
    "test_pyside_admin_add_question_dropzone_opens_blank_drawer_and_saves",
    "test_pyside_history_grid_shows_failed_retry_for_failed_deepseek_row",
    "test_pyside_admin_question_reorder_updates_draft_flow_without_changing_ids",
    "test_pyside_admin_question_drawer_delete_removes_custom_question_from_draft",
    "test_pyside_staffing_v2_dashboard_renders_parallel_main_dashboard_without_mutating_db",
    "test_pyside_admin_question_duplicate_creates_custom_copy_in_draft",
    "test_pyside_admin_advanced_json_marks_selected_file_and_copies_detail_path",
    "test_pyside_admin_signal_hints_search_and_detail_reference",
    "test_pyside_admin_templates_can_add_and_delete_school_draft",
    "test_pyside_admin_templates_copy_path_copies_selected_folder",
    "test_pyside_admin_add_track_modal_creates_draft_track",
    "test_pyside_admin_rubrics_filters_search_and_view_controls_trait_cards",
    "test_pyside_admin_templates_school_drawer_saves_folder_draft",
    "test_pyside_admin_advanced_json_copy_button_copies_visible_viewer_text",
    "test_pyside_admin_templates_new_template_adds_selected_school_template",
    "test_pyside_admin_validation_issue_filter_hides_nonmatching_cards",
    "test_pyside_admin_discard_confirmation_modal_lists_lost_changes",
    "test_pyside_admin_rubrics_renders_all_trait_cards",
    "test_pyside_history_offer_actions_advance_generated_and_approved_rows",
    "test_pyside_admin_validation_review_shows_blocked_banner_summary_and_guidance",
    "test_pyside_admin_validation_review_issue_cards_show_filter_why_and_what",
    "test_pyside_admin_publish_confirmation_summarizes_changes_and_validation",
    "test_pyside_admin_global_version_history_covers_admin_artifact_types",
    "test_pyside_admin_layout_keeps_controls_readable_on_narrow_windows",
    "test_pyside_admin_email_account_type_updates_provider_defaults_and_mockup_panels",
    "test_pyside_admin_advanced_json_selects_file_and_shows_readonly_viewer",
    "test_pyside_admin_rubrics_linked_question_follows_selected_trait",
    "test_pyside_admin_templates_offer_template_health_panel_lists_active_templates",
    "test_pyside_admin_validation_issue_card_has_collapsed_raw_details",
    "test_pyside_admin_question_card_edits_through_drawer_draft",
    "test_pyside_admin_questions_track_tabs_switch_visible_flow",
    "test_pyside_admin_dashboard_validation_issue_opens_affected_notification_rule",
    "test_pyside_admin_validation_details_dialog_shows_technical_payload",
    "test_staffing_v2_director_interviews_backfill_passed_history_rows",
    "test_pyside_staffing_v2_add_position_dialog_creates_need_now_position_through_service",
    "test_pyside_staffing_v2_add_classroom_dialog_creates_classroom_through_service",
    "test_pyside_progress_window_discovers_deepseek_progress_from_history",
    "test_pyside_staffing_v2_mark_need_now_dialog_clears_replacement",
    "test_pyside_staffing_v2_classrooms_paginates_and_saves_detail_without_assignment_mutation",
    "test_pyside_show_schedules_recording_interface_preload_once",
    "test_pyside_admin_templates_browse_folder_updates_selected_path",
    "test_pyside_admin_notifications_template_filter_hides_by_subject_body_completeness",
    "test_pyside_staffing_dashboard_visual_render_uses_real_seed_from_any_cwd",
    "test_pyside_admin_dashboard_draft_changes_lists_all_dirty_files",
    "test_pyside_progress_window_renders_task_status_list",
    "test_pyside_admin_notifications_body_toolbar_inserts_template_markup",
    "test_pyside_admin_modern_sections_hide_legacy_backing_tables",
    "test_pyside_staffing_v2_manage_filled_dialog_selects_next_workflow",
    "test_pyside_staffing_v2_update_permit_dialog_saves_people_permit_details",
    "test_pyside_last_question_footer_finalizes_and_shows_complete_home",
    "test_pyside_home_import_indeed_transcript_opens_rating_flow",
    "test_pyside_progress_window_auto_closes_after_deepseek_complete",
    "test_pyside_admin_notifications_event_filter_hides_nonmatching_rule_cards",
    "test_pyside_admin_prompt_variable_chips_include_all_visible_tokens",
    "test_pyside_admin_templates_school_cards_track_selected_state",
    "test_pyside_progress_window_immediately_shows_ordered_tasks_in_scroll_area",
    "test_pyside_admin_review_changes_button_opens_grouped_diff_dialog",
    "test_pyside_admin_studio_modern_dashboard_and_section_cards",
    "test_pyside_admin_advanced_json_mockup_summary_and_issue_panels",
}


_DURATION_REPORTS: dict[str, float] = {}


def pytest_configure() -> None:
    """Validate that the correct DOCX library is importable before test collection."""

    try:
        importlib.import_module("docx")
    except ModuleNotFoundError as exc:
        if exc.name == "exceptions":
            pytest.exit(_DOCX_IMPORT_HELP, returncode=2)
        raise


def _duration_catalog_by_nodeid() -> dict[str, dict[str, object]]:
    return {str(entry["nodeid"]): entry for entry in load_catalog(CATALOG_PATH).get("entries", [])}


def _slow_pyside_weight(item: pytest.Item) -> float:
    entry = _duration_catalog_by_nodeid().get(item.nodeid)
    if entry is None:
        return 10.0
    return float(entry.get("duration_seconds_n2", 10.0))


def _spread_slow_pyside_items(items: Sequence[pytest.Item], *, worker_count: int = 24) -> list[pytest.Item]:
    """Return items with measured slow PySide tests spread by expected duration."""

    slow_items = [item for item in items if item.get_closest_marker("slow_pyside") is not None]
    fast_items = [item for item in items if item.get_closest_marker("slow_pyside") is None]
    if not slow_items or not fast_items:
        return list(items)

    ordered: list[pytest.Item] = []
    fast_index = 0
    sorted_slow = sorted(slow_items, key=_slow_pyside_weight, reverse=True)
    heavy_slots_per_wave = max(4, min(8, worker_count // 3))
    waves = [sorted_slow[index : index + heavy_slots_per_wave] for index in range(0, len(sorted_slow), heavy_slots_per_wave)]
    for wave_index, wave in enumerate(waves):
        target_fast_count = round(len(fast_items) * wave_index / len(waves))
        ordered.extend(fast_items[fast_index:target_fast_count])
        fast_index = target_fast_count
        ordered.extend(wave)
    ordered.extend(fast_items[fast_index:])
    return ordered


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark measured heavy PySide tests so fast and GUI-heavy passes can be split."""

    catalog = _duration_catalog_by_nodeid()
    for item in items:
        entry = catalog.get(item.nodeid)
        if (entry is not None and bool(entry.get("gui_heavy", False))) or item.name in _MEASURED_SLOW_PYSIDE_TESTS:
            item.add_marker(pytest.mark.pyside_gui)
            item.add_marker(pytest.mark.slow_pyside)
    worker_count = getattr(config.option, "numprocesses", None)
    if worker_count and not getattr(config.option, "markexpr", ""):
        items[:] = _spread_slow_pyside_items(items, worker_count=worker_count if isinstance(worker_count, int) else 24)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" and os.environ.get("PYTEST_DURATION_CATALOG_OUT"):
        _DURATION_REPORTS[report.nodeid] = report.duration


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    out_path = os.environ.get("PYTEST_DURATION_CATALOG_OUT")
    if not out_path or hasattr(session.config, "workerinput"):
        return
    catalog_path = Path(out_path)
    if not catalog_path.is_absolute():
        catalog_path = ROOT / catalog_path
    catalog = load_catalog(catalog_path)
    existing = {str(entry["nodeid"]): dict(entry) for entry in catalog.get("entries", [])}
    for nodeid, duration in _DURATION_REPORTS.items():
        current = existing.setdefault(
            nodeid,
            {
                "nodeid": nodeid,
                "duration_seconds_n2": 0.001,
                "duration_source": "collection_default",
                "gui_heavy": False,
                "placement": "fast",
            },
        )
        current["duration_seconds_n2"] = round(float(duration), 3)
        current["duration_source"] = "measured"
        current["placement"] = determine_placement(
            duration_seconds_n2=float(current["duration_seconds_n2"]),
            gui_heavy=bool(current.get("gui_heavy", False)),
        )
    catalog["entries"] = [existing[nodeid] for nodeid in sorted(existing)]
    catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False, allow_unicode=False), encoding="utf-8")


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """Avoid worker startup for one explicit test when callers opt into xdist auto."""

    cli_args = list(config.invocation_params.args)
    user_set_xdist = any(arg == "-n" or arg.startswith("-n") or arg.startswith("--numprocesses") for arg in cli_args)
    explicit_targets = [arg for arg in config.args if "::" in arg]
    if not user_set_xdist and len(config.args) == 1 and len(explicit_targets) == 1:
        return 0
    return None


@pytest.fixture()
def src_cwd(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run a test from src while preserving repository import paths."""

    monkeypatch.chdir(SRC)
    return SRC
