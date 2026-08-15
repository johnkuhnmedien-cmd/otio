"""AppTest-Skript: rendert die reale Project-Brief-Seite mit einem einzigen,
gepatchten Projekt — isoliert von der echten Projekt-Datenbank."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation import project_brief_defaults_service
from otio_app.ui import project_context
from otio_app.ui.voiceover_generation.project_brief_tab import render_project_brief_page

root = Path(os.environ["REPRO_ROOT"])
_global_data = root / "global_data"
_global_data.mkdir(parents=True, exist_ok=True)
project_brief_defaults_service.ensure_data_dir = lambda: _global_data

project = Project(
    id=os.environ.get("REPRO_PROJECT_ID", "repro-project"),
    name="Repro",
    project_root=str(root / "USA"),
    work_dir=str(root / "USA" / "_otio"),
    project_mode=ProjectMode.WITHOUT_VOICEOVER,
    video_place="USA",
    asset_subdir_names=["Grand Canyon"],
    selected_asset_subdirs=["Grand Canyon"],
)

project_context.list_projects = lambda: [project]
project_context.get_project_by_id = lambda project_id: project if project_id == project.id else None
st.session_state["active_project_id"] = project.id

render_project_brief_page()
