"""Phase 9 Editorial recovery tests."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.editorial_job_recovery import (
    reconcile_orphaned_editorial_run,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_WORKER_INTERRUPTED,
    EDITORIAL_RUN_SCOPE_NARRATIVE,
    EditorialAttempt,
    EditorialAttemptStatus,
    EditorialRun,
    EditorialRunStatus,
)
from otio_app.discovery_v2.editorial_paths import editorial_temp_dir
from otio_app.discovery_v2.persistence import editorial_repository as repo

from test_discovery_v2_analysis_prepare import _new_project, _now


def test_orphan_editorial_run_fails_attempts_and_cleans_own_temp(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "Project"
    (project_root / "Media").mkdir(parents=True)
    project = _new_project(project_root, temp_db_path, name="Phase 9 Recovery")
    run_id = repo.new_editorial_run_id()
    other_run_id = repo.new_editorial_run_id()
    own_temp = editorial_temp_dir(project.project_root_path, run_id)
    other_temp = editorial_temp_dir(project.project_root_path, other_run_id)
    own_temp.mkdir(parents=True)
    other_temp.mkdir(parents=True)
    (own_temp / "tmp.txt").write_text("x", encoding="utf-8")
    (other_temp / "tmp.txt").write_text("x", encoding="utf-8")

    def fail_gateway(*args, **kwargs):  # pragma: no cover - must not be called.
        raise AssertionError("gateway must not be called during recovery")

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.text_gateway.DiscoveryTextGateway.generate",
        fail_gateway,
    )
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        repo.insert_editorial_run(
            conn,
            EditorialRun(
                run_id=run_id,
                project_id=project.id,
                scope=EDITORIAL_RUN_SCOPE_NARRATIVE,
                status=EditorialRunStatus.RUNNING,
                created_at=_now(),
                started_at=_now(),
            ),
        )
        repo.insert_editorial_attempt(
            conn,
            EditorialAttempt(
                attempt_id=repo.new_editorial_attempt_id(),
                run_id=run_id,
                project_id=project.id,
                request_kind="narrative",
                provider="fake",
                model_identifier="fake-editorial-v1",
                gateway_version="discovery-text-gateway-v1",
                prompt_version="editorial-narrative-v1",
                response_schema_version="narrative-plan-v1",
                input_fingerprint="fp",
                status=EditorialAttemptStatus.RUNNING,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    reconcile_orphaned_editorial_run(project)

    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        run = repo.get_editorial_run(conn, run_id=run_id)
        attempts = repo.list_editorial_attempts(conn, run_id=run_id)
    finally:
        conn.close()
    assert run is not None
    assert run.status == EditorialRunStatus.FAILED
    assert run.error_code == EDITORIAL_ERROR_WORKER_INTERRUPTED
    assert attempts[0].status == EditorialAttemptStatus.INTERRUPTED
    assert attempts[0].error_code == EDITORIAL_ERROR_WORKER_INTERRUPTED
    assert not own_temp.exists()
    assert other_temp.exists()
