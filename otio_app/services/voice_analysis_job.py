"""Hintergrund-Jobs für Voice-over-Analyse mit kooperativem Abbruch."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum

from otio_app.models import Project, ProjectStatus
from otio_app.project_repository import get_project_by_id, update_project_status
from otio_app.services.analysis_cancel import (
    clear_cancel_flag,
    make_should_cancel,
    request_cancel_flag,
)
from otio_app.services.analysis_log import append_analysis_log
from otio_app.services.analysis_progress import AnalysisPhase, VoiceAnalysisRunReport
from otio_app.services.asset_analysis_job import get_asset_analysis_job_manager
from otio_app.services.voice_analyzer import analyze_voice_over


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class VoiceAnalysisJobState:
    project_id: str
    status: JobStatus
    backend: str
    whisper_model: str
    gemini_model: str
    report: VoiceAnalysisRunReport | None = None
    error: str | None = None
    phase: AnalysisPhase | str = ""
    phase_data: dict = field(default_factory=dict)
    done_files: int = 0
    total_files: int = 0
    cancel_requested: bool = False
    chain_asset_folders: list[str] = field(default_factory=list)
    chain_asset_model: str = ""


class VoiceAnalysisJobManager:
    """Ein Voice-Job pro Projekt; läuft in einem Daemon-Thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, VoiceAnalysisJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._projects: dict[str, Project] = {}

    def is_running(self, project_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> VoiceAnalysisJobState | None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return None
            return copy.deepcopy(job)

    def start(
        self,
        project: Project,
        *,
        backend: str,
        whisper_model: str,
        gemini_model: str,
        chain_asset_folders: list[str] | None = None,
        chain_asset_model: str = "",
    ) -> bool:
        with self._lock:
            existing = self._jobs.get(project.id)
            if existing is not None and existing.status == JobStatus.RUNNING:
                return False
            cancel_event = threading.Event()
            self._cancel_events[project.id] = cancel_event
            self._projects[project.id] = project
            self._jobs[project.id] = VoiceAnalysisJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                backend=backend,
                whisper_model=whisper_model,
                gemini_model=gemini_model,
                chain_asset_folders=list(chain_asset_folders or []),
                chain_asset_model=chain_asset_model,
            )

        clear_cancel_flag(project, "voice")

        def _run() -> None:
            project_id = project.id
            chain_folders: list[str] = []
            chain_model = ""
            try:
                current = get_project_by_id(project_id)
                if current is None:
                    raise RuntimeError("Projekt nicht gefunden")

                with self._lock:
                    job_snapshot = self._jobs.get(project_id)
                    if job_snapshot is not None:
                        chain_folders = list(job_snapshot.chain_asset_folders)
                        chain_model = job_snapshot.chain_asset_model

                should_cancel = make_should_cancel(current, cancel_event.is_set, "voice")

                def on_progress(phase: AnalysisPhase, data: dict) -> None:
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if job is None:
                            return
                        job.phase = phase
                        job.phase_data = dict(data)
                        job.cancel_requested = should_cancel()
                        if phase == "start":
                            job.total_files = max(int(data.get("total_files", 0)), 1)
                            job.done_files = 0
                        elif phase == "file_done":
                            job.done_files += 1

                _, report = analyze_voice_over(
                    current,
                    use_api=True,
                    backend=backend,
                    model=gemini_model,
                    whisper_model=whisper_model,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )
                update_project_status(project_id, ProjectStatus.READY)
                clear_cancel_flag(current, "voice")
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.report = report
                    job.cancel_requested = False
                    job.status = (
                        JobStatus.CANCELLED if report.cancelled else JobStatus.COMPLETED
                    )
                if (
                    not report.cancelled
                    and chain_folders
                    and chain_model
                    and not get_asset_analysis_job_manager().is_running(project_id)
                ):
                    refreshed = get_project_by_id(project_id)
                    if refreshed is not None:
                        get_asset_analysis_job_manager().start(
                            refreshed,
                            chain_folders,
                            chain_model,
                        )
                        update_project_status(project_id, ProjectStatus.ANALYZING)
            except Exception as exc:  # noqa: BLE001
                update_project_status(project_id, ProjectStatus.READY)
                stored = self._projects.get(project_id)
                if stored is not None:
                    clear_cancel_flag(stored, "voice")
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.status = JobStatus.FAILED
                    job.error = str(exc)
                    job.cancel_requested = False
            finally:
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._projects.pop(project_id, None)

        thread = threading.Thread(target=_run, daemon=True, name=f"voice-analysis-{project.id}")
        thread.start()
        return True

    def request_cancel(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
            project = self._projects.get(project_id)
            job = self._jobs.get(project_id)
            if event is None or job is None or job.status != JobStatus.RUNNING:
                return False
            event.set()
            job.cancel_requested = True
        if project is not None:
            request_cancel_flag(project, "voice")
            append_analysis_log(project, "STOP angefordert — Voice-over-Analyse wird beendet")
        return True

    def cancel_all_running(self) -> None:
        with self._lock:
            project_ids = [
                project_id
                for project_id, job in self._jobs.items()
                if job.status == JobStatus.RUNNING
            ]
        for project_id in project_ids:
            self.request_cancel(project_id)

    def dismiss(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != JobStatus.RUNNING:
                del self._jobs[project_id]


_manager = VoiceAnalysisJobManager()


def get_voice_analysis_job_manager() -> VoiceAnalysisJobManager:
    return _manager
