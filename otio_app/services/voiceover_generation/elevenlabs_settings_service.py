"""ElevenLabs-Einstellungen (Phase 6) — enthalten NIEMALS den API-Key.

Der API-Key kommt ausschließlich aus dem bestehenden Environment-/
User-Secrets-System (otio_app.services.api_keys); dieses Modul verwaltet nur
Voice-ID, Modell und Stimm-Parameter.
"""

from __future__ import annotations

import json

from otio_app.models import Project
from otio_app.project_layout import get_elevenlabs_settings_path
from otio_app.services.voiceover_generation.models import ElevenLabsSettings

__all__ = [
    "default_elevenlabs_settings",
    "load_elevenlabs_settings",
    "save_elevenlabs_settings",
]


def default_elevenlabs_settings(project: Project) -> ElevenLabsSettings:
    return ElevenLabsSettings(project_id=project.id)


def load_elevenlabs_settings(project: Project) -> ElevenLabsSettings:
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return default_elevenlabs_settings(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ElevenLabsSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_elevenlabs_settings(project)


def save_elevenlabs_settings(project: Project, settings: ElevenLabsSettings) -> ElevenLabsSettings:
    normalized = settings.model_copy(update={"project_id": project.id})
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
