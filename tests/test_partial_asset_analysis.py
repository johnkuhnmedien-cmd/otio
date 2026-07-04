"""Tests für pro-Asset JSON-Prüfung bei Teilanalyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    has_successful_asset_cache,
    list_assets_missing_successful_cache,
    media_cache_path,
    save_cached_media,
)


def _project(layout: dict[str, Path]) -> Project:
    return Project(
        id="partial-json-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_list_assets_missing_successful_cache(temp_project_layout: dict[str, Path]) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clip1 = folder / "clip.mp4"
    clip2 = folder / "Florida_Keys_Asset15.mp4"
    clip2.write_bytes(b"video15")

    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip1),
        AssetMediaAnalysis(path=str(clip1), description="OK"),
    )

    missing = list_assets_missing_successful_cache(project, "Grand Canyon")
    assert len(missing) == 1
    assert missing[0].name == "Florida_Keys_Asset15.mp4"
    assert has_successful_asset_cache(project, "Grand Canyon", clip1)
    assert not has_successful_asset_cache(project, "Grand Canyon", clip2)


def test_partial_folder_analyzes_only_assets_without_json(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clip1 = folder / "clip.mp4"
    clip2 = folder / "Florida_Keys_Asset15.mp4"
    clip2.write_bytes(b"video15")
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip1),
        AssetMediaAnalysis(path=str(clip1), description="Bereits fertig"),
    )

    calls: list[str] = []

    def fake_extract(media_path: Path, output_dir: Path, count: int) -> list[Path]:
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
        calls.append(media_name)
        return f"Beschreibung für {media_name}"

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.describe_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["Florida_Keys_Asset15.mp4"]
    cache_file = media_cache_path(project, "Grand Canyon", clip2)
    assert cache_file.is_file()
    assert has_successful_asset_cache(project, "Grand Canyon", clip2)


def test_discover_missing_asset_from_cache_number_gap(
    temp_project_layout: dict[str, Path],
) -> None:
    project = Project(
        id="cache-gap-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    folder_name = "Grand Canyon"
    folder = temp_project_layout["project_root"] / folder_name
    asset14 = folder / "Florida_Keys_Asset14.mp4"
    asset16 = folder / "Florida_Keys_Asset16.mp4"
    asset14.write_bytes(b"v14")
    asset16.write_bytes(b"v16")

    save_cached_media(
        media_cache_path(project, folder_name, asset14),
        AssetMediaAnalysis(path=str(asset14), description="OK 14"),
    )
    save_cached_media(
        media_cache_path(project, folder_name, asset16),
        AssetMediaAnalysis(path=str(asset16), description="OK 16"),
    )

    missing = list_assets_missing_successful_cache(project, folder_name)
    names = {path.name for path in missing}
    assert "Florida_Keys_Asset15.mp4" in names

    discovered = discover_folder_media_paths(project, folder_name)
    discovered_names = {path.name for path in discovered}
    assert "Florida_Keys_Asset15.mp4" in discovered_names


def test_partial_folder_not_skipped_when_frame_dir_exists_without_json(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clip1 = folder / "clip.mp4"
    asset15 = folder / "Florida Keys Asset15.mp4"
    asset15.write_bytes(b"video15")
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip1),
        AssetMediaAnalysis(path=str(clip1), description="OK"),
    )
    frames_dir = (
        project.work_dir_path
        / "frames"
        / safe_folder_slug("Grand Canyon")
        / safe_folder_slug(asset15.stem)
    )
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_001.jpg").write_bytes(b"jpeg")

    calls: list[str] = []

    def fake_extract(media_path: Path, output_dir: Path, count: int) -> list[Path]:
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
        calls.append(media_name)
        return f"Beschreibung für {media_name}"

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.describe_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["Florida Keys Asset15.mp4"]
    assert media_cache_path(project, "Grand Canyon", asset15).is_file()
