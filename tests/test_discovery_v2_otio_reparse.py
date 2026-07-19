from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.otio_reparse_service import reparse_otio_file
from otio_app.discovery_v2.export_paths import resolve_export_relative_path
from otio_app.discovery_v2.persistence import export_repository as export_repo
from test_discovery_v2_editorial_approval import _exported_project


def test_smoke_f_reparse_semantics_match_contract(tmp_path: Path, temp_db_path: Path) -> None:
    project, _approval, validation, export = _exported_project(tmp_path, temp_db_path)
    path = resolve_export_relative_path(project.project_root_path, export.artifact.relative_path)
    result = reparse_otio_file(
        path=path,
        contract=validation.contract,
        export_run_id=export.run.run_id,
        artifact_id=export.artifact.artifact_id,
    )
    assert result.ok
    assert result.report.parseable
    assert result.report.semantically_equivalent
    assert result.report.deviations == []
    assert result.report.total_frames == validation.contract.total_frames


def test_smoke_b_persisted_reparse_report_is_current(tmp_path: Path, temp_db_path: Path) -> None:
    project, _approval, _validation, export = _exported_project(tmp_path, temp_db_path)
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        state = export_repo.get_project_state(conn, project_id=project.id)
        report = export_repo.get_reparse_report(conn, report_id=state.current_reparse_report_id)
        assert report is not None
        assert report.export_run_id == export.run.run_id
        assert report.semantically_equivalent
    finally:
        conn.close()
