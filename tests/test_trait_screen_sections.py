from question_screens import TraitScreenUI


class FakeVar:
    def __init__(self, value: bool):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


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
