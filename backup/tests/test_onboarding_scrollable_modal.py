from __future__ import annotations

from types import SimpleNamespace

import onboarding_scrollable_modal


class _FakeWidget:
    def __init__(self, parent=None, **kwargs):
        self.parent = parent
        self.kwargs = kwargs
        self.grid_calls = []
        self.columnconfigure_calls = []
        self.rowconfigure_calls = []
        self.bindings = {}

    def grid(self, *args, **kwargs):
        self.grid_calls.append((args, kwargs))

    def columnconfigure(self, *args, **kwargs):
        self.columnconfigure_calls.append((args, kwargs))

    def rowconfigure(self, *args, **kwargs):
        self.rowconfigure_calls.append((args, kwargs))

    def bind(self, event, handler, add=None):
        self.bindings[event] = handler
        return None


class _FakeDialog(_FakeWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent=parent, **kwargs)
        self.window_title = None

    def title(self, value):
        self.window_title = value


class _FakeCanvas(_FakeWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent=parent, **kwargs)
        self.configure_calls = []
        self.itemconfigure_calls = []

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)

    def bbox(self, _target):
        return (0, 0, 320, 640)

    def itemconfigure(self, window_id, **kwargs):
        self.itemconfigure_calls.append((window_id, kwargs))


class _FakeScrollableArea:
    def __init__(self):
        self.shell = _FakeWidget()
        self.canvas = _FakeCanvas()
        self.interior = _FakeWidget()
        self.scrollbar = _FakeWidget()
        self.window_id = 7


def test_build_scrollable_modal_container_constructs_scrollable_shell(monkeypatch):
    scrollable_area = _FakeScrollableArea()
    capture = {}

    monkeypatch.setattr(onboarding_scrollable_modal.tk, "Toplevel", _FakeDialog)
    monkeypatch.setattr(onboarding_scrollable_modal.ttk, "Frame", _FakeWidget)
    monkeypatch.setattr(
        onboarding_scrollable_modal,
        "build_scrollable_canvas_area",
        lambda *args, **kwargs: scrollable_area,
    )
    monkeypatch.setattr(
        onboarding_scrollable_modal,
        "bind_canvas_mousewheel",
        lambda canvas, *, activate_widgets: capture.update({
            "canvas": canvas,
            "activate_widgets": activate_widgets,
        }),
    )

    container = onboarding_scrollable_modal.build_scrollable_modal_container(object(), title="Email / Reminder Settings")

    assert container.dialog.window_title == "Email / Reminder Settings"
    assert container.canvas is scrollable_area.canvas
    assert container.interior is scrollable_area.interior
    assert container.scrollbar is scrollable_area.scrollbar
    assert "<Configure>" in container.canvas.bindings
    assert "<Configure>" in container.interior.bindings
    assert container.button_bar.grid_calls
    assert capture == {
        "canvas": scrollable_area.canvas,
        "activate_widgets": (scrollable_area.canvas, scrollable_area.interior),
    }

    container.interior.bindings["<Configure>"](SimpleNamespace())
    assert container.canvas.configure_calls[-1] == {"scrollregion": (0, 0, 320, 640)}

    container.canvas.bindings["<Configure>"](SimpleNamespace(width=480))
    assert container.canvas.itemconfigure_calls[-1] == (7, {"width": 480})
