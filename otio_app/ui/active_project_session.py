"""Aktives Projekt über Streamlit-Neustarts hinweg merken.

Nach einem Kill/Restart ist ``session_state`` leer. Der Browser bleibt oft auf
``/auto-lauf``. Ohne aktives Enhanced-Projekt baut die Navigation die klassische
Seitenliste — ``auto-lauf`` fehlt, Streamlit wartet auf eine nicht existierende
Seite, die Fläche bleibt schwarz.
"""

from __future__ import annotations

from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.project_repository import get_project_by_id
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY

LAST_ACTIVE_PROJECT_FILENAME = "last_active_project_id.txt"


def last_active_project_path() -> Path:
    return ensure_data_dir() / LAST_ACTIVE_PROJECT_FILENAME


def load_last_active_project_id() -> str | None:
    try:
        raw = last_active_project_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def save_last_active_project_id(project_id: str) -> None:
    value = str(project_id or "").strip()
    if not value:
        return
    try:
        last_active_project_path().write_text(value + "\n", encoding="utf-8")
    except OSError:
        pass


def set_active_project_id(project_id: str, *, persist: bool = True) -> None:
    """Setzt das aktive Projekt in der Session und merkt es für den nächsten Start."""
    import streamlit as st

    st.session_state[ACTIVE_PROJECT_KEY] = project_id
    if persist:
        save_last_active_project_id(project_id)


def restore_active_project_into_session() -> str | None:
    """Stellt das letzte Projekt wieder her, bevor ``st.navigation`` die Seiten baut."""
    import streamlit as st

    current = str(st.session_state.get(ACTIVE_PROJECT_KEY) or "").strip()
    if current:
        return current

    last = load_last_active_project_id()
    if not last:
        return None
    project = get_project_by_id(last)
    if project is None:
        return None
    st.session_state[ACTIVE_PROJECT_KEY] = last
    return last
