"""Analyse-Protokoll unter _otio/logs/ (für Diagnose bei iCloud/Teilanalyse)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.models import Project


def analysis_log_path(project: Project) -> Path:
    return project.work_dir_path / "logs" / "analysis.log"


def append_analysis_log(project: Project, message: str) -> None:
    path = analysis_log_path(project)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        return


def read_analysis_log_tail(project: Project, *, max_lines: int = 80) -> str:
    path = analysis_log_path(project)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])
