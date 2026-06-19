from __future__ import annotations

import importlib

import pytest

import interview_runtime
import onboarding_operations
import platform_services
import scoring_reporting
import ui_composition


def test_platform_services_exposes_active_platform_helpers():
    assert "storage_utils" in platform_services.available_modules()
    assert "app_content" not in platform_services.available_modules()
    assert "artifact_cleanup" not in platform_services.available_modules()
    assert "config_adapters" not in platform_services.available_modules()
    assert "ux_metrics" not in platform_services.available_modules()
    assert callable(platform_services.atomic_write_json)
    assert callable(platform_services.compose_intro_script)
    assert callable(platform_services.cleanup_stale_artifacts)
    assert callable(platform_services.load_json_dict)
    assert platform_services.UxMetricsLogger


def test_ui_composition_exposes_active_ui_helpers():
    assert "ui_feedback" not in ui_composition.available_modules()
    assert "question_screens" not in ui_composition.available_modules()
    assert callable(ui_composition.sanitize_user_error)
    assert callable(ui_composition.should_display_modal)
    assert ui_composition.TraitScreenUI


def test_interview_runtime_exposes_existing_session_types():
    from interview_app.session_context import InterviewSessionContext

    assert "interview_app.session_context" in interview_runtime.available_modules()
    assert interview_runtime.InterviewSessionContext is InterviewSessionContext


def test_scoring_reporting_exposes_active_scoring_helpers():
    assert "reporting" not in scoring_reporting.available_modules()
    assert "offer_letter" not in scoring_reporting.available_modules()
    assert "trait_scoring_adapter" in scoring_reporting.available_modules()
    assert callable(scoring_reporting.normalize_candidate_title)
    assert callable(scoring_reporting.build_integration_payload)
    assert callable(scoring_reporting.build_trait_scoring_payload)


def test_onboarding_operations_exposes_existing_models():
    assert "onboarding_models" not in onboarding_operations.available_modules()
    assert onboarding_operations.Employee


@pytest.mark.parametrize(
    "facade",
    [
        platform_services,
        ui_composition,
        interview_runtime,
        scoring_reporting,
        onboarding_operations,
    ],
)
def test_flattened_facades_reject_out_of_scope_modules(facade):
    with pytest.raises(AttributeError):
        facade.load_compat_module("not_a_real_compat_module")


@pytest.mark.parametrize(
    "facade,owner",
    [
        (platform_services, "platform_services"),
        (ui_composition, "ui_composition"),
        (interview_runtime, "interview_runtime"),
        (scoring_reporting, "scoring_reporting"),
        (onboarding_operations, "onboarding_operations"),
    ],
)
def test_flattened_facades_publish_migration_contract(facade, owner):
    ownership = facade.module_ownership()

    assert set(ownership) == set(facade.available_modules())
    assert not ownership or set(ownership.values()) == {owner}
    assert owner in facade.wrapper_policy()


@pytest.mark.parametrize(
    "facade",
    [
        platform_services,
        ui_composition,
        interview_runtime,
        scoring_reporting,
        onboarding_operations,
    ],
)
def test_flattened_facades_keep_legacy_modules_importable(facade):
    for module_name in facade.available_modules():
        assert facade.load_compat_module(module_name) is importlib.import_module(module_name)
        assert facade.public_symbols(module_name)


@pytest.mark.parametrize(
    "facade,module_name,symbol_name",
    [
        (platform_services, "storage_utils", "atomic_write_json"),
        (platform_services, "app_logging", "write_crash_report"),
        (interview_runtime, "interview_app.session_context", "InterviewSessionContext"),
        (interview_runtime, "interview_app.transcript_summary", "summarize_transcript"),
        (scoring_reporting, "trait_scoring_adapter", "build_trait_scoring_payload"),
    ],
)
def test_flattened_facades_expose_app_entrypoint_symbols(facade, module_name, symbol_name):
    legacy_module = importlib.import_module(module_name)

    assert symbol_name in facade.public_symbols(module_name)
    assert getattr(facade, symbol_name) is getattr(legacy_module, symbol_name)
    assert symbol_name in dir(facade)
