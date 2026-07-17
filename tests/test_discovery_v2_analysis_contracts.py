"""Phase 8A/8B/8C: Analysis-Contracts, Schema 14, Pfade, JSON — ohne Medien-I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from otio_app.discovery_v2.analysis_paths import (
    AnalysisPathError,
    analysis_frames_relative_prefix,
    analysis_manifest_json_relative_path,
    analysis_run_json_relative_path,
    assert_analysis_relative_path,
    assert_not_otio_media_path,
    is_analysis_relative_path,
    is_valid_otio_media_relative_path,
    normalize_analysis_relative_path,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_RUN_SCHEMA_VERSION,
    FORBIDDEN_PHASE8A_STATUSES,
    AnalysisInputIdentity,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunReport,
    AnalysisRunReportCounts,
    AnalysisRunStatus,
    prepared_is_not_model_analyzed,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_PROFILE_VERSION,
    IMAGE_PNG_PROFILE_VERSION,
    REMUX_WORKING_PROFILE_VERSION,
    VIDEO_H264_PROFILE_VERSION,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def discovery_project(tmp_path: Path, temp_db_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    (root / "Florida").mkdir()
    (root / "Florida" / "clip.mp4").write_bytes(b"contract-fixture")
    return create_project(
        ProjectCreate(
            name="Analysis Contracts",
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )


def test_schema_13_to_14_preserves_data(discovery_project) -> None:
    root = discovery_project.project_root_path
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "19"
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group,
                file_name, extension, media_kind, size_bytes, mtime_ns,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-keep",
                discovery_project.id,
                "Florida/clip.mp4",
                "Florida",
                "clip.mp4",
                ".mp4",
                "video",
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
        assert reg_db.read_schema_version(conn2) == "19"
        row = conn2.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?", ("asset-keep",)
        ).fetchone()
        assert row is not None
        tables = analysis_repo.analysis_table_names(conn2)
        assert "analysis_runs" in tables
        assert "analysis_run_assets" in tables
        assert "analysis_identities" in tables
        assert "technical_shots" in tables
        assert "representative_frames" in tables
        assert "visual_observations" in tables
        assert "model_analysis_attempts" in tables
        assert "analysis_consent_events" in tables
        assert "visual_observation_reviews" in tables
        assert "consent_events" not in tables
    finally:
        conn2.close()


def test_schema_init_idempotent(discovery_project) -> None:
    root = discovery_project.project_root_path
    conn1 = reg_db.get_registry_connection(root)
    v1 = reg_db.read_schema_version(conn1)
    conn1.close()
    conn2 = reg_db.get_registry_connection(root)
    v2 = reg_db.read_schema_version(conn2)
    conn2.close()
    assert v1 == v2 == REGISTRY_SCHEMA_VERSION == "19"


def test_analysis_identity_unique_and_historical(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        a = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        b = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        assert a.analysis_identity_id == b.analysis_identity_id

        c = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm2",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        d = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm1",
            output_sha256="b" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        e = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm1",
            output_sha256="a" * 64,
            processing_profile_version=VIDEO_H264_PROFILE_VERSION,
        )
        f = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=discovery_project.id,
            asset_id="a1",
            working_media_id="wm1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
            analysis_profile_version="analysis-contract-v2-future",
        )
        conn.commit()
        ids = {a.analysis_identity_id, c.analysis_identity_id, d.analysis_identity_id, e.analysis_identity_id, f.analysis_identity_id}
        assert len(ids) == 5
        listed = analysis_repo.list_analysis_identities(conn, project_id=discovery_project.id)
        assert len(listed) == 5
        assert any(x.analysis_identity_id == a.analysis_identity_id for x in listed)
    finally:
        conn.close()


def test_run_status_values_and_prepared_guard() -> None:
    for status in AnalysisRunStatus:
        assert status.value in {
            "queued",
            "running",
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        }
    assert prepared_is_not_model_analyzed(AnalysisPrepareAssetStatus.PREPARED)
    assert AnalysisPrepareAssetStatus.PREPARED.value == "prepared"
    assert "analyzing" not in {s.value for s in AnalysisPrepareAssetStatus}
    for forbidden in FORBIDDEN_PHASE8A_STATUSES:
        assert forbidden not in {s.value for s in AnalysisPrepareAssetStatus}
        assert forbidden not in {s.value for s in AnalysisRunStatus}


def test_analysis_paths_contract() -> None:
    run_rel = analysis_run_json_relative_path("run-1")
    assert run_rel == "analysis/runs/run-1.json"
    assert is_analysis_relative_path(run_rel)
    assert_analysis_relative_path(run_rel)
    man = analysis_manifest_json_relative_path("id-1")
    assert man.startswith("analysis/manifests/")
    assert analysis_frames_relative_prefix() == "analysis/frames"

    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("/tmp/x")
    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("analysis/../secret")
    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("_otio/foo")
    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("_otio_v2/analysis/runs/x.json")
    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("media/working/a/b/copy-v1/a.mp4")
    with pytest.raises(AnalysisPathError):
        normalize_analysis_relative_path("analysis/media/working/x")

    frame = "analysis/frames/wm1/frame-sample-v1/shot1/f1.jpg"
    assert is_analysis_relative_path(frame)
    assert is_valid_otio_media_relative_path(frame) is False
    with pytest.raises(AnalysisPathError):
        assert_not_otio_media_path(frame)


def test_json_report_roundtrip_and_guards() -> None:
    report = AnalysisRunReport(
        run_id="r1",
        project_id="p1",
        scope="prepare",
        status=AnalysisRunStatus.COMPLETED,
        analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        input_identities=[
            AnalysisInputIdentity(
                project_id="p1",
                asset_id="a1",
                working_media_id="wm1",
                validation_id="v1",
                source_sha256="a" * 64,
                output_sha256="b" * 64,
                processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                media_kind="video",
            )
        ],
        counts=AnalysisRunReportCounts(total_assets=1, prepared_assets=1),
        created_at=_now(),
        completed_at=_now(),
    )
    raw = analysis_repo.serialize_analysis_run_report(report)
    parsed = analysis_repo.parse_analysis_run_report(raw)
    assert parsed.run_id == "r1"
    assert parsed.schema_version == ANALYSIS_RUN_SCHEMA_VERSION
    assert parsed.input_identities[0].analysis_profile_version == (
        ANALYSIS_CONTRACT_PROFILE_VERSION
    )

    with pytest.raises(ValidationError):
        AnalysisRunReport.model_validate(
            {
                **report.model_dump(mode="json"),
                "schema_version": "999",
            }
        )

    bad = report.model_dump(mode="json")
    bad["errors"] = [{"asset_id": "a", "error_code": "x", "error_message": "/abs/path"}]
    # absolute path in error_message is not a path field — only path keys blocked.
    # Force a path-like key:
    bad["artifact_path"] = "/tmp/abs.json"
    with pytest.raises(ValueError):
        analysis_repo.parse_analysis_run_report(bad)


def test_phase7_profiles_unchanged() -> None:
    assert COPY_WORKING_PROFILE_VERSION == "copy-v1"
    assert REMUX_WORKING_PROFILE_VERSION == "remux-mp4-v1"
    assert VIDEO_H264_PROFILE_VERSION == "video-h264-v1"
    assert IMAGE_PNG_PROFILE_VERSION == "image-png-v1"
    assert ANALYSIS_CONTRACT_PROFILE_VERSION == "analysis-contract-v1"


def test_persist_run_contracts_without_executing(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        run = AnalysisRun(
            run_id=str(uuid4()),
            project_id=discovery_project.id,
            scope="prepare",
            status=AnalysisRunStatus.QUEUED,
            created_at=_now(),
            total_assets=0,
        )
        analysis_repo.insert_analysis_run(conn, run)
        conn.commit()
        loaded = analysis_repo.get_analysis_run(conn, run_id=run.run_id)
        assert loaded is not None
        assert loaded.status == AnalysisRunStatus.QUEUED
    finally:
        conn.close()


def test_no_model_names_in_analysis_domain() -> None:
    source = Path("otio_app/discovery_v2/domain/asset_analysis.py").read_text(
        encoding="utf-8"
    )
    for needle in ("gemini", "openai", "anthropic", "openrouter", "claude"):
        assert needle not in source.lower()
