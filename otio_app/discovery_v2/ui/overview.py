"""Streamlit-Platzhalterseiten für Discovery V2 (Übersicht / Einstellungen)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.ui.route_context import render_discovery_project_selector
from otio_app.models import Project, ProjectMode
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY


def active_discovery_project() -> Project | None:
    """Bindet das aktive Discovery-V2-Projekt (Route + Application Service)."""
    project = render_discovery_project_selector("Projekt")
    if project is None:
        return None
    if project.project_mode != ProjectMode.DISCOVERY_V2:
        st.warning(
            "project_mode_mismatch: Diese Seite gehört zu **Discovery V2**. "
            "Bitte ein Discovery-V2-Projekt wählen."
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

    root = project.resolved_work_root
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
    st.code(str(project.resolved_work_root), language=None)
    st.caption(f"Session-Projekt-ID: `{st.session_state.get(ACTIVE_PROJECT_KEY, '—')}`")
