from ui_composition import TraitScreenUI, render_question_footer


class FakeVar:
    def __init__(self, value: bool):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeText:
    def __init__(self, value=""):
        self.value = value

    def get(self, *_args):
        return self.value


class FakeInlineValidation:
    def __init__(self):
        self.messages = []

    def clear(self):
        self.messages.clear()

    def show(self, **kwargs):
        self.messages.append(kwargs)


class FakeBody:
    def __init__(self):
        self.packed = False

    def pack(self, **_kwargs):
        self.packed = True

    def pack_forget(self):
        self.packed = False


class FakeButton:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        self.text = kwargs.get("text", self.text)

    def bind(self, *_args, **_kwargs):
        return None

    def pack(self, **_kwargs):
        return None


class FakeContainer:
    labels = []

    def __init__(self, *_args, **kwargs):
        self.text = kwargs.get("text", "")

    def pack(self, **_kwargs):
        return None


class FakeLogger:
    def __init__(self):
        self.calls = []

    def log_ux_click(self, **fields):
        self.calls.append(("ux.click", fields))

    def log_ux_completion(self, **fields):
        self.calls.append(("ux.completion", fields))


class FakeApp:
    def __init__(self):
        self.metrics_logger = FakeLogger()
        self.settings = {"font_size": 10}


def _build_ui():
    ui = TraitScreenUI.__new__(TraitScreenUI)
    ui.app = FakeApp()
    ui.tid = "classroom_management"
    ui.flow_idx = 2
    return ui


def test_toggle_section_updates_state_and_logs_event():
    ui = _build_ui()
    section_var = FakeVar(False)
    body = FakeBody()
    toggle_button = FakeButton()

    ui._toggle_section("descriptors", section_var, body, toggle_button)

    assert section_var.get() is True
    assert body.packed is True
    assert toggle_button.text == "Hide rubric detail"
    assert len(ui.app.metrics_logger.calls) == 1
    event_type, fields = ui.app.metrics_logger.calls[0]
    assert event_type == "ux.click"
    assert fields["app"] == "interview"
    assert fields["surface"] == "trait_screen"
    assert fields["target"] == "section_toggle"
    assert fields["section"] == "descriptors"
    assert fields["expanded"] is True


def test_toggle_section_collapses_when_expanded():
    ui = _build_ui()
    section_var = FakeVar(True)
    body = FakeBody()
    toggle_button = FakeButton()

    ui._toggle_section("samples", section_var, body, toggle_button)

    assert section_var.get() is False
    assert body.packed is False
    assert toggle_button.text == "Show sample answers"


def test_selected_signal_ids_ignores_legacy_manual_observation_vars():
    ui = _build_ui()
    ui.signal_selection_vars = {
        "S_ONE": FakeVar(True),
        "S_TWO": FakeVar(False),
        "S_THREE": FakeVar(True),
    }

    assert ui._selected_signal_ids() == []


def test_persist_state_clears_manual_observation_ids():
    ui = _build_ui()
    ui.flow_idx = 0
    ui.tid = "trait_1"
    ui.qualification_vars = None
    ui.inline_validation = FakeInlineValidation()
    ui.raw_var = FakeVar(4)
    ui.dq_var = FakeVar(False)
    ui.no_example_var = FakeVar(False)
    ui.v_text = FakeText("")
    ui.q_text = FakeText("question note")
    ui.t_text = FakeText("trait note")
    ui.score_widgets = []
    ui.signal_selection_vars = {
        "S_ONE": FakeVar(True),
        "S_TWO": FakeVar(False),
    }
    ui.app = type(
        "App",
        (),
        {
            "state": type(
                "State",
                (),
                {
                    "trait_inputs": {"trait_1": {}},
                    "current_index": 0,
                },
            )(),
            "metrics_logger": FakeLogger(),
        },
    )()

    assert ui.persist_state() is True

    state = ui.app.state.trait_inputs["trait_1"]
    assert state["selected_signal_ids"] == []
    assert state["question_notes"] == "question note"
    assert state["trait_notes"] == "trait note"


def test_persist_state_preserves_model_suggestions_and_clears_manual_selection():
    ui = _build_ui()
    ui.flow_idx = 0
    ui.tid = "trait_1"
    ui.qualification_vars = None
    ui.inline_validation = FakeInlineValidation()
    ui.raw_var = FakeVar(4)
    ui.dq_var = FakeVar(False)
    ui.no_example_var = FakeVar(False)
    ui.v_text = FakeText("")
    ui.q_text = FakeText("")
    ui.t_text = FakeText("")
    ui.score_widgets = []
    ui.signal_selection_vars = {"S_MANUAL": FakeVar(True)}
    ui.app = type(
        "App",
        (),
        {
            "state": type(
                "State",
                (),
                {
                    "trait_inputs": {
                        "trait_1": {
                            "model_signal_suggestions": [
                                {"signal_id": "S_MODEL", "confidence": 0.75, "rationale": "Model rationale."}
                            ]
                        }
                    },
                    "current_index": 0,
                },
            )(),
            "metrics_logger": FakeLogger(),
        },
    )()

    assert ui.persist_state() is True

    state = ui.app.state.trait_inputs["trait_1"]
    assert state["selected_signal_ids"] == []
    assert state["model_signal_suggestions"] == [
        {"signal_id": "S_MODEL", "confidence": 0.75, "rationale": "Model rationale."}
    ]


def test_render_signal_section_shows_model_suggestion_without_checkbox(monkeypatch):
    import ui_composition

    rendered_labels = []
    monkeypatch.setattr(ui_composition, "BooleanVar", FakeVar)
    monkeypatch.setattr(ui_composition.ttk, "LabelFrame", FakeContainer)

    class FakeLabel(FakeContainer):
        def __init__(self, *_args, **kwargs):
            rendered_labels.append(kwargs.get("text", ""))
            super().__init__(*_args, **kwargs)

    monkeypatch.setattr(ui_composition.ttk, "Label", FakeLabel)
    ui = _build_ui()
    ui.signal_selection_vars = {}
    ui.model_signal_suggestions = {"S_MODEL": {"confidence": 0.7, "rationale": "Matched phrase."}}

    ui._render_signal_section(FakeContainer(), "Core", [{"signal_id": "S_MODEL", "label": "Model signal"}])

    assert ui.signal_selection_vars == {}
    assert rendered_labels == ["Model signal: suggested by model (0.70): Matched phrase."]


def test_primary_viewport_renders_notes_once(monkeypatch):
    import ui_composition

    monkeypatch.setattr(ui_composition.ttk, "Label", FakeContainer)
    ui = _build_ui()
    calls = []
    ui._render_score_box = lambda _parent: calls.append("score")
    ui._render_disqualifier_box = lambda _parent: calls.append("dq")
    ui._render_notes = lambda _parent: calls.append("notes")

    ui._render_primary_viewport(FakeContainer())

    assert calls == ["score", "dq", "notes"]


def test_shared_question_footer_registers_same_actions_for_question_types():
    class FooterApp:
        def __init__(self):
            self.actions = {}
            self.exit_calls = []

        def set_footer_actions(self, left_actions=None, right_actions=None):
            self.actions = dict((left_actions or []) + (right_actions or []))

        def play_flow_question_audio(self, _flow_idx):
            return None

        def exit_current_interview(self, flow_idx, *, persist_current=None):
            self.exit_calls.append((flow_idx, persist_current))

    trait_app = FooterApp()
    custom_app = FooterApp()
    noop = lambda: None

    render_question_footer(
        trait_app,
        flow_idx=2,
        is_last=False,
        go_back=noop,
        skip_question=noop,
        save_draft=noop,
        continue_or_finalize=noop,
        persist_for_exit=noop,
    )
    render_question_footer(
        custom_app,
        flow_idx=2,
        is_last=False,
        go_back=noop,
        skip_question=noop,
        save_draft=noop,
        continue_or_finalize=noop,
        persist_for_exit=noop,
    )

    assert list(trait_app.actions) == ["Back", "Skip", "Save Draft", "Play Audio", "Exit", "Next"]
    assert list(custom_app.actions) == list(trait_app.actions)
    trait_app.actions["Exit"]()
    assert trait_app.exit_calls == [(2, noop)]


def test_partial_sample_answers_do_not_break_scored_guidance(monkeypatch):
    import ui_composition

    rendered_labels = []

    class FakeLabel(FakeContainer):
        def __init__(self, *_args, **kwargs):
            rendered_labels.append(kwargs.get("text", ""))
            super().__init__(*_args, **kwargs)

    monkeypatch.setattr(ui_composition.ttk, "LabelFrame", FakeContainer)
    monkeypatch.setattr(ui_composition.ttk, "Frame", FakeContainer)
    monkeypatch.setattr(ui_composition.ttk, "Label", FakeLabel)
    monkeypatch.setattr(ui_composition.ttk, "Button", lambda *_args, **_kwargs: FakeButton())
    monkeypatch.setattr(ui_composition, "tk", type("FakeTk", (), {"Button": lambda *_args, **_kwargs: FakeButton()}))

    ui = _build_ui()
    ui.samples_expanded_var = FakeVar(True)
    ui.keyboard_session = type("KeyboardSession", (), {"bind": lambda *_args, **_kwargs: None})()

    ui._render_disclosure_section(
        parent=FakeContainer(),
        frame_title="Sample answers",
        section_key="samples",
        section_var=ui.samples_expanded_var,
        line_values={"5": "Strong", "3": "Middle", "1": "Weak"},
    )

    assert "5: Strong" in rendered_labels
    assert "3: Middle" in rendered_labels
    assert "1: Weak" in rendered_labels
