from __future__ import annotations

from pathlib import Path
import sys

import opentimelineio as otio

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.otio_export_service import start_otio_export_run
from otio_app.discovery_v2.export_paths import resolve_export_relative_path
from otio_app.discovery_v2.persistence import export_repository as export_repo
from test_discovery_v2_editorial_approval import _validated_project


def test_smoke_a_validation_to_otio_export_publishes_under_export_only(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, _approval, validation = _validated_project(tmp_path, temp_db_path)
    result = start_otio_export_run(project, sync=True)
    assert result.started, result.error_code
    assert result.run is not None and result.artifact is not None
    assert result.artifact.relative_path.startswith("export/otio/")
    assert result.artifact.relative_path.endswith("/timeline.otio")
    path = resolve_export_relative_path(project.project_root_path, result.artifact.relative_path)
    assert path.exists()
    timeline = otio.adapters.read_from_file(str(path))
    assert timeline.name == validation.contract.timeline_name
    assert [track.name for track in timeline.tracks] == ["V1", "A1"]
    assert not (project.project_root_path / "_otio").exists()


def test_smoke_b_export_updates_current_state_and_hash(tmp_path: Path, temp_db_path: Path) -> None:
    project, _approval, _validation = _validated_project(tmp_path, temp_db_path)
    result = start_otio_export_run(project, sync=True)
    assert result.started and result.run is not None and result.artifact is not None
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        state = export_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_otio_export_run_id == result.run.run_id
        assert state.current_otio_artifact_id == result.artifact.artifact_id
        assert state.current_reparse_report_id
        assert result.run.otio_sha256 == result.artifact.sha256
    finally:
        conn.close()
