from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.application.visual_edit_job_recovery import (
    reconcile_orphaned_visual_edit_run,
)
from otio_app.discovery_v2.domain.visual_edit import VisualEditRun, VisualEditRunStatus
from otio_app.discovery_v2.editing_paths import visual_edit_temp_dir
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from otio_app.models import Project, ProjectMode


def _project(tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    return Project(
        id="project-1",
        name="Recovery",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.DISCOVERY_V2,
        asset_subdir_names=["Media"],
        selected_asset_subdirs=["Media"],
    )


def test_smoke_h_orphan_visual_edit_run_failed_and_temp_cleaned(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run = VisualEditRun(
        run_id="run-1",
        project_id=project.id,
        scope="visual_edit_plan_only",
        status=VisualEditRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
    )
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        visual_repo.insert_visual_edit_run(conn, run)
        conn.commit()
    finally:
        conn.close()
    temp = visual_edit_temp_dir(project.project_root_path, run.run_id)
    temp.mkdir(parents=True)
    (temp / "scratch.json").write_text("{}", encoding="utf-8")
    reconcile_orphaned_visual_edit_run(project)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        failed = visual_repo.get_visual_edit_run(conn, run_id=run.run_id)
        assert failed.status == VisualEditRunStatus.FAILED
        assert failed.error_code == "worker_interrupted"
    finally:
        conn.close()
    assert not temp.exists()
