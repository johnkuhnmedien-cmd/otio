"""Hintergrund-Job für den sequenziellen Enhanced-Auto-Lauf."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id
from otio_app.services.analysis_cancel import (
    clear_cancel_flag,
    make_should_cancel,
    request_cancel_flag,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AUTO_RUN_STEPS,
    AutoRunProgress,
    EnhancedAutoRunCancelled,
    EnhancedAutoRunError,
    EnhancedAutoRunReport,
    run_enhanced_auto_pipeline,
)

JOB_KIND = "enhanced_auto_run"


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class EnhancedAutoRunJobState:
    project_id: str
    status: JobStatus
    step_id: str = ""
    step_label: str = ""
    message: str = ""
    step_index: int = 0
    step_total: int = len(AUTO_RUN_STEPS)
    item_label: str = ""
    item_index: int = 0
    item_total: int = 0
    log_lines: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    error: str | None = None
    report: EnhancedAutoRunReport | None = None
    run_id: int = 0


class EnhancedAutoRunJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, EnhancedAutoRunJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._run_seq = 0

    def reconcile_stuck_job(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            thread = self._threads.get(project_id)
            if job is None or job.status != JobStatus.RUNNING:
                return
            if thread is not None and thread.is_alive():
                return
            job.status = JobStatus.FAILED
            job.error = job.error or "Hintergrund-Job unerwartet beendet — bitte erneut starten"
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)

    def is_running(self, project_id: str) -> bool:
        self.reconcile_stuck_job(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def any_running(self) -> bool:
        with self._lock:
            ids = list(self._jobs)
        return any(self.is_running(pid) for pid in ids)

    def get_state(self, project_id: str) -> EnhancedAutoRunJobState | None:
        self.reconcile_stuck_job(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return None
            return copy.deepcopy(job)

    def request_cancel(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
            job = self._jobs.get(project_id)
            if job is None or job.status != JobStatus.RUNNING:
                return False
            if event is not None:
                event.set()
            job.cancel_requested = True
            job.message = "Stop angefordert…"
        project = get_project_by_id(project_id)
        if project is not None:
            request_cancel_flag(project, JOB_KIND)
        return True

    def thread_alive(self, project_id: str) -> bool | None:
        with self._lock:
            thread = self._threads.get(project_id)
            if thread is None:
                return None
            return thread.is_alive()

    def force_reset(self, project_id: str) -> None:
        with self._lock:
            event = self._cancel_events.get(project_id)
            if event is not None:
                event.set()
            job = self._jobs.get(project_id)
            if job is not None and job.status == JobStatus.RUNNING:
                job.status = JobStatus.CANCELLED
                job.cancel_requested = True
                job.error = job.error or "Manuell zurückgesetzt"
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)
        project = get_project_by_id(project_id)
        if project is not None:
            clear_cancel_flag(project, JOB_KIND)

    def dismiss(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != JobStatus.RUNNING:
                del self._jobs[project_id]
                self._cancel_events.pop(project_id, None)
                self._threads.pop(project_id, None)

    def start(self, project: Project) -> bool:
        self.reconcile_stuck_job(project.id)
        with self._lock:
            for pid, job in self._jobs.items():
                if pid == project.id or job.status != JobStatus.RUNNING:
                    continue
                other = self._threads.get(pid)
                if other is not None and other.is_alive():
                    return False
            existing = self._jobs.get(project.id)
            if existing is not None and existing.status == JobStatus.RUNNING:
                thread = self._threads.get(project.id)
                if thread is not None and thread.is_alive():
                    return False
                existing.status = JobStatus.FAILED
                existing.error = "Vorheriger Job hing fest — wird neu gestartet"
            cancel_event = threading.Event()
            self._run_seq += 1
            run_id = self._run_seq
            self._cancel_events[project.id] = cancel_event
            self._jobs[project.id] = EnhancedAutoRunJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                message="Auto-Lauf startet…",
                run_id=run_id,
            )

        clear_cancel_flag(project, JOB_KIND)

        def _run() -> None:
            project_id = project.id
            owned_run_id = run_id
            current = get_project_by_id(project_id) or project

            def _owns_job(job: EnhancedAutoRunJobState | None) -> bool:
                return job is not None and job.run_id == owned_run_id

            try:
                should_cancel = make_should_cancel(
                    current,
                    cancel_event.is_set,
                    JOB_KIND,
                )

                def on_progress(event: AutoRunProgress) -> None:
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if not _owns_job(job):
                            return
                        job.step_id = event.step_id
                        job.step_label = event.step_label
                        job.message = event.message
                        job.step_index = event.step_index
                        job.step_total = event.step_total
                        job.item_label = event.item_label
                        job.item_index = event.item_index
                        job.item_total = event.item_total
                        lines = list(job.log_lines)
                        lines.append(event.message)
                        job.log_lines = lines[-40:]

                report = run_enhanced_auto_pipeline(
                    current,
                    should_cancel=should_cancel,
                    on_progress=on_progress,
                    skip_done=True,
                )
                with self._lock:
                    job = self._jobs.get(project_id)
                    if not _owns_job(job):
                        return
                    job.report = report
                    job.skipped = list(report.skipped)
                    job.completed_steps = list(report.completed)
                    if job.status != JobStatus.RUNNING:
                        return
                    if report.stopped or should_cancel():
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                        job.message = "Auto-Lauf gestoppt."
                    else:
                        job.status = JobStatus.COMPLETED
                        job.message = report.log_lines[-1] if report.log_lines else "Fertig."
            except EnhancedAutoRunCancelled:
                with self._lock:
                    job = self._jobs.get(project_id)
                    if _owns_job(job) and job.status == JobStatus.RUNNING:
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                        job.message = "Auto-Lauf gestoppt."
            except EnhancedAutoRunError as exc:
                with self._lock:
                    job = self._jobs.get(project_id)
                    if _owns_job(job) and job.status == JobStatus.RUNNING:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        job.message = str(exc)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    job = self._jobs.get(project_id)
                    if _owns_job(job) and job.status == JobStatus.RUNNING:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        job.message = str(exc)
            finally:
                with self._lock:
                    job = self._jobs.get(project_id)
                    ours = _owns_job(job)
                    if ours:
                        if self._cancel_events.get(project_id) is cancel_event:
                            self._cancel_events.pop(project_id, None)
                        if self._threads.get(project_id) is threading.current_thread():
                            self._threads.pop(project_id, None)
                if ours:
                    clear_cancel_flag(project, JOB_KIND)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"enh-auto-run-{project.id}",
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True


_manager: EnhancedAutoRunJobManager | None = None


def get_enhanced_auto_run_job_manager() -> EnhancedAutoRunJobManager:
    global _manager
    if _manager is None:
        _manager = EnhancedAutoRunJobManager()
    return _manager
