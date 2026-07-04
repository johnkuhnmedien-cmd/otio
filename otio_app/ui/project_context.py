"""Gemeinsame Projekt-Auswahl und Workflow-Fortschritt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id, list_projects
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY


@dataclass(frozen=True)
class WorkflowStatus:
    voice_analysis_done: bool
    inventory_done: bool
    mapping_confirmed: bool
    edit_plan_done: bool

    @property
    def analysis_done(self) -> bool:
        return self.voice_analysis_done and self.inventory_done


def get_workflow_status(project: Project) -> WorkflowStatus:
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    edit_plan_path = project.project_root_path / "edit_plan.json"
    return WorkflowStatus(
        voice_analysis_done=project.voice_analysis_path.is_file(),
        inventory_done=project.inventory_path.is_file(),
        mapping_confirmed=bool(mapping and mapping.confirmed),
        edit_plan_done=edit_plan_path.is_file(),
    )


def render_project_selector(label: str = "Projekt") -> Project | None:
    """Zeigt die Projekt-Auswahl und hält die ID sitzungsübergreifend."""
    projects = list_projects()
    if not projects:
        st.info("Noch kein Projekt vorhanden. Lege zuerst unter „Neues Projekt“ eines an.")
        return None

    labels = {project.id: project.name for project in projects}
    default_id = st.session_state.get(ACTIVE_PROJECT_KEY, projects[0].id)
    if default_id not in labels:
        default_id = projects[0].id

    selected_id = st.selectbox(
        label,
        options=list(labels.keys()),
        format_func=lambda pid: labels[pid],
        index=list(labels.keys()).index(default_id),
        key="global_project_selector",
    )
    st.session_state[ACTIVE_PROJECT_KEY] = selected_id
    return get_project_by_id(selected_id)


def _step_label(title: str, done: bool, active: bool) -> str:
    icon = "✅" if done else ("▶️" if active else "⬜")
    return f"{icon} {title}"


def render_workflow_progress(project: Project, current_step: str) -> None:
    """Kompakte Workflow-Leiste über den Workflow-Seiten."""
    status = get_workflow_status(project)
    steps = [
        ("① Analysen", status.analysis_done, current_step == "analysis"),
        ("② Zuordnung", status.mapping_confirmed, current_step == "mapping"),
        ("③ Schnittplan", status.edit_plan_done, current_step == "edit_plan"),
    ]
    columns = st.columns(len(steps))
    for column, (title, done, active) in zip(columns, steps):
        with column:
            st.caption(_step_label(title, done, active))


def render_output_status(project: Project) -> None:
    """Kurzüberblick über erzeugte Dateien."""
    status = get_workflow_status(project)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Voice-over", "✓" if status.voice_analysis_done else "—")
    with col2:
        st.metric("Inventar", "✓" if status.inventory_done else "—")
    with col3:
        st.metric("Zuordnung", "✓" if status.mapping_confirmed else "—")
    with col4:
        st.metric("Schnittplan", "✓" if status.edit_plan_done else "—")


def render_file_paths(project: Project) -> None:
    with st.expander("Dateipfade & Details", expanded=False):
        st.write(f"**Projektordner:** `{project.project_root}`")
        st.write(f"**Voice-over:** `{project.voice_over_dir}`")
        st.write(f"**Voice-Analyse:** `{project.voice_analysis_path}`")
        st.write(f"**Inventar:** `{project.inventory_path}`")
        st.write(f"**Zuordnung:** `{project.voice_folder_mapping_path}`")
