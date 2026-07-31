"""ElevenLabs-Einstellungen (Phase 6) — enthalten NIEMALS den API-Key.

Der API-Key kommt ausschließlich aus dem bestehenden Environment-/
User-Secrets-System (otio_app.services.api_keys); dieses Modul verwaltet nur
Voice-ID, Modell und Stimm-Parameter.

Load-Reihenfolge:
1. Projektspezifische Datei im Language-Work-Dir (Override)
2. sonst globale Defaults für ``Project.language`` unter ``data/``
3. sonst Hardcoded-Defaults
"""

from __future__ import annotations

import json
from typing import Literal

from otio_app.models import Project
from otio_app.project_layout import get_elevenlabs_settings_path
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    load_language_voice_defaults,
    normalize_voice_defaults_language,
    settings_from_language_defaults,
)
from otio_app.services.voiceover_generation.models import ElevenLabsSettings

__all__ = [
    "default_elevenlabs_settings",
    "has_project_elevenlabs_settings",
    "clear_project_elevenlabs_settings",
    "elevenlabs_settings_source",
    "describe_settings_source",
    "load_elevenlabs_settings",
    "save_elevenlabs_settings",
]

SettingsSource = Literal["project", "language_default", "builtin"]


def default_elevenlabs_settings(project: Project) -> ElevenLabsSettings:
    return ElevenLabsSettings(project_id=project.id)


def has_project_elevenlabs_settings(project: Project) -> bool:
    return get_elevenlabs_settings_path(project.language_work_dir_path).is_file()


def clear_project_elevenlabs_settings(project: Project) -> bool:
    """Löscht den Projekt-Override; danach greifen wieder Sprach-Defaults."""
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return False
    path.unlink()
    return True


def elevenlabs_settings_source(project: Project) -> SettingsSource:
    if has_project_elevenlabs_settings(project):
        return "project"
    if load_language_voice_defaults(project.language) is not None:
        return "language_default"
    return "builtin"


def load_elevenlabs_settings(project: Project) -> ElevenLabsSettings:
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ElevenLabsSettings.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass

    language_defaults = load_language_voice_defaults(project.language)
    if language_defaults is not None:
        return settings_from_language_defaults(
            project_id=project.id,
            defaults=language_defaults,
        )
    return default_elevenlabs_settings(project)


def save_elevenlabs_settings(project: Project, settings: ElevenLabsSettings) -> ElevenLabsSettings:
    normalized = settings.model_copy(update={"project_id": project.id})
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def describe_settings_source(project: Project) -> str:
    """Kurzer UI-Hinweis zur Herkunft der geladenen Settings."""
    source = elevenlabs_settings_source(project)
    lang = normalize_voice_defaults_language(project.language)
    if source == "project":
        return f"Quelle: Projekt-Override (manuell für dieses Projekt gespeichert)."
    if source == "language_default":
        return f"Quelle: globaler Sprach-Standard für **{lang}**."
    return f"Quelle: App-Defaults (noch kein Sprach-Standard für {lang})."
