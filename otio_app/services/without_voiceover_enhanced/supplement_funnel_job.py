"""Hintergrund-Job für den Enhanced Supplement-Funnel (Start/Stop/Fortschritt)."""

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
import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc
from otio_app.services.without_voiceover_enhanced.models import SupplementFunnelReport
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    FunnelProgressEvent,
    SupplementFunnelError,
)

JOB_KIND = "enhanced_supplement_funnel"


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class SupplementFunnelJobState:
    project_id: str
    status: JobStatus
    gap_ids: list[str] = field(default_factory=list)
    model: str = ""
    phase: str = ""
    message: str = ""
    fraction: float = 0.0
    gap_id: str = ""
    gap_index: int = 0
    gap_total: int = 0
    log_lines: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    error: str | None = None
    report: SupplementFunnelReport | None = None


class SupplementFunnelJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SupplementFunnelJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def reconcile_stuck_job(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            thread = self._threads.get(project_id)
            if job is None or job.status != JobStatus.RUNNING:
                return
            if thread is not None and thread.is_alive():
                return
            job.status = JobStatus.FAILED
            job.error = job.error or (
                "Hintergrund-Job unerwartet beendet — bitte erneut starten"
            )
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)

    def is_running(self, project_id: str) -> bool:
        self.reconcile_stuck_job(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> SupplementFunnelJobState | None:
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
            project = get_project_by_id(project_id)
        if project is not None:
            request_cancel_flag(project, JOB_KIND)
        return True

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

    def thread_alive(self, project_id: str) -> bool | None:
        with self._lock:
            thread = self._threads.get(project_id)
            if thread is None:
                return None
            return thread.is_alive()

    def dismiss(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != JobStatus.RUNNING:
                del self._jobs[project_id]
                self._cancel_events.pop(project_id, None)
                self._threads.pop(project_id, None)

    def start(
        self,
        project: Project,
        *,
        gap_ids: list[str],
        model: str,
    ) -> bool:
        self.reconcile_stuck_job(project.id)
        with self._lock:
            existing = self._jobs.get(project.id)
            if existing is not None and existing.status == JobStatus.RUNNING:
                thread = self._threads.get(project.id)
                if thread is not None and thread.is_alive():
                    return False
                existing.status = JobStatus.FAILED
                existing.error = "Vorheriger Job hing fest — wird neu gestartet"
            cancel_event = threading.Event()
            self._cancel_events[project.id] = cancel_event
            self._jobs[project.id] = SupplementFunnelJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                gap_ids=list(gap_ids),
                model=(model or "").strip(),
                message="Funnel startet…",
            )

        clear_cancel_flag(project, JOB_KIND)

        def _run() -> None:
            project_id = project.id
            # Snapshot für Runs ohne DB-Eintrag (z. B. UI-Smoke); sonst frisches DB-Objekt.
            current = get_project_by_id(project_id) or project
            try:
                should_cancel = make_should_cancel(
                    current,
                    cancel_event.is_set,
                    JOB_KIND,
                )

                def on_progress(event: FunnelProgressEvent) -> None:
                    label = event.message or event.phase
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if job is None:
                            return
                        job.phase = event.phase
                        job.message = label
                        job.fraction = float(event.fraction or 0.0)
                        job.gap_id = event.gap_id or ""
                        job.gap_index = int(event.gap_index or 0)
                        job.gap_total = int(event.gap_total or 0)
                        lines = list(job.log_lines)
                        lines.append(label)
                        job.log_lines = lines[-30:]

                with self._lock:
                    state = self._jobs.get(project_id)
                    job_model = (state.model if state is not None else "").strip()
                    job_gaps = list(state.gap_ids) if state is not None else []

                # Modulattribut: Smoke-/Monkeypatches auf funnel_svc greifen.
                report = funnel_svc.run_supplement_funnel_for_gaps(
                    current,
                    gap_ids=job_gaps,
                    model=job_model or None,
                    progress_callback=on_progress,
                    should_stop=should_cancel,
                )
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.report = report
                    job.message = report.message
                    job.fraction = 1.0
                    if report.stopped or should_cancel():
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                    else:
                        job.status = JobStatus.COMPLETED
            except SupplementFunnelError as exc:
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
            finally:
                clear_cancel_flag(project, JOB_KIND)
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._threads.pop(project_id, None)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"enh-funnel-{project.id}",
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True


_manager: SupplementFunnelJobManager | None = None


def get_supplement_funnel_job_manager() -> SupplementFunnelJobManager:
    global _manager
    if _manager is None:
        _manager = SupplementFunnelJobManager()
    return _manager
