"""Compatibility wrapper for Word document operations.

The implementation now lives in ``platform_services`` as part of the
flattened platform module.
"""

from __future__ import annotations

from platform_services import BACKEND, Document

__all__ = ["BACKEND", "Document"]
