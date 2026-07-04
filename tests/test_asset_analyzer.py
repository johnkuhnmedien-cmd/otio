"""Tests für Asset-Ordner-Analyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project
from otio_app.services.asset_analyzer import analyze_asset_folders


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="test-project",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_analyze_asset_folders_processes_every_media_file(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")

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

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.extract_frames",
        fake_extract,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.describe_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    document = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["clip.mp4", "clip2.mp4"]
    item = document.items[0]
    assert len(item.assets) == 2
    assert all(asset.description.startswith("Beschreibung für") for asset in item.assets)
    assert "clip.mp4:" in item.description
    assert "clip2.mp4:" in item.description


def test_analyze_asset_folders_uses_per_media_cache(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.extract_frames",
        fake_extract,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.describe_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["clip.mp4"]
