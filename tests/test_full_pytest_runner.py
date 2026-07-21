from __future__ import annotations

from tools import full_pytest_runner


def test_full_suite_does_not_update_source_version_when_metadata_fails() -> None:
    launched: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return 6

    exit_code = full_pytest_runner.run_full_suite(python_executable="python.exe", call=fake_call)

    assert exit_code == 6
    assert "tools/update_source_version.py" not in [item for command in launched for item in command]


def test_legacy_gui_timing_batches_sort_longest_first() -> None:
    entries = [
        {"nodeid": "gui-short", "duration_seconds_n2": 1.0, "gui_heavy": True},
        {"nodeid": "not-gui", "duration_seconds_n2": 99.0, "gui_heavy": False},
        {"nodeid": "gui-long", "duration_seconds_n2": 9.0, "gui_heavy": True},
        {"nodeid": "gui-medium", "duration_seconds_n2": 4.0, "gui_heavy": True},
    ]

    batches = full_pytest_runner.build_gui_test_batches(entries=entries)

    assert batches == [["gui-long", "gui-medium", "gui-short"]]


def test_full_suite_runs_gui_timing_preflight_before_combined_full_suite() -> None:
    quick_command, non_gui_command, gui_commands = full_pytest_runner.build_full_suite_commands(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        catalog_entries=[
            {"nodeid": "tests/gui_slow.py::test_slow", "duration_seconds_n2": 5.0, "gui_heavy": True},
            {"nodeid": "tests/gui_fast.py::test_fast", "duration_seconds_n2": 1.0, "gui_heavy": True},
        ],
    )

    assert quick_command == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "tests/test_pytest_duration_catalog.py",
        "tests/test_gui_action_behavior_coverage.py",
        "tests/test_contract_coverage_matrix.py",
    ]
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
    assert ["-m", "not pyside_gui and not slow_pyside"] == non_gui_command[8:10]
    assert "--ignore=tests/test_pytest_duration_catalog.py" in non_gui_command
    assert "--ignore=tests/test_gui_action_behavior_coverage.py" in non_gui_command
    assert "--ignore=tests/test_contract_coverage_matrix.py" in non_gui_command
    assert len(gui_commands) == 1
    assert gui_commands[0][:8] == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "-n",
        "8",
        "--dist=load",
        "--maxschedchunk=1",
    ]
    assert gui_commands[0][8:] == ["-m", "pyside_gui"]


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
    marker_index = launched[2].index("not pyside_gui and not slow_pyside") - 1
    assert launched[2][marker_index : marker_index + 2] == ["-m", "not pyside_gui and not slow_pyside"]


def test_full_suite_stops_after_gui_timing_preflight_failures() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([7])

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
    assert len(launched) == 1
    assert "tests/test_pytest_duration_catalog.py" in launched[0]


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
    assert launched[3][launched[3].index("-n") + 1] == "8"
    assert launched[3][-2:] == ["-m", "pyside_gui"]


def test_full_suite_updates_source_version_only_after_every_test_phase_passes() -> None:
    launched: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return 0

    assert full_pytest_runner.run_full_suite(python_executable="python.exe", call=fake_call) == 0
    assert launched[-1] == ["python.exe", "tools/update_source_version.py"]
    assert launched.count(["python.exe", "tools/update_source_version.py"]) == 2


def test_failed_full_phase_restores_preflight_source_stamp(tmp_path) -> None:
    stamp = tmp_path / "source_version.txt"
    stamp.write_text("original\n", encoding="utf-8")
    calls = 0

    def fake_call(command: list[str]) -> int:
        nonlocal calls
        calls += 1
        if "tools/update_source_version.py" in command:
            stamp.write_text("provisional\n", encoding="utf-8")
            return 0
        return 0 if calls == 1 else 3

    assert full_pytest_runner.run_full_suite(
        python_executable="python.exe", call=fake_call, source_version_path=stamp,
    ) == 3
    assert stamp.read_text(encoding="utf-8") == "original\n"
