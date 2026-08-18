"""Git- und Versionsinfo für Diagnose in der UI."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from otio_app import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def get_git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision or None


@lru_cache(maxsize=1)
def get_git_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = result.stdout.strip()
    return branch or None


def format_build_label() -> str:
    branch = get_git_branch()
    revision = get_git_revision()
    parts = [f"v{__version__}"]
    if branch:
        parts.append(branch)
    if revision:
        parts.append(revision)
    return " · ".join(parts)


def expected_feature_markers() -> list[str]:
    """Merkmale des aktuellen Fix-Stands — zum Abgleich in der UI."""
    return [
        "Sidebar: 🔍 Hintergrund-Aktivität",
        "URL-Pfad z. B. /systemstatus (st.navigation)",
        "Schnittplan: Radio-Navigation statt Tabs",
        "OTIO Export: ein Klick ohne Pflicht-Vorschau",
        "Sidebar-Seite ▶ Auto-Lauf (Brief → Cuts)",
        "OTIO starten.command: Start/Stop/Neustart inkl. git pull und Branch",
        "Nach Neustart bleibt /auto-lauf erreichbar (letztes Projekt + versteckte Route)",
        "Auto-Lauf-Fortschritt aktualisiert sich selbst (kein extra st.rerun)",
        "Job-Status (Clean Media, Analysen, …) aktualisiert sich selbst",
    ]
