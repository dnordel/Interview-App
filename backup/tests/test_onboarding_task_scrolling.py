from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "onboarding_app.pyw"
loader = SourceFileLoader("onboarding_app_task_scrolling", str(MODULE_PATH))
spec = spec_from_loader(loader.name, loader)
onboarding_app = module_from_spec(spec)
loader.exec_module(onboarding_app)


class _FakeCanvas:
    def __init__(self):
        self.configure_calls = []
        self.bbox_calls = []
        self.itemconfigure_calls = []

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)

    def bbox(self, target):
        self.bbox_calls.append(target)
        return (0, 0, 500, 900)

    def itemconfigure(self, window_id, **kwargs):
        self.itemconfigure_calls.append((window_id, kwargs))


class _FakeWidget:
    def __init__(self):
        self.bindings = {}

    def bind(self, event, handler, add=None):
        self.bindings[event] = handler
        return None


def test_sync_canvas_updates_scrollregion_after_task_content_changes():
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.task_canvas = _FakeCanvas()

    onboarding_app.OnboardingTrackerApp._sync_canvas(app, SimpleNamespace())

    assert app.task_canvas.bbox_calls == ["all"]
    assert app.task_canvas.configure_calls[-1] == {"scrollregion": (0, 0, 500, 900)}


def test_on_canvas_resize_updates_task_window_width():
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.task_canvas = _FakeCanvas()
    app.task_window = 14

    onboarding_app.OnboardingTrackerApp._on_canvas_resize(app, SimpleNamespace(width=420))

    assert app.task_canvas.itemconfigure_calls[-1] == (14, {"width": 420})


def test_bind_task_widget_visibility_scrolls_focused_task_into_view(monkeypatch):
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.task_canvas = object()
    widget = _FakeWidget()
    captured = {}

    monkeypatch.setattr(
        onboarding_app,
        "scroll_widget_into_view",
        lambda canvas, current_widget: captured.update({
            "canvas": canvas,
            "widget": current_widget,
        }),
    )

    onboarding_app.OnboardingTrackerApp._bind_task_widget_visibility(app, widget)
    widget.bindings["<FocusIn>"](SimpleNamespace(widget=widget))

    assert captured == {"canvas": app.task_canvas, "widget": widget}
