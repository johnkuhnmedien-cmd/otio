"""Projektübergreifende Bibliothek für Raw-Style-Texte (allgemein + Intro).

Liegt global unter ``data/raw_style_library.json`` — analog zur
Style-Profile-Bibliothek, nicht unter dem Arbeitsordner eines Projekts.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import RAW_STYLE_LIBRARY_FILENAME
from otio_app.services.voiceover_generation.models import (
    RawStyleLibrary,
    RawStyleLibraryEntry,
)

__all__ = [
    "get_raw_style_library_path",
    "load_raw_style_library",
    "save_raw_style_library",
    "save_raw_to_library",
    "delete_raw_from_library",
    "get_raw_from_library",
]


def get_raw_style_library_path() -> Path:
    return ensure_data_dir() / RAW_STYLE_LIBRARY_FILENAME


def load_raw_style_library() -> RawStyleLibrary:
    path = get_raw_style_library_path()
    if not path.is_file():
        return RawStyleLibrary()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RawStyleLibrary.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return RawStyleLibrary()


def save_raw_style_library(library: RawStyleLibrary) -> RawStyleLibrary:
    path = get_raw_style_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(library.model_dump_json(indent=2), encoding="utf-8")
    return library


def save_raw_to_library(
    name: str,
    *,
    raw_reference_text: str,
    raw_intro_reference_text: str = "",
) -> RawStyleLibrary:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Bitte einen Namen für die Raw-Text-Bibliothek angeben.")
    library = load_raw_style_library()
    remaining = [entry for entry in library.entries if entry.name != cleaned_name]
    remaining.append(
        RawStyleLibraryEntry(
            name=cleaned_name,
            raw_reference_text=raw_reference_text or "",
            raw_intro_reference_text=raw_intro_reference_text or "",
        )
    )
    remaining.sort(key=lambda entry: entry.name.lower())
    return save_raw_style_library(RawStyleLibrary(entries=remaining))


def delete_raw_from_library(name: str) -> RawStyleLibrary:
    library = load_raw_style_library()
    remaining = [entry for entry in library.entries if entry.name != name]
    return save_raw_style_library(RawStyleLibrary(entries=remaining))


def get_raw_from_library(name: str) -> RawStyleLibraryEntry | None:
    library = load_raw_style_library()
    for entry in library.entries:
        if entry.name == name:
            return entry
    return None
