from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pytest_duration_catalog import load_catalog


GUI_TIMING_PREFLIGHT_PATH = "tests/test_pytest_duration_catalog.py"


def build_gui_test_batches(*, entries: list[dict[str, Any]], gui_workers: int = 24) -> list[list[str]]:
    if gui_workers < 1:
        raise ValueError("GUI worker count must be positive.")
    ordered = sorted(
        (entry for entry in entries if bool(entry.get("gui_heavy", False))),
        key=lambda entry: float(entry.get("duration_seconds_n2", 0.0)),
        reverse=True,
    )
    nodeids = [str(entry["nodeid"]) for entry in ordered]
    return [nodeids[index : index + gui_workers] for index in range(0, len(nodeids), gui_workers)]


def build_full_suite_commands(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
    gui_workers: int = 24,
    catalog_entries: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], list[list[str]]]:
    common = ["--dist=load", "--maxschedchunk=1"]
    quick_metadata_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        GUI_TIMING_PREFLIGHT_PATH,
    ]
    non_gui_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        "-n",
        str(full_workers),
        *common,
        "-m",
        "not slow_pyside",
        f"--ignore={GUI_TIMING_PREFLIGHT_PATH}",
    ]
    entries = catalog_entries if catalog_entries is not None else list(load_catalog()["entries"])
    gui_commands = [
        [
            python_executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            str(min(gui_workers, len(batch))),
            *common,
            *batch,
        ]
        for batch in build_gui_test_batches(entries=entries, gui_workers=gui_workers)
    ]
    return quick_metadata_command, non_gui_command, gui_commands


def run_full_suite(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
    gui_workers: int = 24,
    call: Callable[[list[str]], int] = subprocess.call,
) -> int:
    source_version_command = [python_executable, "tools/update_source_version.py"]
    print("[source version preflight] updating deployment stamp", flush=True)
    source_version_exit_code = call(source_version_command)
    if source_version_exit_code:
        print("[source version preflight] FAILED; tests not started", flush=True)
        return source_version_exit_code
    quick_metadata_command, non_gui_command, gui_commands = build_full_suite_commands(
        python_executable=python_executable,
        metadata_workers=metadata_workers,
        full_workers=full_workers,
        gui_workers=gui_workers,
    )
    print("[gui timing preflight] running duration/scenario metadata checks", flush=True)
    quick_exit_code = call(quick_metadata_command)
    if quick_exit_code:
        print("[gui timing preflight] FAILED; full suite not started", flush=True)
        return quick_exit_code
    print("[gui timing preflight] passed", flush=True)
    print(f"[full suite] non-GUI phase with {full_workers} workers", flush=True)
    non_gui_exit_code = call(non_gui_command)
    if non_gui_exit_code:
        print("[full suite] non-GUI phase FAILED; GUI phase not started", flush=True)
        return non_gui_exit_code
    for index, gui_command in enumerate(gui_commands, start=1):
        print(f"[full suite] GUI batch {index}/{len(gui_commands)} with up to {gui_workers} fresh workers", flush=True)
        gui_exit_code = call(gui_command)
        if gui_exit_code:
            return gui_exit_code
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run metadata preflight, non-GUI tests, then measured longest-first GUI batches with fresh workers."
    )
    parser.add_argument("--metadata-workers", type=int, default=8)
    parser.add_argument("--full-workers", type=int, default=24)
    parser.add_argument("--gui-workers", type=int, default=24)
    args = parser.parse_args()
    raise SystemExit(
        run_full_suite(
            metadata_workers=args.metadata_workers,
            full_workers=args.full_workers,
            gui_workers=args.gui_workers,
        )
    )
