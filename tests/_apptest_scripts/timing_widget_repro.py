"""Minimales Repro-Skript für AppTest: Timing-Widgets über ALLE Tab-Wechsel
(Regeln -> Vorschlag -> Prüfen & Speichern -> Regeln) hinweg — nutzt die
ECHTEN _render_tab_*-Funktionen wie render_edit_plan_page()."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from otio_app.models import Project
from otio_app.ui.edit_plan import (
    TAB_EXPORT,
    TAB_GENERATE,
    TAB_REVIEW,
    TAB_RULES,
    _edit_plan_tab_key,
    _render_tab_generate,
    _render_tab_review,
    _render_tab_settings,
    _seed_timing_widgets,
    _set_draft,
)

root = Path(os.environ["REPRO_ROOT"])
project = Project(
    id="repro-project",
    name="Repro",
    project_root=str(root / "USA"),
    work_dir=str(root / "USA" / "_otio"),
    asset_subdir_names=["Folder"],
    selected_asset_subdirs=["Folder"],
)

_seed_timing_widgets(project)

active_tab = st.radio(
    "Schnittplan-Schritt",
    options=(TAB_RULES, TAB_GENERATE, TAB_REVIEW, TAB_EXPORT),
    horizontal=True,
    key=_edit_plan_tab_key(project.id),
)

plan_path = project.folder_edit_plan_path("Folder")

with st.container(key=f"edit-plan-panel-{project.id}"):
    if active_tab == TAB_RULES:
        _render_tab_settings(project)
    elif active_tab == TAB_GENERATE:
        _render_tab_generate(project, "Folder", None)
    elif active_tab == TAB_REVIEW:
        _render_tab_review(project, "Folder", None, plan_path)

st.write("MIN=" + str(st.session_state.get(f"plan_min_{project.id}")))
st.write("MAX=" + str(st.session_state.get(f"plan_max_{project.id}")))
st.write("GEMINI=" + str(st.session_state.get(f"plan_gemini_{project.id}")))
