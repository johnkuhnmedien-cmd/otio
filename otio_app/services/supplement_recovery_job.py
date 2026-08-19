"""Hintergrund-Job für die Bestandsaufnahme beschaffter Assets.

Die Wiederherstellung analysiert jedes Asset einzeln — bei über hundert
Bestands-Assets dauert das entsprechend lang. Ein blockierender Spinner wäre
dafür der falsche Ort: der Browser dürfte nicht neu laden und man sieht nicht,
wo der Lauf steht.

Deshalb dasselbe Muster wie die Asset-Analyse: Daemon-Thread, Fortschritt über
Callback, kooperativer Abbruch. Ein Abbruch ist ungefährlich, weil jedes Asset
sofort ins Inventar und in den Supplement-Cache geschrieben wird.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id
from otio_app.services.supplement_recovery import (
    SupplementRecoveryReport,
    recover_supplements_into_inventory,
)


class RecoveryJobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class SupplementRecoveryJobState:
    project_id: str
    status: RecoveryJobStatus
    folders: list[str] = field(default_factory=list)
    model: str = ""
    total: int = 0
    done: int = 0
    current_media: str = ""
    current_folder: str = ""
    report: SupplementRecoveryReport | None = None
    error: str | None = None
    cancel_requested: bool = False

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(max(self.done / self.total, 0.0), 1.0)


class SupplementRecoveryJobManager:
    """Ein Wiederherstellungs-Job pro Projekt."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SupplementRecoveryJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def _reconcile(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            thread = self._threads.get(project_id)
            if job is None or job.status != RecoveryJobStatus.RUNNING:
                return
            if thread is not None and thread.is_alive():
                return
            job.status = RecoveryJobStatus.FAILED
            job.error = job.error or (
                "Hintergrund-Job unerwartet beendet — bitte erneut starten"
            )
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)

    def is_running(self, project_id: str) -> bool:
        self._reconcile(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == RecoveryJobStatus.RUNNING

    def get_state(self, project_id: str) -> SupplementRecoveryJobState | None:
        self._reconcile(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return copy.deepcopy(job) if job is not None else None

    def start(
        self,
        project: Project,
        *,
        folder_names: list[str] | None = None,
        model: Optional[str] = None,
        limit: int | None = None,
    ) -> bool:
        with self._lock:
            existing = self._jobs.get(project.id)
            if existing is not None and existing.status == RecoveryJobStatus.RUNNING:
                return False
            cancel_event = threading.Event()
            self._cancel_events[project.id] = cancel_event
            self._jobs[project.id] = SupplementRecoveryJobState(
                project_id=project.id,
                status=RecoveryJobStatus.RUNNING,
                folders=list(folder_names or []),
                model=model or "",
            )

        project_id = project.id

        def _on_progress(event: str, payload: dict) -> None:
            with self._lock:
                job = self._jobs.get(project_id)
                if job is None:
                    return
                if event == "start":
                    job.total = int(payload.get("total", 0))
                    job.done = 0
                elif event == "item_start":
                    job.current_media = str(payload.get("media_name", ""))
                    job.current_folder = str(payload.get("folder", ""))
                elif event == "item_done":
                    job.done += 1
                job.cancel_requested = cancel_event.is_set()

        def _run() -> None:
            try:
                current = get_project_by_id(project_id) or project
                report = recover_supplements_into_inventory(
                    current,
                    folder_names=folder_names,
                    model=model,
                    limit=limit,
                    on_progress=_on_progress,
                    should_cancel=cancel_event.is_set,
                )
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.report = report
                    job.cancel_requested = False
                    job.status = (
                        RecoveryJobStatus.CANCELLED
                        if report.cancelled
                        else RecoveryJobStatus.COMPLETED
                    )
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.status = RecoveryJobStatus.FAILED
                    job.error = str(exc)
                    job.cancel_requested = False
            finally:
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._threads.pop(project_id, None)

        thread = threading.Thread(
            target=_run, daemon=True, name=f"supplement-recovery-{project.id}"
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True

    def request_cancel(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
            job = self._jobs.get(project_id)
            if event is None or job is None or job.status != RecoveryJobStatus.RUNNING:
                return False
            event.set()
            job.cancel_requested = True
        return True

    def dismiss(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != RecoveryJobStatus.RUNNING:
                del self._jobs[project_id]


_manager = SupplementRecoveryJobManager()


def get_supplement_recovery_job_manager() -> SupplementRecoveryJobManager:
    return _manager
