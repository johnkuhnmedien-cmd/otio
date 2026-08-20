"""Tests für Job-Registry und hängende Jobs."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from otio_app.services.asset_analysis_job import JobStatus, get_asset_analysis_job_manager
from otio_app.services import job_registry


def test_reconcile_stuck_job_marks_dead_thread_failed() -> None:
    manager = get_asset_analysis_job_manager()
    project_id = "stuck-test-project"

    with manager._lock:  # noqa: SLF001 — Test-Hook
        manager._jobs[project_id] = manager._jobs.get(project_id) or type(  # noqa: SLF001
            "S", (), {}
        )()
        from otio_app.services.asset_analysis_job import AssetAnalysisJobState

        manager._jobs[project_id] = AssetAnalysisJobState(  # noqa: SLF001
            project_id=project_id,
            status=JobStatus.RUNNING,
            folders=["Test"],
            model="test",
        )
        manager._threads[project_id] = threading.Thread(  # noqa: SLF001
            target=lambda: time.sleep(0.01),
            daemon=True,
        )
        manager._threads[project_id].start()
        manager._threads[project_id].join()

    manager.reconcile_stuck_job(project_id)
    state = manager.get_state(project_id)
    assert state is not None
    assert state.status == JobStatus.FAILED

    with manager._lock:  # noqa: SLF001
        manager._jobs.pop(project_id, None)
        manager._threads.pop(project_id, None)


def test_reconcile_all_jobs_runs_once_per_ui_script_run(
    monkeypatch,
) -> None:
    session: dict[str, object] = {}
    fake_st = MagicMock()
    fake_st.session_state = session
    monkeypatch.setattr(job_registry, "list_projects", lambda: [])
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)

    calls = {"n": 0}
    real_clean = job_registry.get_clean_media_job_manager

    def counting_clean():
        calls["n"] += 1
        return real_clean()

    monkeypatch.setattr(job_registry, "get_clean_media_job_manager", counting_clean)

    job_registry.begin_ui_script_run()
    job_registry.reconcile_all_jobs()
    job_registry.reconcile_all_jobs()
    assert calls["n"] == 1

    job_registry.begin_ui_script_run()
    job_registry.reconcile_all_jobs()
    assert calls["n"] == 2


def test_any_job_running_can_skip_reconcile(monkeypatch) -> None:
    reconciled = {"n": 0}

    def fake_reconcile() -> None:
        reconciled["n"] += 1

    monkeypatch.setattr(job_registry, "reconcile_all_jobs", fake_reconcile)
    monkeypatch.setattr(job_registry, "list_projects", lambda: [])
    idle = MagicMock()
    idle.is_running.return_value = False
    idle.any_running.return_value = False
    monkeypatch.setattr(job_registry, "get_clean_media_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_voice_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_asset_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_otio_export_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_supplement_funnel_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_enhanced_auto_run_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_language_auto_run_queue_manager", lambda: idle)

    assert job_registry.any_job_running("proj", reconcile=False) is False
    assert reconciled["n"] == 0
    job_registry.any_job_running("proj")
    assert reconciled["n"] == 1


def test_reconcile_all_jobs_skips_maps_for_classic_projects(monkeypatch) -> None:
    classic = MagicMock(id="classic", is_without_voiceover_enhanced=False)
    monkeypatch.setattr(job_registry, "list_projects", lambda: [classic], raising=False)
    idle = MagicMock()
    idle.reconcile_stuck_job = MagicMock()
    idle._jobs = {}
    idle._states = {}
    idle._lock = None
    map_manager = MagicMock()
    map_manager._jobs = {}
    map_manager._states = {}
    map_manager._lock = None
    monkeypatch.setattr(job_registry, "get_clean_media_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_voice_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_asset_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_otio_export_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_supplement_funnel_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_enhanced_auto_run_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_language_auto_run_queue_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_map_render_job_manager", lambda: map_manager)
    monkeypatch.setitem(__import__("sys").modules, "streamlit", MagicMock(session_state={}))

    job_registry.begin_ui_script_run()
    job_registry.reconcile_all_jobs()
    map_manager.reconcile_stuck_job.assert_not_called()


def test_reconcile_all_jobs_does_not_list_projects(monkeypatch) -> None:
    monkeypatch.setattr(
        job_registry,
        "list_projects",
        lambda: (_ for _ in ()).throw(AssertionError("list_projects")),
        raising=False,
    )
    fake_st = MagicMock()
    fake_st.session_state = {}
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)
    job_registry.begin_ui_script_run()
    job_registry.reconcile_all_jobs()


def test_collect_job_activity_skips_map_state_without_in_memory_job(
    monkeypatch,
) -> None:
    map_manager = MagicMock()
    map_manager._jobs = {}
    map_manager._states = {}
    map_manager._lock = None
    idle = MagicMock()
    idle._jobs = {}
    idle._states = {}
    idle._lock = None
    monkeypatch.setattr(job_registry, "get_clean_media_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_voice_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_asset_analysis_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_otio_export_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_supplement_funnel_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_enhanced_auto_run_job_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_language_auto_run_queue_manager", lambda: idle)
    monkeypatch.setattr(job_registry, "get_map_render_job_manager", lambda: map_manager)
    monkeypatch.setitem(__import__("sys").modules, "streamlit", MagicMock(session_state={}))
    job_registry.begin_ui_script_run()
    activities = job_registry.collect_job_activity()
    map_manager.get_state.assert_not_called()
    assert activities == []

