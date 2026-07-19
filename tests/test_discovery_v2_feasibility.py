from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.feasibility_service import (
    evaluate_feasibility,
    start_feasibility_check_run,
)
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    build_visual_edit_input_context,
    start_visual_edit_plan_run,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_narration_timing import _ready_project


def _planned_project(tmp_path: Path, temp_db_path: Path):
    project = _ready_project(tmp_path, temp_db_path)
    assert start_narration_timing_run(project, sync=True).started
    assert start_visual_edit_plan_run(project, sync=True).started
    return project


def test_feasibility_passes_and_sets_ready_after_humanity(tmp_path: Path, temp_db_path: Path) -> None:
    project = _planned_project(tmp_path, temp_db_path)
    assert start_humanity_review_run(project, sync=True).ok
    result = start_feasibility_check_run(project, sync=True)
    assert result.report is not None
    assert result.report.overall_technical_assessment in {"pass", "pass_with_warnings"}
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        plan = visual_repo.get_plan(conn, plan_id=state.current_visual_edit_plan_id)
        assert plan.status == "ready_for_editorial_review"
    finally:
        conn.close()


def test_smoke_f_planned_graphic_blocks_ready_without_replacement(tmp_path: Path, temp_db_path: Path) -> None:
    project = _planned_project(tmp_path, temp_db_path)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        bundle = visual_repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        graphic_shot = bundle.shots[0].model_copy(update={"media_strategy": "planned_graphic"})
        broken = bundle.model_copy(update={"shots": [graphic_shot, *bundle.shots[1:]], "assignments": bundle.assignments[1:]})
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(broken, context.package)
    finally:
        conn.close()
    assert report.report.overall_technical_assessment == "fail"
    assert any(issue.error_code == "planned_graphic_not_exportable" for issue in report.issues)
