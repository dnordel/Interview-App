import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from director_referral_service import (
    DirectorReferralError,
    append_communication_log,
    build_director_packet,
    send_director_packet,
)


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: str = "ok"):
        self.status = status
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return self._body


class TestDirectorReferralService(unittest.TestCase):
    def test_build_director_packet_includes_required_sections(self):
        packet = build_director_packet(
            payload={"candidate": {"name": "Ada", "interview_date": "2026-01-01", "school": "PS 1", "track": "lead"}},
            scoring={"outcome": "Hire", "percent_of_max": 90.0, "weighted_total": 45, "max_weighted_total": 50},
            report_path=Path("/tmp/report.docx"),
            integration_path=Path("/tmp/integration.json"),
            referral_packet={
                "resume_path": "/tmp/resume.pdf",
                "interview_notes_path": "/tmp/notes.docx",
                "transcript_path": "/tmp/transcript.txt",
            },
        )
        self.assertEqual(packet["candidate"]["name"], "Ada")
        self.assertIn("documents", packet)
        self.assertEqual(packet["documents"]["resume_path"], "/tmp/resume.pdf")

    def test_append_communication_log_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            out = append_communication_log(Path(td), {"event": "director_referral_sent", "timestamp": "now", "status": "success"}, candidate_name="Ada")
            self.assertTrue(out.exists())
            line = out.read_text(encoding="utf-8").strip()
            parsed = json.loads(line)
            self.assertEqual(parsed["event"], "director_referral_sent")

    def test_send_director_packet_rejects_http_endpoint(self):
        with self.assertRaises(DirectorReferralError):
            send_director_packet({"event": "director_referral_packet"}, "http://example.org/referrals")

    def test_send_director_packet_rejects_invalid_https_endpoint(self):
        with self.assertRaises(DirectorReferralError):
            send_director_packet({"event": "director_referral_packet"}, "https://")

    @patch("director_referral_service.request.urlopen")
    def test_send_director_packet_accepts_trusted_https_endpoint(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse(status=201, body="accepted")
        result = send_director_packet(
            {"event": "director_referral_packet"},
            "https://trusted.example.org/referrals",
            allowed_hosts={"trusted.example.org"},
        )
        self.assertEqual(result["status_code"], 201)
        self.assertEqual(result["response"], "accepted")

    def test_send_director_packet_rejects_non_allowlisted_host(self):
        with self.assertRaises(DirectorReferralError):
            send_director_packet(
                {"event": "director_referral_packet"},
                "https://untrusted.example.org/referrals",
                allowed_hosts={"trusted.example.org"},
            )


if __name__ == "__main__":
    unittest.main()
