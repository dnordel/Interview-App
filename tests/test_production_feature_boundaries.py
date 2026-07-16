from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).relative_to(REPO_ROOT).as_posix()
PROHIBITED_PATTERNS = (
    "deep" + "seek",
    "oll" + "ama",
    "artificial" + r"\s+" + "intelligence",
    r"\b" + "a" + "i" + r"\b",
    r"\b" + "l" + "lm" + r"\b",
    "open" + "ai",
    "machine" + r"\s+" + "learning",
    r"\b" + "m" + "l" + r"\b",
    "interview" + r"[_-]" + "m" + "l",
    "m" + "l" + "dataset",
    "model" + r"[_-]" + "signal",
    "model" + r"[_-]" + "trait",
)


def _tracked_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8")
        if relative == THIS_FILE:
            continue
        path = REPO_ROOT / relative
        if path.is_file() and b"\0" not in path.read_bytes()[:8192]:
            paths.append(path)
    return paths


def test_production_tree_excludes_model_generated_features_and_mentions() -> None:
    prohibited = re.compile("|".join(PROHIBITED_PATTERNS), re.IGNORECASE)
    violations: list[str] = []

    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if prohibited.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{line_number}")

    assert violations == []
