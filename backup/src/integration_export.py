from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app_content import sanitize_filename


def normalize_outcome_label(outcome: Any) -> str:
    raw = str(outcome or "").strip().lower()
    mapping = {
        "hire": "hire",
        "borderline": "borderline",
        "no hire": "no_hire",
        "no_hire": "no_hire",
        "nohire": "no_hire",
    }
    return mapping.get(raw, "borderline")


def build_integration_payload(
    payload: dict[str, Any],
    scoring: dict[str, Any],
    *,
    include_flow_slices: bool = True,
) -> dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    rows = scoring.get("rows", []) or []
    custom_answers = payload.get("custom_answers", []) or []
    flow_transcript = payload.get("flow_transcript", []) or []

    trait_notes = [_trait_note(row) for row in rows]
    referral_packet = payload.get("referral_packet", {}) or {}
    communication_log = payload.get("communication_log", []) or []

    export_payload: dict[str, Any] = {
        "candidate": {
            "name": str(candidate.get("name", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
            "qualification": {
                "has_degree": candidate.get("qualification", {}).get("has_degree", None),
                "degree_type": str(candidate.get("qualification", {}).get("degree_type", "")).strip(),
                "degree_in_ece": bool(candidate.get("qualification", {}).get("degree_in_ece", False)),
                "ece_units_completed": candidate.get("qualification", {}).get("ece_units_completed", None),
                "infant_toddler_class_completed": bool(
                    candidate.get("qualification", {}).get("infant_toddler_class_completed", False)
                ),
                "total_units_completed": candidate.get("qualification", {}).get("total_units_completed", None),
                "years_experience": candidate.get("qualification", {}).get("years_experience", None),
            },
        },
        "percent_of_max": float(scoring.get("percent_of_max", 0.0) or 0.0),
        "decision": normalize_outcome_label(scoring.get("outcome")),
        "interview_notes": {
            "traits": trait_notes,
            "custom_answers": custom_answers,
        },
        "referral_packet": {
            "resume_path": str(referral_packet.get("resume_path", "")).strip(),
            "interview_notes_path": str(referral_packet.get("interview_notes_path", "")).strip(),
            "transcript_path": str(referral_packet.get("transcript_path", "")).strip(),
        },
        "communication_log": list(communication_log),
    }

    slices = _flow_slices(flow_transcript)
    if include_flow_slices and slices:
        export_payload["flow_transcript_slices"] = slices

    return export_payload


def serialize_integration_payload(
    base_output_dir: Path,
    export_payload: dict[str, Any],
    *,
    candidate_name: str,
) -> Path:
    base_dir = Path(base_output_dir).expanduser().resolve()
    export_dir = (base_dir / "integration_exports").resolve()
    if base_dir not in {export_dir, *export_dir.parents}:
        raise ValueError("Refusing to write integration export outside base output directory")

    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_candidate = sanitize_filename(candidate_name or "Unknown")
    out_path = export_dir / f"integration-{stamp}-{safe_candidate}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(export_payload, f, indent=2, ensure_ascii=False)

    return out_path


def _trait_note(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trait_id": row.get("trait_id", ""),
        "trait_name": row.get("trait_name", ""),
        "raw_score": row.get("raw_score"),
        "question_notes": row.get("question_notes", ""),
        "trait_notes": row.get("trait_notes", ""),
        "verbatim_notes": row.get("verbatim_notes", ""),
    }


def _flow_slices(flow_transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    for item in flow_transcript:
        candidate_tx = str(item.get("candidate_transcript", "")).strip()
        if not candidate_tx:
            continue

        slices.append(
            {
                "type": item.get("type", ""),
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "candidate_transcript": candidate_tx,
            }
        )

    return slices
