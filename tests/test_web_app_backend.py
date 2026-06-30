from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from web_app_backend import (
    WebAppBackendError,
    build_handler,
    list_web_drafts,
    load_web_draft,
    load_offer_settings,
    load_history_rows,
    load_question_overrides,
    load_bootstrap_payload,
    normalize_web_draft_payload,
    save_question_overrides,
    save_offer_settings,
    save_web_draft,
    save_web_recording,
    score_web_draft_preview,
    finalize_web_draft,
    update_history_offer_status,
)
import web_app_backend
from http.server import ThreadingHTTPServer


def test_load_bootstrap_payload_returns_existing_app_data_shapes():
    payload = load_bootstrap_payload()

    assert isinstance(payload["rubric"], dict)
    assert isinstance(payload["overrides"], dict)
    assert isinstance(payload["history"], list)
    assert isinstance(payload["offerSettings"], dict)


def test_save_question_overrides_normalizes_and_persists_existing_shape(tmp_path, monkeypatch):
    overrides_path = tmp_path / "question_overrides.json"
    monkeypatch.setattr(web_app_backend, "QUESTIONS_OVERRIDE_PATH", overrides_path)

    saved = save_question_overrides(
        {
            "trait_question_overrides": {"trait_1": "  Updated prompt  "},
            "custom_questions": {"preschool": [{"id": "cq_1", "text": "  Custom prompt? ", "order": 2}]},
            "track_question_flow": {"preschool": [{"type": "TRAIT", "id": "trait_1"}, {"type": "custom", "id": "cq_1"}]},
        }
    )

    assert saved["trait_question_overrides"] == {"trait_1": "Updated prompt"}
    assert saved["custom_questions"]["preschool"][0]["text"] == "Custom prompt?"
    assert saved["track_question_flow"]["preschool"] == [{"type": "trait", "id": "trait_1"}, {"type": "custom", "id": "cq_1"}]
    assert load_question_overrides()["trait_question_overrides"]["trait_1"] == "Updated prompt"


def test_save_question_overrides_rejects_invalid_shape_without_payload_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app_backend, "QUESTIONS_OVERRIDE_PATH", tmp_path / "question_overrides.json")

    with pytest.raises(WebAppBackendError, match="not valid") as exc_info:
        save_question_overrides({"custom_questions": {"preschool": [{"id": "secret", "text": "private", "order": -1}]}})

    assert "private" not in str(exc_info.value)


def test_save_offer_settings_normalizes_and_persists_existing_shape(tmp_path, monkeypatch):
    settings_path = tmp_path / "school_offer_settings.json"
    monkeypatch.setattr(web_app_backend, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)

    saved = save_offer_settings(
        {
            " Palmdale ": {
                "full_time_template": " full.docx ",
                "part_time_template": " part.docx ",
                "offer_output_dir": " offers ",
                "interview_notes_dir": r" \Dropbox\LPL PMD Office Shared\Staff\Candidates ",
                "ignored": "drop me",
            }
        }
    )

    assert saved == {
        "Palmdale": {
            "full_time_template": "full.docx",
            "part_time_template": "part.docx",
            "offer_output_dir": "offers",
            "interview_notes_dir": r"\Dropbox\LPL PMD Office Shared\Staff\Candidates",
        }
    }
    assert load_offer_settings()["Palmdale"]["offer_output_dir"] == "offers"
    assert load_offer_settings()["Palmdale"]["interview_notes_dir"] == r"\Dropbox\LPL PMD Office Shared\Staff\Candidates"


def test_save_offer_settings_rejects_invalid_shape_without_value_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app_backend, "SCHOOL_OFFER_SETTINGS_PATH", tmp_path / "school_offer_settings.json")

    with pytest.raises(WebAppBackendError, match="not valid") as exc_info:
        save_offer_settings({"Palmdale": "private path"})

    assert "private path" not in str(exc_info.value)


def test_finalize_web_draft_writes_report_to_school_interview_notes_dir(tmp_path, monkeypatch):
    dropbox_root = tmp_path / "Dropbox"
    base_dir = dropbox_root / "App" / "interviews"
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Palmdale": {
                    "interview_notes_dir": r"\Dropbox\LPL PMD Office Shared\Staff\Candidates",
                }
            }
        ),
        encoding="utf-8",
    )
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps({"traits": [], "tracks": {"preschool": {"label": "Preschool", "max_weighted_total": 10}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app_backend, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(web_app_backend, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(web_app_backend, "QUESTIONS_OVERRIDE_PATH", tmp_path / "question_overrides.json")
    monkeypatch.setattr(web_app_backend, "INTERVIEW_HISTORY_PATH", tmp_path / "interview_history.json")
    monkeypatch.setattr(
        web_app_backend.ScoringEngine,
        "evaluate",
        staticmethod(
            lambda _rubric, _track, _inputs: {
                "rows": [],
                "weighted_total": 0,
                "max_weighted_total": 10,
                "percent_of_max": 0.0,
                "critical_eq_1": False,
                "disqualifier_present": False,
                "locked_rule": None,
                "outcome": "No Hire",
            }
        ),
    )

    class FakeExporter:
        def __init__(self, output_dir: Path):
            self.output_dir = Path(output_dir)

        def export(self, _rubric, payload, _scoring):
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{payload['candidate']['name']} - Interview.docx"
            path.write_text("docx", encoding="utf-8")
            return path

    monkeypatch.setattr(web_app_backend, "DocxExporter", FakeExporter)

    result = finalize_web_draft(
        {
            "candidate": {
                "candidate_name": "Web Candidate",
                "interview_date": "2026-06-17",
                "school": "Palmdale",
                "track": "preschool",
            },
            "trait_inputs": {},
        },
        base_dir=base_dir,
    )

    expected_dir = dropbox_root / "LPL PMD Office Shared" / "Staff" / "Candidates"
    assert Path(result["report_path"]).parent == expected_dir


def test_update_history_offer_status_updates_existing_history_row(tmp_path, monkeypatch):
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist_1",
                    "candidate_name": "Candidate One",
                    "interview_date": "2026-06-17",
                    "saved_at": "2026-06-17T12:00:00Z",
                    "offer_status": "not_generated",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app_backend, "INTERVIEW_HISTORY_PATH", history_path)

    result = update_history_offer_status("hist_1", "offer_sent")

    assert result["updated"] is True
    assert result["history"][0]["offer_status"] == "offer_sent"
    assert load_history_rows()[0]["offer_status"] == "offer_sent"


def test_update_history_offer_status_rejects_missing_row_without_candidate_echo(tmp_path, monkeypatch):
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(json.dumps([{"history_id": "hist_1", "candidate_name": "Private Candidate"}]), encoding="utf-8")
    monkeypatch.setattr(web_app_backend, "INTERVIEW_HISTORY_PATH", history_path)

    with pytest.raises(WebAppBackendError, match="not found") as exc_info:
        update_history_offer_status("missing", "offer_sent")

    assert "Private Candidate" not in str(exc_info.value)


def test_normalize_web_draft_payload_maps_browser_fields_to_desktop_draft_shape():
    payload = normalize_web_draft_payload(
        {
            "candidate": {
                "candidate_name": " Ada Lovelace ",
                "interview_date": "2026-06-17",
                "school": "Hawthorne",
                "track": "preschool",
            },
            "current_flow_index": 4,
            "trait_inputs": {"trait_1": {"raw_score": 5}},
            "custom_inputs": {"Why-ECE": {"answer": "Because."}},
        }
    )

    assert payload["candidate"]["name"] == "Ada Lovelace"
    assert payload["candidate"]["interview_date"] == "2026-06-17"
    assert payload["current_index"] == 4
    assert payload["trait_inputs"]["trait_1"]["raw_score"] == 5
    assert payload["custom_inputs"]["Why-ECE"]["answer"] == "Because."
    assert payload["flow_time_marks"] == []


def test_normalize_web_draft_payload_requires_candidate_name():
    with pytest.raises(WebAppBackendError, match="candidate name is required"):
        normalize_web_draft_payload({"candidate": {"candidate_name": " "}})


def test_score_web_draft_preview_uses_existing_scoring_engine(tmp_path, monkeypatch):
    rubric_path = write_preview_rubric(tmp_path)
    monkeypatch.setattr(web_app_backend, "DEFAULT_RUBRIC_PATH", rubric_path)

    preview = score_web_draft_preview(
        {
            "candidate": {"candidate_name": "Preview Candidate", "track": "preschool"},
            "trait_inputs": {
                "trait_1": {"raw_score": 5},
                "trait_2": {"raw_score": 3, "no_example_after_followups": True},
            },
        }
    )

    assert preview["weighted_total"] == 11
    assert preview["configured_max_weighted_total"] == 20
    assert preview["max_weighted_total"] == 15
    assert preview["percent_of_max_label"] == "73.33%"
    assert preview["scored_traits_count"] == 2
    assert preview["rows"][1]["no_example_after_followups"] is True


def test_score_web_draft_preview_rejects_bad_payload_without_note_echo(tmp_path, monkeypatch):
    rubric_path = write_preview_rubric(tmp_path)
    monkeypatch.setattr(web_app_backend, "DEFAULT_RUBRIC_PATH", rubric_path)

    with pytest.raises(WebAppBackendError) as exc_info:
        score_web_draft_preview({"candidate": {"candidate_name": ""}, "trait_inputs": {"trait_1": {"question_notes": "private"}}})

    assert "private" not in str(exc_info.value)


def test_finalize_web_draft_exports_docx_and_appends_history(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app_backend, "DEFAULT_RUBRIC_PATH", write_preview_rubric(tmp_path))
    monkeypatch.setattr(web_app_backend, "QUESTIONS_OVERRIDE_PATH", tmp_path / "question_overrides.json")
    monkeypatch.setattr(web_app_backend, "INTERVIEW_HISTORY_PATH", tmp_path / "interview_history.json")

    result = finalize_web_draft(
        {
            "candidate": {
                "candidate_name": "Finalize Candidate",
                "interview_date": "2026-06-17",
                "school": "Palmdale",
                "track": "preschool",
            },
            "trait_inputs": {
                "trait_1": {"raw_score": 5, "question_notes": "Concrete evidence."},
                "trait_2": {"raw_score": 4, "trait_notes": "Good support."},
            },
            "custom_inputs": {},
            "flow_recordings": [{"flow_index": 0, "audio_path": "saved.webm", "candidate_transcript": ""}],
        },
        base_dir=tmp_path,
    )

    report_path = Path(result["report_path"])
    integration_path = Path(result["integration_path"])
    history = load_history_rows()
    assert report_path.is_file()
    assert integration_path.is_file()
    assert report_path.parent == tmp_path / "Indeed Interview Notes"
    assert integration_path.parent == tmp_path / "integration_exports"
    assert result["scorePreview"]["outcome"] == "Hire"
    assert result["director_packet"]["event"] == "director_referral_packet"
    assert result["director_packet"]["candidate"]["name"] == "Finalize Candidate"
    assert result["director_packet"]["documents"]["final_report_path"] == str(report_path)
    assert result["director_packet"]["documents"]["integration_export_path"] == str(integration_path)
    assert result["history_entry"]["candidate_name"] == "Finalize Candidate"
    assert history[0]["saved_report_path"] == str(report_path)
    assert history[0]["integration_export_path"] == str(integration_path)
    assert history[0]["flow_recordings"][0]["audio_path"] == "saved.webm"
    assert history[0]["offer_status"] == "not_generated"
    stored_export = json.loads(integration_path.read_text(encoding="utf-8"))
    assert stored_export["candidate"]["name"] == "Finalize Candidate"
    assert stored_export["decision"] == "hire"


def test_finalize_web_draft_rejects_missing_required_candidate_fields_without_note_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app_backend, "DEFAULT_RUBRIC_PATH", write_preview_rubric(tmp_path))
    monkeypatch.setattr(web_app_backend, "QUESTIONS_OVERRIDE_PATH", tmp_path / "question_overrides.json")

    with pytest.raises(WebAppBackendError) as exc_info:
        finalize_web_draft(
            {
                "candidate": {"candidate_name": "Finalize Candidate", "track": "preschool"},
                "trait_inputs": {"trait_1": {"raw_score": 5, "question_notes": "private"}},
            },
            base_dir=tmp_path,
        )

    assert "private" not in str(exc_info.value)


def test_save_web_draft_uses_existing_draft_manager_under_base_dir(tmp_path):
    result = save_web_draft(
        {
            "candidate": {"candidate_name": "Grace Hopper", "track": "preschool"},
            "current_flow_index": 2,
            "trait_inputs": {},
            "custom_inputs": {},
        },
        base_dir=tmp_path,
    )

    draft_path = Path(result["draft_path"])
    assert draft_path.is_file()
    assert draft_path.parent == tmp_path / "drafts"
    saved = json.loads(draft_path.read_text(encoding="utf-8"))
    assert saved["candidate"]["name"] == "Grace Hopper"
    assert saved["current_index"] == 2


def test_save_web_recording_persists_audio_under_base_dir(tmp_path):
    result = save_web_recording(
        {
            "flow_index": 2,
            "question_id": "trait_1",
            "mime_type": "audio/webm;codecs=opus",
            "data_base64": "UklGRg==",
        },
        base_dir=tmp_path,
    )

    audio_path = Path(result["audio_path"])
    assert audio_path.is_file()
    assert audio_path.parent == tmp_path / "web_recordings"
    assert result["flow_index"] == 2
    assert result["question_id"] == "trait_1"
    assert result["mime_type"] == "audio/webm"
    assert result["byte_count"] == 4
    assert result["candidate_transcript"] == ""


def test_save_web_recording_rejects_bad_audio_without_payload_echo(tmp_path):
    with pytest.raises(WebAppBackendError) as exc_info:
        save_web_recording(
            {
                "flow_index": 1,
                "question_id": "private_question",
                "mime_type": "text/plain",
                "data_base64": "private-audio",
            },
            base_dir=tmp_path,
        )

    assert "private-audio" not in str(exc_info.value)


def test_list_web_drafts_returns_metadata_without_notes(tmp_path):
    first = save_web_draft(
        {
            "candidate": {"candidate_name": "First Candidate", "track": "preschool"},
            "trait_inputs": {"trait_1": {"question_notes": "private note"}},
        },
        base_dir=tmp_path,
    )
    second = save_web_draft(
        {
            "candidate": {"candidate_name": "Second Candidate", "track": "infant_toddler"},
            "current_flow_index": 3,
        },
        base_dir=tmp_path,
    )
    os.utime(first["draft_path"], (10, 10))
    os.utime(second["draft_path"], (20, 20))

    rows = list_web_drafts(base_dir=tmp_path)

    assert len(rows) == 2
    assert rows[0]["candidate_name"] == "Second Candidate"
    assert rows[0]["draft_name"].endswith(".json")
    assert "private note" not in json.dumps(rows)


def test_load_web_draft_constrains_name_to_drafts_dir(tmp_path):
    saved = save_web_draft({"candidate": {"candidate_name": "Resume Candidate"}}, base_dir=tmp_path)

    payload = load_web_draft(saved["draft_name"], base_dir=tmp_path)

    assert payload["candidate"]["name"] == "Resume Candidate"
    with pytest.raises(WebAppBackendError):
        load_web_draft("../outside.json", base_dir=tmp_path)


def test_backend_serves_health_static_and_draft_api(tmp_path):
    overrides_path = tmp_path / "question_overrides.json"
    offer_settings_path = tmp_path / "school_offer_settings.json"
    old_path = web_app_backend.QUESTIONS_OVERRIDE_PATH
    old_offer_path = web_app_backend.SCHOOL_OFFER_SETTINGS_PATH
    old_history_path = web_app_backend.INTERVIEW_HISTORY_PATH
    old_rubric_path = web_app_backend.DEFAULT_RUBRIC_PATH
    web_app_backend.QUESTIONS_OVERRIDE_PATH = overrides_path
    web_app_backend.SCHOOL_OFFER_SETTINGS_PATH = offer_settings_path
    web_app_backend.INTERVIEW_HISTORY_PATH = tmp_path / "interview_history.json"
    web_app_backend.DEFAULT_RUBRIC_PATH = write_preview_rubric(tmp_path)
    web_app_backend.INTERVIEW_HISTORY_PATH.write_text(
        json.dumps([{"history_id": "hist_1", "candidate_name": "Web Candidate", "offer_status": "not_generated"}]),
        encoding="utf-8",
    )
    try:
        with running_backend(tmp_path) as base_url:
            health = read_json(f"{base_url}/api/health")
            assert health == {"ok": True}

            index = urlopen(f"{base_url}/web/app/").read().decode("utf-8")
            assert "Preschool Interview Web App" in index

            question_request = Request(
                f"{base_url}/api/question-overrides",
                data=json.dumps({"trait_question_overrides": {"trait_1": "Web prompt"}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            question_result = json.loads(urlopen(question_request).read().decode("utf-8"))
            assert question_result["overrides"]["trait_question_overrides"]["trait_1"] == "Web prompt"

            loaded_questions = read_json(f"{base_url}/api/question-overrides")
            assert loaded_questions["overrides"]["trait_question_overrides"]["trait_1"] == "Web prompt"

            offer_request = Request(
                f"{base_url}/api/offer-settings",
                data=json.dumps({"Palmdale": {"offer_output_dir": "offers"}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            offer_result = json.loads(urlopen(offer_request).read().decode("utf-8"))
            assert offer_result["offerSettings"]["Palmdale"]["offer_output_dir"] == "offers"

            loaded_offer_settings = read_json(f"{base_url}/api/offer-settings")
            assert loaded_offer_settings["offerSettings"]["Palmdale"]["offer_output_dir"] == "offers"

            history_request = Request(
                f"{base_url}/api/history/hist_1/offer-status",
                data=json.dumps({"offer_status": "offer_sent"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            history_result = json.loads(urlopen(history_request).read().decode("utf-8"))
            assert history_result["history"][0]["offer_status"] == "offer_sent"

            loaded_history = read_json(f"{base_url}/api/history")
            assert loaded_history["history"][0]["offer_status"] == "offer_sent"

            recording_request = Request(
                f"{base_url}/api/recordings",
                data=json.dumps(
                    {
                        "flow_index": 1,
                        "question_id": "trait_1",
                        "mime_type": "audio/webm",
                        "data_base64": "UklGRg==",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            recording_result = json.loads(urlopen(recording_request).read().decode("utf-8"))
            assert Path(recording_result["audio_path"]).is_file()
            assert recording_result["candidate_transcript"] == ""

            score_request = Request(
                f"{base_url}/api/score-preview",
                data=json.dumps(
                    {
                        "candidate": {"candidate_name": "Web Candidate", "track": "preschool"},
                        "trait_inputs": {"trait_1": {"raw_score": 5}, "trait_2": {"raw_score": 4}},
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            score_result = json.loads(urlopen(score_request).read().decode("utf-8"))
            assert score_result["scorePreview"]["outcome"] == "Hire"

            finalize_request = Request(
                f"{base_url}/api/finalize",
                data=json.dumps(
                    {
                        "candidate": {
                            "candidate_name": "Web Candidate",
                            "interview_date": "2026-06-17",
                            "track": "preschool",
                        },
                        "trait_inputs": {"trait_1": {"raw_score": 5}, "trait_2": {"raw_score": 4}},
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            finalize_result = json.loads(urlopen(finalize_request).read().decode("utf-8"))
            assert Path(finalize_result["report_path"]).is_file()
            assert Path(finalize_result["integration_path"]).is_file()
            assert finalize_result["director_packet"]["candidate"]["name"] == "Web Candidate"
            assert finalize_result["history_entry"]["candidate_name"] == "Web Candidate"

            request = Request(
                f"{base_url}/api/drafts",
                data=json.dumps({"candidate": {"candidate_name": "Web Candidate"}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            result = json.loads(urlopen(request).read().decode("utf-8"))
            assert result["draft_name"].startswith("draft-")
            assert (tmp_path / "drafts" / result["draft_name"]).is_file()

            drafts = read_json(f"{base_url}/api/drafts")
            assert drafts["drafts"][0]["candidate_name"] == "Web Candidate"

            loaded = read_json(f"{base_url}/api/drafts/{quote(result['draft_name'])}")
            assert loaded["draft"]["candidate"]["name"] == "Web Candidate"
    finally:
        web_app_backend.QUESTIONS_OVERRIDE_PATH = old_path
        web_app_backend.SCHOOL_OFFER_SETTINGS_PATH = old_offer_path
        web_app_backend.INTERVIEW_HISTORY_PATH = old_history_path
        web_app_backend.DEFAULT_RUBRIC_PATH = old_rubric_path


def test_backend_rejects_bad_draft_payload_without_candidate_echo(tmp_path):
    with running_backend(tmp_path) as base_url:
        request = Request(
            f"{base_url}/api/drafts",
            data=json.dumps({"candidate": {"candidate_name": ""}, "notes": "private"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request)
        body = exc_info.value.read().decode("utf-8")

    assert exc_info.value.code == 400
    assert "candidate name is required" in body
    assert "private" not in body


def read_json(url: str) -> dict:
    return json.loads(urlopen(url).read().decode("utf-8"))


def write_preview_rubric(tmp_path: Path) -> Path:
    path = tmp_path / "rubric.json"
    path.write_text(
        json.dumps(
            {
                "tracks": {"preschool": {"label": "Preschool", "max_weighted_total": 20}},
                "absolute_disqualifiers": [],
                "traits": [
                    {
                        "id": "trait_1",
                        "name": "Trait One",
                        "priority": "Critical",
                        "weight": 1,
                        "applicable_tracks": ["all"],
                        "primary_question": "Question one?",
                    },
                    {
                        "id": "trait_2",
                        "name": "Trait Two",
                        "priority": "Standard",
                        "weight": 2,
                        "applicable_tracks": ["preschool"],
                        "primary_question": "Question two?",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@contextmanager
def running_backend(base_dir: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(base_dir=base_dir))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
