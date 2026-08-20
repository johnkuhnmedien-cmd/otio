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

    from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, CleanMediaEntry, CleanMediaManifest
    from otio_app.services.clean_media import CLEAN_STATUS_OK, save_clean_media_manifest
    from otio_app.services.inventory_loader import save_folder_inventory
    from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media

    media_path = str(root / "Grand Canyon" / "clip.mp4")
    save_clean_media_manifest(
        temp_project_layout["work_dir"] / "clean_media" / "Grand_Canyon.json",
        CleanMediaManifest(
            project_id="wf-test",
            folder="Grand Canyon",
            entries=[
                CleanMediaEntry(
                    original_path=media_path,
                    status=CLEAN_STATUS_OK,
                )
            ],
        ),
    )
    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            media_files=[media_path],
            assets=[AssetMediaAnalysis(path=media_path, description="Fertig")],
        ),
    )
    save_cached_media(
        media_cache_path(project, "Grand Canyon", root / "Grand Canyon" / "clip.mp4"),
        AssetMediaAnalysis(path=media_path, description="Fertig"),
    )

    status = get_workflow_status(project)
    assert status.clean_media_done is True
    assert status.voice_analysis_done is True
    assert status.inventory_done is True
    assert status.analysis_done is True
    assert status.mapping_confirmed is False
    assert status.edit_plan_done is False


def test_enhanced_output_status_includes_auto_run_overview() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "project_context.py"
    ).read_text(encoding="utf-8")
    assert "_render_enhanced_output_status" in source
    assert "list_auto_run_step_statuses" in source
    assert "Statusübersicht" in source
    assert "format_auto_run_status_caption" in source
    assert "st.metric(item.short_label" not in source
