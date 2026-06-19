from __future__ import annotations

from types import SimpleNamespace

import onboarding_operations


class _FakeWidget:
    def __init__(self, parent=None, **kwargs):
        self.parent = parent
        self.kwargs = kwargs
        self.bindings = {}
        self.bound_all = {}
        self.unbound_all = []
        self.grid_calls = []
        self.columnconfigure_calls = []
        self.rowconfigure_calls = []

    def bind(self, event, handler, add=None):
        self.bindings[event] = handler
        return None

    def bind_all(self, event, handler):
        self.bound_all[event] = handler

    def unbind_all(self, event):
        self.unbound_all.append(event)

    def grid(self, *args, **kwargs):
        self.grid_calls.append((args, kwargs))

    def columnconfigure(self, *args, **kwargs):
        self.columnconfigure_calls.append((args, kwargs))

    def rowconfigure(self, *args, **kwargs):
        self.rowconfigure_calls.append((args, kwargs))


class _FakeCanvas(_FakeWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent=parent, **kwargs)
        self.configure_calls = []
        self.itemconfigure_calls = []
        self.scroll_calls = []
        self.moveto_calls = []
        self.updated = False

    def create_window(self, coords, window, anchor):
        self.created_window = {"coords": coords, "window": window, "anchor": anchor}
        return 11

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)

    config = configure

    def yview(self, *_args, **_kwargs):
        return None

    def yview_scroll(self, amount, units):
        self.scroll_calls.append((amount, units))

    def yview_moveto(self, fraction):
        self.moveto_calls.append(fraction)

    def winfo_toplevel(self):
        return self.top_level

    def bbox(self, _target):
        return (0, 0, 300, 1200)

    def update_idletasks(self):
        self.updated = True

    def canvasy(self, value):
        return value + 100

    def winfo_height(self):
        return 200

    def winfo_rooty(self):
        return 50


class _FakeScrollbar(_FakeWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent=parent, **kwargs)
        self.set_calls = []

    def set(self, *args):
        self.set_calls.append(args)


class _FakeFocusWidget(_FakeWidget):
    def __init__(self, root_y, height):
        super().__init__()
        self._root_y = root_y
        self._height = height

    def winfo_rooty(self):
        return self._root_y

    def winfo_height(self):
        return self._height


def test_build_scrollable_canvas_area_wires_canvas_and_scrollbar(monkeypatch):
    monkeypatch.setattr(onboarding_operations.tk, "Canvas", _FakeCanvas)
    monkeypatch.setattr(onboarding_operations.ttk, "Frame", _FakeWidget)
    monkeypatch.setattr(onboarding_operations.ttk, "Scrollbar", _FakeScrollbar)

    area = onboarding_operations.build_scrollable_canvas_area(
        object(),
        interior_padding=6,
        canvas_kwargs={"takefocus": 0},
    )

    assert area.canvas.created_window["window"] is area.interior
    assert area.canvas.created_window["anchor"] == "nw"
    assert area.scrollbar.kwargs["command"] == area.canvas.yview
    assert area.canvas.configure_calls[-1]["yscrollcommand"] == area.scrollbar.set
    assert area.scrollbar.grid_calls[-1][1]["column"] == 1


def test_bind_canvas_mousewheel_supports_focus_and_cross_platform_events():
    canvas = _FakeCanvas()
    canvas.top_level = _FakeWidget()
    interior = _FakeWidget()

    onboarding_operations.bind_canvas_mousewheel(canvas, activate_widgets=(canvas, interior))

    canvas.bindings["<FocusIn>"](SimpleNamespace())
    assert set(canvas.bound_all) == {"<MouseWheel>", "<Button-4>", "<Button-5>"}

    mousewheel = canvas.bound_all["<MouseWheel>"]
    linux_up = canvas.bound_all["<Button-4>"]
    linux_down = canvas.bound_all["<Button-5>"]

    assert mousewheel(SimpleNamespace(delta=120)) == "break"
    assert linux_up(SimpleNamespace(num=4, delta=0)) == "break"
    assert linux_down(SimpleNamespace(num=5, delta=0)) == "break"
    assert canvas.scroll_calls == [(-1, "units"), (-1, "units"), (1, "units")]

    canvas.bindings["<FocusOut>"](SimpleNamespace())
    assert canvas.unbound_all[-3:] == ["<MouseWheel>", "<Button-4>", "<Button-5>"]


def test_scroll_widget_into_view_moves_for_hidden_widget():
    canvas = _FakeCanvas()
    widget = _FakeFocusWidget(root_y=320, height=80)

    onboarding_operations.scroll_widget_into_view(canvas, widget, padding=12)

    assert canvas.updated is True
    assert canvas.moveto_calls[-1] > 0
