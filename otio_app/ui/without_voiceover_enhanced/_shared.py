"""Gemeinsame UI-Helfer für without_voiceover_enhanced."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.ui.project_context import render_project_selector


def require_enhanced_mode(project: Project) -> bool:
    if project.project_mode != ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
        st.warning(
            "Diese Seite gehört ausschließlich zum Modus "
            "„Projekt ohne Voice-Over (Enhanced MVP)“."
        )
        return False
    if project.work_dir_path.name != DEFAULT_ENHANCED_WORK_SUBDIR:
        st.error(
            f"Enhanced-Modus erwartet Arbeitsordner `{DEFAULT_ENHANCED_WORK_SUBDIR}`, "
            f"gefunden: `{project.work_dir_path.name}`."
        )
        return False
    if "_otio_v2" in project.work_dir_path.parts:
        st.error("Enhanced-Modus darf nicht unter `_otio_v2` arbeiten.")
        return False
    return True


def get_enhanced_project() -> Project | None:
    project = render_project_selector("Projekt")
    if project is None:
        return None
    if not require_enhanced_mode(project):
        return None
    return project
