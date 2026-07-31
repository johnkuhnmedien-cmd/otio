"""Globale ElevenLabs-Voice-Defaults pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_elevenlabs_settings_path
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    clear_project_elevenlabs_settings,
    elevenlabs_settings_source,
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    get_elevenlabs_voice_defaults_path,
    load_language_voice_defaults,
    save_language_voice_defaults,
)
from otio_app.services.voiceover_generation.models import (
    ElevenLabsLanguageVoiceDefaults,
    ElevenLabsSettings,
)


def _make_project(tmp_path: Path, *, language: str = "en", name: str = "A") -> Project:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "Folder").mkdir(exist_ok=True)
    return Project(
        id=f"proj-{name.lower()}",
        name=name,
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        language=language,
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
    )


@pytest.fixture()
def voice_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_save_and_load_language_defaults_roundtrip(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    saved = save_language_voice_defaults(
        "en",
        ElevenLabsLanguageVoiceDefaults(
            voice_id="voice-en-1",
            model_id="eleven_turbo_v2_5",
            stability=0.4,
        ),
    )
    assert saved.voice_id == "voice-en-1"
    loaded = load_language_voice_defaults("EN")
    assert loaded is not None
    assert loaded.voice_id == "voice-en-1"
    assert loaded.model_id == "eleven_turbo_v2_5"
    assert loaded.stability == 0.4
    assert get_elevenlabs_voice_defaults_path().is_relative_to(voice_defaults_dir)


def test_project_without_override_loads_language_default(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    save_language_voice_defaults(
        "en",
        ElevenLabsLanguageVoiceDefaults(voice_id="voice-global-en", speed=1.2),
    )
    project = _make_project(tmp_path, language="en")
    assert elevenlabs_settings_source(project) == "language_default"
    loaded = load_elevenlabs_settings(project)
    assert loaded.voice_id == "voice-global-en"
    assert loaded.speed == 1.2
    assert loaded.project_id == project.id
    assert not get_elevenlabs_settings_path(project.language_work_dir_path).is_file()


def test_second_project_same_language_gets_same_defaults(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    save_language_voice_defaults(
        "de",
        ElevenLabsLanguageVoiceDefaults(voice_id="voice-de-shared"),
    )
    a = _make_project(tmp_path, language="de", name="ProjA")
    b = _make_project(tmp_path, language="de", name="ProjB")
    assert load_elevenlabs_settings(a).voice_id == "voice-de-shared"
    assert load_elevenlabs_settings(b).voice_id == "voice-de-shared"


def test_project_override_wins_over_language_default(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    save_language_voice_defaults(
        "en",
        ElevenLabsLanguageVoiceDefaults(voice_id="voice-global-en"),
    )
    project = _make_project(tmp_path, language="en")
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(project_id=project.id, voice_id="voice-project-only"),
    )
    assert elevenlabs_settings_source(project) == "project"
    assert load_elevenlabs_settings(project).voice_id == "voice-project-only"


def test_clear_project_override_restores_language_default(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    save_language_voice_defaults(
        "en",
        ElevenLabsLanguageVoiceDefaults(voice_id="voice-global-en"),
    )
    project = _make_project(tmp_path, language="en")
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(project_id=project.id, voice_id="voice-project-only"),
    )
    assert clear_project_elevenlabs_settings(project) is True
    assert elevenlabs_settings_source(project) == "language_default"
    assert load_elevenlabs_settings(project).voice_id == "voice-global-en"


def test_language_defaults_file_never_contains_api_key(
    tmp_path: Path, voice_defaults_dir: Path
) -> None:
    save_language_voice_defaults(
        "en",
        ElevenLabsLanguageVoiceDefaults(voice_id="voice-en"),
    )
    text = get_elevenlabs_voice_defaults_path().read_text(encoding="utf-8").lower()
    assert "api_key" not in text
    assert "xi-api-key" not in text
