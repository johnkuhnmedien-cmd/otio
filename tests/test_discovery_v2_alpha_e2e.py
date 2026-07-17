from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.export_paths import resolve_export_relative_path
from otio_app.discovery_v2.persistence import export_repository as export_repo
from test_discovery_v2_editorial_approval import _exported_project


def test_smoke_h_full_alpha_e2e_reaches_completed_otio_and_reparse(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, approval, validation, export = _exported_project(tmp_path, temp_db_path)
    assert validation.contract is not None
    assert export.run is not None and export.artifact is not None and export.reparse_report is not None
    assert export.run.status.value == "completed"
    assert export.reparse_report.semantically_equivalent
    otio_path = resolve_export_relative_path(project.project_root_path, export.artifact.relative_path)
    assert otio_path.exists()
    assert otio_path.relative_to(project.project_root_path / "_otio_v2").parts[:2] == ("export", "otio")
    assert not (project.project_root_path / "_otio").exists()
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        state = export_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_editorial_approval_id == approval.approval_id
        assert state.current_export_validation_report_id == validation.report.report_id
        assert state.current_otio_export_run_id == export.run.run_id
        assert state.current_otio_artifact_id == export.artifact.artifact_id
        assert state.current_reparse_report_id == export.reparse_report.report_id
    finally:
        conn.close()
