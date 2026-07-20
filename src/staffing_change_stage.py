from __future__ import annotations

from pathlib import Path

from cross_database_change_stage import (
    CrossDatabaseChangeEvent,
    CrossDatabaseChangeStage,
    _required_text,
    _safe_slug,
    _utc_now_iso,
)


_SCHEMA_VERSION = 1
_EVENT_KIND = "staffing_change_event"
_RECEIPT_KIND = "staffing_change_receipt"
StaffingChangeEvent = CrossDatabaseChangeEvent


class StaffingChangeStage(CrossDatabaseChangeStage):
    """Compatibility wrapper for Staffing's shared cross-database stage."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, domain="staffing")
