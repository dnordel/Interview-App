from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, Optional

_SUMMARY_UNAVAILABLE_PREFIX = "Summary unavailable:"
_DEFAULT_MISSING_SUMMARY = (
    f"{_SUMMARY_UNAVAILABLE_PREFIX} transformers summarization model/runtime is unavailable."
)
_SUMMARY_TASK = "summarization"
_TEXT2TEXT_TASK = "text2text-generation"
_SUMMARIZATION_PREFIX = "summarize: "
_UNKNOWN_TASK_MARKER = "Unknown task"


@lru_cache(maxsize=1)
def _build_summarizer_pipeline() -> tuple[Optional[Callable[..., Any]], Optional[str]]:
    try:
        from transformers import pipeline
    except Exception as exc:  # pragma: no cover - exercised with fallback tests
        return None, str(exc)

    try:
        return pipeline(_SUMMARY_TASK), None
    except Exception as summary_exc:
        text2text_pipeline, text2text_error = _build_text2text_summarizer_pipeline(pipeline)
        if text2text_pipeline is not None:
            return text2text_pipeline, None
        detail = _format_runtime_error(summary_exc, text2text_error)
        return None, detail


def _build_text2text_summarizer_pipeline(
    pipeline_factory: Callable[..., Any],
) -> tuple[Optional[Callable[..., Any]], Optional[str]]:
    try:
        base_pipeline = pipeline_factory(_TEXT2TEXT_TASK)
    except Exception as exc:  # pragma: no cover - exercised with fallback tests
        return None, str(exc)

    def summarize_with_prefix(text: str, **kwargs: Any) -> Any:
        prompt = f"{_SUMMARIZATION_PREFIX}{text}"
        return base_pipeline(prompt, **kwargs)

    return summarize_with_prefix, None


def _format_runtime_error(primary_error: Exception, secondary_error: Optional[str]) -> str:
    primary_message = _normalize_runtime_error_text(str(primary_error))
    if secondary_error is None:
        return primary_message

    fallback_message = _normalize_runtime_error_text(secondary_error)
    return f"{primary_message}; fallback {_TEXT2TEXT_TASK} failed: {fallback_message}"


def _normalize_runtime_error_text(message: str) -> str:
    if _UNKNOWN_TASK_MARKER not in message:
        return message
    if _SUMMARY_TASK not in message:
        return message
    return (
        "transformers runtime does not expose the 'summarization' task. "
        f"Attempted fallback task '{_TEXT2TEXT_TASK}'."
    )


def _normalize_transcript_text(transcript_text: Any) -> str:
    if not isinstance(transcript_text, str):
        return ""
    return " ".join(transcript_text.split()).strip()


def _chunk_text(text: str, max_chars: int = 2600, overlap_chars: int = 200) -> list[str]:
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return [chunk for chunk in chunks if chunk]


def summarize_transcript(
    transcript_text: Any,
    summarizer: Optional[Callable[..., Any]] = None,
    max_chars: int = 2600,
) -> str:
    normalized_text = _normalize_transcript_text(transcript_text)
    if not normalized_text:
        return "No candidate transcript available for summarization."

    active_summarizer = summarizer
    runtime_error: Optional[str] = None
    if active_summarizer is None:
        active_summarizer, runtime_error = _build_summarizer_pipeline()

    if active_summarizer is None:
        if runtime_error:
            normalized_runtime_error = _normalize_runtime_error_text(runtime_error)
            return f"{_DEFAULT_MISSING_SUMMARY} ({normalized_runtime_error})"
        return _DEFAULT_MISSING_SUMMARY

    summary_chunks: list[str] = []
    for chunk in _chunk_text(normalized_text, max_chars=max_chars):
        try:
            chunk_summary = active_summarizer(chunk, max_length=90, min_length=20, do_sample=False)
        except Exception as exc:
            return f"{_DEFAULT_MISSING_SUMMARY} ({exc})"

        text = ""
        if isinstance(chunk_summary, list) and chunk_summary:
            text = str(chunk_summary[0].get("summary_text") or "").strip()
        if text:
            summary_chunks.append(text)

    if not summary_chunks:
        return "Summary unavailable: no summary output generated."
    return " ".join(summary_chunks).strip()
