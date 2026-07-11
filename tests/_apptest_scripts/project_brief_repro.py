"""AppTest-Skript: rendert die reale Project-Brief-Seite mit einem einzigen,
gepatchten Projekt — isoliert von der echten Projekt-Datenbank."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from otio_app.models import Project, ProjectMode
from otio_app.ui import project_context
from otio_app.ui.voiceover_generation.project_brief_tab import render_project_brief_page

root = Path(os.environ["REPRO_ROOT"])
project = Project(
    id=os.environ.get("REPRO_PROJECT_ID", "repro-project"),
    name="Repro",
    project_root=str(root / "USA"),
    work_dir=str(root / "USA" / "_otio"),
    project_mode=ProjectMode.WITHOUT_VOICEOVER,
    asset_subdir_names=["Grand Canyon"],
    selected_asset_subdirs=["Grand Canyon"],
)

project_context.list_projects = lambda: [project]
project_context.get_project_by_id = lambda project_id: project if project_id == project.id else None
st.session_state["active_project_id"] = project.id

render_project_brief_page()
