"""Tests für Projekt-Repository."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import ProjectCreate, ProjectMode, ProjectStatus
from otio_app.project_repository import create_project, get_project_by_id, list_projects


def _sample_create(layout: dict[str, Path]) -> ProjectCreate:
    return ProjectCreate(
        name="Repo-Test",
        project_root=str(layout["project_root"]),
        notes="Testnotiz",
        frames_per_shot=3,
    )


def test_create_and_list(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    data = _sample_create(temp_project_layout)
    saved = create_project(
        data,
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Yellowstone"],
    )
    assert saved.status == ProjectStatus.DRAFT
    assert saved.notes == "Testnotiz"
    assert saved.video_place == ""
    assert saved.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert saved.selected_asset_subdirs == ["Yellowstone"]

    projects = list_projects(db_path=temp_db_path)
    assert len(projects) == 1
    assert projects[0].id == saved.id
    assert projects[0].name == "Repo-Test"


def test_get_by_id(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    saved = create_project(
        _sample_create(temp_project_layout),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.name == saved.name
    assert loaded.frames_per_shot == 3


def test_get_by_id_missing(temp_db_path: Path) -> None:
    assert get_project_by_id("missing-id", db_path=temp_db_path) is None


def test_create_project_defaults_to_with_voiceover(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    saved = create_project(
        _sample_create(temp_project_layout),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert saved.project_mode == ProjectMode.WITH_VOICEOVER

    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.project_mode == ProjectMode.WITH_VOICEOVER


def test_create_project_persists_without_voiceover_mode(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    data = ProjectCreate(
        name="Ohne VO Projekt",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
    )
    saved = create_project(
        data,
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    assert saved.project_mode == ProjectMode.WITHOUT_VOICEOVER
    assert saved.is_without_voiceover is True

    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.project_mode == ProjectMode.WITHOUT_VOICEOVER

    projects = list_projects(db_path=temp_db_path)
    assert len(projects) == 1
    assert projects[0].project_mode == ProjectMode.WITHOUT_VOICEOVER


def test_create_project_persists_video_place(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    data = ProjectCreate(
        name="IT Greece",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="IT",
        video_place="Griechenland",
    )
    saved = create_project(
        data,
        db_path=temp_db_path,
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )
    assert saved.video_place == "Griechenland"
    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.video_place == "Griechenland"
