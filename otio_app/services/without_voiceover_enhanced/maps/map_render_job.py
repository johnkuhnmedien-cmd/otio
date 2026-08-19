"""Hintergrund-Job: paralleles Kartenrendern nach ausdrücklichem Klick."""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id
from otio_app.services.analysis_cancel import (
    clear_cancel_flag,
    make_should_cancel,
    request_cancel_flag,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.maps.models import (
    RENDER_STATUS_CANCELLED,
    RENDER_STATUS_DONE,
    RENDER_STATUS_FAILED,
    RENDER_STATUS_PREPARING,
    RENDER_STATUS_WAITING,
    MapPlanItem,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    clamp_max_parallel,
    load_map_plan,
    save_map_plan,
)
from otio_app.services.without_voiceover_enhanced.maps.render_service import (
    MapRenderCancelled,
    MapRenderError,
    MapRenderer,
    selectable_maps,
    status_from_progress,
)
from otio_app.services.without_voiceover_enhanced.paths import map_render_job_path
from pydantic import BaseModel, Field

JOB_KIND = "enhanced_map_render"
RenderMode = Literal["all", "missing", "one"]


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class MapItemRuntimeState(BaseModel):
    chapter_id: str
    status: str = RENDER_STATUS_WAITING
    progress: float = 0.0
    error: str = ""
    output_path: str = ""
    reused: bool = False


class MapRenderJobDocument(BaseModel):
    project_id: str
    status: str = JobStatus.RUNNING.value
    mode: str = "all"
    message: str = ""
    error: str | None = None
    overall_progress: float = 0.0
    max_parallel: int = 4
    resolution: str = "hd"
    chapter_ids: list[str] = Field(default_factory=list)
    items: dict[str, MapItemRuntimeState] = Field(default_factory=dict)
    cancel_requested: bool = False
    run_id: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MapRenderJobState:
    project_id: str
    status: JobStatus
    mode: str = "all"
    message: str = ""
    error: str | None = None
    overall_progress: float = 0.0
    max_parallel: int = 4
    resolution: str = "hd"
    chapter_ids: list[str] = field(default_factory=list)
    items: dict[str, MapItemRuntimeState] = field(default_factory=dict)
    cancel_requested: bool = False
    run_id: int = 0

    def to_document(self) -> MapRenderJobDocument:
        return MapRenderJobDocument(
            project_id=self.project_id,
            status=self.status.value,
            mode=self.mode,
            message=self.message,
            error=self.error,
            overall_progress=self.overall_progress,
            max_parallel=self.max_parallel,
            resolution=self.resolution,
            chapter_ids=list(self.chapter_ids),
            items=dict(self.items),
            cancel_requested=self.cancel_requested,
            run_id=self.run_id,
        )


def _state_from_document(document: MapRenderJobDocument) -> MapRenderJobState:
    try:
        status = JobStatus(document.status)
    except ValueError:
        status = JobStatus.FAILED
    return MapRenderJobState(
        project_id=document.project_id,
        status=status,
        mode=document.mode,
        message=document.message,
        error=document.error,
        overall_progress=document.overall_progress,
        max_parallel=document.max_parallel,
        resolution=document.resolution,
        chapter_ids=list(document.chapter_ids),
        items=dict(document.items),
        cancel_requested=document.cancel_requested,
        run_id=document.run_id,
    )


class MapRenderJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plan_lock = threading.Lock()
        self._jobs: dict[str, MapRenderJobState] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._renderers: dict[str, MapRenderer] = {}
        self._run_seq = 0

    def _persist(self, project: Project, job: MapRenderJobState) -> None:
        write_json(map_render_job_path(project), job.to_document())

    def _load_disk(self, project_id: str) -> MapRenderJobState | None:
        project = get_project_by_id(project_id)
        if project is None:
            return None
        loaded = load_model(map_render_job_path(project), MapRenderJobDocument)
        if loaded is None:
            return None
        return _state_from_document(loaded)

    def reconcile_stuck_job(self, project_id: str) -> None:
        project = get_project_by_id(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            thread = self._threads.get(project_id)
        if job is None:
            disk = self._load_disk(project_id)
            if disk is None:
                return
            with self._lock:
                job = self._jobs.setdefault(project_id, disk)
                thread = self._threads.get(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            thread = self._threads.get(project_id)
            if job is None or job.status != JobStatus.RUNNING:
                return
            if thread is not None and thread.is_alive():
                return
            job.status = JobStatus.CANCELLED
            job.cancel_requested = True
            job.error = (
                job.error
                or "Karten-Render wurde durch einen App-Neustart unterbrochen — bitte fortsetzen."
            )
            job.message = job.error
            for item in job.items.values():
                if item.status not in {RENDER_STATUS_DONE, RENDER_STATUS_FAILED}:
                    item.status = RENDER_STATUS_CANCELLED
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)
            self._renderers.pop(project_id, None)
            snapshot = copy.deepcopy(job)
        if project is not None:
            self._persist(project, snapshot)
            self._mark_plan_cancelled(project, snapshot)

    def _mark_plan_cancelled(self, project: Project, job: MapRenderJobState) -> None:
        with self._plan_lock:
            plan = load_map_plan(project)
            if plan is None:
                return
            changed = False
            for item in plan.maps:
                runtime = job.items.get(item.chapter_id)
                if runtime is None:
                    continue
                if runtime.status == RENDER_STATUS_CANCELLED and item.render_status not in {
                    RENDER_STATUS_DONE,
                    RENDER_STATUS_FAILED,
                }:
                    item.render_status = RENDER_STATUS_CANCELLED
                    item.error_detail = job.error or "Abgebrochen"
                    changed = True
            if changed:
                save_map_plan(project, plan)

    def is_running(self, project_id: str) -> bool:
        self.reconcile_stuck_job(project_id)
        with self._lock:
            job = self._jobs.get(project_id)
            return job is not None and job.status == JobStatus.RUNNING

    def get_state(self, project_id: str) -> MapRenderJobState | None:
        self.reconcile_stuck_job(project_id)
        disk = None
        with self._lock:
            job = self._jobs.get(project_id)
        if job is None:
            disk = self._load_disk(project_id)
            if disk is None:
                return None
            with self._lock:
                job = self._jobs.setdefault(project_id, disk)
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None:
                return None
            return copy.deepcopy(job)

    def request_cancel(self, project_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(project_id)
            job = self._jobs.get(project_id)
            renderer = self._renderers.get(project_id)
            if job is None or job.status != JobStatus.RUNNING:
                return False
            if event is not None:
                event.set()
            job.cancel_requested = True
            job.message = "Stop angefordert…"
        if renderer is not None:
            renderer.kill_all()
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
            renderer = self._renderers.get(project_id)
            job = self._jobs.get(project_id)
            if job is not None and job.status == JobStatus.RUNNING:
                job.status = JobStatus.CANCELLED
                job.cancel_requested = True
                job.error = job.error or "Manuell zurückgesetzt"
            self._cancel_events.pop(project_id, None)
            self._threads.pop(project_id, None)
            self._renderers.pop(project_id, None)
        if renderer is not None:
            renderer.kill_all()
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
                self._renderers.pop(project_id, None)

    def start(
        self,
        project: Project,
        *,
        mode: RenderMode = "all",
        chapter_id: str | None = None,
        renderer: MapRenderer | None = None,
    ) -> bool:
        self.reconcile_stuck_job(project.id)
        plan = load_map_plan(project)
        if plan is None:
            return False
        targets = selectable_maps(plan.maps, mode=mode, chapter_id=chapter_id)
        if not targets:
            return False
        max_parallel = clamp_max_parallel(plan.settings.resolution, plan.settings.max_parallel)
        with self._lock:
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
            active_renderer = renderer or MapRenderer()
            active_renderer.reset_kill_flag()
            self._cancel_events[project.id] = cancel_event
            self._renderers[project.id] = active_renderer
            items = {
                item.chapter_id: MapItemRuntimeState(
                    chapter_id=item.chapter_id,
                    status=RENDER_STATUS_WAITING,
                    progress=0.0,
                )
                for item in targets
            }
            self._jobs[project.id] = MapRenderJobState(
                project_id=project.id,
                status=JobStatus.RUNNING,
                mode="one" if chapter_id else mode,
                message="Kartenrender startet…",
                max_parallel=max_parallel,
                resolution=plan.settings.resolution,
                chapter_ids=[item.chapter_id for item in targets],
                items=items,
                run_id=run_id,
            )
            snapshot = copy.deepcopy(self._jobs[project.id])
        self._persist(project, snapshot)
        self._mark_plan_waiting(project, snapshot)
        clear_cancel_flag(project, JOB_KIND)
        captured = project

        def _run() -> None:
            current = get_project_by_id(captured.id) or captured
            self._execute(
                current,
                run_id,
                cancel_event,
                active_renderer,
                list(targets),
                max_parallel,
                overwrite=bool(chapter_id) or mode == "one",
            )

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"enh-map-render-{project.id}",
        )
        with self._lock:
            self._threads[project.id] = thread
        thread.start()
        return True

    def _mark_plan_waiting(self, project: Project, job: MapRenderJobState) -> None:
        with self._plan_lock:
            plan = load_map_plan(project)
            if plan is None:
                return
            wanted = set(job.chapter_ids)
            for item in plan.maps:
                if item.chapter_id in wanted:
                    item.render_status = RENDER_STATUS_WAITING
                    item.progress = 0.0
                    item.error_detail = ""
            save_map_plan(project, plan)

    def _execute(
        self,
        project: Project,
        owned_run_id: int,
        cancel_event: threading.Event,
        renderer: MapRenderer,
        targets: list[MapPlanItem],
        max_parallel: int,
        overwrite: bool = False,
    ) -> None:
        project_id = project.id

        def _owns(job: MapRenderJobState | None) -> bool:
            return job is not None and job.run_id == owned_run_id

        should_cancel = make_should_cancel(project, cancel_event.is_set, JOB_KIND)
        failures = 0
        cancelled = False

        def _update_item(chapter_id: str, **changes: object) -> None:
            with self._lock:
                job = self._jobs.get(project_id)
                if job is None or not _owns(job):
                    return
                runtime = job.items.get(chapter_id)
                if runtime is None:
                    return
                if "progress" in changes:
                    runtime.progress = max(runtime.progress, float(changes["progress"]))
                if "status" in changes:
                    runtime.status = str(changes["status"])
                if "error" in changes:
                    runtime.error = str(changes["error"])
                if "output_path" in changes:
                    runtime.output_path = str(changes["output_path"])
                if "reused" in changes:
                    runtime.reused = bool(changes["reused"])
                mean = (
                    sum(item.progress for item in job.items.values()) / max(len(job.items), 1)
                )
                job.overall_progress = max(job.overall_progress, mean)
                self._persist(project, copy.deepcopy(job))

        def _render_one(item: MapPlanItem) -> None:
            nonlocal failures, cancelled
            if should_cancel():
                cancelled = True
                _update_item(
                    item.chapter_id,
                    status=RENDER_STATUS_CANCELLED,
                    error="Abgebrochen",
                )
                return
            _update_item(item.chapter_id, status=RENDER_STATUS_PREPARING, progress=0.01)
            last = 0.0

            def on_progress(value: float) -> None:
                nonlocal last
                last = max(last, max(0.0, min(1.0, value)))
                _update_item(
                    item.chapter_id,
                    progress=last,
                    status=status_from_progress(last),
                )
                with self._lock:
                    job = self._jobs.get(project_id)
                    if _owns(job):
                        job.message = (
                            f"{item.original_chapter_label}: "
                            f"{int(last * 100)} %"
                        )

            try:
                result = renderer.render_item(
                    project,
                    item,
                    overwrite=overwrite,
                    progress_callback=on_progress,
                    should_cancel=should_cancel,
                )
                export_path = str(result.get("export_path") or "")
                _update_item(
                    item.chapter_id,
                    status=RENDER_STATUS_DONE,
                    progress=1.0,
                    output_path=export_path,
                    reused=bool(result.get("reused")),
                    error="",
                )
                self._write_plan_item(
                    project,
                    item.chapter_id,
                    render_status=RENDER_STATUS_DONE,
                    output_path=export_path,
                    media_hash=str(result.get("content_hash") or ""),
                    progress=1.0,
                    error_detail="",
                )
            except MapRenderCancelled:
                cancelled = True
                _update_item(
                    item.chapter_id,
                    status=RENDER_STATUS_CANCELLED,
                    error="Abgebrochen",
                )
                self._write_plan_item(
                    project,
                    item.chapter_id,
                    render_status=RENDER_STATUS_CANCELLED,
                    error_detail="Abgebrochen",
                )
            except MapRenderError as exc:
                failures += 1
                _update_item(
                    item.chapter_id,
                    status=RENDER_STATUS_FAILED,
                    error=str(exc),
                )
                self._write_plan_item(
                    project,
                    item.chapter_id,
                    render_status=RENDER_STATUS_FAILED,
                    error_detail=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                _update_item(
                    item.chapter_id,
                    status=RENDER_STATUS_FAILED,
                    error=str(exc) or type(exc).__name__,
                )
                self._write_plan_item(
                    project,
                    item.chapter_id,
                    render_status=RENDER_STATUS_FAILED,
                    error_detail=str(exc) or type(exc).__name__,
                )

        try:
            workers = max(1, max_parallel)
            if workers == 1 or len(targets) == 1:
                for item in targets:
                    if should_cancel():
                        cancelled = True
                        break
                    _render_one(item)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_render_one, item) for item in targets]
                    for future in as_completed(futures):
                        future.result()
                        if should_cancel():
                            cancelled = True
                            renderer.kill_all()
        finally:
            with self._lock:
                job = self._jobs.get(project_id)
                if _owns(job) and job.status == JobStatus.RUNNING:
                    if cancelled or should_cancel():
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                        job.message = "Kartenrender abgebrochen."
                    elif failures and failures == len(targets):
                        job.status = JobStatus.FAILED
                        job.error = "Alle Karten sind fehlgeschlagen."
                        job.message = job.error
                    elif failures:
                        job.status = JobStatus.COMPLETED
                        job.message = (
                            f"Kartenrender fertig — {failures} Fehler, Rest erfolgreich."
                        )
                    else:
                        job.status = JobStatus.COMPLETED
                        job.overall_progress = 1.0
                        job.message = "Kartenrender fertig."
                    snapshot = copy.deepcopy(job)
                else:
                    snapshot = copy.deepcopy(job) if job is not None else None
                if self._cancel_events.get(project_id) is cancel_event:
                    self._cancel_events.pop(project_id, None)
                if self._threads.get(project_id) is threading.current_thread():
                    self._threads.pop(project_id, None)
                if self._renderers.get(project_id) is renderer:
                    self._renderers.pop(project_id, None)
                if snapshot is not None:
                    self._persist(project, snapshot)
            clear_cancel_flag(project, JOB_KIND)

    def _write_plan_item(
        self,
        project: Project,
        chapter_id: str,
        *,
        render_status: str,
        output_path: str = "",
        media_hash: str = "",
        progress: float | None = None,
        error_detail: str = "",
    ) -> None:
        with self._plan_lock:
            plan = load_map_plan(project)
            if plan is None:
                return
            for item in plan.maps:
                if item.chapter_id != chapter_id:
                    continue
                item.render_status = render_status
                if output_path:
                    item.output_path = output_path
                if media_hash:
                    item.media_hash = media_hash
                if progress is not None:
                    item.progress = max(item.progress, progress)
                item.error_detail = error_detail
            save_map_plan(project, plan)


_manager: MapRenderJobManager | None = None


def get_map_render_job_manager() -> MapRenderJobManager:
    global _manager
    if _manager is None:
        _manager = MapRenderJobManager()
    return _manager


def reset_map_render_job_manager_for_tests() -> None:
    global _manager
    _manager = None
