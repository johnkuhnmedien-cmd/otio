"""Tests für Einzel-Asset-Status."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.folder_asset_status import (
    AssetAnalysisState,
    list_missing_or_failed_assets,
)
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def test_list_missing_or_failed_assets(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="asset-gap-test",
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

    gaps = list_missing_or_failed_assets(project, "Grand Canyon")
    assert len(gaps) == 1
    assert gaps[0].path.name == "clip2.mp4"
    assert gaps[0].state == AssetAnalysisState.MISSING
