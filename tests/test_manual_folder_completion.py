"""Tests für manuelle Ordner-Freigabe."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    format_folder_with_status,
    get_folder_analysis_state,
    list_open_folder_names,
)
from otio_app.services.inventory_loader import selected_folders_have_inventory
from otio_app.services.manual_folder_completion import set_manually_complete
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def test_manual_complete_marks_folder_green(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="manual-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")
    media_path = folder / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="OK"),
    )

    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
    set_manually_complete(project, "Grand Canyon", complete=True)
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE
    assert "manuell" in format_folder_with_status(project, "Grand Canyon")
    assert project.folder_inventory_path("Grand Canyon").is_file()
    assert "Grand Canyon" not in list_open_folder_names(project, project.asset_subdir_names)
    assert selected_folders_have_inventory(project) is True
