import json
from pathlib import Path

import pytest

from admin_studio import AdminStudio, AdminStudioPaths


def _rubric() -> dict:
    return {
        "tracks": {"preschool": {"label": "Preschool"}},
        "traits": [
            {
                "id": "trait_1",
                "name": "Empathy",
                "priority": "critical",
                "weight": 3,
                "applicable_tracks": ["preschool"],
                "primary_question": "How do you comfort a child?",
                "descriptors": {"1": "poor", "2": "weak", "3": "ok", "4": "good", "5": "great"},
                "sample_answers": {"1": "", "2": "", "3": "", "4": "", "5": ""},
            }
        ],
    }


def _write_admin_files(tmp_path: Path) -> AdminStudioPaths:
    rubric_path = tmp_path / "rubric.json"
    overrides_path = tmp_path / "question_overrides.json"
    school_settings_path = tmp_path / "school_offer_settings.json"
    prompts_path = tmp_path / "deepseek_prompts.json"
    rubric_path.write_text(json.dumps(_rubric()), encoding="utf-8")
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {},
                "track_question_flow": {},
            }
        ),
        encoding="utf-8",
    )
    school_settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize answers."}), encoding="utf-8")
    return AdminStudioPaths(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        school_settings_path=school_settings_path,
        prompts_path=prompts_path,
        backup_dir=tmp_path / "backups",
    )


def test_admin_studio_draft_tracks_trait_changes_without_writing_until_apply(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()

    draft.update_trait("trait_1", {"name": "Empathy and warmth"})

    summary = draft.change_summary()
    assert draft.is_dirty is True
    assert summary.changed_files == ["rubric.json"]
    assert "trait_1 name: Empathy -> Empathy and warmth" in summary.lines
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["traits"][0]["name"] == "Empathy"

    result = studio.apply_draft(draft, confirm=False)

    assert result.applied is False
    assert result.changed_files == []
    assert not studio.paths.backup_dir.exists()
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["traits"][0]["name"] == "Empathy"


def test_admin_studio_apply_creates_backup_and_writes_confirmed_changes(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_trait("trait_1", {"name": "Empathy and warmth"})

    result = studio.apply_draft(draft, confirm=True)

    assert result.applied is True
    assert result.changed_files == ["rubric.json"]
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["traits"][0]["name"] == "Empathy and warmth"
    backups = list(studio.paths.backup_dir.glob("rubric.*.bak.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["traits"][0]["name"] == "Empathy"


def test_admin_studio_updates_question_text_through_overrides(tmp_path: Path) -> None:
    paths = _write_admin_files(tmp_path)
    paths.overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {"preschool": [{"id": "Why-LPL", "text": "Why LPL?", "order": 1}]},
                "track_question_flow": {"preschool": [{"type": "custom", "id": "Why-LPL"}]},
            }
        ),
        encoding="utf-8",
    )
    studio = AdminStudio.load(paths)
    draft = studio.create_draft()

    draft.update_question_text("preschool", "custom", "Why-LPL", "Why do you want to work here?")
    draft.update_question_text("preschool", "trait", "trait_1", "How would you comfort a child?")
    result = studio.apply_draft(draft, confirm=True)

    saved = json.loads(paths.overrides_path.read_text(encoding="utf-8"))
    assert result.applied is True
    assert saved["custom_questions"]["preschool"][0]["text"] == "Why do you want to work here?"
    assert saved["trait_question_overrides"]["trait_1"] == "How would you comfort a child?"


def test_admin_studio_validation_blocks_invalid_paths_and_writes_nothing(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_school_settings("Palmdale", {"interview_notes_dir": r"..\Candidates"})

    result = studio.apply_draft(draft, confirm=True)

    assert result.applied is False
    assert "Interview notes folder cannot contain '..'." in result.validation_errors
    assert not studio.paths.backup_dir.exists()
    assert json.loads(studio.paths.school_settings_path.read_text(encoding="utf-8")) == {}


def test_admin_studio_discard_restores_clean_draft(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_prompt("answer_summary_user", "New prompt")

    clean = draft.discard()

    assert clean.is_dirty is False
    assert clean.change_summary().lines == []
    assert clean.prompts["answer_summary_user"] == "Summarize answers."
