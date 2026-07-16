"""Pfadregeln für Discovery V2 — getrennt von Classic ``_otio/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR


def get_discovery_v2_root(project_root: Path) -> Path:
    """Berechnet die Discovery-Artefaktwurzel: ``<project_root>/_otio_v2``."""
    return Path(project_root).expanduser().resolve() / DEFAULT_DISCOVERY_V2_WORK_SUBDIR


def is_under_discovery_v2(path: Path, project_root: Path) -> bool:
    """True, wenn ``path`` unter der Discovery-Wurzel des Projekts liegt."""
    root = get_discovery_v2_root(project_root)
    try:
        Path(path).expanduser().resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def assert_path_is_under_discovery_v2(path: Path, project_root: Path) -> Path:
    """Stellt sicher, dass ein Schreibziel unter ``_otio_v2`` liegt.

    Verhindert versehentliches Schreiben nach ``_otio/`` oder in Asset-Ordner.
    """
    resolved = Path(path).expanduser().resolve()
    root = get_discovery_v2_root(project_root)
    classic = Path(project_root).expanduser().resolve() / DEFAULT_WORK_SUBDIR
    try:
        resolved.relative_to(classic)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"Discovery V2 darf nicht nach `{DEFAULT_WORK_SUBDIR}/` schreiben "
            f"(Ziel: {resolved})."
        )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Discovery V2 darf nur unter `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/` "
            f"schreiben (Ziel: {resolved}, erlaubt: {root})."
        ) from exc
    return resolved
