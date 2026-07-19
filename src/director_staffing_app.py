from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from notification_service import NotificationService, NOTIFICATION_RULES_PATH
from app_branding import apply_staffing_app_icon
from staffing_referral_queue import StaffingReferralQueueStore
from staffing_change_stage import StaffingChangeStage
from staffing_dashboard_host import StaffingDashboardAccess, StaffingDashboardHost
from staffing_dashboard_v2 import apply_staffing_v2_light_theme
from staffing_service import StaffingChangeConflict, StaffingService, staffing_change_conflict_message
from staffing_store import StaffingEditLock, StaffingStore
from source_update_monitor import SourceUpdateDetector, build_source_update_banner, relaunch_application


ROOT = Path(__file__).resolve().parents[1]
USER_ARTIFACTS_DIR = ROOT / "user_artifacts"
STAFFING_DB_PATH = USER_ARTIFACTS_DIR / "interviews" / "staffing_dashboard.sqlite3"
STAFFING_SEED_PATH = ROOT / "config" / "staffing_seed.json"
INTERVIEW_HISTORY_DB_PATH = USER_ARTIFACTS_DIR / "interview_history.sqlite3"
INTERVIEW_HISTORY_JSON_PATH = USER_ARTIFACTS_DIR / "interview_history.json"
SOURCE_VERSION_PATH = ROOT / "config" / "source_version.txt"
SOURCE_UPDATE_ROOT = ROOT / "src"
STAFFING_REFERRAL_QUEUE_DB_PATH = USER_ARTIFACTS_DIR / "staffing_referrals.sqlite3"
STAFFING_REFERRAL_QUEUE_LEGACY_PATH = USER_ARTIFACTS_DIR / "staffing_referrals.pending.jsonl"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the director Staffing v2 dashboard.")
    parser.add_argument("--director-school", default="", help="Limit director Staffing dashboard to one school.")
    return parser.parse_args(list(argv) if argv is not None else None)


def import_staffing_seed_if_needed(store: StaffingStore, seed_path: Path = STAFFING_SEED_PATH) -> None:
    store.initialize()
    if not seed_path.exists():
        return
    existing_assignments = store.list_assignments()
    if not existing_assignments:
        store.import_seed_file(seed_path)
        return
    seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
    seed_assignment_count = 0
    for school in seed_data.get("schools", []):
        for classroom in school.get("classrooms", []):
            seed_assignment_count += len(classroom.get("slots", classroom.get("positions", [])))
        for support_row in school.get("support_rows", []):
            seed_assignment_count += len(support_row.get("slots", support_row.get("positions", [])))
    if len(existing_assignments) < seed_assignment_count:
        store.import_seed_file(seed_path)


def staffing_db_path_for_school(school: str, *, base_path: Path = STAFFING_DB_PATH) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", str(school or "").strip().lower()).strip("_")
    if not slug:
        return Path(base_path)
    base = Path(base_path)
    return base.with_name(f"{base.stem}_{slug}{base.suffix}")


def bootstrap_school_staffing_db_from_base(school: str, school_path: Path, *, base_path: Path = STAFFING_DB_PATH) -> None:
    if not str(school or "").strip():
        return
    source = Path(base_path)
    target = Path(school_path)
    if source == target or target.exists() or not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(f"{source.name}{suffix}")
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(f"{target.name}{suffix}"))


def sync_director_referrals(
    store: StaffingStore,
    *,
    school: str,
    history_db_path: Path = INTERVIEW_HISTORY_DB_PATH,
    history_json_path: Path = INTERVIEW_HISTORY_JSON_PATH,
    queue_db_path: Path = STAFFING_REFERRAL_QUEUE_DB_PATH,
    queue_legacy_path: Path = STAFFING_REFERRAL_QUEUE_LEGACY_PATH,
) -> int:
    service = StaffingService(store, notification_service=NotificationService())
    imported = _import_queued_director_referrals(service, school=school, queue_db_path=queue_db_path, queue_legacy_path=queue_legacy_path)
    imported += _backfill_director_referrals_from_history(
        store,
        service,
        school=school,
        history_db_path=history_db_path,
        history_json_path=history_json_path,
    )
    return imported


def append_director_referral_dismissal_event(
    *,
    history_id: str,
    school: str,
    candidate_name: str = "",
    removed_by: str,
    removal_source: str,
    queue_db_path: Path = STAFFING_REFERRAL_QUEUE_DB_PATH,
    queue_legacy_path: Path = STAFFING_REFERRAL_QUEUE_LEGACY_PATH,
) -> None:
    StaffingReferralQueueStore(queue_db_path, legacy_jsonl_path=queue_legacy_path).append(
        {
            "history_id": str(history_id or "").strip(),
            "school": str(school or "").strip(),
            "candidate_name": str(candidate_name or "").strip(),
            "removed_by": str(removed_by or "").strip() or "unknown",
            "removal_source": str(removal_source or "").strip() or "unknown",
        },
        operation="director_candidate_referral_dismissal",
    )


def apply_director_referral_dismissal_to_store(
    *,
    db_path: Path,
    history_id: str,
    school: str,
    candidate_name: str = "",
    removed_by: str,
    removal_source: str,
) -> None:
    target_path = Path(db_path)
    if not target_path.exists():
        return
    store = StaffingStore(target_path)
    removed = StaffingService(store).dismiss_director_referral_history_ids(
        [history_id],
        removed_by=removed_by,
        removal_source=removal_source,
    )
    if removed:
        return
    store.record_director_referral_removal_audit(
        history_id=history_id,
        candidate_name=candidate_name,
        school=school,
        removed_by=removed_by,
        removal_source=removal_source,
    )


def _import_queued_director_referrals(
    service: StaffingService,
    *,
    school: str,
    queue_db_path: Path,
    queue_legacy_path: Path,
) -> int:
    payloads = StaffingReferralQueueStore(queue_db_path, legacy_jsonl_path=queue_legacy_path).pop_for_school(school)
    imported = 0
    for payload in payloads:
        operation = str(payload.get("_operation") or "director_candidate_referral")
        try:
            if operation == "director_candidate_referral_dismissal":
                removed = service.dismiss_director_referral_history_ids(
                    [str(payload["history_id"])],
                    removed_by=str(payload.get("removed_by") or "unknown"),
                    removal_source=str(payload.get("removal_source") or "director_referral_queue"),
                )
                if not removed:
                    service.store.record_director_referral_removal_audit(
                        history_id=str(payload["history_id"]),
                        candidate_name=str(payload.get("candidate_name") or ""),
                        school=str(payload.get("school") or school),
                        removed_by=str(payload.get("removed_by") or "unknown"),
                        removal_source=str(payload.get("removal_source") or "director_referral_queue"),
                    )
            else:
                service.upsert_director_candidate_referral(
                    history_id=str(payload["history_id"]),
                    candidate_name=str(payload["candidate_name"]),
                    school=str(payload["school"]),
                    position=str(payload.get("position", "")),
                    interviewer_rating=payload.get("interviewer_rating"),
                    interviewer_outcome=str(payload["interviewer_outcome"]),
                    interview_date=str(payload.get("interview_date", "")),
                    candidate_email=str(payload.get("candidate_email", "")),
                    referral_date=str(payload.get("referral_date", "")),
                    queue_on_lock=True,
                )
            imported += 1
        except (OSError, ValueError, StaffingEditLock, KeyError):
            continue
    return imported


def _backfill_director_referrals_from_history(
    store: StaffingStore,
    service: StaffingService,
    *,
    school: str,
    history_db_path: Path,
    history_json_path: Path,
) -> int:
    school_filter = str(school or "").strip()
    dismissed_history_ids = store.list_dismissed_director_referral_history_ids()
    imported = 0
    for row in _load_history_payloads(history_db_path=history_db_path, history_json_path=history_json_path):
        outcome = _director_referral_outcome(_history_text(row, "status", "interview_status", "outcome", "determination"))
        if not outcome:
            score = _history_text(row, "score", "percent_of_max", "overall_score", "interview_score")
            outcome = _director_referral_outcome(_history_status_from_score(score))
        row_school = _history_text(row, "school")
        if school_filter and row_school != school_filter:
            continue
        history_id = _history_text(row, "history_id", "row_key") or _history_row_key(row)
        if history_id in dismissed_history_ids:
            continue
        try:
            service.upsert_director_candidate_referral(
                history_id=history_id,
                candidate_name=_history_text(row, "candidate_name", "candidate", "name") or "Unknown candidate",
                school=row_school,
                position=_history_text(row, "position", "candidate_position", "role", "track"),
                interviewer_rating=_director_referral_rating(_history_text(row, "score", "percent_of_max", "overall_score", "interview_score")),
                interviewer_outcome=outcome,
                interview_date=_history_text(row, "interview_date", "date"),
                candidate_email=_history_text(row, "candidate_email", "email", "candidateEmail"),
                referral_date=date.today().isoformat(),
                queue_on_lock=True,
            )
            imported += 1
        except (OSError, ValueError, StaffingEditLock):
            continue
    return imported


def _load_history_payloads(*, history_db_path: Path, history_json_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    db_path = Path(history_db_path)
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                payload_rows = conn.execute(
                    "SELECT payload_json FROM interview_history ORDER BY sort_order ASC, created_at ASC"
                ).fetchall()
        except sqlite3.Error:
            payload_rows = []
        for (payload_json,) in payload_rows:
            try:
                payload = json.loads(str(payload_json))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    if rows:
        return rows
    json_path = Path(history_json_path)
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("history"), list):
        return [row for row in payload["history"] if isinstance(row, dict)]
    return []


def _history_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _history_status_from_score(score: str) -> str:
    rating = _director_referral_rating(score)
    if rating is None:
        return ""
    percent = rating * 10 if rating <= 10 else rating
    if percent >= 80:
        return "Hire"
    if percent >= 65:
        return "Borderline"
    return "No Hire"


def _director_referral_outcome(status: str) -> str:
    normalized = str(status or "").strip().lower().replace("-", " ")
    if normalized == "hire":
        return "hire"
    if normalized == "borderline":
        return "borderline"
    return ""


def _director_referral_rating(score: str) -> float | None:
    text = str(score or "").strip().replace("%", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if 1 <= value <= 10:
        return value
    if 10 < value <= 100:
        return round(value / 10, 2)
    return None


def _history_row_key(row: dict[str, Any]) -> str:
    candidate = _history_text(row, "candidate_name", "candidate", "name") or "unknown"
    interview_date = _history_text(row, "interview_date", "date") or "unknown-date"
    return f"{candidate}:{interview_date}"


def _show_director_staffing_window_maximized(window: Any, QtWidgets: Any) -> None:
    screen = window.screen() or QtWidgets.QApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(screen.availableGeometry())
    window.showMaximized()


def launch_director_staffing_app(*, director_school: str = "") -> int:
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    apply_staffing_v2_light_theme(QtWidgets, QtGui, app)
    window = QtWidgets.QMainWindow()
    window.setWindowTitle("Director Staffing Dashboard")
    apply_staffing_app_icon(QtGui, app, window)
    staffing_path = staffing_db_path_for_school(director_school)
    bootstrap_school_staffing_db_from_base(director_school, staffing_path)
    store = StaffingStore(staffing_path)
    try:
        import_staffing_seed_if_needed(store)
    except StaffingEditLock:
        pass

    notification_service = lambda: NotificationService()
    change_stage = StaffingChangeStage(Path(STAFFING_DB_PATH).with_name("staffing_change_events"))
    replica_slug = re.sub(r"[^a-z0-9]+", "_", director_school.strip().lower()).strip("_") or "all"
    replica = f"director:{replica_slug}"
    actor = str(os.environ.get("USERNAME") or os.environ.get("USER") or "director")
    actor_slug = re.sub(r"[^a-z0-9]+", "_", actor.strip().lower()).strip("_") or "director"

    def resolve_staffing_change_conflict(conflict: StaffingChangeConflict) -> bool:
        answer = QtWidgets.QMessageBox.question(
            window,
            "Staffing Change Conflict",
            staffing_change_conflict_message(conflict),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def staffing_service() -> StaffingService:
        return StaffingService(
            store,
            notification_service=notification_service(),
            change_stage=change_stage,
            replica=replica,
            publisher=f"{replica}:{actor_slug}",
            school_scope=director_school,
            conflict_resolver=resolve_staffing_change_conflict,
        )

    host = StaffingDashboardHost(
        QtCore=QtCore,
        QtGui=QtGui,
        QtWidgets=QtWidgets,
        parent=window,
        store=store,
        service_factory=staffing_service,
        access=StaffingDashboardAccess(
            role="director",
            actor=actor,
            school_scope=director_school,
            removal_source="director_staffing_dashboard",
        ),
        history_path=INTERVIEW_HISTORY_DB_PATH,
        notification_store_path=NOTIFICATION_RULES_PATH,
        notification_service_factory=notification_service,
        director_referral_dismissal_callback=_queue_dashboard_director_referral_dismissals,
    )
    dashboard = host.page
    source_update_detector = SourceUpdateDetector(SOURCE_VERSION_PATH, source_root=SOURCE_UPDATE_ROOT)

    def restart_after_source_update() -> None:
        started = relaunch_application(QtCore, window.close, cwd=ROOT)
        if not started:
            QtWidgets.QMessageBox.warning(
                window,
                "Restart Failed",
                "Could not start the updated app. Please close and reopen it manually.",
            )

    source_update_banner, source_update_restart_button = build_source_update_banner(
        QtWidgets,
        restart_after_source_update,
    )
    central = QtWidgets.QWidget()
    central_layout = QtWidgets.QVBoxLayout(central)
    central_layout.setContentsMargins(0, 0, 0, 0)
    central_layout.setSpacing(0)
    central_layout.addWidget(source_update_banner)
    central_layout.addWidget(dashboard.widget, 1)
    window.setCentralWidget(central)
    setattr(app, "_director_staffing_window", window)
    setattr(app, "_director_staffing_dashboard", dashboard)
    setattr(app, "_director_staffing_host", host)
    setattr(app, "_director_source_update_banner", source_update_banner)
    setattr(app, "_director_source_update_restart_button", source_update_restart_button)
    _show_director_staffing_window_maximized(window, QtWidgets)

    def sync_referrals_after_first_paint() -> None:
        try:
            imported = sync_director_referrals(store, school=director_school)
            imported += staffing_service().replay_staged_changes()
        except StaffingEditLock:
            return
        if imported:
            dashboard.refresh()

    QtCore.QTimer.singleShot(100, sync_referrals_after_first_paint)

    def sync_staged_changes() -> None:
        try:
            imported = staffing_service().replay_staged_changes()
        except StaffingEditLock:
            return
        if imported:
            dashboard.refresh()

    sync_timer = QtCore.QTimer(window)
    sync_timer.setInterval(5000)
    sync_timer.timeout.connect(sync_staged_changes)
    sync_timer.start()
    setattr(app, "_director_staffing_sync_timer", sync_timer)

    def poll_source_version() -> None:
        if source_update_detector.poll():
            source_update_banner.show()

    source_update_timer = QtCore.QTimer(window)
    source_update_timer.setInterval(5000)
    source_update_timer.timeout.connect(poll_source_version)
    source_update_timer.start()
    setattr(app, "_director_source_update_timer", source_update_timer)
    return app.exec()


def _queue_dashboard_director_referral_dismissals(
    candidates: list[Any],
    removed_by: str,
    removal_source: str,
) -> None:
    for candidate in candidates:
        apply_director_referral_dismissal_to_store(
            db_path=STAFFING_DB_PATH,
            history_id=str(getattr(candidate, "history_id", "")),
            school=str(getattr(candidate, "school", "")),
            candidate_name=str(getattr(candidate, "candidate_name", "")),
            removed_by=removed_by,
            removal_source=removal_source,
        )
        append_director_referral_dismissal_event(
            history_id=str(getattr(candidate, "history_id", "")),
            school=str(getattr(candidate, "school", "")),
            candidate_name=str(getattr(candidate, "candidate_name", "")),
            removed_by=removed_by,
            removal_source=removal_source,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return launch_director_staffing_app(director_school=str(args.director_school or ""))


if __name__ == "__main__":
    raise SystemExit(main())
