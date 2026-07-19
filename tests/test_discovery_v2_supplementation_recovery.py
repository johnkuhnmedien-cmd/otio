"""Phase 10 supplementation recovery tests."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.supplementation_job_recovery import (
    reconcile_orphaned_supplementation_run,
)
from otio_app.discovery_v2.domain.supplementation import (
    SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED,
    SUPPLEMENTATION_RUN_SCOPE_SEARCH,
    SupplementationAttempt,
    SupplementationAttemptStatus,
    SupplementationRun,
    SupplementationRunStatus,
)
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.supplementation_paths import supplementation_temp_dir

from test_discovery_v2_analysis_prepare import _new_project, _now


def test_smoke_h_orphan_supplementation_recovery_no_gateway_and_own_temp_only(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "Project"
    (project_root / "Media").mkdir(parents=True)
    project = _new_project(project_root, temp_db_path, name="Phase 10 Recovery")
    run_id = repo.new_supplementation_run_id()
    other_run_id = repo.new_supplementation_run_id()
    own_temp = supplementation_temp_dir(project.project_root_path, run_id)
    other_temp = supplementation_temp_dir(project.project_root_path, other_run_id)
    own_temp.mkdir(parents=True)
    other_temp.mkdir(parents=True)
    (own_temp / "tmp.txt").write_text("x", encoding="utf-8")
    (other_temp / "tmp.txt").write_text("x", encoding="utf-8")

    def fail_gateway(*args, **kwargs):  # pragma: no cover - must not be called.
        raise AssertionError("gateway must not be called during recovery")

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.stock_gateway.StockSearchGateway.search",
        fail_gateway,
    )
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        repo.insert_supplementation_run(
            conn,
            SupplementationRun(
                run_id=run_id,
                project_id=project.id,
                scope=SUPPLEMENTATION_RUN_SCOPE_SEARCH,
                status=SupplementationRunStatus.RUNNING,
                selected_gap_ids=["gap-1"],
                created_at=_now(),
                started_at=_now(),
            ),
        )
        repo.insert_supplementation_attempt(
            conn,
            SupplementationAttempt(
                attempt_id=repo.new_supplementation_attempt_id(),
                run_id=run_id,
                project_id=project.id,
                scope=SUPPLEMENTATION_RUN_SCOPE_SEARCH,
                gap_id="gap-1",
                status=SupplementationAttemptStatus.RUNNING,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    reconcile_orphaned_supplementation_run(project)

    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        run = repo.get_supplementation_run(conn, run_id=run_id)
        attempts = repo.list_supplementation_attempts(conn, run_id=run_id)
    finally:
        conn.close()
    assert run is not None
    assert run.status == SupplementationRunStatus.FAILED
    assert run.error_code == SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED
    assert attempts[0].status == SupplementationAttemptStatus.INTERRUPTED
    assert not own_temp.exists()
    assert other_temp.exists()
