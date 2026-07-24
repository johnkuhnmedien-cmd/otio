"""Hintergrund-Job für Adobe Research-Excel Import (Start/Stop/Live-Fortschritt)."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
    """Ein globaler Import-Job (vor Projektanlage, kein project_id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ResearchImportJobState()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._plan: AdobeResearchImportPlan | None = None

    def reconcile(self) -> None:
        with self._lock:
            if self._state.status != JobStatus.RUNNING:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._state.status = JobStatus.FAILED
            self._state.error = self._state.error or (
                "Hintergrund-Job unerwartet beendet — bitte erneut starten"
            )
            self._thread = None

    def is_running(self) -> bool:
        self.reconcile()
        with self._lock:
            return self._state.status == JobStatus.RUNNING

    def get_state(self) -> ResearchImportJobState:
        self.reconcile()
        with self._lock:
            return copy.deepcopy(self._state)

    def request_cancel(self) -> bool:
        with self._lock:
            if self._state.status != JobStatus.RUNNING:
                return False
            self._cancel_event.set()
            self._state.cancel_requested = True
            self._state.message = "Stop angefordert — aktuelles Asset wird noch beendet…"
            return True

    def start(
        self,
        plan: AdobeResearchImportPlan,
        target_root: str | Path,
        *,
        chapter_titles: list[str] | None = None,
        skip_existing_ids: bool = True,
    ) -> bool:
        self.reconcile()
        with self._lock:
            if self._state.status == JobStatus.RUNNING:
                return False
            self._cancel_event = threading.Event()
            self._plan = plan
            titles = list(chapter_titles or [ch.title for ch in plan.chapters])
            self._state = ResearchImportJobState(
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
                name="adobe-research-import",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def _run(self) -> None:
        plan = self._plan
        if plan is None:
            return
        with self._lock:
            target = self._state.target_root
            titles = list(self._state.chapter_titles)
            skip_existing = self._state.skip_existing_ids

        def on_progress(event: AdobeResearchImportProgress) -> None:
            with self._lock:
                self._state.done = event.done
                self._state.total = event.total
                self._state.fraction = event.fraction
                self._state.current_folder = event.folder_name
                self._state.current_asset_id = event.asset_id
                self._state.current_chapter = event.chapter_title
                self._state.message = (
                    f"{event.done}/{event.total} · {event.folder_name} · "
                    f"{event.asset_id} · {event.status}"
                    + (f" — {event.message}" if event.message else "")
                )
                line = self._state.message
                self._state.log_lines.append(line)
                self._state.log_lines = self._state.log_lines[-40:]

        def on_live(statuses: dict[str, dict]) -> None:
            with self._lock:
                self._state.live_statuses = dict(statuses)

        try:
            result = download_research_import(
                plan,
                target,
                chapter_titles=titles,
                skip_existing_ids=skip_existing,
                progress_callback=on_progress,
                should_stop=self._cancel_event.is_set,
                live_status_callback=on_live,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._state.status = JobStatus.FAILED
                self._state.error = str(exc)
                self._state.message = f"Fehler: {exc}"
                self._thread = None
            return

        with self._lock:
            self._state.result = result
            if result.cancelled or self._cancel_event.is_set():
                self._state.status = JobStatus.CANCELLED
                self._state.message = (
                    f"Gestoppt: {result.downloaded} neu · {result.skipped} übersprungen · "
                    f"{result.errors} Fehler — Rest bleibt Open."
                )
            else:
                self._state.status = JobStatus.COMPLETED
                self._state.fraction = 1.0
                self._state.message = (
                    f"Fertig: {result.downloaded} neu · {result.skipped} übersprungen · "
                    f"{result.errors} Fehler"
                )
            self._thread = None


_MANAGER: ResearchImportJobManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_research_import_job_manager() -> ResearchImportJobManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ResearchImportJobManager()
        return _MANAGER
