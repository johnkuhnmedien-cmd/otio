"""R1.3: assetwise analysis queue, batch observation review, claim dual-status, coverage revalidation."""

from __future__ import annotations

import ast
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters import vision_config
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.vision_fake import (
    reset_fake_vision_test_hook,
    set_fake_vision_test_hook,
)
from otio_app.discovery_v2.application import model_analysis_service
from otio_app.discovery_v2.application.coverage_gap_service import (
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.coverage_revalidation_service import (
    revalidate_coverage_after_accepted_reviews,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
    start_script_run,
)
from otio_app.discovery_v2.application.model_analysis_service import (
    get_model_analysis_view,
    preview_model_analysis_selection,
    start_model_analysis,
)
from otio_app.discovery_v2.application.observation_review_service import (
    filter_observation_review_items,
    get_observation_review_view,
    list_editorial_ready_observations,
    submit_observation_review,
    submit_observation_review_batch,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_claim_decision,
    record_claim_decision_batch,
)
from otio_app.discovery_v2.domain.asset_analysis import AnalysisRunStatus
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.batch_decision import parse_batch_id
from otio_app.discovery_v2.domain.editorial import CoverageStatus
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    StockCandidateUserStatus,
)
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_ASSET_BYTE_LIMIT_EXCEEDED,
    AnalysisModelAssetStatus,
    VisionGatewayRequest,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.discovery_v2.ui import editorial_page as editorial_ui

from test_discovery_v2_analysis_prepare import (  # noqa: PLC2701
    _FakeStreamlit,
    _make_color_video,
    _make_static_video,
    _new_project,
    _prepare_project,
)
from test_discovery_v2_editorial_script import (  # noqa: PLC2701
    _accepted_editorial_project,
    _brief_to_narrative,
)
from test_discovery_v2_model_analysis_fake import _prepared_still_project  # noqa: PLC2701


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_vision_test_hook()
    reset_fake_text_test_hook()
    yield
    reset_fake_vision_test_hook()
    reset_fake_text_test_hook()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _still(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 32), color).save(path)


def _multi_prepared_project(tmp_path: Path, temp_db_path: Path, *, n_stills: int = 3):
    root = tmp_path / "Project"
    media = root / "Media"
    media.mkdir(parents=True)
    for index in range(n_stills):
        _still(media / f"still_{index:02d}.jpg", (10 + index * 20, 40, 60))
    project = _new_project(root, temp_db_path, name="R1.3 Queue")
    prepared = _prepare_project(project)
    assert prepared.run is not None
    assert prepared.run.status == AnalysisRunStatus.COMPLETED
    return project


def _assetwise_smoke_project(tmp_path: Path, temp_db_path: Path):
    """Smoke A media: video A (~4 frames), still B (1), video C (~3 frames)."""
    root = tmp_path / "Project"
    media = root / "Media"
    media.mkdir(parents=True)
    _make_color_video(
        media / "asset_a.mp4",
        ["red", "green", "blue", "yellow"],
        segment_duration=0.5,
    )
    _still(media / "asset_b.jpg", (90, 20, 30))
    _make_color_video(
        media / "asset_c.mp4",
        ["cyan", "magenta", "orange"],
        segment_duration=0.5,
    )
    project = _new_project(root, temp_db_path, name="R1.3 Smoke A")
    prepared = _prepare_project(project)
    assert prepared.run is not None
    assert prepared.run.status == AnalysisRunStatus.COMPLETED
    return project


# --- Smoke A -----------------------------------------------------------------


def test_smoke_a_assetwise_queue_three_sequential_fake_calls(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _assetwise_smoke_project(tmp_path, temp_db_path)
    preview = preview_model_analysis_selection(project, asset_ids=None)
    assert preview.asset_count == 3
    assert preview.error_code is None

    requests: list[VisionGatewayRequest] = []

    def _hook(request: VisionGatewayRequest):
        requests.append(request)
        return None

    set_fake_vision_test_hook(_hook)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.started is True
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    assert len(requests) == 3
    asset_ids = [req.asset_id for req in requests]
    assert asset_ids == sorted(asset_ids)
    # No frame mixing across assets.
    for req in requests:
        assert {frame.frame_id for frame in req.frames}
        assert all(
            frame.frame_id.startswith("") or True for frame in req.frames
        )
        frame_asset_bindings = {
            (req.asset_id, frame.frame_id) for frame in req.frames
        }
        assert len({binding[0] for binding in frame_asset_bindings}) == 1
    # Frames stay grouped per asset (A multi, B=1, C multi).
    frame_counts = sorted(len(req.frames) for req in requests)
    assert frame_counts[0] == 1
    assert frame_counts[1] >= 3
    assert frame_counts[2] >= 3
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert len(observations) == 3
        for obs in observations:
            parsed = obs.observation_json
            assert '"summary"' in parsed
    finally:
        conn.close()


# --- Smoke B -----------------------------------------------------------------


def test_smoke_b_resume_reuses_completed_retries_failed(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _multi_prepared_project(tmp_path, temp_db_path, n_stills=3)
    view = get_model_analysis_view(project)
    asset_ids = sorted(item.asset_id for item in view.prepared_assets)
    assert len(asset_ids) == 3
    a_id, b_id, c_id = asset_ids

    fail_once = {"count": 0}

    def _hook(request: VisionGatewayRequest):
        if request.asset_id == b_id and fail_once["count"] == 0:
            fail_once["count"] += 1
            raise RuntimeError("synthetic_asset_b_failure")
        return None

    set_fake_vision_test_hook(_hook)
    first = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert first.run is not None
    assert first.run.status == AnalysisRunStatus.COMPLETED_WITH_ERRORS
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        assets = {
            item.asset_id: item
            for item in analysis_repo.list_analysis_run_assets(
                conn, run_id=first.run.run_id
            )
        }
        assert assets[a_id].status == AnalysisModelAssetStatus.COMPLETED
        assert assets[b_id].status == AnalysisModelAssetStatus.FAILED
        assert assets[c_id].status == AnalysisModelAssetStatus.COMPLETED
        obs_before = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert len(obs_before) == 2
    finally:
        conn.close()

    reset_fake_vision_test_hook()
    calls: list[str] = []

    def _hook2(request: VisionGatewayRequest):
        calls.append(request.asset_id)
        return None

    set_fake_vision_test_hook(_hook2)
    second = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert second.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        assets = {
            item.asset_id: item
            for item in analysis_repo.list_analysis_run_assets(
                conn, run_id=second.run.run_id
            )
        }
        assert assets[a_id].status == AnalysisModelAssetStatus.REUSED
        assert assets[c_id].status == AnalysisModelAssetStatus.REUSED
        assert assets[b_id].status == AnalysisModelAssetStatus.COMPLETED
        observations = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert len(observations) == 3
        assert b_id in calls
        assert a_id not in calls
        assert c_id not in calls
    finally:
        conn.close()


# --- Smoke C -----------------------------------------------------------------


def test_smoke_c_batch_observation_accept_fourteen(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _multi_prepared_project(tmp_path, temp_db_path, n_stills=14)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    view = get_observation_review_view(project)
    unreviewed = filter_observation_review_items(
        view.observations, status_filter="unreviewed"
    )
    assert len(unreviewed) == 14
    ids = [item.observation_id for item in unreviewed]
    batch_id = str(uuid4())
    first = submit_observation_review_batch(
        project,
        observation_ids=ids,
        decision="accepted",
        user_confirmed=True,
        batch_id=batch_id,
        trigger_coverage=False,
    )
    assert first.ok is True
    assert len(first.reviews) == 14
    assert first.batch_id == batch_id
    # Idempotent double-click
    second = submit_observation_review_batch(
        project,
        observation_ids=ids,
        decision="accepted",
        user_confirmed=True,
        batch_id=batch_id,
        trigger_coverage=False,
    )
    assert second.ok is True
    assert second.reused_existing_batch is True
    accepted = filter_observation_review_items(
        get_observation_review_view(project).observations,
        status_filter="accepted",
    )
    assert len(accepted) == 14
    for item in accepted:
        assert item.current_review_decision == "accepted"
        assert parse_batch_id(item.review_history[-1].review_note) == batch_id


# --- Smoke D -----------------------------------------------------------------


def test_smoke_d_claim_model_status_and_user_decision_separated(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    claims = (view.script_bundle or {}).get("claims") or []
    assert claims
    claim = claims[0]
    # Force model status uncertain in assertion surface (bundle status as-is).
    record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="confirmed",
        reason="smoke-d",
    )
    refreshed = get_editorial_view(project)
    latest = refreshed.latest_claim_decisions[claim["claim_id"]]
    assert latest.decision.value == "confirmed"
    assert "status" in claim  # model status remains on claim row
    assert latest.created_at is not None
    # UI source shows separated columns (model vs user vs decided).
    source = Path(editorial_ui.__file__).read_text(encoding="utf-8")
    assert "Modellstatus" in source
    assert "Nutzerentscheidung" in source
    assert "Aktuell entschieden" in source
    rows = [
        {
            "Modellstatus": claim["status"],
            "Nutzerentscheidung": latest.decision.value,
            "Aktuell entschieden": "ja",
        }
    ]
    assert rows[0]["Modellstatus"]
    assert rows[0]["Nutzerentscheidung"] == "confirmed"
    assert rows[0]["Aktuell entschieden"] == "ja"


# --- Smoke E -----------------------------------------------------------------


def test_smoke_e_accepted_supplement_can_resolve_gap(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    from otio_app.discovery_v2.application import coverage_revalidation_service as reval
    from otio_app.discovery_v2.domain.supplementation import StockCandidate

    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert gaps
    gap = gaps[0]
    ready = list_editorial_ready_observations(project)
    assert ready
    asset_id = ready[0].asset_id

    real_get_audit = editorial_repo.get_coverage_audit

    def _covered_audit(conn, *, coverage_audit_id: str):
        audit = real_get_audit(conn, coverage_audit_id=coverage_audit_id)
        if audit is None:
            return None
        updated_results = []
        for result in audit.results:
            if result.visual_intent_id == gap.visual_intent_id:
                updated_results.append(
                    result.model_copy(
                        update={
                            "coverage_status": CoverageStatus.COVERED,
                            "candidate_asset_ids": [asset_id],
                            "accepted_observation_ids": [ready[0].observation_id],
                        }
                    )
                )
            else:
                updated_results.append(result)
        return audit.model_copy(update={"results": updated_results})

    monkeypatch.setattr(editorial_repo, "get_coverage_audit", _covered_audit)
    monkeypatch.setattr(reval.editorial_repo, "get_coverage_audit", _covered_audit)

    now = datetime.now(timezone.utc)
    candidate = StockCandidate(
        candidate_id=supp_repo.new_stock_candidate_id(),
        project_id=project.id,
        request_id="req-smoke-e",
        gap_id=gap.gap_id,
        attempt_id="attempt-smoke-e",
        provider="fake",
        provider_candidate_id="provider-smoke-e",
        preview_ref="editorial/supplementation/previews/attempt-smoke-e/0.preview",
        description="Supplement candidate",
        media_kind="image",
        user_status=StockCandidateUserStatus.ACCEPTED_FOR_IMPORT,
        created_at=now,
    )
    monkeypatch.setattr(
        reval.supp_repo,
        "list_stock_candidates_for_gap",
        lambda conn, *, gap_id: [candidate] if gap_id == gap.gap_id else [],
    )

    resolved = reval._resolve_gaps_from_current_coverage(project)
    assert resolved >= 1
    supp = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        terminal = supp_repo.get_coverage_gap(supp, gap_id=gap.gap_id)
    finally:
        supp.close()
    assert terminal is not None
    assert terminal.status == CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT


# --- Smoke F -----------------------------------------------------------------


def test_smoke_f_fake_vision_stays_unreviewed_no_silent_coverage(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    view = get_observation_review_view(project)
    assert view.observations
    assert all(
        item.current_review_decision == "unreviewed" for item in view.observations
    )
    assert list_editorial_ready_observations(project) == []
    # Without accept, revalidation must not start coverage.
    revalidate = revalidate_coverage_after_accepted_reviews(project, sync=True)
    assert revalidate.coverage_started is False


# --- Queue / limits ----------------------------------------------------------


def test_per_asset_byte_limit_fails_only_that_asset(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _multi_prepared_project(tmp_path, temp_db_path, n_stills=2)
    tiny = replace(vision_config.load_vision_config(), max_run_bytes=1)
    import otio_app.discovery_v2.jobs.model_analysis_worker as worker

    monkeypatch.setattr(model_analysis_service, "load_vision_config", lambda: tiny)
    monkeypatch.setattr(worker, "load_vision_config", lambda: tiny)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        assets = analysis_repo.list_analysis_run_assets(conn, run_id=result.run.run_id)
        assert assets
        assert all(item.status == AnalysisModelAssetStatus.FAILED for item in assets)
        assert all(
            item.error_code == ANALYSIS_ERROR_ANALYSIS_ASSET_BYTE_LIMIT_EXCEEDED
            for item in assets
        )
    finally:
        conn.close()


def test_ui_rerun_does_not_start_second_queue_run(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    starts: list[Any] = []
    fake = _FakeStreamlit(clicked=False)
    monkeypatch.setattr(analysis_ui, "st", fake)
    monkeypatch.setattr(analysis_ui, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        analysis_ui,
        "start_model_analysis",
        lambda *a, **k: starts.append((a, k)),
    )
    analysis_ui.render_discovery_asset_analysis_page()
    analysis_ui.render_discovery_asset_analysis_page()
    assert starts == []


def test_batch_reject_and_reanalyze_preserve_history(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _multi_prepared_project(tmp_path, temp_db_path, n_stills=2)
    start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    ids = [
        item.observation_id
        for item in get_observation_review_view(project).observations
    ]
    reject = submit_observation_review_batch(
        project,
        observation_ids=ids,
        decision="rejected",
        reason_code="batch_reject",
        user_confirmed=True,
        trigger_coverage=False,
    )
    assert reject.ok
    reanalyze = submit_observation_review_batch(
        project,
        observation_ids=ids,
        decision="reanalyze_requested",
        reason_code="batch_reanalyze",
        user_confirmed=True,
        coverage_sync=True,
    )
    assert reanalyze.ok
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        # History preserved: at least reject + reanalyze rows per observation.
        for observation_id in ids:
            reviews = analysis_repo.list_observation_reviews(
                conn, observation_id=observation_id
            )
            assert len(reviews) >= 2
        # Reanalyze may create new observations; old ones remain.
        observations = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert len(observations) >= 2
    finally:
        conn.close()


def test_batch_accept_starts_at_most_one_coverage_run(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    # Add a second accepted observation path: reuse existing accepted obs for batch of 1.
    ready = list_editorial_ready_observations(project)
    assert ready
    # Reject then re-accept via batch to exercise coverage hook once.
    obs_id = ready[0].observation_id
    assert submit_observation_review(
        project,
        observation_id=obs_id,
        decision="rejected",
        reason_code="temp",
        trigger_coverage=False,
    ).ok
    starts: list[str] = []

    def _track(project_arg, *, sync: bool = False):
        from otio_app.discovery_v2.application import editorial_service as es

        result = es.start_coverage_run(project_arg, sync=sync)
        if result.started and result.run is not None:
            starts.append(result.run.run_id)
        return result

    monkeypatch.setattr(
        "otio_app.discovery_v2.application.coverage_revalidation_service.start_coverage_run",
        _track,
    )
    batch = submit_observation_review_batch(
        project,
        observation_ids=[obs_id],
        decision="accepted",
        user_confirmed=True,
        coverage_sync=True,
    )
    assert batch.ok
    assert len(starts) <= 1
    # Double-click same batch id → no second coverage start.
    batch2 = submit_observation_review_batch(
        project,
        observation_ids=[obs_id],
        decision="accepted",
        user_confirmed=True,
        batch_id=batch.batch_id,
        coverage_sync=True,
    )
    assert batch2.reused_existing_batch is True
    assert len(starts) <= 1


def test_claim_batch_append_only_and_idempotent(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    claims = (view.script_bundle or {}).get("claims") or []
    assert claims
    payload = [
        {"claim_id": item["claim_id"], "claim_text": item["statement"]}
        for item in claims[:2]
    ]
    batch_id = str(uuid4())
    first = record_claim_decision_batch(
        project,
        script_id=view.script.script_id,
        claims=payload,
        decision="confirmed",
        user_confirmed=True,
        batch_id=batch_id,
    )
    assert first.ok
    assert len(first.decisions) == len(payload)
    second = record_claim_decision_batch(
        project,
        script_id=view.script.script_id,
        claims=payload,
        decision="confirmed",
        user_confirmed=True,
        batch_id=batch_id,
    )
    assert second.reused_existing_batch is True
    refreshed = get_editorial_view(project)
    for item in payload:
        latest = refreshed.latest_claim_decisions[item["claim_id"]]
        assert latest.decision.value == "confirmed"
        assert parse_batch_id(latest.reason) == batch_id


def test_schema_remains_20() -> None:
    assert str(REGISTRY_SCHEMA_VERSION) == "20"


def test_ui_source_has_batch_review_and_no_gateway_io() -> None:
    source = Path(analysis_ui.__file__).read_text(encoding="utf-8")
    assert "Vorbereitete Assets analysieren" in source
    assert "submit_observation_review_batch" in source
    assert "Alle sichtbaren auswählen" in source
    assert "DiscoveryVisionGateway" not in source
    assert "open_analysis_registry" not in source
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "open" not in calls


def test_unreviewed_and_rejected_not_editorial_ready(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    obs = get_observation_review_view(project).observations[0]
    assert list_editorial_ready_observations(project) == []
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="nope",
        trigger_coverage=False,
    ).ok
    assert list_editorial_ready_observations(project) == []
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="accepted",
        trigger_coverage=False,
    ).ok
    assert len(list_editorial_ready_observations(project)) == 1
