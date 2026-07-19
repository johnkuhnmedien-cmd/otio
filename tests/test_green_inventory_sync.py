"""Tests: Inventory-JSON nur bei grünem Ordner-Status."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    get_folder_analysis_state,
)
from otio_app.services.inventory_loader import sync_folder_inventory_with_status
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def test_inventory_json_only_when_folder_green(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="green-inv-test",
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
    assert not project.folder_inventory_path("Grand Canyon").is_file()

    save_cached_media(
        media_cache_path(project, "Grand Canyon", folder / "clip2.mp4"),
        AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description="OK 2"),
    )
    # Status-Anzeige ist read-only — Inventory erst per Sync.
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE
    assert not project.folder_inventory_path("Grand Canyon").is_file()
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    assert project.folder_inventory_path("Grand Canyon").is_file()

    save_cached_media(
        media_cache_path(project, "Grand Canyon", folder / "clip2.mp4"),
        AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description=""),
    )
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
    assert project.folder_inventory_path("Grand Canyon").is_file()

    sync_folder_inventory_with_status(project, "Grand Canyon")
    assert not project.folder_inventory_path("Grand Canyon").is_file()
