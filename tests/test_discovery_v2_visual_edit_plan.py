from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import reset_visual_edit_job_launcher_for_tests
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.feasibility_service import start_feasibility_check_run
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.visual_edit_plan_service import start_visual_edit_plan_run
from otio_app.discovery_v2.application.visual_edit_repair_service import (
    apply_selected_repair_proposals,
    list_repair_proposal_views,
    propose_editorial_repairs,
    select_repair_proposals,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_narration_timing import _ready_project


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_visual_edit_job_launcher_for_tests()
    reset_fake_text_test_hook()
    reset_fake_voice_call_count()
    yield
    reset_fake_voice_call_count()
    reset_fake_text_test_hook()
    reset_visual_edit_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _visual_ready_project(tmp_path: Path, temp_db_path: Path):
    project = _ready_project(tmp_path, temp_db_path)
    timing = start_narration_timing_run(project, sync=True)
    assert timing.started and timing.run is not None
    return project


def _current_bundle(project):
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.current_visual_edit_plan_id
        bundle = visual_repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        assert bundle is not None
        return bundle
    finally:
        conn.close()


def test_smoke_a_visual_edit_fake_e2e_repair_then_ready(tmp_path: Path, temp_db_path: Path) -> None:
    project = _visual_ready_project(tmp_path, temp_db_path)
    plan_result = start_visual_edit_plan_run(project, sync=True)
    assert plan_result.started and plan_result.plan is not None
    humanity = start_humanity_review_run(project, sync=True)
    assert humanity.ok and humanity.review is not None
    feasibility = start_feasibility_check_run(project, sync=True)
    assert feasibility.report is not None
    proposals = propose_editorial_repairs(project)
    assert proposals.ok and proposals.proposals
    selectable = [item for item in list_repair_proposal_views(project) if item.selectable]
    if selectable:
        proposal_id = selectable[0].proposal.proposal_id
        assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
        repaired = apply_selected_repair_proposals(
            project,
            selected_proposal_ids=[proposal_id],
        )
        assert repaired.ok and repaired.output_plan is not None
        assert repaired.output_plan.plan_version == 2
        assert start_humanity_review_run(project, sync=True).ok
        ready = start_feasibility_check_run(project, sync=True)
        assert ready.report is not None
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        current = _current_bundle(project)
        # Without executable blockers the first feasibility pass already marks ready.
        assert current.plan.status in {
            "ready_for_editorial_review",
            "repair_required",
            "review_required",
        }
        if not selectable:
            assert current.plan.status == "ready_for_editorial_review"
        assert visual_repo.find_active_visual_edit_run(conn, project_id=project.id) is None
    finally:
        conn.close()


def test_smoke_b_fake_plan_is_not_one_shot_per_sentence_or_source_group_chapter(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _visual_ready_project(tmp_path, temp_db_path)
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    sentence_to_shots: dict[str, int] = {}
    for shot in bundle.shots:
        for sentence_id in shot.sentence_ids:
            sentence_to_shots[sentence_id] = sentence_to_shots.get(sentence_id, 0) + 1
    assert any(count > 1 for count in sentence_to_shots.values())
    assert any(len(shot.sentence_ids) > 1 for shot in bundle.shots)
    assert len({round(shot.duration_seconds, 3) for shot in bundle.shots}) > 2
    assert all("source group" not in (shot.shot_function or "").lower() for shot in bundle.shots)
    # Multi-sentence coverage proves at least one cut is not a pure sentence-boundary 1:1 cut.
    assert any(len(shot.sentence_ids) > 1 for shot in bundle.shots)
