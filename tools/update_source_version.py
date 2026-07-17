from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = ROOT / "src"
DEFAULT_VERSION_PATH = ROOT / "config" / "source_version.txt"
if str(DEFAULT_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_SOURCE_ROOT))

from source_update_monitor import source_digest


def write_source_version(
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    version_path: Path = DEFAULT_VERSION_PATH,
    updated_at: str = "",
) -> dict[str, str]:
    timestamp = str(updated_at or _utc_now_iso()).strip()
    if not timestamp:
        raise ValueError("Updated timestamp is required.")
    target = Path(version_path).resolve()
    digest = source_digest(source_root)
    try:
        existing = dict(
            line.split("=", 1)
            for line in target.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    except OSError:
        existing = {}
    if existing.get("source_sha256") == digest and existing.get("updated_at"):
        return {"updated_at": existing["updated_at"], "source_sha256": digest}
    payload = {"updated_at": timestamp, "source_sha256": digest}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    text = f"updated_at={payload['updated_at']}\nsource_sha256={payload['source_sha256']}\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update source-version stamp after source code changes.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--version-path", type=Path, default=DEFAULT_VERSION_PATH)
    args = parser.parse_args(argv)
    payload = write_source_version(source_root=args.source_root, version_path=args.version_path)
    print(f"Updated {args.version_path}: {payload['updated_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
