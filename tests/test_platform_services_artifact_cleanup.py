
from pathlib import Path

from platform_services import cleanup_stale_artifacts, delete_recording_artifacts


def test_delete_recording_artifacts_removes_known_files(tmp_path: Path):
    wav = tmp_path / "Candidate_A_Q1_trait_x.wav"
    jsonl = tmp_path / "Candidate_A_Q1_trait_x_transcript.jsonl"
    wav.write_text("x")
    jsonl.write_text("x")

    flow_recordings = {
        0: {
            "attempts": [
                {
                    "mic_wav": str(wav),
                    "transcript_jsonl": str(jsonl),
                }
            ]
        }
    }

    deleted = delete_recording_artifacts(tmp_path, flow_recordings)
    assert wav in deleted
    assert jsonl in deleted
    assert not wav.exists()
    assert not jsonl.exists()


def test_cleanup_stale_artifacts_only_deletes_candidate_prefixed(tmp_path: Path):
    stale = tmp_path / "Candidate_A_old.wav"
    keep = tmp_path / "question_overrides.json"
    stale.write_text("x")
    keep.write_text("{}")

    deleted = cleanup_stale_artifacts(tmp_path)
    assert stale in deleted
    assert not stale.exists()
    assert keep.exists()
