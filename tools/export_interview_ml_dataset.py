from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_store import InterviewMLDatasetStore, ml_dataset_path_for_history_path
from platform_services import INTERVIEW_HISTORY_PATH


def export_ml_dataset(*, ml_path: Path | None = None, output_dir: Path | None = None) -> dict[str, str]:
    dataset_path = Path(ml_path) if ml_path is not None else ml_dataset_path_for_history_path(INTERVIEW_HISTORY_PATH)
    export_dir = Path(output_dir) if output_dir is not None else dataset_path.parent / "interview_ml_exports"
    exports = InterviewMLDatasetStore(dataset_path).export_dataset(export_dir)
    return {name: str(path) for name, path in exports.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export interview ML dataset CSV/JSONL files.")
    parser.add_argument("--ml-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    print(json.dumps(export_ml_dataset(ml_path=args.ml_path, output_dir=args.output_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
