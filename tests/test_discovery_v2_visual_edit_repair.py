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
    list_repair_proposal_views,
    propose_editorial_repairs,
    select_repair_proposals,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from fixtures.visual_edit_rework_v1 import (
    editorial_ready_views_for_seed,
    ensure_six_visual_intents,
    install_no_media_io_guards,
    install_observation_hook,
    seed_video_candidates,
)
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from test_discovery_v2_script_lock import (
    _decide_all_claims,
    _resolve_all_gaps_locally,
    _script_coverage_project,
)


def test_smoke_g_repair_creates_new_plan_and_stales_old_reviews(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    ensure_six_visual_intents(project)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    ).ok
    assert start_voice_generation_run(project, sync=True).started
    assert start_pause_direction_run(project, sync=True).started
    assert start_narration_timing_run(project, sync=True).started
    install_no_media_io_guards(monkeypatch)
    seeded = seed_video_candidates(
        project,
        labels=("A", "B", "C", "D", "E", "F", "G"),
        tech_ranges=[(0.0, 8.0)],
        id_prefix="repair-smoke",
    )
    summaries = {label: "Identical local motif for humanity run" for label in "ABCDEF"}
    summaries["G"] = "Distinct alternate motif coverage"
    install_observation_hook(
        monkeypatch,
        editorial_ready_views_for_seed(project, seeded, summary_by_label=summaries),
    )
    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_humanity_review_run(project, sync=True).ok
    assert start_feasibility_check_run(project, sync=True).report is not None
    proposals = propose_editorial_repairs(project)
    assert proposals.ok and proposals.proposals
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    assert views
    proposal_id = views[0].proposal.proposal_id
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    applied = apply_selected_repair_proposals(
        project,
        selected_proposal_ids=[proposal_id],
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
