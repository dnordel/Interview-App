"""Pytest configuration for dependency sanity checks."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


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


def pytest_configure() -> None:
    """Validate that the correct DOCX library is importable before test collection."""

    try:
        importlib.import_module("docx")
    except ModuleNotFoundError as exc:
        if exc.name == "exceptions":
            pytest.exit(_DOCX_IMPORT_HELP, returncode=2)
        raise


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """Keep xdist defaulted on, but avoid worker startup for one explicit test."""

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
