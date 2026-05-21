from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from interview_app.audio_runtime import AudioRuntimeController
from interview_app.transcription_executor import BoundedTranscriptionExecutor
from interview_app.transcription_queue import TranscriptionQueueState


class _Result:
    mic_wav = Path('/tmp/mic.wav')
    sys_wav = Path('/tmp/sys.wav')
    transcript_txt = Path('/tmp/txt.txt')
    transcript_jsonl = Path('/tmp/segments.jsonl')


def _build_app() -> SimpleNamespace:
    app = SimpleNamespace()
    app._audio_state_lock = threading.Lock()
    app._transcription_queue_state = TranscriptionQueueState()
    app._append_recording_attempt = lambda _idx, payload: payload
    app._persist_interview_session_snapshot = lambda _idx: None
    app._delete_file_if_exists = lambda _path: None
    app._extract_candidate_transcript_from_jsonl = lambda _path, _label: 'candidate text'
    app.state = SimpleNamespace(flow_candidate_transcripts={})
    return app


def _payload(flow_idx: int = 2) -> dict:
    return {
        'flow_idx': flow_idx,
        'session': object(),
        'base_dir': Path('/tmp'),
        'base_name': 'base',
        'candidate_label': 'CANDIDATE',
        'job_uuid': 'job-123',
        'retry_count': 1,
        'interview_session_id': 'session-22',
        'finalize_correlation_id': 'finalize-33',
    }


def test_transcription_logging_success_events(caplog) -> None:
    app = _build_app()
    controller = AudioRuntimeController(app, SimpleNamespace())
    session = SimpleNamespace(stop_and_transcribe=lambda **_kwargs: _Result())

    with caplog.at_level(logging.INFO):
        executor = BoundedTranscriptionExecutor(
            queue_state=app._transcription_queue_state,
            worker_fn=controller.background_transcribe_question,
            max_workers=1,
        )
        payload = _payload(2)
        payload['session'] = session
        executor.submit(2, payload)
        app._transcription_queue_state.wait_for_pending()
        executor.shutdown(wait=True)

    messages = [record.message for record in caplog.records]
    assert 'transcription_job_queued' in messages
    assert 'transcription_job_started' in messages
    assert 'transcription_job_completed' in messages
    completed = next(record for record in caplog.records if record.message == 'transcription_job_completed')
    assert completed.finalize_correlation_id == 'finalize-33'
    assert completed.interview_session_id == 'session-22'
    assert completed.job_uuid == 'job-123'
    assert completed.retry_count == 1
    assert completed.terminal_status == 'completed'


def test_transcription_logging_failure_events(caplog) -> None:
    app = _build_app()
    controller = AudioRuntimeController(app, SimpleNamespace())

    def _raise(**_kwargs):
        raise RuntimeError('boom')

    session = SimpleNamespace(stop_and_transcribe=_raise)
    with caplog.at_level(logging.INFO):
        executor = BoundedTranscriptionExecutor(
            queue_state=app._transcription_queue_state,
            worker_fn=controller.background_transcribe_question,
            max_workers=1,
        )
        payload = _payload(3)
        payload['session'] = session
        executor.submit(3, payload)
        app._transcription_queue_state.wait_for_pending()
        executor.shutdown(wait=True)

    messages = [record.message for record in caplog.records]
    assert 'transcription_job_failed' in messages
    failed = next(record for record in caplog.records if record.message == 'transcription_job_failed')
    assert failed.terminal_status == 'failed'


def test_transcription_logging_timeout_events(caplog) -> None:
    app = _build_app()
    controller = AudioRuntimeController(app, SimpleNamespace())

    def _raise_timeout(**_kwargs):
        raise TimeoutError('timeout')

    session = SimpleNamespace(stop_and_transcribe=_raise_timeout)
    with caplog.at_level(logging.INFO):
        executor = BoundedTranscriptionExecutor(
            queue_state=app._transcription_queue_state,
            worker_fn=controller.background_transcribe_question,
            max_workers=1,
        )
        payload = _payload(4)
        payload['session'] = session
        executor.submit(4, payload)
        app._transcription_queue_state.wait_for_pending()
        executor.shutdown(wait=True)

    messages = [record.message for record in caplog.records]
    assert 'transcription_job_timed_out' in messages
    timed_out = next(record for record in caplog.records if record.message == 'transcription_job_timed_out')
    assert timed_out.terminal_status == 'timed_out'


def test_hung_transcription_timeout_releases_pending_and_captures_reason() -> None:
    app = _build_app()
    controller = AudioRuntimeController(app, SimpleNamespace())

    def _hang(**_kwargs):
        import time

        time.sleep(1.0)
        return _Result()

    session = SimpleNamespace(stop_and_transcribe=_hang)
    executor = BoundedTranscriptionExecutor(
        queue_state=app._transcription_queue_state,
        worker_fn=controller.background_transcribe_question,
        max_workers=1,
    )
    payload = _payload(8)
    payload['session'] = session
    payload['job_timeout_seconds'] = 0.1
    executor.submit(8, payload)
    app._transcription_queue_state.wait_for_pending()
    executor.shutdown(wait=True)

    assert app._transcription_queue_state.pending_count() == 0
    assert app._transcription_queue_state.error_reasons()[8] == 'transcription_timeout'
