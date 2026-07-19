"""Discovery-V2 launcher for editorial jobs."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Literal

WorkerKind = Literal[
    "editorial_narrative",
    "editorial_script",
    "editorial_structure",
    "editorial_coverage",
]


class EditorialJobLauncher:
    """One active editorial thread per project."""

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
        from otio_app.discovery_v2.jobs.editorial_worker import (
            process_editorial_run,
        )

        if worker in {
            "editorial_narrative",
            "editorial_script",
            "editorial_structure",
            "editorial_coverage",
        }:
            return process_editorial_run
        raise ValueError(f"Unsupported editorial worker: {worker}")

    def launch(
        self,
        *,
        project_id: str,
        project_root: Path,
        run_id: str,
        worker: WorkerKind = "editorial_narrative",
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

    def _run_in_thread(
        self,
        project_id: str,
        project_root: Path,
        run_id: str,
        worker: WorkerKind,
    ) -> None:
        try:
            self._worker_fn(worker)(project_root, run_id)
        finally:
            with self._lock:
                current = self._threads.get(project_id)
                if current is threading.current_thread():
                    self._threads.pop(project_id, None)


_LAUNCHER: EditorialJobLauncher | None = None
_LAUNCHER_LOCK = threading.Lock()


def get_editorial_job_launcher() -> EditorialJobLauncher:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = EditorialJobLauncher()
        return _LAUNCHER


def reset_editorial_job_launcher_for_tests() -> None:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        _LAUNCHER = None


__all__ = [
    "EditorialJobLauncher",
    "get_editorial_job_launcher",
    "reset_editorial_job_launcher_for_tests",
]
