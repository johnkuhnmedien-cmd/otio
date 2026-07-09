"""Generisches AppTest-Skript: rendert eine beliebige Seite der Pipeline
"Projekt ohne Voice-Over" mit einem einzigen, gepatchten Projekt — isoliert
von der echten Projekt-Datenbank. Welche Seite gerendert wird, steuert die
Umgebungsvariable REPRO_RENDER_FUNCTION ("module.path:function_name").

Wird für die vereinfachte Modell-Auswahl (EIN Dropdown statt Provider-
Selectbox + Modell-Freitext) in mehreren Tabs wiederverwendet, um Duplikation
zu vermeiden."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import streamlit as st

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    VoiceoverStyleProfile,
)
from otio_app.services.voiceover_generation.style_profile_service import save_style_profile
from otio_app.services.voiceover_generation.voiceover_author_service import generate_folder_voiceover
from otio_app.services.voiceover_generation.voiceover_review_service import confirm_folder_voiceover
from otio_app.ui import project_context

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
(root / "USA" / "Grand Canyon").mkdir(parents=True, exist_ok=True)

if os.environ.get("REPRO_SETUP") == "dramaturgy_and_voiceovers_confirmed":
    inv_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder="Grand Canyon",
        assets=[AssetMediaAnalysis(path="Grand Canyon/clip1.mp4", description="Weite Aufnahme.")],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    fake_response = PlanLlmResponse(
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text=json.dumps({"voiceover_text_full": "Text.", "sentence_items": []}),
    )
    with patch(
        "otio_app.services.voiceover_generation.voiceover_author_service.generate_plan_text_with_metadata",
        return_value=fake_response,
    ):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, "Grand Canyon")

style_profile_library_name = os.environ.get("REPRO_STYLE_PROFILE_LIBRARY_NAME")
if style_profile_library_name:
    save_style_profile(
        project,
        VoiceoverStyleProfile(project_id=project.id, library_name=style_profile_library_name),
    )

project_context.list_projects = lambda: [project]
project_context.get_project_by_id = lambda project_id: project if project_id == project.id else None
st.session_state["active_project_id"] = project.id

module_path, function_name = os.environ["REPRO_RENDER_FUNCTION"].split(":")
render_function = getattr(importlib.import_module(module_path), function_name)
render_function()
