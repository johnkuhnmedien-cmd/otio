"""Phase 8B: local analysis-prepare tests for shots and representative frames."""

from __future__ import annotations

import ast
import json
import math
import sqlite3
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from otio_app.discovery_v2.adapters.ffmpeg_runner import (
    ffmpeg_available,
    ffmpeg_encoder_available,
    run_ffmpeg,
)
from otio_app.discovery_v2.adapters.frame_sample import (
    FrameSampleError,
    black_frame_candidate_timestamps,
    prepare_still_preview,
    representative_timestamp_for_shot,
    select_representative_timestamps,
)
from otio_app.discovery_v2.adapters.frame_signals import FrameSignals, compute_frame_signals
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.shot_detect import (
    ShotDetectError,
    normalize_shot_boundaries,
)
from otio_app.discovery_v2.analysis_paths import (
    AnalysisPathError,
    analysis_frame_relative_path,
    analysis_temp_dir,
    assert_not_otio_media_path,
    is_valid_otio_media_relative_path,
    normalize_analysis_relative_path,
    resolve_analysis_relative_path,
)
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    reconcile_orphaned_analysis_run,
)
from otio_app.discovery_v2.application.analysis_prepare_service import (
    AnalysisPrepareEligibilityItemView,
    AnalysisPrepareStatusView,
    start_analysis_prepare,
)
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import start_copy_intake
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.technical_validation_service import (
    start_technical_validation,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_PREPARE_PROFILE_VERSION,
    FRAME_SAMPLE_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
    AnalysisEligibility,
    AnalysisIdentityRecord,
    AnalysisInputIdentity,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunReport,
    AnalysisRunStatus,
    RepresentativeFrameRecord,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.media_intake import COPY_WORKING_PROFILE_VERSION
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _reset_launchers() -> None:
    from otio_app.discovery_v2.adapters.analysis_job_launcher import (
        reset_analysis_job_launcher_for_tests,
    )

    reset_intake_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()
    yield
    reset_intake_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


@dataclass(frozen=True)
class _Shot:
    ordinal: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def _shot(ordinal: int, start: float, end: float) -> _Shot:
    return _Shot(ordinal=ordinal, start_seconds=start, end_seconds=end)


def _new_project(root: Path, temp_db_path: Path, *, name: str = "Phase 8B") -> Project:
    return create_project(
        ProjectCreate(
            name=name,
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Media"],
        selected_asset_subdirs=["Media"],
    )


def _import_validate_plan_and_copy(project: Project) -> None:
    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    confirm_selection(project, snap, draft, acknowledged=True)
    import_confirmed_selection(project)
    validation = start_technical_validation(project, sync=True)
    assert validation.started and validation.run is not None
    plan = create_intake_plan(project)
    assert plan.created and plan.plan is not None
    copied = start_copy_intake(project, sync=True)
    assert copied.started and copied.run is not None


def _prepare_project(project: Project):
    _import_validate_plan_and_copy(project)
    result = start_analysis_prepare(project, sync=True)
    assert result.run is not None
    return result


def _all_analysis_artifacts(
    project: Project,
) -> tuple[list[TechnicalShotRecord], list[RepresentativeFrameRecord], list[AnalysisRunAsset]]:
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        shots = analysis_repo.list_technical_shots_for_project(
            conn, project_id=project.id
        )
        frames = analysis_repo.list_representative_frames_for_project(
            conn, project_id=project.id
        )
        runs = analysis_repo.list_analysis_runs(conn, project_id=project.id)
        assets: list[AnalysisRunAsset] = []
        for run in runs:
            assets.extend(analysis_repo.list_analysis_run_assets(conn, run_id=run.run_id))
        return shots, frames, assets
    finally:
        conn.close()


def _working_media_for_project(project: Project):
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        return copy_repo.list_working_media(conn, project_id=project.id)
    finally:
        conn.close()


def _require_ffmpeg() -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg executable is not available")
    if not ffmpeg_encoder_available("libx264"):
        pytest.skip("ffmpeg libx264 encoder is not available")


def _run_fixture_ffmpeg(argv: list[str], *, timeout_sec: int = 180) -> None:
    _require_ffmpeg()
    result = run_ffmpeg(argv, timeout_sec=timeout_sec)
    if result.returncode != 0:
        pytest.skip(f"ffmpeg fixture failed: {result.stderr or result.stdout}")


def _make_color_video(
    path: Path,
    colors: list[str],
    *,
    segment_duration: float = 0.7,
    width: int = 96,
    height: int = 64,
    fps: int = 10,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for color in colors:
        argv.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={width}x{height}:r={fps}:d={segment_duration}",
            ]
        )
    streams = "".join(f"[{i}:v]" for i in range(len(colors)))
    argv.extend(
        [
            "-filter_complex",
            f"{streams}concat=n={len(colors)}:v=1:a=0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    _run_fixture_ffmpeg(argv)


def _make_static_video(
    path: Path,
    *,
    duration: float,
    width: int = 96,
    height: int = 64,
    fps: int = 2,
    color: str = "blue",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_fixture_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:r={fps}:d={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        timeout_sec=240,
    )


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)


# --- Unit: shot boundaries -------------------------------------------------


def test_normalize_shot_boundaries_three_cuts_and_bounds() -> None:
    assert normalize_shot_boundaries(4.0, [1.0, 2.0, 3.0]) == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
        (3.0, 4.0),
    ]
    assert normalize_shot_boundaries(2.0, [-1.0, 0.0, 0.5, 2.0, 9.0]) == [
        (0.0, 0.5),
        (0.5, 2.0),
    ]


def test_normalize_shot_boundaries_dedupes_and_merges_short_segments() -> None:
    # 1.03 is within the 0.04s dedupe window from 1.0; 1.05 survives.
    assert normalize_shot_boundaries(2.0, [1.0, 1.03, 1.05]) == [
        (0.0, 1.05),
        (1.05, 2.0),
    ]
    assert normalize_shot_boundaries(2.0, [0.2, 1.0]) == [
        (0.0, 1.0),
        (1.0, 2.0),
    ]
    assert normalize_shot_boundaries(2.0, [1.0, 1.2]) == [
        (0.0, 1.2),
        (1.2, 2.0),
    ]


def test_normalize_shot_boundaries_long_no_cut_and_short_video() -> None:
    assert normalize_shot_boundaries(12.0, []) == [(0.0, 12.0)]

    long_segments = normalize_shot_boundaries(65.0, [])
    assert len(long_segments) == 3
    assert long_segments[0][0] == 0.0
    assert long_segments[-1][1] == 65.0
    assert all((end - start) <= 30.0 for start, end in long_segments)

    assert normalize_shot_boundaries(0.79, [0.1, 0.4]) == [(0.0, 0.79)]


@pytest.mark.parametrize("duration", [0.0, -1.0, math.inf, math.nan])
def test_normalize_shot_boundaries_invalid_duration(duration: float) -> None:
    with pytest.raises(ShotDetectError) as exc:
        normalize_shot_boundaries(duration, [1.0])
    assert exc.value.code == "invalid_shot_boundaries"


# --- Unit: frame sampling --------------------------------------------------


def test_select_representative_timestamps_midpoint_and_overview_dedupe() -> None:
    one = [_shot(0, 0.0, 10.0)]
    assert select_representative_timestamps(one) == [(one[0], 5.0)]
    assert representative_timestamp_for_shot(_shot(0, 0.0, 0.10)) == pytest.approx(0.05)

    two = [_shot(0, 0.0, 2.0), _shot(1, 8.0, 10.0)]
    selected = select_representative_timestamps(two)
    assert selected == [(two[0], 1.0), (two[1], 9.0), (None, 5.0)]


def test_select_representative_timestamps_frame_cap_rules() -> None:
    twenty_three = [
        *[_shot(i, float(i), float(i) + 0.5) for i in range(11)],
        *[_shot(i, 100.0 + float(i), 100.5 + float(i)) for i in range(11, 23)],
    ]
    selected_23 = select_representative_timestamps(twenty_three)
    assert len(selected_23) == 24
    assert selected_23[-1][0] is None

    twenty_four = [_shot(i, float(i), float(i) + 0.5) for i in range(24)]
    selected_24 = select_representative_timestamps(twenty_four)
    assert len(selected_24) == 24
    assert all(shot is not None for shot, _ in selected_24)

    mixed = [
        _shot(5, 5.0, 6.0),
        *[_shot(i, float(i), float(i) + (3.0 if i % 2 == 0 else 1.0)) for i in range(26)],
    ]
    selected_many = select_representative_timestamps(mixed)
    ordinals = [shot.ordinal for shot, _ in selected_many if shot is not None]
    assert len(selected_many) == 24
    assert ordinals == sorted(ordinals)
    assert 0 in ordinals and 2 in ordinals


def test_select_representative_timestamps_rejects_bad_shot() -> None:
    with pytest.raises(FrameSampleError):
        select_representative_timestamps([{"ordinal": 0, "start": 1.0, "end": 1.0}])


def test_black_frame_candidate_order_and_clamping() -> None:
    candidates = black_frame_candidate_timestamps(_shot(0, 10.0, 12.0))
    assert candidates == [11.0, 10.75, 11.25]
    short = black_frame_candidate_timestamps(_shot(0, 0.0, 0.10))
    assert short[0] == pytest.approx(0.05)
    assert all(0.0 <= value <= 0.10 for value in short)


def test_worker_uses_alternate_candidate_when_midpoint_is_black(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker
    from otio_app.discovery_v2.adapters.media_probe import NormalizedMediaProbe

    timestamps: list[float] = []

    def _fake_extract(_input, output, timestamp, **_kwargs):
        timestamps.append(timestamp)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(f"frame-{len(timestamps)}".encode("ascii"))
        return ["ffmpeg", "-ss", str(timestamp)]

    def _fake_signals(_path):
        is_black = len(timestamps) == 1
        return FrameSignals(
            brightness_mean=0.0 if is_black else 0.5,
            black_fraction=1.0 if is_black else 0.0,
            is_black=is_black,
            sharpness_score=1.0,
            pixel_sha256=("a" if is_black else "b") * 64,
            frame_sha256=("c" if is_black else "d") * 64,
        )

    monkeypatch.setattr(worker, "extract_video_frame_jpeg", _fake_extract)
    monkeypatch.setattr(worker, "_compute_signals", _fake_signals)
    monkeypatch.setattr(worker, "_frame_dimensions", lambda _path: (64, 36))
    monkeypatch.setattr(worker, "new_frame_id", lambda: "frame-fixed")

    shot = TechnicalShotRecord(
        shot_id="shot-1",
        analysis_identity_id="identity-1",
        project_id="project-1",
        asset_id="asset-1",
        working_media_id="wm-1",
        ordinal=0,
        start_seconds=10.0,
        end_seconds=12.0,
        duration_seconds=2.0,
        created_at=_now(),
    )
    run = AnalysisRun(
        run_id="run-1",
        project_id="project-1",
        scope="analysis_prepare_only",
        status=AnalysisRunStatus.RUNNING,
        created_at=_now(),
    )
    asset = AnalysisRunAsset(
        run_id=run.run_id,
        asset_id="asset-1",
        working_media_id="wm-1",
        validation_id="validation-1",
        source_sha256="a" * 64,
        output_sha256="b" * 64,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        media_kind="video",
        status=AnalysisPrepareAssetStatus.EXTRACTING_FRAMES,
        analysis_identity_id="identity-1",
    )

    prepared = worker._extract_representative_video_frame(
        project_root=tmp_path,
        run=run,
        asset=asset,
        working_path=tmp_path / "working.mp4",
        probe=NormalizedMediaProbe(media_kind="video", rotation_degrees=None),
        shot=shot,
        ordinal=0,
        timestamp=11.0,
    )

    assert timestamps[:2] == [11.0, 10.75]
    assert prepared.record.timestamp_seconds == pytest.approx(10.75)
    assert prepared.record.is_black is False


# --- Unit: frame signals / still preview -----------------------------------


def test_compute_frame_signals_on_synthetic_images(tmp_path: Path) -> None:
    black = tmp_path / "black.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(black)
    black_signals = compute_frame_signals(black)
    assert black_signals.brightness_mean == 0.0
    assert black_signals.black_fraction == 1.0
    assert black_signals.is_black is True
    assert len(black_signals.pixel_sha256) == 64
    assert len(black_signals.frame_sha256) == 64

    edge = tmp_path / "edge.png"
    image = Image.new("RGB", (16, 16), (255, 255, 255))
    for x in range(8):
        for y in range(16):
            image.putpixel((x, y), (0, 0, 0))
    image.save(edge)
    edge_signals = compute_frame_signals(edge)
    assert 0.45 < edge_signals.brightness_mean < 0.55
    assert edge_signals.black_fraction == pytest.approx(0.5)
    assert edge_signals.is_black is False
    assert edge_signals.sharpness_score > 0


def test_prepare_still_preview_opaque_jpeg_and_alpha_png(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.png"
    Image.new("RGB", (32, 20), (10, 200, 30)).save(opaque)
    opaque_result = prepare_still_preview(opaque, tmp_path / "opaque-preview.png")
    assert opaque_result.output_format == "JPEG"
    assert opaque_result.output_path.suffix == ".jpg"
    assert opaque_result.has_alpha is False
    with Image.open(opaque_result.output_path) as preview:
        assert preview.format == "JPEG"
        assert preview.mode == "RGB"

    alpha = tmp_path / "alpha.png"
    rgba = Image.new("RGBA", (32, 20), (10, 20, 30, 0))
    rgba.putpixel((0, 0), (10, 20, 30, 255))
    rgba.save(alpha)
    alpha_result = prepare_still_preview(alpha, tmp_path / "alpha-preview.jpg")
    assert alpha_result.output_format == "PNG"
    assert alpha_result.output_path.suffix == ".png"
    assert alpha_result.has_alpha is True
    with Image.open(alpha_result.output_path) as preview:
        assert preview.format == "PNG"
        assert preview.mode == "RGBA"
        assert preview.getpixel((1, 1))[3] == 0


# --- Schema / contracts ----------------------------------------------------


def test_schema_13_to_14_preserves_data_and_is_idempotent(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    (root / "Media" / "clip.mp4").write_bytes(b"placeholder")
    project = _new_project(root, temp_db_path)

    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "19"
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
                "Media/clip.mp4",
                "Media",
                "clip.mp4",
                ".mp4",
                "video",
                1,
                1,
                _now().isoformat(),
                _now().isoformat(),
            ),
        )
        conn.execute("DROP TABLE representative_frames")
        conn.execute("DROP TABLE technical_shots")
        conn.execute("UPDATE registry_schema SET schema_version = '13'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == "19"
        assert conn2.execute(
            "SELECT COUNT(*) FROM assets WHERE asset_id = 'asset-keep'"
        ).fetchone()[0] == 1
        tables = {
            str(row[0])
            for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"technical_shots", "representative_frames"}.issubset(tables)
        assert {
            "visual_observations",
            "model_analysis_attempts",
            "analysis_consent_events",
            "visual_observation_reviews",
        }.issubset(tables)
        for forbidden in (
            "consent_events",
            "dramaturgy",
        ):
            assert forbidden not in tables
    finally:
        conn2.close()

    conn3 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn3) == REGISTRY_SCHEMA_VERSION == "19"
    finally:
        conn3.close()


def test_analysis_shot_and_frame_unique_keys(tmp_path: Path, temp_db_path: Path) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path)
    conn = reg_db.get_registry_connection(root)
    try:
        identity = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        conn.commit()
        shot = TechnicalShotRecord(
            shot_id="shot-1",
            analysis_identity_id=identity.analysis_identity_id,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-1",
            ordinal=0,
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
            created_at=_now(),
        )
        analysis_repo.insert_technical_shot(conn, shot)
        with pytest.raises(sqlite3.IntegrityError):
            analysis_repo.insert_technical_shot(
                conn, shot.model_copy(update={"shot_id": "shot-2"})
            )
        frame = _frame_record(identity, project, ordinal=0, shot_id=None)
        analysis_repo.insert_representative_frame(conn, frame)
        with pytest.raises(sqlite3.IntegrityError):
            analysis_repo.insert_representative_frame(
                conn, frame.model_copy(update={"frame_id": "frame-2"})
            )
    finally:
        conn.close()


def _frame_record(
    identity: AnalysisIdentityRecord,
    project: Project,
    *,
    ordinal: int,
    shot_id: str | None,
    timestamp_seconds: float | None = None,
) -> RepresentativeFrameRecord:
    return RepresentativeFrameRecord(
        frame_id=f"frame-{ordinal}",
        analysis_identity_id=identity.analysis_identity_id,
        project_id=project.id,
        asset_id=identity.asset_id,
        working_media_id=identity.working_media_id,
        shot_id=shot_id,
        ordinal=ordinal,
        timestamp_seconds=timestamp_seconds,
        relative_path=analysis_frame_relative_path(
            working_media_id=identity.working_media_id,
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            shot_or_still="still" if shot_id is None else shot_id,
            frame_id=f"frame-{ordinal}",
            extension="jpg",
        ),
        frame_sha256="b" * 64,
        pixel_sha256="c" * 64,
        file_size_bytes=1,
        width=1,
        height=1,
        brightness_mean=0.1,
        black_fraction=0.0,
        sharpness_score=0.0,
        is_black=False,
        created_at=_now(),
    )


def test_frame_timestamp_contract_still_null_but_shot_frame_requires_timestamp() -> None:
    base = dict(
        frame_id="frame-1",
        analysis_identity_id="identity-1",
        project_id="project-1",
        asset_id="asset-1",
        working_media_id="wm-1",
        ordinal=0,
        relative_path="analysis/frames/wm-1/frame-sample-v1/still/frame-1.jpg",
        frame_sha256="a" * 64,
        pixel_sha256="b" * 64,
        file_size_bytes=1,
        width=10,
        height=10,
        brightness_mean=0.0,
        black_fraction=0.0,
        sharpness_score=0.0,
        is_black=False,
        created_at=_now(),
    )
    still = RepresentativeFrameRecord(**base, shot_id=None, timestamp_seconds=None)
    assert still.timestamp_seconds is None
    with pytest.raises(ValidationError):
        RepresentativeFrameRecord(**base, shot_id="shot-1", timestamp_seconds=None)
    shot_frame = RepresentativeFrameRecord(
        **{**base, "frame_id": "frame-2"},
        shot_id="shot-1",
        timestamp_seconds=0.5,
    )
    assert shot_frame.timestamp_seconds == 0.5


def test_analysis_paths_are_under_analysis_frames_not_otio_media() -> None:
    rel = analysis_frame_relative_path(
        working_media_id="wm-1",
        sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
        shot_or_still="shot-1",
        frame_id="frame-1",
        extension="jpeg",
    )
    assert rel == "analysis/frames/wm-1/frame-sample-v1/shot-1/frame-1.jpg"
    assert "_otio" not in rel
    assert "media/working" not in rel
    assert is_valid_otio_media_relative_path(rel) is False
    with pytest.raises(AnalysisPathError):
        assert_not_otio_media_path(rel)
    for bad in (
        "_otio_v2/analysis/frames/x.jpg",
        "analysis/media/working/x.jpg",
        "media/working/a/b/c.jpg",
    ):
        with pytest.raises(AnalysisPathError):
            normalize_analysis_relative_path(bad)


def test_analysis_report_schema_omits_future_model_tables() -> None:
    report = AnalysisRunReport(
        run_id="run-1",
        project_id="project-1",
        scope="analysis_prepare_only",
        analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
        status=AnalysisRunStatus.COMPLETED,
        created_at=_now(),
        input_identities=[
            AnalysisInputIdentity(
                project_id="project-1",
                asset_id="asset-1",
                working_media_id="wm-1",
                validation_id="validation-1",
                source_sha256="a" * 64,
                output_sha256="b" * 64,
                processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                media_kind="image",
            )
        ],
    )
    data = json.loads(analysis_repo.serialize_analysis_run_report(report))
    assert data["schema_version"] == "1"
    dumped = json.dumps(data)
    for forbidden in (
        "visual_observations",
        "model_analysis_attempts",
        "consent_events",
    ):
        assert forbidden not in dumped


# --- UI static/fake streamlit ----------------------------------------------


def test_asset_analysis_ui_source_is_prepare_only_and_no_media_io() -> None:
    source = Path(analysis_ui.__file__).read_text(encoding="utf-8")
    for needle in (
        "ffmpeg",
        "ffprobe",
        "Image.open",
        "compute_sha256",
        "stat(",
        "subprocess",
        "Provider",
        "model_id",
        "consent_events",
        "openai",
        "gemini",
    ):
        assert needle not in source
    assert "start_analysis_prepare" in source
    assert "start_model_analysis" in source
    tree = ast.parse(source)
    start_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_analysis_prepare"
    ]
    assert len(start_calls) == 1
    model_start_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_model_analysis"
    ]
    assert len(model_start_calls) == 1
    button_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "button"
    ]
    assert button_calls


class _FakeStreamlit:
    def __init__(self, *, clicked: bool = False) -> None:
        self.clicked = clicked
        self.titles: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.successes: list[str] = []
        self.captions: list[str] = []
        self.markdowns: list[str] = []
        self.dataframes: list[Any] = []
        self.buttons: list[dict[str, Any]] = []
        self.session_state: dict = {}

    def title(self, text: str) -> None:
        self.titles.append(text)

    def subheader(self, text: str) -> None:
        self.captions.append(f"## {text}")

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def success(self, text: str) -> None:
        self.successes.append(text)

    def caption(self, text: str) -> None:
        self.captions.append(text)

    def markdown(self, text: str) -> None:
        self.markdowns.append(text)

    def write(self, *args: Any, **_kwargs: Any) -> None:
        self.captions.append(str(args))

    def dataframe(self, data: Any, **_kwargs: Any) -> None:
        self.dataframes.append(data)

    def button(self, label: str, **kwargs: Any) -> bool:
        self.buttons.append({"label": label, **kwargs})
        return self.clicked


def _fake_prepare_view(*, can_start: bool) -> AnalysisPrepareStatusView:
    return AnalysisPrepareStatusView(
        ok=True,
        message=None,
        plan_id="plan-1",
        chain_ok=True,
        can_start=can_start,
        items=[
            AnalysisPrepareEligibilityItemView(
                eligibility=AnalysisEligibility(
                    asset_id="asset-1",
                    working_media_id="wm-1",
                    eligible=True,
                    expected_processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                    actual_processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                    media_kind="video",
                    source_group="Media",
                    source_relative_path="Media/clip.mp4",
                    output_sha256="a" * 64,
                    validation_id="validation-1",
                    display_name="clip.mp4",
                )
            )
        ],
    )


@pytest.mark.parametrize(
    "clicked,can_start,expected_starts",
    [(False, True, 0), (True, False, 0), (True, True, 1)],
)
def test_asset_analysis_ui_button_is_explicit_start_only(
    monkeypatch: pytest.MonkeyPatch,
    clicked: bool,
    can_start: bool,
    expected_starts: int,
) -> None:
    fake_st = _FakeStreamlit(clicked=clicked)
    monkeypatch.setattr(analysis_ui, "st", fake_st)
    monkeypatch.setattr(
        analysis_ui,
        "active_discovery_project",
        lambda: SimpleNamespace(
            id="project-1",
            project_mode=ProjectMode.DISCOVERY_V2,
            name="P",
            language="de",
            project_root_path=Path("/tmp/no-media-io"),
        ),
    )
    monkeypatch.setattr(
        analysis_ui, "get_analysis_prepare_view", lambda _p: _fake_prepare_view(can_start=can_start)
    )
    monkeypatch.setattr(analysis_ui, "_render_prepare_review", lambda *_args: None)
    starts: list[Any] = []

    def _fake_start(project: Project, *, sync: bool = False):
        starts.append((project, sync))
        return SimpleNamespace(started=True, message="started", run=None)

    monkeypatch.setattr(analysis_ui, "start_analysis_prepare", _fake_start)

    analysis_ui.render_discovery_asset_analysis_page()
    analysis_ui.render_discovery_asset_analysis_page()

    assert len(starts) == expected_starts * 2
    assert fake_st.buttons
    assert any("keine Medien an externe Dienste" in info for info in fake_st.infos)
    if can_start is False:
        assert all(button["disabled"] is True for button in fake_st.buttons)


# --- Integration / smoke ---------------------------------------------------


def test_smoke_video_three_hard_cuts_produces_monotone_shots_and_frames(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    _make_color_video(root / "Media" / "hardcuts.mp4", ["red", "green", "blue", "white"])
    project = _new_project(root, temp_db_path, name="Hard Cuts")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, _assets = _all_analysis_artifacts(project)
    assert len(shots) >= 3
    assert len(frames) >= min(len(shots), 24)
    ordered = sorted(shots, key=lambda item: item.ordinal)
    assert ordered[0].start_seconds == pytest.approx(0.0)
    for previous, current in zip(ordered, ordered[1:]):
        assert current.ordinal == previous.ordinal + 1
        assert current.start_seconds == pytest.approx(previous.end_seconds, abs=1e-3)
    for frame in frames:
        assert frame.relative_path.startswith("analysis/frames/")
        assert "_otio" not in frame.relative_path
        assert resolve_analysis_relative_path(project.project_root_path, frame.relative_path).is_file()
        if frame.shot_id is not None:
            assert frame.timestamp_seconds is not None


def test_smoke_long_static_video_splits_at_30s_and_respects_frame_cap(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    _make_static_video(root / "Media" / "long.mp4", duration=65.0, fps=1)
    project = _new_project(root, temp_db_path, name="Long Static")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, _assets = _all_analysis_artifacts(project)
    assert len(shots) == 3
    assert all(shot.duration_seconds <= 30.0 for shot in shots)
    assert len(frames) <= 24


def test_smoke_very_short_video_has_one_shot_and_one_frame(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    _make_static_video(root / "Media" / "short.mp4", duration=0.5, fps=10, color="red")
    project = _new_project(root, temp_db_path, name="Short")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, _assets = _all_analysis_artifacts(project)
    assert len(shots) == 1
    assert len(frames) == 1
    assert frames[0].timestamp_seconds is not None


def test_smoke_portrait_video_frame_stays_portrait_and_scaled(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    _make_static_video(
        root / "Media" / "portrait.mp4",
        duration=1.0,
        width=360,
        height=640,
        fps=5,
        color="purple",
    )
    project = _new_project(root, temp_db_path, name="Portrait")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    _shots, frames, _assets = _all_analysis_artifacts(project)
    assert len(frames) == 1
    assert frames[0].height > frames[0].width
    assert max(frames[0].width, frames[0].height) <= 1280


@pytest.mark.skip(reason="VFR fixture generation is environment-sensitive; argv unit tests cover no -r")
def test_smoke_vfr_fixture_if_creatable() -> None:
    pass


def test_smoke_opaque_still_prepares_one_jpeg_without_shots(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    Image.new("RGB", (40, 24), (220, 20, 20)).save(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="Still JPEG")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, _assets = _all_analysis_artifacts(project)
    assert shots == []
    assert len(frames) == 1
    assert frames[0].timestamp_seconds is None
    assert frames[0].shot_id is None
    assert frames[0].relative_path.endswith(".jpg")
    with Image.open(resolve_analysis_relative_path(project.project_root_path, frames[0].relative_path)) as preview:
        assert preview.format == "JPEG"


def test_smoke_alpha_still_prepares_png_with_alpha(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    image = Image.new("RGBA", (32, 32), (20, 40, 60, 0))
    image.putpixel((0, 0), (20, 40, 60, 255))
    image.save(root / "Media" / "alpha.png")
    project = _new_project(root, temp_db_path, name="Still Alpha")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, _assets = _all_analysis_artifacts(project)
    assert shots == []
    assert len(frames) == 1
    assert frames[0].relative_path.endswith(".png")
    with Image.open(resolve_analysis_relative_path(project.project_root_path, frames[0].relative_path)) as preview:
        assert preview.format == "PNG"
        assert preview.mode == "RGBA"
        assert preview.getpixel((1, 1))[3] == 0


def test_smoke_audio_only_is_not_applicable_without_frames(
    tmp_path: Path, temp_db_path: Path
) -> None:
    if not ffmpeg_available():
        pytest.skip("ffprobe-backed audio validation requires ffmpeg tools")
    root = tmp_path / "Project"
    _make_wav(root / "Media" / "sound.wav")
    project = _new_project(root, temp_db_path, name="Audio")

    result = _prepare_project(project)
    assert result.run.status == AnalysisRunStatus.COMPLETED
    shots, frames, assets = _all_analysis_artifacts(project)
    assert shots == []
    assert frames == []
    assert len(assets) == 1
    assert assets[0].status == AnalysisPrepareAssetStatus.NOT_APPLICABLE
    assert assets[0].error_code == "not_applicable"


def test_smoke_second_start_reuses_same_identity_and_artifacts(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    Image.new("RGB", (24, 24), (1, 2, 3)).save(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="Reuse")

    first = _prepare_project(project)
    assert first.run.status == AnalysisRunStatus.COMPLETED
    _shots1, frames1, assets1 = _all_analysis_artifacts(project)
    identity_ids = {asset.analysis_identity_id for asset in assets1}
    frame_ids = {frame.frame_id for frame in frames1}

    second = start_analysis_prepare(project, sync=True)
    assert second.run is not None
    assert second.run.status == AnalysisRunStatus.COMPLETED
    _shots2, frames2, assets2 = _all_analysis_artifacts(project)
    latest_assets = [
        asset for asset in assets2 if asset.run_id == second.run.run_id
    ]
    assert {asset.analysis_identity_id for asset in latest_assets} == identity_ids
    assert {frame.frame_id for frame in frames2} == frame_ids
    assert latest_assets[0].error_code == "reused"


def test_smoke_analysis_artifact_conflict_leaves_existing_file_unchanged(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    Image.new("RGB", (24, 24), (10, 20, 30)).save(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="Conflict")
    _import_validate_plan_and_copy(project)
    wm = _working_media_for_project(project)[0]
    fixed_frame_id = "00000000-0000-0000-0000-000000000001"
    conflict_rel = analysis_frame_relative_path(
        working_media_id=wm.working_media_id,
        sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
        shot_or_still="still",
        frame_id=fixed_frame_id,
        extension="jpg",
    )
    conflict_path = resolve_analysis_relative_path(project.project_root_path, conflict_rel)
    conflict_path.parent.mkdir(parents=True, exist_ok=True)
    conflict_path.write_bytes(b"do-not-overwrite")
    monkeypatch.setattr(worker, "new_frame_id", lambda: fixed_frame_id)

    result = start_analysis_prepare(project, sync=True)
    assert result.run is not None
    _shots, _frames, assets = _all_analysis_artifacts(project)
    latest = [asset for asset in assets if asset.run_id == result.run.run_id][0]
    assert latest.status == AnalysisPrepareAssetStatus.FAILED
    assert latest.error_code == "analysis_artifact_conflict"
    assert conflict_path.read_bytes() == b"do-not-overwrite"


def test_smoke_orphan_recovery_marks_interrupted_cleans_temp_keeps_published_frame(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="Orphan")
    run_id = "run-orphan"
    conn = reg_db.get_registry_connection(root)
    try:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group, file_name,
                extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-1",
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
        identity = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        frame = _frame_record(identity, project, ordinal=0, shot_id=None)
        final_path = resolve_analysis_relative_path(root, frame.relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"published")
        analysis_repo.insert_representative_frame(conn, frame)
        run = AnalysisRun(
            run_id=run_id,
            project_id=project.id,
            scope="analysis_prepare_only",
            analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
            status=AnalysisRunStatus.RUNNING,
            created_at=_now(),
            started_at=_now(),
            total_assets=1,
        )
        analysis_repo.insert_analysis_run(conn, run)
        analysis_repo.insert_analysis_run_asset(
            conn,
            AnalysisRunAsset(
                run_id=run_id,
                asset_id="asset-1",
                working_media_id="wm-1",
                validation_id="validation-1",
                source_sha256="a" * 64,
                output_sha256="a" * 64,
                processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                media_kind="image",
                status=AnalysisPrepareAssetStatus.EXTRACTING_FRAMES,
                analysis_identity_id=identity.analysis_identity_id,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    temp = analysis_temp_dir(root, run_id) / "partial.tmp.jpg"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(b"partial")

    updated = reconcile_orphaned_analysis_run(project)
    assert updated is not None
    assert updated.status == AnalysisRunStatus.FAILED
    assert "worker_interrupted" in (updated.error_summary or "")
    assert not temp.exists()
    assert final_path.read_bytes() == b"published"
    conn2 = reg_db.get_registry_connection(root)
    try:
        assets = analysis_repo.list_analysis_run_assets(conn2, run_id=run_id)
        assert assets[0].status == AnalysisPrepareAssetStatus.INTERRUPTED
        assert assets[0].error_code == "worker_interrupted"
    finally:
        conn2.close()
