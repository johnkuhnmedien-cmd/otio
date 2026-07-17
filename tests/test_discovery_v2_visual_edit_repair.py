from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.feasibility_service import start_feasibility_check_run
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.visual_edit_plan_service import start_visual_edit_plan_run
from otio_app.discovery_v2.application.visual_edit_repair_service import (
    apply_selected_repair_proposals,
    propose_editorial_repairs,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_narration_timing import _ready_project


def test_smoke_g_repair_creates_new_plan_and_stales_old_reviews(tmp_path: Path, temp_db_path: Path) -> None:
    project = _ready_project(tmp_path, temp_db_path)
    assert start_narration_timing_run(project, sync=True).started
    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_humanity_review_run(project, sync=True).ok
    assert start_feasibility_check_run(project, sync=True).report is not None
    proposals = propose_editorial_repairs(project)
    assert proposals.ok and proposals.proposals
    applied = apply_selected_repair_proposals(
        project,
        selected_proposal_ids=[proposals.proposals[0].proposal_id],
    )
    assert applied.ok and applied.output_plan is not None
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        assert state.current_visual_edit_plan_id == applied.output_plan.plan_id
        assert state.current_repair_run_id == applied.repair_run.run_id
        plans = visual_repo.list_plans(conn, project_id=project.id)
        assert [plan.plan_version for plan in plans][:2] == [2, 1]
        old = [plan for plan in plans if plan.plan_version == 1][0]
        assert old.status == "superseded"
    finally:
        conn.close()
