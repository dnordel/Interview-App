from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from staffing_referral_queue import StaffingReferralQueueStore


def _payload(history_id: str, school: str) -> dict[str, str]:
    return {
        "history_id": history_id,
        "candidate_name": f"Candidate {history_id}",
        "school": school,
        "position": "Teacher",
    }


def test_referral_queue_pops_matching_school_and_preserves_other_schools(tmp_path: Path) -> None:
    store = StaffingReferralQueueStore(tmp_path / "staffing_referrals.sqlite3")
    store.append(_payload("hist-palmdale", "Palmdale"))
    store.append(_payload("hist-hawthorne", "Hawthorne"), operation="director_candidate_referral_dismissal")

    palmdale = store.pop_for_school("Palmdale")
    hawthorne = store.pop_for_school("Hawthorne")

    assert [record["history_id"] for record in palmdale] == ["hist-palmdale"]
    assert palmdale[0]["_operation"] == "director_candidate_referral"
    assert [record["history_id"] for record in hawthorne] == ["hist-hawthorne"]
    assert hawthorne[0]["_operation"] == "director_candidate_referral_dismissal"


def test_referral_queue_imports_legacy_jsonl_once_and_skips_malformed_lines(tmp_path: Path) -> None:
    db_path = tmp_path / "staffing_referrals.sqlite3"
    legacy_path = tmp_path / "staffing_referrals.pending.jsonl"
    legacy_path.write_text(
        "\n".join(
            [
                json.dumps({"operation": "director_candidate_referral", "payload": _payload("hist-import", "Palmdale")}),
                "{not-json",
                json.dumps({"payload": "wrong-shape"}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    store = StaffingReferralQueueStore(db_path, legacy_jsonl_path=legacy_path)

    assert [record["history_id"] for record in store.pop_for_school("Palmdale")] == ["hist-import"]
    assert store.pop_for_school("Palmdale") == []
    assert legacy_path.exists()
    assert legacy_path.with_suffix(legacy_path.suffix + ".imported").exists()
    assert not legacy_path.with_suffix(legacy_path.suffix + ".importing").exists()


def test_referral_queue_concurrent_appends_preserve_all_events(tmp_path: Path) -> None:
    store = StaffingReferralQueueStore(tmp_path / "staffing_referrals.sqlite3")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: store.append(_payload(f"hist-{index}", "Palmdale")), range(40)))

    records = store.pop_for_school("Palmdale")

    assert {record["history_id"] for record in records} == {f"hist-{index}" for index in range(40)}
