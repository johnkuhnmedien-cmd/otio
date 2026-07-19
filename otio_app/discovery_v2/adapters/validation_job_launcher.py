"""Enger Discovery-spezifischer Job-Launcher für technische Prüfungen.

Kein Anschluss an die Classic-Job-Registry — nur Daemon-Threads + SQLite-Status.
"""

from __future__ import annotations

import threading
from pathlib import Path

from otio_app.discovery_v2.jobs.technical_validation_worker import (
    process_validation_run,
)


class ValidationJobLauncher:
    """Ein aktiver Prüf-Thread pro Projekt; Status bleibt in der Registry-DB."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def is_thread_alive(self, project_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(project_id)
            return thread is not None and thread.is_alive()

    def is_active(self, project_id: str) -> bool:
        """True, wenn für dieses Projekt ein Worker (Thread/Sync) aktiv ist."""
        return self.is_thread_alive(project_id)

    def launch(
        self,
        *,
        project_id: str,
        project_root: Path,
        run_id: str,
        sync: bool = False,
    ) -> bool:
        """Startet die Verarbeitung. Returns False, wenn bereits ein Thread läuft."""
        if sync:
            with self._lock:
                existing = self._threads.get(project_id)
                if existing is not None and existing.is_alive():
                    return False
                # Sync-Lauf im aktuellen Thread markieren (Recovery erkennt ihn).
                self._threads[project_id] = threading.current_thread()
            try:
                process_validation_run(project_root, run_id)
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
                args=(project_id, project_root, run_id),
                name=f"discovery-v2-validation-{run_id[:8]}",
                daemon=True,
            )
            self._threads[project_id] = thread
            thread.start()
            return True

    def _run_in_thread(
        self, project_id: str, project_root: Path, run_id: str
    ) -> None:
        try:
            process_validation_run(project_root, run_id)
        finally:
            with self._lock:
                current = self._threads.get(project_id)
                if current is threading.current_thread():
                    self._threads.pop(project_id, None)


_LAUNCHER: ValidationJobLauncher | None = None
_LAUNCHER_LOCK = threading.Lock()


def get_validation_job_launcher() -> ValidationJobLauncher:
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = ValidationJobLauncher()
        return _LAUNCHER


def reset_validation_job_launcher_for_tests() -> None:
    """Test-Hilfe: Singleton zurücksetzen."""
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        _LAUNCHER = None
