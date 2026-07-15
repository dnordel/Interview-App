from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ctypes
import gc
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from hiring_pipeline import HiringPipelineStore, HiringWorkflowService, MigrationParityReport


@dataclass(frozen=True)
class HiringMigrationResult:
    backup_path: Path
    report_path: Path
    integrity_status: str
    idempotent: bool
    committed: bool
    committed_at: str
    parity: MigrationParityReport


class HiringMigrationCoordinator:
    """Fail-closed migration of canonical hiring history through a validated staging copy."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        if self.db_path.name.casefold() != "interview_history.sqlite3":
            raise ValueError("Canonical interview history database path is required.")

    def run(self) -> HiringMigrationResult:
        if not self.db_path.is_file():
            raise ValueError("Canonical interview history database does not exist.")
        migration_dir = self.db_path.parent / "hiring_migrations"
        migration_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = migration_dir / f"interview_history.pre-hiring-v2.{stamp}.sqlite3"
        report_path = migration_dir / f"hiring-v2-parity.{stamp}.json"
        staging_path = migration_dir / f"interview_history.hiring-v2.{stamp}.staging.sqlite3"
        lock_path = migration_dir / "hiring-v2-migration.lock"
        lock_fd = self._acquire_lock(lock_path)
        try:
            gc.collect()
            self._verify_source_is_available()
            self._sqlite_backup(self.db_path, backup_path)
            self._require_integrity(backup_path)
            self._sqlite_backup(self.db_path, staging_path)
            first = HiringWorkflowService(HiringPipelineStore(staging_path)).reconcile_history()
            self._require_integrity(staging_path)
            second = HiringWorkflowService(HiringPipelineStore(staging_path)).reconcile_history()
            idempotent = asdict(first) == asdict(second)
            if not idempotent:
                raise ValueError("Hiring migration is not idempotent.")
            if first.application_count + first.skipped_rows < first.source_rows:
                raise ValueError("Hiring migration parity validation failed.")
            gc.collect()
            os.replace(staging_path, self.db_path)
            committed_at = datetime.now(timezone.utc).isoformat()
            payload: dict[str, Any] = {
                **asdict(first),
                "backup_path": backup_path.name,
                "database_name": self.db_path.name,
                "integrity_status": "ok",
                "idempotent": True,
                "committed": True,
                "committed_at": committed_at,
            }
            report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return HiringMigrationResult(
                backup_path=backup_path,
                report_path=report_path,
                integrity_status="ok",
                idempotent=True,
                committed=True,
                committed_at=committed_at,
                parity=first,
            )
        finally:
            if staging_path.exists():
                staging_path.unlink()
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)

    @staticmethod
    def _acquire_lock(lock_path: Path) -> int:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ValueError("Hiring migration is already running.") from exc

    def _verify_source_is_available(self) -> None:
        self._require_replace_access()
        try:
            with sqlite3.connect(self.db_path, timeout=0) as conn:
                conn.execute("PRAGMA busy_timeout = 0")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._require_integrity_connection(conn)
                conn.execute("BEGIN EXCLUSIVE")
                conn.rollback()
        except sqlite3.Error as exc:
            raise ValueError("Canonical interview history database is busy or invalid.") from exc

    def _require_replace_access(self) -> None:
        if os.name != "nt":
            return
        delete_access = 0x00010000
        share_all = 0x00000001 | 0x00000002 | 0x00000004
        open_existing = 3
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(self.db_path),
            delete_access,
            share_all,
            None,
            open_existing,
            0,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ValueError("Canonical interview history database is open in another process.")
        kernel32.CloseHandle(ctypes.c_void_p(handle))

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as destination_conn:
            source_conn.backup(destination_conn)

    @classmethod
    def _require_integrity(cls, path: Path) -> None:
        try:
            with sqlite3.connect(path) as conn:
                cls._require_integrity_connection(conn)
        except sqlite3.Error as exc:
            raise ValueError("SQLite integrity validation failed.") from exc

    @staticmethod
    def _require_integrity_connection(conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or str(row[0]).casefold() != "ok":
            raise ValueError("SQLite integrity validation failed.")
