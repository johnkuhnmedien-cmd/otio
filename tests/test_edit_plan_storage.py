"""Tests für pro-Ort-Schnittplan-Speicherung."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_builder import (
    list_saved_edit_plan_folders,
    load_edit_plan,
    mapped_folders_have_confirmed_plans,
    migrate_legacy_edit_plan,
    save_edit_plan,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="folder-plan-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys", "Grand Canyon"],
        selected_asset_subdirs=["Florida Keys", "Grand Canyon"],
    )


def _sample_shot(folder: str, index: int) -> EditPlanShot:
    return EditPlanShot(
        voice_file="voice.wav",
        folder=folder,
        voice_start_sec=float(index),
        voice_end_sec=float(index + 3),
        duration_sec=3.0,
        asset_path=f"/media/{folder.replace(' ', '_')}_{index}.mp4",
        motif=f"motif {index}",
        passage_text=f"text {index}",
    )


def test_save_and_load_per_folder(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanDocument(
        project_id=project.id,
        folder_name="Florida Keys",
        confirmed=True,
        settings=EditPlanSettings(),
        shots=[_sample_shot("Florida Keys", 1)],
    )
    path = save_edit_plan(project, document, "Florida Keys")
    assert path == project.folder_edit_plan_path("Florida Keys")
    assert path.is_file()

    loaded = load_edit_plan(project, "Florida Keys")
    assert loaded is not None
    assert loaded.confirmed is True
    assert loaded.folder_name == "Florida Keys"
    assert len(loaded.shots) == 1


def test_migrate_legacy_edit_plan_splits_by_folder(tmp_path: Path) -> None:
    project = _project(tmp_path)
    legacy = project.edit_plan_path
    legacy.write_text(
        EditPlanDocument(
            project_id=project.id,
            confirmed=True,
            settings=EditPlanSettings(),
            shots=[
                _sample_shot("Florida Keys", 1),
                _sample_shot("Grand Canyon", 2),
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    saved = migrate_legacy_edit_plan(project)
    assert len(saved) == 2
    assert not legacy.is_file()
    assert legacy.with_suffix(".json.migrated").is_file()

    florida = load_edit_plan(project, "Florida Keys")
    canyon = load_edit_plan(project, "Grand Canyon")
    assert florida is not None and len(florida.shots) == 1
    assert canyon is not None and len(canyon.shots) == 1


def test_list_saved_edit_plan_folders(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=False,
            shots=[_sample_shot("Florida Keys", 1)],
        ),
        "Florida Keys",
    )
    folders = list_saved_edit_plan_folders(project)
    assert folders == ["Florida Keys"]


def test_mapped_folders_have_confirmed_plans(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert mapped_folders_have_confirmed_plans(project, ["Florida Keys"]) is False

    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            shots=[_sample_shot("Florida Keys", 1)],
        ),
        "Florida Keys",
    )
    assert mapped_folders_have_confirmed_plans(project, ["Florida Keys"]) is True
    assert mapped_folders_have_confirmed_plans(
        project,
        ["Florida Keys", "Grand Canyon"],
    ) is False
