from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from app_content import sanitize_filename


class DirectorReferralError(RuntimeError):
    pass


def _allowed_referral_hosts_from_env() -> set[str]:
    raw_hosts = str(os.environ.get("DIRECTOR_REFERRAL_ALLOWED_HOSTS", "")).strip()
    if not raw_hosts:
        return set()
    hosts = [part.strip().lower() for part in raw_hosts.split(",")]
    return {host for host in hosts if host}


def _validate_referral_endpoint(endpoint: str, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme.lower() != "https":
        raise DirectorReferralError("Director referral endpoint must use HTTPS.")
    if not host:
        raise DirectorReferralError("Director referral endpoint host is missing.")

    enforced_hosts = allowed_hosts if allowed_hosts is not None else _allowed_referral_hosts_from_env()
    if enforced_hosts and host not in {h.lower() for h in enforced_hosts if h}:
        raise DirectorReferralError("Director referral endpoint host is not in the allowlist.")

    return endpoint


def build_director_packet(
    *,
    payload: dict[str, Any],
    scoring: dict[str, Any],
    report_path: Path,
    integration_path: Path,
    referral_packet: dict[str, str],
    generated_transcript_path: Path | None = None,
) -> dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    documents = {
        "resume_path": str(referral_packet.get("resume_path", "")).strip(),
        "interview_notes_path": str(referral_packet.get("interview_notes_path", "")).strip(),
        "transcript_path": str(generated_transcript_path or referral_packet.get("transcript_path", "")).strip(),
        "final_report_path": str(report_path),
        "integration_export_path": str(integration_path),
    }
    return {
        "event": "director_referral_packet",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "name": str(candidate.get("name", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
        },
        "scoring": {
            "outcome": scoring.get("outcome"),
            "percent_of_max": scoring.get("percent_of_max"),
            "weighted_total": scoring.get("weighted_total"),
            "max_weighted_total": scoring.get("max_weighted_total"),
        },
        "documents": documents,
    }


def send_director_packet(
    packet: dict[str, Any],
    endpoint: str,
    *,
    timeout_seconds: int = 12,
    allowed_hosts: set[str] | None = None,
) -> dict[str, Any]:
    endpoint_clean = str(endpoint or "").strip()
    if not endpoint_clean:
        raise DirectorReferralError("Director referral endpoint is not configured.")
    _validate_referral_endpoint(endpoint_clean, allowed_hosts)

    req = request.Request(
        endpoint_clean,
        data=json.dumps(packet).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            return {
                "status_code": int(getattr(resp, "status", 200) or 200),
                "response": response_body,
            }
    except error.HTTPError as exc:
        raise DirectorReferralError(f"Referral endpoint rejected packet ({exc.code}): {exc.reason}") from exc
    except error.URLError as exc:
        raise DirectorReferralError(f"Failed to reach referral endpoint: {exc.reason}") from exc


def default_referral_endpoint() -> str:
    return str(os.environ.get("DIRECTOR_REFERRAL_ENDPOINT", "")).strip()


def append_communication_log(base_dir: Path, event: dict[str, Any], *, candidate_name: str) -> Path:
    comm_dir = Path(base_dir).expanduser() / "communications"
    comm_dir.mkdir(parents=True, exist_ok=True)
    safe_candidate = sanitize_filename(candidate_name or "Unknown")
    out_path = comm_dir / f"director-referral-{safe_candidate}.jsonl"
    line = json.dumps(event, ensure_ascii=False)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return out_path
