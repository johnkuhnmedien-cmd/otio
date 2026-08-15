"""Projektspezifische Dramaturgie-Wortziele und Sprachstandard."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_dramaturgy_settings_path
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    save_language_dramaturgy_word_defaults,
)
from otio_app.services.voiceover_generation.dramaturgy_settings_service import (
    default_dramaturgy_settings,
    load_dramaturgy_settings,
    save_dramaturgy_settings,
    word_band_from_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgySettings,
    DramaturgyWordDefaults,
)


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
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.dramaturgy_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_word_band_matches_intro_window() -> None:
    band = word_band_from_settings(
        DramaturgySettings(project_id="x", target_words=150, word_tolerance_percent=20)
    )
    assert band.min_words == 120
    assert band.max_words == 180
    assert band.tolerance_words == 30


def test_default_settings_use_language_standard(
    tmp_path: Path, isolated_data: Path
) -> None:
    save_language_dramaturgy_word_defaults(
        "pt",
        DramaturgyWordDefaults(target_words=130, word_tolerance_percent=10),
    )
    settings = default_dramaturgy_settings(_make_project(tmp_path, language="pt"))
    assert settings.target_words == 130
    assert settings.word_tolerance_percent == 10


def test_save_and_load_project_settings(tmp_path: Path, isolated_data: Path) -> None:
    project = _make_project(tmp_path)
    saved = save_dramaturgy_settings(
        project,
        DramaturgySettings(project_id=project.id, target_words=170, word_tolerance_percent=25),
    )
    assert saved.target_words == 170
    loaded = load_dramaturgy_settings(project)
    assert loaded.word_tolerance_percent == 25
    assert get_dramaturgy_settings_path(project.language_work_dir_path).is_file()
