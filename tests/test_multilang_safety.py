"""Multi-language safety: same project_root, different languages."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.database import get_connection
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_layout import get_voice_analysis_path, language_folder_name
from otio_app.project_repository import (
    create_project,
    find_project_by_root_and_language,
    find_projects_by_root,
)
from otio_app.services.language_scope import ensure_language_scope


def _create(
    layout: dict[str, Path],
    *,
    name: str,
    language: str,
    db_path: Path,
) -> ProjectCreate:
    return ProjectCreate(
        name=name,
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        language=language,
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
    )


def test_unique_index_root_language(temp_db_path: Path) -> None:
    conn = get_connection(temp_db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_projects_root_language_mode'"
        ).fetchall()
        assert rows
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_projects_root_language'"
        ).fetchall()
        assert not legacy
    finally:
        conn.close()


def test_second_language_same_root_allowed(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    de = create_project(
        _create(temp_project_layout, name="USA DE", language="de", db_path=temp_db_path),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    en = create_project(
        _create(temp_project_layout, name="USA EN", language="en", db_path=temp_db_path),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert de.language == "de"
    assert en.language == "en"
    assert de.project_root == en.project_root
    assert de.language_work_dir_path != en.language_work_dir_path
    assert de.language_work_dir_path.name == "DE"
    assert en.language_work_dir_path.name == "EN"
    siblings = find_projects_by_root(de.project_root, db_path=temp_db_path)
    assert {p.language for p in siblings} == {"de", "en"}


def test_duplicate_same_language_rejected(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    create_project(
        _create(temp_project_layout, name="USA DE", language="de", db_path=temp_db_path),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    with pytest.raises(ValueError, match="bereits ein Projekt"):
        create_project(
            _create(temp_project_layout, name="USA DE 2", language="DE", db_path=temp_db_path),
            db_path=temp_db_path,
            asset_subdir_names=["Grand Canyon"],
            selected_asset_subdirs=["Grand Canyon"],
        )


def test_find_by_root_and_language(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    create_project(
        _create(temp_project_layout, name="USA DE", language="de", db_path=temp_db_path),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    found = find_project_by_root_and_language(
        temp_project_layout["project_root"],
        "DE",
        db_path=temp_db_path,
    )
    assert found is not None
    assert found.name == "USA DE"
    assert find_project_by_root_and_language(
        temp_project_layout["project_root"],
        "en",
        db_path=temp_db_path,
    ) is None


def test_voice_analysis_lives_in_language_scope(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    project = create_project(
        _create(temp_project_layout, name="USA DE", language="de", db_path=temp_db_path),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    path = project.voice_analysis_path
    assert path.parent.name == language_folder_name(project.language)
    assert path == get_voice_analysis_path(project.language_work_dir_path)

    # Legacy root file wird nach Language-Scope migriert.
    legacy = project.project_root_path / "voice_over_analysis.json"
    legacy.write_text('{"project_id":"x"}', encoding="utf-8")
    ensure_language_scope(project)
    assert project.voice_analysis_path.is_file()
    assert not legacy.exists()
