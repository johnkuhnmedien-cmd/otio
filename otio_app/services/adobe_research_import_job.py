"""Hintergrund-Job für Adobe Research-Excel Import (Start/Stop/Live-Fortschritt)."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from otio_app.services.adobe_download_projects import (
    get_download_project,
    project_dir,
    update_download_project,
)
from otio_app.services.adobe_research_import import (
    AdobeResearchImportPlan,
    AdobeResearchImportProgress,
    AdobeResearchImportResult,
    download_research_import,
)


class JobStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class ResearchImportJobState:
    project_id: str = ""
    status: JobStatus = JobStatus.IDLE
    target_root: str = ""
    sheet_name: str = ""
    chapter_titles: list[str] = field(default_factory=list)
    skip_existing_ids: bool = True
    message: str = ""
    fraction: float = 0.0
    done: int = 0
    total: int = 0
    current_folder: str = ""
    current_asset_id: str = ""
    current_chapter: str = ""
    cancel_requested: bool = False
    error: str | None = None
    live_statuses: dict[str, dict] = field(default_factory=dict)
    result: AdobeResearchImportResult | None = None
    log_lines: list[str] = field(default_factory=list)


class ResearchImportJobManager:
    """Jobs pro Download-Projekt; parallel nur ein laufender Job global."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ResearchImportJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._plans: dict[str, AdobeResearchImportPlan] = {}

    def reconcile(self, project_id: str | None = None) -> None:
        with self._lock:
            ids = [project_id] if project_id else list(self._jobs)
            for pid in ids:
                job = self._jobs.get(pid)
                thread = self._threads.get(pid)
                if job is None or job.status != JobStatus.RUNNING:
                    continue
                if thread is not None and thread.is_alive():
                    continue
                job.status = JobStatus.FAILED
                job.error = job.error or (
                    "Hintergrund-Job unerwartet beendet — bitte erneut starten"
                )
                self._threads.pop(pid, None)
                self._cancel_events.pop(pid, None)

    def any_running(self) -> str | None:
        self.reconcile()
        with self._lock:
            for pid, job in self._jobs.items():
                if job.status == JobStatus.RUNNING:
                    return pid
        return None

    def is_running(self, project_id: str) -> bool:
        self.reconcile(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> ResearchImportJobState:
        self.reconcile(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return ResearchImportJobState(project_id=project_id)
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
            job.message = "Stop angefordert — aktuelles Asset wird noch beendet…"
            return True

    def start(
        self,
        project_id: str,
        plan: AdobeResearchImportPlan,
        target_root: str | Path,
        *,
        chapter_titles: list[str] | None = None,
        skip_existing_ids: bool = True,
    ) -> bool:
        self.reconcile()
        with self._lock:
            running = next(
                (pid for pid, job in self._jobs.items() if job.status == JobStatus.RUNNING),
                None,
            )
            if running:
                return False
            if self._jobs.get(project_id) and self._jobs[project_id].status == JobStatus.RUNNING:
                return False

            cancel_event = threading.Event()
            self._cancel_events[project_id] = cancel_event
            self._plans[project_id] = plan
            titles = list(chapter_titles or [ch.title for ch in plan.chapters])
            self._jobs[project_id] = ResearchImportJobState(
                project_id=project_id,
                status=JobStatus.RUNNING,
                target_root=str(Path(target_root).expanduser().resolve()),
                sheet_name=plan.sheet_name,
                chapter_titles=titles,
                skip_existing_ids=skip_existing_ids,
                message="Import gestartet…",
                total=sum(
                    ch.asset_count
                    for ch in plan.chapters
                    if ch.title in set(titles) or ch.folder_name in set(titles)
                ),
            )
            thread = threading.Thread(
                target=self._run,
                args=(project_id,),
                name=f"adobe-research-import-{project_id}",
                daemon=True,
            )
            self._threads[project_id] = thread
            thread.start()
            return True

    def _run(self, project_id: str) -> None:
        with self._lock:
            plan = self._plans.get(project_id)
            job = self._jobs.get(project_id)
            event = self._cancel_events.get(project_id)
            if plan is None or job is None or event is None:
                return
            target = job.target_root
            titles = list(job.chapter_titles)
            skip_existing = job.skip_existing_ids

        def on_progress(progress: AdobeResearchImportProgress) -> None:
            with self._lock:
                state = self._jobs.get(project_id)
                if state is None:
                    return
                state.done = progress.done
                state.total = progress.total
                state.fraction = progress.fraction
                state.current_folder = progress.folder_name
                state.current_asset_id = progress.asset_id
                state.current_chapter = progress.chapter_title
                state.message = (
                    f"{progress.done}/{progress.total} · {progress.folder_name} · "
                    f"{progress.asset_id} · {progress.status}"
                    + (f" — {progress.message}" if progress.message else "")
                )
                state.log_lines.append(state.message)
                state.log_lines = state.log_lines[-40:]

        def on_live(statuses: dict[str, dict]) -> None:
            with self._lock:
                state = self._jobs.get(project_id)
                if state is not None:
                    state.live_statuses = dict(statuses)

        try:
            result = download_research_import(
                plan,
                target,
                state_dir=project_dir(project_id),
                chapter_titles=titles,
                skip_existing_ids=skip_existing,
                progress_callback=on_progress,
                should_stop=event.is_set,
                live_status_callback=on_live,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                state = self._jobs.get(project_id)
                if state is not None:
                    state.status = JobStatus.FAILED
                    state.error = str(exc)
                    state.message = f"Fehler: {exc}"
                self._threads.pop(project_id, None)
                self._cancel_events.pop(project_id, None)
            return

        # Meta touch — updated_at für Sortierung
        try:
            if get_download_project(project_id) is not None:
                update_download_project(project_id)
        except Exception:
            pass

        with self._lock:
            state = self._jobs.get(project_id)
            if state is None:
                return
            state.result = result
            if result.cancelled or event.is_set():
                state.status = JobStatus.CANCELLED
                state.message = (
                    f"Gestoppt: {result.downloaded} neu · {result.skipped} übersprungen · "
                    f"{result.errors} Fehler — Rest bleibt Open."
                )
            else:
                state.status = JobStatus.COMPLETED
                state.fraction = 1.0
                state.message = (
                    f"Fertig: {result.downloaded} neu · {result.skipped} übersprungen · "
                    f"{result.errors} Fehler"
                )
            self._threads.pop(project_id, None)
            self._cancel_events.pop(project_id, None)


_MANAGER: ResearchImportJobManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_research_import_job_manager() -> ResearchImportJobManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ResearchImportJobManager()
        return _MANAGER
