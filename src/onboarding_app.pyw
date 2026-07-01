from __future__ import annotations

from pathlib import Path


_IMPL_PATH = Path(__file__).with_suffix(".py")
exec(compile(_IMPL_PATH.read_text(encoding="utf-8"), str(_IMPL_PATH), "exec"), globals())
