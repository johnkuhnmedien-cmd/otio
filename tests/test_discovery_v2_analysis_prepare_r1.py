"""Phase 8B R1 permanent regressions.

Auftrag coverage:
- A: shot normalization matrix and NaN/Infinity rejection.
- B/G: productive FFmpeg argv, no shell/no -r, timeout, rotation/noautorotate docs.
- C: worker-level working-media input protection.
- D: reuse, identity, temp, and publish atomicity.
- E: job/UI/service view/report/source-scan guardrails.
- F: frame selection, all-black fallback, and still preview sizing.
- H: 1..86 Abschlussbericht matrix self-check.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

import otio_app.discovery_v2.adapters.ffmpeg_runner as ffmpeg_runner
import otio_app.discovery_v2.adapters.frame_sample as frame_sample
import otio_app.discovery_v2.adapters.shot_detect as shot_detect
from otio_app.discovery_v2.adapters.ffmpeg_runner import FFmpegRunResult
from otio_app.discovery_v2.adapters.frame_sample import (
    extract_video_frame_jpeg,
    prepare_still_preview,
    select_representative_timestamps,
)
from otio_app.discovery_v2.adapters.frame_signals import FrameSignals
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.media_probe import NormalizedMediaProbe
from otio_app.discovery_v2.adapters.shot_detect import (
    ShotDetectError,
    normalize_shot_boundaries,
)
from otio_app.discovery_v2.analysis_paths import (
    analysis_frame_relative_path,
    analysis_run_json_relative_path,
    analysis_temp_dir,
    assert_not_otio_media_path,
    resolve_analysis_relative_path,
)
from otio_app.discovery_v2.application import analysis_prepare_service
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    reconcile_orphaned_analysis_run,
)
from otio_app.discovery_v2.application.analysis_prepare_service import (
    AnalysisPrepareStatusView,
)
from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    AnalysisEligibilityView,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_PREPARE_PROFILE_VERSION,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    FRAME_SAMPLE_PROFILE_VERSION,
    AnalysisEligibility,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunStatus,
    RepresentativeFrameRecord,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.domain.media_intake import COPY_WORKING_PROFILE_VERSION
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.models import ProjectMode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discovery_v2_analysis_prepare import (  # noqa: PLC2701 - shared test helpers
    _FakeStreamlit,
    _all_analysis_artifacts,
    _frame_record,
    _import_validate_plan_and_copy,
    _make_static_video,
    _new_project,
    _now,
    _prepare_project,
    _require_ffmpeg,
    _shot,
    _working_media_for_project,
)


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


@dataclass
class _ValidationContext:
    project_root: Path
    conn: Any
    run: AnalysisRun
    asset: AnalysisRunAsset
    working_path: Path
    output_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_image(path: Path, size: tuple[int, int] = (24, 16)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (20, 40, 80)).save(path)


def _seed_validation_context(
    tmp_path: Path,
    temp_db_path: Path,
    *,
    working_relative_path: str = "media/working/wm-1/clip.jpg",
    status: str = "completed",
    create_working_file: bool = True,
    media_kind: str = "image",
) -> _ValidationContext:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True, exist_ok=True)
    source_path = root / "Media" / "clip.jpg"
    _write_image(source_path)
    project = _new_project(root, temp_db_path, name="R1 Validation")
    payload = b"working-media-bytes"
    output_sha = _sha256(payload)
    working_path = get_discovery_v2_root(root) / Path(
        *working_relative_path.replace("\\", "/").split("/")
    )
    if create_working_file:
        working_path.parent.mkdir(parents=True, exist_ok=True)
        working_path.write_bytes(payload)

    conn = analysis_repo.open_analysis_registry(root)
    conn.execute("PRAGMA foreign_keys = OFF")
    now = _now().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO assets (
            asset_id, project_id, source_relative_path, source_group, file_name,
            extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "asset-1",
            project.id,
            "Media/clip.jpg",
            "Media",
            "clip.jpg",
            ".jpg",
            media_kind,
            source_path.stat().st_size,
            source_path.stat().st_mtime_ns,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO working_media (
            working_media_id, project_id, asset_id, plan_id, intake_run_id,
            source_relative_path, working_relative_path, source_sha256,
            output_sha256, media_kind, extension, action,
            processing_profile_version, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "wm-1",
            project.id,
            "asset-1",
            "plan-1",
            "intake-1",
            "Media/clip.jpg",
            working_relative_path,
            "1" * 64,
            output_sha,
            media_kind,
            ".jpg",
            "copy",
            COPY_WORKING_PROFILE_VERSION,
            status,
            now,
            now,
        ),
    )
    conn.commit()

    run = AnalysisRun(
        run_id="run-1",
        project_id=project.id,
        scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
        analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
        status=AnalysisRunStatus.RUNNING,
        created_at=_now(),
    )
    asset = AnalysisRunAsset(
        run_id=run.run_id,
        asset_id="asset-1",
        working_media_id="wm-1",
        validation_id="validation-1",
        source_sha256="1" * 64,
        output_sha256=output_sha,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        media_kind=media_kind,
        status=AnalysisPrepareAssetStatus.PENDING,
        created_at=_now(),
    )
    return _ValidationContext(root, conn, run, asset, working_path, output_sha)


def _assert_worker_error(code: str, func, *args, **kwargs) -> None:
    from otio_app.discovery_v2.jobs.analysis_prepare_worker import (
        AnalysisPrepareWorkerError,
    )

    with pytest.raises(AnalysisPrepareWorkerError) as exc:
        func(*args, **kwargs)
    assert exc.value.code == code


def _fake_signals(seed: str, *, is_black: bool = False) -> FrameSignals:
    return FrameSignals(
        brightness_mean=0.0 if is_black else 0.5,
        black_fraction=1.0 if is_black else 0.0,
        is_black=is_black,
        sharpness_score=1.0,
        pixel_sha256=hashlib.sha256(f"pixel-{seed}".encode()).hexdigest(),
        frame_sha256=hashlib.sha256(f"frame-{seed}".encode()).hexdigest(),
    )


def _artifact_payload(**payload: Any) -> None:
    path = Path("/tmp/cursor/artifacts/phase8b_r1_ffmpeg_argv.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(payload)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")


# --- A. Shot normalization matrix ------------------------------------------


def test_r1_shot_normalization_matrix_explicit() -> None:
    assert normalize_shot_boundaries(4.0, [3.0, 1.0, 2.0]) == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
        (3.0, 4.0),
    ]
    assert normalize_shot_boundaries(2.0, [-1.0, 0.0, 0.5, 2.0, 9.0]) == [
        (0.0, 0.5),
        (0.5, 2.0),
    ]
    assert normalize_shot_boundaries(2.0, [0.2, 1.0]) == [(0.0, 1.0), (1.0, 2.0)]
    assert normalize_shot_boundaries(2.0, [1.0, 1.2]) == [(0.0, 1.2), (1.2, 2.0)]
    assert normalize_shot_boundaries(2.0, [0.2, 0.35, 0.55, 1.5]) == [
        (0.0, 0.55),
        (0.55, 1.5),
        (1.5, 2.0),
    ]
    assert normalize_shot_boundaries(0.3, []) == [(0.0, 0.3)]
    assert normalize_shot_boundaries(30.0, []) == [(0.0, 30.0)]
    long_segments = normalize_shot_boundaries(61.0, [])
    assert long_segments[0][0] == 0.0
    assert long_segments[-1][1] == 61.0
    assert len(long_segments) == 3
    assert all(end - start <= 30.0 for start, end in long_segments)


def test_r1_shot_normalization_rejects_nan_and_infinity() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ShotDetectError) as exc:
            normalize_shot_boundaries(4.0, [1.0, bad])
        assert exc.value.code == "invalid_shot_boundaries"


def test_r1_shot_normalization_dedupes_exact_window_deterministically() -> None:
    # 0.75 and 0.79 are exactly 0.04s apart in the input contract.
    assert normalize_shot_boundaries(2.0, [0.5, 0.75, 0.79, 1.5]) == [
        (0.0, 0.75),
        (0.75, 1.5),
        (1.5, 2.0),
    ]


# --- B/G. FFmpeg argv and runner guardrails --------------------------------


def test_r1_shot_detect_ffmpeg_argv_capture_and_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], *, timeout_sec: int) -> FFmpegRunResult:
        captured["argv"] = list(argv)
        captured["timeout_sec"] = timeout_sec
        return FFmpegRunResult(
            argv=list(argv),
            returncode=0,
            stdout="",
            stderr="frame pts_time:1.25\nframe pts_time:2.5\n",
        )

    monkeypatch.setattr(shot_detect, "run_ffmpeg", _fake_run)

    cuts = shot_detect.detect_scene_cut_seconds(
        Path("/project/_otio_v2/media/working/clip.mp4"),
        duration_seconds=4.0,
    )

    argv = captured["argv"]
    assert cuts == [1.25, 2.5]
    assert captured["timeout_sec"] == 1800
    assert argv[0] == "ffmpeg"
    assert any("gt(scene,0.35)" in part for part in argv)
    assert "-r" not in argv
    assert argv[argv.index("-f") : argv.index("-f") + 3] == ["-f", "null", "-"]
    assert all("shell" not in str(part).lower() for part in argv)
    _artifact_payload(shot={"argv": argv, "timeout_sec": captured["timeout_sec"]})


def test_r1_frame_sample_ffmpeg_argv_capture_rotation_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_argv: dict[str, list[str]] = {}
    timeouts: list[int] = []

    def _fake_run(argv: list[str], *, timeout_sec: int) -> FFmpegRunResult:
        timeouts.append(timeout_sec)
        output = Path(argv[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 6), (1, 2, 3)).save(output, format="JPEG")
        key = "none" if "none.jpg" in output.name else output.stem
        captured_argv[key] = list(argv)
        return FFmpegRunResult(argv=list(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(frame_sample, "run_ffmpeg", _fake_run)

    input_path = tmp_path / "working.mp4"
    for rotation, name in ((90, "rot90.jpg"), (0, "rot0.jpg"), (None, "none.jpg")):
        extract_video_frame_jpeg(
            input_path,
            tmp_path / name,
            12.345,
            rotation_degrees=rotation,
        )

    rot90 = captured_argv["rot90"]
    rot0 = captured_argv["rot0"]
    none = captured_argv["none"]
    assert timeouts and all(value == 120 for value in timeouts)
    for argv in (rot90, rot0, none):
        text = " ".join(argv)
        assert argv[0] == "ffmpeg"
        assert argv[argv.index("-ss") + 1] == "12.345"
        assert argv[argv.index("-frames:v") : argv.index("-frames:v") + 2] == [
            "-frames:v",
            "1",
        ]
        assert argv[argv.index("-q:v") : argv.index("-q:v") + 2] == ["-q:v", "2"]
        assert "min(1280" in argv[argv.index("-vf") + 1]
        assert "-noautorotate" in argv
        assert "-r" not in argv
        assert "crop" not in text
        assert "pad" not in text
        assert "zoom" not in text
        assert "16:9" not in text
        assert "force_original_aspect_ratio=decrease" in argv[argv.index("-vf") + 1]
    assert "transpose=clock" in rot90[rot90.index("-vf") + 1]
    assert "transpose" not in rot0[rot0.index("-vf") + 1]
    assert "transpose" not in none[none.index("-vf") + 1]
    _artifact_payload(
        frame={
            "rotation_90": rot90,
            "rotation_0": rot0,
            "rotation_none": none,
            "timeout_sec": 120,
        }
    )


def test_r1_real_ffmpeg_runner_uses_no_shell_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_subprocess_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ffmpeg_runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", _fake_subprocess_run)

    result = ffmpeg_runner.run_ffmpeg(["ffmpeg", "-version"], timeout_sec=7)

    assert result.returncode == 0
    assert captured["argv"] == ["ffmpeg", "-version"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 7
    assert captured["kwargs"]["check"] is False


def test_r1_rotation_documentation_argv_and_portrait_smoke_if_ffmpeg_available(
    tmp_path: Path,
) -> None:
    rot90 = frame_sample.build_extract_video_frame_jpeg_argv(
        input_path=tmp_path / "portrait.mp4",
        output_path=tmp_path / "frame90.jpg",
        timestamp=0.25,
        rotation_degrees=90,
    )
    rot0 = frame_sample.build_extract_video_frame_jpeg_argv(
        input_path=tmp_path / "portrait.mp4",
        output_path=tmp_path / "frame0.jpg",
        timestamp=0.25,
        rotation_degrees=0,
    )
    assert "-noautorotate" in rot90
    assert "transpose=clock" in rot90[rot90.index("-vf") + 1]
    assert "-noautorotate" in rot0
    assert "transpose" not in rot0[rot0.index("-vf") + 1]

    if not ffmpeg_runner.ffmpeg_available():
        return
    _require_ffmpeg()
    video = tmp_path / "portrait.mp4"
    _make_static_video(video, duration=1.0, width=360, height=640, fps=5)
    output = tmp_path / "portrait.jpg"
    extract_video_frame_jpeg(video, output, 0.2, rotation_degrees=0)
    with Image.open(output) as image:
        assert max(image.size) <= 1280
        assert image.height > image.width


# --- C. Worker-level input protection --------------------------------------


def test_r1_validate_working_media_hash_mismatch(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    ctx = _seed_validation_context(tmp_path, temp_db_path)
    try:
        ctx.working_path.write_bytes(b"changed-after-row")
        _assert_worker_error(
            "working_media_hash_mismatch",
            worker._validate_working_media,
            ctx.conn,
            ctx.project_root,
            run=ctx.run,
            asset=ctx.asset,
        )
    finally:
        ctx.conn.close()


@pytest.mark.parametrize(
    "relative_path",
    [
        "/tmp/clip.jpg",
        "../media/working/clip.jpg",
        "_otio_v2/media/working/clip.jpg",
        "analysis/temp/run-1/clip.jpg",
        "media/working/../clip.jpg",
        "media/working/_otio_bad/clip.jpg",
    ],
)
def test_r1_validate_working_media_rejects_invalid_paths(
    tmp_path: Path,
    temp_db_path: Path,
    relative_path: str,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    ctx = _seed_validation_context(
        tmp_path,
        temp_db_path,
        working_relative_path=relative_path,
        create_working_file=False,
    )
    try:
        _assert_worker_error(
            "invalid_working_media_path",
            worker._validate_working_media,
            ctx.conn,
            ctx.project_root,
            run=ctx.run,
            asset=ctx.asset,
        )
    finally:
        ctx.conn.close()


def test_r1_validate_working_media_rejects_original_media_path(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    ctx = _seed_validation_context(
        tmp_path,
        temp_db_path,
        working_relative_path="Media/clip.jpg",
        create_working_file=False,
    )
    try:
        _assert_worker_error(
            "invalid_working_media_path",
            worker._validate_working_media,
            ctx.conn,
            ctx.project_root,
            run=ctx.run,
            asset=ctx.asset,
        )
    finally:
        ctx.conn.close()


def test_r1_validate_working_media_raw_ready_is_stale(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    ctx = _seed_validation_context(tmp_path, temp_db_path, status="ready")
    try:
        _assert_worker_error(
            "stale_working_media",
            worker._validate_working_media,
            ctx.conn,
            ctx.project_root,
            run=ctx.run,
            asset=ctx.asset,
        )
    finally:
        ctx.conn.close()


def test_r1_validate_working_media_missing_file(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    ctx = _seed_validation_context(
        tmp_path,
        temp_db_path,
        create_working_file=False,
    )
    try:
        _assert_worker_error(
            "working_media_missing",
            worker._validate_working_media,
            ctx.conn,
            ctx.project_root,
            run=ctx.run,
            asset=ctx.asset,
        )
    finally:
        ctx.conn.close()


# --- D. Idempotency / atomicity --------------------------------------------


def test_r1_reuse_rejects_corrupted_persisted_frame_not_silent_reused(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 Corrupt Reuse")

    first = _prepare_project(project)
    assert first.run is not None
    _shots, frames, _assets = _all_analysis_artifacts(project)
    frame_path = resolve_analysis_relative_path(root, frames[0].relative_path)
    frame_path.write_bytes(b"corrupted-frame")

    second = analysis_prepare_service.start_analysis_prepare(project, sync=True)
    assert second.run is not None
    _shots2, frames2, assets2 = _all_analysis_artifacts(project)
    latest = [asset for asset in assets2 if asset.run_id == second.run.run_id][0]
    assert latest.error_code != "reused"
    assert latest.status in {AnalysisPrepareAssetStatus.PREPARED, AnalysisPrepareAssetStatus.FAILED}
    if latest.status == AnalysisPrepareAssetStatus.PREPARED:
        assert frames2
        assert all(frame.relative_path != frames[0].relative_path for frame in frames2)


def test_r1_final_exists_exact_is_accepted_without_overwrite(tmp_path: Path) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    final_path = tmp_path / "analysis" / "frames" / "frame.jpg"
    final_path.parent.mkdir(parents=True)
    data = b"same-final-bytes"
    final_path.write_bytes(data)

    assert worker._final_path_exact_or_free(
        final_path,
        frame_sha256=_sha256(data),
        file_size_bytes=len(data),
    ) is True
    assert final_path.read_bytes() == data


def test_r1_multi_frame_conflict_publishes_no_sibling(
    tmp_path: Path,
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="R1 Atomic Video")
    conn = analysis_repo.open_analysis_registry(root)
    working_path = get_discovery_v2_root(root) / "media" / "working" / "wm-1" / "clip.mp4"
    working_path.parent.mkdir(parents=True)
    working_path.write_bytes(b"video")
    now = _now().isoformat()
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
            "Media/clip.mp4",
            "Media",
            "clip.mp4",
            ".mp4",
            "video",
            5,
            1,
            now,
            now,
        ),
    )
    identity = analysis_repo.find_or_create_analysis_identity(
        conn,
        project_id=project.id,
        asset_id="asset-1",
        working_media_id="wm-1",
        output_sha256="2" * 64,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
    )
    run = AnalysisRun(
        run_id="run-atomic",
        project_id=project.id,
        scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
        analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
        status=AnalysisRunStatus.RUNNING,
        created_at=_now(),
    )
    asset = AnalysisRunAsset(
        run_id=run.run_id,
        asset_id="asset-1",
        working_media_id="wm-1",
        validation_id="validation-1",
        source_sha256="1" * 64,
        output_sha256="2" * 64,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        media_kind="video",
        status=AnalysisPrepareAssetStatus.PENDING,
        analysis_identity_id=identity.analysis_identity_id,
        created_at=_now(),
    )
    analysis_repo.insert_analysis_run(conn, run)
    analysis_repo.insert_analysis_run_asset(conn, asset)
    conn.commit()

    monkeypatch.setattr(
        worker,
        "probe_source_media",
        lambda *_args, **_kwargs: NormalizedMediaProbe(
            media_kind="video",
            duration_seconds=2.0,
            rotation_degrees=None,
        ),
    )
    monkeypatch.setattr(worker, "detect_scene_cut_seconds", lambda *_args, **_kwargs: [1.0])
    monkeypatch.setattr(
        worker,
        "normalize_shot_boundaries",
        lambda _duration, _cuts: [(0.0, 1.0), (1.0, 2.0)],
    )
    monkeypatch.setattr(worker, "select_representative_timestamps", lambda shots: [(shots[0], 0.5), (shots[1], 1.5)])
    shot_ids = iter(["shot-1", "shot-2"])
    monkeypatch.setattr(worker, "new_shot_id", lambda: next(shot_ids))

    first_final: Path | None = None
    second_final: Path | None = None

    def _fake_extract(
        *,
        project_root: Path,
        run: AnalysisRun,
        asset: AnalysisRunAsset,
        working_path: Path,
        probe: NormalizedMediaProbe,
        shot: TechnicalShotRecord | None,
        ordinal: int,
        timestamp: float,
    ) -> Any:
        del working_path, probe
        assert shot is not None
        temp_path = analysis_temp_dir(project_root, run.run_id) / f"frame-{ordinal}.tmp.jpg"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_bytes = f"temp-{ordinal}".encode("ascii")
        temp_path.write_bytes(temp_bytes)
        frame_id = f"frame-{ordinal}"
        final_rel = analysis_frame_relative_path(
            working_media_id=asset.working_media_id,
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            shot_or_still=shot.shot_id,
            frame_id=frame_id,
            extension="jpg",
        )
        final_path = resolve_analysis_relative_path(project_root, final_rel)
        signals = _fake_signals(str(ordinal))
        record = RepresentativeFrameRecord(
            frame_id=frame_id,
            analysis_identity_id=asset.analysis_identity_id or "",
            project_id=run.project_id,
            asset_id=asset.asset_id,
            working_media_id=asset.working_media_id,
            shot_id=shot.shot_id,
            ordinal=ordinal,
            timestamp_seconds=timestamp,
            relative_path=final_rel,
            frame_sha256=_sha256(temp_bytes),
            pixel_sha256=signals.pixel_sha256,
            file_size_bytes=len(temp_bytes),
            width=64,
            height=36,
            brightness_mean=signals.brightness_mean,
            black_fraction=signals.black_fraction,
            sharpness_score=signals.sharpness_score,
            is_black=signals.is_black,
            created_at=_now(),
        )
        return worker.PreparedFrame(record=record, temp_path=temp_path, final_path=final_path)

    monkeypatch.setattr(worker, "_extract_representative_video_frame", _fake_extract)

    first_final = resolve_analysis_relative_path(
        root,
        analysis_frame_relative_path(
            working_media_id="wm-1",
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            shot_or_still="shot-1",
            frame_id="frame-0",
            extension="jpg",
        ),
    )
    second_final = resolve_analysis_relative_path(
        root,
        analysis_frame_relative_path(
            working_media_id="wm-1",
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            shot_or_still="shot-2",
            frame_id="frame-1",
            extension="jpg",
        ),
    )
    second_final.parent.mkdir(parents=True, exist_ok=True)
    second_final.write_bytes(b"conflict")

    try:
        _assert_worker_error(
            "analysis_artifact_conflict",
            worker._prepare_video_asset,
            conn,
            root,
            run,
            asset,
            worker.ValidatedWorkingMedia(
                row={},
                path=working_path,
                file_size_bytes=working_path.stat().st_size,
                output_sha256="2" * 64,
            ),
        )
        assert first_final is not None and not first_final.exists()
        assert second_final is not None and second_final.read_bytes() == b"conflict"
    finally:
        conn.close()


def test_r1_prepare_temp_run_dir_cleaned_after_success(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 Temp Clean")

    result = _prepare_project(project)

    assert result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    assert not analysis_temp_dir(root, result.run.run_id).exists()


def test_r1_working_media_bytes_unchanged_after_prepare(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex

    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 WM Intact")
    _import_validate_plan_and_copy(project)
    wm = _working_media_for_project(project)[0]
    working_abs = get_discovery_v2_root(root) / wm.working_relative_path
    before = compute_sha256_hex(working_abs)
    before_size = working_abs.stat().st_size

    result = analysis_prepare_service.start_analysis_prepare(project, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    assert compute_sha256_hex(working_abs) == before
    assert working_abs.stat().st_size == before_size
    assert before == wm.output_sha256.lower()


def test_r1_new_working_media_id_and_output_hash_create_separate_identity_old_frames_remain(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="R1 Identity")
    conn = analysis_repo.open_analysis_registry(root)
    try:
        old_identity = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-old",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        old_frame = _frame_record(old_identity, project, ordinal=0, shot_id=None)
        analysis_repo.insert_representative_frame(conn, old_frame)
        new_identity = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-new",
            output_sha256="b" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        conn.commit()
        assert new_identity.analysis_identity_id != old_identity.analysis_identity_id
        old_frames = analysis_repo.list_representative_frames(
            conn,
            analysis_identity_id=old_identity.analysis_identity_id,
        )
        assert [frame.frame_id for frame in old_frames] == [old_frame.frame_id]
    finally:
        conn.close()


def test_r1_analysis_frames_not_working_media_and_rejected_as_otio_media(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 No Analysis Working")

    result = _prepare_project(project)
    assert result.run is not None
    _shots, frames, _assets = _all_analysis_artifacts(project)
    assert frames and all(frame.relative_path.startswith("analysis/frames/") for frame in frames)
    working_rows = _working_media_for_project(project)
    assert working_rows
    assert all(not row.working_relative_path.startswith("analysis/") for row in working_rows)
    with pytest.raises(Exception):
        assert_not_otio_media_path(frames[0].relative_path)


# --- E. Jobs / UI / service -------------------------------------------------


@pytest.mark.parametrize("status", [AnalysisRunStatus.QUEUED, AnalysisRunStatus.RUNNING])
def test_r1_active_analysis_run_blocks_second_start(
    tmp_path: Path,
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: AnalysisRunStatus,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="R1 Active")
    conn = analysis_repo.open_analysis_registry(root)
    try:
        active = AnalysisRun(
            run_id=f"run-{status.value}",
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
            analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
            status=status,
            created_at=_now(),
        )
        analysis_repo.insert_analysis_run(conn, active)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        analysis_prepare_service,
        "get_analysis_eligibility_view",
        lambda _project: AnalysisEligibilityView(
            ok=True,
            message=None,
            plan_id="plan-1",
            items=[
                AnalysisEligibility(
                    asset_id="asset-1",
                    working_media_id="wm-1",
                    eligible=True,
                    expected_processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                    actual_processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                    media_kind="image",
                    source_group="Media",
                    source_relative_path="Media/still.jpg",
                    output_sha256="a" * 64,
                    validation_id="validation-1",
                )
            ],
        ),
    )
    fake_launcher = SimpleNamespace(is_active=lambda _project_id: True)
    monkeypatch.setattr(analysis_prepare_service, "get_analysis_job_launcher", lambda: fake_launcher)
    import otio_app.discovery_v2.application.analysis_prepare_job_recovery as recovery

    monkeypatch.setattr(recovery, "get_analysis_job_launcher", lambda: fake_launcher)

    result = analysis_prepare_service.start_analysis_prepare(project, sync=True)

    assert result.started is False
    assert result.run is not None
    assert result.run.run_id == active.run_id


def test_r1_ui_button_false_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_st = _FakeStreamlit(clicked=False)
    starts: list[Any] = []
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
        analysis_ui,
        "get_analysis_prepare_view",
        lambda _p: AnalysisPrepareStatusView(
            ok=True,
            message=None,
            plan_id="plan-1",
            chain_ok=True,
            can_start=True,
            items=[],
        ),
    )
    monkeypatch.setattr(analysis_ui, "_render_prepare_review", lambda *_args: None)
    monkeypatch.setattr(
        analysis_ui,
        "start_analysis_prepare",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    analysis_ui.render_discovery_asset_analysis_page()

    assert fake_st.buttons and fake_st.buttons[0]["label"] == "Lokale Analyse vorbereiten"
    assert starts == []


def test_r1_orphan_recovery_cleans_only_own_temp(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="R1 Orphan Own Temp")
    conn = analysis_repo.open_analysis_registry(root)
    run_id = "run-orphan-r1"
    other_run_id = "run-other-r1"
    try:
        analysis_repo.insert_analysis_run(
            conn,
            AnalysisRun(
                run_id=run_id,
                project_id=project.id,
                scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
                analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
                status=AnalysisRunStatus.RUNNING,
                created_at=_now(),
                started_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    own_temp = analysis_temp_dir(root, run_id) / "partial.tmp.jpg"
    other_temp = analysis_temp_dir(root, other_run_id) / "other.tmp.jpg"
    own_temp.parent.mkdir(parents=True, exist_ok=True)
    other_temp.parent.mkdir(parents=True, exist_ok=True)
    own_temp.write_bytes(b"own")
    other_temp.write_bytes(b"other")

    updated = reconcile_orphaned_analysis_run(project)

    assert updated is not None
    assert updated.status == AnalysisRunStatus.FAILED
    assert not analysis_temp_dir(root, run_id).exists()
    assert other_temp.read_bytes() == b"other"


def test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io(
    tmp_path: Path,
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 View No IO")
    _import_validate_plan_and_copy(project)

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("media IO must not run during get_analysis_prepare_view")

    original_stat = Path.stat

    def _fail_media_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        text = self.as_posix()
        if (
            "/Media/" in text
            or "/media/working/" in text
            or "/analysis/frames/" in text
            or text.endswith((".jpg", ".jpeg", ".png", ".mp4", ".mov"))
        ):
            raise AssertionError("media Path.stat must not run during view")
        return original_stat(self, *args, **kwargs)

    import otio_app.discovery_v2.adapters.source_hash as source_hash
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    with monkeypatch.context() as media_io_guard:
        media_io_guard.setattr(Image, "open", _fail)
        media_io_guard.setattr(source_hash, "compute_sha256_hex", _fail)
        media_io_guard.setattr(worker, "compute_sha256_hex", _fail)
        media_io_guard.setattr(ffmpeg_runner, "run_ffmpeg", _fail)
        media_io_guard.setattr(shot_detect, "run_ffmpeg", _fail)
        media_io_guard.setattr(frame_sample, "run_ffmpeg", _fail)
        media_io_guard.setattr(Path, "stat", _fail_media_stat)
        view = analysis_prepare_service.get_analysis_prepare_view(project)

    assert view.ok is True
    assert view.items


def test_r1_render_prepare_review_reads_real_sqlite_shots_and_frames(
    tmp_path: Path,
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="R1 Review")
    conn = analysis_repo.open_analysis_registry(root)
    try:
        identity = analysis_repo.find_or_create_analysis_identity(
            conn,
            project_id=project.id,
            asset_id="asset-1",
            working_media_id="wm-1",
            output_sha256="a" * 64,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
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
        frame = _frame_record(identity, project, ordinal=0, shot_id="shot-1", timestamp_seconds=0.5)
        analysis_repo.insert_technical_shot(conn, shot)
        analysis_repo.insert_representative_frame(conn, frame)
        conn.commit()
    finally:
        conn.close()
    fake_st = _FakeStreamlit(clicked=False)
    monkeypatch.setattr(analysis_ui, "st", fake_st)

    analysis_ui._render_prepare_review(project.id, root)

    flattened = json.dumps(fake_st.dataframes, default=str)
    assert "shot-detect-v1" in flattened
    assert frame.relative_path in flattened
    assert len(fake_st.dataframes) == 2


def test_r1_sync_prepare_writes_report_under_analysis_runs_without_absolute_paths(
    tmp_path: Path,
    temp_db_path: Path,
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    _write_image(root / "Media" / "still.jpg")
    project = _new_project(root, temp_db_path, name="R1 Report")

    result = _prepare_project(project)

    assert result.run is not None
    report_rel = analysis_run_json_relative_path(result.run.run_id)
    report_path = resolve_analysis_relative_path(root, report_rel)
    assert report_path.is_file()
    assert report_path.parent == get_discovery_v2_root(root) / "analysis" / "runs"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    dumped = json.dumps(payload)
    assert str(root) not in dumped
    assert payload["counts"]["total_assets"] == 1
    assert payload["counts"]["frame_count"] == 1


def test_r1_worker_source_does_not_open_source_originals_for_ffmpeg_input() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "otio_app/discovery_v2/jobs/analysis_prepare_worker.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_text = ast.get_source_segment(source, node) or ""
            if "source_relative_path" in call_text and (
                "Path(" in call_text
                or "open(" in call_text
                or "probe_source_media(" in call_text
                or "detect_scene_cut_seconds(" in call_text
                or "extract_video_frame_jpeg(" in call_text
            ):
                bad_calls.append(call_text)
    assert bad_calls == []
    assert "source_relative_path" not in source
    assert "probe_source_media(working.path" in source


# --- F. Frame selection gaps ------------------------------------------------


def test_r1_overview_dedupe_when_overview_within_point_ten() -> None:
    shots = [_shot(0, 0.0, 1.0), _shot(1, 5.0, 5.2), _shot(2, 9.0, 10.0)]

    selected = select_representative_timestamps(shots)

    assert len(selected) == 3
    assert all(shot is not None for shot, _timestamp in selected)


def test_r1_equal_duration_tie_breaks_by_ordinal_for_more_than_24_shots() -> None:
    shots = [_shot(i, float(100 - i), float(101 - i)) for i in reversed(range(30))]

    selected = select_representative_timestamps(shots)

    assert len(selected) == 24
    assert [shot.ordinal for shot, _ in selected if shot is not None] == list(range(24))


def test_r1_all_black_candidates_fall_back_to_midpoint_with_is_black(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.discovery_v2.jobs import analysis_prepare_worker as worker

    timestamps: list[float] = []

    def _fake_extract(_input: Path, output: Path, timestamp: float, **_kwargs: Any) -> list[str]:
        timestamps.append(timestamp)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(f"black-{len(timestamps)}".encode("ascii"))
        return ["ffmpeg", "-ss", str(timestamp)]

    monkeypatch.setattr(worker, "extract_video_frame_jpeg", _fake_extract)
    monkeypatch.setattr(worker, "_compute_signals", lambda _path: _fake_signals("black", is_black=True))
    monkeypatch.setattr(worker, "_frame_dimensions", lambda _path: (64, 36))
    monkeypatch.setattr(worker, "new_frame_id", lambda: "frame-black")

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
        run_id="run-black",
        project_id="project-1",
        scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
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

    assert timestamps == [11.0, 10.75, 11.25, 11.0]
    assert prepared.record.timestamp_seconds == pytest.approx(11.0)
    assert prepared.record.is_black is True


def test_r1_still_preview_no_upscale_and_large_downscales(tmp_path: Path) -> None:
    small = tmp_path / "small.png"
    large = tmp_path / "large.png"
    Image.new("RGB", (32, 20), (1, 2, 3)).save(small)
    Image.new("RGB", (1600, 900), (1, 2, 3)).save(large)

    small_result = prepare_still_preview(small, tmp_path / "small-preview.jpg")
    large_result = prepare_still_preview(large, tmp_path / "large-preview.jpg")

    assert (small_result.width, small_result.height) == (32, 20)
    assert max(large_result.width, large_result.height) <= 1280
    assert large_result.width == 1280


# --- H. Matrix self-check (original Auftrag §26) ----------------------------


def test_r1_shot_detection_failed_on_ffmpeg_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(argv: list[str], *, timeout_sec: int) -> FFmpegRunResult:
        del timeout_sec
        return FFmpegRunResult(
            argv=list(argv),
            returncode=1,
            stdout="",
            stderr="decoder error",
        )

    monkeypatch.setattr(shot_detect, "run_ffmpeg", _fake_run)
    with pytest.raises(ShotDetectError) as exc:
        shot_detect.detect_scene_cut_seconds(
            Path("/tmp/missing-working.mp4"),
            duration_seconds=2.0,
        )
    assert exc.value.code == "shot_detection_failed"


def test_r1_matrix_86_complete() -> None:
    import importlib.util

    matrix_path = Path(__file__).resolve().parent / "_phase8b_matrix_86.py"
    spec = importlib.util.spec_from_file_location("phase8b_matrix_86", matrix_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matrix = module.MATRIX_86
    requirements = module.MATRIX_86_REQUIREMENTS

    assert sorted(matrix) == list(range(1, 87))
    assert sorted(requirements) == list(range(1, 87))
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
