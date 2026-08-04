"""Globale ElevenLabs-Voice-Defaults pro Sprache (unter ``data/``).

Projektspezifische Overrides bleiben in
``voiceover_generation/elevenlabs_settings.json`` im Language-Work-Dir.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    ELEVENLABS_VOICE_DEFAULTS_FILENAME,
    normalize_elevenlabs_output_format,
)
from otio_app.project_layout import language_folder_name
from otio_app.services.voiceover_generation.models import (
    ElevenLabsLanguageVoiceDefaults,
    ElevenLabsSettings,
    ElevenLabsVoiceDefaultsDocument,
)

__all__ = [
    "get_elevenlabs_voice_defaults_path",
    "normalize_voice_defaults_language",
    "load_voice_defaults_document",
    "save_voice_defaults_document",
    "load_language_voice_defaults",
    "save_language_voice_defaults",
    "delete_language_voice_defaults",
    "settings_from_language_defaults",
    "language_defaults_from_settings",
]


def get_elevenlabs_voice_defaults_path() -> Path:
    return ensure_data_dir() / ELEVENLABS_VOICE_DEFAULTS_FILENAME


def normalize_voice_defaults_language(language: str) -> str:
    return language_folder_name(language or "DE")


def load_voice_defaults_document() -> ElevenLabsVoiceDefaultsDocument:
    path = get_elevenlabs_voice_defaults_path()
    if not path.is_file():
        return ElevenLabsVoiceDefaultsDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ElevenLabsVoiceDefaultsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return ElevenLabsVoiceDefaultsDocument()


def save_voice_defaults_document(
    document: ElevenLabsVoiceDefaultsDocument,
) -> ElevenLabsVoiceDefaultsDocument:
    path = get_elevenlabs_voice_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def load_language_voice_defaults(
    language: str,
) -> ElevenLabsLanguageVoiceDefaults | None:
    key = normalize_voice_defaults_language(language)
    document = load_voice_defaults_document()
    entry = document.by_language.get(key)
    if entry is None:
        return None
    normalized_format = normalize_elevenlabs_output_format(
        entry.output_format,
        migrate_legacy_default=True,
    )
    if normalized_format == entry.output_format:
        return entry
    migrated = entry.model_copy(update={"output_format": normalized_format})
    updated = dict(document.by_language)
    updated[key] = migrated
    save_voice_defaults_document(
        ElevenLabsVoiceDefaultsDocument(by_language=updated)
    )
    return migrated


def save_language_voice_defaults(
    language: str,
    defaults: ElevenLabsLanguageVoiceDefaults | ElevenLabsSettings,
) -> ElevenLabsLanguageVoiceDefaults:
    key = normalize_voice_defaults_language(language)
    entry = language_defaults_from_settings(defaults)
    # Leeres Format → wav; absichtliches mp3 bleibt beim Speichern erhalten.
    entry = entry.model_copy(
        update={
            "output_format": normalize_elevenlabs_output_format(entry.output_format),
        }
    )
    document = load_voice_defaults_document()
    updated = dict(document.by_language)
    updated[key] = entry
    save_voice_defaults_document(
        ElevenLabsVoiceDefaultsDocument(by_language=updated)
    )
    return entry


def delete_language_voice_defaults(language: str) -> None:
    key = normalize_voice_defaults_language(language)
    document = load_voice_defaults_document()
    if key not in document.by_language:
        return
    updated = dict(document.by_language)
    del updated[key]
    save_voice_defaults_document(
        ElevenLabsVoiceDefaultsDocument(by_language=updated)
    )


def language_defaults_from_settings(
    settings: ElevenLabsLanguageVoiceDefaults | ElevenLabsSettings,
) -> ElevenLabsLanguageVoiceDefaults:
    return ElevenLabsLanguageVoiceDefaults(
        voice_id=settings.voice_id,
        model_id=settings.model_id,
        output_format=normalize_elevenlabs_output_format(settings.output_format),
        stability=settings.stability,
        similarity_boost=settings.similarity_boost,
        style=settings.style,
        use_speaker_boost=settings.use_speaker_boost,
        speed=settings.speed,
        language_code=settings.language_code,
    )


def settings_from_language_defaults(
    *,
    project_id: str,
    defaults: ElevenLabsLanguageVoiceDefaults,
) -> ElevenLabsSettings:
    return ElevenLabsSettings(
        project_id=project_id,
        voice_id=defaults.voice_id,
        model_id=defaults.model_id,
        output_format=normalize_elevenlabs_output_format(
            defaults.output_format,
            migrate_legacy_default=True,
        ),
        stability=defaults.stability,
        similarity_boost=defaults.similarity_boost,
        style=defaults.style,
        use_speaker_boost=defaults.use_speaker_boost,
        speed=defaults.speed,
        language_code=defaults.language_code,
    )
