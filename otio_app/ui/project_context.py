"""Gemeinsame Projekt-Auswahl und Workflow-Fortschritt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id, list_projects
from otio_app.services.edit_plan_builder import (
    EditPlanLocationState,
    get_mapped_folders,
    list_saved_edit_plan_folders,
    mapped_folders_have_confirmed_plans,
)
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.clean_media import (
    folder_manifest_path,
    load_clean_media_manifest,
    manifest_needs_processing,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY, PAGE_ANALYSIS, PAGE_CLEAN_MEDIA, PAGE_EDIT_PLAN, PAGE_MAPPING, PAGE_SUPPLEMENT


@dataclass(frozen=True)
class WorkflowStatus:
    clean_media_done: bool
    voice_analysis_done: bool
    inventory_done: bool
    mapping_confirmed: bool
    edit_plan_done: bool

    @property
    def analysis_done(self) -> bool:
        return self.voice_analysis_done and self.inventory_done


def _fast_clean_media_ready(project: Project) -> bool:
    """Manifest-Check ohne Datei-Stat pro Medium."""
    folders = project.selected_asset_subdirs
    if not folders:
        return False
    for folder_name in folders:
        manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
        if manifest is None or not manifest.entries or manifest_needs_processing(manifest):
            return False
    return True


def _fast_inventory_ready(project: Project) -> bool:
    """Prüft nur, ob je Ordner eine Inventory-JSON existiert — ohne Media-Scan."""
    folders = project.selected_asset_subdirs
    if not folders:
        return False
    for folder_name in folders:
        if not get_folder_inventory_path(project.work_dir_path, folder_name).is_file():
            return False
    return True


def get_workflow_status(project: Project, *, lightweight: bool = False) -> WorkflowStatus:
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    mapped_folders = get_mapped_folders(project)
    edit_plan_done = mapped_folders_have_confirmed_plans(project, mapped_folders)
    if lightweight:
        return WorkflowStatus(
            clean_media_done=_fast_clean_media_ready(project),
            voice_analysis_done=project.voice_analysis_path.is_file(),
            inventory_done=_fast_inventory_ready(project),
            mapping_confirmed=bool(mapping and mapping.confirmed),
            edit_plan_done=edit_plan_done,
        )
    from otio_app.services.clean_media import selected_folders_have_clean_media
    from otio_app.services.inventory_loader import selected_folders_have_inventory

    return WorkflowStatus(
        clean_media_done=selected_folders_have_clean_media(project),
        voice_analysis_done=project.voice_analysis_path.is_file(),
        inventory_done=selected_folders_have_inventory(project),
        mapping_confirmed=bool(mapping and mapping.confirmed),
        edit_plan_done=edit_plan_done,
    )


def get_edit_plan_location_progress(project: Project) -> tuple[int, int]:
    """Anzahl bestätigter Orte und Gesamtzahl zugeordneter Orte."""
    from otio_app.services.edit_plan_cache import count_confirmed_folders

    mapped_folders = get_mapped_folders(project)
    if not mapped_folders:
        return 0, 0
    return count_confirmed_folders(project, mapped_folders), len(mapped_folders)


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


def render_workflow_progress(
    project: Project,
    current_step: str,
    *,
    lightweight: bool = False,
    location_statuses: list | None = None,
) -> None:
    """Kompakte Workflow-Leiste über den Workflow-Seiten."""
    status = get_workflow_status(project, lightweight=lightweight)
    steps = [
        (PAGE_CLEAN_MEDIA, status.clean_media_done, current_step in {"clean_media", PAGE_CLEAN_MEDIA}),
        (PAGE_ANALYSIS, status.analysis_done, current_step in {"analysis", PAGE_ANALYSIS}),
        (PAGE_MAPPING, status.mapping_confirmed, current_step in {"mapping", PAGE_MAPPING}),
        (PAGE_SUPPLEMENT, status.mapping_confirmed, current_step == PAGE_SUPPLEMENT),
        (PAGE_EDIT_PLAN, status.edit_plan_done, current_step in {"edit_plan", PAGE_EDIT_PLAN}),
    ]
    columns = st.columns(len(steps))
    for column, (title, done, active) in zip(columns, steps):
        with column:
            st.caption(_step_label(title, done, active))

    if location_statuses is not None:
        confirmed_count = sum(
            1 for item in location_statuses if item.state == EditPlanLocationState.CONFIRMED
        )
        total_count = len(location_statuses)
    else:
        confirmed_count, total_count = get_edit_plan_location_progress(project)
    if total_count > 0 and not status.edit_plan_done:
        st.caption(f"Schnittplan-Fortschritt: **{confirmed_count}/{total_count}** Orte abgeschlossen")


def render_output_status(project: Project) -> None:
    """Kurzüberblick über erzeugte Dateien."""
    status = get_workflow_status(project)
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Clean Media", "✓" if status.clean_media_done else "—")
    with col2:
        st.metric("Voice-over", "✓" if status.voice_analysis_done else "—")
    with col3:
        st.metric("Inventar", "✓" if status.inventory_done else "—")
    with col4:
        st.metric("Zuordnung", "✓" if status.mapping_confirmed else "—")
    with col5:
        st.metric("Schnittplan", "✓" if status.edit_plan_done else "—")


def render_file_paths(project: Project) -> None:
    with st.expander("Dateipfade & Details", expanded=False):
        st.write(f"**Projektordner:** `{project.project_root}`")
        st.write(f"**Voice-over:** `{project.voice_over_dir}`")
        st.write(f"**Voice-Analyse:** `{project.voice_analysis_path}`")
        st.write(f"**Inventar:** `{project.inventory_dir}` (pro Ordner eine JSON)")
        st.write(f"**Clean Media:** `{project.work_dir_path / 'clean'}` (Transcodes)")
        st.write(f"**Clean-Manifeste:** `{project.work_dir_path / 'clean_media'}`")
        st.write(f"**Zuordnung:** `{project.voice_folder_mapping_path}`")
        st.write(f"**Schnittpläne:** `{project.edit_plan_dir}` (pro Ort eine JSON)")
        st.write(f"**OTIO-Export:** `{project.work_dir_path / 'exports'}`")
        saved_folders = list_saved_edit_plan_folders(project)
        if saved_folders:
            st.caption("Gespeichert: " + ", ".join(f"`{name}`" for name in saved_folders))
        legacy = project.edit_plan_path
        if legacy.is_file():
            st.caption(f"Legacy-Datei (wird beim Laden migriert): `{legacy}`")
