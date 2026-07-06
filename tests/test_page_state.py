"""Tests für Seiten-Widget-Bereinigung."""

from __future__ import annotations

from otio_app.ui.navigation import PAGE_MAPPING
from otio_app.ui.page_state import clear_page_widget_state


class _FakeSessionState(dict):
    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        dict.__delitem__(self, key)


def test_clear_mapping_widgets(monkeypatch):
    import streamlit as st

    fake_state = _FakeSessionState(
        {
            "mapping_folder_abc_0": "Big Sur",
            "confirm_mapping_abc": True,
            "plan_min_xyz": 2.0,
            "sidebar_nav": PAGE_MAPPING,
        }
    )
    monkeypatch.setattr(st, "session_state", fake_state, raising=False)

    clear_page_widget_state(PAGE_MAPPING)

    assert "mapping_folder_abc_0" not in fake_state
    assert "confirm_mapping_abc" not in fake_state
    assert fake_state["plan_min_xyz"] == 2.0
