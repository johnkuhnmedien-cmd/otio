"""Tests für Job-Registry und hängende Jobs."""

from __future__ import annotations

import threading
import time

from otio_app.services.asset_analysis_job import JobStatus, get_asset_analysis_job_manager


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
