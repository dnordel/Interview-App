from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app_content import INTERVIEW_HISTORY_PATH
from hiring_migration import HiringMigrationCoordinator


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and migrate canonical Hiring v2 history.")
    parser.add_argument("--database", type=Path, default=INTERVIEW_HISTORY_PATH)
    args = parser.parse_args()
    result = HiringMigrationCoordinator(args.database).run()
    print(
        json.dumps(
            {
                "backup": str(result.backup_path),
                "report": str(result.report_path),
                "source_rows": result.parity.source_rows,
                "applications": result.parity.application_count,
                "conflicts": result.parity.conflict_count,
                "skipped": result.parity.skipped_rows,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
