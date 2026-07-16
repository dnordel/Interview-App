from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any, Optional

from platform_services import (
    ConfigValidationError,
    atomic_write_json,
    load_json_dict,
    normalize_question_overrides_config,
    validate_disqualifier_config,
    validate_rubric_config,
)

DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS: dict[str, str] = {
    "Hawthorne": r"\LPL HAW Office Shared Docs\Staff\Candidates",
    "Long Beach": r"\Dropbox\LPL NLB Office Shared\Staff\Candidates",
    "North Long Beach": r"\Dropbox\LPL NLB Office Shared\Staff\Candidates",
    "Palmdale": r"\Dropbox\LPL PMD Office Shared\Staff\Candidates",
}

DEFAULT_SCHOOL_OFFER_DIRS: dict[str, str] = {
    "Hawthorne": r"\Dropbox\HR-HAW\HAW Employment Offers",
    "Long Beach": r"\Dropbox\HR-NLB\NLB Employment Offers",
    "North Long Beach": r"\Dropbox\HR-NLB\NLB Employment Offers",
    "Palmdale": r"\Dropbox\HR-PMD\PMD Employment Offers",
}

DEFAULT_SCHOOL_OFFER_TEMPLATE_NAMES: dict[str, tuple[str, str]] = {
    "Hawthorne": (
        ".Preschool Partners Offer of Employment TEMPLATE - FULL TIME.docx",
        ".Preschool Partners Offer of Employment TEMPLATE - PART TIME.docx",
    ),
    "Long Beach": (
        ".Launch Pad Learning NLB Offer of Employment TEMPLATE - FULL TIME.docx",
        ".Launch Pad Learning NLB Offer of Employment TEMPLATE - PART TIME.docx",
    ),
    "North Long Beach": (
        ".Launch Pad Learning NLB Offer of Employment TEMPLATE - FULL TIME.docx",
        ".Launch Pad Learning NLB Offer of Employment TEMPLATE - PART TIME.docx",
    ),
    "Palmdale": (
        ".Launch Pad Learning PMD Offer of Employment TEMPLATE - FULL TIME.docx",
        ".Launch Pad Learning PMD Offer of Employment TEMPLATE - PART TIME.docx",
    ),
}

def default_school_offer_settings() -> dict[str, dict[str, str]]:
    return {
        school: {
            "full_time_template": str(PureWindowsPath(DEFAULT_SCHOOL_OFFER_DIRS[school]) / DEFAULT_SCHOOL_OFFER_TEMPLATE_NAMES[school][0]),
            "part_time_template": str(PureWindowsPath(DEFAULT_SCHOOL_OFFER_DIRS[school]) / DEFAULT_SCHOOL_OFFER_TEMPLATE_NAMES[school][1]),
            "contractor_template": "",
            "offer_output_dir": DEFAULT_SCHOOL_OFFER_DIRS[school],
            "interview_notes_dir": notes_dir,
        }
        for school, notes_dir in DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS.items()
    }


def resolve_interview_notes_output_dir(
    base_dir: Path,
    school: str,
    settings: dict[str, dict[str, str]] | None = None,
) -> Path:
    school_name = str(school or "").strip()
    configured = _interview_notes_dir_for_school(school_name, settings or {})
    if not configured:
        return Path(base_dir) / "Indeed Interview Notes"

    configured_path = PureWindowsPath(configured)
    if configured_path.drive:
        _reject_unsafe_path_parts(configured_path.parts)
        return Path(configured).expanduser()

    parts = _portable_dropbox_path_parts(configured)
    if not parts:
        return Path(base_dir) / "Indeed Interview Notes"
    dropbox_root = _find_dropbox_root(Path(base_dir))
    return dropbox_root.joinpath(*parts)


def resolve_offer_template_path(
    base_dir: Path,
    school: str,
    hours: int,
    settings: dict[str, dict[str, str]] | None = None,
) -> Path:
    field = "full_time_template" if int(hours) >= 30 else "part_time_template"
    configured = _school_offer_config(school, settings).get(field, "")
    if not configured:
        label = "full-time" if field == "full_time_template" else "part-time"
        raise ValueError(f"{label.capitalize()} offer template is not configured for {school}.")
    return _resolve_configured_dropbox_path(base_dir, configured)


def resolve_offer_output_dir(
    base_dir: Path,
    school: str,
    settings: dict[str, dict[str, str]] | None = None,
) -> Path:
    configured = _school_offer_config(school, settings).get("offer_output_dir", "")
    if not configured:
        raise ValueError(f"Offer output folder is not configured for {school}.")
    return _resolve_configured_dropbox_path(base_dir, configured)


def _school_offer_config(
    school: str,
    settings: dict[str, dict[str, str]] | None,
) -> dict[str, str]:
    school_name = str(school or "").strip()
    defaults = default_school_offer_settings().get(school_name, {})
    configured = (settings or {}).get(school_name, {})
    output = dict(defaults)
    if isinstance(configured, dict):
        output.update({str(key): str(value or "").strip() for key, value in configured.items()})
    return output


def _resolve_configured_dropbox_path(base_dir: Path, configured: str) -> Path:
    configured_path = PureWindowsPath(str(configured or "").strip())
    if configured_path.drive:
        _reject_unsafe_path_parts(configured_path.parts)
        return Path(str(configured_path)).expanduser()
    parts = _portable_dropbox_path_parts(str(configured_path))
    if not parts:
        raise ValueError("Configured offer path is empty.")
    return _find_dropbox_root(Path(base_dir)).joinpath(*parts)


def _interview_notes_dir_for_school(
    school: str,
    settings: dict[str, dict[str, str]],
) -> str:
    cfg = settings.get(school, {}) if isinstance(settings, dict) else {}
    if isinstance(cfg, dict):
        configured = str(cfg.get("interview_notes_dir", "")).strip()
        if configured:
            return configured
    return DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS.get(school, "")


def _find_dropbox_root(base_dir: Path) -> Path:
    resolved = Path(base_dir).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if "dropbox" in candidate.name.casefold():
            return candidate
    return resolved


def _portable_dropbox_path_parts(path_text: str) -> list[str]:
    text = str(path_text or "").strip()
    parts = [
        part.strip()
        for part in re.split(r"[\\/]+", text)
        if part.strip() and part.strip() != "."
    ]
    if parts and parts[0].casefold() == "dropbox":
        parts = parts[1:]
    _reject_unsafe_path_parts(parts)
    return parts


def _reject_unsafe_path_parts(parts: tuple[str, ...] | list[str]) -> None:
    if any(str(part).strip() == ".." for part in parts):
        raise ValueError("Interview notes folder cannot contain parent-directory segments.")

class RubricLoader:
    """Loads rubric.json and validates required structure."""

    def __init__(self, rubric_path: Path):
        self.rubric_path = Path(rubric_path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        data = load_json_dict(
            self.rubric_path,
            required=True,
            context="rubric.json",
        )
        self.validate(data)
        return data

    @staticmethod
    def validate(data: dict[str, Any]) -> None:
        try:
            validate_rubric_config(data)
        except ConfigValidationError as exc:
            raise ValueError(f"Invalid rubric configuration: {exc}") from exc

    def get_traits_for_track(self, track_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in self.data["traits"]:
            applicable = t.get("applicable_tracks", [])
            if "all" in applicable or track_key in applicable:
                out.append(t)
        return out


class DisqualifierSignalLibrary:
    """Loads disqualifier_signals.json (optional) and indexes it by trait_id."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()
        self.by_trait_id = self._build_index()

    def _load(self) -> dict[str, Any]:
        try:
            data = load_json_dict(
                self.path,
                required=False,
                context="disqualifier_signals.json",
                default={"questions": []},
            )
            validate_disqualifier_config(data)
            return data
        except ConfigValidationError:
            return {"questions": []}

    def _build_index(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for q in self.data.get("questions", []):
            raw_trait = str(q.get("trait_id", "")).strip()
            if raw_trait.isdigit():
                out[f"trait_{raw_trait}"] = q
            elif raw_trait:
                out[raw_trait] = q
        return out

    def get_for_trait(self, trait_id: str) -> Optional[dict[str, Any]]:
        return self.by_trait_id.get(trait_id)


# =========================
# Question overrides + custom questions + mixed flow
# =========================

class QuestionOverridesStore:
    """
    Persists GUI-edited question configuration without touching rubric.json.

    question_overrides.json:
    {
      "track_trait_order": {
        "<track_key>": ["trait_3", "trait_1", ...]
      },
      "trait_question_overrides": {
        "trait_1": "Overridden primary question text..."
      },
      "custom_questions": {
        "<track_key>": [
          {"id": "cq_...", "text": "Custom question?", "order": 1},
          ...
        ]
      },
      "track_question_flow": {
        "<track_key>": [
          {"type": "trait", "id": "trait_3"},
          {"type": "custom", "id": "cq_20260101_120000"},
          ...
        ]
      }
    }
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.load()

    @staticmethod
    def _default_data() -> dict[str, Any]:
        return {
            "track_trait_order": {},
            "trait_question_overrides": {},
            "custom_questions": {},
            "track_question_flow": {},
        }

    def _archive_corrupt_file(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt_path = self.path.with_name(f"{self.path.stem}.corrupt-{timestamp}{self.path.suffix}")
        try:
            self.path.replace(corrupt_path)
        except OSError:
            return

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_data()

        try:
            data = load_json_dict(
                self.path,
                required=False,
                context="question_overrides.json",
                default=self._default_data(),
            )
            return normalize_question_overrides_config(data)
        except (ConfigValidationError, OSError):
            self._archive_corrupt_file()
            return self._default_data()

    def save(self) -> None:
        atomic_write_json(self.path, self.data, indent=2, ensure_ascii=False)

    # ---- Trait ordering (legacy per track) ----
    def get_trait_order(self, track_key: str) -> list[str]:
        return list(self.data.get("track_trait_order", {}).get(track_key, []) or [])

    def set_trait_order(self, track_key: str, trait_ids: list[str]) -> None:
        self.data.setdefault("track_trait_order", {})[track_key] = list(trait_ids)
        self.save()

    # ---- Trait question text overrides ----
    def get_trait_question_override(self, trait_id: str) -> str | None:
        v = (self.data.get("trait_question_overrides", {}) or {}).get(trait_id)
        if isinstance(v, str) and v.strip():
            return v
        return None

    def set_trait_question_override(self, trait_id: str, text: str) -> None:
        self.data.setdefault("trait_question_overrides", {})[trait_id] = text.strip()
        self.save()

    def clear_trait_question_override(self, trait_id: str) -> None:
        overrides = self.data.setdefault("trait_question_overrides", {})
        if trait_id in overrides:
            del overrides[trait_id]
            self.save()

    # ---- Custom questions (CRUD per track) ----
    def list_custom_questions(self, track_key: str) -> list[dict[str, Any]]:
        items = list((self.data.get("custom_questions", {}) or {}).get(track_key, []) or [])
        return sorted(items, key=lambda x: (int(x.get("order", 999999)), str(x.get("text", "")).lower()))

    def upsert_custom_question(self, track_key: str, q: dict[str, Any]) -> None:
        self.data.setdefault("custom_questions", {}).setdefault(track_key, [])
        items = self.data["custom_questions"][track_key]

        qid = str(q.get("id") or "").strip()
        if not qid:
            raise ValueError("Custom question requires an id")

        for i, it in enumerate(items):
            if str(it.get("id")) == qid:
                items[i] = q
                self.save()
                return

        items.append(q)
        self.save()

    def delete_custom_question(self, track_key: str, qid: str) -> None:
        items = self.data.setdefault("custom_questions", {}).setdefault(track_key, [])
        self.data["custom_questions"][track_key] = [it for it in items if str(it.get("id")) != str(qid)]
        self.save()

    # ---- Mixed interview flow (per track) ----
    @staticmethod
    def _clean_flow_items(flow: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for it in flow or []:
            t = str(it.get("type", "")).strip().lower()
            qid = str(it.get("id", "")).strip()
            if t in {"trait", "custom"} and qid:
                cleaned.append({"type": t, "id": qid})
        return cleaned

    def get_question_flow_raw(self, track_key: str) -> list[dict[str, Any]]:
        flow = list((self.data.get("track_question_flow", {}) or {}).get(track_key, []) or [])
        return self._clean_flow_items(flow)

    def set_question_flow(self, track_key: str, flow: list[dict[str, Any]]) -> None:
        self.data.setdefault("track_question_flow", {})[track_key] = self._clean_flow_items(flow)
        self.save()

    def ensure_flow(
        self,
        track_key: str,
        valid_trait_ids_in_order: list[str],
        valid_custom_ids_in_order: list[str],
    ) -> list[dict[str, Any]]:
        """
        Returns a safe flow that includes:
        - all valid traits exactly once (non-deletable)
        - valid customs if referenced
        - any missing customs appended at the end
        - any missing traits appended at the end
        """
        raw = self.get_question_flow_raw(track_key)

        valid_trait_set = set(valid_trait_ids_in_order)
        valid_custom_set = set(valid_custom_ids_in_order)

        out: list[dict[str, Any]] = []
        seen_traits: set[str] = set()
        seen_customs: set[str] = set()

        for it in raw:
            t = it["type"]
            qid = it["id"]
            if t == "trait":
                if qid in valid_trait_set and qid not in seen_traits:
                    out.append({"type": "trait", "id": qid})
                    seen_traits.add(qid)
                continue
            if qid in valid_custom_set and qid not in seen_customs:
                out.append({"type": "custom", "id": qid})
                seen_customs.add(qid)

        for tid in valid_trait_ids_in_order:
            if tid not in seen_traits:
                out.append({"type": "trait", "id": tid})
                seen_traits.add(tid)

        for cid in valid_custom_ids_in_order:
            if cid not in seen_customs:
                out.append({"type": "custom", "id": cid})
                seen_customs.add(cid)

        if out != raw:
            self.set_question_flow(track_key, out)

        return out

    def remove_custom_from_flow(self, track_key: str, qid: str) -> None:
        flow = self.get_question_flow_raw(track_key)
        flow = [it for it in flow if not (it.get("type") == "custom" and str(it.get("id")) == str(qid))]
        self.set_question_flow(track_key, flow)

    def insert_custom_into_flow(self, track_key: str, qid: str, index: int) -> None:
        flow = self.get_question_flow_raw(track_key)
        if any(it.get("type") == "custom" and it.get("id") == qid for it in flow):
            return
        index = max(0, min(index, len(flow)))
        flow.insert(index, {"type": "custom", "id": qid})
        self.set_question_flow(track_key, flow)


# =========================
# Scoring and decisions
# =========================


class InterviewHistoryStore:
    """Persists finalized interview history to SQLite, importing legacy JSON rows."""

    _QUERY_COLUMNS: dict[str, str] = {
        "history_id": "TEXT",
        "candidate_name": "TEXT",
        "candidate_email": "TEXT",
        "school": "TEXT",
        "position": "TEXT",
        "interview_date": "TEXT",
        "outcome": "TEXT",
        "score": "REAL",
        "offer_status": "TEXT",
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.suffix.casefold() == ".sqlite3":
            self.db_path = self.path
            self.json_path = self.path.with_suffix(".json")
        else:
            self.json_path = self.path
            self.db_path = self.path.with_suffix(".sqlite3")

    def load(self) -> list[dict[str, Any]]:
        self._ensure_db()
        rows = self._load_from_db()
        if rows:
            return rows
        imported = self._import_json_rows_if_needed()
        if imported:
            return self._load_from_db()
        return []

    def load_filtered(
        self,
        *,
        school: str = "",
        outcome: str = "",
        offer_status: str = "",
        search: str = "",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_db()
        self._import_json_rows_if_needed()
        where: list[str] = []
        params: list[Any] = []
        school_text = str(school or "").strip()
        outcome_text = str(outcome or "").strip()
        offer_text = str(offer_status or "").strip()
        search_text = str(search or "").strip()
        if school_text:
            where.append("LOWER(school) = LOWER(?)")
            params.append(school_text)
        if outcome_text:
            where.append("LOWER(outcome) = LOWER(?)")
            params.append(outcome_text)
        if offer_text:
            where.append("LOWER(offer_status) = LOWER(?)")
            params.append(offer_text)
        if search_text:
            like = f"%{search_text.lower()}%"
            where.append(
                """
                (
                    LOWER(candidate_name) LIKE ?
                    OR LOWER(candidate_email) LIKE ?
                    OR LOWER(school) LIKE ?
                    OR LOWER(position) LIKE ?
                    OR LOWER(outcome) LIKE ?
                    OR LOWER(payload_json) LIKE ?
                )
                """
            )
            params.extend([like, like, like, like, like, like])
        query = "SELECT payload_json FROM interview_history"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY sort_order ASC, created_at ASC"
        if limit is not None and int(limit) > 0:
            query += " LIMIT ?"
            params.append(int(limit))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return self._payloads_from_rows(rows)

    def _load_from_path(self, path: Path | None) -> list[dict[str, Any]]:
        if path is None or not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _legacy_root_path(self) -> Path | None:
        if self.json_path.parent.name != "user_artifacts":
            return None
        legacy_path = self.json_path.parent.parent / self.json_path.name
        if legacy_path == self.json_path:
            return None
        return legacy_path

    @classmethod
    def _merge_history_rows(
        cls,
        legacy_rows: list[dict[str, Any]],
        canonical_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in [*legacy_rows, *canonical_rows]:
            key = cls.build_row_key(row)
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            merged.append(row)
        return merged

    def append(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict):
            return
        self._ensure_db()
        self._import_json_rows_if_needed()
        with sqlite3.connect(self.db_path) as conn:
            sort_order = self._next_sort_order(conn)
            row_key = self.build_row_key(entry) or f"row_{sort_order}"
            self._write_history_row(conn, row_key, sort_order, entry)
            conn.commit()

    def append_with_candidate_report(
        self,
        entry: dict[str, Any],
        report_snapshot: dict[str, Any],
        *,
        actor: str,
        actor_role: str = "admin",
        app_version: str = "",
    ) -> None:
        """Atomically persist one history row and its initial structured report."""

        if not isinstance(entry, dict) or not isinstance(report_snapshot, dict):
            raise ValueError("History entry and candidate report snapshot are required.")
        from candidate_report import CandidateReportRepository

        self._ensure_db()
        self._import_json_rows_if_needed()
        history_id = self.build_row_key(entry)
        if not history_id:
            raise ValueError("Structured candidate report requires a stable history id.")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            sort_order = self._next_sort_order(conn)
            self._write_history_row(conn, history_id, sort_order, entry)
            CandidateReportRepository.initialize_connection(conn)
            CandidateReportRepository.insert_initial_on_connection(
                conn,
                history_id,
                report_snapshot,
                actor=actor,
                actor_role=actor_role,
                app_version=app_version,
            )
            conn.commit()

    @staticmethod
    def build_row_key(entry: dict[str, Any]) -> str:
        row_id = str(entry.get("history_id", "")).strip()
        if row_id:
            return row_id
        candidate = str(entry.get("candidate_name", "")).strip().lower()
        interview_date = str(entry.get("interview_date", "")).strip()
        saved_at = str(entry.get("saved_at", "")).strip()
        if not saved_at:
            return ""
        return f"{candidate}|{interview_date}|{saved_at}"

    def update_row(self, row_key: str, updates: dict[str, Any]) -> bool:
        key = str(row_key).strip()
        if not key or not updates:
            return False
        self._ensure_db()
        self._import_json_rows_if_needed()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT sort_order, payload_json FROM interview_history WHERE row_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return False
            sort_order = int(row[0])
            try:
                payload = json.loads(str(row[1]))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(updates)
            self._write_history_row(conn, key, sort_order, payload)
            conn.commit()
            return True

    def delete_row(self, row_key: str) -> bool:
        key = str(row_key).strip()
        if not key:
            return False
        self._ensure_db()
        self._import_json_rows_if_needed()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM interview_history WHERE row_key = ?", (key,))
            conn.commit()
            return cursor.rowcount > 0

    def update_offer_state(self, row_key: str, offer_status: str, offer_letter_path: str = "") -> bool:
        key = str(row_key).strip()
        if not key:
            return False
        payload: dict[str, Any] = {"offer_status": str(offer_status).strip().lower()}
        if offer_letter_path:
            path_text = str(offer_letter_path).strip()
            payload["offer_letter_path"] = path_text
            payload["offer_path"] = path_text
        return self.update_row(key, payload)

    def repair_interview_notes_links(self, notes_dir: Path) -> int:
        rows = self.load()
        if not rows:
            return 0
        notes_index = self._interview_notes_index(Path(notes_dir))
        if not notes_index:
            return 0
        repaired = 0
        for row in rows:
            if self._has_existing_notes_link(row):
                continue
            candidate = self._normalize_match_text(str(row.get("candidate_name", "") or row.get("candidate", "")))
            interview_date = str(row.get("interview_date", "") or "").strip()
            school = self._normalize_match_text(str(row.get("school", "") or ""))
            notes_path = notes_index.get((interview_date, candidate, school)) or notes_index.get((interview_date, candidate, ""))
            if notes_path is None:
                continue
            path_text = str(notes_path)
            row["interview_notes_path"] = path_text
            row["saved_report_path"] = path_text
            row["notes_path"] = path_text
            row["report_path"] = path_text
            repaired += 1
        if repaired:
            self._save(rows)
        return repaired

    @classmethod
    def _interview_notes_index(cls, notes_dir: Path) -> dict[tuple[str, str, str], Path]:
        if not notes_dir.exists() or not notes_dir.is_dir():
            return {}
        index: dict[tuple[str, str, str], Path] = {}
        pattern = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}) - (?P<school>.+?) - (?P<candidate>.+?) - Interview\.docx$", re.IGNORECASE)
        for path in notes_dir.glob("*.docx"):
            match = pattern.match(path.name)
            if match is None:
                continue
            interview_date = match.group("date")
            school = cls._normalize_match_text(match.group("school"))
            candidate = cls._normalize_match_text(match.group("candidate"))
            index.setdefault((interview_date, candidate, school), path)
            index.setdefault((interview_date, candidate, ""), path)
        return index

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

    @staticmethod
    def _has_existing_notes_link(row: dict[str, Any]) -> bool:
        for key in ("interview_notes_path", "saved_report_path", "notes_path", "report_path"):
            path_text = str(row.get(key, "") or "").strip()
            if path_text and Path(path_text).exists():
                return True
        return False

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._ensure_db()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM interview_history")
            for index, row in enumerate(items):
                if not isinstance(row, dict):
                    continue
                row_key = self.build_row_key(row) or f"row_{index}"
                self._write_history_row(conn, row_key, index, row)
            conn.commit()

    def _write_history_row(self, conn: sqlite3.Connection, row_key: str, sort_order: int, row: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO interview_history (
                row_key, sort_order, payload_json, history_id, candidate_name,
                candidate_email, school, position, interview_date, outcome, score,
                offer_status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                sort_order = excluded.sort_order,
                payload_json = excluded.payload_json,
                history_id = excluded.history_id,
                candidate_name = excluded.candidate_name,
                candidate_email = excluded.candidate_email,
                school = excluded.school,
                position = excluded.position,
                interview_date = excluded.interview_date,
                outcome = excluded.outcome,
                score = excluded.score,
                offer_status = excluded.offer_status,
                updated_at = excluded.updated_at
            """,
            (
                row_key,
                sort_order,
                json.dumps(row, ensure_ascii=False, sort_keys=True),
                self._history_text(row, "history_id"),
                self._history_text(row, "candidate_name", "candidate", "name"),
                self._history_text(row, "candidate_email", "email", "candidateEmail"),
                self._history_text(row, "school"),
                self._history_text(row, "position", "candidate_position", "role", "track"),
                self._history_text(row, "interview_date", "date"),
                self._history_text(row, "outcome", "status", "interview_status", "determination"),
                self._history_score(row),
                self._history_text(row, "offer_status"),
                datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )

    @staticmethod
    def _next_sort_order(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM interview_history").fetchone()
        return int(row[0] if row is not None else 0)

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS interview_history (
                    row_key TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interview_history_sort ON interview_history(sort_order)")
            self._ensure_query_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interview_history_candidate ON interview_history(candidate_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interview_history_school ON interview_history(school)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interview_history_date ON interview_history(interview_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interview_history_offer ON interview_history(offer_status)")
            conn.commit()

    def _ensure_query_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(interview_history)").fetchall()}
        for name, column_type in self._QUERY_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE interview_history ADD COLUMN {name} {column_type}")

    def _load_from_db(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM interview_history ORDER BY sort_order ASC, created_at ASC"
            ).fetchall()
        return self._payloads_from_rows(rows)

    @staticmethod
    def _payloads_from_rows(rows: list[Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for (payload_json,) in rows:
            try:
                payload = json.loads(str(payload_json))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                output.append(payload)
        return output

    def _import_json_rows_if_needed(self) -> int:
        self._ensure_db()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM interview_history").fetchone()
            if row is not None and int(row[0]) > 0:
                return 0
        canonical_rows = self._load_from_path(self.json_path)
        legacy_rows = self._load_from_path(self._legacy_root_path())
        if canonical_rows and legacy_rows:
            rows = self._merge_history_rows(legacy_rows, canonical_rows)
        else:
            rows = canonical_rows or legacy_rows
        if not rows:
            return 0
        self._save(rows)
        return len(rows)

    @staticmethod
    def _history_text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @classmethod
    def _history_score(cls, row: dict[str, Any]) -> float | None:
        for key in ("percent_of_max", "score", "overall_score", "interview_score"):
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

class SchoolOfferSettingsStore:
    """Per-school templates and output folders for offer and interview-note generation."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        output: dict[str, dict[str, str]] = {}
        for school, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            output[str(school)] = {
                "full_time_template": str(cfg.get("full_time_template", "")).strip(),
                "part_time_template": str(cfg.get("part_time_template", "")).strip(),
                "contractor_template": str(cfg.get("contractor_template", "")).strip(),
                "offer_output_dir": str(cfg.get("offer_output_dir", "")).strip(),
                "interview_notes_dir": str(cfg.get("interview_notes_dir", "")).strip(),
            }
        return output

    def save(self, data: dict[str, dict[str, str]]) -> None:
        atomic_write_json(self.path, data, indent=2, ensure_ascii=False)


class SchoolEmailTemplateStore:
    """Per-school email template and recipient overrides."""

    TEMPLATE_KEYS = (
        "director_referral_subject_template",
        "director_referral_body_template",
        "director_email_to",
        "offer_approval_subject_template",
        "offer_approval_body_template",
        "offer_acceptance_subject_template",
        "offer_acceptance_body_template",
        "offer_email_to",
        "welcome_email_subject_template",
        "welcome_email_body_template",
    )

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def _sanitize_config(cls, cfg: dict[str, Any]) -> dict[str, str]:
        return {key: str(cfg.get(key, "")).strip() for key in cls.TEMPLATE_KEYS}

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}

        output: dict[str, dict[str, str]] = {}
        for school, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            output[str(school)] = self._sanitize_config(cfg)
        return output

    def save(self, data: dict[str, dict[str, str]]) -> None:
        payload = {str(school): self._sanitize_config(cfg) for school, cfg in data.items() if isinstance(cfg, dict)}
        atomic_write_json(self.path, payload, indent=2, ensure_ascii=False)


class InterviewAppSettingsStore:
    """Persists cross-launch interview app UI/runtime settings."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def save(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Interview app settings must be a dictionary")
        atomic_write_json(self.path, data, indent=2, ensure_ascii=False)
