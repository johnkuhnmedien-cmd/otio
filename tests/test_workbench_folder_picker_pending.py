"""Regression: folder quick-select must not write widget keys after instantiate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import streamlit as st

from otio_app.services.folder_analysis_status import FolderAnalysisState
from otio_app.ui import project_workbench as pw


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

    def multiselect(*_a, key=None, **_k):
        state.mark_widget(key)
        return list(state.get(key, []))

    monkeypatch.setattr(st, "multiselect", multiselect)
    monkeypatch.setattr(st, "caption", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "columns", lambda n: [MagicMock() for _ in range(n)])
    monkeypatch.setattr(st, "success", lambda *_a, **_k: None)

    clicks: dict[str, bool] = {}

    def button(label, key=None, **_k):
        return bool(clicks.get(key, False))

    monkeypatch.setattr(st, "button", button)

    reruns = {"count": 0}

    def rerun():
        reruns["count"] += 1
        raise SystemExit("rerun")

    monkeypatch.setattr(st, "rerun", rerun)
    return state, clicks, reruns


def test_nur_offene_ordner_sets_pending_then_applies_before_widget(fake_st, monkeypatch):
    state, clicks, reruns = fake_st
    project = SimpleNamespace(
        id="proj-1",
        asset_subdir_names=["Athens", "Crete", "Done"],
        selected_asset_subdirs=["Athens", "Crete", "Done"],
    )
    monkeypatch.setattr(
        pw,
        "_get_folder_status_cache",
        lambda _p: {
            "Athens": FolderAnalysisState.PENDING,
            "Crete": FolderAnalysisState.PARTIAL,
            "Done": FolderAnalysisState.COMPLETE,
        },
    )
    monkeypatch.setattr(pw, "format_folder_with_status", lambda *_a, **_k: "label")

    clicks["open_proj-1"] = True
    with pytest.raises(SystemExit, match="rerun"):
        pw._render_folder_picker(project)

    assert reruns["count"] == 1
    assert state.get("workbench_folders_pending_proj-1") == ["Athens", "Crete"]
    # Click-run must not assign the widget-bound key after multiselect exists.
    assert "workbench_folders_proj-1" in state._widget_keys

    # Next run: Streamlit resets widget-instantiation tracking each script run.
    state._widget_keys.clear()
    clicks["open_proj-1"] = False
    selected = pw._render_folder_picker(project)
    assert "workbench_folders_pending_proj-1" not in state
    assert state["workbench_folders_proj-1"] == ["Athens", "Crete"]
    assert selected == ["Athens", "Crete"]
