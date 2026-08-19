"""Sequenzielle Auto-Lauf-Queue über offene Sprachen."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, find_projects_by_root
from otio_app.services.language_auto_run_queue import (
    LanguageAutoRunQueueBusyError,
    LanguageAutoRunQueueJobManager,
    reset_language_auto_run_queue_manager_for_tests,
)
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    resolve_sibling_project,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)


class _FakeAuto:
    def __init__(self, *, delay: float = 0.04, fail_first: bool = False) -> None:
        self.delay = delay
        self.fail_first = fail_first
        self.started: list[str] = []
        self.running: set[str] = set()
        self.states: dict[str, SimpleNamespace] = {}
        self.max_running = 0
        self.cancelled: list[str] = []
        self._lock = threading.Lock()
        self.started_event = threading.Event()
        self.stop_afters: list[str] = []

    def any_running(self) -> bool:
        with self._lock:
            return bool(self.running)

    def is_running(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self.running

    def reconcile_stuck_job(self, project_id: str) -> None:
        return None

    def get_state(self, project_id: str) -> SimpleNamespace | None:
        with self._lock:
            return self.states.get(project_id)

    def request_cancel(self, project_id: str) -> bool:
        self.cancelled.append(project_id)
        self._finish(project_id, "cancelled", error="gestoppt")
        return True

    def start(self, project, *, stop_after: str = "youtube", **_kwargs) -> bool:
        pid = project.id
        self.stop_afters.append(stop_after)
        with self._lock:
            if self.running:
                raise AssertionError(
                    f"paralleler Auto-Lauf: {self.running} vs {pid}"
                )
            self.running.add(pid)
            self.max_running = max(self.max_running, len(self.running))
            self.started.append(pid)
            self.states[pid] = SimpleNamespace(
                status="running", error=None, message="läuft"
            )
        self.started_event.set()
        if self.fail_first and len(self.started) == 1:
            self._finish(pid, "failed", error="Keine ElevenLabs Voice-ID konfiguriert")
            return True

        def _done() -> None:
            time.sleep(self.delay)
            with self._lock:
                still = pid in self.running
            if still:
                self._finish(pid, "completed")

        threading.Thread(target=_done, daemon=True).start()
        return True

    def _finish(self, pid: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self.running.discard(pid)
            self.states[pid] = SimpleNamespace(
                status=status, error=error, message=error or status
            )


def _wait_queue(manager: LanguageAutoRunQueueJobManager, source_id: str):
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not manager.is_running(source_id):
            return manager.get_state(source_id)
        time.sleep(0.01)
    raise AssertionError(manager.get_state(source_id))


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        id="de-source",
        name="DE_Test Automatic",
        language="de",
        video_place="Griechenland",
        is_without_voiceover_enhanced=True,
    )


def _manager(fake: _FakeAuto, siblings: dict[str, SimpleNamespace], source):
    return LanguageAutoRunQueueJobManager(
        poll_interval_s=0.01,
        auto_run_manager_factory=lambda: fake,
        load_project=lambda pid: source if pid == source.id else None,
        resolve_sibling=lambda _src, lang: siblings[normalize_brief_language(lang)],
    )


@pytest.fixture(autouse=True)
def reset_queue_singleton() -> None:
    reset_language_auto_run_queue_manager_for_tests()
    yield
    reset_language_auto_run_queue_manager_for_tests()


@pytest.fixture
def patch_incomplete(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "otio_app.services.language_auto_run_queue.auto_run_pipeline_complete",
        lambda _project, **_kwargs: False,
    )


def test_queue_runs_languages_sequentially_never_overlap(patch_incomplete) -> None:
    source = _source()
    siblings = {
        "EN": SimpleNamespace(id="en-proj", name="EN_Test", language="en"),
        "PT": SimpleNamespace(id="pt-proj", name="PT_Test", language="pt"),
    }
    fake = _FakeAuto(delay=0.05)
    manager = _manager(fake, siblings, source)
    manager.start(source, ["EN", "PT"])
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert fake.started == ["en-proj", "pt-proj"]
    assert fake.max_running == 1
    assert state.completed_languages == ["EN", "PT"]


def test_queue_continues_after_failed_language(patch_incomplete) -> None:
    source = _source()
    siblings = {
        "EN": SimpleNamespace(id="en-proj", name="EN_Test", language="en"),
        "PT": SimpleNamespace(id="pt-proj", name="PT_Test", language="pt"),
    }
    fake = _FakeAuto(fail_first=True)
    manager = _manager(fake, siblings, source)
    manager.start(source, ["EN", "PT"])
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert state.failed_languages == ["EN"]
    assert state.completed_languages == ["PT"]
    assert "ElevenLabs" in (state.error or "")
    assert "Weiter nach Fehler" in (state.error or "")
    assert fake.started == ["en-proj", "pt-proj"]
    assert fake.max_running == 1


def test_queue_cancel_does_not_start_next(patch_incomplete) -> None:
    source = _source()
    siblings = {
        "EN": SimpleNamespace(id="en-proj", name="EN_Test", language="en"),
        "FR": SimpleNamespace(id="fr-proj", name="FR_Test", language="fr"),
    }
    fake = _FakeAuto(delay=2.0)
    manager = _manager(fake, siblings, source)
    manager.start(source, ["EN", "FR"])
    assert fake.started_event.wait(timeout=1.0)
    assert fake.started == ["en-proj"]
    assert manager.request_cancel(source.id) is True
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "cancelled"
    assert fake.started == ["en-proj"]
    assert "en-proj" in fake.cancelled


def test_queue_busy_if_auto_run_already_running(patch_incomplete) -> None:
    source = _source()
    fake = _FakeAuto()
    fake.running.add("other")
    manager = LanguageAutoRunQueueJobManager(
        auto_run_manager_factory=lambda: fake,
        load_project=lambda _pid: source,
        resolve_sibling=lambda *_a, **_k: source,
    )
    with pytest.raises(LanguageAutoRunQueueBusyError, match="Auto-Lauf läuft bereits"):
        manager.start(source, ["EN"])


def test_queue_requires_video_place() -> None:
    source = _source()
    source.video_place = ""
    manager = LanguageAutoRunQueueJobManager(
        auto_run_manager_factory=lambda: _FakeAuto(),
    )
    with pytest.raises(LanguageSiblingError, match="Land/Region"):
        manager.start(source, ["EN"])


def test_queue_clones_remaining_languages_after_failure(
    temp_project_layout,
    temp_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "otio_app.services.language_auto_run_queue.auto_run_pipeline_complete",
        lambda _project, **_kwargs: False,
    )
    work = temp_project_layout["project_root"] / "_otio_enhanced"
    source = create_project(
        ProjectCreate(
            name="DE_Test Automatic",
            project_root=str(temp_project_layout["project_root"]),
            work_dir=str(work),
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            language="de",
            video_place="Griechenland",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    fake = _FakeAuto(fail_first=True)
    manager = LanguageAutoRunQueueJobManager(
        poll_interval_s=0.01,
        auto_run_manager_factory=lambda: fake,
        load_project=lambda pid: source if pid == source.id else None,
        resolve_sibling=lambda src, lang: resolve_sibling_project(
            src, lang, db_path=temp_db_path
        ),
    )
    manager.start(source, ["EN", "PT"])
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert state.failed_languages == ["EN"]
    assert "PT" in state.completed_languages
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    langs = {item.language for item in siblings}
    assert langs == {"de", "en", "pt"}


def test_queue_skips_already_complete_language(monkeypatch) -> None:
    source = _source()
    siblings = {
        "EN": SimpleNamespace(id="en-proj", name="EN_Test", language="en"),
        "PT": SimpleNamespace(id="pt-proj", name="PT_Test", language="pt"),
    }

    def fake_complete(project, **_kwargs) -> bool:
        return project.id == "en-proj"

    monkeypatch.setattr(
        "otio_app.services.language_auto_run_queue.auto_run_pipeline_complete",
        fake_complete,
    )
    fake = _FakeAuto(delay=0.02)
    manager = _manager(fake, siblings, source)
    manager.start(source, ["EN", "PT"])
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert fake.started == ["pt-proj"]
    assert state.completed_languages == ["EN", "PT"]


def test_queue_rejects_empty_languages() -> None:
    source = _source()
    manager = LanguageAutoRunQueueJobManager(
        auto_run_manager_factory=lambda: _FakeAuto(),
        load_project=lambda _pid: source,
    )
    with pytest.raises(ValueError, match="Keine Sprachen"):
        manager.start(source, [])


def test_queue_runs_source_language_when_selected(patch_incomplete) -> None:
    source = _source()
    fake = _FakeAuto()
    manager = LanguageAutoRunQueueJobManager(
        poll_interval_s=0.01,
        auto_run_manager_factory=lambda: fake,
        load_project=lambda pid: source if pid == source.id else None,
        resolve_sibling=lambda src, lang: src
        if normalize_brief_language(lang) == "DE"
        else siblings_unused(lang),
    )
    manager.start(source, ["DE"])
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert fake.started == ["de-source"]
    assert fake.stop_afters == ["youtube"]
    assert state.completed_languages == ["DE"]


def siblings_unused(lang: str):
    raise AssertionError(f"unerwartete Sprache {lang}")


def test_queue_passes_funnel_stop_after(patch_incomplete) -> None:
    source = _source()
    siblings = {
        "EN": SimpleNamespace(id="en-proj", name="EN_Test", language="en"),
    }
    fake = _FakeAuto()
    manager = _manager(fake, siblings, source)
    manager.start(source, ["EN"], stop_after="funnel")
    state = _wait_queue(manager, source.id)
    assert state is not None
    assert state.status == "completed"
    assert state.stop_after == "funnel"
    assert fake.stop_afters == ["funnel"]


def test_job_registry_and_auto_run_ui_include_queue() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    registry = (root / "otio_app" / "services" / "job_registry.py").read_text(
        encoding="utf-8"
    )
    assert "get_language_auto_run_queue_manager" in registry
    assert "Sprachen-Queue" in registry
    ui = (
        root
        / "otio_app"
        / "ui"
        / "without_voiceover_enhanced"
        / "auto_run_ui.py"
    ).read_text(encoding="utf-8")
    assert "get_language_auto_run_queue_manager" in ui

