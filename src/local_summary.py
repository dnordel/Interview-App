from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

try:
    from transformers import pipeline
except Exception:  # pragma: no cover
    pipeline = None  # type: ignore[assignment]


SUMMARY_UNAVAILABLE_MESSAGE = "Summary unavailable (local model unavailable)"


@dataclass(frozen=True)
class SummarySettings:
    model_id: str = "facebook/bart-large-cnn"
    max_input_chars: int = 12000
    executive_min_length: int = 120
    executive_max_length: int = 220
    answer_min_length: int = 30
    answer_max_length: int = 90


class LocalInterviewSummarizer:
    """Local Hugging Face summarizer with safe fallback behavior for offline exports."""

    def __init__(self, settings: SummarySettings | None = None) -> None:
        self._settings = settings or SummarySettings()
        self._generator: Any = None
        self._generator_lock = Lock()
        self._generator_init_failed = False

    def summarize_executive(self, transcript_text: str) -> str:
        text = self._normalize_input(transcript_text)
        if not text:
            return SUMMARY_UNAVAILABLE_MESSAGE
        prompt = (
            "Write an executive summary for an interview candidate. "
            "Include evaluative language covering communication quality, professionalism, "
            "classroom-readiness, strengths, and concerns.\n\n"
            f"Candidate transcript:\n{text}"
        )
        return self._generate_summary(prompt, self._settings.executive_min_length, self._settings.executive_max_length)

    def summarize_answer(self, answer_text: str, question_text: str = "") -> str:
        answer = self._normalize_input(answer_text)
        if not answer:
            return SUMMARY_UNAVAILABLE_MESSAGE
        question = str(question_text or "").strip()
        prompt = (
            "Summarize the candidate's answer with evaluative language. "
            "Describe clarity, relevance, strengths, and concerns in 2-4 sentences.\n\n"
            f"Question: {question if question else 'N/A'}\n"
            f"Answer: {answer}"
        )
        return self._generate_summary(prompt, self._settings.answer_min_length, self._settings.answer_max_length)

    def _normalize_input(self, text: str) -> str:
        normalized = str(text or "").strip()
        return normalized[: self._settings.max_input_chars]

    def _get_generator(self) -> Any:
        if self._generator_init_failed:
            return None
        if self._generator is not None:
            return self._generator
        with self._generator_lock:
            if self._generator_init_failed:
                return None
            if self._generator is not None:
                return self._generator
            if pipeline is None:
                self._generator_init_failed = True
                return None
            try:
                self._generator = pipeline("summarization", model=self._settings.model_id)
            except Exception:
                self._generator_init_failed = True
                return None
            return self._generator

    def _generate_summary(self, prompt: str, min_length: int, max_length: int) -> str:
        generator = self._get_generator()
        if generator is None:
            return SUMMARY_UNAVAILABLE_MESSAGE
        try:
            outputs = generator(prompt, min_length=min_length, max_length=max_length, do_sample=False)
        except Exception:
            return SUMMARY_UNAVAILABLE_MESSAGE
        if not outputs:
            return SUMMARY_UNAVAILABLE_MESSAGE
        summary_text = str(outputs[0].get("summary_text") or SUMMARY_UNAVAILABLE_MESSAGE).strip()
        return summary_text or SUMMARY_UNAVAILABLE_MESSAGE
