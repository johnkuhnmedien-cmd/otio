"""Voice-over-Dateien an Asset-Ordner über Dateinamen zuordnen."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import (
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
)
from otio_app.models import Project
from otio_app.project_layout import safe_path_is_dir
from otio_app.services.media_utils import list_media_files


def normalize_match_label(value: str) -> str:
    """Normalisiert Namen für den Abgleich (Kleinbuchstaben, _ → Leerzeichen)."""
    return value.casefold().replace("_", " ").strip()


def compact_match_label(value: str) -> str:
    """Entfernt Leerzeichen für kompakte Varianten (Florida Keys → floridakeys)."""
    return normalize_match_label(value).replace(" ", "")


def filename_contains_folder(filename: str, folder_name: str) -> bool:
    """Prüft, ob der Ordnername im Dateinamen vorkommt."""
    stem = Path(filename).stem
    normalized_stem = normalize_match_label(stem)
    compact_stem = compact_match_label(stem)
    normalized_folder = normalize_match_label(folder_name)
    compact_folder = compact_match_label(folder_name)
    if not normalized_folder:
        return False
    return normalized_folder in normalized_stem or compact_folder in compact_stem


def match_voice_file_to_folder(
    filename: str,
    folder_names: list[str],
) -> str | None:
    """Ordnet eine Voice-over-Datei dem passenden Asset-Ordner zu (längster Treffer)."""
    candidates = sorted(folder_names, key=len, reverse=True)
    for folder_name in candidates:
        if filename_contains_folder(filename, folder_name):
            return folder_name
    return None


def suggest_voice_folder_mappings(
    project: Project,
    voice_files: list[Path] | None = None,
) -> list[VoiceFolderMappingEntry]:
    """Erzeugt Zuordnungsvorschläge anhand der Dateinamen."""
    if voice_files is None:
        voice_dir = project.voice_over_dir
        if not safe_path_is_dir(voice_dir):
            return []
        voice_files = list_media_files(voice_dir)

    entries: list[VoiceFolderMappingEntry] = []
    for voice_path in voice_files:
        folder = match_voice_file_to_folder(
            voice_path.name,
            project.asset_subdir_names,
        )
        entries.append(
            VoiceFolderMappingEntry(
                voice_file=str(voice_path),
                folder=folder,
                match_method="filename",
                confirmed=False,
            )
        )
    return entries


def load_voice_folder_mapping(path: Path) -> VoiceFolderMappingDocument | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceFolderMappingDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_voice_folder_mapping(
    project: Project,
    entries: list[VoiceFolderMappingEntry],
    *,
    confirmed: bool,
) -> VoiceFolderMappingDocument:
    document = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=confirmed,
        entries=entries,
    )
    project.voice_folder_mapping_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return document


def merge_with_saved_mapping(
    project: Project,
    suggestions: list[VoiceFolderMappingEntry],
) -> list[VoiceFolderMappingEntry]:
    """Behält bestätigte manuelle Zuordnungen, ergänzt neue Voice-over-Dateien."""
    saved = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if saved is None:
        return suggestions

    saved_by_voice = {entry.voice_file: entry for entry in saved.entries}
    merged: list[VoiceFolderMappingEntry] = []
    for suggestion in suggestions:
        existing = saved_by_voice.get(suggestion.voice_file)
        if existing is not None:
            merged.append(existing)
        else:
            merged.append(suggestion)
    return merged
