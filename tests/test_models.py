"""Tests für Pydantic-Projektmodelle."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from otio_app.defaults import DEFAULT_FRAMES_PER_SHOT
from otio_app.models import (
    Project,
    ProjectCreate,
    ProjectMode,
    ProjectStatus,
    validate_asset_selection,
)


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
    work_dir = temp_project_layout["work_dir"]
    assert data.inventory_path == project_root / "inventory.json"
    assert data.inventory_dir == work_dir / "inventory"
    assert data.folder_inventory_path("Grand Canyon") == work_dir / "inventory" / "Grand_Canyon.json"
    assert data.voice_analysis_path == work_dir / "DE" / "voice_over_analysis.json"


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
    project = Project.from_create(
        data,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert project.status == ProjectStatus.DRAFT
    assert project.name == "USA Reise"
    assert project.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert project.selected_asset_subdirs == ["Grand Canyon"]


def test_validate_asset_selection_rejects_empty() -> None:
    with pytest.raises(ValueError, match="Mindestens ein"):
        validate_asset_selection(["Grand Canyon"], [])


def test_validate_asset_selection_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Ungültige"):
        validate_asset_selection(["Grand Canyon"], ["Unknown"])


def test_project_create_defaults_to_with_voiceover(
    temp_project_layout: dict[str, Path],
) -> None:
    data = _make_create(temp_project_layout)
    assert data.project_mode == ProjectMode.WITH_VOICEOVER


def test_project_create_accepts_without_voiceover_mode(
    temp_project_layout: dict[str, Path],
) -> None:
    data = ProjectCreate(
        name="Ohne VO",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
    )
    assert data.project_mode == ProjectMode.WITHOUT_VOICEOVER


def test_project_from_create_preserves_project_mode(
    temp_project_layout: dict[str, Path],
) -> None:
    data = ProjectCreate(
        name="Ohne VO",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
    )
    project = Project.from_create(
        data,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    assert project.project_mode == ProjectMode.WITHOUT_VOICEOVER
    assert project.is_without_voiceover is True


def test_project_default_mode_is_with_voiceover_when_omitted(
    temp_project_layout: dict[str, Path],
) -> None:
    data = _make_create(temp_project_layout)
    project = Project.from_create(
        data,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert project.project_mode == ProjectMode.WITH_VOICEOVER
    assert project.is_without_voiceover is False


def test_voiceover_generation_dir_property(
    temp_project_layout: dict[str, Path],
) -> None:
    data = ProjectCreate(
        name="Ohne VO",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
    )
    project = Project.from_create(
        data,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    assert project.voiceover_generation_dir == (
        temp_project_layout["work_dir"] / "DE" / "voiceover_generation"
    )
