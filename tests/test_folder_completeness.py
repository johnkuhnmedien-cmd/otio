"""Tests für strikte Ordner-Vollständigkeit."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    get_folder_analysis_state,
)
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def _project(layout: dict[str, Path]) -> Project:
    return Project(
        id="strict-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_partial_folder_is_not_complete_with_stale_inventory(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")
    media_path = folder / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="OK"),
    )
    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            media_files=[str(media_path), str(folder / "clip2.mp4")],
            assets=[
                AssetMediaAnalysis(path=str(media_path), description="OK"),
                AssetMediaAnalysis(path=str(folder / "clip2.mp4"), description=""),
            ],
        ),
    )

    state = get_folder_analysis_state(project, "Grand Canyon")
    assert state == FolderAnalysisState.PARTIAL
    # Status-Anzeige löscht stale Inventory nicht mehr; Sync räumt auf.
    from otio_app.services.inventory_loader import sync_folder_inventory_with_status

    assert project.folder_inventory_path("Grand Canyon").is_file()
    sync_folder_inventory_with_status(project, "Grand Canyon")
    assert not project.folder_inventory_path("Grand Canyon").is_file()


def test_inventory_written_only_when_all_assets_done(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> str:
        return f"Beschreibung für {media_name}"

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.describe_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    assert project.folder_inventory_path("Grand Canyon").is_file()
    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.COMPLETE
