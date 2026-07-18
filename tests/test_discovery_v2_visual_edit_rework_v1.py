"""V1 reproduction tests for Visual Edit E3/E4 repair loop (no product fix)."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.visual_edit_rework_v1 import (
    ASSET_A_ID,
    ASSET_IDS,
    ASSET_REUSE_MAX,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    ensure_six_visual_intents,
    install_no_media_io_guards,
    install_six_candidate_observation_hook,
    normalize_issue_signature,
    overlap_ratio,
    plan_content_fingerprint,
    seed_six_video_candidates,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import (
    reset_visual_edit_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.feasibility_service import (
    evaluate_feasibility,
    start_feasibility_check_run,
)
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
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
    propose_editorial_repairs,
)
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.visual_edit import VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE
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


def _reproduced_project(tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ready project + 6 video candidates; Fake planner still binds all shots to Asset A."""

    # Pad intents before Script Lock so the lock fingerprint includes six intents.
    project = _script_coverage_project(tmp_path, temp_db_path)
    intents = ensure_six_visual_intents(project)
    assert len(intents) == 6
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
    seeded = seed_six_video_candidates(project)
    assert [item["asset_id"] for item in seeded] == list(ASSET_IDS)
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    # Guard only after fixture bootstrap — reproduction path must stay metadata-only.
    install_no_media_io_guards(monkeypatch)
    return project, seeded


def test_fixture_has_multiple_valid_assets_but_fake_plan_uses_first_asset_only(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn)
    finally:
        conn.close()
    candidates = context.package["candidates"]
    assert len(candidates) == 6
    assert [item["asset_id"] for item in candidates] == list(ASSET_IDS)
    assert all(item["media_kind"] == "video" for item in candidates)
    assert all(item.get("technical_shots") for item in candidates)
    assert len(context.package["visual_intents"]) == 6

    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    assert len(bundle.shots) == 6
    assert len(bundle.assignments) == 6
    chosen = [item.asset_id for item in bundle.assignments]
    assert chosen == [ASSET_A_ID] * 6
    assert len(set(chosen)) == 1
    # Candidates B–F were available but unused by Fake candidates[0] selection.
    unused = set(ASSET_IDS) - {ASSET_A_ID}
    assert unused.isdisjoint(set(chosen))
    assert seeded[0]["asset_id"] == ASSET_A_ID


def test_reproduced_plan_fails_e3_asset_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    assert len(bundle.assignments) == 6
    assert len({item.asset_id for item in bundle.assignments}) == 1
    reuse = sum(1 for item in bundle.assignments if item.asset_id == ASSET_A_ID)
    assert reuse == 6
    assert reuse > ASSET_REUSE_MAX
    # Policy source must be the domain constant, not a test-local number.
    assert ASSET_REUSE_MAX == 3

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()

    e3 = [
        issue
        for issue in report.issues
        if issue.severity == "blocking"
        and "E3" in issue.technical_details
        and ASSET_A_ID in issue.technical_details
    ]
    assert e3, report.issues
    assert all(issue.blocks_phase_13 for issue in e3)
    assert all(issue.error_code == VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE for issue in e3)
    assert report.report.metrics.get("asset_reuse_counts", {}).get(ASSET_A_ID) == 6
    assert report.report.overall_technical_assessment == "fail"


def test_reproduced_plan_fails_e4_source_range_overlap(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    video_assignments = [
        item
        for item in bundle.assignments
        if item.technical_source_in_seconds is not None
        and item.technical_source_out_seconds is not None
    ]
    assert len(video_assignments) == 6
    assert SOURCE_RANGE_OVERLAP_RATIO_MAX == 0.90

    overlaps: list[tuple[str, str, float]] = []
    for index, left in enumerate(video_assignments):
        for right in video_assignments[index + 1 :]:
            if left.asset_id != right.asset_id or left.working_media_id != right.working_media_id:
                continue
            ratio = overlap_ratio(left, right)
            overlaps.append((left.assignment_id, right.assignment_id, ratio))
    severe = [item for item in overlaps if item[2] >= SOURCE_RANGE_OVERLAP_RATIO_MAX]
    assert len(severe) >= 3, overlaps
    # Persist concrete overlap values for the Pflichtbericht.
    assert all(item[2] == pytest.approx(1.0) for item in severe[:3]) or any(
        item[2] >= SOURCE_RANGE_OVERLAP_RATIO_MAX for item in severe
    )

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()

    e4 = [
        issue
        for issue in report.issues
        if issue.severity == "blocking" and "E4" in issue.technical_details
    ]
    assert e4, report.issues
    assert all(issue.blocks_phase_13 for issue in e4)
    # Current product sets assignment_id on the right-hand pair member; shot_id may be None (V2 finding).
    assert any(issue.assignment_id for issue in e4), (
        "V2-Befund: E4-Issues sollten Assignment-IDs tragen; aktuell mindestens eine erwartet"
    )
    missing_shot = [issue for issue in e4 if issue.shot_id is None]
    assert missing_shot  # document current gap for V2 — do not fix here


def test_current_repair_proposal_has_no_executable_reassignment(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    before = _current_bundle(project)
    feasibility = start_feasibility_check_run(project, sync=True)
    assert feasibility.report is not None
    assert feasibility.report.overall_technical_assessment == "fail"

    proposals = propose_editorial_repairs(project)
    assert proposals.ok and proposals.proposals
    proposal = proposals.proposals[0]
    assert proposal.repair_type == "vary_first_local_motif"
    assert proposal.user_status == "proposed"
    # No executable reassignment contract on the current RepairProposal model.
    assert not hasattr(proposal, "operations")
    assert not hasattr(proposal, "target_asset_id")
    assert not hasattr(proposal, "target_source_in_seconds")
    assert "replace_assignment_asset" not in proposal.repair_type
    assert "replace_assignment_source_range" not in proposal.repair_type
    assert ASSET_A_ID not in (proposal.description + proposal.expected_effect)
    # Fake proposal touches only the first shot id (or plan id), not E3/E4 assignment targets.
    assert proposal.affected_ids
    assignment_ids = {item.assignment_id for item in before.assignments}
    assert assignment_ids.isdisjoint(set(proposal.affected_ids))

    applied = apply_selected_repair_proposals(
        project,
        selected_proposal_ids=[proposal.proposal_id],
    )
    assert applied.ok and applied.output_plan is not None
    after = _current_bundle(project)
    assert after.plan.plan_id != before.plan.plan_id
    assert [item.asset_id for item in after.assignments] == [ASSET_A_ID] * 6
    assert [
        (item.technical_source_in_seconds, item.technical_source_out_seconds)
        for item in after.assignments
    ] == [
        (item.technical_source_in_seconds, item.technical_source_out_seconds)
        for item in before.assignments
    ]


def test_repeated_feasibility_of_unchanged_plan_has_same_issue_signature(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    content_fp = plan_content_fingerprint(bundle)

    first = start_feasibility_check_run(project, sync=True)
    assert first.report is not None
    second = start_feasibility_check_run(project, sync=True)
    assert second.report is not None
    assert first.report.report_id != second.report.report_id

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        assert state.current_visual_edit_plan_id == bundle.plan.plan_id
        first_bundle = visual_repo.get_feasibility_report_bundle(
            conn, report_id=first.report.report_id
        )
        second_bundle = visual_repo.get_feasibility_report_bundle(
            conn, report_id=second.report.report_id
        )
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
    finally:
        conn.close()

    assert first_bundle is not None and second_bundle is not None
    assert first_bundle.report.plan_id == second_bundle.report.plan_id == bundle.plan.plan_id
    assert plan_content_fingerprint(_current_bundle(project)) == content_fp
    assert first_bundle.report.input_fingerprint == second_bundle.report.input_fingerprint
    assert first_bundle.report.input_fingerprint == context.fingerprint
    assert first_bundle.report.input_fingerprint == bundle.plan.input_fingerprint

    sig1 = normalize_issue_signature(
        issues=first_bundle.issues,
        plan_fingerprint=bundle.plan.input_fingerprint,
    )
    sig2 = normalize_issue_signature(
        issues=second_bundle.issues,
        plan_fingerprint=bundle.plan.input_fingerprint,
    )
    assert sig1 == sig2
    assert any("E3" in issue.technical_details for issue in first_bundle.issues)
    assert any("E4" in issue.technical_details for issue in first_bundle.issues)


def test_reproduction_uses_no_gateway_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    config = load_text_config()
    assert config.provider == "fake"
    assert REGISTRY_SCHEMA_VERSION == "20"

    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_feasibility_check_run(project, sync=True).report is not None
    proposals = propose_editorial_repairs(project)
    assert proposals.ok
    # Guards installed in _reproduced_project fail on media I/O / subprocess.
    # Fake text provider is the only allowed text path.
    assert config.provider == "fake"
