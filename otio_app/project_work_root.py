"""Zentrale, mode-aware Auflösung der Projekt-Arbeitswurzel.

Classic / Without-VO: gespeicherter ``work_dir`` (üblicherweise ``_otio``).
Discovery V2: immer ``<project_root>/_otio_v2`` — der DB-Wert ``work_dir``
wird bewusst ignoriert und darf nie als Schreibziel dienen.
"""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.models import Project, ProjectMode


def resolve_project_work_root(project: Project) -> Path:
    """Liefert die produktive Artefakt-/Arbeitswurzel anhand von ``project_mode``.

    Discovery ignoriert einen gespeicherten Classic-``work_dir``-String vollständig.
    Es werden keine Verzeichnisse angelegt.
    """
    root = Path(project.project_root).expanduser().resolve()
    if project.project_mode == ProjectMode.DISCOVERY_V2:
        return root / DEFAULT_DISCOVERY_V2_WORK_SUBDIR

    # Bestehende Pipelines: gespeicherter work_dir (Kompatibilität).
    stored = Path(project.work_dir).expanduser()
    if not stored.is_absolute():
        stored = root / stored
    try:
        return stored.resolve()
    except OSError:
        return stored


def is_discovery_work_root(path: Path, project: Project) -> bool:
    """True, wenn ``path`` die Discovery-Arbeitswurzel des Projekts ist."""
    if project.project_mode != ProjectMode.DISCOVERY_V2:
        return False
    try:
        return path.expanduser().resolve() == resolve_project_work_root(project)
    except OSError:
        return False


def classic_work_subdir_name() -> str:
    return DEFAULT_WORK_SUBDIR


def discovery_work_subdir_name() -> str:
    return DEFAULT_DISCOVERY_V2_WORK_SUBDIR
