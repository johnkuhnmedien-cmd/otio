"""Tests: Inventory-Sync löscht keine bestehenden JSONs mehr."""

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


def test_inventory_json_created_when_folder_green(
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
    # Partial: Sync baut trotzdem Inventory aus vorhandenem Cache (kein Delete).
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    assert project.folder_inventory_path("Grand Canyon").is_file()

    save_cached_media(
        media_cache_path(project, "Grand Canyon", folder / "clip2.mp4"),
        AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description="OK 2"),
    )
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    assert project.folder_inventory_path("Grand Canyon").is_file()


def test_inventory_json_preserved_when_folder_becomes_partial(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Partial darf shared Inventory nicht mehr löschen."""
    project = Project(
        id="green-inv-preserve",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clip1 = folder / "clip.mp4"
    clip2 = folder / "clip2.mp4"
    clip2.write_bytes(b"video2")
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip1),
        AssetMediaAnalysis(path=str(clip1), description="OK"),
    )
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip2),
        AssetMediaAnalysis(path=str(clip2), description="OK 2"),
    )
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    inv_path = project.folder_inventory_path("Grand Canyon")
    assert inv_path.is_file()
    before = inv_path.read_text(encoding="utf-8")

    # Ein Asset „bricht“ → Ordner PARTIAL, Inventory muss bleiben.
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip2),
        AssetMediaAnalysis(path=str(clip2), description=""),
    )
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
    assert sync_folder_inventory_with_status(project, "Grand Canyon") is True
    assert inv_path.is_file()
    after = inv_path.read_text(encoding="utf-8")
    # Partial-Sync darf die Datei nicht entfernen.
    assert after
    assert "OK" in after
    del before
