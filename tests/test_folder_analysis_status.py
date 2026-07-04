"""Tests für Ordner-Analyse-Status."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.inventory_loader import materialize_folder_inventory_from_cache
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    get_folder_analysis_state,
    list_open_folder_names,
)


def test_folder_complete_when_all_media_cached(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="status-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    entry = AssetMediaAnalysis(path=str(media_path), description="Test")
    save_cached_media(media_cache_path(project, "Grand Canyon", media_path), entry)
    materialize_folder_inventory_from_cache(project, "Grand Canyon")

    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE


def test_list_open_folder_names(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="status-test-2",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    entry = AssetMediaAnalysis(path=str(media_path), description="Test")
    save_cached_media(media_cache_path(project, "Grand Canyon", media_path), entry)
    materialize_folder_inventory_from_cache(project, "Grand Canyon")

    open_names = list_open_folder_names(project, project.asset_subdir_names)
    assert open_names == ["Yellowstone"]
