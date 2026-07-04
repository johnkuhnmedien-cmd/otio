"""Platzhalter für den Schnittplan-Workflow (Meilenstein 4)."""

from __future__ import annotations

import streamlit as st

from otio_app.ui.project_context import (
    render_file_paths,
    render_project_selector,
    render_workflow_progress,
)


def render_edit_plan_placeholder() -> None:
    st.header("③ Schnittplan")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="edit_plan")

    st.info(
        "Der Schnittplan kommt im nächsten Meilenstein: Passagen aus Whisper mit "
        "Assets verknüpfen, Shots (3–8 s) vorschlagen und nach deiner Freigabe "
        "als `edit_plan.json` speichern."
    )

    st.markdown(
        """
        **Geplante Einstellungen (übersichtlich in Tabs):**
        - Whisper & Schnittregeln (3–8 s, Audio +1 s)
        - Fallback-Reihenfolge (lokal → Adobe Stock → Pexels → KI-Bild)
        - Vorschau & manuelle Korrektur vor dem Speichern
        """
    )

    render_file_paths(project)
