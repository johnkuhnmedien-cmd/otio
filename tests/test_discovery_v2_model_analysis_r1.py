"""Phase 8C R1 permanent regressions / Abnahmenachweis.

Compact evidence for Handoff Testplan groups 1..15.
Only fills gaps left by test_discovery_v2_model_analysis_fake.py.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

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
from otio_app.discovery_v2.application import model_analysis_service
from otio_app.discovery_v2.application.model_analysis_service import (
    start_model_analysis,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    AnalysisPrepareAssetStatus,
    AnalysisRunStatus,
    prepared_is_not_model_analyzed,
)
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING,
    ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED,
    ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED,
    ANALYSIS_ERROR_MODEL_RESPONSE_INVALID,
    AnalysisModelAssetStatus,
    VisionGatewayRequest,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discovery_v2_analysis_prepare import (  # noqa: PLC2701
    _new_project,
    _prepare_project,
)
from test_discovery_v2_model_analysis_fake import (  # noqa: PLC2701
    _prepared_still_project,
    _request,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_fake_vision_test_hook()
    yield
    reset_fake_vision_test_hook()
    reset_analysis_job_launcher_for_tests()


def test_r1_observation_json_under_analysis_observations(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert len(observations) == 1
        relative = observations[0].relative_json_path
        assert relative.startswith("analysis/observations/")
        assert relative.endswith(".json")
        assert "../" not in relative
        assert not relative.startswith("/")
        assert "_otio/" not in relative
        assert "media/working" not in relative
    finally:
        conn.close()


def test_r1_consent_is_per_run_and_not_reused(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    first = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    second = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    denied = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=False, sync=True
    )
    assert first.run is not None and second.run is not None
    assert denied.started is False
    assert denied.error_code == "analysis_consent_required"
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        rows = conn.execute(
            """
            SELECT consent_id, run_id, acknowledged
            FROM analysis_consent_events
            WHERE project_id = ?
            ORDER BY created_at, consent_id
            """,
            (project.id,),
        ).fetchall()
        assert len(rows) == 2
        assert {str(row["run_id"]) for row in rows} == {
            first.run.run_id,
            second.run.run_id,
        }
        assert all(int(row["acknowledged"]) == 1 for row in rows)
        assert str(rows[0]["consent_id"]) != str(rows[1]["consent_id"])
    finally:
        conn.close()


def test_r1_working_media_relative_path_rejected_as_model_input(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        frame = analysis_repo.list_representative_frames_for_project(
            conn, project_id=project.id
        )[0]
        conn.execute(
            """
            UPDATE representative_frames
            SET relative_path = ?
            WHERE frame_id = ?
            """,
            (
                f"media/working/{frame.working_media_id}/copy-v1/still.jpg",
                frame.frame_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    conn2 = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        asset = analysis_repo.list_analysis_run_assets(conn2, run_id=result.run.run_id)[0]
        assert asset.status == AnalysisModelAssetStatus.FAILED
        assert asset.error_code == ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING
        assert (
            analysis_repo.list_visual_observations_for_project(
                conn2, project_id=project.id
            )
            == []
        )
    finally:
        conn2.close()


def test_r1_worker_enforces_max_frames_per_run(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    tiny = replace(vision_config.load_vision_config(), max_frames_per_run=0)
    import otio_app.discovery_v2.jobs.model_analysis_worker as worker

    monkeypatch.setattr(model_analysis_service, "load_vision_config", lambda: tiny)
    monkeypatch.setattr(worker, "load_vision_config", lambda: tiny)
    # Bypass preview gate so the worker path is exercised.
    monkeypatch.setattr(
        model_analysis_service,
        "_preview_from_assets",
        lambda selected, config: model_analysis_service.ModelAnalysisSelectionPreview(
            asset_count=len(selected),
            frame_count=1,
            total_bytes=1,
            assets=[],
        ),
    )
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        asset = analysis_repo.list_analysis_run_assets(conn, run_id=result.run.run_id)[0]
        assert asset.status == AnalysisModelAssetStatus.FAILED
        assert asset.error_code == ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED
    finally:
        conn.close()


def test_r1_invalid_response_type_is_model_response_invalid() -> None:
    def _bad(request: VisionGatewayRequest) -> dict:
        del request
        return {
            "summary": 123,
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

    set_fake_vision_test_hook(_bad)
    with pytest.raises(VisionGatewayError) as exc:
        DiscoveryVisionGateway().analyze(_request())
    assert exc.value.code == ANALYSIS_ERROR_MODEL_RESPONSE_INVALID


def test_r1_retry_exhausted_after_max_retries(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    calls = {"count": 0}

    def _always_transient(_request: VisionGatewayRequest):
        calls["count"] += 1
        return FakeVisionTransientError("temporary")

    set_fake_vision_test_hook(_always_transient)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.FAILED
    assert calls["count"] == vision_config.MAX_RETRIES + 1
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        attempts = analysis_repo.list_model_analysis_attempts(
            conn, project_id=project.id
        )
        assert attempts[-1].error_code == ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED
        assert (
            analysis_repo.list_visual_observations_for_project(
                conn, project_id=project.id
            )
            == []
        )
    finally:
        conn.close()


def test_r1_provider_error_messages_omit_secrets(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    secret = "sk-secret-key-abcdef"
    leak_markers = (secret, "api_key=", "Bearer ", "Authorization:")

    def _leaky(_request: VisionGatewayRequest):
        return FakeVisionTransientError(
            f"upstream failed Authorization: Bearer {secret} api_key={secret}"
        )

    set_fake_vision_test_hook(_leaky)
    result = start_model_analysis(
        project, asset_ids=None, consent_acknowledged=True, sync=True
    )
    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.FAILED
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        attempt = analysis_repo.list_model_analysis_attempts(
            conn, project_id=project.id
        )[-1]
        blob = " ".join(
            [
                attempt.error_code or "",
                attempt.error_message or "",
                result.run.error_summary or "",
                result.message or "",
            ]
        )
        for marker in leak_markers:
            assert marker not in blob
        assert attempt.error_code == ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED
    finally:
        conn.close()


def test_r1_gateway_disabled_is_unconfigured() -> None:
    disabled = replace(vision_config.load_vision_config(), enabled=False)
    with pytest.raises(VisionGatewayError) as exc:
        DiscoveryVisionGateway(config=disabled)
    assert exc.value.code == ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED


def test_r1_prepared_is_not_model_analyzed_guard() -> None:
    assert prepared_is_not_model_analyzed(AnalysisPrepareAssetStatus.PREPARED) is True
    assert prepared_is_not_model_analyzed(AnalysisPrepareAssetStatus.FAILED) is False


def test_r1_no_dramaturgy_or_visual_beats_in_phase8c_modules() -> None:
    for rel in (
        "otio_app/discovery_v2/domain/visual_observation.py",
        "otio_app/discovery_v2/adapters/vision_config.py",
        "otio_app/discovery_v2/adapters/vision_gateway.py",
        "otio_app/discovery_v2/adapters/vision_fake.py",
        "otio_app/discovery_v2/application/model_analysis_service.py",
        "otio_app/discovery_v2/jobs/model_analysis_worker.py",
        "otio_app/discovery_v2/ui/asset_analysis_page.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        assert "dramaturgy" not in source
        assert "visual_beat" not in source
        assert "visual beats" not in source


def test_r1_matrix_15_complete() -> None:
    matrix_path = Path(__file__).resolve().parent / "_phase8c_matrix_15.py"
    spec = importlib.util.spec_from_file_location("phase8c_matrix_15", matrix_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matrix = module.MATRIX_15
    requirements = module.MATRIX_15_REQUIREMENTS

    assert sorted(matrix) == list(range(1, 16))
    assert sorted(requirements) == list(range(1, 16))
    repo_root = Path(__file__).resolve().parents[1]
    cache: dict[str, set[str]] = {}
    for item, entries in matrix.items():
        assert requirements[item]
        assert entries, item
        for evidence_kind, node_id in entries:
            assert evidence_kind in {
                "runtime",
                "sqlite",
                "fake_adapter",
                "e2e",
                "source_ast",
            }, (item, evidence_kind)
            file_name, _, test_name = node_id.partition("::")
            assert file_name and test_name, node_id
            test_name = test_name.split("[", 1)[0]
            if file_name not in cache:
                path = repo_root / file_name
                tree = ast.parse(path.read_text(encoding="utf-8"))
                cache[file_name] = {
                    node.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                }
            assert test_name in cache[file_name], node_id


def test_r1_schema_12_to_13_still_preserves_assets(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Diff-Nachweis: Schema-Migration 12→13 bleibt datenhaltend."""
    from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
    from otio_app.discovery_v2.persistence import asset_registry_database as reg_db

    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    Image.new("RGB", (8, 8), (1, 2, 3)).save(root / "Media" / "still.jpg")
    _new_project(root, temp_db_path, name="Phase 8C R1 Schema")
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "13"
        conn.execute("UPDATE registry_schema SET schema_version = '12'")
        conn.commit()
    finally:
        conn.close()
    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == REGISTRY_SCHEMA_VERSION == "13"
        tables = {
            str(row[0])
            for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "analysis_consent_events",
            "model_analysis_attempts",
            "visual_observations",
        }.issubset(tables)
    finally:
        conn2.close()
