"""Projektordner-Struktur und Asset-Erkennung."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_WORK_SUBDIR, INVENTORY_FILENAME, VOICE_ANALYSIS_FILENAME

LANGUAGE_FOLDER_NAMES: dict[str, str] = {
    "de": "DE",
    "en": "EN",
}


def language_folder_name(language: str) -> str:
    """Ordnername für Voice-over-Sprachen (z. B. de -> DE)."""
    normalized = language.strip().lower()
    return LANGUAGE_FOLDER_NAMES.get(normalized, language.strip().upper())


def default_work_dir(project_root: Path) -> Path:
    """Standard-Arbeitsordner innerhalb des Projektroots."""
    return project_root / DEFAULT_WORK_SUBDIR


def get_voice_over_dir(
    project_root: Path,
    voice_over_subdir: str,
    language: str,
) -> Path:
    """Pfad zum sprachspezifischen Voice-over-Unterordner."""
    return project_root / voice_over_subdir / language_folder_name(language)


def get_inventory_path(project_root: Path) -> Path:
    return project_root / INVENTORY_FILENAME


def get_voice_analysis_path(project_root: Path) -> Path:
    return project_root / VOICE_ANALYSIS_FILENAME


def reserved_subdir_names(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
) -> set[str]:
    """Unterordner, die keine Asset-Ordner sind."""
    reserved = {voice_over_subdir, DEFAULT_WORK_SUBDIR}
    try:
        if work_dir.parent == project_root:
            reserved.add(work_dir.name)
    except ValueError:
        pass
    return reserved


def discover_asset_subdirs(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
) -> list[Path]:
    """Listet Asset-Unterordner im Projektroot (nur lesen, keine Änderungen)."""
    return [
        project_root / name
        for name in discover_asset_subdir_names(project_root, work_dir, voice_over_subdir)
    ]


def discover_asset_subdir_names(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
) -> list[str]:
    """Listet Asset-Unterordner-Namen — bricht bei iCloud-/Netzwerkfehlern nicht ab."""
    if not project_root.is_dir():
        return []

    reserved = reserved_subdir_names(project_root, work_dir, voice_over_subdir)
    names: list[str] = []
    try:
        entries = sorted(project_root.iterdir())
    except OSError:
        return []

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if entry.name.startswith("."):
            continue
        if entry.name in reserved:
            continue
        names.append(entry.name)
    return names


def safe_path_is_dir(path: Path) -> bool:
    """Prüft ein Verzeichnis ohne Hänger bei nicht verfügbaren Cloud-Dateien."""
    try:
        return path.is_dir()
    except OSError:
        return False
