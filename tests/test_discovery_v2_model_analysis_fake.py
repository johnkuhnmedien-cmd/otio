"""Phase 8C: fake vision gateway and model analysis foundation."""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from otio_app.discovery_v2.adapters import vision_config
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.vision_fake import (
    FakeVisionTransientError,
    reset_fake_vision_test_hook,
    set_fake_vision_test_hook,
)
from otio_app.discovery_v2.adapters.vision_gateway import (
    DiscoveryVisionGateway,
    VisionGatewayError,
)
from otio_app.discovery_v2.analysis_paths import resolve_analysis_relative_path
from otio_app.discovery_v2.application import model_analysis_service
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    reconcile_orphaned_analysis_run,
)
from otio_app.discovery_v2.application.model_analysis_service import (
    get_model_analysis_view,
    preview_model_analysis_selection,
    start_model_analysis,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_MODEL_PROFILE,
    ANALYSIS_RUN_SCOPE_MODEL,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.media_intake import COPY_WORKING_PROFILE_VERSION
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH,
    ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING,
    ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH,
    AnalysisModelAssetStatus,
    VisionFramePart,
    VisionGatewayRequest,
    VisualObservation,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.models import ProjectMode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discovery_v2_analysis_prepare import (  # noqa: PLC2701 - shared helpers
    _FakeStreamlit,
    _new_project,
    _now,
    _prepare_project,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_fake_vision_test_hook()
    yield
    reset_fake_vision_test_hook()
    reset_analysis_job_launcher_for_tests()


def _prepared_still_project(tmp_path: Path, temp_db_path: Path):
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    Image.new("RGB", (32, 20), (10, 20, 30)).save(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="Phase 8C Fake")
    prepared = _prepare_project(project)
    assert prepared.run is not None
    assert prepared.run.status == AnalysisRunStatus.COMPLETED
    return project


def _request() -> VisionGatewayRequest:
    config = vision_config.load_vision_config()
    return VisionGatewayRequest(
        project_id="project-1",
        run_id="run-1",
        asset_id="asset-1",
        analysis_identity_id="identity-1",
        media_kind="image",
        prompt="prompt",
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        frames=[
            VisionFramePart(
                frame_id="frame-1",
                relative_path="analysis/frames/wm/frame-sample-v1/still/frame-1.jpg",
                mime_type="image/jpeg",
                frame_sha256="a" * 64,
                file_size_bytes=1,
                ordinal=0,
            )
        ],
    )


def test_schema_13_to_14_preserves_data_and_is_idempotent(tmp_path: Path, temp_db_path: Path) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path)
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "14"
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group, file_name,
                extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-keep",
                project.id,
                "Media/still.jpg",
                "Media",
                "still.jpg",
                ".jpg",
                "image",
                1,
                1,
                _now().isoformat(),
                _now().isoformat(),
            ),
        )
        conn.execute("UPDATE registry_schema SET schema_version = '13'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == REGISTRY_SCHEMA_VERSION == "14"
        assert conn2.execute("SELECT COUNT(*) FROM assets WHERE asset_id = 'asset-keep'").fetchone()[0] == 1
    finally:
        conn2.close()
    conn3 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn3) == "14"
    finally:
        conn3.close()


def test_schema_14_tables_exist_without_dramaturgy_or_visual_beats(tmp_path: Path, temp_db_path: Path) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _new_project(root, temp_db_path)
    conn = reg_db.get_registry_connection(root)
    try:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"analysis_consent_events", "model_analysis_attempts", "visual_observations", "visual_observation_reviews"}.issubset(tables)
        assert "dramaturgy" not in tables
        assert "visual_beats" not in tables
    finally:
        conn.close()


def test_visual_observation_rejects_extra_fields_and_empty_evidence() -> None:
    payload = {
        "summary": "x",
        "visible_subjects": [],
        "actions": [],
        "setting": None,
        "indoor_outdoor": "unknown",
        "day_night": "unknown",
        "people_present": None,
        "crowd_level": "unknown",
        "camera_scale": "unknown",
        "camera_motion_hint": "unknown",
        "visual_quality_notes": [],
        "readable_text_present": None,
        "readable_text_summary": None,
        "possible_location_clues": [],
        "geographic_confidence": 0.0,
        "landmark_candidates": [],
        "weather_visible": None,
        "safety_or_sensitive_content": [],
        "possible_synthetic_indicators": [],
        "synthetic_confidence": 0.0,
        "uncertainty_notes": [],
        "evidence_frame_ids": ["frame-1"],
        "editorial_signals": [],
    }
    with pytest.raises(ValidationError):
        VisualObservation.model_validate({**payload, "extra": "nope"})
    with pytest.raises(ValidationError):
        VisualObservation.model_validate({**payload, "evidence_frame_ids": []})


def test_gateway_uses_fake_only_and_unknown_provider_errors() -> None:
    response = DiscoveryVisionGateway().analyze(_request())
    assert response.provider == "fake"
    bad_config = replace(vision_config.load_vision_config(), provider="other")
    with pytest.raises(VisionGatewayError) as exc:
        DiscoveryVisionGateway(config=bad_config)
    assert exc.value.code == "vision_model_unavailable"


def test_fake_e2e_start_with_consent_persists_observation(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)
        assert len(observations) == 1
        payload = json.loads(observations[0].observation_json)
        assert payload["provider"] == "fake"
        assert payload["observation"]["evidence_frame_ids"]
    finally:
        conn.close()


def test_without_consent_returns_required_error(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=False, sync=True)
    assert result.started is False
    assert result.error_code == ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED


def test_new_modules_have_no_real_provider_or_http_imports() -> None:
    for rel in (
        "otio_app/discovery_v2/adapters/vision_config.py",
        "otio_app/discovery_v2/adapters/vision_gateway.py",
        "otio_app/discovery_v2/adapters/vision_fake.py",
        "otio_app/discovery_v2/application/model_analysis_service.py",
        "otio_app/discovery_v2/jobs/model_analysis_worker.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        for needle in ("gemini", "openai", "anthropic", "openrouter", "xai", "httpx", "requests", "ffmpeg", "ffprobe"):
            assert needle not in source


def test_worker_imports_gateway_not_fake_directly() -> None:
    path = Path("otio_app/discovery_v2/jobs/model_analysis_worker.py")
    source = path.read_text(encoding="utf-8")
    assert "vision_gateway" in source
    assert "vision_fake" not in source
    tree = ast.parse(source)
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert "otio_app.discovery_v2.adapters.vision_gateway" in imports


def test_invalid_evidence_from_fake_hook_fails_without_success_persist(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    def _bad(request: VisionGatewayRequest) -> dict:
        del request
        return {
            "summary": "bad evidence",
            "visible_subjects": [],
            "actions": [],
            "setting": None,
            "indoor_outdoor": "unknown",
            "day_night": "unknown",
            "people_present": None,
            "crowd_level": "unknown",
            "camera_scale": "unknown",
            "camera_motion_hint": "unknown",
            "visual_quality_notes": [],
            "readable_text_present": None,
            "readable_text_summary": None,
            "possible_location_clues": [],
            "geographic_confidence": 0.0,
            "landmark_candidates": [],
            "weather_visible": None,
            "safety_or_sensitive_content": [],
            "possible_synthetic_indicators": [],
            "synthetic_confidence": 0.0,
            "uncertainty_notes": [],
            "evidence_frame_ids": ["not-a-request-frame"],
            "editorial_signals": [],
        }
    set_fake_vision_test_hook(_bad)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.FAILED
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        assert analysis_repo.list_visual_observations_for_project(conn, project_id=project.id) == []
        attempts = analysis_repo.list_model_analysis_attempts(conn, project_id=project.id)
        assert attempts[-1].error_code == ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH
    finally:
        conn.close()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("missing", ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING),
        ("hash", ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH),
    ],
)
def test_frame_missing_and_hash_mismatch(tmp_path: Path, temp_db_path: Path, mode: str, expected: str) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        frame = analysis_repo.list_representative_frames_for_project(conn, project_id=project.id)[0]
    finally:
        conn.close()
    frame_path = resolve_analysis_relative_path(project.project_root_path, frame.relative_path)
    if mode == "missing":
        frame_path.unlink()
    else:
        frame_path.write_bytes(b"changed")
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.run is not None
    conn2 = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        asset = analysis_repo.list_analysis_run_assets(conn2, run_id=result.run.run_id)[0]
        assert asset.status == AnalysisModelAssetStatus.FAILED
        assert asset.error_code == expected
    finally:
        conn2.close()


def test_frame_limit_exceeded_from_config(tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    tiny = replace(vision_config.load_vision_config(), max_frames_per_run=0)
    monkeypatch.setattr(model_analysis_service, "load_vision_config", lambda: tiny)
    preview = preview_model_analysis_selection(project, asset_ids=None)
    assert preview.error_code == ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED


def test_retry_succeeds_once(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    calls = {"count": 0}
    def _flaky(_request: VisionGatewayRequest):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeVisionTransientError("temporary")
        return None
    set_fake_vision_test_hook(_flaky)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    assert calls["count"] == 2


def test_cache_reuses_identical_attempt(tmp_path: Path, temp_db_path: Path) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    first = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    second = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert first.run is not None and second.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)
        assert len(observations) == 1
        latest_asset = analysis_repo.list_analysis_run_assets(conn, run_id=second.run.run_id)[0]
        assert latest_asset.status == AnalysisModelAssetStatus.REUSED
        attempts = analysis_repo.list_model_analysis_attempts(conn, project_id=project.id)
        assert [attempt.status for attempt in attempts][-1] == "reused"
    finally:
        conn.close()


def test_frame_fingerprint_change_creates_new_observation(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    first = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert first.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        frames = analysis_repo.list_representative_frames(
            conn,
            analysis_identity_id=analysis_repo.list_analysis_run_assets(
                conn, run_id=first.run.run_id
            )[0].analysis_identity_id
            or "",
        )
        assert frames
        frame = frames[0]
        frame_path = resolve_analysis_relative_path(
            project.project_root_path, frame.relative_path
        )
        Image.new("RGB", (32, 20), (200, 10, 10)).save(frame_path)
        import hashlib

        new_sha = hashlib.sha256(frame_path.read_bytes()).hexdigest()
        conn.execute(
            """
            UPDATE representative_frames
            SET frame_sha256 = ?, file_size_bytes = ?
            WHERE frame_id = ?
            """,
            (new_sha, frame_path.stat().st_size, frame.frame_id),
        )
        conn.commit()
    finally:
        conn.close()
    second = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert second.run is not None
    conn2 = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(
            conn2, project_id=project.id
        )
        assert len(observations) == 2
        fingerprints = {obs.frame_hash_fingerprint for obs in observations}
        assert len(fingerprints) == 2
        latest_asset = analysis_repo.list_analysis_run_assets(
            conn2, run_id=second.run.run_id
        )[0]
        assert latest_asset.status == AnalysisModelAssetStatus.COMPLETED
    finally:
        conn2.close()


def test_new_prompt_version_creates_new_observation(tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    first = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert first.run is not None
    new_config = replace(vision_config.load_vision_config(), prompt_version="vision-prompt-v2")
    import otio_app.discovery_v2.jobs.model_analysis_worker as worker
    monkeypatch.setattr(model_analysis_service, "load_vision_config", lambda: new_config)
    monkeypatch.setattr(worker, "load_vision_config", lambda: new_config)
    second = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert second.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)
        assert len(observations) == 2
        assert {obs.prompt_version for obs in observations} == {"vision-prompt-v1", "vision-prompt-v2"}
    finally:
        conn.close()


def test_orphan_recovery_marks_model_run_interrupted(tmp_path: Path, temp_db_path: Path) -> None:
    from otio_app.discovery_v2.domain.visual_observation import ModelAnalysisAttemptRecord

    project = _prepared_still_project(tmp_path, temp_db_path)
    config = vision_config.load_vision_config()
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        prepared_run = analysis_repo.get_latest_analysis_run(conn, project_id=project.id, scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY)
        assert prepared_run is not None
        prepared_asset = analysis_repo.list_analysis_run_assets(conn, run_id=prepared_run.run_id)[0]
        assert prepared_asset.analysis_identity_id is not None
        run = AnalysisRun(
            run_id="model-orphan",
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_MODEL,
            analysis_profile_version=ANALYSIS_MODEL_PROFILE,
            status=AnalysisRunStatus.RUNNING,
            created_at=_now(),
            started_at=_now(),
            total_assets=1,
        )
        analysis_repo.insert_analysis_run(conn, run)
        analysis_repo.insert_analysis_run_asset(
            conn,
            prepared_asset.model_copy(update={"run_id": run.run_id, "status": AnalysisModelAssetStatus.ANALYZING}),
        )
        analysis_repo.insert_model_analysis_attempt(
            conn,
            ModelAnalysisAttemptRecord(
                attempt_id="attempt-orphan",
                analysis_identity_id=prepared_asset.analysis_identity_id,
                project_id=project.id,
                asset_id=prepared_asset.asset_id,
                run_id=run.run_id,
                provider=config.provider,
                model_identifier=config.model_identifier,
                gateway_version=config.gateway_version,
                prompt_version=config.prompt_version,
                response_schema_version=config.response_schema_version,
                status="running",
                attempt_number=1,
                frame_count=1,
                frame_hash_fingerprint="f" * 64,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    updated = reconcile_orphaned_analysis_run(project)
    assert updated is not None
    assert updated.status == AnalysisRunStatus.FAILED
    conn2 = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        asset = analysis_repo.list_analysis_run_assets(conn2, run_id="model-orphan")[0]
        assert asset.status == AnalysisModelAssetStatus.INTERRUPTED
        attempt = analysis_repo.list_model_analysis_attempts(conn2, run_id="model-orphan")[0]
        assert attempt.status == "interrupted"
        assert attempt.error_code == "worker_interrupted"
        assert attempt.completed_at is not None
    finally:
        conn2.close()


def test_active_model_blocks_prepare_or_model_start(tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        run = AnalysisRun(
            run_id="active-model",
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_MODEL,
            analysis_profile_version=ANALYSIS_MODEL_PROFILE,
            status=AnalysisRunStatus.RUNNING,
            created_at=_now(),
        )
        analysis_repo.insert_analysis_run(conn, run)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(model_analysis_service, "reconcile_orphaned_analysis_run", lambda _project: None)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.started is False
    assert result.run is not None
    assert result.run.run_id == "active-model"


def test_ui_button_false_no_start_and_view_has_no_media_io(tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    starts: list[Any] = []
    fake_st = _FakeStreamlit(clicked=False)
    monkeypatch.setattr(analysis_ui, "st", fake_st)
    monkeypatch.setattr(analysis_ui, "active_discovery_project", lambda: project)
    monkeypatch.setattr(analysis_ui, "start_model_analysis", lambda *args, **kwargs: starts.append((args, kwargs)))
    original_stat = Path.stat
    def _guarded_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if "/analysis/frames/" in self.as_posix():
            raise AssertionError("model analysis view must not stat frame files")
        return original_stat(self, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "stat", _guarded_stat)
        view = get_model_analysis_view(project)
    assert view.ok and view.prepared_assets
    analysis_ui.render_discovery_asset_analysis_page()
    assert starts == []


def test_no_hard_coded_gemini_model_in_domain_or_application() -> None:
    for rel in (
        "otio_app/discovery_v2/domain/asset_analysis.py",
        "otio_app/discovery_v2/domain/visual_observation.py",
        "otio_app/discovery_v2/application/model_analysis_service.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        assert "gemini" not in source
        assert "gemini-" not in source
