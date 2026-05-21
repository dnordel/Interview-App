from pathlib import Path


def test_interview_app_initializes_transcription_executor_attribute() -> None:
    source = Path('src/interview_app.pyw').read_text(encoding='utf-8')
    assert 'self._transcription_executor: BoundedTranscriptionExecutor | None = None' in source
