"""Tests für Analyse-Repository-Updates."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import ProjectCreate, ProjectStatus
from otio_app.project_repository import (
    create_project,
    get_project_by_id,
    update_project_selection,
    update_project_status,
)


def test_update_selection_and_status(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    data = ProjectCreate(
        name="Update-Test",
        project_root=str(temp_project_layout["project_root"]),
    )
    saved = create_project(
        data,
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    updated = update_project_selection(
        saved.id,
        ["Yellowstone"],
        db_path=temp_db_path,
    )
    assert updated.selected_asset_subdirs == ["Yellowstone"]

    status_updated = update_project_status(
        saved.id,
        ProjectStatus.READY,
        db_path=temp_db_path,
    )
    assert status_updated.status == ProjectStatus.READY
    reloaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert reloaded is not None
    assert reloaded.status == ProjectStatus.READY
