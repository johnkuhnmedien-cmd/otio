"""Discovery V2 — Video-Transcode-Intake (Phase 7C2, video-h264-v1)."""

from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.media_probe import (
    NormalizedMediaProbe,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.adapters.video_transcode import (
    VideoTranscodeError,
    assert_video_h264_argv,
    build_video_h264_argv,
    evaluate_video_audio_policy,
    evaluate_video_color_policy,
    publish_video_h264_v1,
    validate_video_h264_output_policy,
)
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import (
    can_start_copy_intake,
    start_copy_intake,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.remux_intake_job_recovery import (
    reconcile_orphaned_intake_run,
)
from otio_app.discovery_v2.application.remux_intake_service import (
    can_start_remux_intake,
    start_remux_intake,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.video_transcode_service import (
    can_start_video_transcode_intake,
    get_video_transcode_status,
    start_video_transcode_intake,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_COPY_ONLY,
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
    VIDEO_H264_PROFILE_VERSION,
    IntakeAction,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.ui import media_intake_page as intake_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


@pytest.fixture(autouse=True)
def _reset_launcher() -> None:
    reset_intake_job_launcher_for_tests()
    yield
    reset_intake_job_launcher_for_tests()


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _ffmpeg_make_video(
    path: Path,
    *,
    width: int = 320,
    height: int = 240,
    pix_fmt: str = "yuv420p",
    codec: str = "libx264",
    with_audio: bool = False,
    audio_channels: int = 2,
    timecode: str | None = None,
    duration: float = 0.4,
    fps: int = 25,
    rotate_meta: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s={width}x{height}:r={fps}:d={duration}",
    ]
    if with_audio:
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}",
            ]
        )
    cmd.extend(["-c:v", codec, "-pix_fmt", pix_fmt, "-t", str(duration)])
    if with_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "64k", "-ac", str(audio_channels)])
    if timecode:
        cmd.extend(["-metadata", f"timecode={timecode}"])
    if rotate_meta is not None:
        cmd.extend(["-metadata:s:v:0", f"rotate={rotate_meta}"])
    cmd.append(str(path))
    _run_ffmpeg(cmd)


def _ffmpeg_make_multi_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
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
            "color=c=green:s=320x240:r=25:d=0.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    # 10-Bit → Transcode
    _ffmpeg_make_video(
        root / "Florida" / "clip10.mp4",
        pix_fmt="yuv420p10le",
        with_audio=True,
        audio_channels=2,
    )
    # 4:2:2 → Transcode
    _ffmpeg_make_video(
        root / "Florida" / "clip422.mp4",
        pix_fmt="yuv422p",
        with_audio=False,
    )
    # Copy-taugliches Bild / Audio
    _write(root / "Florida" / "still.jpg", b"fake-image")
    _write(root / "Florida" / "sound.wav", b"fake-audio")
    # Bild-Transcode (ignorieren)
    _write(root / "Florida" / "scan.tif", b"fake-tiff")
    # Remux-Kandidat (h264/yuv420p/8 in mkv)
    _ffmpeg_make_video(
        root / "Chicago" / "friendly.mkv",
        pix_fmt="yuv420p",
        with_audio=True,
        audio_channels=1,
    )
    _write(root / "_otio" / "classic.mp4", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Video Transcode Intake",
            project_root=str(media_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


@pytest.fixture
def imported(discovery_project: Project):
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    selection = confirm_selection(
        discovery_project, snap, draft, acknowledged=True
    )
    result = import_confirmed_selection(discovery_project)
    return snap, selection, result


def _seed_validation_and_plan(project: Project) -> object:
    root = project.project_root_path
    conn = reg_db.get_registry_connection(root)
    try:
        latest = val_repo.find_latest_import(conn, project_id=project.id)
        assert latest is not None
        import_id, selection_id, scan_id, asset_count = latest
        run = ValidationRunRecord(
            run_id=str(uuid4()),
            project_id=project.id,
            import_id=import_id,
            selection_id=selection_id,
            scan_id=scan_id,
            status=ValidationRunStatus.COMPLETED,
            created_at=_now(),
            started_at=_now(),
            completed_at=_now(),
            total_assets=asset_count,
            processed_assets=asset_count,
            successful_assets=asset_count,
            failed_assets=0,
        )
        val_repo.insert_run(conn, run)
        for asset in val_repo.list_assets_for_import(conn, import_id=import_id):
            kind = asset.media_kind.value
            src = root / asset.source_relative_path
            digest = compute_sha256_hex(src) if src.is_file() else "0" * 64
            size = src.stat().st_size if src.is_file() else 0
            mtime = src.stat().st_mtime_ns if src.is_file() else 0
            name = asset.source_relative_path.lower()
            if kind == "video":
                if "clip10" in name:
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "container_format": "mp4",
                        "width": 320,
                        "height": 240,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv420p10le",
                        "bit_depth": 10,
                        "duration_seconds": 0.4,
                        "audio_stream_count": 1,
                        "checked_size_bytes": size,
                        "checked_mtime_ns": mtime,
                    }
                elif "clip422" in name:
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": None,
                        "container_format": "mp4",
                        "width": 320,
                        "height": 240,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv422p",
                        "bit_depth": 8,
                        "duration_seconds": 0.4,
                        "audio_stream_count": 0,
                        "checked_size_bytes": size,
                        "checked_mtime_ns": mtime,
                    }
                elif name.endswith(".mkv"):
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "container_format": "matroska",
                        "width": 320,
                        "height": 240,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv420p",
                        "bit_depth": 8,
                        "duration_seconds": 0.4,
                        "audio_stream_count": 1,
                        "checked_size_bytes": size,
                        "checked_mtime_ns": mtime,
                    }
                else:
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "container_format": "mp4",
                        "width": 1920,
                        "height": 1080,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv420p",
                        "bit_depth": 8,
                        "duration_seconds": 1.0,
                        "audio_stream_count": 1,
                        "checked_size_bytes": size,
                        "checked_mtime_ns": mtime,
                    }
            elif kind == "audio":
                defaults = {
                    "audio_codec": "pcm_s16le",
                    "container_format": "wav",
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                }
            elif kind == "image":
                defaults = {
                    "container_format": "image2",
                    "width": 64,
                    "height": 48,
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                }
            else:
                defaults = {
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                }
            val_repo.insert_asset_validation(
                conn,
                AssetValidationRecord(
                    validation_id=str(uuid4()),
                    run_id=run.run_id,
                    asset_id=asset.asset_id,
                    source_relative_path=asset.source_relative_path,
                    status=AssetValidationStatus.PROBE_SUCCEEDED,
                    sha256=digest,
                    media_kind=kind,
                    validated_at=_now(),
                    source_group=asset.source_group,
                    **defaults,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    result = create_intake_plan(project)
    assert result.created and result.plan
    return result.plan


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }


def _probe(**kwargs) -> NormalizedMediaProbe:
    base = dict(
        media_kind="video",
        container_format="mp4",
        video_codec="h264",
        audio_codec=None,
        width=320,
        height=240,
        duration_seconds=0.4,
        frame_rate_numerator=25,
        frame_rate_denominator=1,
        audio_stream_count=0,
        audio_channels=None,
        embedded_timecode=None,
        pixel_format="yuv420p",
        bit_depth=8,
        rotation_degrees=None,
        subtitle_stream_count=0,
        data_stream_count=0,
    )
    base.update(kwargs)
    return NormalizedMediaProbe(**base)


# --- FFmpeg-Profil ---------------------------------------------------------


def test_argv_profile_constraints() -> None:
    audio = evaluate_video_audio_policy(_probe(audio_stream_count=0))
    argv = build_video_h264_argv(
        source_path=Path("/tmp/in.mp4"),
        temp_path=Path("/tmp/out.tmp.mp4"),
        source=_probe(),
        audio=audio,
    )
    assert argv[0] == "ffmpeg"
    assert_video_h264_argv(argv)
    assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "libx264"
    assert "-crf" in argv and argv[argv.index("-crf") + 1] == "18"
    assert "-preset" in argv and argv[argv.index("-preset") + 1] == "medium"
    assert "-profile:v" in argv and argv[argv.index("-profile:v") + 1] == "high"
    assert "-pix_fmt" in argv and argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert "-noautorotate" in argv
    assert "-r" not in argv
    assert "-level" not in argv and "-level:v" not in argv
    joined = " ".join(argv)
    assert "scale=" not in joined
    assert "crop=" not in joined
    assert "pad=" not in joined
    assert "zoom" not in joined
    assert "16:9" not in joined
    assert "shell=True" not in joined


# --- Audio-Policy ----------------------------------------------------------


def test_audio_policy_variants() -> None:
    d0 = evaluate_video_audio_policy(_probe(audio_stream_count=0))
    assert d0.map_audio is False and d0.policy_result == "no_audio"

    d1 = evaluate_video_audio_policy(
        _probe(audio_stream_count=1, audio_codec="aac", audio_channels=1)
    )
    assert d1.map_audio and d1.channels == 1 and d1.policy_result == "aac_mono"

    d2 = evaluate_video_audio_policy(
        _probe(audio_stream_count=1, audio_codec="aac", audio_channels=2)
    )
    assert d2.map_audio and d2.channels == 2 and d2.policy_result == "aac_stereo"

    with pytest.raises(VideoTranscodeError) as e_multi:
        evaluate_video_audio_policy(
            _probe(audio_stream_count=2, audio_codec="aac", audio_channels=2)
        )
    assert e_multi.value.code == "video_multiple_audio_streams_unsupported"

    with pytest.raises(VideoTranscodeError) as e_ch:
        evaluate_video_audio_policy(
            _probe(audio_stream_count=1, audio_codec="aac", audio_channels=6)
        )
    assert e_ch.value.code == "video_audio_channels_unsupported"

    with pytest.raises(VideoTranscodeError) as e_unk:
        evaluate_video_audio_policy(
            _probe(audio_stream_count=1, audio_codec="aac", audio_channels=None)
        )
    assert e_unk.value.code == "video_audio_channels_unknown"


def test_hdr_color_blocked() -> None:
    with pytest.raises(VideoTranscodeError) as exc:
        evaluate_video_color_policy(
            _probe(color_transfer="smpte2084", color_primaries="bt2020")
        )
    assert exc.value.code == "video_color_profile_unsupported"


def test_output_policy_duration_and_timecode_rotation() -> None:
    src = _probe(audio_stream_count=0, duration_seconds=1.0)
    audio = evaluate_video_audio_policy(src)
    out_bad = _probe(duration_seconds=2.0)
    with pytest.raises(VideoTranscodeError) as exc:
        validate_video_h264_output_policy(
            source=src, output=out_bad, expected_audio=audio
        )
    assert exc.value.code == "output_policy_mismatch"

    src_tc = _probe(embedded_timecode="01:00:00:00")
    audio_tc = evaluate_video_audio_policy(src_tc)
    validate_video_h264_output_policy(
        source=src_tc,
        output=_probe(embedded_timecode="01:00:00:00", data_stream_count=1),
        expected_audio=audio_tc,
    )
    with pytest.raises(VideoTranscodeError) as exc2:
        validate_video_h264_output_policy(
            source=src_tc,
            output=_probe(embedded_timecode=None),
            expected_audio=audio_tc,
        )
    assert exc2.value.code == "timecode_preservation_failed"

    src_rot = _probe(rotation_degrees=90.0)
    audio_r = evaluate_video_audio_policy(src_rot)
    validate_video_h264_output_policy(
        source=src_rot,
        output=_probe(rotation_degrees=90.0),
        expected_audio=audio_r,
    )
    with pytest.raises(VideoTranscodeError) as exc3:
        validate_video_h264_output_policy(
            source=src_rot,
            output=_probe(rotation_degrees=0.0),
            expected_audio=audio_r,
        )
    assert exc3.value.code == "rotation_preservation_failed"


# --- Service gates ---------------------------------------------------------


def test_only_video_transcode_items_processed(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    vt = [
        i
        for i in plan.items
        if i.planned_action == IntakeAction.TRANSCODE
        and i.media_kind == "video"
    ]
    copy = [i for i in plan.items if i.planned_action == IntakeAction.COPY]
    remux = [i for i in plan.items if i.planned_action == IntakeAction.REMUX]
    image_tc = [
        i
        for i in plan.items
        if i.planned_action == IntakeAction.TRANSCODE and i.media_kind == "image"
    ]
    assert vt
    assert copy or remux or image_tc
    before = _source_snapshots(discovery_project.project_root_path)
    result = start_video_transcode_intake(discovery_project, sync=True)
    assert result.started and result.run
    assert result.run.scope == INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY
    _, assets, working, _ = get_video_transcode_status(discovery_project)
    assert assets
    assert all(a.planned_action == IntakeAction.TRANSCODE for a in assets)
    assert all((a.media_kind or "").lower() == "video" for a in assets)
    assert working
    for wm in working:
        assert f"/{VIDEO_H264_PROFILE_VERSION}/" in wm.working_relative_path
        assert wm.action == "transcode"
        assert wm.working_relative_path.endswith(".mp4")
    assert not any("/copy-v1/" in wm.working_relative_path for wm in working)
    assert not any("/remux-mp4-v1/" in wm.working_relative_path for wm in working)
    assert before == _source_snapshots(discovery_project.project_root_path)
    classic = discovery_project.project_root_path / "_otio" / "classic.mp4"
    assert classic.read_bytes() == b"classic"


def test_stale_plan_and_double_start_block(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    ok, _, _ = can_start_video_transcode_intake(discovery_project)
    assert ok

    conn = copy_repo.open_registry(discovery_project.project_root_path)
    copy_repo.insert_intake_run(
        conn,
        IntakeRunRecord(
            run_id=str(uuid4()),
            project_id=discovery_project.id,
            plan_id=plan.plan_id,
            import_id=plan.import_id,
            selection_id=plan.selection_id,
            scan_id=plan.scan_id,
            validation_run_id=plan.validation_run_id,
            status=IntakeRunStatus.RUNNING,
            created_at=_now(),
            total_assets=1,
            scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        ),
    )
    conn.commit()
    conn.close()
    launcher = __import__(
        "otio_app.discovery_v2.adapters.intake_job_launcher",
        fromlist=["get_intake_job_launcher"],
    ).get_intake_job_launcher()
    launcher._threads[discovery_project.id] = __import__("threading").current_thread()
    ok2, msg2, _ = can_start_video_transcode_intake(discovery_project)
    assert not ok2
    assert "bereits" in (msg2 or "").lower()
    # Copy/Remux ebenfalls blockiert
    ok_c, _, _ = can_start_copy_intake(discovery_project)
    ok_r, _, _ = can_start_remux_intake(discovery_project)
    assert not ok_c and not ok_r
    launcher._threads.pop(discovery_project.id, None)


def test_active_copy_and_remux_block_video_transcode(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    for scope in (INTAKE_RUN_SCOPE_COPY_ONLY, INTAKE_RUN_SCOPE_REMUX_ONLY):
        conn = copy_repo.open_registry(discovery_project.project_root_path)
        run_id = str(uuid4())
        copy_repo.insert_intake_run(
            conn,
            IntakeRunRecord(
                run_id=run_id,
                project_id=discovery_project.id,
                plan_id=plan.plan_id,
                import_id=plan.import_id,
                selection_id=plan.selection_id,
                scan_id=plan.scan_id,
                validation_run_id=plan.validation_run_id,
                status=IntakeRunStatus.RUNNING,
                created_at=_now(),
                total_assets=1,
                scope=scope,
            ),
        )
        conn.commit()
        conn.close()
        launcher = __import__(
            "otio_app.discovery_v2.adapters.intake_job_launcher",
            fromlist=["get_intake_job_launcher"],
        ).get_intake_job_launcher()
        launcher._threads[discovery_project.id] = __import__(
            "threading"
        ).current_thread()
        ok, msg, _ = can_start_video_transcode_intake(discovery_project)
        assert not ok
        assert "bereits" in (msg or "").lower()
        # terminal machen
        conn = copy_repo.open_registry(discovery_project.project_root_path)
        run = copy_repo.get_intake_run(conn, run_id=run_id)
        assert run
        copy_repo.update_intake_run(
            conn,
            run.model_copy(
                update={
                    "status": IntakeRunStatus.COMPLETED,
                    "completed_at": _now(),
                }
            ),
        )
        conn.commit()
        conn.close()
        launcher._threads.pop(discovery_project.id, None)
    ok_final, _, _ = can_start_video_transcode_intake(discovery_project)
    assert ok_final


def test_rerun_does_not_auto_start() -> None:
    src = Path(intake_ui.__file__).read_text(encoding="utf-8")
    assert "discovery_v2_video_transcode_start_btn" in src
    assert "Video-Transkodierung starten" in src
    assert "Video-Transkodierung" in src
    tree = ast.parse(src)
    module_calls = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            module_calls.append(node)
    assert not module_calls or "start_video_transcode_intake" not in src.split(
        "def render_discovery_media_intake_page"
    )[0]


def test_path_builder_video_profile() -> None:
    asset_id = "4f6b2a1c-1111-2222-3333-444455556666"
    sha = "c" * 64
    rel = copy_repo.build_working_relative_path(
        asset_id=asset_id,
        source_sha256=sha,
        extension=".mp4",
        profile_version=VIDEO_H264_PROFILE_VERSION,
    )
    assert rel == (
        f"media/working/{asset_id}/{sha}/{VIDEO_H264_PROFILE_VERSION}/"
        f"{asset_id}.mp4"
    )
    with pytest.raises(ValueError):
        copy_repo.build_working_relative_path(
            asset_id=asset_id,
            source_sha256=sha,
            extension=".mp4",
            profile_version="evil-v1",
        )


# --- Real publish / E2E smoke ---------------------------------------------


def test_real_10bit_and_422_transcode(tmp_path: Path) -> None:
    cases = [
        ("ten.mp4", {"pix_fmt": "yuv420p10le"}),
        ("yuv422.mp4", {"pix_fmt": "yuv422p"}),
        ("yuv444.mp4", {"pix_fmt": "yuv444p"}),
    ]
    for name, opts in cases:
        src = tmp_path / name
        _ffmpeg_make_video(src, **opts)
        root = tmp_path / f"proj_{name}"
        root.mkdir()
        sha = compute_sha256_hex(src)
        probe_in = probe_source_media(src, media_kind=MediaKind.VIDEO)
        final = (
            root
            / "_otio_v2"
            / "media"
            / "working"
            / "a1"
            / sha
            / VIDEO_H264_PROFILE_VERSION
            / "a1.mp4"
        )
        temp = root / "_otio_v2" / "media" / "temp" / "r" / "a1.tmp.mp4"
        before = src.read_bytes()
        mtime = src.stat().st_mtime_ns
        result = publish_video_h264_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=final,
            expected_source_sha256=sha,
            source_probe=probe_in,
        )
        assert final.is_file() and not temp.exists()
        assert result.output_probe.video_codec == "h264"
        assert result.output_probe.pixel_format in {"yuv420p", "yuvj420p"}
        assert result.output_probe.bit_depth == 8
        assert result.output_probe.width == probe_in.width
        assert result.output_probe.height == probe_in.height
        assert result.output_sha256
        assert "-c:v" in result.argv and "libx264" in result.argv
        assert "-r" not in result.argv
        assert src.read_bytes() == before
        assert src.stat().st_mtime_ns == mtime


def test_real_portrait_mono_and_silent(tmp_path: Path) -> None:
    portrait = tmp_path / "portrait.mp4"
    _ffmpeg_make_video(
        portrait,
        width=240,
        height=320,
        pix_fmt="yuv422p",
        with_audio=True,
        audio_channels=1,
    )
    silent = tmp_path / "silent.mp4"
    _ffmpeg_make_video(silent, pix_fmt="yuv420p10le", with_audio=False)

    for src, expect_audio in ((portrait, "aac_mono"), (silent, "no_audio")):
        root = tmp_path / f"root_{src.stem}"
        root.mkdir()
        sha = compute_sha256_hex(src)
        probe_in = probe_source_media(src, media_kind=MediaKind.VIDEO)
        final = (
            root
            / "_otio_v2"
            / "media"
            / "working"
            / "x"
            / sha
            / VIDEO_H264_PROFILE_VERSION
            / "x.mp4"
        )
        temp = root / "_otio_v2" / "media" / "temp" / "r" / "x.tmp.mp4"
        result = publish_video_h264_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=final,
            expected_source_sha256=sha,
            source_probe=probe_in,
        )
        assert result.audio_policy == expect_audio
        assert result.output_probe.width == probe_in.width
        assert result.output_probe.height == probe_in.height
        if expect_audio == "aac_mono":
            assert result.output_probe.audio_codec == "aac"
            assert result.output_probe.audio_channels == 1
            assert probe_in.height > probe_in.width
            assert result.output_probe.height > result.output_probe.width
        else:
            assert not result.output_probe.audio_codec
            assert result.timecode_policy == "absent"
            assert result.output_probe.embedded_timecode is None


def test_real_timecode_preservation(tmp_path: Path) -> None:
    src = tmp_path / "tc.mp4"
    _ffmpeg_make_video(src, pix_fmt="yuv422p", timecode="01:02:03:04")
    root = tmp_path / "proj"
    root.mkdir()
    sha = compute_sha256_hex(src)
    probe_in = probe_source_media(src, media_kind=MediaKind.VIDEO)
    assert probe_in.embedded_timecode == "01:02:03:04"
    final = (
        root
        / "_otio_v2"
        / "media"
        / "working"
        / "t"
        / sha
        / VIDEO_H264_PROFILE_VERSION
        / "t.mp4"
    )
    temp = root / "_otio_v2" / "media" / "temp" / "r" / "t.tmp.mp4"
    result = publish_video_h264_v1(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=final,
        expected_source_sha256=sha,
        source_probe=probe_in,
    )
    assert result.timecode_policy == "preserved"
    assert result.output_probe.embedded_timecode == "01:02:03:04"


def test_multi_audio_blocks_before_ffmpeg(tmp_path: Path) -> None:
    multi = tmp_path / "multi.mp4"
    _ffmpeg_make_multi_audio(multi)
    probe = probe_source_media(multi, media_kind=MediaKind.VIDEO)
    with pytest.raises(VideoTranscodeError) as exc:
        evaluate_video_audio_policy(probe)
    assert exc.value.code == "video_multiple_audio_streams_unsupported"


def test_idempotent_reuse_and_conflict(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    first = start_video_transcode_intake(discovery_project, sync=True)
    assert first.run and first.run.succeeded_assets >= 1
    _, assets1, working1, _ = get_video_transcode_status(discovery_project)
    wm_ids = {w.working_media_id for w in working1}
    files_before = [
        p
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file() and VIDEO_H264_PROFILE_VERSION in p.parts
    ]
    assert files_before
    second = start_video_transcode_intake(discovery_project, sync=True)
    assert second.run
    _, assets2, working2, _ = get_video_transcode_status(discovery_project)
    assert any(a.status == IntakeRunAssetStatus.REUSED for a in assets2)
    assert {w.working_media_id for w in working2} == wm_ids
    files_after = [
        p
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file() and VIDEO_H264_PROFILE_VERSION in p.parts
    ]
    assert len(files_after) == len(files_before)

    files_before[0].write_bytes(b"corrupt-video-transcode-output")
    third = start_video_transcode_intake(discovery_project, sync=True)
    assert third.run
    _, assets3, _, _ = get_video_transcode_status(discovery_project)
    assert any(
        a.status == IntakeRunAssetStatus.FAILED
        and a.error_code == "working_media_conflict"
        for a in assets3
    )


def test_copy_and_remux_outputs_untouched(discovery_project, imported, monkeypatch) -> None:
    def _probe_copy(path, *, media_kind):
        kind = media_kind.value if hasattr(media_kind, "value") else str(media_kind)
        return NormalizedMediaProbe(
            media_kind=kind,
            container_format="mp4" if kind == "video" else "image2",
            video_codec="h264" if kind == "video" else None,
            width=64,
            height=48,
            pixel_format="yuv420p" if kind == "video" else None,
            bit_depth=8 if kind == "video" else None,
        )

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.probe_source_media",
        _probe_copy,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.jobs.copy_intake_worker.probe_source_media",
        _probe_copy,
    )
    _seed_validation_and_plan(discovery_project)
    copy_result = start_copy_intake(discovery_project, sync=True)
    assert copy_result.started
    remux_result = start_remux_intake(discovery_project, sync=True)
    assert remux_result.started
    preserved = {
        p: p.read_bytes()
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file() and ("copy-v1" in p.parts or "remux-mp4-v1" in p.parts)
    }
    assert preserved
    vt = start_video_transcode_intake(discovery_project, sync=True)
    assert vt.started
    for path, data in preserved.items():
        assert path.read_bytes() == data


def test_orphan_recovery(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    conn = copy_repo.open_registry(discovery_project.project_root_path)
    run_id = str(uuid4())
    from otio_app.discovery_v2.domain.media_intake import IntakeRunAssetRecord

    copy_repo.insert_intake_run(
        conn,
        IntakeRunRecord(
            run_id=run_id,
            project_id=discovery_project.id,
            plan_id=plan.plan_id,
            import_id=plan.import_id,
            selection_id=plan.selection_id,
            scan_id=plan.scan_id,
            validation_run_id=plan.validation_run_id,
            status=IntakeRunStatus.RUNNING,
            created_at=_now(),
            total_assets=1,
            scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        ),
    )
    item = next(
        i
        for i in plan.items
        if i.planned_action == IntakeAction.TRANSCODE and i.media_kind == "video"
    )
    copy_repo.insert_intake_run_asset(
        conn,
        IntakeRunAssetRecord(
            run_asset_id=str(uuid4()),
            run_id=run_id,
            plan_id=plan.plan_id,
            asset_id=item.asset_id,
            source_relative_path=item.source_relative_path,
            source_group=item.source_group,
            media_kind=item.media_kind,
            planned_action=IntakeAction.TRANSCODE,
            status=IntakeRunAssetStatus.RUNNING,
            source_sha256=item.source_sha256,
        ),
    )
    # fremdes Temp darf bleiben
    alien = (
        discovery_project.project_root_path
        / "_otio_v2"
        / "media"
        / "temp"
        / "other-run"
        / "keep.tmp.mp4"
    )
    alien.parent.mkdir(parents=True, exist_ok=True)
    alien.write_bytes(b"keep-me")
    own_temp = (
        discovery_project.project_root_path
        / "_otio_v2"
        / "media"
        / "temp"
        / run_id
        / f"{item.asset_id}.tmp.mp4"
    )
    own_temp.parent.mkdir(parents=True, exist_ok=True)
    own_temp.write_bytes(b"partial")
    conn.commit()
    conn.close()

    updated = reconcile_orphaned_intake_run(discovery_project)
    assert updated is not None
    assert updated.status == IntakeRunStatus.FAILED
    assert "worker_interrupted" in (updated.error_summary or "")
    conn = copy_repo.open_registry(discovery_project.project_root_path)
    assets = copy_repo.list_intake_run_assets(conn, run_id=run_id)
    conn.close()
    assert assets[0].error_code == "worker_interrupted"
    assert alien.is_file()
    ok, _, _ = can_start_video_transcode_intake(discovery_project)
    assert ok


def test_report_scope_and_relative_paths(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    result = start_video_transcode_intake(discovery_project, sync=True)
    assert result.run
    report_path = copy_repo.intake_run_report_path(
        discovery_project.project_root_path, result.run.run_id
    )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["scope"] == "video_transcode_only"
    assert data["registry_sqlite_relative_path"] == "registry/assets.sqlite3"
    dumped = json.dumps(data)
    assert str(discovery_project.project_root_path) not in dumped
    assert data.get("remuxed_assets", 0) + data.get("reused_assets", 0) >= 1


def test_failed_policy_leaves_no_final(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    _ffmpeg_make_video(src, pix_fmt="yuv422p")
    root = tmp_path / "proj"
    root.mkdir()
    sha = compute_sha256_hex(src)
    final = (
        root
        / "_otio_v2"
        / "media"
        / "working"
        / "a"
        / sha
        / VIDEO_H264_PROFILE_VERSION
        / "a.mp4"
    )
    temp = root / "_otio_v2" / "media" / "temp" / "r" / "a.tmp.mp4"
    # falscher Hash → keine Final-Datei
    with pytest.raises(VideoTranscodeError) as exc:
        publish_video_h264_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=final,
            expected_source_sha256="a" * 64,
        )
    assert exc.value.code == "source_hash_mismatch"
    assert not final.exists()


def test_navigation_and_no_classic_hooks() -> None:
    assert NAVIGATION_OPTIONS
    assert VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert DISCOVERY_V2_NAVIGATION_OPTIONS
    adapter = Path(
        "otio_app/discovery_v2/adapters/video_transcode.py"
    ).read_text(encoding="utf-8")
    worker = Path(
        "otio_app/discovery_v2/jobs/video_transcode_worker.py"
    ).read_text(encoding="utf-8")
    assert "transcode_to_clean" not in adapter
    assert "transcode_to_clean" not in worker
    assert "shell=True" not in adapter
    assert "_otio/" not in adapter or "_otio_v2" in adapter


def test_no_image_convert_api_surface() -> None:
    ui = Path(intake_ui.__file__).read_text(encoding="utf-8")
    assert "Bildkonvertierung starten" not in ui
    assert "start_image" not in ui
    service = Path(
        "otio_app/discovery_v2/application/video_transcode_service.py"
    ).read_text(encoding="utf-8")
    assert "FastAPI" not in service
    assert "@app." not in service
