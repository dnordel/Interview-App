import unittest

import local_summary
from local_summary import LocalInterviewSummarizer, SummarySettings, SUMMARY_UNAVAILABLE_MESSAGE


class TestLocalInterviewSummarizer(unittest.TestCase):
    def test_get_generator_returns_none_when_pipeline_init_fails(self):
        original_pipeline = local_summary.pipeline

        def _raising_pipeline(*args, **kwargs):
            raise RuntimeError("backend unavailable")

        local_summary.pipeline = _raising_pipeline
        try:
            summarizer = LocalInterviewSummarizer(SummarySettings(max_input_chars=1000))
            result = summarizer.summarize_executive("Candidate answered with detailed examples.")
        finally:
            local_summary.pipeline = original_pipeline

        self.assertEqual(result, SUMMARY_UNAVAILABLE_MESSAGE)

    def test_generate_summary_returns_fallback_on_inference_error(self):
        class _RaisingGenerator:
            def __call__(self, *args, **kwargs):
                raise RuntimeError("inference failure")

        summarizer = LocalInterviewSummarizer()
        summarizer._generator = _RaisingGenerator()
        result = summarizer.summarize_answer("Strong answer", "How do you teach?")
        self.assertEqual(result, SUMMARY_UNAVAILABLE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
