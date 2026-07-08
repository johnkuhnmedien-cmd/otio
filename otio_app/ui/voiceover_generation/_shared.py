"""Gemeinsame Platzhalter-Darstellung für die Phase-1-Seiten dieser Pipeline."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.models import Project, ProjectMode
from otio_app.ui.project_context import render_project_selector


def render_placeholder_page(
    *,
    title: str,
    phase_hint: str,
    target_path_label: str,
    target_path_fn,
) -> None:
    """Zeigt eine Platzhalterseite mit Projektauswahl und dem künftigen Zielpfad.

    target_path_fn erhält den Arbeitsordner (work_dir_path) und liefert den
    Pfad, unter dem diese Seite ihr Artefakt in einer späteren Phase speichern
    wird — so ist die Pfadstruktur schon jetzt sichtbar und testbar.
    """
    st.header(title)

    project = render_project_selector("Projekt")
    if project is None:
        return

    if project.project_mode != ProjectMode.WITHOUT_VOICEOVER:
        st.warning(
            "Dieses Projekt ist auf „Projekt mit Voice-Over“ eingestellt. "
            "Diese Seite gehört zur Pipeline „Projekt ohne Voice-Over“ und "
            "sollte für dieses Projekt nicht verwendet werden."
        )
        return

    st.info(
        f"🚧 Noch nicht implementiert — {phase_hint}. "
        "Diese Seite ist Teil des neuen Diagnose-/Generierungsworkflows "
        "„Projekt ohne Voice-Over“ und wird in einer späteren Phase befüllt."
    )
    target_path: Path = target_path_fn(project.work_dir_path)
    st.caption(f"Künftiger Zielpfad: `{target_path}`")


def get_active_voiceover_gen_project() -> Project | None:
    """Hilfsfunktion für spätere Phasen — identisch zur bestehenden Projekt-Auswahl."""
    return render_project_selector("Projekt")
