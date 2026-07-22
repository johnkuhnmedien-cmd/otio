"""Tests für robuste Medien-Erkennung und Cache-Vereinigung."""

from __future__ import annotations

from otio_app.services.gemini_client import MediaFrameAnalysis

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.folder_asset_status import (
    AssetAnalysisState,
    folder_is_fully_analyzed,
    list_missing_or_failed_assets,
)
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    is_successfully_analyzed,
    media_cache_path,
    save_cached_media,
)
from otio_app.services.media_utils import (
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    list_media_files,
)


def _project(layout: dict[str, Path]) -> Project:
    return Project(
        id="discovery-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_placeholder_description_is_not_successful() -> None:
    entry = AssetMediaAnalysis(
        path="/tmp/clip.mp4",
        description=NO_ANALYZABLE_MEDIA_DESCRIPTION,
    )
    assert not is_successfully_analyzed(entry)


def test_discover_includes_cache_only_asset(temp_project_layout: dict[str, Path]) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    asset15 = folder / "Florida_Keys_Asset15.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", asset15),
        AssetMediaAnalysis(path=str(asset15), description="Aus Cache"),
    )

    discovered = discover_folder_media_paths(project, "Grand Canyon")
    names = {path.name for path in discovered}
    assert "clip.mp4" in names
    assert "Florida_Keys_Asset15.mp4" in names


def test_placeholder_cache_is_retried_on_reanalysis(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    media_path = folder / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(
            path=str(media_path),
            description=NO_ANALYZABLE_MEDIA_DESCRIPTION,
        ),
    )

    assert not folder_is_fully_analyzed(project, "Grand Canyon")
    gaps = list_missing_or_failed_assets(project, "Grand Canyon")
    assert len(gaps) == 1
    assert gaps[0].state == AssetAnalysisState.FAILED

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
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis(description=f"Neu analysiert: {media_name}")
    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    assert folder_is_fully_analyzed(project, "Grand Canyon")


def test_list_media_files_uses_glob_fallback(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "extra_clip.mp4").write_bytes(b"video-extra")

    def fail_iterdir(_directory: Path) -> tuple[list[str], str | None]:
        return [], "iterdir failed"

    monkeypatch.setattr(
        "otio_app.services.media_utils._list_media_names_iterdir",
        fail_iterdir,
    )
    monkeypatch.setattr(
        "otio_app.services.media_utils._list_media_names_os_listdir",
        fail_iterdir,
    )

    files = list_media_files(folder)
    names = {path.name for path in files}
    assert "clip.mp4" in names
    assert "extra_clip.mp4" in names
