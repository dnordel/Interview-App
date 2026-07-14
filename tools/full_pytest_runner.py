from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence


METADATA_TEST_PATHS = (
    "tests/test_check_contract_review.py",
    "tests/test_contract_coverage_matrix.py",
    "tests/test_contract_scaffolding.py",
    "tests/test_full_pytest_runner.py",
    "tests/test_interview_app_contract_interfaces.py",
    "tests/test_interview_root_contracts.py",
    "tests/test_onboarding_contract_interfaces.py",
    "tests/test_pytest_duration_catalog.py",
    "tests/test_regenerate_contract_test_matrix.py",
    "tests/test_scoring_engine_contract.py",
    "tests/test_setup_and_run_contract.py",
    "tests/test_shared_module_contract_interfaces.py",
    "tests/test_trait_signal_schema.py",
)


def build_full_suite_commands(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
) -> tuple[list[str], list[str]]:
    common = ["--dist=load", "--maxschedchunk=1"]
    metadata_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        "-n",
        str(metadata_workers),
        *common,
        *METADATA_TEST_PATHS,
    ]
    full_command = [
        python_executable,
        "-m",
        "pytest",
        "-q",
        "-n",
        str(full_workers),
        *common,
        *(f"--ignore={path}" for path in METADATA_TEST_PATHS),
    ]
    return metadata_command, full_command


def run_full_suite(
    *,
    python_executable: str = sys.executable,
    metadata_workers: int = 8,
    full_workers: int = 24,
    call: Callable[[list[str]], int] = subprocess.call,
) -> int:
    metadata_command, full_command = build_full_suite_commands(
        python_executable=python_executable,
        metadata_workers=metadata_workers,
        full_workers=full_workers,
    )
    print(f"[metadata preflight] running with {metadata_workers} workers", flush=True)
    metadata_exit_code = call(metadata_command)
    if metadata_exit_code:
        print("[metadata preflight] FAILED; main suite not started", flush=True)
        return metadata_exit_code
    print(f"[metadata preflight] passed; starting main suite with {full_workers} workers", flush=True)
    return call(full_command)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run metadata tests in parallel, then start the parallel main suite only when metadata passes."
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
