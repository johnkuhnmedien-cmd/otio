from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.export_job_recovery import reconcile_orphaned_export_run
from otio_app.discovery_v2.domain.export import OtioExportRun, OtioExportRunStatus
from otio_app.discovery_v2.persistence import export_repository as export_repo
from test_discovery_v2_editorial_approval import _validated_project


def test_smoke_g_orphaned_export_run_marked_failed_and_temp_removed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, approval, validation = _validated_project(tmp_path, temp_db_path)
    run = OtioExportRun(
        run_id=export_repo.new_otio_export_run_id(),
        project_id=project.id,
        approval_id=approval.approval_id,
        validation_report_id=validation.report.report_id,
        visual_edit_plan_id=approval.visual_edit_plan_id,
        input_fingerprint=approval.input_fingerprint,
        status=OtioExportRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
    )
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        export_repo.insert_otio_export_run(conn, run)
        conn.commit()
    finally:
        conn.close()
    temp = project.project_root_path / "_otio_v2" / "export" / "temp" / run.run_id
    temp.mkdir(parents=True)
    (temp / "partial.otio").write_text("partial", encoding="utf-8")
    reconcile_orphaned_export_run(project)
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        recovered = export_repo.get_otio_export_run(conn, run_id=run.run_id)
        assert recovered is not None
        assert recovered.status == OtioExportRunStatus.FAILED
        assert recovered.error_code == "worker_interrupted"
    finally:
        conn.close()
    assert not temp.exists()
