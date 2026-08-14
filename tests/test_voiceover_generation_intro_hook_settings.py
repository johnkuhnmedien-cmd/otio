"""Phase 5: Intro-Hook-Settings-Service."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_intro_hook_settings_path, get_voiceover_generation_dir
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    default_intro_hook_settings,
    load_intro_hook_settings,
    save_intro_hook_settings,
)
from otio_app.services.voiceover_generation.models import IntroHookSettings, ProjectBrief
from otio_app.services.voiceover_generation.project_brief_service import save_project_brief


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="intro-settings-project",
        name="Intro Settings Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_default_settings_use_language_from_project_brief(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_project_brief(
        project,
        ProjectBrief(project_id=project.id, language="EN", tone_tags=["mysterious"]),
    )
    settings = default_intro_hook_settings(project)
    assert settings.language == "EN"
    assert settings.tone == "mysterious"


def test_default_settings_fall_back_to_cinematic_tone(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = default_intro_hook_settings(project)
    assert settings.language == "DE"
    assert settings.tone == "cinematic"
    assert settings.target_words == 70
    assert settings.min_words == 56
    assert settings.max_words == 84
    assert settings.word_tolerance_percent == 20


def test_save_and_load_intro_hook_settings_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = IntroHookSettings(
        project_id=project.id,
        language="FR",
        target_words=100,
        word_tolerance_percent=20,
        allow_questions=False,
        must_include=["mystery"],
    )
    save_intro_hook_settings(project, settings)

    loaded = load_intro_hook_settings(project)
    assert loaded.language == "FR"
    assert loaded.target_words == 100
    assert loaded.min_words == 80
    assert loaded.max_words == 120
    assert loaded.allow_questions is False
    assert loaded.must_include == ["mystery"]

    path = get_intro_hook_settings_path(project.language_work_dir_path)
    assert path.is_file()
    assert path.is_relative_to(get_voiceover_generation_dir(project.language_work_dir_path))


def test_intro_word_window_follows_target_and_tolerance() -> None:
    from otio_app.defaults import intro_word_window
    from otio_app.services.voiceover_generation.models import IntroHookSettings

    assert intro_word_window(90, 20) == (72, 108)
    settings = IntroHookSettings(
        project_id="p",
        target_words=90,
        min_words=1,
        max_words=999,
        word_tolerance_percent=20,
    )
    assert settings.min_words == 72
    assert settings.max_words == 108


def test_load_intro_hook_settings_returns_default_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = load_intro_hook_settings(project)
    assert settings.language == "DE"
