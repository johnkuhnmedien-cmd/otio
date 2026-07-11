"""Dateibasiertes Abbrechen — funktioniert auch bei Streamlit-Neustart."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from otio_app.models import Project


class AnalysisCancelledError(Exception):
    """Analyse wurde vom Nutzer abgebrochen."""


def cancel_flag_path(project: Project, job_kind: str = "asset") -> Path:
    return project.work_dir_path / "jobs" / f"{job_kind}_analysis_{project.id}.cancel"


def clear_cancel_flag(project: Project, job_kind: str = "asset") -> None:
    try:
        cancel_flag_path(project, job_kind).unlink(missing_ok=True)
    except OSError:
        pass


def request_cancel_flag(project: Project, job_kind: str = "asset") -> None:
    path = cancel_flag_path(project, job_kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="utf-8")


def is_cancel_flag_set(project: Project, job_kind: str = "asset") -> bool:
    return cancel_flag_path(project, job_kind).is_file()


def make_should_cancel(
    project: Project,
    event_is_set: Callable[[], bool],
    job_kind: str = "asset",
) -> Callable[[], bool]:
    """Kombiniert Thread-Event und Cancel-Datei."""

    def should_cancel() -> bool:
        return bool(event_is_set() or is_cancel_flag_set(project, job_kind))

    return should_cancel
