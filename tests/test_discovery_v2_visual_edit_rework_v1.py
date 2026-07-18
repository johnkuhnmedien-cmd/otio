"""V1 fixture retained: after V2 planner hardening, same setup yields a valid plan."""

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
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    build_visual_edit_input_context,
    start_visual_edit_plan_run,
)
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
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
    """Same V1 fixture: six equal video candidates A–F after script lock."""

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
    install_no_media_io_guards(monkeypatch)
    return project, seeded


def test_fixture_has_multiple_valid_assets_but_fake_plan_uses_first_asset_only(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke A (V1 fixture): Fake ranks A..F; Python assigns six distinct assets."""

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
    assert chosen == list(ASSET_IDS)
    assert len(set(chosen)) == 6
    assert seeded[0]["asset_id"] == ASSET_A_ID


def test_reproduced_plan_fails_e3_asset_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2: E3 is prevented at plan time — reuse stays within ASSET_REUSE_MAX."""

    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    assert len(bundle.assignments) == 6
    reuse = {asset_id: 0 for asset_id in ASSET_IDS}
    for item in bundle.assignments:
        reuse[item.asset_id] = reuse.get(item.asset_id, 0) + 1
    assert all(count <= ASSET_REUSE_MAX for count in reuse.values())
    assert ASSET_REUSE_MAX == 3

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()

    e3 = [issue for issue in report.issues if "E3" in issue.technical_details]
    assert e3 == []
    assert report.report.overall_technical_assessment in {"pass", "pass_with_warnings"}


def test_reproduced_plan_fails_e4_source_range_overlap(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2: distinct assets avoid E4; policy constant remains central."""

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

    severe = []
    for index, left in enumerate(video_assignments):
        for right in video_assignments[index + 1 :]:
            if left.asset_id != right.asset_id or left.working_media_id != right.working_media_id:
                continue
            ratio = overlap_ratio(left, right)
            if ratio >= SOURCE_RANGE_OVERLAP_RATIO_MAX:
                severe.append((left.assignment_id, right.assignment_id, ratio))
    assert severe == []

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()

    e4 = [issue for issue in report.issues if "E4" in issue.technical_details]
    assert e4 == []
    assert report.report.overall_technical_assessment in {"pass", "pass_with_warnings"}


def test_current_repair_proposal_has_no_executable_reassignment(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair path remains V3-deferred; V2 plan is already technically feasible."""

    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    assert start_visual_edit_plan_run(project, sync=True).started
    feasibility = start_feasibility_check_run(project, sync=True)
    assert feasibility.report is not None
    assert feasibility.report.overall_technical_assessment in {"pass", "pass_with_warnings"}


def test_repeated_feasibility_of_unchanged_plan_has_same_issue_signature(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged valid plan: repeated feasibility keeps fingerprints; no E3/E4 issues."""

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
    assert not any("E3" in issue.technical_details for issue in first_bundle.issues)
    assert not any("E4" in issue.technical_details for issue in first_bundle.issues)


def test_reproduction_uses_no_gateway_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _seeded = _reproduced_project(tmp_path, temp_db_path, monkeypatch)
    config = load_text_config()
    assert config.provider == "fake"
    assert REGISTRY_SCHEMA_VERSION == "20"

    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_feasibility_check_run(project, sync=True).report is not None
    assert config.provider == "fake"
