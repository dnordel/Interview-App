from __future__ import annotations

from typing import Any

from interview_app.view_protocols import RouterFlowState, RouterNavigator, RouterRenderer, RouterState


class AppRouterPorts(RouterNavigator, RouterRenderer, RouterFlowState):
    def __init__(self, app: Any) -> None:
        self._app = app

    def state(self) -> RouterState:
        return self._app.state

    def bind_all(self, key_combo: str, callback: Any) -> None:
        self._app.bind_all(key_combo, callback)

    def footer_action(self, label: str) -> Any:
        return self._app._footer_actions_by_label.get(label)

    def open_settings(self) -> None:
        self._app.open_settings()

    def open_question_editor(self) -> None:
        self._app.open_question_editor()

    def show_keyboard_shortcuts_help(self) -> None:
        self._app.show_keyboard_shortcuts_help()

    def show_start_screen(self) -> None:
        self._app.show_start_screen()

    def show_candidate_info(self) -> None:
        self._app.show_candidate_info()

    def show_trait_screen_by_trait_id(self, flow_idx: int, trait: dict[str, str]) -> None:
        self._app.show_trait_screen_by_trait_id(flow_idx, trait)

    def show_custom_question_item_screen(self, flow_idx: int, custom_question: dict[str, str]) -> None:
        self._app.show_custom_question_item_screen(flow_idx, custom_question)

    def flow_len(self) -> int:
        return self._app._flow_len()

    def flow_item(self, index: int) -> dict[str, str] | None:
        return self._app._get_flow_item(index)

    def mark_flow_timestamp(self, flow_index: int) -> None:
        self._app._mark_flow_timestamp(flow_index)

    def start_question_recording_for_flow(self, flow_index: int) -> None:
        self._app._start_question_recording_for_flow(flow_index)

    def trait_by_id(self, trait_id: str) -> dict[str, str] | None:
        return self._app._trait_by_id(trait_id)

    def custom_by_id(self, custom_id: str) -> dict[str, str] | None:
        return self._app._custom_by_id(custom_id)

    def build_active_flow(self, track: str) -> None:
        self._app._build_active_flow(track)


class UiRouter:
    ROUTE_START = "start"
    ROUTE_CANDIDATE_INFO = "candidate_info"
    ROUTE_QUESTION_TRAIT = "question_trait"
    ROUTE_QUESTION_CUSTOM = "question_custom"
    ROUTE_HISTORY = "history"

    ROUTE_FALLBACK = ROUTE_CANDIDATE_INFO

    def __init__(self, navigator: RouterNavigator, renderer: RouterRenderer, flow_state: RouterFlowState) -> None:
        self.navigator = navigator
        self.renderer = renderer
        self.flow_state = flow_state

    def setup_shortcuts(self) -> None:
        bindings = self._shortcut_bindings()
        for key_combo, callback in bindings.items():
            self.renderer.bind_all(key_combo, callback)

    def _shortcut_bindings(self) -> dict[str, Any]:
        return {
            "<Control-n>": lambda _e: self._invoke_footer_action("Next"),
            "<Control-Right>": lambda _e: self._invoke_footer_action("Next"),
            "<Control-b>": lambda _e: self._invoke_footer_action("Back"),
            "<Control-Left>": lambda _e: self._invoke_footer_action("Back"),
            "<Control-s>": lambda _e: self._invoke_footer_action("Save Draft"),
            "<Control-Shift-F>": lambda _e: self._run_finalize_shortcut(),
            "<Control-,>": lambda _e: self.navigator.open_settings(),
            "<Control-e>": lambda _e: self.navigator.open_question_editor(),
            "<F1>": lambda _e: self.navigator.show_keyboard_shortcuts_help(),
        }

    def _invoke_footer_action(self, label: str) -> str | None:
        command = self.renderer.footer_action(label)
        if callable(command):
            command()
        return "break"

    def _run_finalize_shortcut(self) -> str | None:
        for label in ("Finalize", "Continue"):
            command = self.renderer.footer_action(label)
            if callable(command):
                command()
                break
        return "break"

    def flow_item_route(self, item: dict[str, Any] | None) -> str:
        if not item:
            return self.ROUTE_FALLBACK
        item_type = item.get("type")
        if item_type == "trait":
            return self.ROUTE_QUESTION_TRAIT
        if item_type == "custom":
            return self.ROUTE_QUESTION_CUSTOM
        return self.ROUTE_FALLBACK

    def route_to(self, route: str, **kwargs: Any) -> None:
        if route == self.ROUTE_START:
            self.navigator.show_start_screen()
            return
        if route == self.ROUTE_CANDIDATE_INFO:
            self.navigator.show_candidate_info()
            return
        if route == self.ROUTE_HISTORY:
            self.navigator.show_start_screen()
            return
        if route == self.ROUTE_QUESTION_TRAIT:
            self.navigator.show_trait_screen_by_trait_id(kwargs["flow_idx"], kwargs["trait"])
            return
        if route == self.ROUTE_QUESTION_CUSTOM:
            self.navigator.show_custom_question_item_screen(kwargs["flow_idx"], kwargs["custom_question"])
            return
        self.navigator.show_candidate_info()

    def show_flow_screen(self, flow_index: int) -> None:
        if not self.flow_state.state().track:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return
        if flow_index < 0:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return

        flow_len = self.flow_state.flow_len()
        if flow_index >= flow_len:
            if flow_len > 0:
                self.show_flow_screen(flow_len - 1)
                return
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return

        item = self.flow_state.flow_item(flow_index)
        if not item:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return

        self.flow_state.mark_flow_timestamp(flow_index)
        self.flow_state.start_question_recording_for_flow(flow_index)
        self.flow_state.state().current_index = flow_index + 1

        route = self.flow_item_route(item)
        if route == self.ROUTE_QUESTION_TRAIT:
            self._route_trait(flow_index, str(item.get("id", "")))
            return
        if route == self.ROUTE_QUESTION_CUSTOM:
            self._route_custom(flow_index, str(item.get("id", "")))
            return
        self.route_to(self.ROUTE_CANDIDATE_INFO)

    def _route_trait(self, flow_index: int, trait_id: str) -> None:
        trait = self.flow_state.trait_by_id(trait_id)
        if trait:
            self.route_to(self.ROUTE_QUESTION_TRAIT, flow_idx=flow_index, trait=trait)
            return
        self._route_after_navigation_failure(flow_index)

    def _route_custom(self, flow_index: int, custom_id: str) -> None:
        custom_question = self.flow_state.custom_by_id(custom_id)
        if custom_question:
            self.route_to(self.ROUTE_QUESTION_CUSTOM, flow_idx=flow_index, custom_question=custom_question)
            return
        self._route_after_navigation_failure(flow_index)

    def _route_after_navigation_failure(self, flow_index: int) -> None:
        track = self.flow_state.state().track
        if not track:
            self.route_to(self.ROUTE_FALLBACK)
            return
        self.flow_state.build_active_flow(track)
        refreshed_item = self.flow_state.flow_item(flow_index)
        if refreshed_item:
            self.show_flow_screen(flow_index)
            return
        self.route_to(self.ROUTE_FALLBACK)
