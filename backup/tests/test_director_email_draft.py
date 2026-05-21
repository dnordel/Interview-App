import unittest

from director_email_draft import build_mailto_url


class TestDirectorEmailDraft(unittest.TestCase):
    def test_build_mailto_url_includes_subject_and_body(self):
        result = build_mailto_url(
            subject="Director Referral: Ada",
            body="Line one\nLine two",
            to_recipients="director@example.org",
        )
        self.assertTrue(result.startswith("mailto:director@example.org?"))
        self.assertIn("subject=Director%20Referral%3A%20Ada", result)
        self.assertIn("body=Line%20one%0ALine%20two", result)

    def test_build_mailto_url_without_query(self):
        self.assertEqual(build_mailto_url(subject="", body="", to_recipients=""), "mailto:")

    def test_build_mailto_url_encodes_recipient_safely(self):
        result = build_mailto_url(
            subject="Subject",
            body="Body",
            to_recipients="director+team name@example.org",
        )
        self.assertTrue(result.startswith("mailto:director%2Bteam%20name@example.org?"))

    def test_build_mailto_url_recipient_query_delimiters_do_not_corrupt_query(self):
        result = build_mailto_url(
            subject="Director Referral",
            body="Body",
            to_recipients="director?bad=1&oops=2@example.org",
        )
        self.assertIn(
            "mailto:director%3Fbad%3D1%26oops%3D2@example.org?subject=Director%20Referral&body=Body",
            result,
        )

    def test_build_mailto_url_recipient_without_subject_or_body_remains_unchanged(self):
        self.assertEqual(
            build_mailto_url(subject="", body="", to_recipients="director@example.org"),
            "mailto:director@example.org",
        )

    def test_build_mailto_url_sanitizes_subject_header_injection_patterns(self):
        result = build_mailto_url(
            subject="Director\r\nBcc:bad@example.org",
            body="Body",
            to_recipients="director@example.org",
        )
        self.assertIn("subject=Director%20%20Bcc%3Abad%40example.org", result)


if __name__ == "__main__":
    unittest.main()
