from __future__ import annotations

from types import SimpleNamespace

from interview_app.bootstrap import wire_views
from interview_app.views import CandidateSetupView, SignalReferenceView, StartScreenView


class _CallTracker:
    def __init__(self) -> None:
        self.called = False
        self.args: tuple[object, ...] = ()
        self.kwargs: dict[str, object] = {}

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.called = True
        self.args = args
        self.kwargs = kwargs


def test_wire_views_constructs_singleton_view_instances() -> None:
    app = SimpleNamespace(page_frame=object())

    wire_views(app)

    assert isinstance(app.start_screen_view, StartScreenView)
    assert isinstance(app.candidate_setup_view, CandidateSetupView)
    assert isinstance(app.signal_reference_view, SignalReferenceView)


def test_interview_app_view_wrappers_delegate() -> None:
    app = SimpleNamespace(
        start_screen_view=SimpleNamespace(render=_CallTracker(), render_today_dashboard=_CallTracker()),
        candidate_setup_view=SimpleNamespace(render=_CallTracker()),
        signal_reference_view=SimpleNamespace(
            render_progress_strip=_CallTracker(),
            show_disqualifier_reference=_CallTracker(),
            render_signal_examples=_CallTracker(),
        ),
    )

    show_start_screen = lambda: app.start_screen_view.render()
    render_today_dashboard = lambda parent: app.start_screen_view.render_today_dashboard(parent)
    show_candidate_info = lambda: app.candidate_setup_view.render()
    render_progress_strip = lambda parent, idx, scored: app.signal_reference_view.render_progress_strip(parent, idx, is_scored=scored)
    show_disqualifier_reference = lambda: app.signal_reference_view.show_disqualifier_reference()
    render_signal_examples = lambda parent, trait_id: app.signal_reference_view.render_signal_examples(parent, trait_id)

    parent = object()
    show_start_screen()
    render_today_dashboard(parent)
    show_candidate_info()
    render_progress_strip(parent, 2, True)
    show_disqualifier_reference()
    render_signal_examples(parent, "trait_1")

    assert app.start_screen_view.render.called
    assert app.start_screen_view.render_today_dashboard.called
    assert app.candidate_setup_view.render.called
    assert app.signal_reference_view.render_progress_strip.called
    assert app.signal_reference_view.render_progress_strip.kwargs["is_scored"] is True
    assert app.signal_reference_view.show_disqualifier_reference.called
    assert app.signal_reference_view.render_signal_examples.args == (parent, "trait_1")
