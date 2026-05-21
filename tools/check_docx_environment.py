#!/usr/bin/env python3
"""Lightweight environment check to verify python-docx import source."""

from __future__ import annotations

import pathlib
import sys


EXPECTED_PATH_HINTS = (
    "site-packages/python_docx",
    "site-packages/docx",
    "dist-packages/python_docx",
    "dist-packages/docx",
)


def _is_expected_python_docx_path(import_path: pathlib.Path) -> bool:
    normalized = str(import_path).replace("\\", "/").lower()
    return any(hint in normalized for hint in EXPECTED_PATH_HINTS)


def main() -> int:
    try:
        import docx  # type: ignore
    except Exception as exc:
        print(f"[FAIL] Could not import docx module: {exc}")
        print("Install required dependency: python-docx")
        return 1

    module_path = pathlib.Path(getattr(docx, "__file__", "<unknown>"))
    print(f"docx.__file__ = {module_path}")
    print(
        "Expected package path pattern includes one of: "
        + ", ".join(EXPECTED_PATH_HINTS)
    )

    if _is_expected_python_docx_path(module_path):
        print("[OK] docx import path looks compatible with python-docx.")
        return 0

    print("[FAIL] docx import path does not match expected python-docx package location.")
    print("Uninstall incompatible package and reinstall python-docx.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
