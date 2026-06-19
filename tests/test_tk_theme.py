from __future__ import annotations

import tk_theme


class _FakeRoot:
    def __init__(self) -> None:
        self.configs: list[dict[str, str]] = []

    def configure(self, **kwargs: str) -> None:
        self.configs.append(kwargs)


class _FakeStyle:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __init__(self, root: object) -> None:
        self.root = root

    def theme_use(self, name: str) -> None:
        self.calls.append(("theme_use", (name,), {}))

    def configure(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("configure", (name, *args), kwargs))

    def map(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("map", (name, *args), kwargs))


def test_theme_tokens_expose_professional_ops_palette() -> None:
    assert tk_theme.COLORS["app_bg"] == "#eef2f7"
    assert tk_theme.COLORS["surface"] == "#ffffff"
    assert tk_theme.SPACING["md"] == 10
    assert tk_theme.font_tuple(10, delta=2, weight="bold") == ("TkDefaultFont", 12, "bold")


def test_apply_professional_ops_theme_is_idempotent(monkeypatch) -> None:
    root = _FakeRoot()
    _FakeStyle.calls = []
    monkeypatch.setattr(tk_theme.ttk, "Style", _FakeStyle)

    first = tk_theme.apply_professional_ops_theme(root, font_size=11)
    second = tk_theme.apply_professional_ops_theme(root, font_size=11)

    assert isinstance(first, _FakeStyle)
    assert isinstance(second, _FakeStyle)
    assert root.configs == [{"background": tk_theme.COLORS["app_bg"]}, {"background": tk_theme.COLORS["app_bg"]}]
    assert ("configure", ("Treeview",), {"rowheight": 25, "background": "#ffffff", "fieldbackground": "#ffffff"}) in _FakeStyle.calls
