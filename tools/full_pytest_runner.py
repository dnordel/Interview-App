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

GUI_TIMING_PREFLIGHT_PATH = "tests/test_pytest_duration_catalog.py"
GUI_ACTION_PREFLIGHT_PATH = "tests/test_gui_action_behavior_coverage.py"
METADATA_PREFLIGHT_PATHS = (GUI_TIMING_PREFLIGHT_PATH, GUI_ACTION_PREFLIGHT_PATH)
SOURCE_VERSION_PATH = ROOT / "config" / "source_version.txt"


def build_gui_test_batches(*, entries: list[dict[str, Any]], gui_workers: int = 2) -> list[list[str]]:
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
    gui_workers: int = 2,
    catalog_entries: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], list[list[str]]]:
    common = ["--dist=load", "--maxschedchunk=1"]
    quick_metadata_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        *METADATA_PREFLIGHT_PATHS,
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
        "not pyside_gui and not slow_pyside",
        *(f"--ignore={path}" for path in METADATA_PREFLIGHT_PATHS),
    ]
    _ = catalog_entries
    gui_commands = [
        [
            python_executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            str(gui_workers),
            *common,
            "-m",
            "pyside_gui",
        ]
    ]
    return quick_metadata_command, non_gui_command, gui_commands


def run_full_suite(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
    gui_workers: int = 2,
    call: Callable[[list[str]], int] = subprocess.call,
    source_version_path: Path = SOURCE_VERSION_PATH,
) -> int:
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
    version_path = Path(source_version_path)
    prior_stamp = version_path.read_bytes() if version_path.is_file() else None

    def restore_prior_stamp() -> None:
        if prior_stamp is None:
            version_path.unlink(missing_ok=True)
            return
        version_path.parent.mkdir(parents=True, exist_ok=True)
        version_path.write_bytes(prior_stamp)

    provisional_command = [python_executable, "tools/update_source_version.py"]
    print("[source version preflight] writing reversible test stamp", flush=True)
    provisional_exit_code = call(provisional_command)
    if provisional_exit_code:
        restore_prior_stamp()
        return provisional_exit_code
    print(f"[full suite] non-GUI phase with {full_workers} workers", flush=True)
    non_gui_exit_code = call(non_gui_command)
    if non_gui_exit_code:
        restore_prior_stamp()
        print("[full suite] non-GUI phase FAILED; GUI phase not started", flush=True)
        return non_gui_exit_code
    for gui_command in gui_commands:
        print(f"[full suite] GUI phase with {gui_workers} persistent workers", flush=True)
        gui_exit_code = call(gui_command)
        if gui_exit_code:
            restore_prior_stamp()
            return gui_exit_code
    print("[source version finalization] updating deployment stamp", flush=True)
    return call([python_executable, "tools/update_source_version.py"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run metadata preflight, parallel non-GUI tests, then one isolated two-worker GUI phase."
    )
    parser.add_argument("--metadata-workers", type=int, default=8)
    parser.add_argument("--full-workers", type=int, default=24)
    parser.add_argument("--gui-workers", type=int, default=2)
    args = parser.parse_args()
    raise SystemExit(
        run_full_suite(
            metadata_workers=args.metadata_workers,
            full_workers=args.full_workers,
            gui_workers=args.gui_workers,
        )
    )
