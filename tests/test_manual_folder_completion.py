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
from otio_app.services.manual_folder_completion import (
    is_manually_complete,
    list_manually_complete_folders,
    set_manually_complete,
    set_manually_complete_many,
)
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


def test_clear_all_manual_marks(temp_project_layout: dict[str, Path]) -> None:
    root = temp_project_layout["project_root"]
    for name in ("Grand Canyon", "Antelope Canyon"):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clip.mp4").write_bytes(b"video")
        (folder / "clip2.mp4").write_bytes(b"video2")

    project = Project(
        id="manual-clear-all",
        name="Test",
        project_root=str(root),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon", "Antelope Canyon"],
        selected_asset_subdirs=["Grand Canyon", "Antelope Canyon"],
    )
    for name in project.asset_subdir_names:
        media_path = root / name / "clip.mp4"
        save_cached_media(
            media_cache_path(project, name, media_path),
            AssetMediaAnalysis(path=str(media_path), description="OK"),
        )

    changed = set_manually_complete_many(
        project, project.asset_subdir_names, complete=True
    )
    assert set(changed) == set(project.asset_subdir_names)
    assert list_manually_complete_folders(project) == sorted(
        project.asset_subdir_names, key=str.casefold
    )

    cleared = set_manually_complete_many(
        project, list_manually_complete_folders(project), complete=False
    )
    assert set(cleared) == set(project.asset_subdir_names)
    assert list_manually_complete_folders(project) == []
    assert is_manually_complete(project, "Grand Canyon") is False
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
