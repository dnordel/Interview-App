from __future__ import annotations

from types import SimpleNamespace

from interview_app.ui_router import UiRouter


class _FakeRouterPorts:
    def __init__(self) -> None:
        self.bindings: dict[str, object] = {}
        self._footer_actions_by_label: dict[str, object] = {}
        self.track = "Toddler"
        self._state = SimpleNamespace(track=self.track, current_index=0)
        self._flow = [{"type": "trait", "id": "trait_1"}]
        self.calls: list[tuple[str, object]] = []

    def bind_all(self, key_combo: str, callback: object) -> None:
        self.bindings[key_combo] = callback

    def footer_action(self, label: str) -> object | None:
        return self._footer_actions_by_label.get(label)

    def open_settings(self) -> None:
        self.calls.append(("open_settings", None))

    def open_question_editor(self) -> None:
        self.calls.append(("open_question_editor", None))

    def show_keyboard_shortcuts_help(self) -> None:
        self.calls.append(("show_keyboard_shortcuts_help", None))

    def show_start_screen(self) -> None:
        self.calls.append(("show_start_screen", None))

    def show_candidate_info(self) -> None:
        self.calls.append(("show_candidate_info", None))

    def show_trait_screen_by_trait_id(self, flow_idx: int, trait: dict[str, str]) -> None:
        self.calls.append(("show_trait_screen_by_trait_id", (flow_idx, trait)))

    def show_custom_question_item_screen(self, flow_idx: int, custom_question: dict[str, str]) -> None:
        self.calls.append(("show_custom_question_item_screen", (flow_idx, custom_question)))

    def state(self) -> SimpleNamespace:
        return self._state

    def flow_len(self) -> int:
        return len(self._flow)

    def flow_item(self, index: int) -> dict[str, str] | None:
        if 0 <= index < len(self._flow):
            return self._flow[index]
        return None

    def mark_flow_timestamp(self, flow_index: int) -> None:
        self.calls.append(("mark", flow_index))

    def start_question_recording_for_flow(self, flow_index: int) -> None:
        self.calls.append(("record", flow_index))

    def trait_by_id(self, _trait_id: str) -> dict[str, str] | None:
        return None

    def custom_by_id(self, _custom_id: str) -> dict[str, str] | None:
        return None

    def build_active_flow(self, _track: str) -> None:
        self.calls.append(("rebuild", None))
        self._flow = []


def test_flow_item_route_selection() -> None:
    ports = _FakeRouterPorts()
    router = UiRouter(ports, ports, ports)

    assert router.flow_item_route({"type": "trait", "id": "x"}) == router.ROUTE_QUESTION_TRAIT
    assert router.flow_item_route({"type": "custom", "id": "x"}) == router.ROUTE_QUESTION_CUSTOM
    assert router.flow_item_route({"type": "unknown", "id": "x"}) == router.ROUTE_FALLBACK
    assert router.flow_item_route(None) == router.ROUTE_FALLBACK


def test_shortcut_bindings_preserve_existing_key_combos() -> None:
    ports = _FakeRouterPorts()
    router = UiRouter(ports, ports, ports)

    router.setup_shortcuts()

    expected = {
        "<Control-n>",
        "<Control-Right>",
        "<Control-b>",
        "<Control-Left>",
        "<Control-s>",
        "<Control-Shift-F>",
        "<Control-,>",
        "<Control-e>",
        "<F1>",
    }
    assert expected.issubset(set(ports.bindings.keys()))


def test_show_flow_screen_falls_back_on_navigation_failure() -> None:
    ports = _FakeRouterPorts()
    router = UiRouter(ports, ports, ports)

    router.show_flow_screen(0)

    assert ("rebuild", None) in ports.calls
    assert ports.calls[-1][0] == "show_candidate_info"
