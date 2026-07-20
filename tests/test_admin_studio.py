import json
from pathlib import Path

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
    rubric_path.write_text(json.dumps(_rubric()), encoding="utf-8")
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {},
                "track_question_flow": {"preschool": [{"type": "trait", "id": "trait_1"}]},
            }
        ),
        encoding="utf-8",
    )
    school_settings_path.write_text(json.dumps({}), encoding="utf-8")
    return AdminStudioPaths(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        school_settings_path=school_settings_path,
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


def test_admin_studio_draft_duplicates_trait_without_writing_until_apply(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()

    new_id = draft.duplicate_trait("trait_1")

    assert new_id == "trait_2"
    assert draft.is_dirty is True
    assert [trait["id"] for trait in draft.rubric["traits"]] == ["trait_1", "trait_2"]
    assert draft.rubric["traits"][1]["name"] == "Empathy Copy"
    assert draft.rubric["traits"][1]["descriptors"]["5"] == "great"
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["traits"][0]["id"] == "trait_1"


def test_admin_studio_draft_deletes_trait_without_writing_until_apply(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    new_id = draft.duplicate_trait("trait_1")

    draft.delete_trait(new_id)

    assert [trait["id"] for trait in draft.rubric["traits"]] == ["trait_1"]
    assert draft.is_dirty is False
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["traits"][0]["id"] == "trait_1"


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
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
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


def test_admin_studio_draft_adds_track_without_writing_until_apply(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()

    draft.add_track("infant_toddler", "Infant/Toddler", "Infant and toddler interview flow.", active=True)

    summary = draft.change_summary()
    assert draft.rubric["tracks"]["infant_toddler"] == {
        "label": "Infant/Toddler",
        "description": "Infant and toddler interview flow.",
        "active": True,
    }
    assert draft.overrides["track_question_flow"]["infant_toddler"] == []
    assert summary.changed_files == ["rubric.json", "question_overrides.json"]
    assert "Track added: Infant/Toddler (infant_toddler)" in summary.lines
    assert "infant_toddler" not in json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["tracks"]

    result = studio.apply_draft(draft, confirm=True)

    assert result.applied is True
    assert json.loads(studio.paths.rubric_path.read_text(encoding="utf-8"))["tracks"]["infant_toddler"]["label"] == "Infant/Toddler"
    assert json.loads(studio.paths.overrides_path.read_text(encoding="utf-8"))["track_question_flow"]["infant_toddler"] == []


def test_admin_studio_draft_adds_custom_question_to_track_flow(tmp_path: Path) -> None:
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

    draft.add_custom_question(
        "preschool",
        "classroom_scenario",
        "Classroom Scenario",
        "How would you respond during a classroom transition?",
        section="Qualification",
        position=2,
    )

    assert draft.overrides["custom_questions"]["preschool"][1] == {
        "id": "classroom_scenario",
        "label": "Classroom Scenario",
        "text": "How would you respond during a classroom transition?",
        "order": 2,
        "section": "Qualification",
    }
    assert draft.overrides["track_question_flow"]["preschool"][1] == {"type": "custom", "id": "classroom_scenario"}
    assert "Question flow or custom question settings changed." in draft.change_summary().lines


def test_admin_studio_draft_reorders_question_flow_without_changing_ids(tmp_path: Path) -> None:
    paths = _write_admin_files(tmp_path)
    paths.overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {"preschool": [{"id": "Why-LPL", "text": "Why LPL?", "order": 1}]},
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    studio = AdminStudio.load(paths)
    draft = studio.create_draft()

    draft.move_question("preschool", 0, 1)

    assert draft.overrides["track_question_flow"]["preschool"] == [
        {"type": "trait", "id": "trait_1"},
        {"type": "custom", "id": "Why-LPL"},
    ]
    assert draft.overrides["custom_questions"]["preschool"][0]["id"] == "Why-LPL"
    assert draft.change_summary().changed_files == ["question_overrides.json"]
    assert "Question flow or custom question settings changed." in draft.change_summary().lines


def test_admin_studio_draft_deletes_custom_question_from_flow_and_overrides(tmp_path: Path) -> None:
    paths = _write_admin_files(tmp_path)
    paths.overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {
                    "preschool": [
                        {"id": "Why-LPL", "text": "Why LPL?", "order": 1},
                        {"id": "Classroom", "text": "What would you do?", "order": 2},
                    ]
                },
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "custom", "id": "Classroom"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    studio = AdminStudio.load(paths)
    draft = studio.create_draft()

    draft.delete_question("preschool", "custom", "Why-LPL")

    assert draft.overrides["track_question_flow"]["preschool"] == [
        {"type": "custom", "id": "Classroom"},
        {"type": "trait", "id": "trait_1"},
    ]
    assert draft.overrides["custom_questions"]["preschool"] == [
        {"id": "Classroom", "text": "What would you do?", "order": 1}
    ]
    assert draft.change_summary().changed_files == ["question_overrides.json"]


def test_admin_studio_draft_duplicates_custom_question_after_source(tmp_path: Path) -> None:
    paths = _write_admin_files(tmp_path)
    paths.overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {"preschool": [{"id": "Why-LPL", "text": "Why LPL?", "order": 1}]},
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    studio = AdminStudio.load(paths)
    draft = studio.create_draft()

    new_id = draft.duplicate_question("preschool", "custom", "Why-LPL")

    assert new_id == "Why-LPL-copy"
    assert draft.overrides["track_question_flow"]["preschool"] == [
        {"type": "custom", "id": "Why-LPL"},
        {"type": "custom", "id": "Why-LPL-copy"},
        {"type": "trait", "id": "trait_1"},
    ]
    assert draft.overrides["custom_questions"]["preschool"] == [
        {"id": "Why-LPL", "text": "Why LPL?", "order": 1},
        {"id": "Why-LPL-copy", "label": "Why-LPL Copy", "text": "Why LPL?", "order": 2, "section": "Qualification"},
    ]
    assert draft.change_summary().changed_files == ["question_overrides.json"]


def test_admin_studio_validation_flags_trait_missing_from_question_flow(tmp_path: Path) -> None:
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

    assert "Rubric trait 'trait_1' is missing a linked question in Questions & Flow." in draft.validate()


def test_admin_studio_validation_blocks_invalid_paths_and_writes_nothing(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_school_settings("Palmdale", {"interview_notes_dir": r"..\Candidates"})

    result = studio.apply_draft(draft, confirm=True)

    assert result.applied is False
    assert "Interview notes folder cannot contain '..'." in result.validation_errors
    assert not studio.paths.backup_dir.exists()
    assert json.loads(studio.paths.school_settings_path.read_text(encoding="utf-8")) == {}


def test_admin_studio_validation_blocks_unsafe_offer_paths(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_school_settings("Palmdale", {"offer_output_dir": r"..\Employment Offers"})

    result = studio.apply_draft(draft, confirm=True)

    assert result.applied is False
    assert "Offer paths cannot contain '..'." in result.validation_errors
    assert json.loads(studio.paths.school_settings_path.read_text(encoding="utf-8")) == {}


def test_admin_studio_discard_restores_clean_draft(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))
    draft = studio.create_draft()
    draft.update_trait("trait_1", {"name": "Changed"})

    clean = draft.discard()

    assert clean.is_dirty is False
    assert clean.change_summary().lines == []
    assert clean.rubric["traits"][0]["name"] == "Empathy"


def test_admin_studio_summary_groups_sections_for_staffing_settings_navigation(tmp_path: Path) -> None:
    studio = AdminStudio.load(_write_admin_files(tmp_path))

    summary = studio.summary()

    groups = [(section.group, section.title) for section in summary.sections]
    assert groups == [
        ("Interview", "Interview Flow"),
        ("Interview", "Rubrics"),
        ("Operations", "Templates & Folders"),
        ("Services", "Shared Email Account"),
    ]
