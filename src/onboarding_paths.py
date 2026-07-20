from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from typing import Iterable
from uuid import uuid4


@dataclass(frozen=True)
class OnboardingPaths:
    """Portable paths for shared onboarding replicas and encrypted artifacts."""

    user_artifacts: Path
    root: Path

    @classmethod
    def from_user_artifacts(cls, user_artifacts: Path) -> "OnboardingPaths":
        base = Path(user_artifacts).resolve()
        return cls(user_artifacts=base, root=base / "onboarding")

    @property
    def admin_replica(self) -> Path:
        return self.root / "admin.sqlite3"

    def director_replica(self, school: str) -> Path:
        return self.root / "directors" / f"{_school_slug(school)}.sqlite3"

    @property
    def change_stage(self) -> Path:
        return self.root / "changes"

    @property
    def keyring(self) -> Path:
        return self.root / "keyring.json"

    @property
    def encrypted_files(self) -> Path:
        return self.root / "vault"


def migrate_legacy_onboarding_artifacts(
    paths: OnboardingPaths,
    *,
    schools: Iterable[str] = (),
) -> tuple[Path, ...]:
    """Copy legacy shared files into v2 layout without deleting or overwriting."""

    candidates = [(paths.user_artifacts / "onboarding_admin.sqlite3", paths.admin_replica)]
    candidates.extend(
        (
            paths.user_artifacts / f"onboarding_{_school_slug(school)}.sqlite3",
            paths.director_replica(school),
        )
        for school in schools
    )
    candidates.append((paths.user_artifacts / "onboarding.keyring.json", paths.keyring))
    migrated: list[Path] = []
    for source, destination in candidates:
        if not source.is_file() or destination.exists():
            continue
        _copy_file_atomic(source, destination, allowed_root=paths.root)
        migrated.append(destination)
    return tuple(migrated)


def _school_slug(school: str) -> str:
    clean = str(school or "").strip()
    if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise ValueError("School name must be a non-path value.")
    slug = re.sub(r"[^a-z0-9]+", "_", clean.casefold()).strip("_")
    if not slug:
        raise ValueError("School name is required.")
    return slug


def _copy_file_atomic(source: Path, destination: Path, *, allowed_root: Path) -> None:
    root = Path(allowed_root).resolve()
    target = Path(destination).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Onboarding migration destination escapes shared root.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
