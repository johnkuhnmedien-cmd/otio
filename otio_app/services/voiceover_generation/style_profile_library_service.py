"""Projektübergreifende Style-Profile-Bibliothek (Projekt ohne Voice-Over).

Im Gegensatz zu allen anderen Artefakten dieser Pipeline liegt die Bibliothek
NICHT unter dem Arbeitsordner (`_otio/`) eines einzelnen Projekts, sondern
global unter `data/` (siehe otio_app.config.ensure_data_dir()) — exakt die
gleiche Ablage wie für die Projekt-Datenbank (`data/projects.db`) und die
API-Schlüssel (`data/user_secrets.env`). Dadurch kann ein einmal erzeugtes
Style Profile in jedem beliebigen weiteren Projekt wiederverwendet werden.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import STYLE_PROFILE_LIBRARY_FILENAME
from otio_app.services.voiceover_generation.models import (
    StyleProfileLibrary,
    StyleProfileLibraryEntry,
    VoiceoverStyleProfile,
)

__all__ = [
    "get_style_profile_library_path",
    "load_style_profile_library",
    "save_style_profile_library",
    "save_profile_to_library",
    "delete_profile_from_library",
    "get_profile_from_library",
]


def get_style_profile_library_path() -> Path:
    return ensure_data_dir() / STYLE_PROFILE_LIBRARY_FILENAME


def load_style_profile_library() -> StyleProfileLibrary:
    path = get_style_profile_library_path()
    if not path.is_file():
        return StyleProfileLibrary()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return StyleProfileLibrary.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return StyleProfileLibrary()


def save_style_profile_library(library: StyleProfileLibrary) -> StyleProfileLibrary:
    path = get_style_profile_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(library.model_dump_json(indent=2), encoding="utf-8")
    return library


def save_profile_to_library(name: str, profile: VoiceoverStyleProfile) -> StyleProfileLibrary:
    """Speichert (oder ersetzt) einen benannten Eintrag in der Bibliothek.

    Ein bereits vorhandener Eintrag mit demselben Namen wird überschrieben —
    Namen sind der einzige Schlüssel der Bibliothek."""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Bitte einen Namen für die Style-Profile-Bibliothek angeben.")
    library = load_style_profile_library()
    remaining = [entry for entry in library.entries if entry.name != cleaned_name]
    remaining.append(StyleProfileLibraryEntry(name=cleaned_name, profile=profile))
    remaining.sort(key=lambda entry: entry.name.lower())
    return save_style_profile_library(StyleProfileLibrary(entries=remaining))


def delete_profile_from_library(name: str) -> StyleProfileLibrary:
    library = load_style_profile_library()
    remaining = [entry for entry in library.entries if entry.name != name]
    return save_style_profile_library(StyleProfileLibrary(entries=remaining))


def get_profile_from_library(name: str) -> VoiceoverStyleProfile | None:
    library = load_style_profile_library()
    for entry in library.entries:
        if entry.name == name:
            return entry.profile
    return None
