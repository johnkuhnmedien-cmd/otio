"""Phase 4: Folder-Voice-over-Settings — Vorbefüllung aus Dramaturgie."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path, get_folder_voiceover_settings_path
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    enabled_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
    update_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan


def _make_project_with_confirmed_dramaturgy(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in ("Grand Canyon", "Yellowstone"):
        (project_root / folder).mkdir()
    project = Project(
        id="settings-project",
        name="Settings Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    for folder in ("Grand Canyon", "Yellowstone"):
        path = get_folder_inventory_path(project.work_dir_path, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"{folder} view")],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name="Grand Canyon",
                order_index=1,
                enabled=True,
                dramaturgy_role="opener",
                recommended_word_count=140,
                recommended_min_words=126,
                recommended_max_words=154,
            ),
            DramaturgyFolderEntry(
                folder_name="Yellowstone",
                order_index=2,
                enabled=False,
                dramaturgy_role="setup",
                recommended_word_count=0,
                recommended_min_words=0,
                recommended_max_words=0,
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    return project


def test_settings_prefilled_from_confirmed_dramaturgy(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)

    by_folder = {setting.folder_name: setting for setting in document.settings}
    assert by_folder["Grand Canyon"].target_words == 140
    assert by_folder["Grand Canyon"].min_words == 126
    assert by_folder["Grand Canyon"].max_words == 154
    assert by_folder["Grand Canyon"].dramaturgy_role == "opener"
    assert by_folder["Grand Canyon"].enabled is True
    assert by_folder["Yellowstone"].enabled is False


def test_settings_fall_back_to_heuristic_when_dramaturgy_words_missing(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    yellowstone = next(s for s in document.settings if s.folder_name == "Yellowstone")
    # 0-Werte in der Dramaturgie -> Phase-3-Heuristik greift, Ergebnis > 0.
    assert yellowstone.target_words > 0
    assert yellowstone.min_words > 0
    assert yellowstone.max_words > 0


def test_only_enabled_folders_are_returned_by_enabled_settings(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    active = enabled_settings(document)
    assert [setting.folder_name for setting in active] == ["Grand Canyon"]


def test_enabled_settings_handles_none_document() -> None:
    assert enabled_settings(None) == []


def test_save_and_load_settings_roundtrip(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    loaded = load_folder_voiceover_settings(project)
    assert loaded is not None
    assert {s.folder_name for s in loaded.settings} == {"Grand Canyon", "Yellowstone"}

    path = get_folder_voiceover_settings_path(project.work_dir_path)
    assert path.is_file()


def test_load_settings_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    assert load_folder_voiceover_settings(project) is None


def test_update_folder_voiceover_settings_applies_edits(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    edited_rows = [
        {
            "folder_name": "Grand Canyon",
            "target_words": 200,
            "energy": "high",
            "must_include": "sunset, silence",
            "enabled": True,
        },
        {"folder_name": "Yellowstone", "enabled": True},
    ]
    updated = update_folder_voiceover_settings(project, edited_rows)
    by_folder = {setting.folder_name: setting for setting in updated.settings}
    assert by_folder["Grand Canyon"].target_words == 200
    assert by_folder["Grand Canyon"].energy == "high"
    assert by_folder["Grand Canyon"].must_include == ["sunset", "silence"]
    assert by_folder["Yellowstone"].enabled is True

    reloaded = load_folder_voiceover_settings(project)
    assert reloaded.settings == updated.settings
