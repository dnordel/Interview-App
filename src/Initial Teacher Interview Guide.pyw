"""Backward-compatible wrapper for the canonical app entrypoint.

Deprecated: use interview_app.pyw directly.
"""

from __future__ import annotations

import runpy
from pathlib import Path

CANONICAL_ENTRYPOINT = Path(__file__).with_name("interview_app.pyw")

if not CANONICAL_ENTRYPOINT.exists():
    raise FileNotFoundError(f"Missing canonical entrypoint: {CANONICAL_ENTRYPOINT}")

runpy.run_path(str(CANONICAL_ENTRYPOINT), run_name="__main__")
