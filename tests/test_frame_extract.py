"""Tests für Frame-Extraktion und Erkennung über Frame-Ordner."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.frame_extract import compute_frame_timestamps
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    media_cache_path,
    save_cached_media,
)


def test_compute_frame_timestamps_without_duration_uses_multiple_offsets() -> None:
    assert compute_frame_timestamps(None, 3) == [0.0, 2.0, 5.0]
    assert compute_frame_timestamps(0.0, 2) == [0.0, 2.0]


def test_compute_frame_timestamps_with_duration_spreads_evenly() -> None:
    assert compute_frame_timestamps(12.0, 3) == [3.0, 6.0, 9.0]


def test_discover_includes_media_from_existing_frame_dir(
    temp_project_layout: dict[str, Path],
) -> None:
    project = Project(
        id="frame-dir-discovery",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    asset15 = folder / "Florida_Keys_Asset15.mp4"
    frames_dir = (
        project.work_dir_path
        / "frames"
        / safe_folder_slug("Grand Canyon")
        / safe_folder_slug(asset15.stem)
    )
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_001.jpg").write_bytes(b"jpeg")

    discovered_ghost = discover_folder_media_paths(project, "Grand Canyon")
    ghost_names = {path.name for path in discovered_ghost}
    assert "clip.mp4" in ghost_names
    assert "Florida_Keys_Asset15.mp4" not in ghost_names

    (folder / f".{asset15.name}.icloud").write_text("placeholder", encoding="utf-8")
    discovered = discover_folder_media_paths(project, "Grand Canyon")
    names = {path.name for path in discovered}
    assert "clip.mp4" in names
    assert "Florida_Keys_Asset15.mp4" in names
