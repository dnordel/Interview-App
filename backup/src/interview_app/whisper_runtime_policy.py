from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    model: str
    device: str
    compute_type: str


def resolve_runtime(settings: dict[str, Any]) -> RuntimeConfig:
    model = _value_or_default(
        settings,
        runtime_key="whisper_runtime_model",
        preferred_key="whisper_model",
        default_value="large-v3",
    )
    device = _value_or_default(
        settings,
        runtime_key="whisper_runtime_device",
        preferred_key="whisper_device",
        default_value="cuda",
    ).lower()
    compute_type = _value_or_default(
        settings,
        runtime_key="whisper_runtime_compute_type",
        preferred_key="whisper_compute_type",
        default_value="float16",
    ).lower()
    return RuntimeConfig(model=model, device=device, compute_type=compute_type)


def fallback_from_exception(
    exc: Exception,
    preferred: RuntimeConfig,
    settings: dict[str, Any],
) -> RuntimeConfig | None:
    if not _is_device_runtime_exception(exc):
        return None
    return _resolve_cpu_fallback(preferred=preferred, settings=settings)


def persist_runtime_choice(
    settings: dict[str, Any],
    runtime_config: RuntimeConfig,
    mode: str,
) -> None:
    settings["whisper_runtime_model"] = runtime_config.model
    settings["whisper_runtime_device"] = runtime_config.device
    settings["whisper_runtime_compute_type"] = runtime_config.compute_type
    settings["whisper_runtime_mode"] = mode


def _value_or_default(
    settings: dict[str, Any],
    *,
    runtime_key: str,
    preferred_key: str,
    default_value: str,
) -> str:
    value = str(settings.get(runtime_key) or settings.get(preferred_key) or default_value).strip()
    return value or default_value


def _is_device_runtime_exception(exc: Exception) -> bool:
    text = str(exc).lower()
    return _contains_runtime_error_marker(text)


def _contains_runtime_error_marker(text: str) -> bool:
    markers = (
        "cuda",
        "cudnn",
        "cublas",
        "cublas64_12.dll",
        "device",
        "not enough gpu",
        "no gpu",
        "invalid device",
        "torch.cuda",
    )
    return any(marker in text for marker in markers)


def _resolve_cpu_fallback(*, preferred: RuntimeConfig, settings: dict[str, Any]) -> RuntimeConfig:
    fallback_model = str(settings.get("whisper_fallback_model") or "").strip()
    model = fallback_model or preferred.model or "small"
    return RuntimeConfig(model=model, device="cpu", compute_type="int8")
