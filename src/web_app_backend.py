from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from data_store import InterviewHistoryStore, QuestionOverridesStore, SchoolOfferSettingsStore
from platform_services import (
    DEFAULT_BASE_DIR,
    DEFAULT_RUBRIC_PATH,
    INTERVIEW_HISTORY_PATH,
    QUESTIONS_OVERRIDE_PATH,
    REPO_ROOT,
    SCHOOL_OFFER_SETTINGS_PATH,
    ConfigValidationError,
    normalize_question_overrides_config,
    safe_read_json,
)
from scoring_reporting import (
    DocxExporter,
    DraftManager,
    ReportingValidationError,
    ScoringEngine,
    build_director_packet,
    build_integration_payload,
    serialize_integration_payload,
)

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RECORDING_REQUEST_BYTES = 25 * 1024 * 1024
WEB_APP_DIR = REPO_ROOT / "web" / "app"
RECORDING_MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
}


class WebAppBackendError(ValueError):
    """Raised when a web app API request cannot be safely handled."""


def load_bootstrap_payload() -> dict[str, Any]:
    return {
        "rubric": safe_read_json(DEFAULT_RUBRIC_PATH, {}, dict),
        "overrides": safe_read_json(QUESTIONS_OVERRIDE_PATH, {}, dict),
        "history": safe_read_json(INTERVIEW_HISTORY_PATH, [], list),
        "offerSettings": safe_read_json(SCHOOL_OFFER_SETTINGS_PATH, {}, dict),
    }


def load_question_overrides() -> dict[str, Any]:
    return QuestionOverridesStore(QUESTIONS_OVERRIDE_PATH).data


def save_question_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebAppBackendError("Question override payload must be a JSON object.")
    try:
        normalized = normalize_question_overrides_config(payload)
    except ConfigValidationError as exc:
        raise WebAppBackendError("Question override payload is not valid.") from exc
    store = QuestionOverridesStore(QUESTIONS_OVERRIDE_PATH)
    store.data = normalized
    store.save()
    return normalized


def load_offer_settings() -> dict[str, dict[str, str]]:
    return SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH).load()


def save_offer_settings(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    normalized = _normalize_offer_settings(payload)
    store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
    store.save(normalized)
    return normalized


def load_history_rows() -> list[dict[str, Any]]:
    return InterviewHistoryStore(INTERVIEW_HISTORY_PATH).load()


def update_history_offer_status(row_key: str, offer_status: str, offer_letter_path: str = "") -> dict[str, Any]:
    key = _clean_text(row_key)
    status = _clean_text(offer_status).lower()
    if not key:
        raise WebAppBackendError("History row key is required.")
    if not status:
        raise WebAppBackendError("Offer status is required.")
    store = InterviewHistoryStore(INTERVIEW_HISTORY_PATH)
    updated = store.update_offer_state(key, status, _clean_text(offer_letter_path))
    if not updated:
        raise WebAppBackendError("History row was not found.")
    return {"updated": True, "history": store.load()}


def score_web_draft_preview(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_web_draft_payload(payload)
    rubric = _rubric_with_question_overrides()
    track = normalized["candidate"].get("track") or next(iter(rubric.get("tracks") or {}), "")
    try:
        return ScoringEngine.evaluate(rubric, track, normalized["trait_inputs"])
    except (KeyError, TypeError, ValueError, ReportingValidationError) as exc:
        raise WebAppBackendError("Draft scoring preview could not be calculated.") from exc


def finalize_web_draft(payload: dict[str, Any], *, base_dir: Path = DEFAULT_BASE_DIR) -> dict[str, Any]:
    normalized = normalize_web_draft_payload(payload)
    _require_finalize_candidate(normalized)
    rubric = _rubric_with_question_overrides()
    track = normalized["candidate"].get("track") or next(iter(rubric.get("tracks") or {}), "")
    try:
        scoring = ScoringEngine.evaluate(rubric, track, normalized["trait_inputs"])
        report_payload = _build_report_payload(normalized, rubric)
        output_dir = Path(base_dir) / "Indeed Interview Notes"
        out_path = DocxExporter(output_dir).export(rubric, report_payload, scoring)
        _require_child_path(out_path, output_dir)
        report_payload["referral_packet"]["interview_notes_path"] = str(out_path)
        integration_payload = build_integration_payload(report_payload, scoring, include_flow_slices=True)
        integration_path = serialize_integration_payload(
            Path(base_dir),
            integration_payload,
            candidate_name=normalized["candidate"].get("name", ""),
        )
        _require_child_path(integration_path, Path(base_dir) / "integration_exports")
        director_packet = build_director_packet(
            payload=report_payload,
            scoring=scoring,
            report_path=out_path,
            integration_path=integration_path,
            referral_packet=report_payload.get("referral_packet", {}) or {},
            generated_transcript_path=None,
        )
    except (KeyError, TypeError, ValueError, ReportingValidationError, OSError) as exc:
        raise WebAppBackendError("Web draft could not be finalized.") from exc

    history_entry = _build_history_entry(
        report_payload["candidate"],
        scoring,
        out_path,
        integration_path,
        report_payload.get("flow_recordings", []),
    )
    InterviewHistoryStore(INTERVIEW_HISTORY_PATH).append(history_entry)
    return {
        "report_path": str(out_path),
        "integration_path": str(integration_path),
        "director_packet": director_packet,
        "history_entry": history_entry,
        "scorePreview": scoring,
    }


def normalize_web_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebAppBackendError("Draft payload must be a JSON object.")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise WebAppBackendError("Draft payload candidate must be a JSON object.")
    candidate_name = _clean_text(candidate.get("candidate_name") or candidate.get("name"))
    if not candidate_name:
        raise WebAppBackendError("Draft payload candidate name is required.")

    return {
        "candidate": {
            "name": candidate_name,
            "interview_date": _clean_text(candidate.get("interview_date")),
            "school": _clean_text(candidate.get("school")),
            "track": _clean_text(candidate.get("track")),
            "qualification": _mapping_or_empty(candidate.get("qualification")),
        },
        "current_index": _non_negative_int(payload.get("current_flow_index", payload.get("current_index"))),
        "trait_inputs": _mapping_or_empty(payload.get("trait_inputs")),
        "custom_inputs": _mapping_or_empty(payload.get("custom_inputs")),
        "flow_time_marks": _list_or_empty(payload.get("flow_time_marks")),
        "flow_candidate_transcripts": _list_or_empty(payload.get("flow_candidate_transcripts")),
        "flow_recordings": _list_or_empty(payload.get("flow_recordings")),
        "referral_packet": _mapping_or_empty(payload.get("referral_packet")),
        "communication_log": _list_or_empty(payload.get("communication_log")),
    }


def save_web_draft(payload: dict[str, Any], *, base_dir: Path = DEFAULT_BASE_DIR) -> dict[str, str]:
    normalized = normalize_web_draft_payload(payload)
    manager = DraftManager(base_dir)
    path = manager.save_draft(normalized)
    _require_child_path(path, manager.drafts_dir)
    return {"draft_path": str(path), "draft_name": path.name}


def save_web_recording(payload: dict[str, Any], *, base_dir: Path = DEFAULT_BASE_DIR) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebAppBackendError("Recording payload must be a JSON object.")
    flow_index = _non_negative_int(payload.get("flow_index"))
    question_id = _safe_recording_token(payload.get("question_id"), default="question")
    mime_type = _clean_text(payload.get("mime_type")).split(";", 1)[0].lower()
    if mime_type not in RECORDING_MIME_EXTENSIONS:
        raise WebAppBackendError("Recording MIME type is not supported.")
    data_base64 = _clean_text(payload.get("data_base64"))
    if not data_base64:
        raise WebAppBackendError("Recording audio data is required.")
    try:
        audio_bytes = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WebAppBackendError("Recording audio data must be valid base64.") from exc
    if not audio_bytes:
        raise WebAppBackendError("Recording audio data is empty.")
    if len(audio_bytes) > MAX_RECORDING_REQUEST_BYTES:
        raise WebAppBackendError("Recording audio data is too large.")

    recording_dir = Path(base_dir) / "web_recordings"
    recording_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = recording_dir / f"q{flow_index}-{question_id}-{stamp}{RECORDING_MIME_EXTENSIONS[mime_type]}"
    _require_child_path(out_path, recording_dir)
    out_path.write_bytes(audio_bytes)
    return {
        "flow_index": flow_index,
        "question_id": question_id,
        "mime_type": mime_type,
        "audio_path": str(out_path),
        "byte_count": len(audio_bytes),
        "candidate_transcript": "",
    }


def list_web_drafts(*, base_dir: Path = DEFAULT_BASE_DIR, limit: int = 20) -> list[dict[str, str]]:
    manager = DraftManager(base_dir)
    draft_paths = sorted(
        (path for path in manager.drafts_dir.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    rows: list[dict[str, str]] = []
    for path in draft_paths[: max(0, limit)]:
        payload = safe_read_json(path, {}, dict)
        candidate = payload.get("candidate") if isinstance(payload, dict) else {}
        candidate = candidate if isinstance(candidate, dict) else {}
        rows.append(
            {
                "draft_name": path.name,
                "candidate_name": _clean_text(candidate.get("name") or candidate.get("candidate_name")),
                "interview_date": _clean_text(candidate.get("interview_date")),
                "school": _clean_text(candidate.get("school")),
                "track": _clean_text(candidate.get("track")),
                "modified_at": str(path.stat().st_mtime),
            }
        )
    return rows


def load_web_draft(draft_name: str, *, base_dir: Path = DEFAULT_BASE_DIR) -> dict[str, Any]:
    safe_name = _safe_draft_name(draft_name)
    manager = DraftManager(base_dir)
    path = manager.drafts_dir / safe_name
    _require_child_path(path, manager.drafts_dir)
    if not path.is_file():
        raise WebAppBackendError("Draft was not found.")
    payload = manager.load_draft(path)
    if not isinstance(payload, dict):
        raise WebAppBackendError("Draft payload must be a JSON object.")
    return payload


def build_handler(*, base_dir: Path = DEFAULT_BASE_DIR) -> type[BaseHTTPRequestHandler]:
    class WebAppRequestHandler(BaseHTTPRequestHandler):
        server_version = "InterviewWebAppBackend/0.1"

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route == "/api/health":
                self._send_json({"ok": True})
                return
            if route == "/api/bootstrap":
                self._send_json(load_bootstrap_payload())
                return
            if route == "/api/question-overrides":
                self._send_json({"overrides": load_question_overrides()})
                return
            if route == "/api/offer-settings":
                self._send_json({"offerSettings": load_offer_settings()})
                return
            if route == "/api/history":
                self._send_json({"history": load_history_rows()})
                return
            if route == "/api/drafts":
                self._send_json({"drafts": list_web_drafts(base_dir=base_dir)})
                return
            if route.startswith("/api/drafts/"):
                draft_name = unquote(route.removeprefix("/api/drafts/"))
                try:
                    self._send_json({"draft": load_web_draft(draft_name, base_dir=base_dir)})
                except (WebAppBackendError, json.JSONDecodeError):
                    self._send_error(404, "Draft was not found.")
                return
            if route in {"/", "/web/app", "/web/app/"}:
                self._send_static_file(WEB_APP_DIR / "index.html")
                return
            if route.startswith("/web/app/"):
                relative = route.removeprefix("/web/app/")
                self._send_static_file(WEB_APP_DIR / relative)
                return
            self._send_error(404, "Not found.")

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            if route == "/api/question-overrides":
                try:
                    payload = self._read_json_body()
                    self._send_json({"overrides": save_question_overrides(payload)})
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route == "/api/offer-settings":
                try:
                    payload = self._read_json_body()
                    self._send_json({"offerSettings": save_offer_settings(payload)})
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route.startswith("/api/history/") and route.endswith("/offer-status"):
                row_key = unquote(route.removeprefix("/api/history/").removesuffix("/offer-status"))
                try:
                    payload = self._read_json_body()
                    self._send_json(
                        update_history_offer_status(
                            row_key,
                            str(payload.get("offer_status", "")),
                            str(payload.get("offer_letter_path", "")),
                        )
                    )
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route == "/api/score-preview":
                try:
                    payload = self._read_json_body()
                    self._send_json({"scorePreview": score_web_draft_preview(payload)})
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route == "/api/finalize":
                try:
                    payload = self._read_json_body()
                    self._send_json(finalize_web_draft(payload, base_dir=base_dir), status=201)
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route == "/api/recordings":
                try:
                    payload = self._read_json_body(max_bytes=MAX_RECORDING_REQUEST_BYTES)
                    self._send_json(save_web_recording(payload, base_dir=base_dir), status=201)
                except WebAppBackendError as exc:
                    self._send_error(400, str(exc))
                except json.JSONDecodeError:
                    self._send_error(400, "Request body must be valid JSON.")
                return
            if route != "/api/drafts":
                self._send_error(404, "Not found.")
                return
            try:
                payload = self._read_json_body()
                self._send_json(save_web_draft(payload, base_dir=base_dir), status=201)
            except WebAppBackendError as exc:
                self._send_error(400, str(exc))
            except json.JSONDecodeError:
                self._send_error(400, "Request body must be valid JSON.")

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _read_json_body(self, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise WebAppBackendError("Invalid Content-Length header.") from exc
            if content_length < 1:
                raise WebAppBackendError("Request body is required.")
            if content_length > max_bytes:
                raise WebAppBackendError("Request body is too large.")
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise WebAppBackendError("Request body must be a JSON object.")
            return payload

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def _send_static_file(self, path: Path) -> None:
            try:
                _require_child_path(path, WEB_APP_DIR)
            except WebAppBackendError:
                self._send_error(404, "Not found.")
                return
            if not path.is_file():
                self._send_error(404, "Not found.")
                return
            body = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return WebAppRequestHandler


def run_server(*, host: str = "127.0.0.1", port: int = 8766, base_dir: Path = DEFAULT_BASE_DIR) -> None:
    handler = build_handler(base_dir=base_dir)
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _safe_recording_token(value: Any, *, default: str) -> str:
    raw = _clean_text(value) or default
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return (safe.strip("._-") or default)[:80]


def _normalize_offer_settings(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    if not isinstance(payload, dict):
        raise WebAppBackendError("Offer settings payload must be a JSON object.")
    output: dict[str, dict[str, str]] = {}
    for school, cfg in payload.items():
        school_name = _clean_text(school)
        if not school_name:
            continue
        if not isinstance(cfg, dict):
            raise WebAppBackendError("Offer settings payload is not valid.")
        output[school_name] = {
            "full_time_template": _clean_text(cfg.get("full_time_template")),
            "part_time_template": _clean_text(cfg.get("part_time_template")),
            "offer_output_dir": _clean_text(cfg.get("offer_output_dir")),
        }
    return output


def _rubric_with_question_overrides() -> dict[str, Any]:
    rubric = safe_read_json(DEFAULT_RUBRIC_PATH, {}, dict)
    if not isinstance(rubric.get("traits"), list) or not isinstance(rubric.get("tracks"), dict):
        raise WebAppBackendError("Rubric configuration is not valid.")
    merged = dict(rubric)
    overrides = load_question_overrides()
    merged["trait_question_overrides"] = dict(overrides.get("trait_question_overrides", {}) or {})
    return merged


def _require_finalize_candidate(payload: dict[str, Any]) -> None:
    candidate = payload.get("candidate") if isinstance(payload, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    for field_name in ("name", "interview_date", "track"):
        if not _clean_text(candidate.get(field_name)):
            raise WebAppBackendError("Finalize requires candidate name, interview date, and track.")


def _build_report_payload(payload: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(payload["candidate"])
    track = candidate.get("track", "")
    overrides = load_question_overrides()
    flow = _flow_for_track(rubric, overrides, track)
    trait_inputs = payload.get("trait_inputs", {}) or {}
    custom_inputs = payload.get("custom_inputs", {}) or {}
    flow_transcript: list[dict[str, Any]] = []
    custom_answers: list[dict[str, str]] = []
    for index, item in enumerate(flow, start=1):
        item_type = _clean_text(item.get("type")).lower()
        item_id = _clean_text(item.get("id"))
        if item_type == "trait":
            trait = _trait_by_id(rubric, item_id)
            if not trait:
                continue
            state = trait_inputs.get(item_id, {}) if isinstance(trait_inputs, dict) else {}
            state = state if isinstance(state, dict) else {}
            flow_transcript.append(
                {
                    "flow_index": index,
                    "type": "trait",
                    "id": item_id,
                    "trait_id": item_id,
                    "title": _clean_text(trait.get("name")) or item_id,
                    "question": _primary_question(rubric, trait),
                    "candidate_transcript": _clean_text(state.get("question_notes")),
                    "raw_score": state.get("raw_score"),
                    "question_notes": _clean_text(state.get("question_notes")),
                    "trait_notes": _clean_text(state.get("trait_notes")),
                    "verbatim_notes": _clean_text(state.get("verbatim_notes")),
                    "no_example_after_followups": bool(state.get("no_example_after_followups")),
                    "absolute_disqualifier": bool(state.get("absolute_disqualifier")),
                }
            )
        elif item_type == "custom":
            custom = _custom_question_by_id(overrides, track, item_id)
            state = custom_inputs.get(item_id, {}) if isinstance(custom_inputs, dict) else {}
            state = state if isinstance(state, dict) else {}
            question_text = _clean_text(custom.get("text")) if custom else item_id
            answer = _clean_text(state.get("answer"))
            custom_answers.append({"question_text": question_text, "answer": answer})
            flow_transcript.append(
                {
                    "flow_index": index,
                    "type": "custom",
                    "id": item_id,
                    "title": item_id,
                    "question": question_text,
                    "candidate_transcript": answer,
                }
            )
    return {
        **payload,
        "candidate": candidate,
        "referral_packet": dict(payload.get("referral_packet", {}) or {}),
        "communication_log": list(payload.get("communication_log", []) or []),
        "flow_recordings": list(payload.get("flow_recordings", []) or []),
        "audio_recording": list(payload.get("flow_recordings", []) or []),
        "flow_transcript": flow_transcript,
        "custom_answers": custom_answers,
    }


def _flow_for_track(rubric: dict[str, Any], overrides: dict[str, Any], track: str) -> list[dict[str, Any]]:
    configured = overrides.get("track_question_flow", {}).get(track, [])
    if isinstance(configured, list) and configured:
        return [item for item in configured if isinstance(item, dict)]
    custom_items = [
        {"type": "custom", "id": item.get("id")}
        for item in overrides.get("custom_questions", {}).get(track, [])
        if isinstance(item, dict)
    ]
    trait_items = [{"type": "trait", "id": trait.get("id")} for trait in _traits_for_track(rubric, track)]
    return [*custom_items, *trait_items]


def _traits_for_track(rubric: dict[str, Any], track: str) -> list[dict[str, Any]]:
    traits = rubric.get("traits", [])
    output: list[dict[str, Any]] = []
    for trait in traits if isinstance(traits, list) else []:
        if not isinstance(trait, dict):
            continue
        applicable = trait.get("applicable_tracks", ["all"])
        if "all" in applicable or track in applicable:
            output.append(trait)
    return output


def _trait_by_id(rubric: dict[str, Any], trait_id: str) -> dict[str, Any] | None:
    for trait in rubric.get("traits", []) if isinstance(rubric.get("traits"), list) else []:
        if isinstance(trait, dict) and _clean_text(trait.get("id")) == trait_id:
            return trait
    return None


def _custom_question_by_id(overrides: dict[str, Any], track: str, custom_id: str) -> dict[str, Any] | None:
    items = overrides.get("custom_questions", {}).get(track, [])
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and _clean_text(item.get("id")) == custom_id:
            return item
    return None


def _primary_question(rubric: dict[str, Any], trait: dict[str, Any]) -> str:
    trait_id = _clean_text(trait.get("id"))
    overrides = rubric.get("trait_question_overrides", {}) or {}
    return _clean_text(overrides.get(trait_id)) or _clean_text(trait.get("primary_question"))


def _build_history_entry(
    candidate: dict[str, Any],
    scoring: dict[str, Any],
    out_path: Path,
    integration_path: Path,
    recording_metadata: Any,
) -> dict[str, Any]:
    saved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "history_id": str(uuid4()),
        "interview_date": _clean_text(candidate.get("interview_date")),
        "candidate_name": _clean_text(candidate.get("name")),
        "interview_score": scoring.get("percent_of_max", 0),
        "determination": _clean_text(scoring.get("outcome")),
        "school": _clean_text(candidate.get("school")),
        "track": _clean_text(candidate.get("track")),
        "saved_report_path": str(out_path),
        "integration_export_path": str(integration_path),
        "transcript_path": "",
        "interview_notes_path": str(out_path),
        "saved_at": saved_at,
        "offer_status": "not_generated",
        "offer_path": "",
        "offer_letter_path": "",
        "flow_recordings": recording_metadata if isinstance(recording_metadata, list) else [],
    }


def _safe_draft_name(value: str) -> str:
    name = Path(str(value or "")).name
    if not name or name != str(value) or not name.endswith(".json"):
        raise WebAppBackendError("Draft name is not valid.")
    return name


def _require_child_path(path: Path, parent: Path) -> None:
    resolved_path = Path(path).resolve()
    resolved_parent = Path(parent).resolve()
    if resolved_parent not in resolved_path.parents:
        raise WebAppBackendError("Draft path escaped the configured draft directory.")


if __name__ == "__main__":
    run_server()
