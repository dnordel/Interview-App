from __future__ import annotations

import re
import threading
from importlib import import_module
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import messagebox
from typing import Any, Callable

from app_content import DEFAULT_BASE_DIR, sanitize_filename
from .retranscribe_progress import RetranscriptionProgressDialog
from .types import HistoryRowKey, OfferTransitionResult, RetranscribeResultPayload
from .whisper_runtime_policy import fallback_from_exception, persist_runtime_choice, resolve_runtime


class HistoryActionsService:
    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def offer_transition(status: str) -> OfferTransitionResult | None:
        transitions: dict[str, OfferTransitionResult] = {
            "not_generated": {"next_status": "generated", "done_message": "Offer generated."},
            "generated": {"next_status": "approved", "done_message": "Offer marked as approved."},
            "approved": {"next_status": "accepted", "done_message": "Offer marked as accepted."},
            "accepted": {"next_status": "welcome_email_sent", "done_message": "Welcome email sent."},
        }
        return transitions.get(status)

    def update_history_offer_status(self, row: dict[str, Any], status: str, offer_path: str = "") -> bool:
        row_key = self._row_key(row)
        if not row_key:
            return False
        if not self.app.history_store.update_offer_state(row_key, status, offer_path):
            return False
        self.app._refresh_history_tree()
        return True

    def handle_offer_action_for_row(self, row: dict[str, Any]) -> None:
        status = str(row.get("offer_status", "not_generated")).strip().lower() or "not_generated"
        if status == "not_generated":
            self.app._open_offer_generator(row)
            return
        if status == "welcome_email_sent":
            self.app._open_onboarding_tracker()
            return
        transition = self.offer_transition(status)
        if transition is None:
            return
        if not self.app._draft_offer_email_for_transition(status, row):
            return
        if not self.update_history_offer_status(row, transition["next_status"]):
            return
        messagebox.showinfo("Offer Workflow", transition["done_message"])

    def handle_retranscribe_for_row(self, row: dict[str, Any]) -> None:
        row_key = self._row_key(row)
        if not row_key:
            messagebox.showerror("Transcription", "Could not identify selected history row.")
            return
        recordings = self._recordings_for_retranscribe(row)
        if not recordings:
            messagebox.showwarning(
                "Transcription",
                "No per-question recording metadata was saved for this interview."
                "\nTranscription cannot be retried for this row.",
            )
            return

        if not callable(getattr(self.app, "after", None)):
            self._run_retranscribe_sync(row, row_key, recordings)
            return
        self._run_retranscribe_async(row, row_key, recordings)

    def _run_retranscribe_sync(
        self,
        row: dict[str, Any],
        row_key: HistoryRowKey,
        recordings: list[dict[str, Any]],
    ) -> None:
        try:
            result = self._run_retranscribe_flow(row, row_key, recordings)
        except Exception as exc:
            messagebox.showerror("Transcription", f"Retry transcription failed:\n\n{exc}")
            return
        messagebox.showinfo("Transcription", f"Transcript regenerated:\n{result['transcript_path']}")

    def _run_retranscribe_async(
        self,
        row: dict[str, Any],
        row_key: HistoryRowKey,
        recordings: list[dict[str, Any]],
    ) -> None:
        progress_dialog = RetranscriptionProgressDialog(self.app, total_steps=len(recordings))
        progress_dialog.show()
        result_queue: SimpleQueue[tuple[str, Any]] = SimpleQueue()

        def _progress_callback(completed_steps: int, total_steps: int, status_text: str) -> None:
            result_queue.put(("progress", (completed_steps, total_steps, status_text)))

        def _worker() -> None:
            try:
                result = self._run_retranscribe_flow(row, row_key, recordings, progress_callback=_progress_callback)
                result_queue.put(("done", result))
            except Exception as exc:
                result_queue.put(("error", exc))

        threading.Thread(target=_worker, name="history-retranscribe", daemon=True).start()
        self._poll_retranscribe_events(progress_dialog, result_queue)

    def _poll_retranscribe_events(
        self,
        progress_dialog: RetranscriptionProgressDialog,
        result_queue: SimpleQueue[tuple[str, Any]],
    ) -> None:
        try:
            while True:
                event_name, payload = result_queue.get_nowait()
                if event_name == "progress":
                    completed_steps, _total_steps, status_text = payload
                    progress_dialog.update(completed_steps=completed_steps, status_text=status_text)
                    continue
                progress_dialog.close()
                if event_name == "done":
                    messagebox.showinfo("Transcription", f"Transcript regenerated:\n{payload['transcript_path']}")
                    return
                messagebox.showerror("Transcription", f"Retry transcription failed:\n\n{payload}")
                return
        except Empty:
            self.app.after(120, lambda: self._poll_retranscribe_events(progress_dialog, result_queue))

    def _run_retranscribe_flow(
        self,
        row: dict[str, Any],
        row_key: HistoryRowKey,
        recordings: list[dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> RetranscribeResultPayload:
        transcript_path = self.retranscribe_history_recordings(row, recordings, progress_callback=progress_callback)
        payload: RetranscribeResultPayload = {
            "row_key": row_key,
            "transcript_path": transcript_path,
            "flow_recordings": recordings,
        }
        self.app.history_store.update_row(
            row_key,
            {
                "transcript_path": transcript_path,
                "flow_recordings": recordings,
            },
        )
        self.app._refresh_history_tree()
        return payload

    def _recordings_for_retranscribe(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        saved = self._coerce_saved_recordings(row.get("flow_recordings"))
        if saved:
            row["flow_recordings"] = saved
            return saved

        persisted = self._history_row_from_store(row)
        if persisted:
            persisted_saved = self._coerce_saved_recordings(persisted.get("flow_recordings"))
            if persisted_saved:
                row["flow_recordings"] = persisted_saved
                return persisted_saved

            persisted_audio = self._coerce_saved_recordings(persisted.get("audio_recording"))
            if persisted_audio:
                row["flow_recordings"] = persisted_audio
                return persisted_audio

        audio_recording = self._coerce_saved_recordings(row.get("audio_recording"))
        if audio_recording:
            row["flow_recordings"] = audio_recording
            return audio_recording

        recovered = self._recover_recordings_from_audio_files(row)
        if recovered:
            row["flow_recordings"] = recovered
        return recovered

    def _history_row_from_store(self, row: dict[str, Any]) -> dict[str, Any] | None:
        row_key = self._row_key(row)
        if not row_key:
            return None
        loader = getattr(self.app.history_store, "load", None)
        if not callable(loader):
            return None
        for item in loader():
            if not isinstance(item, dict):
                continue
            if self._row_key(item) == row_key:
                return item
        return None

    def _coerce_saved_recordings(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []

        normalized: list[dict[str, Any]] = []
        for key, raw in sorted(value.items(), key=lambda item: str(item[0])):
            if not isinstance(raw, dict):
                continue
            flow_index = self._parse_flow_index(raw.get("flow_index"), key)
            normalized.append(self._normalize_recording_entry(flow_index, raw))
        return normalized

    def _parse_flow_index(self, value: Any, fallback: Any) -> int:
        for candidate in [value, fallback]:
            try:
                parsed = int(candidate)
            except (TypeError, ValueError):
                continue
            if parsed < 0:
                return 0
            return parsed
        return 0

    def _normalize_recording_entry(self, flow_index: int, raw: dict[str, Any]) -> dict[str, Any]:
        wav_paths = raw.get("wav_paths") if isinstance(raw.get("wav_paths"), dict) else {}
        mic_wav = str(raw.get("mic_wav") or wav_paths.get("mic") or "").strip()
        sys_wav = str(raw.get("sys_wav") or wav_paths.get("system") or "").strip()
        attempts = raw.get("attempts") if isinstance(raw.get("attempts"), list) else []
        if not attempts:
            attempts = [{
                "base_name": str(raw.get("base_name") or "").strip(),
                "mic_wav": mic_wav,
                "sys_wav": sys_wav,
                "candidate_label": str(raw.get("candidate_label") or "CANDIDATE"),
                "candidate_transcript": str(raw.get("candidate_transcript") or "").strip(),
            }]
        return {
            "flow_index": flow_index,
            "base_name": str(raw.get("base_name") or "").strip(),
            "mic_wav": mic_wav,
            "sys_wav": sys_wav,
            "wav_paths": {
                "mic": mic_wav,
                "system": sys_wav,
            },
            "transcript_paths": {
                "jsonl": str(raw.get("transcript_jsonl") or (raw.get("transcript_paths") or {}).get("jsonl") or "").strip(),
                "txt": str(raw.get("transcript_txt") or (raw.get("transcript_paths") or {}).get("txt") or "").strip(),
            },
            "candidate_label": str(raw.get("candidate_label") or "CANDIDATE").strip() or "CANDIDATE",
            "candidate_transcript": str(raw.get("candidate_transcript") or "").strip(),
            "attempt_count": len(attempts),
            "attempts": [dict(item) for item in attempts if isinstance(item, dict)],
        }

    def _recover_recordings_from_audio_files(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        candidate_name = re.sub(r"[^A-Za-z0-9]+", "_", str(row.get("candidate_name", "candidate"))).strip("_") or "candidate"
        interview_date = str(row.get("interview_date", "")).strip()
        base_dir = Path(self.app.settings.get("base_dir", str(DEFAULT_BASE_DIR))).expanduser()
        grouped: dict[tuple[str, int, str], dict[str, str]] = {}
        search_dirs = self._audio_search_dirs(base_dir)

        for directory in search_dirs:
            self._collect_recordings_from_directory(
                directory=directory,
                grouped=grouped,
                candidate_name=candidate_name,
                interview_date=interview_date,
            )

        if not grouped:
            return []

        dates = {date_key for date_key, _, _ in grouped.keys()}
        selected_date = interview_date if interview_date in dates else max(dates)

        recovered: list[dict[str, Any]] = []
        for (date_key, flow_idx, base_name), wav_paths in sorted(grouped.items()):
            if date_key != selected_date:
                continue
            mic_wav = str(wav_paths.get("mic") or "").strip()
            sys_wav = str(wav_paths.get("system") or "").strip()
            if not mic_wav and not sys_wav:
                continue
            recovered.append(
                {
                    "flow_index": flow_idx,
                    "base_name": base_name,
                    "wav_paths": {"mic": mic_wav, "system": sys_wav},
                    "transcript_paths": {"jsonl": "", "txt": ""},
                    "candidate_label": "CANDIDATE",
                    "candidate_transcript": "",
                    "attempt_count": 1,
                    "attempts": [
                        {
                            "base_name": base_name,
                            "mic_wav": mic_wav,
                            "sys_wav": sys_wav,
                            "candidate_label": "CANDIDATE",
                            "candidate_transcript": "",
                        }
                    ],
                }
            )
        return recovered

    @staticmethod
    def _audio_search_dirs(base_dir: Path) -> list[Path]:
        candidates = [base_dir, base_dir / "interviews", base_dir.parent / "interviews"]
        unique: list[Path] = []
        for candidate in candidates:
            resolved = candidate.expanduser()
            if resolved in unique:
                continue
            unique.append(resolved)
        return unique

    def _collect_recordings_from_directory(
        self,
        *,
        directory: Path,
        grouped: dict[tuple[str, int, str], dict[str, str]],
        candidate_name: str,
        interview_date: str,
    ) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        for wav_path in directory.glob("Candidate_*.wav"):
            parsed = self._parse_recoverable_wav(
                wav_path=wav_path,
                candidate_name=candidate_name,
                interview_date=interview_date,
            )
            if parsed is None:
                continue
            date_key, flow_idx, base_name, source = parsed
            key = (date_key, flow_idx, base_name)
            grouped.setdefault(key, {})[source] = str(wav_path)

    @staticmethod
    def _parse_recoverable_wav(
        *,
        wav_path: Path,
        candidate_name: str,
        interview_date: str,
    ) -> tuple[str, int, str, str] | None:
        match = re.match(r"^(?P<base>.+)_(?P<source>mic|sys)$", wav_path.stem)
        if match is None:
            return None
        base_name = match.group("base")
        source = "mic" if match.group("source") == "mic" else "system"
        expected_prefix = f"Candidate_{candidate_name}_"
        if not base_name.startswith(expected_prefix):
            return None
        date_match = re.search(r"(?P<date>\d{4}-\d{2}-\d{2})", base_name)
        if interview_date and (date_match is None or date_match.group("date") != interview_date):
            return None
        question_match = re.search(r"_Q(?P<question>\d+)", base_name)
        if question_match is None:
            return None
        date_key = date_match.group("date") if date_match else "0000-00-00"
        flow_idx = max(0, int(question_match.group("question")) - 1)
        return date_key, flow_idx, base_name, source

    def retranscribe_history_recordings(
        self,
        row: dict[str, Any],
        recordings: list[dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> str:
        transcribe_existing_recordings = self._resolve_transcribe_existing_recordings()
        base_dir = Path(self.app.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        base_dir.mkdir(parents=True, exist_ok=True)
        all_segments: list[dict[str, Any]] = []
        ordered = sorted(recordings, key=lambda item: int(item.get("flow_index", 0)))
        preferred_runtime = resolve_runtime(self.app.settings)
        total_steps = len(ordered)
        for idx, rec in enumerate(ordered, start=1):
            flow_idx = int(rec.get("flow_index", 0))
            if callable(progress_callback):
                progress_callback(idx - 1, total_steps, f"Transcribing question {flow_idx + 1}...")
            base_name = self._base_name_for_retry(row, rec, flow_idx)
            attempt = self.app._latest_recording_attempt(rec)
            candidate_label = str(attempt.get("candidate_label") or rec.get("candidate_label") or "CANDIDATE")
            result = self._transcribe_recording_with_runtime_fallback(
                transcribe_existing_recordings=transcribe_existing_recordings,
                output_dir=base_dir,
                base_name=base_name,
                attempt=attempt,
                candidate_label=candidate_label,
                preferred_runtime=preferred_runtime,
            )
            rec["transcript_txt"] = str(result.transcript_txt)
            rec["transcript_jsonl"] = str(result.transcript_jsonl)
            rec["candidate_transcript"] = self.app._extract_candidate_transcript_from_jsonl(
                result.transcript_jsonl,
                candidate_label,
            )
            all_segments.extend(self.app._load_jsonl_segments_for_merge(result.transcript_jsonl))
            if callable(progress_callback):
                progress_callback(idx, total_steps, f"Completed question {flow_idx + 1}")
        transcript_path = self.app._write_merged_history_transcript(row, all_segments)
        return str(transcript_path)

    @staticmethod
    def _resolve_transcribe_existing_recordings() -> Any:
        try:
            recorder_module = import_module("interview_audio_recorder")
        except ImportError as exc:
            raise RuntimeError(f"Transcription dependencies are unavailable: {exc}") from exc

        candidate_names = [
            "transcribe_existing_recordings",
            "transcribe_existing_recording",
        ]
        for candidate_name in candidate_names:
            transcribe_fn = getattr(recorder_module, candidate_name, None)
            if callable(transcribe_fn):
                return transcribe_fn

        exported_names = sorted(name for name in dir(recorder_module) if name.startswith("transcribe"))
        available = ", ".join(exported_names) if exported_names else "<none>"
        message = (
            "Transcription dependencies are unavailable: "
            "interview_audio_recorder does not export a supported retry transcription function. "
            f"Expected one of {candidate_names}; available exports: {available}."
        )
        raise RuntimeError(message)

    def _transcribe_recording_with_runtime_fallback(
        self,
        *,
        transcribe_existing_recordings: Any,
        output_dir: Path,
        base_name: str,
        attempt: dict[str, Any],
        candidate_label: str,
        preferred_runtime: Any,
    ) -> Any:
        try:
            result = self._transcribe_single_recording(
                transcribe_existing_recordings=transcribe_existing_recordings,
                output_dir=output_dir,
                base_name=base_name,
                attempt=attempt,
                candidate_label=candidate_label,
                runtime=preferred_runtime,
            )
            persist_runtime_choice(self.app.settings, preferred_runtime, "preferred")
            return result
        except Exception as exc:
            fallback_runtime = fallback_from_exception(exc, preferred_runtime, self.app.settings)
            if fallback_runtime is None:
                raise
            result = self._transcribe_single_recording(
                transcribe_existing_recordings=transcribe_existing_recordings,
                output_dir=output_dir,
                base_name=base_name,
                attempt=attempt,
                candidate_label=candidate_label,
                runtime=fallback_runtime,
            )
            persist_runtime_choice(self.app.settings, fallback_runtime, "cpu_fallback")
            self.app._warn_whisper_fallback_once()
            return result

    def _transcribe_single_recording(
        self,
        *,
        transcribe_existing_recordings: Any,
        output_dir: Path,
        base_name: str,
        attempt: dict[str, Any],
        candidate_label: str,
        runtime: Any,
    ) -> Any:
        return transcribe_existing_recordings(
            output_dir=output_dir,
            base_name=base_name,
            mic_wav=attempt.get("mic_wav"),
            sys_wav=attempt.get("sys_wav"),
            mic_label="INTERVIEWER",
            sys_label=candidate_label,
            language=self.app._current_whisper_language(),
            whisper_model=str(runtime.model),
            whisper_device=str(runtime.device),
            whisper_compute_type=str(runtime.compute_type) or None,
            whisper_settings=self.app._current_whisper_transcription_settings(),
        )

    @staticmethod
    def _base_name_for_retry(row: dict[str, Any], rec: dict[str, Any], flow_idx: int) -> str:
        base_name = str(rec.get("base_name") or "").strip()
        if base_name:
            return base_name
        candidate_name = sanitize_filename(row.get("candidate_name", "candidate"))
        return f"history_retry_{candidate_name}_Q{flow_idx + 1}"

    def _row_key(self, row: dict[str, Any]) -> HistoryRowKey:
        return str(self.app.history_store.build_row_key(row)).strip()
