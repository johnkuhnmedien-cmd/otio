"""Intro-Settings dürfen Widget-Keys nicht nach der Instanziierung setzen."""

from __future__ import annotations

import pytest
import streamlit as st

from otio_app.services.voiceover_generation.models import IntroHookSettings
from otio_app.ui.voiceover_generation import intro_tab


class _FakeSessionState(dict):
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
    return state


def test_apply_intro_settings_to_session_before_widgets(fake_st) -> None:
    settings = IntroHookSettings(
        project_id="p",
        target_words=90,
        word_tolerance_percent=15,
        tone="documentary",
        freeform_rule_for_llm="Números por extenso.",
        forbidden_phrases=["wow"],
        must_include=["história"],
    )
    intro_tab.apply_intro_settings_to_session("p", settings)
    keys = intro_tab._intro_settings_keys("p")
    assert fake_st[keys["target_words"]] == 90
    assert fake_st[keys["tone"]] == "documentary"
    assert fake_st[keys["freeform_rule"]] == "Números por extenso."
    assert fake_st[keys["forbidden_phrases"]] == "wow"
    assert fake_st[keys["must_include"]] == "história"


def test_apply_intro_settings_after_widget_raises(fake_st) -> None:
    keys = intro_tab._intro_settings_keys("p")
    fake_st[keys["tone"]] = "old"
    fake_st.mark_widget(keys["tone"])
    with pytest.raises(st.errors.StreamlitAPIException):
        intro_tab.apply_intro_settings_to_session(
            "p",
            IntroHookSettings(project_id="p", tone="new"),
        )
