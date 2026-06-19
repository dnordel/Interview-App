from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web" / "app"


def test_web_app_files_exist():
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "styles.css").is_file()
    assert (WEB_DIR / "app.js").is_file()
    assert (WEB_DIR / "data.js").is_file()
    assert (WEB_DIR / "README.md").is_file()


def test_web_app_covers_primary_screen_routes():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    for route in (
        '"start"',
        '"candidate"',
        '"interview"',
        '"review"',
        '"onboarding"',
        '"settings"',
        '"questions"',
        '"history"',
    ):
        assert route in app_js


def test_web_app_loads_existing_local_configuration_sources():
    data_js = (WEB_DIR / "data.js").read_text(encoding="utf-8")

    for source_path in (
        "../../config/rubric.json",
        "../../config/question_overrides.json",
        "../../interview_history.json",
        "../../school_offer_settings.json",
        "../../Trait-Based%20Scoring/preschool_teacher_interview_signals_weighted.json",
    ):
        assert source_path in data_js


def test_web_app_uses_data_adapter_for_domain_operations():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    data_js = (WEB_DIR / "data.js").read_text(encoding="utf-8")

    for exported_name in (
        "createInitialState",
        "loadApplicationData",
        "loadSignalDefinition",
        "finalizeWebDraftToBackend",
        "uploadWebRecordingToBackend",
        "loadDraftsFromBackend",
        "loadWebDraftFromBackend",
        "applyDraftPayload",
        "saveQuestionOverridesToBackend",
        "saveOfferSettingsToBackend",
        "updateHistoryOfferStatusToBackend",
        "scoreWebDraftPreview",
        "flowForTrack",
        "createTraitResponse",
        "createCustomResponse",
        "buildWebDraftPayload",
        "saveWebDraftToBackend",
    ):
        assert exported_name in app_js
        assert f"export function {exported_name}" in data_js or f"export async function {exported_name}" in data_js

    assert "const signalFiles" not in app_js
    assert "signalFiles" not in data_js
    assert "async function loadJson" not in app_js
    assert "/api/bootstrap" in data_js
    assert "/api/drafts" in data_js
    assert "/api/question-overrides" in data_js
    assert "/api/offer-settings" in data_js
    assert "/api/history/" in data_js
    assert "/api/score-preview" in data_js
    assert "/api/finalize" in data_js
    assert "/api/recordings" in data_js
    assert "data-draft-name" in app_js
    assert "resumeDraft" in app_js
    assert "saveQuestionOverrides" in app_js
    assert "traitQuestionText" in app_js
    assert "saveOfferSettings" in app_js
    assert "data-offer-field" in app_js
    assert "data-history-offer" in app_js
    assert "updateHistoryOfferStatus" in app_js
    assert "renderReview" in app_js
    assert "Score preview" in app_js
    assert "Finalize report" in app_js
    assert "Integration export" in app_js
    assert "Director referral packet" in app_js
    assert "MediaRecorder" in app_js
    assert "Start recording" in app_js
    assert "Stop and save" in app_js


def test_web_app_preserves_existing_trait_state_field_names():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    for field_name in (
        "raw_score",
        "absolute_disqualifier",
        "no_example_after_followups",
        "selected_signal_ids",
        "question_notes",
        "trait_notes",
        "verbatim_notes",
    ):
        assert field_name in app_js


def test_web_app_does_not_use_browser_storage_for_candidate_text():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    data_js = (WEB_DIR / "data.js").read_text(encoding="utf-8")

    assert "localStorage" not in app_js + data_js
    assert "sessionStorage" not in app_js + data_js


def test_web_app_readme_keeps_tk_as_main_entry_point():
    readme = (WEB_DIR / "README.md").read_text(encoding="utf-8")

    assert "Tk desktop app remains the main point of entry." in readme
    assert "No launch scripts are changed" in readme
    assert "score preview" in readme
    assert "generate a DOCX interview report" in readme
    assert "JSON integration export" in readme
    assert "director referral packet preview" in readme
    assert "browser audio recordings" in readme
    assert "audio transcription remain desktop-only" in readme


def test_web_app_css_covers_focus_and_responsive_stacked_layout():
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "@media (max-width: 1040px)" in css
    assert "grid-template-columns: 1fr" in css


def test_web_app_contracts_are_registered():
    contract = yaml.safe_load((ROOT / "contracts" / "web_app.contract.yaml").read_text(encoding="utf-8"))
    system = yaml.safe_load((ROOT / "contracts" / "system.contract.yaml").read_text(encoding="utf-8"))
    architecture = yaml.safe_load((ROOT / "contracts" / "architecture.contract.yaml").read_text(encoding="utf-8"))

    assert contract["module"]["name"] == "web_app"
    assert contract["module"]["version"] == "0.11.0"
    assert {item["name"] for item in contract["functions"]} >= {
        "createInitialState",
        "loadApplicationData",
        "loadSignalDefinition",
        "loadDraftsFromBackend",
        "loadWebDraftFromBackend",
        "applyDraftPayload",
        "saveQuestionOverridesToBackend",
        "saveOfferSettingsToBackend",
        "updateHistoryOfferStatusToBackend",
        "scoreWebDraftPreview",
        "finalizeWebDraftToBackend",
        "uploadWebRecordingToBackend",
        "buildWebDraftPayload",
    }
    assert system["modules"]["web_app"]["path"] == "web/app/"
    assert "question_overrides_json" in system["modules"]["web_app"]["depends_on"]
    assert "interview_history_json" in system["modules"]["web_app"]["depends_on"]
    assert "school_offer_settings_json" in system["modules"]["web_app"]["depends_on"]
    assert "web_app_backend" in system["modules"]
    modules = {
        module_name
        for service in architecture["services"].values()
        for module_name in service.get("modules", [])
    }
    assert "web_app" in modules
