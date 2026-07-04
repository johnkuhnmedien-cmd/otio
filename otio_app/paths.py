"""Pfadnormalisierung und -validierung."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_WORK_SUBDIR
from otio_app.project_layout import default_work_dir


class PathValidationError(ValueError):
    """Fehler bei der Pfadvalidierung."""


def normalize_path(raw: str) -> Path:
    """Normalisiert einen Benutzerpfad mit expanduser() und resolve()."""
    return Path(raw).expanduser().resolve()


def validate_readonly_dir(path: Path) -> Path:
    """Prüft, ob der Pfad ein existierendes Verzeichnis ist."""
    if not path.exists():
        raise PathValidationError(f"Verzeichnis existiert nicht: {path}")
    if not path.is_dir():
        raise PathValidationError(f"Pfad ist kein Verzeichnis: {path}")
    return path


def validate_project_layout(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
) -> None:
    """Prüft die grundlegende Projektordner-Struktur."""
    validate_readonly_dir(project_root)

    if work_dir == project_root:
        raise PathValidationError(
            "work_dir darf nicht identisch mit dem Projektordner sein."
        )

    voice_over_root = project_root / voice_over_subdir
    if work_dir == voice_over_root or voice_over_root in work_dir.parents:
        raise PathValidationError(
            "work_dir darf nicht im Voice-over-Ordner liegen."
        )

    if work_dir.parent == project_root and work_dir.name != DEFAULT_WORK_SUBDIR:
        raise PathValidationError(
            f"work_dir darf kein Asset-Unterordner sein: {work_dir.name}"
        )


def resolve_work_dir(project_root: Path, work_dir_raw: str | None) -> Path:
    """Ermittelt den Arbeitsordner (Standard: project_root/_otio)."""
    if work_dir_raw is None or not work_dir_raw.strip():
        return default_work_dir(project_root)
    return normalize_path(work_dir_raw)


def create_work_dir(work_dir: Path) -> Path:
    """Legt den Arbeitsordner an (nur nach ausdrücklicher UI-Bestätigung aufrufen)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir
