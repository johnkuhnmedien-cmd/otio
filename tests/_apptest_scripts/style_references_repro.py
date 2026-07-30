"""AppTest-Skript: rendert die reale Style-References-Seite mit einem einzigen,
gepatchten Projekt — isoliert von der echten Projekt-Datenbank UND von der
echten (projektübergreifenden) Style-Profile-Bibliothek unter data/."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

import otio_app.services.voiceover_generation.raw_style_library_service as raw_style_library_service
import otio_app.services.voiceover_generation.style_profile_library_service as style_profile_library_service
from otio_app.models import Project, ProjectMode
from otio_app.ui import project_context
from otio_app.ui.voiceover_generation.style_references_tab import render_style_references_page

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

# Isoliert die projektübergreifenden Bibliotheken unter root/global_data statt im
# echten data/-Verzeichnis der Anwendung.
_global_data_dir = root / "global_data"
style_profile_library_service.ensure_data_dir = lambda: _global_data_dir
raw_style_library_service.ensure_data_dir = lambda: _global_data_dir

render_style_references_page()
