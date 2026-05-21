from pathlib import Path

from data_store import DisqualifierSignalLibrary, QuestionOverridesStore, RubricLoader


def test_disqualifier_library_uses_safe_default_when_invalid(tmp_path: Path):
    path = tmp_path / "disqualifier_signals.json"
    path.write_text('{"questions": [', encoding="utf-8")

    library = DisqualifierSignalLibrary(path)

    assert library.data == {"questions": []}
    assert library.get_for_trait("trait_1") is None


def test_question_overrides_invalid_shape_archives_and_resets(tmp_path: Path):
    path = tmp_path / "question_overrides.json"
    path.write_text('[]', encoding="utf-8")

    store = QuestionOverridesStore(path)

    assert store.data == {
        "track_trait_order": {},
        "trait_question_overrides": {},
        "custom_questions": {},
        "track_question_flow": {},
    }
    assert not path.exists()
    assert list(tmp_path.glob("question_overrides.corrupt-*.json"))


def test_rubric_loader_rejects_missing_required_fields_without_payload_echo(tmp_path: Path):
    path = tmp_path / "rubric.json"
    path.write_text('{"metadata": {}}', encoding="utf-8")

    try:
        RubricLoader(path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError")

    assert "missing required key" in message
    assert "{\"metadata\"" not in message
