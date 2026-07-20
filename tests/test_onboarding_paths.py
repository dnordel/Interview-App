from pathlib import Path

from onboarding_paths import OnboardingPaths, migrate_legacy_onboarding_artifacts


def test_portable_paths_are_scoped_under_shared_onboarding_root(tmp_path: Path) -> None:
    paths = OnboardingPaths.from_user_artifacts(tmp_path)

    assert paths.root == tmp_path / "onboarding"
    assert paths.admin_replica == paths.root / "admin.sqlite3"
    assert paths.director_replica(" Palmdale ") == paths.root / "directors" / "palmdale.sqlite3"
    assert paths.change_stage == paths.root / "changes"
    assert paths.keyring == paths.root / "keyring.json"


def test_legacy_artifacts_migrate_additively_without_overwriting_destination(tmp_path: Path) -> None:
    (tmp_path / "onboarding_admin.sqlite3").write_bytes(b"legacy-admin")
    (tmp_path / "onboarding_palmdale.sqlite3").write_bytes(b"legacy-director")
    (tmp_path / "onboarding.keyring.json").write_text("legacy-keyring", encoding="utf-8")
    paths = OnboardingPaths.from_user_artifacts(tmp_path)
    paths.root.mkdir(parents=True)
    paths.admin_replica.write_bytes(b"current-admin")

    migrated = migrate_legacy_onboarding_artifacts(paths, schools=("Palmdale",))

    assert migrated == (paths.director_replica("Palmdale"), paths.keyring)
    assert paths.admin_replica.read_bytes() == b"current-admin"
    assert paths.director_replica("Palmdale").read_bytes() == b"legacy-director"
    assert paths.keyring.read_text(encoding="utf-8") == "legacy-keyring"
    assert (tmp_path / "onboarding_admin.sqlite3").exists()


def test_school_slug_rejects_missing_or_path_like_school(tmp_path: Path) -> None:
    paths = OnboardingPaths.from_user_artifacts(tmp_path)

    for school in ("", "..", "Palmdale/../../outside"):
        try:
            paths.director_replica(school)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected unsafe school to fail: {school!r}")
