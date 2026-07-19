"""Discovery-spezifischer Launcher für Intake-Jobs (Copy/Remux/Video-Transcode).

Kein Anschluss an die Classic-Job-Registry — Daemon-Threads + SQLite-Status.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Literal

from otio_app.discovery_v2.jobs.copy_intake_worker import process_copy_intake_run
from otio_app.discovery_v2.jobs.image_convert_worker import process_image_convert_run
from otio_app.discovery_v2.jobs.remux_intake_worker import process_remux_intake_run
from otio_app.discovery_v2.jobs.video_transcode_worker import (
    process_video_transcode_run,
)

WorkerKind = Literal["copy", "remux", "video_transcode", "image_convert"]


class IntakeJobLauncher:
    """Ein aktiver Intake-Thread pro Projekt."""

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
        if worker == "remux":
            return process_remux_intake_run
        if worker == "video_transcode":
            return process_video_transcode_run
        if worker == "image_convert":
            return process_image_convert_run
        return process_copy_intake_run

    def launch(
        self,
        *,
        project_id: str,
        project_root: Path,
        run_id: str,
        worker: WorkerKind = "copy",
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
                name=f"discovery-v2-{worker}-intake-{run_id[:8]}",
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


_LAUNCHER: IntakeJobLauncher | None = None
_LAUNCHER_LOCK = threading.Lock()


def get_intake_job_launcher() -> IntakeJobLauncher:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = IntakeJobLauncher()
        return _LAUNCHER


def reset_intake_job_launcher_for_tests() -> None:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        _LAUNCHER = None
