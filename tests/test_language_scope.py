"""Tests für Language-Scope `_otio/{LANG}/` und einmalige Migration."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import get_language_work_dir, language_folder_name
from otio_app.services.language_scope import (
    LANGUAGE_SCOPE_MARKER_NAME,
    ensure_language_scope,
    migrate_language_scope,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    load_cut_plan_settings,
    save_cut_plan_settings,
)


def _project(tmp_path: Path, *, language: str = "de") -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    work = root / "_otio"
    work.mkdir()
    return Project(
        id="lang-scope",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        language=language,
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def test_language_folder_name_and_work_dir(tmp_path: Path) -> None:
    assert language_folder_name("de") == "DE"
    assert language_folder_name("EN") == "EN"
    work = tmp_path / "_otio"
    assert get_language_work_dir(work, "de") == work / "DE"


def test_fresh_project_creates_language_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    lang_dir = ensure_language_scope(project)
    assert lang_dir == project.work_dir_path / "DE"
    assert lang_dir.is_dir()
    marker = project.work_dir_path / LANGUAGE_SCOPE_MARKER_NAME
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert "DE" in payload["languages"]
    assert payload["source_layout"] == "fresh"


def test_migrate_moves_flat_editorial_artifacts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = project.work_dir_path
    flat_vo = work / "voiceover_generation" / "cut_plan"
    flat_vo.mkdir(parents=True)
    settings_file = flat_vo / "cut_plan_settings.json"
    settings_file.write_text('{"project_id":"lang-scope"}', encoding="utf-8")
    flat_edit = work / "edit_plan"
    flat_edit.mkdir()
    (flat_edit / "Florida_Keys.json").write_text("{}", encoding="utf-8")
    (work / "edit_plan_rules.json").write_text("{}", encoding="utf-8")
    flat_clean = work / "clean" / "Florida_Keys"
    flat_clean.mkdir(parents=True)
    (flat_clean / "clip.mp4").write_bytes(b"x")

    lang_dir = migrate_language_scope(project)
    assert lang_dir == work / "DE"
    assert (lang_dir / "voiceover_generation" / "cut_plan" / "cut_plan_settings.json").is_file()
    assert (lang_dir / "edit_plan" / "Florida_Keys.json").is_file()
    assert (lang_dir / "edit_plan_rules.json").is_file()
    # SHARED bleibt
    assert (work / "clean" / "Florida_Keys" / "clip.mp4").is_file()
    assert not (work / "voiceover_generation").exists()
    assert not (work / "edit_plan").exists()


def test_migrate_rewrites_absolute_json_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = project.work_dir_path
    audio_dir = work / "voiceover_generation" / "audio" / "001_Florida_Keys"
    audio_dir.mkdir(parents=True)
    audio = audio_dir / "voiceover_v001.mp3"
    audio.write_bytes(b"x")
    manifest = work / "voiceover_generation" / "voiceover_audio_manifest.json"
    old_path = str(audio.resolve())
    manifest.write_text(
        json.dumps({"items": [{"audio_path": old_path}]}),
        encoding="utf-8",
    )

    lang_dir = migrate_language_scope(project)
    new_manifest = lang_dir / "voiceover_generation" / "voiceover_audio_manifest.json"
    payload = json.loads(new_manifest.read_text(encoding="utf-8"))
    new_path = payload["items"][0]["audio_path"]
    assert "/DE/voiceover_generation/" in new_path.replace("\\", "/")
    assert Path(new_path).is_file()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = project.work_dir_path
    (work / "exports").mkdir()
    (work / "exports" / "a.otio").write_text("x", encoding="utf-8")
    first = migrate_language_scope(project)
    second = migrate_language_scope(project)
    assert first == second
    assert (first / "exports" / "a.otio").is_file()


def test_cut_plan_settings_land_in_language_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, folder_title_enabled=True),
    )
    path = project.language_work_dir_path / "voiceover_generation" / "cut_plan" / "cut_plan_settings.json"
    assert path.is_file()
    loaded = load_cut_plan_settings(project)
    assert loaded.folder_title_enabled is True
    # nicht mehr flat unter _otio/
    assert not (
        project.work_dir_path / "voiceover_generation" / "cut_plan" / "cut_plan_settings.json"
    ).exists()
