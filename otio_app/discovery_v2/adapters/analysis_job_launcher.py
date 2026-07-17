"""Discovery-V2 launcher for analysis-prepare jobs.

No connection to the classic job registry: daemon threads + SQLite status.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Literal

WorkerKind = Literal["analysis_prepare"]


class AnalysisJobLauncher:
    """One active analysis-prepare thread per project."""

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
        if worker == "analysis_prepare":
            from otio_app.discovery_v2.jobs.analysis_prepare_worker import (
                process_analysis_prepare_run,
            )

            return process_analysis_prepare_run
        raise ValueError(f"Unsupported analysis worker: {worker}")

    def launch(
        self,
        *,
        project_id: str,
        project_root: Path,
        run_id: str,
        worker: WorkerKind = "analysis_prepare",
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


_LAUNCHER: AnalysisJobLauncher | None = None
_LAUNCHER_LOCK = threading.Lock()


def get_analysis_job_launcher() -> AnalysisJobLauncher:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = AnalysisJobLauncher()
        return _LAUNCHER


def reset_analysis_job_launcher_for_tests() -> None:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        _LAUNCHER = None
