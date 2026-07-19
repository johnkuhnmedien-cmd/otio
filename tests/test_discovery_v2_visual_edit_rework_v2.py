"""V2 planner hardening: E3/E4-aware assignment and source-range resolution."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.visual_edit_rework_v1 import (
    ASSET_IDS,
    ASSET_REUSE_MAX,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    ensure_six_visual_intents,
    install_no_media_io_guards,
    install_six_candidate_observation_hook,
    overlap_ratio,
    seed_six_video_candidates,
    seed_video_candidates,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import FakeTextAdapter, reset_fake_text_test_hook
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
from otio_app.discovery_v2.domain.visual_edit import (
    VISUAL_EDIT_ERROR_NO_E3_COMPLIANT_ASSIGNMENT,
    VISUAL_EDIT_ERROR_NO_E4_COMPLIANT_SOURCE_RANGE,
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


def _current_plan_id(project) -> str | None:
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        return None if state is None else state.current_visual_edit_plan_id
    finally:
        conn.close()


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


def _force_shot_count(monkeypatch: pytest.MonkeyPatch, shot_count: int) -> None:
    original = FakeTextAdapter._visual_edit_plan

    def _wrapped(self, request):
        payload = original(self, request)
        shots = list(payload.get("shots") or [])
        while len(shots) < shot_count and shots:
            template = dict(shots[-1])
            ordinal = len(shots)
            template["shot_id"] = f"{template['shot_id']}-extra-{ordinal}"
            template["ordinal"] = ordinal
            template["priority"] = ordinal
            # Keep ranked_candidates from template (editorial set unchanged).
            shots.append(template)
        payload["shots"] = shots[:shot_count]
        transitions = []
        for left, right in zip(payload["shots"], payload["shots"][1:]):
            transitions.append(
                {
                    "transition_id": f"tr-{left['shot_id']}-{right['shot_id']}",
                    "from_shot_id": left["shot_id"],
                    "to_shot_id": right["shot_id"],
                    "editorial_function": "rhythm_cut",
                    "technical_type": "cut",
                    "desired_duration_seconds": 0.0,
                }
            )
        payload["transitions"] = transitions
        return payload

    monkeypatch.setattr(FakeTextAdapter, "_visual_edit_plan", _wrapped)


def test_smoke_a_v1_fixture_yields_six_distinct_e3_e4_pass(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_six_video_candidates(project)
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    first = start_visual_edit_plan_run(project, sync=True)
    assert first.started and first.plan is not None
    bundle = _current_bundle(project)
    chosen = [item.asset_id for item in bundle.assignments]
    assert chosen == list(ASSET_IDS)
    assert all(
        sum(1 for item in bundle.assignments if item.asset_id == asset_id) <= ASSET_REUSE_MAX
        for asset_id in ASSET_IDS
    )

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()
    assert report.report.overall_technical_assessment in {"pass", "pass_with_warnings"}
    assert not any("E3" in issue.technical_details for issue in report.issues)
    assert not any("E4" in issue.technical_details for issue in report.issues)

    second = start_visual_edit_plan_run(project, sync=True)
    assert second.started and second.plan is not None
    again = [item.asset_id for item in _current_bundle(project).assignments]
    assert again == chosen


def test_smoke_b_two_assets_respect_e3_reuse_max(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_video_candidates(
        project,
        labels=("A", "B"),
        tech_ranges=[(0.0, 30.0)],
        id_prefix="rework-v2b",
    )
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    assert len(bundle.assignments) == 6
    counts: dict[str, int] = {}
    for item in bundle.assignments:
        counts[item.asset_id] = counts.get(item.asset_id, 0) + 1
    assert len(counts) == 2
    assert all(count <= ASSET_REUSE_MAX for count in counts.values())
    assert sum(counts.values()) == 6

    # Same-asset ranges must remain E4-compliant.
    by_asset: dict[str, list] = {}
    for item in bundle.assignments:
        by_asset.setdefault(item.asset_id, []).append(item)
    for items in by_asset.values():
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                assert overlap_ratio(left, right) < SOURCE_RANGE_OVERLAP_RATIO_MAX


def test_smoke_c_e3_unsolvable_does_not_publish_current(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_video_candidates(
        project,
        labels=("A",),
        tech_ranges=[(0.0, 30.0)],
        id_prefix="rework-v2c",
    )
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    _force_shot_count(monkeypatch, ASSET_REUSE_MAX + 1)
    install_no_media_io_guards(monkeypatch)

    before = _current_plan_id(project)
    result = start_visual_edit_plan_run(project, sync=True)
    assert not result.started
    assert result.error_code == VISUAL_EDIT_ERROR_NO_E3_COMPLIANT_ASSIGNMENT
    assert _current_plan_id(project) == before


def test_smoke_d_one_video_three_tech_shots_non_overlapping_ranges(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_video_candidates(
        project,
        labels=("A",),
        tech_ranges=[(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)],
        id_prefix="rework-v2d",
    )
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    assert start_visual_edit_plan_run(project, sync=True).started
    bundle = _current_bundle(project)
    # Fake with one candidate produces three shots.
    assert len(bundle.assignments) == 3
    assert len({item.asset_id for item in bundle.assignments}) == 1
    for index, left in enumerate(bundle.assignments):
        for right in bundle.assignments[index + 1 :]:
            assert overlap_ratio(left, right) < SOURCE_RANGE_OVERLAP_RATIO_MAX

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        report = evaluate_feasibility(bundle, context.package)
    finally:
        conn.close()
    assert not any("E4" in issue.technical_details for issue in report.issues)


def test_smoke_e_e4_unsolvable_does_not_publish_current(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_video_candidates(
        project,
        labels=("A",),
        tech_ranges=[(0.0, 1.2)],
        id_prefix="rework-v2e",
    )
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    before = _current_plan_id(project)
    result = start_visual_edit_plan_run(project, sync=True)
    # One short tech shot cannot host three non-overlapping ranges → block.
    assert not result.started
    assert result.error_code == VISUAL_EDIT_ERROR_NO_E4_COMPLIANT_SOURCE_RANGE
    assert _current_plan_id(project) == before


def test_smoke_f_old_plan_remains_historical_when_new_valid_plan_published(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_six_video_candidates(project)
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    assert start_visual_edit_plan_run(project, sync=True).started
    first = _current_bundle(project)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        visual_repo.update_plan_status(conn, plan_id=first.plan.plan_id, status="repair_required")
        conn.commit()
    finally:
        conn.close()

    assert start_visual_edit_plan_run(project, sync=True).started
    second = _current_bundle(project)
    assert second.plan.plan_id != first.plan.plan_id
    assert second.plan.plan_version == first.plan.plan_version + 1

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        old = visual_repo.get_plan(conn, plan_id=first.plan.plan_id)
        assert old is not None
        assert old.status == "superseded"
        assert old.plan_id == first.plan.plan_id
        # Historical assignments unchanged.
        old_bundle = visual_repo.get_plan_bundle(conn, plan_id=first.plan.plan_id)
        assert [item.asset_id for item in old_bundle.assignments] == [
            item.asset_id for item in first.assignments
        ]
    finally:
        conn.close()


def test_fake_ranked_candidates_rotate_deterministically(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-cause contract: Fake no longer materializes only candidates[0]."""

    project = _ready_locked_project(tmp_path, temp_db_path)
    seeded = seed_six_video_candidates(project)
    install_six_candidate_observation_hook(monkeypatch, project, seeded)
    install_no_media_io_guards(monkeypatch)

    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        context = build_visual_edit_input_context(project, conn=conn)
    finally:
        conn.close()
    from otio_app.discovery_v2.adapters.text_config import load_text_config
    from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway
    from otio_app.discovery_v2.domain.editorial import TextGatewayRequest
    from otio_app.discovery_v2.domain.visual_edit import (
        PROMPT_VERSION_VISUAL_EDIT_PLAN,
        RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
        TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    )
    from otio_app.discovery_v2.application.visual_edit_plan_service import (
        _intent_models,
        _beat_models,
        _sentence_models,
    )

    config = load_text_config()
    request = TextGatewayRequest(
        project_id=project.id,
        run_id="run-rank-check",
        request_kind=TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
        prompt="visual_edit_plan",
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=PROMPT_VERSION_VISUAL_EDIT_PLAN,
        response_schema_version=RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
        sentences=_sentence_models(context.script_bundle),
        visual_beats=_beat_models(context.script_bundle),
        visual_intents=_intent_models(context.script_bundle),
        observations=context.observations,
        candidate_asset_ids=[item.asset_id for item in context.observations],
        input_fingerprint=context.fingerprint,
        visual_edit_input=context.package,
    )
    payload = DiscoveryTextGateway(config=config).generate(request).visual_edit_plan
    assert payload is not None
    preferred = [shot.candidate_asset_id for shot in payload.shots]
    assert preferred == list(ASSET_IDS)
    for index, shot in enumerate(payload.shots):
        ranked_ids = [ref.asset_id for ref in shot.ranked_candidates]
        assert len(ranked_ids) == 6
        assert ranked_ids[0] == ASSET_IDS[index]
        assert set(ranked_ids) == set(ASSET_IDS)
    assert REGISTRY_SCHEMA_VERSION == "20"
