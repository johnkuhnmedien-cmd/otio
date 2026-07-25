"""E2: AppTest über echte Import-Seite → JobManager.start → download_research_import."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = Path(__file__).parent / "_apptest_scripts" / "adobe_e2_route_import_smoke.py"
TINY = Path(__file__).parent / "fixtures" / "adobe_tiny_valid.mp4"


@pytest.mark.skipif(not TINY.is_file(), reason="video fixture missing")
def test_apptest_route_start_runs_real_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "e2-apptest"
    monkeypatch.setenv("ADOBE_E2_SMOKE_ROOT", str(root))

    # Frischer Job-Manager (kein Restzustand / kein Result-Seed).
    import otio_app.services.adobe_research_import_job as job_mod

    state_dir = root / "state" / "e2-route-smoke"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(job_mod, "project_dir", lambda pid: state_dir)
    job_mod._MANAGER = job_mod.ResearchImportJobManager()  # noqa: SLF001

    at = AppTest.from_file(str(SCRIPT), default_timeout=20)
    at.run()
    assert not at.exception, at.exception

    from otio_app.services.adobe_research_import_job import (
        JobStatus,
        get_research_import_job_manager,
    )

    mgr = get_research_import_job_manager()
    before = mgr.get_state("e2-route-smoke")
    assert before.result is None
    assert before.status == JobStatus.IDLE

    start_buttons = [
        b
        for b in at.button
        if "Lizenzieren" in (b.label or "") or "herunterladen" in (b.label or "")
    ]
    assert start_buttons, f"Start-Button fehlt: {[b.label for b in at.button]}"
    # Klick startet mgr.start → Worker-Thread mit download_research_import.
    # Kein erneutes at.run() während RUNNING: die Seite pollt mit time.sleep+rerun.
    start_buttons[0].click().run(timeout=20)
    assert not at.exception, at.exception

    deadline = time.time() + 30
    final = None
    while time.time() < deadline:
        final = mgr.get_state("e2-route-smoke")
        if final.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            break
        time.sleep(0.05)

    assert final is not None
    assert final.status == JobStatus.COMPLETED, final.error or final.message
    assert final.result is not None
    assert final.result.downloaded >= 1
    assert final.result.diagnostics["request_counters"]["license_history"] == 0
    assert list((root / "media").rglob("*.mp4")), "keine lokale MP4 nach Job"
