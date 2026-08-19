"""Hintergrund-Job der Bestandsaufnahme: Fortschritt, Abschluss, Stop."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from otio_app.services.supplement_recovery import SupplementRecoveryReport
from otio_app.services.supplement_recovery_job import (
    RecoveryJobStatus,
    SupplementRecoveryJobManager,
)
from tests.test_supplement_recovery import _project  # Layout-Helfer wiederverwenden


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def manager() -> SupplementRecoveryJobManager:
    return SupplementRecoveryJobManager()


def _patch_recovery(monkeypatch: pytest.MonkeyPatch, behaviour):
    monkeypatch.setattr(
        "otio_app.services.supplement_recovery_job.recover_supplements_into_inventory",
        behaviour,
    )


def test_job_reports_progress_and_completes(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")
    released = {"go": False}

    def fake_recovery(_project, **kwargs):
        on_progress = kwargs["on_progress"]
        on_progress("start", {"total": 3})
        for index in (1, 2, 3):
            on_progress(
                "item_start",
                {"index": index, "total": 3, "media_name": f"a{index}.mp4", "folder": "F"},
            )
            while not released["go"] and index == 1:
                time.sleep(0.01)
            on_progress("item_done", {"index": index, "total": 3})
        on_progress("complete", {"total": 3})
        return SupplementRecoveryReport(scanned=3, pending=3, recovered=3, analyzed=3)

    _patch_recovery(monkeypatch, fake_recovery)

    def _total_known() -> bool:
        state = manager.get_state(project.id)
        return state is not None and state.total == 3

    assert manager.start(project) is True
    assert _wait_until(_total_known)
    running = manager.get_state(project.id)
    assert running.status == RecoveryJobStatus.RUNNING
    assert running.current_media == "a1.mp4"
    assert running.current_folder == "F"
    assert running.fraction == 0.0

    released["go"] = True
    assert _wait_until(lambda: not manager.is_running(project.id))

    done = manager.get_state(project.id)
    assert done.status == RecoveryJobStatus.COMPLETED
    assert done.done == 3
    assert done.fraction == 1.0
    assert done.report.recovered == 3


def test_second_start_is_refused_while_running(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")
    release = {"go": False}

    def fake_recovery(_project, **kwargs):
        kwargs["on_progress"]("start", {"total": 1})
        while not release["go"]:
            time.sleep(0.01)
        return SupplementRecoveryReport()

    _patch_recovery(monkeypatch, fake_recovery)

    assert manager.start(project) is True
    assert _wait_until(lambda: manager.is_running(project.id))
    assert manager.start(project) is False

    release["go"] = True
    assert _wait_until(lambda: not manager.is_running(project.id))


def test_cancel_is_passed_to_the_recovery(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")
    observed: dict[str, bool] = {}

    def fake_recovery(_project, **kwargs):
        should_cancel = kwargs["should_cancel"]
        kwargs["on_progress"]("start", {"total": 2})
        while not should_cancel():
            time.sleep(0.01)
        observed["cancelled"] = True
        return SupplementRecoveryReport(pending=2, recovered=1, cancelled=True)

    _patch_recovery(monkeypatch, fake_recovery)
    assert manager.start(project) is True
    assert _wait_until(lambda: manager.is_running(project.id))

    assert manager.request_cancel(project.id) is True
    assert _wait_until(lambda: not manager.is_running(project.id))

    assert observed.get("cancelled") is True
    state = manager.get_state(project.id)
    assert state.status == RecoveryJobStatus.CANCELLED
    assert state.report.recovered == 1


def test_failure_is_reported_not_raised(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")

    def fake_recovery(_project, **kwargs):
        raise RuntimeError("Netzwerk weg")

    _patch_recovery(monkeypatch, fake_recovery)
    assert manager.start(project) is True
    assert _wait_until(lambda: not manager.is_running(project.id))

    state = manager.get_state(project.id)
    assert state.status == RecoveryJobStatus.FAILED
    assert "Netzwerk weg" in (state.error or "")


def test_dismiss_clears_finished_job_only(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")
    _patch_recovery(
        monkeypatch, lambda _project, **kwargs: SupplementRecoveryReport(recovered=1)
    )
    manager.start(project)
    assert _wait_until(lambda: not manager.is_running(project.id))

    manager.dismiss(project.id)
    assert manager.get_state(project.id) is None


def test_limit_and_folders_reach_the_recovery(tmp_path, manager, monkeypatch):
    project = _project(tmp_path, "de")
    captured: dict[str, object] = {}

    def fake_recovery(_project, **kwargs):
        captured.update(kwargs)
        return SupplementRecoveryReport()

    _patch_recovery(monkeypatch, fake_recovery)
    manager.start(project, folder_names=["Cliffs of Moher"], model="gemini-x", limit=25)
    assert _wait_until(lambda: not manager.is_running(project.id))

    assert captured["folder_names"] == ["Cliffs of Moher"]
    assert captured["model"] == "gemini-x"
    assert captured["limit"] == 25
