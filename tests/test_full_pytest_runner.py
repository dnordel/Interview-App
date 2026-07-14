from __future__ import annotations

from tools import full_pytest_runner


def test_full_suite_runs_parallel_metadata_phase_before_main_suite() -> None:
    metadata_command, full_command = full_pytest_runner.build_full_suite_commands(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
    )

    assert metadata_command[:10] == [
        "python.exe",
        "-m",
        "pytest",
        "-q",
        "-n",
        "8",
        "--dist=load",
        "--maxschedchunk=1",
        *full_pytest_runner.METADATA_TEST_PATHS[:2],
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
    assert all(f"--ignore={path}" in full_command for path in full_pytest_runner.METADATA_TEST_PATHS)


def test_full_suite_stops_after_parallel_metadata_failures() -> None:
    launched: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return 3

    exit_code = full_pytest_runner.run_full_suite(
        python_executable="python.exe",
        metadata_workers=8,
        full_workers=24,
        call=fake_call,
    )

    assert exit_code == 3
    assert len(launched) == 1
    assert launched[0][launched[0].index("-n") + 1] == "8"


def test_full_suite_starts_main_phase_after_metadata_passes() -> None:
    launched: list[list[str]] = []
    exit_codes = iter([0, 5])

    def fake_call(command: list[str]) -> int:
        launched.append(command)
        return next(exit_codes)

    exit_code = full_pytest_runner.run_full_suite(call=fake_call)

    assert exit_code == 5
    assert len(launched) == 2
    assert launched[1][launched[1].index("-n") + 1] == "24"
