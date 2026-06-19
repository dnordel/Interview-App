import unittest

from scoring_reporting import (
    is_supported_document_path,
    missing_required_docs,
    normalize_referral_packet,
    validate_referral_packet,
)


class TestReferralPacket(unittest.TestCase):
    def test_missing_required_docs_returns_labels_in_order(self):
        missing = missing_required_docs({"resume_path": "resume.pdf"})
        self.assertEqual(missing, ["Interview notes document"])

    def test_missing_required_docs_with_both_required_docs_returns_empty(self):
        missing = missing_required_docs(
            {
                "resume_path": "resume.pdf",
                "interview_notes_document_path": "notes.docx",
            }
        )
        self.assertEqual(missing, [])

    def test_validate_referral_packet_success(self):
        packet = {
            "resume_path": "resume.pdf",
            "interview_notes_document_path": "notes.docx",
            "transcript_path": "candidate.txt",
        }
        ok, missing = validate_referral_packet(packet)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_normalize_referral_packet_defaults(self):
        normalized = normalize_referral_packet(None)
        self.assertEqual(
            normalized,
            {
                "resume_path": "",
                "interview_notes_document_path": "",
                "interview_notes_path": "",
                "transcript_path": "",
            },
        )

    def test_document_extension_filter(self):
        self.assertTrue(is_supported_document_path("/tmp/resume.PDF"))
        self.assertFalse(is_supported_document_path("/tmp/archive.zip"))

    def test_normalize_referral_packet_backfills_canonical_path_from_legacy_fields(self):
        normalized = normalize_referral_packet(
            {
                "resume_path": "resume.pdf",
                "interview_notes_path": "notes.docx",
                "transcript_path": "transcript.txt",
            }
        )
        self.assertEqual(normalized["interview_notes_document_path"], "notes.docx")

    def test_normalize_referral_packet_backfills_canonical_path_from_transcript(self):
        normalized = normalize_referral_packet({"transcript_path": "transcript.txt"})
        self.assertEqual(normalized["interview_notes_document_path"], "transcript.txt")


if __name__ == "__main__":
    unittest.main()
