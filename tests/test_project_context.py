"""Tests für Workflow-Status."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.ui.project_context import get_workflow_status


def test_workflow_status_detects_completed_steps(temp_project_layout: dict[str, Path]) -> None:
    root = temp_project_layout["project_root"]
    project = Project(
        id="wf-test",
        name="Test",
        project_root=str(root),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    project.voice_analysis_path.write_text("{}", encoding="utf-8")

    from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
    from otio_app.services.inventory_loader import save_folder_inventory

    media_path = str(root / "Grand Canyon" / "clip.mp4")
    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            media_files=[media_path],
            assets=[AssetMediaAnalysis(path=media_path, description="Fertig")],
        ),
    )

    status = get_workflow_status(project)
    assert status.voice_analysis_done is True
    assert status.inventory_done is True
    assert status.analysis_done is True
    assert status.mapping_confirmed is False
    assert status.edit_plan_done is False
