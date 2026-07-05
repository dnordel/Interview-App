"""
interview_audio_recorder.py

A small, importable module that:
1) Starts two FFmpeg recording processes (mic + system) into separate WAV files
2) Stops them on demand
3) Transcribes both tracks locally (faster-whisper) into a timestamped, interleaved transcript

Designed to be embedded inside a larger Python app (Tkinter, etc.).
No main().

Typical flow:
  session = start_recording(...)
  ...
  result = session.stop_and_transcribe(...)

Dependencies:
  - ffmpeg on PATH
  - pip install faster-whisper soundfile

Windows device discovery:
  ffmpeg -list_devices true -f dshow -i dummy

Linux device discovery:
  pactl list short sources
"""

from __future__ import annotations

import json
import logging
import os
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from interview_runtime import probe_audio_file, write_transcription_diagnostic


# ----------------------------
# Types
# ----------------------------

@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "speaker": self.speaker, "text": self.text}


@dataclass
class RecordingResult:
    mic_wav: Path
    sys_wav: Path
    transcript_txt: Path
    transcript_jsonl: Path


@dataclass
class ExistingTranscriptionResult:
    transcript_txt: Path
    transcript_jsonl: Path
    segment_count: int


class FasterWhisperBackend:
    def __init__(self, *, model_name: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe_segments(
        self,
        *,
        wav_path: Path,
        speaker: str,
        language: str,
        offset_sec: float,
        min_start_sec: float,
        whisper_settings: Mapping[str, Any],
    ) -> list[Segment]:
        segs, _info = self.model.transcribe(str(wav_path), language=language, **whisper_settings)
        return _segments_from_transcript_chunks(
            chunks=segs,
            speaker=speaker,
            offset_sec=offset_sec,
            min_start_sec=min_start_sec,
            start_attr="start",
            end_attr="end",
        )


class FasterWhisperModelBackend:
    def __init__(self, model: Any) -> None:
        self.model = model

    def transcribe_segments(
        self,
        *,
        wav_path: Path,
        speaker: str,
        language: str,
        offset_sec: float,
        min_start_sec: float,
        whisper_settings: Mapping[str, Any],
    ) -> list[Segment]:
        segs, _info = self.model.transcribe(str(wav_path), language=language, **whisper_settings)
        return _segments_from_transcript_chunks(
            chunks=segs,
            speaker=speaker,
            offset_sec=offset_sec,
            min_start_sec=min_start_sec,
            start_attr="start",
            end_attr="end",
        )


class OpenVinoGenAIBackend:
    def __init__(self, *, model_name: str, device: str) -> None:
        import openvino_genai as ov_genai

        model_path = _resolve_openvino_model_path(model_name)
        self.pipeline = ov_genai.WhisperPipeline(model_path, device)

    def transcribe_segments(
        self,
        *,
        wav_path: Path,
        speaker: str,
        language: str,
        offset_sec: float,
        min_start_sec: float,
        whisper_settings: Mapping[str, Any],
    ) -> list[Segment]:
        raw_audio = _load_audio_for_openvino(wav_path)
        language_token = f"<|{language}|>" if language and not language.startswith("<|") else language
        result = self.pipeline.generate(
            raw_audio,
            language=language_token,
            task="transcribe",
            return_timestamps=True,
        )
        chunks = getattr(result, "chunks", None)
        if chunks:
            return _segments_from_transcript_chunks(
                chunks=chunks,
                speaker=speaker,
                offset_sec=offset_sec,
                min_start_sec=min_start_sec,
                start_attr="start_ts",
                end_attr="end_ts",
            )
        text = str(result).strip()
        return [Segment(start=offset_sec, end=offset_sec, speaker=speaker, text=text)] if text else []


class WhisperCppCliBackend:
    def __init__(self, *, model_name: str) -> None:
        exe = str(os.environ.get("INTERVIEW_WHISPERCPP_EXE") or "").strip()
        if not exe:
            raise RuntimeError("whisper.cpp backend requires INTERVIEW_WHISPERCPP_EXE.")
        self.exe = exe
        self.model_path = model_name

    def transcribe_segments(
        self,
        *,
        wav_path: Path,
        speaker: str,
        language: str,
        offset_sec: float,
        min_start_sec: float,
        whisper_settings: Mapping[str, Any],
    ) -> list[Segment]:
        output_stem = wav_path.with_suffix("")
        json_path = Path(str(output_stem) + ".json")
        command = [
            self.exe,
            "-m",
            self.model_path,
            "-f",
            str(wav_path),
            "-l",
            language or "en",
            "-oj",
            "-of",
            str(output_stem),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "whisper.cpp failed").strip()
            raise RuntimeError(reason)
        if not json_path.exists():
            text = (completed.stdout or "").strip()
            return [Segment(start=offset_sec, end=offset_sec, speaker=speaker, text=text)] if text else []
        return _segments_from_whisper_cpp_json(
            json_path=json_path,
            speaker=speaker,
            offset_sec=offset_sec,
            min_start_sec=min_start_sec,
        )


class RecordingSession:
    """
    A running recording session (two FFmpeg processes).
    Call stop_and_transcribe() when you want to end and produce transcripts.
    """

    def __init__(
        self,
        *,
        os_name: str,
        mic_wav: Path,
        sys_wav: Path,
        mic_label: str,
        sys_label: str,
        mic_offset: float,
        sys_offset: float,
        whisper_model: str,
        whisper_device: str,
        whisper_compute_type: str,
        whisper_backend: str = "faster_whisper",
        whisper_settings: Optional[Mapping[str, Any]] = None,
    ):
        self._logger = logging.getLogger(__name__)
        self.os_name = os_name
        self.mic_wav = mic_wav
        self.sys_wav = sys_wav
        self.mic_label = mic_label
        self.sys_label = sys_label
        self.mic_offset = mic_offset
        self.sys_offset = sys_offset
        self.whisper_model = whisper_model
        self.whisper_device = whisper_device
        self.whisper_compute_type = whisper_compute_type
        self.whisper_backend = _normalize_whisper_backend(whisper_backend)
        self.whisper_settings = _normalize_whisper_transcribe_settings(whisper_settings)

        self._procs: list[subprocess.Popen] = []
        self._started = False
        self._stopped = False
        self._windows_job_handle: Any | None = None
        self._mic_transcribed_until = mic_offset
        self._sys_transcribed_until = sys_offset

    _model_cache: dict[tuple[str, str, str], Any] = {}
    _backend_cache: dict[tuple[str, str, str, str], Any] = {}

    @classmethod
    def _get_or_create_model(cls, model_name: str, device: str, compute_type: str) -> Any:
        key = (model_name, device, compute_type)
        cached = cls._model_cache.get(key)
        if cached is not None:
            return cached

        t0 = time.perf_counter()
        model = FasterWhisperBackend(model_name=model_name, device=device, compute_type=compute_type).model
        cls._model_cache[key] = model
        logging.getLogger(__name__).info(
            "Initialized Whisper model cache for model=%s device=%s compute_type=%s in %.2fs",
            model_name,
            device,
            compute_type,
            time.perf_counter() - t0,
        )
        return model

    @classmethod
    def _get_or_create_backend(cls, backend: str, model_name: str, device: str, compute_type: str) -> Any:
        if _normalize_whisper_backend(backend) == "faster_whisper":
            return FasterWhisperModelBackend(cls._get_or_create_model(model_name, device, compute_type))
        return _get_or_create_backend(
            backend=backend,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            cache=cls._backend_cache,
        )

    @property
    def is_running(self) -> bool:
        return self._started and not self._stopped

    def _safe_terminate(self, p: subprocess.Popen, timeout_s: float = 5.0) -> None:
        try:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=timeout_s)
        except Exception:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def _ensure_not_stopped(self) -> None:
        if self._stopped:
            raise RuntimeError("This RecordingSession has already been stopped.")

    def _popen_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if self.os_name.lower() == "windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if self.os_name.lower() == "linux":
            kwargs["preexec_fn"] = _linux_parent_death_preexec
        return kwargs

    def _initialize_windows_job(self) -> None:
        if self.os_name.lower() != "windows":
            return
        self._windows_job_handle = _create_windows_kill_on_close_job()

    def _track_process(self, proc: subprocess.Popen) -> None:
        if self.os_name.lower() != "windows":
            return
        if not self._windows_job_handle:
            return
        _assign_process_to_windows_job(self._windows_job_handle, proc)

    def start(self, mic_cmd: list[str] | None, sys_cmd: list[str] | None) -> None:
        """
        Start the two FFmpeg processes. This returns immediately.
        """
        if self._started:
            raise RuntimeError("RecordingSession already started.")
        self._initialize_windows_job()
        popen_kwargs = self._popen_kwargs()
        started: list[subprocess.Popen] = []
        try:
            for command in (mic_cmd, sys_cmd):
                if command is None:
                    continue
                proc = subprocess.Popen(command, **popen_kwargs)
                self._track_process(proc)
                started.append(proc)
        except Exception:
            for proc in started:
                self._safe_terminate(proc)
            self._close_windows_job()
            raise

        self._procs = started
        self._started = True

    def stop(self) -> None:
        """
        Stop both FFmpeg processes. Safe to call multiple times.
        """
        if not self._started:
            return
        if self._stopped:
            return
        for p in self._procs:
            self._safe_terminate(p)
        self._close_windows_job()
        self._stopped = True

    def _close_windows_job(self) -> None:
        handle = self._windows_job_handle
        self._windows_job_handle = None
        if not handle:
            return
        _close_windows_handle(handle)

    def stop_and_transcribe(
        self,
        *,
        output_dir: str | Path,
        base_name: str,
        language: str = "en",
        write_txt: bool = True,
        write_jsonl: bool = True,
        whisper_settings: Optional[Mapping[str, Any]] = None,
    ) -> RecordingResult:
        """
        Stops recording, transcribes both WAVs, and writes a timestamped interleaved transcript.

        Returns paths to outputs.
        """
        if not self._stopped:
            self.stop()

        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_txt = output_dir / f"{base_name}_transcript.txt"
        transcript_jsonl = output_dir / f"{base_name}_transcript.jsonl"

        t0 = time.perf_counter()
        backend = self._get_or_create_backend(
            self.whisper_backend,
            self.whisper_model,
            self.whisper_device,
            self.whisper_compute_type,
        )

        resolved_whisper_settings = _normalize_whisper_transcribe_settings(whisper_settings or self.whisper_settings)
        try:
            _ensure_at_least_one_audio_track({"mic": self.mic_wav, "system": self.sys_wav})
            mic_segs = _transcribe_segments(
                backend=backend,
                wav_path=self.mic_wav,
                speaker=self.mic_label,
                language=language,
                offset_sec=self.mic_offset,
                whisper_settings=resolved_whisper_settings,
            )
            sys_segs = _transcribe_segments(
                backend=backend,
                wav_path=self.sys_wav,
                speaker=self.sys_label,
                language=language,
                offset_sec=self.sys_offset,
                whisper_settings=resolved_whisper_settings,
            )
        except Exception as exc:
            diagnostic_path = write_transcription_diagnostic(
                output_dir=output_dir,
                base_name=base_name,
                stage="stop_and_transcribe",
                error=exc,
                context={
                    "language": language,
                    "whisper_model": self.whisper_model,
                    "whisper_device": self.whisper_device,
                    "whisper_compute_type": self.whisper_compute_type,
                    "whisper_backend": self.whisper_backend,
                    "whisper_settings": resolved_whisper_settings,
                    "mic": probe_audio_file(self.mic_wav),
                    "sys": probe_audio_file(self.sys_wav),
                },
            )
            self._logger.exception("stop_and_transcribe_failed")
            raise RuntimeError(f"Transcription failed. Diagnostic log: {diagnostic_path}") from exc

        merged = _merge_interleaved(mic_segs, sys_segs)
        self._logger.info(
            "Full transcription completed for base_name=%s with %d segments in %.2fs",
            base_name,
            len(merged),
            time.perf_counter() - t0,
        )

        if write_txt:
            with transcript_txt.open("w", encoding="utf-8") as f:
                f.write("TIMESTAMPED INTERLEAVED TRANSCRIPT\n")
                f.write(f"mic_label={self.mic_label}, sys_label={self.sys_label}\n")
                f.write(f"mic_offset={self.mic_offset}, sys_offset={self.sys_offset}\n")
                f.write("\n")
                for seg in merged:
                    f.write(f"[{_fmt_ts(seg.start)}] {seg.speaker}: {seg.text}\n")

        if write_jsonl:
            with transcript_jsonl.open("w", encoding="utf-8") as f:
                for seg in merged:
                    f.write(json.dumps(seg.to_dict(), ensure_ascii=False) + "\n")

        return RecordingResult(
            mic_wav=self.mic_wav,
            sys_wav=self.sys_wav,
            transcript_txt=transcript_txt,
            transcript_jsonl=transcript_jsonl,
        )

    def transcribe_new_segments(self, *, language: str = "en") -> list[Segment]:
        """
        Transcribe only segments that start after the last retrieved cursor per audio source.
        Intended for incremental question-by-question processing during a long recording session.
        """
        t0 = time.perf_counter()
        backend = self._get_or_create_backend(
            self.whisper_backend,
            self.whisper_model,
            self.whisper_device,
            self.whisper_compute_type,
        )

        mic_segs = _transcribe_segments(
            backend=backend,
            wav_path=self.mic_wav,
            speaker=self.mic_label,
            language=language,
            offset_sec=self.mic_offset,
            min_start_sec=self._mic_transcribed_until,
            whisper_settings=self.whisper_settings,
        )
        if mic_segs:
            self._mic_transcribed_until = max(self._mic_transcribed_until, max(s.end for s in mic_segs))

        sys_segs = _transcribe_segments(
            backend=backend,
            wav_path=self.sys_wav,
            speaker=self.sys_label,
            language=language,
            offset_sec=self.sys_offset,
            min_start_sec=self._sys_transcribed_until,
            whisper_settings=self.whisper_settings,
        )
        if sys_segs:
            self._sys_transcribed_until = max(self._sys_transcribed_until, max(s.end for s in sys_segs))

        merged = _merge_interleaved(mic_segs, sys_segs)
        self._logger.info(
            "Incremental transcription returned %d segments in %.2fs (mic_cursor=%.2f sys_cursor=%.2f)",
            len(merged),
            time.perf_counter() - t0,
            self._mic_transcribed_until,
            self._sys_transcribed_until,
        )
        return merged


# ----------------------------
# Public API
# ----------------------------

def start_recording(
    *,
    os_name: str,
    output_dir: str | Path,
    base_name: Optional[str] = None,
    sample_rate: int = 16000,
    # Windows devices (DirectShow)
    win_mic_device: Optional[str] = None,
    win_sys_device: Optional[str] = None,
    # Linux sources (Pulse/PipeWire)
    linux_mic_source: Optional[str] = None,
    linux_sys_monitor: Optional[str] = None,
    # Labels
    mic_label: str = "INTERVIEWER",
    sys_label: str = "CANDIDATE",
    # Alignment offsets (seconds)
    mic_offset: float = 0.0,
    sys_offset: float = 0.0,
    # Whisper settings
    whisper_model: str = "small",
    whisper_device: str = "cpu",
    whisper_compute_type: Optional[str] = None,
    whisper_backend: str = "faster_whisper",
    whisper_settings: Optional[Mapping[str, Any]] = None,
) -> RecordingSession:
    """
    Starts recording immediately and returns a RecordingSession handle.

    You should call:
      result = session.stop_and_transcribe(output_dir=..., base_name=...)
    when your UI "Stop" button is pressed.

    Notes:
      - On Windows with headphones, VB-Cable is recommended:
          win_sys_device="CABLE Output (VB-Audio Virtual Cable)"
      - On Linux, use a .monitor source for system audio.

    Raises:
      - SystemExit with a clear message if ffmpeg is missing
      - ValueError for missing required device parameters
    """
    os_name = os_name.lower().strip()
    if os_name not in ("windows", "linux"):
        raise ValueError("os_name must be 'windows' or 'linux'.")

    ffmpeg = _require_ffmpeg()

    outdir = Path(output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if base_name is None:
        base_name = f"interview_{_stamp()}"

    mic_wav = outdir / f"{base_name}_mic.wav"
    sys_wav = outdir / f"{base_name}_sys.wav"

    # Default compute type
    if whisper_compute_type is None:
        whisper_compute_type = "int8" if whisper_device.lower() == "cpu" else "float16"

    if os_name == "windows":
        if not win_mic_device and not win_sys_device:
            raise ValueError(
                "Windows requires at least one audio device: win_mic_device or win_sys_device.\n"
                "Get names via: ffmpeg -list_devices true -f dshow -i dummy"
            )
        mic_cmd = _ffmpeg_windows_mic_cmd(ffmpeg, mic_wav, sample_rate, win_mic_device) if win_mic_device else None
        sys_cmd = _ffmpeg_windows_system_cmd(ffmpeg, sys_wav, sample_rate, win_sys_device) if win_sys_device else None

    else:
        if not linux_mic_source or not linux_sys_monitor:
            raise ValueError(
                "Linux requires linux_mic_source and linux_sys_monitor.\n"
                "Get names via: pactl list short sources"
            )
        mic_cmd = _ffmpeg_linux_mic_cmd(ffmpeg, mic_wav, sample_rate, linux_mic_source)
        sys_cmd = _ffmpeg_linux_system_cmd(ffmpeg, sys_wav, sample_rate, linux_sys_monitor)

    session = RecordingSession(
        os_name=os_name,
        mic_wav=mic_wav,
        sys_wav=sys_wav,
        mic_label=mic_label,
        sys_label=sys_label,
        mic_offset=mic_offset,
        sys_offset=sys_offset,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        whisper_compute_type=whisper_compute_type,
        whisper_backend=whisper_backend,
        whisper_settings=whisper_settings,
    )
    session.start(mic_cmd, sys_cmd)
    return session


# ----------------------------
# Internal helpers
# ----------------------------

def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg not found on PATH. Install FFmpeg and open a new terminal.")
    return exe


def _fmt_ts(seconds: float) -> str:
    if seconds is None:
        seconds = 0.0
    if seconds < 0:
        seconds = 0.0
    s = int(seconds + 0.5)
    hh = s // 3600
    mm = (s % 3600) // 60
    ss = s % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _normalize_whisper_backend(value: str | None) -> str:
    backend = str(value or "faster_whisper").strip().lower().replace("-", "_")
    if backend in {"openvino", "openvino_genai"}:
        return "openvino_genai"
    if backend in {"whisper_cpp", "whispercpp"}:
        return "whisper_cpp"
    return "faster_whisper"


def _resolve_openvino_model_path(model_name: str) -> str:
    candidate = Path(model_name).expanduser()
    if candidate.exists():
        return str(candidate.resolve())

    repo_id = str(model_name or "").strip()
    if not repo_id.startswith("OpenVINO/"):
        raise RuntimeError(f"OpenVINO Whisper model must be a local path or approved OpenVINO repo id: {repo_id}")

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        raise RuntimeError("OpenVINO Whisper model download requires huggingface_hub.") from exc

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LPL_InterviewTool" / "openvino_models"
    safe_name = repo_id.replace("/", "_").replace("\\", "_")
    target = base / safe_name
    target.mkdir(parents=True, exist_ok=True)
    return snapshot_download(repo_id=repo_id, local_dir=str(target), local_dir_use_symlinks=False)


def _get_or_create_backend(
    *,
    backend: str,
    model_name: str,
    device: str,
    compute_type: str,
    cache: dict[tuple[str, str, str, str], Any] | None = None,
) -> Any:
    resolved_backend = _normalize_whisper_backend(backend)
    key = (resolved_backend, model_name, device, compute_type)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    t0 = time.perf_counter()
    if resolved_backend == "openvino_genai":
        instance = OpenVinoGenAIBackend(model_name=model_name, device=device)
    elif resolved_backend == "whisper_cpp":
        instance = WhisperCppCliBackend(model_name=model_name)
    else:
        instance = FasterWhisperBackend(model_name=model_name, device=device, compute_type=compute_type)

    if cache is not None:
        cache[key] = instance
    logging.getLogger(__name__).info(
        "Initialized transcription backend=%s model=%s device=%s compute_type=%s in %.2fs",
        resolved_backend,
        model_name,
        device,
        compute_type,
        time.perf_counter() - t0,
    )
    return instance


def _segments_from_transcript_chunks(
    *,
    chunks: Any,
    speaker: str,
    offset_sec: float,
    min_start_sec: float,
    start_attr: str,
    end_attr: str,
) -> list[Segment]:
    segments: list[Segment] = []
    for chunk in chunks:
        text = str(getattr(chunk, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(chunk, start_attr, 0.0)) + offset_sec
        end = float(getattr(chunk, end_attr, start)) + offset_sec
        if start < min_start_sec:
            continue
        segments.append(Segment(start=start, end=end, speaker=speaker, text=text))
    return segments


def _load_audio_for_openvino(wav_path: Path) -> list[float]:
    import soundfile as sf

    audio, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("OpenVINO transcription requires numpy.") from exc

    audio_array = np.asarray(audio, dtype=np.float32)
    if audio_array.ndim > 1:
        audio_array = audio_array.mean(axis=1)
    if sample_rate != 16000 and audio_array.size > 0:
        duration = audio_array.size / float(sample_rate)
        target_size = max(1, int(duration * 16000))
        source_x = np.linspace(0.0, duration, num=audio_array.size, endpoint=False)
        target_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
        audio_array = np.interp(target_x, source_x, audio_array).astype(np.float32)
    return audio_array.tolist()


def _parse_whisper_cpp_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    parts = text.replace(",", ".").split(":")
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) >= 3 else 0
    except ValueError:
        return 0.0
    return hours * 3600 + minutes * 60 + seconds


def _segments_from_whisper_cpp_json(
    *,
    json_path: Path,
    speaker: str,
    offset_sec: float,
    min_start_sec: float,
) -> list[Segment]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    raw_segments = payload.get("transcription") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list):
        raw_segments = payload.get("segments") if isinstance(payload, dict) else []
    segments: list[Segment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        timestamps = item.get("timestamps") if isinstance(item.get("timestamps"), dict) else {}
        start = _parse_whisper_cpp_timestamp(item.get("start") or timestamps.get("from")) + offset_sec
        end = _parse_whisper_cpp_timestamp(item.get("end") or timestamps.get("to")) + offset_sec
        text = str(item.get("text") or "").strip()
        if not text or start < min_start_sec:
            continue
        segments.append(Segment(start=start, end=end, speaker=speaker, text=text))
    return segments


def _ffmpeg_windows_mic_cmd(ffmpeg: str, out_wav: Path, sr: int, mic_device: str) -> list[str]:
    return [ffmpeg, "-y", "-f", "dshow", "-i", f"audio={mic_device}", "-ac", "1", "-ar", str(sr), str(out_wav)]


def _ffmpeg_windows_system_cmd(ffmpeg: str, out_wav: Path, sr: int, sys_device: str) -> list[str]:
    return [ffmpeg, "-y", "-f", "dshow", "-i", f"audio={sys_device}", "-ac", "1", "-ar", str(sr), str(out_wav)]


def _ffmpeg_linux_mic_cmd(ffmpeg: str, out_wav: Path, sr: int, mic_source: str) -> list[str]:
    return [ffmpeg, "-y", "-f", "pulse", "-i", mic_source, "-ac", "1", "-ar", str(sr), str(out_wav)]


def _ffmpeg_linux_system_cmd(ffmpeg: str, out_wav: Path, sr: int, sys_monitor: str) -> list[str]:
    return [ffmpeg, "-y", "-f", "pulse", "-i", sys_monitor, "-ac", "1", "-ar", str(sr), str(out_wav)]


def _linux_parent_death_preexec() -> None:
    pr_set_pdeathsig = getattr(getattr(os, "prctl", None), "__call__", None)
    if pr_set_pdeathsig is not None:
        try:
            os.prctl(1, signal.SIGTERM)  # type: ignore[attr-defined]
            return
        except Exception:
            pass

    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.prctl(1, signal.SIGTERM)
    except Exception:
        return


def _create_windows_kill_on_close_job() -> Any | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    JobObjectExtendedLimitInformation = 9

    ok = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if ok:
        return job

    _close_windows_handle(job)
    return None


def _assign_process_to_windows_job(job_handle: Any, process: subprocess.Popen) -> None:
    if not job_handle:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject(job_handle, process._handle)
    except Exception:
        return


def _close_windows_handle(handle: Any) -> None:
    try:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
    except Exception:
        return


def _transcribe_segments(
    *,
    backend: Any,
    wav_path: Path,
    speaker: str,
    language: str,
    offset_sec: float,
    min_start_sec: float = 0.0,
    whisper_settings: Optional[Mapping[str, Any]] = None,
) -> list[Segment]:
    if not wav_path.exists():
        logging.getLogger(__name__).warning("Audio file missing, skipping transcription: %s", wav_path)
        return []
    if wav_path.stat().st_size <= 0:
        return []

    t0 = time.perf_counter()
    segs_out: list[Segment] = []
    transcribe_settings = _normalize_whisper_transcribe_settings(whisper_settings)
    segs_out = backend.transcribe_segments(
        wav_path=wav_path,
        speaker=speaker,
        language=language,
        offset_sec=offset_sec,
        min_start_sec=min_start_sec,
        whisper_settings=transcribe_settings,
    )

    logging.getLogger(__name__).info(
        "Transcribed %s for speaker=%s in %.2fs (%d kept segments, min_start=%.2f)",
        wav_path.name,
        speaker,
        time.perf_counter() - t0,
        len(segs_out),
        min_start_sec,
    )
    return segs_out


def _ensure_audio_track_available(wav_path: Path, *, source_name: str) -> None:
    if not wav_path.exists():
        raise FileNotFoundError(f"{source_name} audio file is missing: {wav_path}")
    if wav_path.stat().st_size <= 0:
        raise RuntimeError(f"{source_name} audio file is empty: {wav_path}")


def _ensure_at_least_one_audio_track(available_tracks: Mapping[str, Path]) -> None:
    errors: list[str] = []
    for source_name, wav_path in available_tracks.items():
        try:
            _ensure_audio_track_available(wav_path, source_name=source_name)
            return
        except (FileNotFoundError, RuntimeError) as exc:
            errors.append(str(exc))
    details = "; ".join(errors) if errors else "no audio tracks provided"
    raise RuntimeError(f"No transcribable audio tracks were found: {details}")


def transcribe_existing_recordings(
    *,
    output_dir: str | Path,
    base_name: str,
    mic_wav: str | Path | None,
    sys_wav: str | Path | None,
    mic_label: str = "INTERVIEWER",
    sys_label: str = "CANDIDATE",
    mic_offset: float = 0.0,
    sys_offset: float = 0.0,
    language: str = "en",
    whisper_model: str = "small",
    whisper_device: str = "cpu",
    whisper_compute_type: str | None = None,
    whisper_backend: str = "faster_whisper",
    whisper_settings: Optional[Mapping[str, Any]] = None,
) -> ExistingTranscriptionResult:
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_txt = out_dir / f"{base_name}_transcript.txt"
    transcript_jsonl = out_dir / f"{base_name}_transcript.jsonl"

    resolved_compute_type = whisper_compute_type
    if resolved_compute_type is None:
        resolved_compute_type = "int8" if whisper_device.lower() == "cpu" else "float16"

    backend = RecordingSession._get_or_create_backend(
        whisper_backend,
        whisper_model,
        whisper_device,
        resolved_compute_type,
    )
    settings = _normalize_whisper_transcribe_settings(whisper_settings)
    mic_path = Path(mic_wav).expanduser() if mic_wav else None
    sys_path = Path(sys_wav).expanduser() if sys_wav else None
    provided_tracks = {
        source_name: wav_path
        for source_name, wav_path in (("mic", mic_path), ("system", sys_path))
        if wav_path is not None
    }
    _ensure_at_least_one_audio_track(provided_tracks)
    mic_segments = _transcribe_segments(
        backend=backend,
        wav_path=mic_path if mic_path else Path(""),
        speaker=mic_label,
        language=language,
        offset_sec=mic_offset,
        whisper_settings=settings,
    ) if mic_path else []
    sys_segments = _transcribe_segments(
        backend=backend,
        wav_path=sys_path if sys_path else Path(""),
        speaker=sys_label,
        language=language,
        offset_sec=sys_offset,
        whisper_settings=settings,
    ) if sys_path else []
    merged = _merge_interleaved(mic_segments, sys_segments)

    with transcript_txt.open("w", encoding="utf-8") as handle:
        handle.write("TIMESTAMPED INTERLEAVED TRANSCRIPT\n")
        handle.write(f"mic_label={mic_label}, sys_label={sys_label}\n")
        handle.write(f"mic_offset={mic_offset}, sys_offset={sys_offset}\n\n")
        for seg in merged:
            handle.write(f"[{_fmt_ts(seg.start)}] {seg.speaker}: {seg.text}\n")

    with transcript_jsonl.open("w", encoding="utf-8") as handle:
        for seg in merged:
            handle.write(json.dumps(seg.to_dict(), ensure_ascii=False) + "\n")

    return ExistingTranscriptionResult(
        transcript_txt=transcript_txt,
        transcript_jsonl=transcript_jsonl,
        segment_count=len(merged),
    )



def _normalize_whisper_transcribe_settings(settings: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    defaults: dict[str, Any] = {"vad_filter": True, "beam_size": 5, "temperature": 0.0}
    if not settings:
        return defaults

    resolved = dict(defaults)
    beam_size = settings.get("beam_size")
    if isinstance(beam_size, int) and 1 <= beam_size <= 10:
        resolved["beam_size"] = beam_size

    vad_filter = settings.get("vad_filter")
    if isinstance(vad_filter, bool):
        resolved["vad_filter"] = vad_filter

    temperature = settings.get("temperature")
    if isinstance(temperature, (float, int)):
        temp_value = float(temperature)
        if 0.0 <= temp_value <= 1.0:
            resolved["temperature"] = temp_value

    return resolved

def _merge_interleaved(a: list[Segment], b: list[Segment]) -> list[Segment]:
    merged = a + b
    merged.sort(key=lambda x: (x.start, x.end))
    return merged
