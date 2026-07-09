from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence


DEFAULT_GUI_WORKERS = 8
DEFAULT_OTHER_WORKERS = 12


def build_split_pytest_commands(
    *,
    python_executable: str = sys.executable,
    gui_workers: int = DEFAULT_GUI_WORKERS,
    other_workers: int = DEFAULT_OTHER_WORKERS,
    extra_pytest_args: Sequence[str] = (),
) -> list[list[str]]:
    """Build the two pytest commands used by the split full-suite runner."""

    extra_args = list(extra_pytest_args)
    return [
        [
            python_executable,
            "-m",
            "pytest",
            "-m",
            "slow_pyside",
            "-n",
            str(gui_workers),
            *extra_args,
        ],
        [
            python_executable,
            "-m",
            "pytest",
            "-m",
            "not slow_pyside",
            "-n",
            str(other_workers),
            *extra_args,
        ],
    ]


def run_split_pytest(
    *,
    python_executable: str = sys.executable,
    gui_workers: int = DEFAULT_GUI_WORKERS,
    other_workers: int = DEFAULT_OTHER_WORKERS,
    extra_pytest_args: Sequence[str] = (),
    popen: Callable[[list[str]], subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    """Run GUI-heavy and non-GUI pytest groups concurrently."""

    commands = build_split_pytest_commands(
        python_executable=python_executable,
        gui_workers=gui_workers,
        other_workers=other_workers,
        extra_pytest_args=extra_pytest_args,
    )
    processes = [popen(command) for command in commands]
    exit_code = 0
    for process in processes:
        process.communicate()
        if process.returncode:
            exit_code = process.returncode if exit_code == 0 else exit_code
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run slow PySide and non-slow pytest groups concurrently with separate worker budgets."
    )
    parser.add_argument("--gui-workers", type=int, default=DEFAULT_GUI_WORKERS)
    parser.add_argument("--other-workers", type=int, default=DEFAULT_OTHER_WORKERS)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    extra_pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    return run_split_pytest(
        gui_workers=args.gui_workers,
        other_workers=args.other_workers,
        extra_pytest_args=extra_pytest_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
