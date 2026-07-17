from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs" / "pytest_duration_catalog.yaml"


def determine_placement(*, duration_seconds_n2: float, gui_heavy: bool) -> str:
    if gui_heavy:
        if duration_seconds_n2 >= 30:
            return "gui_wave_1"
        if duration_seconds_n2 >= 20:
            return "gui_wave_2"
        return "gui_wave_3"
    if duration_seconds_n2 >= 15:
        return "non_gui_tail"
    if duration_seconds_n2 >= 5:
        return "non_gui_middle"
    return "fast"


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    payload.setdefault("version", 1)
    payload.setdefault("entries", [])
    return payload


def catalog_entries_by_nodeid(path: Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    payload = load_catalog(path)
    return {str(entry["nodeid"]): entry for entry in payload.get("entries", [])}


def collect_nodeids(*, python_executable: str = sys.executable) -> list[str]:
    result = subprocess.run(
        [python_executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") or line.startswith("tests\\")
    ]


def normalize_catalog(
    *,
    nodeids: list[str],
    existing_entries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for nodeid in nodeids:
        current = dict(existing_entries.get(nodeid, {}))
        duration = float(current.get("duration_seconds_n2", 0.001))
        duration_source = str(current.get("duration_source") or "collection_default")
        gui_heavy = bool(current.get("gui_heavy", False))
        entries.append(
            {
                "nodeid": nodeid,
                "duration_seconds_n2": round(duration, 3),
                "duration_source": duration_source,
                "gui_heavy": gui_heavy,
                "placement": determine_placement(duration_seconds_n2=duration, gui_heavy=gui_heavy),
            }
        )
    return {
        "version": 1,
        "measurement_command": "python -m pytest -n 8 with PYTEST_DURATION_CATALOG_OUT=docs/pytest_duration_catalog.yaml",
        "entries": entries,
    }


def write_catalog(payload: dict[str, Any], path: Path = CATALOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def refresh_catalog_from_collection(path: Path = CATALOG_PATH) -> None:
    write_catalog(
        normalize_catalog(nodeids=collect_nodeids(), existing_entries=catalog_entries_by_nodeid(path)),
        path,
    )


def build_benchmark_commands(
    *,
    entries: list[dict[str, Any]],
    workers: int = 8,
    python_executable: str = sys.executable,
) -> list[list[str]]:
    if workers < 1:
        raise ValueError("Benchmark worker count must be positive.")
    common = ["--dist=load", "--maxschedchunk=1"]
    commands = [
        [
            python_executable,
            "-m",
            "pytest",
            "-n",
            str(workers),
            *common,
            "-m",
            "not slow_pyside",
        ]
    ]
    gui_nodeids = [
        str(entry["nodeid"])
        for entry in sorted(
            (entry for entry in entries if bool(entry.get("gui_heavy", False))),
            key=lambda entry: float(entry.get("duration_seconds_n2", 0.0)),
            reverse=True,
        )
    ]
    for index in range(0, len(gui_nodeids), workers):
        batch = gui_nodeids[index : index + workers]
        commands.append(
            [
                python_executable,
                "-m",
                "pytest",
                "-n",
                str(workers),
                *common,
                *batch,
            ]
        )
    return commands


def benchmark_catalog(
    path: Path = CATALOG_PATH,
    *,
    workers: int = 8,
    python_executable: str = sys.executable,
    call: Callable[..., int] = subprocess.call,
) -> int:
    refresh_catalog_from_collection(path)
    commands = build_benchmark_commands(
        entries=list(load_catalog(path).get("entries", [])),
        workers=workers,
        python_executable=python_executable,
    )
    env = {**os.environ, "PYTEST_DURATION_CATALOG_OUT": str(Path(path).resolve())}
    for index, command in enumerate(commands, start=1):
        print(f"[duration benchmark] phase {index}/{len(commands)} with {workers} workers", flush=True)
        exit_code = call(command, cwd=ROOT, env=env)
        if exit_code:
            return int(exit_code)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain pytest duration catalog coverage.")
    parser.add_argument("--refresh-from-collection", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    args = parser.parse_args(argv)
    if args.refresh_from_collection:
        refresh_catalog_from_collection(args.catalog)
        return 0
    if args.benchmark:
        return benchmark_catalog(args.catalog, workers=args.workers)
    parser.error("No action requested.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
