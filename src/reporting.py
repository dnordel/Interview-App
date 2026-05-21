from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from docx_compat import Document

from app_content import sanitize_filename
from local_summary import LocalInterviewSummarizer


class ReportingValidationError(ValueError):
    """Raised when a report cannot be scored or exported due to invalid draft data."""

class ScoringEngine:
    """
    Computes:
    - weighted totals
    - percent of max
    - critical trait override flags
    - absolute disqualifier lock
    - final outcome: Hire / Borderline / No Hire
    """

    @staticmethod
    def _coerce_raw_score(value: Any) -> tuple[Optional[int], int]:
        if isinstance(value, int) and value in {1, 2, 3, 4, 5}:
            return value, value
        if isinstance(value, str) and value.isdigit():
            v = int(value)
            if v in {1, 2, 3, 4, 5}:
                return v, v
        return None, 0

    @staticmethod
    def _get_track_config(rubric: dict[str, Any], track_key: Any) -> dict[str, Any]:
        tracks = rubric.get("tracks", {}) or {}
        resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
        track_cfg = tracks[resolved_track_key]
        return track_cfg

    @staticmethod
    def _resolve_track_key_for_scoring(rubric: dict[str, Any], track_key: Any) -> str:
        tracks = rubric.get("tracks", {}) or {}
        if isinstance(track_key, str) and track_key in tracks:
            return track_key
        if tracks:
            return next(iter(tracks))
        raise ReportingValidationError(
            "Invalid track key in draft. This track is missing from the current rubric."
        )

    @staticmethod
    def _calculate_percent(weighted_total: int, denominator: int) -> tuple[Optional[Decimal], float]:
        if denominator <= 0:
            return None, 0.0

        pct = (Decimal(weighted_total) * Decimal("100")) / Decimal(denominator)
        pct_rounded = pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return pct, float(pct_rounded)

    @staticmethod
    def _is_critical_priority(priority: Any) -> bool:
        return isinstance(priority, str) and priority.strip().lower() == "critical"

    @staticmethod
    def evaluate(rubric: dict[str, Any], track_key: Any, trait_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        resolved_track_key = ScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
        track_cfg = ScoringEngine._get_track_config(rubric, resolved_track_key)
        traits = [
            t for t in rubric["traits"]
            if "all" in t["applicable_tracks"] or resolved_track_key in t["applicable_tracks"]
        ]

        trait_q_overrides = rubric.get("trait_question_overrides", {}) or {}

        rows: list[dict[str, Any]] = []
        weighted_total = 0
        weighted_max_possible_included_traits = 0
        skipped_traits_count = 0
        scored_traits_count = 0

        critical_eq_1 = False
        critical_lt_3 = False
        disqualifier_present = False

        for trait in traits:
            tid = trait["id"]
            state = trait_results.get(tid, {}) or {}
            skipped = bool(state.get("skipped", False))

            dq = bool(state.get("absolute_disqualifier", False))
            disqualifier_present = disqualifier_present or dq

            raw_display, raw_for_math = ScoringEngine._coerce_raw_score(state.get("raw_score", None))

            weight = int(trait["weight"])
            weighted = 0
            if skipped:
                skipped_traits_count += 1
            else:
                scored_traits_count += 1
                weighted_max_possible_included_traits += 5 * weight
                weighted = raw_for_math * weight
                weighted_total += weighted

            is_critical = ScoringEngine._is_critical_priority(trait.get("priority"))
            has_scored_value = raw_display is not None
            if is_critical and not skipped and has_scored_value:
                if raw_for_math == 1:
                    critical_eq_1 = True
                if raw_for_math < 3:
                    critical_lt_3 = True

            pq = trait_q_overrides.get(tid) or trait["primary_question"]

            rows.append(
                {
                    "trait_id": tid,
                    "trait_name": trait["name"],
                    "priority": trait["priority"],
                    "weight": weight,
                    "skipped": skipped,
                    "raw_score": raw_display,
                    "raw_score_math": raw_for_math,
                    "weighted_score": weighted,
                    "question_notes": state.get("question_notes", ""),
                    "trait_notes": state.get("trait_notes", ""),
                    "verbatim_notes": state.get("verbatim_notes", ""),
                    "no_example_after_followups": bool(state.get("no_example_after_followups", False)),
                    "absolute_disqualifier": dq,
                    "primary_question": pq,
                }
            )

        configured_max_weighted = int(track_cfg["max_weighted_total"])

        # Use only the traits that were actually scored as the denominator.
        # This ensures skipped questions do not count against the candidate.
        effective_max_weighted = weighted_max_possible_included_traits or configured_max_weighted

        pct, percent_of_max = ScoringEngine._calculate_percent(weighted_total, effective_max_weighted)

        # Keep the Hire / Borderline / No Hire thresholds exactly the same.
        # We are only changing the denominator so skipped questions are excluded.
        logic_denominator = effective_max_weighted
        logic_pct, _logic_percent_of_max = ScoringEngine._calculate_percent(weighted_total, logic_denominator)

        percent_label = f"{percent_of_max}%"
        if weighted_max_possible_included_traits == 0:
            percent_label = "N/A (all questions skipped)"

        locked_rule: Optional[str] = None
        pct_for_logic = float(logic_pct) if logic_pct is not None else 0.0

        if disqualifier_present:
            outcome = "No Hire"
            locked_rule = "Any Absolute Disqualifier observed => Immediate NO HIRE"
        elif critical_eq_1:
            outcome = "No Hire"
            locked_rule = "Any Critical trait raw score = 1 => Immediate NO HIRE"
        elif pct_for_logic >= 80 and critical_lt_3:
            outcome = "No Hire"
            locked_rule = "Any Critical trait raw score < 3 => Cannot assign HIRE"
        elif pct_for_logic >= 80:
            outcome = "Hire"
        elif pct_for_logic >= 65:
            outcome = "Borderline"
        else:
            outcome = "No Hire"

        return {
            "rows": rows,
            "weighted_total": weighted_total,
            "configured_max_weighted_total": configured_max_weighted,
            "max_weighted_total": effective_max_weighted,
            "max_weighted_total_included_traits": weighted_max_possible_included_traits,
            "percent_of_max": percent_of_max,
            "percent_of_max_label": percent_label,
            "skipped_traits_count": skipped_traits_count,
            "scored_traits_count": scored_traits_count,
            "critical_eq_1": critical_eq_1,
            "critical_lt_3": critical_lt_3,
            "disqualifier_present": disqualifier_present,
            "locked_rule": locked_rule,
            "outcome": outcome,
        }


# =========================
# Draft persistence
# =========================

class DraftManager:
    """Saves and loads interview drafts as JSON under <base>/drafts."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.drafts_dir = self.base_dir / "drafts"
        self.final_dir = self.base_dir / "final"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)

    def save_draft(self, payload: dict[str, Any]) -> Path:
        candidate = payload.get("candidate", {}).get("name", "Unknown")
        safe = sanitize_filename(candidate or "Unknown")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.drafts_dir / f"draft-{stamp}-{safe}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    def load_draft(self, path: Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)


# =========================
# DOCX report export
# =========================

class DocxExporter:
    """Exports a finalized interview report to a single .docx file (one per candidate)."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _require_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            raise ReportingValidationError("Draft is missing candidate details; unable to export report.")
        return candidate

    @staticmethod
    def _require_candidate_field(candidate: dict[str, Any], field_name: str) -> str:
        value = candidate.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ReportingValidationError(f"Draft is missing required candidate field: '{field_name}'.")

    @staticmethod
    def _extract_full_candidate_transcript(payload: dict[str, Any]) -> str:
        transcript_segments: list[str] = []
        flow_transcript = payload.get("flow_transcript", []) or []
        for item in flow_transcript:
            if not isinstance(item, dict):
                continue
            tx = str(item.get("candidate_transcript") or "").strip()
            if tx:
                transcript_segments.append(tx)

        if transcript_segments:
            return "\n\n".join(transcript_segments).strip()

        audio_recording = payload.get("audio_recording", {}) or {}
        if isinstance(audio_recording, list):
            for item in audio_recording:
                if not isinstance(item, dict):
                    continue
                tx = str(item.get("candidate_transcript") or "").strip()
                if tx:
                    transcript_segments.append(tx)
        elif isinstance(audio_recording, dict):
            tx = str(audio_recording.get("candidate_transcript") or "").strip()
            if tx:
                transcript_segments.append(tx)

        return "\n\n".join(transcript_segments).strip()

    def export(
        self,
        rubric: dict[str, Any],
        payload: dict[str, Any],
        scoring: dict[str, Any],
        *,
        include_generated_summaries: bool = True,
    ) -> Path:
        candidate = self._require_candidate(payload)
        cname = self._require_candidate_field(candidate, "name")
        interview_date = self._require_candidate_field(candidate, "interview_date")
        track_key = self._require_candidate_field(candidate, "track")
        school = candidate.get("school", "")
        qualification = candidate.get("qualification", {}) or {}
        track_cfg = ScoringEngine._get_track_config(rubric, track_key)
        track_label = str(track_cfg.get("label") or track_key)

        summarizer = LocalInterviewSummarizer()

        doc = Document()
        doc.add_heading("Structured Behavioral Interview Report", level=1)

        doc.add_paragraph(f"Candidate Name: {cname}")
        doc.add_paragraph(f"Interview Date: {interview_date}")
        doc.add_paragraph(f"School/Location: {school}")
        doc.add_paragraph(f"Track: {track_label}")

        has_degree = qualification.get("has_degree", None)
        has_degree_text = "Yes" if has_degree is True else "No" if has_degree is False else "Not provided"
        degree_type = str(qualification.get("degree_type", "") or "").strip() or "N/A"
        degree_in_ece = "Yes" if qualification.get("degree_in_ece", False) else "No"
        ece_units = qualification.get("ece_units_completed", None)
        ece_units_text = "N/A" if ece_units is None else str(ece_units)
        infant_toddler = "Yes" if qualification.get("infant_toddler_class_completed", False) else "No"
        total_units = qualification.get("total_units_completed", None)
        total_units_text = "N/A" if total_units is None else str(total_units)
        years_experience = qualification.get("years_experience", None)
        years_experience_text = "N/A" if years_experience is None else str(years_experience)

        doc.add_heading("Candidate Education Summary", level=2)
        doc.add_paragraph(f"Has degree: {has_degree_text}")
        doc.add_paragraph(f"Degree type: {degree_type}")
        doc.add_paragraph(f"Degree in Early Childhood Education (ECE): {degree_in_ece}")
        doc.add_paragraph(f"ECE units completed: {ece_units_text}")
        doc.add_paragraph(f"Infant/toddler class completed: {infant_toddler}")
        doc.add_paragraph(f"Total units completed (if no degree): {total_units_text}")
        doc.add_paragraph(f"Years of experience: {years_experience_text}")

        doc.add_heading("Score Summary", level=2)
        table = doc.add_table(rows=1, cols=5)
        hdr = table.rows[0].cells
        hdr[0].text = "Trait"
        hdr[1].text = "Priority"
        hdr[2].text = "Weight"
        hdr[3].text = "Raw Score"
        hdr[4].text = "Weighted Score"

        for row in scoring["rows"]:
            cells = table.add_row().cells
            cells[0].text = row["trait_name"]
            cells[1].text = row["priority"]
            cells[2].text = str(row["weight"])
            if row.get("skipped", False):
                cells[3].text = "Skipped"
                cells[4].text = "Excluded"
                continue
            raw_display = row.get("raw_score", None)
            cells[3].text = "N/A" if raw_display is None else str(raw_display)
            cells[4].text = str(row["weighted_score"])

        doc.add_paragraph(f"Weighted Total: {scoring['weighted_total']} / {scoring['max_weighted_total']}")
        doc.add_paragraph(f"Skipped scored questions: {scoring.get('skipped_traits_count', 0)}")
        percent_of_max_label = scoring.get("percent_of_max_label", f"{scoring['percent_of_max']}%")
        doc.add_paragraph(f"Percent of Max: {percent_of_max_label}")
        doc.add_paragraph(f"Final Outcome: {scoring['outcome']}")

        doc.add_heading("Override Summary", level=2)
        doc.add_paragraph(f"Any Critical trait = 1: {'Yes' if scoring['critical_eq_1'] else 'No'}")
        doc.add_paragraph(f"Any Absolute Disqualifier observed: {'Yes' if scoring['disqualifier_present'] else 'No'}")
        doc.add_paragraph(f"Outcome lock rule: {scoring['locked_rule'] if scoring['locked_rule'] else 'None'}")

        doc.add_heading("Executive Candidate Summary", level=2)
        executive_summary = "Summary pending/failed"
        if include_generated_summaries:
            executive_summary = summarizer.summarize_executive(self._extract_full_candidate_transcript(payload))
        doc.add_paragraph(executive_summary)

        doc.add_heading("Interview Flow (Scored + Non-scored in asked order)", level=2)
        flow_transcript = payload.get("flow_transcript", []) or []
        if not flow_transcript:
            doc.add_paragraph("No flow transcript available.")
        else:
            for i, item in enumerate(flow_transcript, start=1):
                itype = (item.get("type") or "").strip()
                title = (item.get("title") or "").strip()
                qtext = (item.get("question") or "").strip()
                doc.add_paragraph(f"{i}. {title} ({itype})")
                if qtext:
                    doc.add_paragraph(f"Question: {qtext}")
                cand_tx = (item.get("candidate_transcript") or "").strip()

                if itype == "trait":
                    answer_summary = "Summary pending/failed"
                    if include_generated_summaries:
                        answer_summary = summarizer.summarize_answer(cand_tx, qtext)
                    doc.add_paragraph(f"Answer Summary: {answer_summary}")
                    if cand_tx:
                        doc.add_paragraph(f"Candidate Answer (auto-transcribed): {cand_tx}")
                    else:
                        doc.add_paragraph("Candidate Answer (auto-transcribed): (No candidate transcript captured)")
                    raw = item.get("raw_score", None)
                    doc.add_paragraph(f"Raw Score: {'N/A' if raw is None else raw}")
                    ne = "Yes" if item.get("no_example_after_followups") else "No"
                    doc.add_paragraph(f"No example after follow-ups: {ne}")
                    doc.add_paragraph(f"Question Notes: {item.get('question_notes','')}")
                    doc.add_paragraph(f"Trait Notes: {item.get('trait_notes','')}")
                    doc.add_paragraph(f"Verbatim quote/notes: {item.get('verbatim_notes','')}")
                    dq = "Yes" if item.get("absolute_disqualifier") else "No"
                    doc.add_paragraph(f"Absolute Disqualifier Checked: {dq}")
                else:
                    answer_summary = "Summary pending/failed"
                    if include_generated_summaries:
                        answer_summary = summarizer.summarize_answer(cand_tx, qtext)
                    doc.add_paragraph(f"Answer Summary: {answer_summary}")
                    if cand_tx:
                        doc.add_paragraph(f"Candidate Answer (auto-transcribed): {cand_tx}")
                    else:
                        doc.add_paragraph("Candidate Answer (auto-transcribed): (No candidate transcript captured)")

        doc.add_heading("Candidate Transcript (Full)", level=2)
        full_candidate_transcript = self._extract_full_candidate_transcript(payload)
        if full_candidate_transcript:
            doc.add_paragraph(full_candidate_transcript)
        else:
            doc.add_paragraph("No full candidate transcript available.")

        doc.add_heading("Trait-by-Trait Detail", level=2)
        for idx, row in enumerate(scoring["rows"], start=1):
            doc.add_heading(f"{idx}. {row['trait_name']}", level=3)
            doc.add_paragraph(f"Priority: {row['priority']} | Weight: x{row['weight']}")
            doc.add_paragraph(f"Primary Question: {row['primary_question']}")
            raw_display = row.get("raw_score", None)
            doc.add_paragraph(f"Selected Raw Score: {'N/A' if raw_display is None else raw_display}")
            ne = "Yes" if row.get("no_example_after_followups") else "No"
            doc.add_paragraph(f"No example after follow-ups: {ne}")
            doc.add_paragraph(f"Question Notes: {row['question_notes']}")
            doc.add_paragraph(f"Trait Notes: {row['trait_notes']}")
            doc.add_paragraph(f"Verbatim quote/notes: {row['verbatim_notes']}")

        doc.add_heading("Custom Questions (Non-scored)", level=2)
        custom_answers = payload.get("custom_answers", []) or []
        if not custom_answers:
            doc.add_paragraph("None.")
        else:
            for i, item in enumerate(custom_answers, start=1):
                qtext = (item.get("question_text") or "").strip()
                ans = (item.get("answer") or "").strip()
                doc.add_paragraph(f"{i}. {qtext}")
                doc.add_paragraph(f"Answer: {ans if ans else 'N/A'}")

        doc.add_heading("Global Disqualifiers", level=2)
        for d in rubric["absolute_disqualifiers"]:
            doc.add_paragraph(f"- {d}")

        doc.add_paragraph("Observed disqualifier evidence (from verbatim notes):")
        evidence_added = False
        for row in scoring["rows"]:
            if row["absolute_disqualifier"] and (row.get("verbatim_notes") or "").strip():
                doc.add_paragraph(f"- {row['trait_name']}: {row['verbatim_notes'].strip()}")
                evidence_added = True
        if not evidence_added:
            doc.add_paragraph("- None recorded")

        school_part = sanitize_filename(school) if school else "UnknownSchool"
        filename = f"{interview_date} - {school_part} - {sanitize_filename(cname)} - Interview.docx"
        out_path = self.output_dir / filename
        return self._save_document(doc, out_path)

    def _save_document(self, doc: Document, out_path: Path) -> Path:
        try:
            doc.save(out_path)
            return out_path
        except PermissionError:
            for suffix in range(1, 6):
                candidate = out_path.with_name(f"{out_path.stem} (updated {suffix}){out_path.suffix}")
                try:
                    doc.save(candidate)
                    return candidate
                except PermissionError:
                    continue
            raise


# =========================
# Interview state container
# =========================
