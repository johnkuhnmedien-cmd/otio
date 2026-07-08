"""AppTest-Skript für Phase 10.4: rendert AUSSCHLIESSLICH den Production-
EditPlan-Staging-Bereich — isoliert von Projekt-Selector/Navigation, damit die
Widget-/Element-Introspektion von AppTest direkt auf diesen Bereich zielt."""

from __future__ import annotations

import os
from pathlib import Path

from otio_app.models import Project, ProjectMode
from otio_app.ui.voiceover_generation.cut_plan_tab import _render_production_edit_plan_staging

root = Path(os.environ["REPRO_ROOT"])
folder_name = os.environ.get("REPRO_FOLDER", "Grand Canyon")
project = Project(
    id=os.environ.get("REPRO_PROJECT_ID", "repro-project"),
    name="Repro",
    project_root=str(root / "USA"),
    work_dir=str(root / "USA" / "_otio"),
    project_mode=ProjectMode.WITHOUT_VOICEOVER,
    asset_subdir_names=[folder_name],
    selected_asset_subdirs=[folder_name],
)

_render_production_edit_plan_staging(project)
