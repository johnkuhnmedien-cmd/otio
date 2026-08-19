"""Sequenzieller Auto-Lauf für alle offenen Sprachen eines Projekts."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.models import Project
from otio_app.project_repository import get_project_by_id
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    auto_run_pipeline_complete,
    open_languages_for_auto_run,
    resolve_sibling_project,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AUTO_RUN_STOP_AFTER_YOUTUBE,
    normalize_auto_run_stop_after,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_value(status: object) -> str:
    if hasattr(status, "value"):
        return str(getattr(status, "value"))
    return str(status)


@dataclass
class LanguageAutoRunQueueState:
    source_project_id: str
    languages: list[str]
    current_language: str | None = None
    current_project_id: str | None = None
    current_index: int = 0
    completed_languages: list[str] = field(default_factory=list)
    failed_languages: list[str] = field(default_factory=list)
    failed_language: str | None = None
    language_errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status: str = "idle"
    cancel_requested: bool = False
    stop_after: str = AUTO_RUN_STOP_AFTER_YOUTUBE


class LanguageAutoRunQueueBusyError(RuntimeError):
    """Queue oder ein Auto-Lauf läuft bereits."""


class LanguageAutoRunQueueJobManager:
    def __init__(
        self,
        *,
        poll_interval_s: float = _POLL_INTERVAL_S,
        auto_run_manager_factory: Callable[[], object] | None = None,
        wait_sleep: Callable[[float], None] = time.sleep,
        load_project: Callable[[str], Project | None] | None = None,
        resolve_sibling: Callable[[Project, str], Project] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, LanguageAutoRunQueueState] = {}
        self._cancel: dict[str, bool] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._poll_interval_s = poll_interval_s
        self._auto_run_manager_factory = auto_run_manager_factory
        self._wait_sleep = wait_sleep
        self._load_project = load_project or get_project_by_id
        self._resolve_sibling = resolve_sibling or resolve_sibling_project

    def _auto_manager(self) -> object:
        if self._auto_run_manager_factory is not None:
            return self._auto_run_manager_factory()
        from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
            get_enhanced_auto_run_job_manager,
        )

        return get_enhanced_auto_run_job_manager()

    def start(
        self,
        source: Project,
        languages: list[str] | None = None,
        *,
        stop_after: str = AUTO_RUN_STOP_AFTER_YOUTUBE,
    ) -> LanguageAutoRunQueueState:
        if not source.is_without_voiceover_enhanced:
            raise LanguageSiblingError(
                "Auto-Lauf gibt es nur für Enhanced-MVP-Projekte."
            )
        if not str(source.video_place or "").strip():
            raise LanguageSiblingError(
                "Kein Land/Region am Projekt — zuerst unter Gespeicherte Projekte eintragen."
            )
        source_id = str(source.id)
        stop_after = normalize_auto_run_stop_after(stop_after)
        if languages is None:
            languages = open_languages_for_auto_run(
                source,
                stop_after=stop_after,
                include_current=True,
            )
        languages = [
            normalize_brief_language(lang)
            for lang in languages
            if str(lang).strip()
        ]
        if not languages:
            raise ValueError("Keine Sprachen für den sequenziellen Auto-Lauf.")

        auto_manager = self._auto_manager()
        if _manager_any_running(auto_manager):
            raise LanguageAutoRunQueueBusyError(
                "Ein Auto-Lauf läuft bereits. Warte, bis er fertig ist."
            )
        with self._lock:
            if self._any_running_locked():
                raise LanguageAutoRunQueueBusyError(
                    "Die Sprachen-Queue läuft bereits. Warte, bis sie fertig ist."
                )
            if _manager_any_running(auto_manager):
                raise LanguageAutoRunQueueBusyError(
                    "Ein Auto-Lauf läuft bereits. Warte, bis er fertig ist."
                )
            state = LanguageAutoRunQueueState(
                source_project_id=source_id,
                languages=languages,
                current_language=languages[0],
                current_index=0,
                started_at=_now_iso(),
                status="running",
                stop_after=stop_after,
            )
            self._states[source_id] = state
            self._cancel[source_id] = False
            thread = threading.Thread(
                target=self._run_queue,
                args=(source_id,),
                name=f"language-auto-run-queue-{source_id[:8]}",
                daemon=True,
            )
            self._threads[source_id] = thread
        logger.info(
            "Sprachen-Queue gestartet für %s: %s",
            source.name,
            ", ".join(languages),
        )
        thread.start()
        return state

    def request_cancel(self, source_project_id: str) -> bool:
        source_id = str(source_project_id)
        current_project_id: str | None = None
        with self._lock:
            state = self._states.get(source_id)
            if state is None or state.status != "running":
                return False
            self._cancel[source_id] = True
            state.cancel_requested = True
            state.error = "Sprachen-Queue wird gestoppt …"
            current_project_id = state.current_project_id
        if current_project_id:
            _cancel_auto_run(self._auto_manager(), current_project_id)
        return True

    def is_running(self, source_project_id: str) -> bool:
        self.reconcile_stuck_job(str(source_project_id))
        with self._lock:
            state = self._states.get(str(source_project_id))
            return state is not None and state.status == "running"

    def any_running(self) -> bool:
        with self._lock:
            ids = [
                pid
                for pid, state in self._states.items()
                if state.status == "running"
            ]
        return any(self.is_running(pid) for pid in ids)

    def _any_running_locked(self) -> bool:
        return any(state.status == "running" for state in self._states.values())

    def get_state(self, source_project_id: str) -> LanguageAutoRunQueueState | None:
        self.reconcile_stuck_job(str(source_project_id))
        with self._lock:
            state = self._states.get(str(source_project_id))
            if state is None:
                return None
            return LanguageAutoRunQueueState(
                source_project_id=state.source_project_id,
                languages=list(state.languages),
                current_language=state.current_language,
                current_project_id=state.current_project_id,
                current_index=state.current_index,
                completed_languages=list(state.completed_languages),
                failed_languages=list(state.failed_languages),
                failed_language=state.failed_language,
                language_errors=dict(state.language_errors),
                error=state.error,
                started_at=state.started_at,
                finished_at=state.finished_at,
                status=state.status,
                cancel_requested=state.cancel_requested,
                stop_after=state.stop_after,
            )

    def get_state_for_projects(
        self,
        projects: Sequence[Project],
    ) -> LanguageAutoRunQueueState | None:
        """Queue-Stand für eine Familie — laufend vor abgeschlossen."""
        fallback: LanguageAutoRunQueueState | None = None
        for project in projects:
            state = self.get_state(str(project.id))
            if state is None:
                continue
            if state.status == "running":
                return state
            if fallback is None:
                fallback = state
        return fallback

    def thread_alive(self, source_project_id: str) -> bool | None:
        with self._lock:
            thread = self._threads.get(str(source_project_id))
            if thread is None:
                return None
            return thread.is_alive()

    def dismiss(self, source_project_id: str) -> None:
        with self._lock:
            state = self._states.get(str(source_project_id))
            if state is not None and state.status != "running":
                self._states.pop(str(source_project_id), None)
                self._cancel.pop(str(source_project_id), None)
                self._threads.pop(str(source_project_id), None)

    def force_reset_all(self) -> int:
        auto_manager = self._auto_manager()
        with self._lock:
            running_ids = [
                pid
                for pid, state in self._states.items()
                if state.status == "running"
            ]
            current_project_ids = [
                state.current_project_id
                for state in self._states.values()
                if state.current_project_id
            ]
            now = _now_iso()
            for pid in running_ids:
                self._cancel[pid] = True
                state = self._states[pid]
                state.status = "cancelled"
                state.cancel_requested = True
                state.error = "Sprachen-Queue wurde zurückgesetzt."
                state.finished_at = now
            self._threads.clear()
        for pid in current_project_ids:
            if pid:
                _reset_auto_run(auto_manager, pid)
        return len(running_ids)

    def reconcile_stuck_job(self, source_project_id: str) -> bool:
        source_id = str(source_project_id)
        with self._lock:
            state = self._states.get(source_id)
            if state is None or state.status != "running":
                return False
            thread = self._threads.get(source_id)
            if thread is not None and thread.is_alive():
                return False
            state.status = "failed"
            state.error = (
                "Die Sprachen-Queue ist abgebrochen "
                "(Hintergrundprozess nicht mehr aktiv)."
            )
            state.finished_at = _now_iso()
            self._threads.pop(source_id, None)
            return True

    def _run_queue(self, source_id: str) -> None:
        auto_manager = self._auto_manager()
        try:
            source = self._load_project(source_id)
            if source is None:
                self._fail(source_id, "Quellprojekt nicht mehr gefunden.")
                return
            with self._lock:
                languages = list(self._states[source_id].languages)
                stop_after = self._states[source_id].stop_after
            for index, language in enumerate(languages):
                if self._is_cancelled(source_id):
                    self._mark_cancelled(source_id)
                    return
                with self._lock:
                    state = self._states[source_id]
                    state.current_index = index
                    state.current_language = language
                    state.current_project_id = None
                    state.error = None
                try:
                    sibling = self._resolve_sibling(source, language)
                except Exception as exc:  # noqa: BLE001 — eine Sprache, Queue weiter
                    logger.exception(
                        "Sprachen-Queue: Anlegen von %s fehlgeschlagen", language
                    )
                    self._note_language_failure(source_id, language, str(exc))
                    continue
                with self._lock:
                    self._states[source_id].current_project_id = sibling.id
                if _pipeline_complete(sibling, stop_after):
                    with self._lock:
                        self._states[source_id].completed_languages.append(language)
                    continue
                if self._is_cancelled(source_id):
                    self._mark_cancelled(source_id)
                    return
                try:
                    started = _start_auto_run(auto_manager, sibling, stop_after)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Sprachen-Queue: Auto-Lauf für %s konnte nicht starten",
                        sibling.name,
                    )
                    self._note_language_failure(source_id, language, str(exc))
                    continue
                if started is False:
                    self._note_language_failure(
                        source_id,
                        language,
                        f"Auto-Lauf für {language} läuft bereits oder konnte nicht starten.",
                    )
                    continue
                if not self._wait_for_auto_run(source_id, sibling.id):
                    return
                auto_state = auto_manager.get_state(sibling.id)
                auto_status = (
                    _status_value(auto_state.status)
                    if auto_state is not None
                    else "failed"
                )
                if auto_status != "completed":
                    error = None
                    if auto_state is not None:
                        error = getattr(auto_state, "error", None) or getattr(
                            auto_state, "message", None
                        )
                    error = error or (
                        f"Auto-Lauf für {language} ist nicht durchgelaufen ({auto_status})."
                    )
                    if auto_status == "cancelled" or self._is_cancelled(source_id):
                        self._mark_cancelled(source_id, error=str(error))
                        return
                    self._note_language_failure(source_id, language, str(error))
                    continue
                with self._lock:
                    self._states[source_id].completed_languages.append(language)
            self._mark_completed(source_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sprachen-Queue unerwartet abgebrochen")
            self._fail(source_id, str(exc))

    def _wait_for_auto_run(self, source_id: str, sibling_id: str) -> bool:
        auto_manager = self._auto_manager()
        while True:
            if self._is_cancelled(source_id):
                _cancel_auto_run(auto_manager, sibling_id)
                self._mark_cancelled(source_id)
                return False
            if hasattr(auto_manager, "reconcile_stuck_job"):
                auto_manager.reconcile_stuck_job(sibling_id)
            if not auto_manager.is_running(sibling_id):
                return True
            self._wait_sleep(self._poll_interval_s)

    def _is_cancelled(self, source_id: str) -> bool:
        with self._lock:
            return bool(self._cancel.get(source_id))

    def _note_language_failure(
        self,
        source_id: str,
        language: str,
        error: str,
    ) -> None:
        with self._lock:
            state = self._states.get(source_id)
            if state is None:
                return
            if language not in state.failed_languages:
                state.failed_languages.append(language)
            state.failed_language = language
            state.language_errors[language] = error
            logger.warning(
                "Sprachen-Queue: %s fehlgeschlagen, nächste Sprache: %s",
                language,
                error,
            )

    def _fail(
        self,
        source_id: str,
        error: str,
        *,
        failed_language: str | None = None,
    ) -> None:
        with self._lock:
            state = self._states.get(source_id)
            if state is None:
                return
            state.status = "failed"
            state.error = error
            state.failed_language = failed_language
            state.finished_at = _now_iso()
            self._threads.pop(source_id, None)

    def _mark_cancelled(self, source_id: str, *, error: str | None = None) -> None:
        with self._lock:
            state = self._states.get(source_id)
            if state is None:
                return
            state.status = "cancelled"
            state.cancel_requested = True
            state.error = error or "Sprachen-Queue gestoppt."
            state.finished_at = _now_iso()
            self._threads.pop(source_id, None)

    def _mark_completed(self, source_id: str) -> None:
        with self._lock:
            state = self._states.get(source_id)
            if state is None:
                return
            state.status = "completed"
            state.current_language = None
            state.current_project_id = None
            if state.failed_languages:
                parts = [
                    f"{lang}: {state.language_errors.get(lang) or 'fehlgeschlagen'}"
                    for lang in state.failed_languages
                ]
                state.error = "Weiter nach Fehler. " + " · ".join(parts)
            else:
                state.error = None
            state.finished_at = _now_iso()
            self._threads.pop(source_id, None)


def _pipeline_complete(project: Project, stop_after: str) -> bool:
    try:
        return bool(auto_run_pipeline_complete(project, stop_after=stop_after))
    except TypeError:
        return bool(auto_run_pipeline_complete(project))


def _start_auto_run(manager: object, project: Project, stop_after: str) -> bool:
    start = getattr(manager, "start")
    try:
        return bool(start(project, stop_after=stop_after))
    except TypeError:
        return bool(start(project))


def _manager_any_running(manager: object) -> bool:
    any_running = getattr(manager, "any_running", None)
    if callable(any_running):
        return bool(any_running())
    return False


def _cancel_auto_run(manager: object, project_id: str) -> None:
    request_cancel = getattr(manager, "request_cancel", None)
    if callable(request_cancel):
        try:
            request_cancel(project_id)
            return
        except Exception:  # noqa: BLE001
            logger.exception("Auto-Lauf-Stop für %s fehlgeschlagen", project_id)
    cancel = getattr(manager, "cancel", None)
    if callable(cancel):
        try:
            cancel(project_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-Lauf-Stop für %s fehlgeschlagen", project_id)


def _reset_auto_run(manager: object, project_id: str) -> None:
    force_reset = getattr(manager, "force_reset", None)
    if callable(force_reset):
        try:
            force_reset(project_id)
            return
        except Exception:  # noqa: BLE001
            logger.exception("Auto-Lauf-Reset für %s fehlgeschlagen", project_id)
    _cancel_auto_run(manager, project_id)


_queue_manager: LanguageAutoRunQueueJobManager | None = None
_queue_manager_lock = threading.Lock()


def get_language_auto_run_queue_manager() -> LanguageAutoRunQueueJobManager:
    global _queue_manager
    with _queue_manager_lock:
        if _queue_manager is None:
            _queue_manager = LanguageAutoRunQueueJobManager()
        return _queue_manager


def reset_language_auto_run_queue_manager_for_tests() -> None:
    global _queue_manager
    with _queue_manager_lock:
        _queue_manager = None
