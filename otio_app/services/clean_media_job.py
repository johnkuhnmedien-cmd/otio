"""Hintergrund-Jobs für Clean Media (Validate + Transcode)."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from otio_app.analysis_models import CleanMediaManifest
from otio_app.models import Project
from otio_app.project_repository import get_project_by_id
from otio_app.services.clean_media import (
    list_folder_media,
    process_folder,
    validate_folder,
)


class CleanMediaJobMode(str, Enum):
    VALIDATE = "validate"
    PROCESS = "process"


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class CleanMediaJobState:
    project_id: str
    status: JobStatus
    mode: CleanMediaJobMode
    folders: list[str]
    phase: str = ""
    phase_data: dict = field(default_factory=dict)
    done_media: int = 0
    total_media: int = 0
    cancel_requested: bool = False
    manifests: dict[str, CleanMediaManifest] = field(default_factory=dict)
    error: str | None = None


class CleanMediaJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, CleanMediaJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def reconcile_stuck_job(self, project_id: str) -> None:
        """Markiert Jobs als beendet, wenn der Hintergrund-Thread nicht mehr läuft."""
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

    def get_state(self, project_id: str) -> CleanMediaJobState | None:
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
        return True

    def force_reset(self, project_id: str) -> None:
        """Hängenden oder blockierten Job sofort aus der Anzeige nehmen."""
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
        folders: list[str],
        *,
        mode: CleanMediaJobMode = CleanMediaJobMode.PROCESS,
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
            self._jobs[project.id] = CleanMediaJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                mode=mode,
                folders=list(folders),
            )

        def _run() -> None:
            project_id = project.id
            try:
                current = get_project_by_id(project_id)
                if current is None:
                    raise RuntimeError("Projekt nicht gefunden")

                def should_cancel() -> bool:
                    return cancel_event.is_set()

                manifests: dict[str, CleanMediaManifest] = {}
                total_media = 0
                done_media = 0

                for folder_index, folder_name in enumerate(folders, start=1):
                    if should_cancel():
                        break
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if job is not None:
                            job.phase = "folder_start"
                            job.phase_data = {
                                "folder": folder_name,
                                "folder_index": folder_index,
                                "folder_count": len(folders),
                            }

                    media_files = list_folder_media(current, folder_name)
                    total_media += len(media_files)

                    def on_progress(phase: str, entry) -> None:
                        nonlocal done_media
                        if should_cancel():
                            return
                        done_media += 1
                        with self._lock:
                            job = self._jobs.get(project_id)
                            if job is None:
                                return
                            job.phase = "media_done"
                            job.phase_data = {
                                "folder": folder_name,
                                "media_name": Path(entry.original_path).name,
                                "status": entry.status,
                                "done_media": done_media,
                                "total_media": max(total_media, 1),
                            }
                            job.done_media = done_media
                            job.total_media = max(total_media, 1)

                    runner = (
                        validate_folder
                        if mode == CleanMediaJobMode.VALIDATE
                        else process_folder
                    )
                    manifest = runner(
                        current,
                        folder_name,
                        should_cancel=should_cancel,
                        on_progress=on_progress,
                    )
                    manifests[folder_name] = manifest

                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is None:
                        return
                    job.manifests = manifests
                    job.status = (
                        JobStatus.CANCELLED if should_cancel() else JobStatus.COMPLETED
                    )
            except Exception as exc:
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
            finally:
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._threads.pop(project_id, None)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"clean-media-{project.id}",
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True


_manager: CleanMediaJobManager | None = None


def get_clean_media_job_manager() -> CleanMediaJobManager:
    global _manager
    if _manager is None:
        _manager = CleanMediaJobManager()
    return _manager


def summarize_manifest(manifest: CleanMediaManifest) -> dict[str, int]:
    from otio_app.services.clean_media import (
        CLEAN_STATUS_CLEAN,
        CLEAN_STATUS_FAILED,
        CLEAN_STATUS_NEEDS_TRANSCODE,
        CLEAN_STATUS_OK,
    )

    counts = {
        CLEAN_STATUS_OK: 0,
        CLEAN_STATUS_CLEAN: 0,
        CLEAN_STATUS_NEEDS_TRANSCODE: 0,
        CLEAN_STATUS_FAILED: 0,
    }
    for entry in manifest.entries:
        if entry.status in counts:
            counts[entry.status] += 1
    return counts
