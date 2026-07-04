"""Tests für Pydantic-Projektmodelle."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from otio_app.defaults import DEFAULT_FRAMES_PER_SHOT
from otio_app.models import Project, ProjectCreate, ProjectStatus


def _make_create(layout: dict[str, Path]) -> ProjectCreate:
    return ProjectCreate(
        name="USA Reise",
        project_root=str(layout["project_root"]),
    )


def test_project_create_defaults(temp_project_layout: dict[str, Path]) -> None:
    data = _make_create(temp_project_layout)
    assert data.language == "de"
    assert data.frames_per_shot == DEFAULT_FRAMES_PER_SHOT
    assert data.fps == 25.0
    assert data.voice_over_subdir == "Voice over"
    assert data.work_dir.endswith("_otio")


def test_project_create_discovers_asset_subdirs(
    temp_project_layout: dict[str, Path],
) -> None:
    data = _make_create(temp_project_layout)
    names = {path.name for path in data.asset_subdirs}
    assert names == {"Grand Canyon", "Yellowstone"}


def test_project_create_voice_over_dir(temp_project_layout: dict[str, Path]) -> None:
    data = _make_create(temp_project_layout)
    assert data.voice_over_dir == temp_project_layout["voice_over_dir"]


def test_project_create_output_paths(temp_project_layout: dict[str, Path]) -> None:
    data = _make_create(temp_project_layout)
    project_root = temp_project_layout["project_root"]
    assert data.inventory_path == project_root / "inventory.json"
    assert data.voice_analysis_path == project_root / "voice_over_analysis.json"


def test_project_create_rejects_empty_name(
    temp_project_layout: dict[str, Path],
) -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(
            name="   ",
            project_root=str(temp_project_layout["project_root"]),
        )


def test_project_create_rejects_invalid_frames(
    temp_project_layout: dict[str, Path],
) -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(
            name="Test",
            project_root=str(temp_project_layout["project_root"]),
            frames_per_shot=0,
        )


def test_project_create_rejects_work_dir_inside_assets(
    temp_project_layout: dict[str, Path],
) -> None:
    asset_dir = temp_project_layout["asset_dirs"][0]
    with pytest.raises(ValidationError, match="Asset-Unterordner"):
        ProjectCreate(
            name="Test",
            project_root=str(temp_project_layout["project_root"]),
            work_dir=str(asset_dir),
        )


def test_project_from_create_sets_draft_status(
    temp_project_layout: dict[str, Path],
) -> None:
    data = _make_create(temp_project_layout)
    project = Project.from_create(data, asset_subdir_names=["Grand Canyon", "Yellowstone"])
    assert project.status == ProjectStatus.DRAFT
    assert project.name == "USA Reise"
    assert project.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
