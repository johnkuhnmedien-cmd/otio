"""Streamlit-Platzhalterseiten für Discovery V2 (Übersicht / Einstellungen)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.models import Project, ProjectMode
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY
from otio_app.ui.project_context import render_project_selector


def active_discovery_project() -> Project | None:
    project = render_project_selector("Projekt")
    if project is None:
        return None
    if project.project_mode != ProjectMode.DISCOVERY_V2:
        st.warning(
            "Diese Seite gehört zu **Discovery V2**. "
            "Bitte ein Discovery-V2-Projekt in der Sidebar aktivieren "
            "oder unter „Neues Projekt“ eines anlegen."
        )
        return None
    return project


def render_discovery_overview_page() -> None:
    """Erste Discovery-V2-Seite — nur Status, keine Pipeline-Aktionen."""
    st.title("Discovery V2")
    project = active_discovery_project()
    if project is None:
        return

    st.markdown("**Status:** Grundgerüst aktiv")
    st.info("Die Discovery-Pipeline wird schrittweise aufgebaut.")

    root = get_discovery_v2_root(project.project_root_path)
    st.subheader("Artefaktwurzel")
    st.code(str(root), language=None)
    st.caption(
        "Discovery V2 schreibt ausschließlich unter `_otio_v2/`. "
        "Bestehende Artefakte unter `_otio/` werden nicht verändert."
    )
    st.caption(f"Aktives Projekt: **{project.name}** · Sprache: `{project.language}`")


def render_discovery_settings_page() -> None:
    """Platzhalter für spätere Discovery-Projekteinstellungen."""
    st.title("Projekteinstellungen")
    project = active_discovery_project()
    if project is None:
        return

    st.markdown("**Status:** Grundgerüst aktiv")
    st.info("Die Discovery-Pipeline wird schrittweise aufgebaut.")
    st.write(f"**Projekt:** {project.name}")
    st.write(f"**Modus:** Discovery V2 (`{project.project_mode.value}`)")
    st.write(f"**Sprache:** {project.language}")
    st.write(f"**FPS:** {project.fps}")
    st.write(f"**Auflösung:** {project.width} × {project.height}")
    st.code(str(get_discovery_v2_root(project.project_root_path)), language=None)
    st.caption(f"Session-Projekt-ID: `{st.session_state.get(ACTIVE_PROJECT_KEY, '—')}`")
