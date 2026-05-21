from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import logging
from pathlib import Path


loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
InterviewApp = interview_app.InterviewApp


def test_report_callback_exception_logs_traceback_and_log_path(monkeypatch) -> None:
    stream = io.StringIO()
    test_logger = logging.getLogger("tests.interview_app.callback")
    test_logger.handlers = []
    test_logger.propagate = False
    test_logger.setLevel(logging.ERROR)
    test_logger.addHandler(logging.StreamHandler(stream))

    monkeypatch.setattr(interview_app, "logger", test_logger)
    monkeypatch.setattr(interview_app, "get_configured_log_path", lambda: Path("logs/test.log"))
    monkeypatch.setattr(interview_app, "write_crash_report", lambda **_: Path("logs/crash-reports/crash-test.log"))

    dialogs: list[tuple[str, str]] = []
    monkeypatch.setattr(interview_app.messagebox, "showerror", lambda title, msg: dialogs.append((title, msg)))

    app = InterviewApp.__new__(InterviewApp)
    app._app_log_path = Path("logs/test.log")

    try:
        raise RuntimeError("callback boom")
    except RuntimeError as exc:
        InterviewApp.report_callback_exception(app, RuntimeError, exc, exc.__traceback__)

    logged = stream.getvalue()
    assert "tk_callback_exception" in logged
    assert "callback boom" in logged
    assert dialogs
    # Intentionally normalize separators so this assertion passes on all OSes.
    assert "logs/test.log" in dialogs[0][1].replace("\\", "/")
    assert "logs/crash-reports/crash-test.log" in dialogs[0][1].replace("\\", "/")
