"""Dateibasiertes Abbrechen — funktioniert auch bei Streamlit-Neustart."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from otio_app.models import Project


class AnalysisCancelledError(Exception):
    """Asset-Analyse wurde vom Nutzer abgebrochen."""


def cancel_flag_path(project: Project) -> Path:
    return project.work_dir_path / "jobs" / f"asset_analysis_{project.id}.cancel"


def clear_cancel_flag(project: Project) -> None:
    try:
        cancel_flag_path(project).unlink(missing_ok=True)
    except OSError:
        pass


def request_cancel_flag(project: Project) -> None:
    path = cancel_flag_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="utf-8")


def is_cancel_flag_set(project: Project) -> bool:
    return cancel_flag_path(project).is_file()


def make_should_cancel(project: Project, event_is_set: Callable[[], bool]) -> Callable[[], bool]:
    """Kombiniert Thread-Event und Cancel-Datei."""

    def should_cancel() -> bool:
        return bool(event_is_set() or is_cancel_flag_set(project))

    return should_cancel
