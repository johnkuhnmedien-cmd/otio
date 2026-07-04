"""Hintergrund-Jobs für Asset-Analyse mit kooperativem Abbruch."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from otio_app.models import Project, ProjectStatus
from otio_app.project_repository import get_project_by_id, update_project_status
from otio_app.services.analysis_progress import AnalysisPhase, AnalysisRunReport
from otio_app.services.asset_analyzer import analyze_asset_folders


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class AssetAnalysisJobState:
    project_id: str
    status: JobStatus
    folders: list[str]
    model: str
    report: AnalysisRunReport | None = None
    error: str | None = None
    phase: AnalysisPhase | str = ""
    phase_data: dict = field(default_factory=dict)
    done_media: int = 0
    total_media: int = 0


class AssetAnalysisJobManager:
    """Ein Job pro Projekt; läuft in einem Daemon-Thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, AssetAnalysisJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def is_running(self, project_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> AssetAnalysisJobState | None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return None
            return copy.deepcopy(job)

    def start(self, project: Project, folders: list[str], model: str) -> bool:
        with self._lock:
            existing = self._jobs.get(project.id)
            if existing is not None and existing.status == JobStatus.RUNNING:
                return False
            cancel_event = threading.Event()
            self._cancel_events[project.id] = cancel_event
            self._jobs[project.id] = AssetAnalysisJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                folders=list(folders),
                model=model,
            )

        def _run() -> None:
            project_id = project.id
            try:
                current = get_project_by_id(project_id)
                if current is None:
                    raise RuntimeError("Projekt nicht gefunden")

                def on_progress(phase: AnalysisPhase, data: dict) -> None:
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if job is None:
                            return
                        job.phase = phase
                        job.phase_data = dict(data)
                        if phase == "start":
                            job.total_media = max(int(data.get("total_media", 0)), 1)
                            job.done_media = 0
                        elif phase == "media_done":
                            job.done_media += 1

                _, report = analyze_asset_folders(
                    current,
                    folders,
                    use_api=True,
                    model=model,
                    on_progress=on_progress,
                    should_cancel=cancel_event.is_set,
                )
                update_project_status(project_id, ProjectStatus.READY)
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.report = report
                    job.status = (
                        JobStatus.CANCELLED if report.cancelled else JobStatus.COMPLETED
                    )
            except Exception as exc:  # noqa: BLE001
                update_project_status(project_id, ProjectStatus.READY)
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.status = JobStatus.FAILED
                    job.error = str(exc)
            finally:
                with self._lock:
                    self._cancel_events.pop(project_id, None)

        thread = threading.Thread(target=_run, daemon=True, name=f"asset-analysis-{project.id}")
        thread.start()
        return True

    def request_cancel(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
            if event is None:
                return False
            event.set()
            return True

    def dismiss(self, project_id: str) -> None:
        """Entfernt abgeschlossene Jobs aus der Anzeige."""
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != JobStatus.RUNNING:
                del self._jobs[project_id]


_manager = AssetAnalysisJobManager()


def get_asset_analysis_job_manager() -> AssetAnalysisJobManager:
    return _manager
