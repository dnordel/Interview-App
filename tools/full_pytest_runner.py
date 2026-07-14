from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable


GUI_TIMING_PREFLIGHT_TESTS = (
    "tests/test_pytest_duration_catalog.py::test_duration_catalog_covers_collected_tests",
    "tests/test_pytest_duration_catalog.py::test_gui_scenario_catalog_entries_have_measured_scheduler_durations",
    "tests/test_pytest_duration_catalog.py::test_marked_pyside_gui_tests_are_scenarios_or_focused_exceptions",
    "tests/test_pytest_duration_catalog.py::test_required_pyside_gui_surfaces_have_named_scenarios",
)


def build_full_suite_commands(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
) -> tuple[list[str], list[str]]:
    common = ["--dist=load", "--maxschedchunk=1"]
    quick_metadata_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        *GUI_TIMING_PREFLIGHT_TESTS,
    ]
    full_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        "-n",
        str(full_workers),
        *common,
        *(f"--deselect={nodeid}" for nodeid in GUI_TIMING_PREFLIGHT_TESTS),
    ]
    return quick_metadata_command, full_command


def run_full_suite(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
    call: Callable[[list[str]], int] = subprocess.call,
) -> int:
    quick_metadata_command, full_command = build_full_suite_commands(
        python_executable=python_executable,
        metadata_workers=metadata_workers,
        full_workers=full_workers,
    )
    print("[gui timing preflight] running duration/scenario metadata checks", flush=True)
    quick_exit_code = call(quick_metadata_command)
    if quick_exit_code:
        print("[gui timing preflight] FAILED; full suite not started", flush=True)
        return quick_exit_code
    print("[gui timing preflight] passed", flush=True)
    print(f"[full suite] starting with {full_workers} workers", flush=True)
    return call(full_command)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run GUI timing/scenario preflight first, then run the full parallel suite with those preflight nodes deselected."
    )
    parser.add_argument("--metadata-workers", type=int, default=8)
    parser.add_argument("--full-workers", type=int, default=24)
    args = parser.parse_args()
    raise SystemExit(
        run_full_suite(
            metadata_workers=args.metadata_workers,
            full_workers=args.full_workers,
        )
    )
