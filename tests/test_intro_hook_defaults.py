"""Globale Intro-Settings-Defaults pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    apply_language_defaults_to_settings,
    get_intro_hook_defaults_path,
    load_language_intro_defaults,
    save_language_intro_defaults,
)
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    default_intro_hook_settings,
    load_intro_hook_settings,
)
from otio_app.services.voiceover_generation.models import IntroHookSettings
from otio_app.services.voiceover_generation.project_brief_service import save_project_brief
from otio_app.services.voiceover_generation.models import ProjectBrief


def _make_project(tmp_path: Path, *, language: str = "pt") -> Project:
    root = tmp_path / "Greece"
    root.mkdir(parents=True, exist_ok=True)
    return Project(
        id="pt-greece",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Griechenland",
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


@pytest.fixture()
def intro_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.intro_hook_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.project_brief_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_save_and_load_language_intro_defaults(
    tmp_path: Path, intro_defaults_dir: Path
) -> None:
    saved = save_language_intro_defaults(
        "pt",
        IntroHookSettings(
            project_id="x",
            language="PT",
            target_words=90,
            word_tolerance_percent=15,
            tone="documentary",
            freeform_rule_for_llm="Números por extenso.",
            forbidden_phrases=["incrível", ""],
            allow_questions=False,
            must_include=["história"],
            must_avoid=["clichê"],
        ),
    )
    assert saved.target_words == 90
    assert saved.tone == "documentary"
    assert saved.forbidden_phrases == ["incrível"]
    loaded = load_language_intro_defaults("PT")
    assert loaded is not None
    assert loaded.freeform_rule_for_llm == "Números por extenso."
    assert loaded.allow_questions is False
    assert loaded.must_include == ["história"]
    assert get_intro_hook_defaults_path().is_relative_to(intro_defaults_dir)


def test_default_settings_pick_up_language_standard(
    tmp_path: Path, intro_defaults_dir: Path
) -> None:
    save_language_intro_defaults(
        "pt",
        IntroHookSettings(
            project_id="x",
            target_words=80,
            word_tolerance_percent=10,
            tone="intimate",
            freeform_rule_for_llm="Fala como um guia local.",
            allow_direct_place_name=False,
        ),
    )
    project = _make_project(tmp_path, language="pt")
    save_project_brief(
        project,
        ProjectBrief(project_id=project.id, language="DE", tone_tags=["mysterious"]),
    )
    settings = default_intro_hook_settings(project)
    assert settings.language == "PT"
    assert settings.target_words == 80
    assert settings.word_tolerance_percent == 10
    assert settings.min_words == 72
    assert settings.max_words == 88
    assert settings.tone == "intimate"
    assert settings.freeform_rule_for_llm == "Fala como um guia local."
    assert settings.allow_direct_place_name is False
    # Brief-Ton darf den Sprachstandard nicht überschreiben.
    assert settings.tone != "mysterious"


def test_apply_language_defaults_keeps_project_id(
    tmp_path: Path, intro_defaults_dir: Path
) -> None:
    defaults = save_language_intro_defaults(
        "de",
        IntroHookSettings(
            project_id="source",
            target_words=60,
            tone="cinematic",
            allow_tease_multiple_places=False,
        ),
    )
    project = _make_project(tmp_path, language="de")
    applied = apply_language_defaults_to_settings(
        IntroHookSettings(project_id=project.id, language="DE", tone="old"),
        defaults,
    )
    assert applied.project_id == project.id
    assert applied.target_words == 60
    assert applied.allow_tease_multiple_places is False


def test_load_project_settings_uses_language_standard_when_file_missing(
    tmp_path: Path, intro_defaults_dir: Path
) -> None:
    save_language_intro_defaults(
        "en",
        IntroHookSettings(project_id="x", target_words=100, tone="epic"),
    )
    project = _make_project(tmp_path, language="en")
    loaded = load_intro_hook_settings(project)
    assert loaded.target_words == 100
    assert loaded.tone == "epic"
    assert loaded.language == "EN"
