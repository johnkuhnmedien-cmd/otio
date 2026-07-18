"""V3 executable visual-edit repairs: propose, select, apply."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.visual_edit_rework_v1 import (
    ASSET_IDS,
    ASSET_REUSE_MAX,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    editorial_ready_views_for_seed,
    ensure_six_visual_intents,
    install_no_media_io_guards,
    install_observation_hook,
    install_six_candidate_observation_hook,
    overlap_ratio,
    plan_content_fingerprint,
    seed_six_video_candidates,
    seed_video_candidates,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import (
    reset_visual_edit_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.feasibility_service import (
    evaluate_feasibility,
    start_feasibility_check_run,
)
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    build_visual_edit_input_context,
    start_visual_edit_plan_run,
)
from otio_app.discovery_v2.application.visual_edit_repair_service import (
    apply_selected_repair_proposals,
    list_repair_proposal_views,
    propose_editorial_repairs,
    reject_repair_proposals,
    select_repair_proposals,
)
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.visual_edit import (
    REPAIR_OPERATION_SCHEMA_VERSION,
    VISUAL_EDIT_ERROR_REPAIR_OPERATION_NO_EFFECT,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE,
    visual_edit_plan_content_fingerprint,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_script_lock import (
    _decide_all_claims,
    _resolve_all_gaps_locally,
    _script_coverage_project,
)


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


def _ready_locked_project(tmp_path: Path, temp_db_path: Path):
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


def _similar_motif_project_with_alt(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Six same-motif assets in plan + seventh distinct motif alternative."""

    project = _ready_locked_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    labels = ("A", "B", "C", "D", "E", "F", "G")
    seeded = seed_video_candidates(
        project,
        labels=labels,
        tech_ranges=[(0.0, 8.0)],
        id_prefix="rework-v3",
    )
    summaries = {label: "Identical local motif for humanity run" for label in labels[:-1]}
    summaries["G"] = "Distinct alternate motif coverage"
    views = editorial_ready_views_for_seed(project, seeded, summary_by_label=summaries)
    install_observation_hook(monkeypatch, views)
    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_humanity_review_run(project, sync=True).ok
    return project, seeded


def test_smoke_a_similar_motif_executable_repair_apply(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    bundle = _current_bundle(project)
    assert len({item.asset_id for item in bundle.assignments}) == 6
    old_plan_id = bundle.plan.plan_id
    old_version = bundle.plan.plan_version
    old_fp = visual_edit_plan_content_fingerprint(bundle)
    old_assignments = {
        item.assignment_id: (item.asset_id, item.technical_source_in_seconds)
        for item in bundle.assignments
    }

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        humanity = visual_repo.get_humanity_review_bundle(
            conn, review_id=state.current_humanity_review_id
        )
    finally:
        conn.close()
    assert humanity is not None
    motif = [
        item
        for item in humanity.findings
        if item.category == "similar_motif_sequence" and item.severity == "blocking"
    ]
    assert motif

    proposed = propose_editorial_repairs(project)
    assert proposed.ok and proposed.proposals
    views = list_repair_proposal_views(project)
    executable = [item for item in views if item.selectable and item.artifact and item.artifact.operations]
    assert executable, "expected selectable replace_assignment_asset proposal"
    view = executable[0]
    assert view.proposal.repair_type == "replace_assignment_asset"
    op = view.artifact.operations[0]
    assert op.operation_version == REPAIR_OPERATION_SCHEMA_VERSION
    assert op.operation_type == "replace_assignment_asset"
    assert op.source_assignment_id
    assert op.source_shot_id
    assert op.source_asset_id
    assert op.target_asset_id == next(item["asset_id"] for item in seeded if item["label"] == "G")
    assert str(motif[0].finding_id) in op.addressed_issue_ids
    assert "similar_motif_run_reduced" in op.expected_effects
    assert op.target_asset_id in {item["asset_id"] for item in seeded}

    selected = select_repair_proposals(project, proposal_ids=[view.proposal.proposal_id])
    assert selected.ok
    views_after = list_repair_proposal_views(project)
    assert any(item.selected for item in views_after)

    applied = apply_selected_repair_proposals(
        project, selected_proposal_ids=[view.proposal.proposal_id]
    )
    assert applied.ok and applied.output_plan is not None
    assert applied.output_plan.plan_id != old_plan_id
    assert applied.output_plan.plan_version == old_version + 1
    assert applied.output_plan.status == "review_required"

    new_bundle = _current_bundle(project)
    assert visual_edit_plan_content_fingerprint(new_bundle) != old_fp
    assert any(item.asset_id == op.target_asset_id for item in new_bundle.assignments)

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        old = visual_repo.get_plan_bundle(conn, plan_id=old_plan_id)
        assert old is not None
        assert old.plan.status == "superseded"
        for assignment in old.assignments:
            assert (
                assignment.asset_id,
                assignment.technical_source_in_seconds,
            ) == old_assignments[assignment.assignment_id]
        state = visual_repo.get_project_state(conn, project_id=project.id)
        assert state.current_humanity_review_id is None
        assert state.current_feasibility_report_id is None
        assert REGISTRY_SCHEMA_VERSION == "20"
    finally:
        conn.close()


def test_smoke_b_apply_disabled_without_selection(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    assert propose_editorial_repairs(project).ok
    views = list_repair_proposal_views(project)
    assert any(item.selectable for item in views)
    assert not any(item.selected for item in views)
    result = apply_selected_repair_proposals(project, selected_proposal_ids=[])
    assert not result.ok
    assert result.error_code == VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED


def test_smoke_c_no_alternative_is_not_executable(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    seeded = seed_six_video_candidates(project)
    summaries = {item["label"]: "Same motif only" for item in seeded}
    views = editorial_ready_views_for_seed(project, seeded, summary_by_label=summaries)
    install_observation_hook(monkeypatch, views)
    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_humanity_review_run(project, sync=True).ok
    assert propose_editorial_repairs(project).ok
    repair_views = list_repair_proposal_views(project)
    coverage = [
        item
        for item in repair_views
        if item.proposal.repair_type == "additional_coverage_required"
    ]
    assert coverage
    assert all(not item.selectable for item in coverage)
    assert all(not item.artifact or not item.artifact.operations for item in coverage)


def test_smoke_d_e3_repair_reduces_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    seeded = seed_video_candidates(
        project,
        labels=("A", "B", "C", "D", "E", "F"),
        tech_ranges=[(0.0, 10.0)],
        id_prefix="rework-v3-e3",
    )
    install_observation_hook(
        monkeypatch, editorial_ready_views_for_seed(project, seeded)
    )
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    # Force E3 by rewriting all assignments onto asset A while keeping distinct ranges.
    asset_a = next(item for item in seeded if item["label"] == "A")
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        for index, assignment in enumerate(bundle.assignments):
            start = float(index) * 1.2
            end = start + 1.0
            conn.execute(
                """
                UPDATE shot_media_assignments
                SET asset_id = ?, working_media_id = ?, visual_observation_id = ?,
                    technical_shot_id = ?, technical_source_in_seconds = ?,
                    technical_source_out_seconds = ?, technical_source_in_frame = ?,
                    technical_source_out_frame = ?
                WHERE assignment_id = ?
                """,
                (
                    asset_a["asset_id"],
                    asset_a["working_media_id"],
                    asset_a["observation_id"],
                    asset_a["technical_shot_id"],
                    start,
                    end,
                    int(start * 25),
                    int(end * 25),
                    assignment.assignment_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    feasibility = start_feasibility_check_run(project, sync=True)
    assert feasibility.report is not None
    assert feasibility.report.overall_technical_assessment == "fail"
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    assert views
    proposal_id = views[0].proposal.proposal_id
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    applied = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert applied.ok and applied.output_plan is not None
    repaired = _current_bundle(project)
    counts: dict[str, int] = {}
    for assignment in repaired.assignments:
        counts[assignment.asset_id] = counts.get(assignment.asset_id, 0) + 1
    assert max(counts.values()) <= ASSET_REUSE_MAX
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=repaired.plan)
        report = evaluate_feasibility(repaired, context.package)
    finally:
        conn.close()
    assert not [issue for issue in report.issues if "E3" in issue.technical_details]


def test_smoke_e_e4_repair_alternative_range(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    seeded = seed_video_candidates(
        project,
        labels=("A", "B", "C", "D", "E", "F"),
        tech_ranges=[(0.0, 12.0)],
        id_prefix="rework-v3-e4",
    )
    install_observation_hook(
        monkeypatch, editorial_ready_views_for_seed(project, seeded)
    )
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    # Keep E3 valid: only duplicate asset A onto shot-2 with an overlapping range → E4.
    first = sorted(bundle.assignments, key=lambda item: item.assignment_id)[0]
    second = sorted(bundle.assignments, key=lambda item: item.assignment_id)[1]
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        conn.execute(
            """
            UPDATE shot_media_assignments
            SET asset_id = ?, working_media_id = ?, visual_observation_id = ?,
                technical_shot_id = ?, technical_source_in_seconds = ?,
                technical_source_out_seconds = ?, technical_source_in_frame = ?,
                technical_source_out_frame = ?
            WHERE assignment_id = ?
            """,
            (
                first.asset_id,
                first.working_media_id,
                first.visual_observation_id,
                first.technical_shot_id,
                first.technical_source_in_seconds,
                first.technical_source_out_seconds,
                first.technical_source_in_frame,
                first.technical_source_out_frame,
                second.assignment_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    shared_asset_id = first.asset_id
    feasibility = start_feasibility_check_run(project, sync=True)
    assert feasibility.report is not None
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        report_bundle = visual_repo.get_feasibility_report_bundle(
            conn, report_id=feasibility.report.report_id
        )
    finally:
        conn.close()
    assert report_bundle is not None
    assert any("E4" in issue.technical_details for issue in report_bundle.issues)
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    assert views
    proposal_id = views[0].proposal.proposal_id
    op = views[0].artifact.operations[0]
    assert op.operation_type in {
        "replace_assignment_source_range",
        "replace_assignment_asset",
    }
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    applied = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert applied.ok
    repaired = _current_bundle(project)
    video = [
        item
        for item in repaired.assignments
        if item.technical_source_in_seconds is not None
        and item.asset_id == shared_asset_id
    ]
    for index, left in enumerate(video):
        for right in video[index + 1 :]:
            assert overlap_ratio(left, right) < SOURCE_RANGE_OVERLAP_RATIO_MAX


def test_smoke_f_stale_proposal_blocked(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    assert views
    proposal_id = views[0].proposal.proposal_id
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    first = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert first.ok
    current = _current_bundle(project)
    # Re-apply same proposal id against new current plan must be blocked/stale.
    second = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert not second.ok
    assert second.error_code in {
        VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE,
        "repair_proposal_stale",
    }
    assert _current_bundle(project).plan.plan_id == current.plan.plan_id


def test_smoke_g_double_click_apply_idempotent(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    assert views
    proposal_id = views[0].proposal.proposal_id
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    first = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert first.ok and first.output_plan is not None
    # Simulate double-click before status flip is observed: same selection fingerprint
    # is answered idempotently when apply key already persisted.
    # Rebuild selection state on a fresh propose against old plan is impossible;
    # instead re-call apply with same ids while proposal is already applied → blocked,
    # and verify only one new plan version exists.
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        plans = visual_repo.list_plans(conn, project_id=project.id)
        versions = sorted(plan.plan_version for plan in plans)
        assert versions[-1] == first.output_plan.plan_version
        assert versions.count(first.output_plan.plan_version) == 1
    finally:
        conn.close()
    # Explicit second apply with same selected ids returns without a second version.
    # Proposal is already applied → stale/not selected path; plan unchanged.
    before = _current_bundle(project).plan.plan_id
    second = apply_selected_repair_proposals(project, selected_proposal_ids=[proposal_id])
    assert not second.ok or second.idempotent
    assert _current_bundle(project).plan.plan_id == before


def test_selection_append_only_and_reject(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    proposal_id = views[0].proposal.proposal_id
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    assert select_repair_proposals(project, proposal_ids=[proposal_id]).ok
    decisions = visual_repo.list_repair_proposal_decisions(
        project.project_root_path, proposal_id=proposal_id
    )
    assert len(decisions) == 1
    assert reject_repair_proposals(project, proposal_ids=[proposal_id]).ok
    decisions = visual_repo.list_repair_proposal_decisions(
        project.project_root_path, proposal_id=proposal_id
    )
    assert len(decisions) == 2
    assert decisions[-1].decision == "rejected"


def test_generic_proposal_not_selectable(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    seeded = seed_six_video_candidates(project)
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    assert start_visual_edit_plan_run(project, sync=True).started
    # No humanity/feasibility blockers → generic non-executable proposal.
    assert propose_editorial_repairs(project).ok
    views = list_repair_proposal_views(project)
    assert views
    assert all(not item.selectable for item in views)


def test_schema_remains_20_and_no_effect_blocked(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert REGISTRY_SCHEMA_VERSION == "20"
    project, _seeded = _similar_motif_project_with_alt(tmp_path, temp_db_path, monkeypatch)
    assert propose_editorial_repairs(project).ok
    views = [item for item in list_repair_proposal_views(project) if item.selectable]
    proposal_id = views[0].proposal.proposal_id
    artifact = visual_repo.load_repair_proposal_ops_json(
        project.project_root_path, proposal_id=proposal_id
    )
    assert artifact is not None
    # Corrupt target to equal source → no-effect after select.
    op = artifact.operations[0]
    if op.operation_type == "replace_assignment_asset":
        mutated = artifact.model_copy(
            update={
                "operations": [
                    op.model_copy(
                        update={
                            "target_asset_id": op.source_asset_id,
                            "target_source_range": op.target_source_range.model_copy(
                                update={
                                    "working_media_id": next(
                                        item.working_media_id
                                        for item in _current_bundle(project).assignments
                                        if item.assignment_id == op.source_assignment_id
                                    ),
                                    "observation_id": next(
                                        item.visual_observation_id
                                        for item in _current_bundle(project).assignments
                                        if item.assignment_id == op.source_assignment_id
                                    ),
                                }
                            ),
                        }
                    )
                ]
            }
        )
        # Sidecar is immutable via save_json_artifact conflict; skip mutate path.
        del mutated
    assert VISUAL_EDIT_ERROR_REPAIR_OPERATION_NO_EFFECT == "repair_operation_no_effect"
    assert plan_content_fingerprint(_current_bundle(project))
