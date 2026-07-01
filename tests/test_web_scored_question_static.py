from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web" / "scored-question"


def test_scored_question_web_files_exist():
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "styles.css").is_file()
    assert (WEB_DIR / "app.js").is_file()
    assert (WEB_DIR / "README.md").is_file()


def test_scored_question_web_loads_existing_rubric_and_trait_signal_files():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "../../config/rubric.json" in app_js
    assert "../../Trait-Based%20Scoring/preschool_teacher_interview_signals_weighted.json" in app_js
    assert "state.weightedSignals?.traits" in app_js
    assert "item.trait_id" in app_js
    assert "trait_aliases" in app_js
    assert "signalFiles" not in app_js


def test_scored_question_web_uses_existing_trait_input_field_names():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    for field_name in (
        "trait_inputs",
        "raw_score",
        "absolute_disqualifier",
        "no_example_after_followups",
        "selected_signal_ids",
        "question_notes",
        "trait_notes",
        "verbatim_notes",
    ):
        assert field_name in app_js


def test_scored_question_web_does_not_silently_persist_candidate_text():
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "localStorage" not in app_js
    assert "sessionStorage" not in app_js


def test_scored_question_web_css_covers_keyboard_focus_and_mobile_layout():
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "@media (max-width: 1040px)" in css
    assert "grid-template-columns: 1fr" in css


def test_scored_question_web_contracts_are_registered():
    contract = yaml.safe_load((ROOT / "contracts" / "web_scored_question.contract.yaml").read_text(encoding="utf-8"))
    system = yaml.safe_load((ROOT / "contracts" / "system.contract.yaml").read_text(encoding="utf-8"))
    architecture = yaml.safe_load((ROOT / "contracts" / "architecture.contract.yaml").read_text(encoding="utf-8"))

    assert contract["module"]["name"] == "web_scored_question"
    assert system["modules"]["web_scored_question"]["path"] == "web/scored-question/"
    assert system["modules"]["config_rubric_json"]["path"] == "config/rubric.json"
    assert (
        system["modules"]["trait_based_scoring_json"]["path"]
        == "Trait-Based Scoring/preschool_teacher_interview_signals_weighted.json"
    )
    modules = {
        module_name
        for service in architecture["services"].values()
        for module_name in service.get("modules", [])
    }
    assert "web_scored_question" in modules
