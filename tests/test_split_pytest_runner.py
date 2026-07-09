from __future__ import annotations

import sys

from tools import split_pytest_runner


class _FakeProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.communicated = False

    def communicate(self) -> None:
        self.communicated = True


def test_build_split_pytest_commands_allocates_gui_and_non_gui_workers() -> None:
    commands = split_pytest_runner.build_split_pytest_commands(
        python_executable="python.exe",
        gui_workers=8,
        other_workers=12,
    )

    assert commands == [
        [
            "python.exe",
            "-m",
            "pytest",
            "-m",
            "slow_pyside",
            "-n",
            "8",
        ],
        [
            "python.exe",
            "-m",
            "pytest",
            "-m",
            "not slow_pyside",
            "-n",
            "12",
        ],
    ]


def test_run_split_pytest_launches_both_groups_before_waiting() -> None:
    launched: list[list[str]] = []
    processes = [_FakeProcess(0), _FakeProcess(1)]

    def fake_popen(command: list[str]) -> _FakeProcess:
        launched.append(command)
        return processes[len(launched) - 1]

    exit_code = split_pytest_runner.run_split_pytest(
        python_executable=sys.executable,
        gui_workers=8,
        other_workers=12,
        popen=fake_popen,
    )

    assert len(launched) == 2
    assert all(process.communicated for process in processes)
    assert exit_code == 1
