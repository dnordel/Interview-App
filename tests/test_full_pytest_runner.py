from __future__ import annotations

from tools import full_pytest_runner


def test_full_suite_updates_source_version_before_metadata_preflight() -> None:
    launched: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return 6

    exit_code = full_pytest_runner.run_full_suite(python_executable="python.exe", call=fake_call)

    assert exit_code == 6
    assert launched == [["python.exe", "tools/update_source_version.py"]]


def test_gui_timing_batches_sort_longest_first_and_limit_worker_reuse() -> None:
    entries = [
        {"nodeid": "gui-short", "duration_seconds_n2": 1.0, "gui_heavy": True},
        {"nodeid": "not-gui", "duration_seconds_n2": 99.0, "gui_heavy": False},
        {"nodeid": "gui-long", "duration_seconds_n2": 9.0, "gui_heavy": True},
        {"nodeid": "gui-medium", "duration_seconds_n2": 4.0, "gui_heavy": True},
    ]

    batches = full_pytest_runner.build_gui_test_batches(entries=entries, gui_workers=2)

    assert batches == [["gui-long", "gui-medium"], ["gui-short"]]


def test_full_suite_runs_gui_timing_preflight_before_combined_full_suite() -> None:
    quick_command, non_gui_command, gui_commands = full_pytest_runner.build_full_suite_commands(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        gui_workers=24,
        catalog_entries=[
            {"nodeid": "tests/gui_slow.py::test_slow", "duration_seconds_n2": 5.0, "gui_heavy": True},
            {"nodeid": "tests/gui_fast.py::test_fast", "duration_seconds_n2": 1.0, "gui_heavy": True},
        ],
    )

    assert quick_command == ["python.exe", "-m", "pytest", "-q", "tests/test_pytest_duration_catalog.py"]
    assert non_gui_command[:8] == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "-n",
        "24",
        "--dist=load",
        "--maxschedchunk=1",
    ]
    assert ["-m", "not slow_pyside"] == non_gui_command[8:10]
    assert "--ignore=tests/test_pytest_duration_catalog.py" in non_gui_command
    assert len(gui_commands) == 1
    assert gui_commands[0][:8] == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "-n",
        "2",
        "--dist=load",
        "--maxschedchunk=1",
    ]
    assert gui_commands[0][8:] == ["tests/gui_slow.py::test_slow", "tests/gui_fast.py::test_fast"]


def test_full_suite_returns_combined_full_suite_failure_after_preflight_passes() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 0, 3])

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return next(exit_codes)

    exit_code = full_pytest_runner.run_full_suite(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        call=fake_call,
    )

    assert exit_code == 3
    assert len(launched) == 3
    assert launched[2][launched[2].index("-n") + 1] == "24"
    assert launched[2][-3:-1] == ["-m", "not slow_pyside"]


def test_full_suite_stops_after_gui_timing_preflight_failures() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 7])

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return next(exit_codes)

    exit_code = full_pytest_runner.run_full_suite(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        call=fake_call,
    )

    assert exit_code == 7
    assert len(launched) == 2
    assert "tests/test_pytest_duration_catalog.py" in launched[1]


def test_full_suite_starts_combined_full_phase_after_preflight_passes() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 0, 0, 5])

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return next(exit_codes)

    exit_code = full_pytest_runner.run_full_suite(call=fake_call)

    assert exit_code == 5
    assert len(launched) == 4
    assert launched[2][launched[2].index("-n") + 1] == "24"
    assert launched[3][launched[3].index("-n") + 1] == "24"
    assert any("test_" in argument for argument in launched[3][8:])
