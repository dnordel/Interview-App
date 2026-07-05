from interview_app.whisper_runtime_policy import (
    RuntimeConfig,
    fallback_from_exception,
    persist_runtime_choice,
    resolve_runtime,
)


def test_resolve_runtime_prefers_runtime_keys() -> None:
    settings = {
        "whisper_runtime_model": "medium",
        "whisper_runtime_device": "CUDA",
        "whisper_runtime_compute_type": "FLOAT16",
        "whisper_model": "small",
    }
    config = resolve_runtime(settings)
    assert config == RuntimeConfig(model="medium", device="cuda", compute_type="float16")


def test_resolve_runtime_uses_cpu_when_cuda_configured_without_nvidia(monkeypatch) -> None:
    monkeypatch.setenv("INTERVIEW_GPU_VENDOR", "amd")
    config = resolve_runtime(
        {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
            "whisper_fallback_model": "small",
        }
    )
    assert config == RuntimeConfig(model="small", device="cpu", compute_type="int8")


def test_resolve_runtime_selects_whisper_cpp_for_amd_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("INTERVIEW_GPU_VENDOR", "amd")
    monkeypatch.setenv("INTERVIEW_WHISPERCPP_EXE", "C:/tools/whisper-cli.exe")
    monkeypatch.setenv("INTERVIEW_WHISPERCPP_MODEL", "C:/models/ggml-small.bin")
    config = resolve_runtime(
        {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
        }
    )
    assert config == RuntimeConfig(
        model="C:/models/ggml-small.bin",
        device="vulkan",
        compute_type="int8",
        backend="whisper_cpp",
    )


def test_resolve_runtime_selects_openvino_for_intel_gpu(monkeypatch) -> None:
    monkeypatch.setenv("INTERVIEW_GPU_VENDOR", "intel")
    config = resolve_runtime(
        {
            "whisper_model": "large-v3",
            "whisper_openvino_model": "OpenVINO/whisper-small-int8-ov",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
        }
    )
    assert config == RuntimeConfig(
        model="OpenVINO/whisper-small-int8-ov",
        device="GPU",
        compute_type="fp16",
        backend="openvino_genai",
    )


def test_resolve_runtime_keeps_cuda_when_nvidia_detected(monkeypatch) -> None:
    monkeypatch.setenv("INTERVIEW_GPU_VENDOR", "nvidia")
    config = resolve_runtime(
        {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
        }
    )
    assert config == RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")


def test_fallback_from_exception_returns_cpu_runtime_for_device_errors() -> None:
    preferred = RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")
    fallback = fallback_from_exception(RuntimeError("CUDA device unavailable"), preferred, {})
    assert fallback == RuntimeConfig(model="large-v3", device="cpu", compute_type="int8")


def test_fallback_from_exception_honors_fallback_model() -> None:
    preferred = RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")
    fallback = fallback_from_exception(
        RuntimeError("invalid device"),
        preferred,
        {"whisper_fallback_model": "small"},
    )
    assert fallback == RuntimeConfig(model="small", device="cpu", compute_type="int8")


def test_fallback_from_exception_detects_cublas_dll_errors() -> None:
    preferred = RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")
    fallback = fallback_from_exception(
        RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"),
        preferred,
        {},
    )
    assert fallback == RuntimeConfig(model="large-v3", device="cpu", compute_type="int8")


def test_fallback_from_exception_detects_missing_nvidia_driver_errors() -> None:
    preferred = RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")
    fallback = fallback_from_exception(
        RuntimeError("There is no NVIDIA driver on your system"),
        preferred,
        {},
    )
    assert fallback == RuntimeConfig(model="large-v3", device="cpu", compute_type="int8")


def test_fallback_from_exception_returns_none_for_non_device_errors() -> None:
    preferred = RuntimeConfig(model="large-v3", device="cuda", compute_type="float16")
    fallback = fallback_from_exception(RuntimeError("disk full"), preferred, {})
    assert fallback is None


def test_persist_runtime_choice_updates_settings() -> None:
    settings: dict[str, str] = {}
    runtime = RuntimeConfig(model="small", device="cpu", compute_type="int8")
    persist_runtime_choice(settings, runtime, "cpu_fallback")
    assert settings == {
        "whisper_runtime_model": "small",
        "whisper_runtime_device": "cpu",
        "whisper_runtime_compute_type": "int8",
        "whisper_runtime_backend": "faster_whisper",
        "whisper_runtime_mode": "cpu_fallback",
    }
