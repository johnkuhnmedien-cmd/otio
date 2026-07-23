"""Tests für Legacy-Cache-Pfade unter _otio/inventory/<Ordner>/."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.inventory_loader import materialize_folder_inventory_from_cache
from otio_app.services.media_inventory_cache import (
    legacy_per_asset_cache_path,
    save_cached_media,
)


def test_materialize_reads_legacy_inventory_subfolder_cache(
    temp_project_layout: dict[str, Path],
) -> None:
    project = Project(
        id="legacy-cache-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    save_cached_media(
        legacy_per_asset_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="Legacy-Cache"),
    )

    item, error = materialize_folder_inventory_from_cache(project, "Grand Canyon")
    assert error is None
    assert item is not None
    assert project.folder_inventory_path("Grand Canyon").is_file()
    assert item.assets[0].description == "Legacy-Cache"
    assert project.folder_inventory_path("Grand Canyon").read_text(encoding="utf-8")
    assert (
        project.work_dir_path
        / "cache"
        / "inventory"
        / "Grand_Canyon"
        / "clip.mp4.json"
    ).is_file()
