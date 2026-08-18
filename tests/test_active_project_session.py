"""Persistiertes aktives Projekt über App-Neustarts."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import otio_app.ui.active_project_session as session_mod
from otio_app.models import ProjectMode
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY


def test_save_and_load_last_active_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_mod, "last_active_project_path", lambda: tmp_path / "last.txt")
    assert session_mod.load_last_active_project_id() is None
    session_mod.save_last_active_project_id("proj-1")
    assert session_mod.load_last_active_project_id() == "proj-1"


def test_restore_skips_when_session_already_has_project(monkeypatch) -> None:
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {ACTIVE_PROJECT_KEY: "already"})
    monkeypatch.setattr(session_mod, "load_last_active_project_id", lambda: "from-disk")
    monkeypatch.setattr(
        session_mod,
        "get_project_by_id",
        lambda _pid: SimpleNamespace(),
    )
    restored = session_mod.restore_active_project_into_session()
    assert restored == "already"
    assert st.session_state[ACTIVE_PROJECT_KEY] == "already"


def test_restore_loads_last_enhanced_project(monkeypatch) -> None:
    import streamlit as st

    fake = SimpleNamespace(id="enh-1", project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED)
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(session_mod, "load_last_active_project_id", lambda: "enh-1")
    monkeypatch.setattr(session_mod, "get_project_by_id", lambda pid: fake if pid == "enh-1" else None)
    restored = session_mod.restore_active_project_into_session()
    assert restored == "enh-1"
    assert st.session_state[ACTIVE_PROJECT_KEY] == "enh-1"


def test_restore_ignores_unknown_project(monkeypatch) -> None:
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(session_mod, "load_last_active_project_id", lambda: "gone")
    monkeypatch.setattr(session_mod, "get_project_by_id", lambda _pid: None)
    assert session_mod.restore_active_project_into_session() is None
    assert ACTIVE_PROJECT_KEY not in st.session_state
