from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.humanity_review_service import (
    deterministic_humanity_signals,
    start_humanity_review_run,
)
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.visual_edit_plan_service import start_visual_edit_plan_run
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_narration_timing import _ready_project


def _project_with_plan(tmp_path: Path, temp_db_path: Path):
    project = _ready_project(tmp_path, temp_db_path)
    assert start_narration_timing_run(project, sync=True).started
    assert start_visual_edit_plan_run(project, sync=True).started
    return project


def test_humanity_review_persists_completed_review(tmp_path: Path, temp_db_path: Path) -> None:
    project = _project_with_plan(tmp_path, temp_db_path)
    result = start_humanity_review_run(project, sync=True)
    assert result.ok and result.review is not None
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.current_humanity_review_id == result.review.review_id
        bundle = visual_repo.get_humanity_review_bundle(conn, review_id=result.review.review_id)
        assert bundle is not None
        assert bundle.review.status == "completed"
    finally:
        conn.close()


def test_smoke_e_humanity_signals_flag_generic_stock_and_similar_motif(tmp_path: Path, temp_db_path: Path) -> None:
    from otio_app.discovery_v2.application.feasibility_service import (
        evaluate_ready_for_editorial_review,
        start_feasibility_check_run,
    )
    from otio_app.discovery_v2.domain.visual_edit import HumanityFinding

    project = _project_with_plan(tmp_path, temp_db_path)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        bundle = visual_repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        package = {
            "narration_timeline": {"entries": []},
            "candidates": [
                {
                    "observation_id": assignment.visual_observation_id,
                    "generic_stock_like": True,
                    "motif_hash": "same",
                }
                for assignment in bundle.assignments
            ],
        }
        signals = deterministic_humanity_signals(bundle, package)
    finally:
        conn.close()
    assert signals["generic_stock_blocking"] is True
    assert signals["max_similar_motif_run"] >= 3

    # Blocking open finding must prevent ready_for_editorial_review.
    assert start_humanity_review_run(project, sync=True).ok
    assert start_feasibility_check_run(project, sync=True).report is not None
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        review = visual_repo.get_humanity_review_bundle(conn, review_id=state.current_humanity_review_id)
        finding = HumanityFinding(
            finding_id=visual_repo.new_humanity_finding_id(),
            review_id=review.review.review_id,
            shot_id=None,
            plan_level=True,
            category="generic_stock_risk",
            severity="blocking",
            rationale="Forced blocking finding for smoke E.",
            evidence_refs=["smoke-e"],
            recommended_action="repair",
            user_status="open",
        )
        visual_repo.insert_humanity_finding(conn, finding)
        conn.commit()
        assert evaluate_ready_for_editorial_review(conn, project_id=project.id) is False
        plan = visual_repo.get_plan(conn, plan_id=state.current_visual_edit_plan_id)
        assert plan.status != "ready_for_editorial_review"
    finally:
        conn.close()
