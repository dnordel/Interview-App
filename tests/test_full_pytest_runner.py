from __future__ import annotations

from tools import full_pytest_runner


def test_full_suite_runs_gui_timing_preflight_before_combined_full_suite() -> None:
    quick_command, full_command = full_pytest_runner.build_full_suite_commands(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
    )

    assert quick_command == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "tests/test_pytest_duration_catalog.py::test_duration_catalog_covers_collected_tests",
        "tests/test_pytest_duration_catalog.py::test_gui_scenario_catalog_entries_have_measured_scheduler_durations",
        "tests/test_pytest_duration_catalog.py::test_marked_pyside_gui_tests_are_scenarios_or_focused_exceptions",
        "tests/test_pytest_duration_catalog.py::test_required_pyside_gui_surfaces_have_named_scenarios",
    ]
    assert full_command[:8] == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "-n",
        "24",
        "--dist=load",
        "--maxschedchunk=1",
    ]
    assert all(f"--deselect={nodeid}" in full_command for nodeid in full_pytest_runner.GUI_TIMING_PREFLIGHT_TESTS)
    assert not any(argument.startswith("--ignore=tests/test_") for argument in full_command)


def test_full_suite_returns_combined_full_suite_failure_after_preflight_passes() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 3])

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
    assert len(launched) == 2
    assert launched[1][launched[1].index("-n") + 1] == "24"


def test_full_suite_stops_after_gui_timing_preflight_failures() -> None:
    launched: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return 7

    exit_code = full_pytest_runner.run_full_suite(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        call=fake_call,
    )

    assert exit_code == 7
    assert len(launched) == 1
    assert any("test_duration_catalog_covers_collected_tests" in part for part in launched[0])


def test_full_suite_starts_combined_full_phase_after_preflight_passes() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 5])

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return next(exit_codes)

    exit_code = full_pytest_runner.run_full_suite(call=fake_call)

    assert exit_code == 5
    assert len(launched) == 2
    assert launched[1][launched[1].index("-n") + 1] == "24"
