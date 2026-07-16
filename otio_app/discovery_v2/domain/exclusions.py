"""Zentrale, testbare Ausschlussregeln für den Discovery-V2-Scanner."""

from __future__ import annotations

from pathlib import Path

# Nur exakte Verzeichnisnamen — z. B. `_otio_v2_backup` bleibt sichtbar.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "_otio",
        "_otio_v2",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
    }
)

EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
    }
)

# Unvollständige Downloads / Temp-Suffixe (keine inhaltliche Prüfung).
EXCLUDED_FILE_SUFFIXES: tuple[str, ...] = (
    ".part",
    ".crdownload",
    ".tmp",
    ".download",
)


def is_excluded_dir_name(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES


def is_excluded_file_name(name: str) -> bool:
    if name in EXCLUDED_FILE_NAMES:
        return True
    # AppleDouble / versteckte Temp-Dateien
    if name.startswith("._"):
        return True
    if name.startswith("~"):
        return True
    lower = name.lower()
    return any(lower.endswith(suffix) for suffix in EXCLUDED_FILE_SUFFIXES)


def exclusion_reason_for_dir(name: str) -> str:
    return f"reserviertes Verzeichnis: {name}"


def exclusion_reason_for_file(name: str) -> str:
    if name in EXCLUDED_FILE_NAMES:
        return f"Systemdatei: {name}"
    if name.startswith("._") or name.startswith("~"):
        return f"versteckte temporäre Datei: {name}"
    lower = name.lower()
    for suffix in EXCLUDED_FILE_SUFFIXES:
        if lower.endswith(suffix):
            return f"unvollständige/temporäre Datei ({suffix})"
    return f"ausgeschlossen: {name}"


def path_has_excluded_dir_component(relative: Path) -> bool:
    """True, wenn ein Pfadsegment exakt einem reservierten Verzeichnisnamen entspricht."""
    return any(part in EXCLUDED_DIR_NAMES for part in relative.parts)
