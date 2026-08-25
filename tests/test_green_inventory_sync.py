"""Tests: Inventory-JSON bei grünen und teilweisen Ordnern."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    get_folder_analysis_state,
)
from otio_app.services.inventory_loader import (
    load_folder_inventory_file,
    sync_folder_inventory_with_status,
)
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def test_inventory_json_written_for_partial_and_complete_folders(
    temp_project_layout: dict[str, Path],
) -> None:
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
    inventory_path = project.folder_inventory_path("Grand Canyon")
    assert not inventory_path.is_file()
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    assert inventory_path.is_file()
    item = load_folder_inventory_file(inventory_path)
    assert item is not None
    assert [Path(asset.path).name for asset in item.assets] == ["clip.mp4"]
    slim_path = slim_inventory_path_for(inventory_path)
    assert slim_path.is_file()
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL

    save_cached_media(
        media_cache_path(project, "Grand Canyon", folder / "clip2.mp4"),
        AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description="OK 2"),
    )
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    complete = load_folder_inventory_file(inventory_path)
    assert complete is not None
    assert {Path(asset.path).name for asset in complete.assets} == {"clip.mp4", "clip2.mp4"}

    save_cached_media(
        media_cache_path(project, "Grand Canyon", folder / "clip2.mp4"),
        AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description=""),
    )
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    reduced = load_folder_inventory_file(inventory_path)
    assert reduced is not None
    assert [Path(asset.path).name for asset in reduced.assets] == ["clip.mp4"]
    assert slim_inventory_path_for(inventory_path).is_file()
