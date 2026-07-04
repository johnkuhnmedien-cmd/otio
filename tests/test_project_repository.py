"""Tests für Projekt-Repository."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import ProjectCreate, ProjectStatus
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
    saved = create_project(data, db_path=temp_db_path)
    assert saved.status == ProjectStatus.DRAFT
    assert saved.notes == "Testnotiz"
    assert len(saved.asset_subdir_names) == 2

    projects = list_projects(db_path=temp_db_path)
    assert len(projects) == 1
    assert projects[0].id == saved.id
    assert projects[0].name == "Repo-Test"


def test_get_by_id(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    saved = create_project(_sample_create(temp_project_layout), db_path=temp_db_path)
    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.name == saved.name
    assert loaded.frames_per_shot == 3


def test_get_by_id_missing(temp_db_path: Path) -> None:
    assert get_project_by_id("missing-id", db_path=temp_db_path) is None
