"""Regression: Videotitel erzeugen must not write the widget key after instantiate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import streamlit as st

from otio_app.services.voiceover_generation.models import ProjectBrief
from otio_app.ui.voiceover_generation import project_brief_tab as brief_tab


class _FakeSessionState(dict):
    """Mirrors Streamlit: widget-bound keys become immutable after instantiate."""

    def __init__(self) -> None:
        super().__init__()
        self._widget_keys: set[str] = set()

    def mark_widget(self, key: str) -> None:
        self._widget_keys.add(key)

    def __setitem__(self, key, value):  # type: ignore[no-untyped-def]
        if key in self._widget_keys:
            raise st.errors.StreamlitAPIException(
                f"st.session_state.{key} cannot be modified after the widget "
                f"with key {key} is instantiated."
            )
        super().__setitem__(key, value)


@pytest.fixture()
def fake_st(monkeypatch: pytest.MonkeyPatch):
    state = _FakeSessionState()
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(st, "success", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "error", lambda *_a, **_k: None)
    return state


def test_queue_title_applies_before_widget_not_after(fake_st) -> None:
    state = fake_st
    project_id = "proj-1"
    title_key = brief_tab._key(project_id, "video_title")
    state[title_key] = ""
    state.mark_widget(title_key)

    with pytest.raises(st.errors.StreamlitAPIException, match="cannot be modified"):
        state[title_key] = "As maravilhas da Grécia"

    brief_tab._queue_pending_title(
        project_id,
        "As maravilhas da Grécia",
        flash="Titel: As maravilhas da Grécia",
    )
    assert state[title_key] == ""
    assert state[brief_tab._pending_title_key(project_id)] == "As maravilhas da Grécia"

    state._widget_keys.clear()
    project = SimpleNamespace(id=project_id)
    brief_tab._hydrate_brief_session(project)
    assert brief_tab._pending_title_key(project_id) not in state
    assert state[title_key] == "As maravilhas da Grécia"


def test_queue_brief_applies_before_widget_not_after(fake_st) -> None:
    state = fake_st
    project_id = "proj-1"
    title_key = brief_tab._key(project_id, "video_title")
    state[title_key] = "alt"
    state.mark_widget(title_key)

    queued = ProjectBrief(project_id=project_id, video_title="neu", language="PT")
    brief_tab._queue_pending_brief(project_id, queued)
    assert state[title_key] == "alt"

    state._widget_keys.clear()
    project = SimpleNamespace(id=project_id)
    brief_tab._hydrate_brief_session(project)
    assert brief_tab._pending_brief_key(project_id) not in state
    assert state[title_key] == "neu"
    assert state[brief_tab._key(project_id, "language")] == "PT"
