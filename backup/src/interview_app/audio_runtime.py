from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

from .types import RecordingTranscriptionPayload
from .whisper_runtime_policy import RuntimeConfig, fallback_from_exception, persist_runtime_choice, resolve_runtime

logger = logging.getLogger(__name__)
TRANSCRIPTION_TIMEOUT_REASON = "transcription_timeout"


class AudioRuntimeController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def wait_for_pending_transcriptions(self) -> None:
        self.app._transcription_queue_state.wait_for_pending()

    def background_transcribe_question(
        self,
        *,
        flow_idx: int,
        session: Any,
        base_dir: Path,
        base_name: str,
        candidate_label: str,
        job_timeout_seconds: float = 180.0,
    ) -> None:
        queue_state = self.app._transcription_queue_state
        if queue_state.is_canceled(flow_idx):
            return
        queue_state.mark_started(flow_idx)
        try:
            result = self._stop_and_transcribe_with_timeout(
                flow_idx=flow_idx,
                session=session,
                base_dir=base_dir,
                base_name=base_name,
                timeout_seconds=job_timeout_seconds,
            )
            if queue_state.is_canceled(flow_idx):
                self._cleanup_canceled_result(result)
                queue_state.mark_completed(flow_idx, terminal_status="canceled")
                return
            payload = self._build_payload(flow_idx, base_name, base_dir, result, candidate_label)
            with self.app._audio_state_lock:
                entry = self.app._append_recording_attempt(flow_idx, payload)
                self.app.state.flow_candidate_transcripts[flow_idx] = str(entry.get("candidate_transcript") or "").strip()
            self.app._persist_interview_session_snapshot(flow_idx)
            queue_state.mark_completed(flow_idx)
        except TimeoutError:
            queue_state.mark_timed_out(flow_idx, TRANSCRIPTION_TIMEOUT_REASON)
            raise
        except Exception as exc:
            queue_state.mark_failed(flow_idx, str(exc))
            raise

    def _stop_and_transcribe_with_timeout(
        self,
        *,
        flow_idx: int,
        session: Any,
        base_dir: Path,
        base_name: str,
        timeout_seconds: float,
    ) -> Any:
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def _target() -> None:
            try:
                outcome["result"] = session.stop_and_transcribe(
                    output_dir=base_dir,
                    base_name=base_name,
                    language="en",
                )
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        if done.wait(timeout=max(0.1, float(timeout_seconds))):
            error = outcome.get("error")
            if error is not None:
                raise error
            return outcome["result"]
        logger.error(
            "transcription_stop_and_transcribe_timeout",
            extra={
                "flow_idx": flow_idx,
                "timeout_seconds": float(timeout_seconds),
                "reason": TRANSCRIPTION_TIMEOUT_REASON,
            },
        )
        raise TimeoutError(TRANSCRIPTION_TIMEOUT_REASON)

    def start_recording_session(
        self,
        start_recording: Any,
        *,
        base_dir: Path,
        base_name: str,
        runtime_config: RuntimeConfig,
    ) -> Any:
        return start_recording(
            os_name="windows" if sys.platform.startswith("win") else "linux",
            output_dir=base_dir,
            base_name=base_name,
            win_mic_device="Microphone (Realtek USB Audio)",
            win_sys_device="CABLE Output (VB-Audio Virtual Cable)",
            whisper_model=runtime_config.model,
            whisper_device=runtime_config.device,
            whisper_compute_type=runtime_config.compute_type,
            whisper_settings=self.app._current_whisper_transcription_settings(),
        )

    def start_recording_with_runtime_fallback(
        self,
        start_recording: Any,
        *,
        base_dir: Path,
        base_name: str,
    ) -> Any:
        preferred_runtime = resolve_runtime(self.app.settings)
        try:
            session = self.start_recording_session(
                start_recording,
                base_dir=base_dir,
                base_name=base_name,
                runtime_config=preferred_runtime,
            )
            persist_runtime_choice(self.app.settings, preferred_runtime, "preferred")
            return session
        except Exception as exc:
            fallback_runtime = fallback_from_exception(exc, preferred_runtime, self.app.settings)
            if fallback_runtime is None:
                raise
            session = self.start_recording_session(
                start_recording,
                base_dir=base_dir,
                base_name=base_name,
                runtime_config=fallback_runtime,
            )
            persist_runtime_choice(self.app.settings, fallback_runtime, "cpu_fallback")
            self.app._warn_whisper_fallback_once()
            return session

    def _cleanup_canceled_result(self, result: Any) -> None:
        self.app._delete_file_if_exists(Path(result.mic_wav))
        self.app._delete_file_if_exists(Path(result.sys_wav))
        self.app._delete_file_if_exists(Path(result.transcript_txt))
        self.app._delete_file_if_exists(Path(result.transcript_jsonl))

    def _build_payload(
        self,
        flow_idx: int,
        base_name: str,
        base_dir: Path,
        result: Any,
        candidate_label: str,
    ) -> RecordingTranscriptionPayload:
        candidate_transcript = self.app._extract_candidate_transcript_from_jsonl(result.transcript_jsonl, candidate_label)
        return {
            "flow_index": flow_idx,
            "base_name": base_name,
            "output_dir": str(base_dir),
            "mic_wav": str(result.mic_wav),
            "sys_wav": str(result.sys_wav),
            "transcript_txt": str(result.transcript_txt),
            "transcript_jsonl": str(result.transcript_jsonl),
            "candidate_label": candidate_label,
            "candidate_transcript": candidate_transcript,
        }
