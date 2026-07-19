"""Discovery V2 launcher for Phase 13 export jobs."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Literal

WorkerKind = Literal["otio_export"]


class ExportJobLauncher:
    """One active export thread per project."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def is_thread_alive(self, project_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(project_id)
            return thread is not None and thread.is_alive()

    def is_active(self, project_id: str) -> bool:
        return self.is_thread_alive(project_id)

    def _worker_fn(self, worker: WorkerKind) -> Callable[[Path, str], object]:
        from otio_app.discovery_v2.jobs.export_worker import process_export_run

        return lambda project_root, run_id: process_export_run(project_root, run_id, worker=worker)

    def launch(
        self,
        *,
        project_id: str,
        project_root: Path,
        run_id: str,
        worker: WorkerKind,
        sync: bool = False,
    ) -> bool:
        process_fn = self._worker_fn(worker)
        if sync:
            with self._lock:
                existing = self._threads.get(project_id)
                if existing is not None and existing.is_alive():
                    return False
                self._threads[project_id] = threading.current_thread()
            try:
                process_fn(project_root, run_id)
            finally:
                with self._lock:
                    current = self._threads.get(project_id)
                    if current is threading.current_thread():
                        self._threads.pop(project_id, None)
            return True
        with self._lock:
            existing = self._threads.get(project_id)
            if existing is not None and existing.is_alive():
                return False
            thread = threading.Thread(
                target=self._run_in_thread,
                args=(project_id, project_root, run_id, worker),
                name=f"discovery-v2-{worker}-{run_id[:8]}",
                daemon=True,
            )
            self._threads[project_id] = thread
            thread.start()
            return True

    def _run_in_thread(self, project_id: str, project_root: Path, run_id: str, worker: WorkerKind) -> None:
        try:
            self._worker_fn(worker)(project_root, run_id)
        finally:
            with self._lock:
                current = self._threads.get(project_id)
                if current is threading.current_thread():
                    self._threads.pop(project_id, None)


_LAUNCHER: ExportJobLauncher | None = None
_LAUNCHER_LOCK = threading.Lock()


def get_export_job_launcher() -> ExportJobLauncher:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = ExportJobLauncher()
        return _LAUNCHER


def reset_export_job_launcher_for_tests() -> None:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        _LAUNCHER = None


__all__ = ["ExportJobLauncher", "get_export_job_launcher", "reset_export_job_launcher_for_tests"]
