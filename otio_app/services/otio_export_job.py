"""Hintergrund-Job für OTIO-Export (Progress + Abbrechen)."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import resolve_otio_export_path
from otio_app.project_repository import get_project_by_id
from otio_app.services.analysis_cancel import (
    clear_cancel_flag,
    make_should_cancel,
    request_cancel_flag,
)
from otio_app.services.otio_export_debug import (
    build_otio_export_merge_debug_report,
    save_otio_export_merge_debug_report,
)
from otio_app.services.otio_export_settings import load_otio_export_settings
from otio_app.services.otio_exporter import (
    OtioExportCancelled,
    OtioExportProgressEvent,
    export_otio_timeline,
    merge_confirmed_edit_plans,
)


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class OtioExportJobState:
    project_id: str
    status: JobStatus
    folder_names: list[str]
    output_basename: str
    phase: str = ""
    message: str = ""
    detail: str = ""
    folder: str = ""
    fraction: float = 0.0
    current: int = 0
    total: int = 0
    cancel_requested: bool = False
    log_lines: list[str] = field(default_factory=list)
    output_path: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    aspect_fill_notes: list[str] = field(default_factory=list)
    debug_path: str = ""


_MAX_LOG_LINES = 80


class OtioExportJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, OtioExportJobState] = {}
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
            job.error = job.error or "Hintergrund-Job unerwartet beendet — bitte erneut starten"
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)

    def is_running(self, project_id: str) -> bool:
        self.reconcile_stuck_job(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> OtioExportJobState | None:
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
            job.message = "Abbruch angefordert…"
        project = get_project_by_id(project_id)
        if project is not None:
            request_cancel_flag(project, "otio_export")
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
            clear_cancel_flag(project, "otio_export")

    def dismiss(self, project_id: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is not None and job.status != JobStatus.RUNNING:
                del self._jobs[project_id]
                self._cancel_events.pop(project_id, None)
                self._threads.pop(project_id, None)

    def _append_log(self, project_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return
            job.log_lines.append(line)
            if len(job.log_lines) > _MAX_LOG_LINES:
                job.log_lines = job.log_lines[-_MAX_LOG_LINES:]

    def _apply_progress(self, project_id: str, event: OtioExportProgressEvent) -> None:
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return
            job.phase = event.stage
            job.message = event.message
            job.detail = event.detail
            job.folder = event.folder
            job.fraction = event.fraction
            job.current = event.current
            job.total = event.total
        line = event.message
        if event.detail:
            line = f"{line} — {event.detail}"
        self._append_log(project_id, line)

    def start(
        self,
        project: Project,
        *,
        folder_names: list[str],
        output_basename: str,
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
            self._jobs[project.id] = OtioExportJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                folder_names=list(folder_names),
                output_basename=output_basename,
                phase="starting",
                message="Export startet…",
                fraction=0.0,
            )

        clear_cancel_flag(project, "otio_export")

        def _run() -> None:
            project_id = project.id
            try:
                current = get_project_by_id(project_id)
                if current is None:
                    raise RuntimeError("Projekt nicht gefunden")

                should_cancel = make_should_cancel(
                    current, cancel_event.is_set, "otio_export"
                )

                def on_progress(event: OtioExportProgressEvent) -> None:
                    self._apply_progress(project_id, event)

                on_progress(
                    OtioExportProgressEvent(
                        stage="merge",
                        message="Schnittpläne zusammenführen…",
                        fraction=0.05,
                    )
                )
                merged = merge_confirmed_edit_plans(current, folder_names=folder_names)
                if not merged.ready:
                    debug = build_otio_export_merge_debug_report(merged)
                    debug_path = ""
                    try:
                        debug_path = str(
                            save_otio_export_merge_debug_report(current.work_dir_path, debug)
                        )
                    except OSError:
                        pass
                    with self._lock:
                        job = self._jobs.get(project_id)
                        if job is not None:
                            job.status = JobStatus.BLOCKED
                            job.error = (
                                f"Export blockiert — Merge meldet Probleme "
                                f"({debug.issue_count} Issues, Status {debug.validation_status})."
                            )
                            job.warnings = list(merged.warnings)
                            job.debug_path = debug_path
                            job.fraction = 1.0
                            job.phase = "blocked"
                            job.message = "Export blockiert"
                    return

                export_path = resolve_otio_export_path(
                    current.work_dir_path, basename=output_basename
                )
                export_settings = load_otio_export_settings(current)
                result = export_otio_timeline(
                    current,
                    merged,
                    export_settings=export_settings,
                    output_path=export_path,
                    progress_callback=on_progress,
                    should_cancel=should_cancel,
                )
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.COMPLETED
                        job.output_path = str(result.path)
                        job.warnings = [
                            w
                            for w in merged.warnings
                            if w.startswith("Regel-Hinweis (Export trotzdem möglich):")
                        ]
                        job.aspect_fill_notes = list(result.aspect_fill_notes)
                        job.fraction = 1.0
                        job.phase = "done"
                        job.message = "Export fertig"
                        job.detail = str(result.path)
            except OtioExportCancelled:
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.CANCELLED
                        job.error = "OTIO-Export abgebrochen."
                        job.phase = "cancelled"
                        job.message = "Abgebrochen"
                        job.fraction = job.fraction or 0.0
            except Exception as exc:  # noqa: BLE001 — Job-Grenze
                with self._lock:
                    job = self._jobs.get(project_id)
                    if job is not None:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        job.phase = "failed"
                        job.message = "Export fehlgeschlagen"
            finally:
                clear_cancel_flag(project, "otio_export")
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._threads.pop(project_id, None)

        thread = threading.Thread(
            target=_run,
            name=f"otio-export-{project.id}",
            daemon=True,
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True


_MANAGER: OtioExportJobManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_otio_export_job_manager() -> OtioExportJobManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = OtioExportJobManager()
        return _MANAGER
