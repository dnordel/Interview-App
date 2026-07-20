from __future__ import annotations

import importlib
import json
import logging
import app_logging


def _reset_root_handlers() -> None:
    root_logger = logging.getLogger()
    handlers = list(root_logger.handlers)
    for handler in handlers:
        root_logger.removeHandler(handler)
        handler.close()


def test_initialize_app_logging_creates_fixed_log_file(tmp_path, monkeypatch) -> None:
    _reset_root_handlers()
    module = importlib.reload(app_logging)

    monkeypatch.delenv("INTERVIEW_APP_DEBUG", raising=False)
    monkeypatch.delenv("INTERVIEW_APP_LOG_LEVEL", raising=False)

    log_path = module.initialize_app_logging(app_root=tmp_path)

    assert log_path == tmp_path / "logs" / "interview-app.log"
    assert log_path.exists()


def test_initialize_app_logging_redacts_candidate_data(tmp_path, monkeypatch) -> None:
    _reset_root_handlers()
    module = importlib.reload(app_logging)
    monkeypatch.delenv("INTERVIEW_APP_DEBUG", raising=False)

    log_path = module.initialize_app_logging(app_root=tmp_path)
    logger = logging.getLogger("tests.app_logging")
    logger.info(
        "candidate contact john.doe@example.com +1 415 555 0100",
        extra={"candidate_name": "John Doe", "phone": "+1 415 555 0100"},
    )

    for handler in logging.getLogger().handlers:
        handler.flush()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])

    assert payload["message"] == "candidate contact [REDACTED_EMAIL] [REDACTED_PHONE]"
    assert payload["context"]["candidate_name"] == "[REDACTED]"
    assert payload["context"]["phone"] == "[REDACTED]"


def test_initialize_app_logging_honors_debug_env(tmp_path, monkeypatch) -> None:
    _reset_root_handlers()
    module = importlib.reload(app_logging)

    monkeypatch.setenv("INTERVIEW_APP_DEBUG", "true")
    monkeypatch.delenv("INTERVIEW_APP_LOG_LEVEL", raising=False)

    module.initialize_app_logging(app_root=tmp_path)
    assert logging.getLogger().level == logging.DEBUG
