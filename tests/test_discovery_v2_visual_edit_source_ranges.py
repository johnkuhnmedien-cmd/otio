from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.feasibility_service import evaluate_feasibility
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    _resolve_video_range,
    build_visual_edit_input_context,
    start_visual_edit_plan_run,
)
from otio_app.discovery_v2.domain.visual_edit import VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_narration_timing import _ready_project


def test_smoke_c_video_source_range_resolves_inside_technical_shot_with_handles() -> None:
    tech = {
        "technical_shot_id": "tech-1",
        "start_seconds": 2.0,
        "end_seconds": 8.0,
        "duration_seconds": 6.0,
    }
    start, end, notes = _resolve_video_range(tech, desired=2.0, bias="middle")
    assert start >= 2.10
    assert end <= 7.90
    assert round(end - start, 3) == 2.0
    assert notes == []


def test_short_technical_shot_uses_zero_handles_and_uncertainty_note() -> None:
    tech = {
        "technical_shot_id": "tech-2",
        "start_seconds": 0.0,
        "end_seconds": 0.9,
        "duration_seconds": 0.9,
    }
    start, end, notes = _resolve_video_range(tech, desired=0.8, bias="beginning")
    assert start == 0.0
    assert end <= 0.9
    assert "technical_short_handles_zero" in notes


def test_smoke_d_preview_temp_original_analysis_frame_rejected_as_working_media(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _ready_project(tmp_path, temp_db_path)
    assert start_narration_timing_run(project, sync=True).started
    assert start_visual_edit_plan_run(project, sync=True).started
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        bundle = visual_repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        # Candidate package contains only completed working media.
        allowed = {
            str(item["working_media_id"])
            for item in context.package["candidates"]
        }
        assert allowed
        assert all(
            not any(token in wm_id for token in ("preview", "temp", "original", "analysis_frame"))
            for wm_id in allowed
        )
        poisoned = bundle.assignments[0].model_copy(
            update={"working_media_id": "preview-or-temp-or-original-or-analysis_frame"}
        )
        broken = bundle.model_copy(
            update={"assignments": [poisoned, *bundle.assignments[1:]]}
        )
        report = evaluate_feasibility(broken, context.package)
    finally:
        conn.close()
    assert report.report.overall_technical_assessment == "fail"
    assert any(
        issue.error_code == VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE
        for issue in report.issues
    )
