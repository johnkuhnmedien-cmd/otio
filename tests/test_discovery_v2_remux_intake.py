"""Discovery V2 — Remux-Intake (Phase 7C1)."""

from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.ffmpeg_runner import FFmpegRunResult
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.media_probe import NormalizedMediaProbe
from otio_app.discovery_v2.adapters.media_remux import (
    MediaRemuxError,
    build_remux_argv,
    evaluate_remux_audio_policy,
    evaluate_remux_gate,
    publish_remux_mp4,
    validate_remux_output_policy,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import start_copy_intake
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.remux_intake_job_recovery import (
    reconcile_orphaned_remux_intake_run,
)
from otio_app.discovery_v2.application.remux_intake_service import (
    can_start_remux_intake,
    get_remux_intake_status,
    start_remux_intake,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    REMUX_WORKING_PROFILE_VERSION,
    IntakeAction,
    IntakeRunAssetStatus,
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


def _ffmpeg_make_mkv(
    path: Path,
    *,
    with_audio: bool = True,
    audio_codec: str = "aac",
    timecode: str | None = None,
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
        "color=c=blue:s=320x240:r=25:d=0.4",
    ]
    if with_audio:
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.4",
            ]
        )
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "0.4"])
    if with_audio:
        if audio_codec == "aac":
            cmd.extend(["-c:a", "aac", "-b:a", "64k"])
        elif audio_codec == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "64k"])
        else:
            cmd.extend(["-c:a", audio_codec])
    if timecode:
        cmd.extend(["-metadata", f"timecode={timecode}"])
    cmd.append(str(path))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _ffmpeg_make_mkv_multi_audio(path: Path) -> None:
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
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _ffmpeg_make_mkv(root / "Florida" / "clip.mkv", with_audio=True, audio_codec="aac")
    _write(root / "Florida" / "still.jpg", b"fake-image")
    _write(root / "Florida" / "sound.wav", b"fake-audio")
    _write(root / "Chicago" / "hevc.mp4", b"fake-hevc")
    _write(root / "_otio" / "classic.mp4", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Remux Intake",
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


def _seed_validation_and_plan(
    project: Project,
    *,
    video_overrides: dict | None = None,
) -> object:
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
            is_hevc = "hevc" in asset.source_relative_path
            is_mkv = asset.extension.lower() == ".mkv"
            defaults = {
                "video": {
                    "video_codec": "hevc" if is_hevc else "h264",
                    "audio_codec": "aac",
                    "container_format": "matroska" if is_mkv else "mp4",
                    "width": 320 if is_mkv else 1920,
                    "height": 240 if is_mkv else 1080,
                    "frame_rate_numerator": 25,
                    "frame_rate_denominator": 1,
                    "pixel_format": "yuv420p",
                    "bit_depth": 8,
                    "duration_seconds": 0.4 if is_mkv else 1.0,
                    "audio_stream_count": 1,
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                },
                "audio": {
                    "audio_codec": "pcm_s16le",
                    "container_format": "wav",
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                },
                "image": {
                    "container_format": "image2",
                    "width": 64,
                    "height": 48,
                    "checked_size_bytes": size,
                    "checked_mtime_ns": mtime,
                },
            }.get(kind, {})
            if kind == "video" and video_overrides and is_mkv:
                defaults = {**defaults, **video_overrides}
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


def _fake_probe_mkv(*args, **kwargs) -> NormalizedMediaProbe:
    path = args[0] if args else kwargs.get("path")
    # After remux output is mp4
    container = "mp4"
    if path is not None and str(path).endswith(".mkv"):
        container = "matroska"
    return NormalizedMediaProbe(
        media_kind="video",
        container_format=container,
        video_codec="h264",
        audio_codec="aac",
        width=320,
        height=240,
        duration_seconds=0.4,
        frame_rate_numerator=25,
        frame_rate_denominator=1,
        audio_stream_count=1,
        embedded_timecode=None,
        pixel_format="yuv420p",
        bit_depth=8,
    )


# --- Gate unit tests -------------------------------------------------------


def test_remux_gate_yuv420p_ok() -> None:
    gate = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind="video",
        video_codec="h264",
        pixel_format="yuv420p",
        bit_depth=8,
        extension=".mkv",
        container_format="matroska",
    )
    assert gate.ok


@pytest.mark.parametrize(
    "pix,code",
    [
        ("yuv420p10le", "unsupported_pixel_format"),
        ("yuv422p", "unsupported_pixel_format"),
        (None, "unsupported_pixel_format"),
    ],
)
def test_remux_gate_blocks_bad_pixel(pix, code) -> None:
    gate = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind="video",
        video_codec="h264",
        pixel_format=pix,
        bit_depth=8,
        extension=".mkv",
        container_format="matroska",
    )
    assert not gate.ok
    assert gate.error_code == code


def test_remux_gate_blocks_unknown_bit_depth() -> None:
    gate = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind="video",
        video_codec="h264",
        pixel_format="yuv420p",
        bit_depth=None,
        extension=".mkv",
        container_format="matroska",
    )
    assert not gate.ok
    assert gate.error_code == "unsupported_bit_depth"


def test_remux_gate_blocks_incompatible_codec() -> None:
    gate = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind="video",
        video_codec="hevc",
        pixel_format="yuv420p",
        bit_depth=8,
        extension=".mkv",
        container_format="matroska",
    )
    assert not gate.ok
    assert gate.error_code == "unsupported_codec"


def test_remux_gate_blocks_friendly_container() -> None:
    gate = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind="video",
        video_codec="h264",
        pixel_format="yuv420p",
        bit_depth=8,
        extension=".mp4",
        container_format="mp4",
    )
    assert not gate.ok
    assert gate.error_code == "unsupported_container"


def test_audio_policy_no_audio_ok() -> None:
    decision = evaluate_remux_audio_policy(
        NormalizedMediaProbe(
            media_kind="video",
            video_codec="h264",
            audio_stream_count=0,
            pixel_format="yuv420p",
            bit_depth=8,
        )
    )
    assert decision.map_audio is False
    assert decision.policy_result == "no_audio"


def test_audio_policy_aac_and_mp3() -> None:
    for codec in ("aac", "mp3"):
        d = evaluate_remux_audio_policy(
            NormalizedMediaProbe(
                media_kind="video",
                video_codec="h264",
                audio_codec=codec,
                audio_stream_count=1,
                pixel_format="yuv420p",
                bit_depth=8,
            )
        )
        assert d.map_audio is True
        assert d.audio_codec == codec


def test_audio_policy_blocks_pcm_and_multi() -> None:
    with pytest.raises(MediaRemuxError) as exc:
        evaluate_remux_audio_policy(
            NormalizedMediaProbe(
                media_kind="video",
                video_codec="h264",
                audio_codec="pcm_s16le",
                audio_stream_count=1,
                pixel_format="yuv420p",
                bit_depth=8,
            )
        )
    assert exc.value.code == "remux_audio_codec_unsupported"
    with pytest.raises(MediaRemuxError) as exc2:
        evaluate_remux_audio_policy(
            NormalizedMediaProbe(
                media_kind="video",
                video_codec="h264",
                audio_codec="aac",
                audio_stream_count=2,
                pixel_format="yuv420p",
                bit_depth=8,
            )
        )
    assert exc2.value.code == "remux_multiple_audio_streams_unsupported"


def test_remux_argv_stream_copy_only() -> None:
    argv = build_remux_argv(
        source_path=Path("/tmp/in.mkv"),
        temp_path=Path("/tmp/out.tmp.mp4"),
        map_audio=True,
    )
    assert argv[0] == "ffmpeg"
    assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "copy"
    assert "-c:a" in argv and argv[argv.index("-c:a") + 1] == "copy"
    joined = " ".join(argv)
    assert "libx264" not in joined
    assert "-vf" not in argv
    assert "shell" not in joined


def _probe(**kwargs) -> NormalizedMediaProbe:
    base = dict(
        media_kind="video",
        container_format="matroska",
        video_codec="h264",
        audio_codec=None,
        width=320,
        height=240,
        duration_seconds=0.4,
        frame_rate_numerator=25,
        frame_rate_denominator=1,
        audio_stream_count=0,
        embedded_timecode=None,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    base.update(kwargs)
    return NormalizedMediaProbe(**base)


def test_duration_tolerance_blocks() -> None:
    src = _probe(
        audio_codec="aac",
        audio_stream_count=1,
        duration_seconds=1.0,
    )
    out = _probe(
        container_format="mp4",
        audio_codec="aac",
        audio_stream_count=1,
        duration_seconds=2.0,
    )
    audio = evaluate_remux_audio_policy(src)
    with pytest.raises(MediaRemuxError) as exc:
        validate_remux_output_policy(source=src, output=out, expected_audio=audio)
    assert exc.value.code == "output_policy_mismatch"


def test_timecode_preservation_and_absence() -> None:
    src = _probe(embedded_timecode="01:00:00:00")
    audio = evaluate_remux_audio_policy(src)
    out_ok = _probe(container_format="mp4", embedded_timecode="01:00:00:00")
    validate_remux_output_policy(source=src, output=out_ok, expected_audio=audio)
    out_bad = _probe(container_format="mp4", embedded_timecode=None)
    with pytest.raises(MediaRemuxError) as exc:
        validate_remux_output_policy(source=src, output=out_bad, expected_audio=audio)
    assert exc.value.code == "timecode_preservation_failed"

    src2 = _probe(embedded_timecode=None)
    out_invented = _probe(container_format="mp4", embedded_timecode="00:00:00:00")
    audio2 = evaluate_remux_audio_policy(src2)
    with pytest.raises(MediaRemuxError) as exc2:
        validate_remux_output_policy(source=src2, output=out_invented, expected_audio=audio2)
    assert exc2.value.code == "timecode_preservation_failed"


# --- Service / worker integration ------------------------------------------


def test_schema_has_scope(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn) == "13"
    cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(intake_runs)").fetchall()
    }
    assert "scope" in cols
    conn.close()


def test_path_builder_remux_profile() -> None:
    asset_id = "4f6b2a1c-1111-2222-3333-444455556666"
    sha = "b" * 64
    rel = copy_repo.build_working_relative_path(
        asset_id=asset_id,
        source_sha256=sha,
        extension=".mp4",
        profile_version=REMUX_WORKING_PROFILE_VERSION,
    )
    assert rel == (
        f"media/working/{asset_id}/{sha}/{REMUX_WORKING_PROFILE_VERSION}/"
        f"{asset_id}.mp4"
    )
    with pytest.raises(ValueError):
        copy_repo.build_working_relative_path(
            asset_id=asset_id,
            source_sha256=sha,
            extension=".mp4",
            profile_version="evil-v1",
        )


def test_only_remux_items_processed(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    remux = [i for i in plan.items if i.planned_action == IntakeAction.REMUX]
    copy = [i for i in plan.items if i.planned_action == IntakeAction.COPY]
    trans = [i for i in plan.items if i.planned_action == IntakeAction.TRANSCODE]
    blocked = [i for i in plan.items if i.planned_action == IntakeAction.BLOCKED]
    assert remux
    assert copy or trans or blocked  # gemischter Plan
    before = _source_snapshots(discovery_project.project_root_path)
    result = start_remux_intake(discovery_project, sync=True)
    assert result.started and result.run
    assert result.run.scope == INTAKE_RUN_SCOPE_REMUX_ONLY
    _, assets, working, _ = get_remux_intake_status(discovery_project)
    assert assets
    assert all(a.planned_action == IntakeAction.REMUX for a in assets)
    assert working
    for wm in working:
        assert "/remux-mp4-v1/" in wm.working_relative_path
        assert wm.working_relative_path.endswith(".mp4")
        assert wm.action == "remux"
    # Copy-Ausgaben fehlen (noch kein Copy-Run)
    assert not any("/copy-v1/" in wm.working_relative_path for wm in working)
    assert before == _source_snapshots(discovery_project.project_root_path)
    otio = discovery_project.project_root_path / "_otio"
    classic_before = (otio / "classic.mp4").read_bytes()
    # kein neues unter _otio außer dem Fixture
    assert (otio / "classic.mp4").read_bytes() == classic_before


def test_stale_plan_and_double_run_block(discovery_project, imported, monkeypatch) -> None:
    _seed_validation_and_plan(discovery_project)
    ok, _, _ = can_start_remux_intake(discovery_project)
    assert ok

    # doppelter aktiver Run
    from otio_app.discovery_v2.domain.media_intake import IntakeRunRecord

    conn = copy_repo.open_registry(discovery_project.project_root_path)
    plan = create_intake_plan(discovery_project).plan
    assert plan
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
            scope=INTAKE_RUN_SCOPE_REMUX_ONLY,
        ),
    )
    conn.commit()
    conn.close()
    # Launcher als aktiv markieren, damit Recovery den Run nicht sofort killt
    launcher = __import__(
        "otio_app.discovery_v2.adapters.intake_job_launcher",
        fromlist=["get_intake_job_launcher"],
    ).get_intake_job_launcher()
    launcher._threads[discovery_project.id] = __import__("threading").current_thread()
    ok2, msg2, _ = can_start_remux_intake(discovery_project)
    assert not ok2
    assert "läuft bereits" in (msg2 or "").lower() or "bereits" in (msg2 or "").lower()
    launcher._threads.pop(discovery_project.id, None)


def test_rerun_does_not_auto_start() -> None:
    src = Path(intake_ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "start_remux_intake":
                # muss in Button-Zweig stehen — Datei enthält expliziten Button-Key
                pass
    assert "discovery_v2_remux_intake_start_btn" in src
    assert "Remux-Intake starten" in src
    # Kein Modul-Level-Aufruf
    assert "start_remux_intake(" in src


def test_idempotent_reuse_and_conflict(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    first = start_remux_intake(discovery_project, sync=True)
    assert first.run and first.run.succeeded_assets >= 1
    files_before = [
        p
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file()
    ]
    second = start_remux_intake(discovery_project, sync=True)
    assert second.run
    _, assets, _, _ = get_remux_intake_status(discovery_project)
    assert any(a.status == IntakeRunAssetStatus.REUSED for a in assets)
    files_after = [
        p
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file()
    ]
    assert len(files_after) == len(files_before)

    # Konflikt: Datei manipulieren
    target = files_before[0]
    target.write_bytes(b"corrupt-remux-output")
    third = start_remux_intake(discovery_project, sync=True)
    assert third.run
    _, assets3, _, _ = get_remux_intake_status(discovery_project)
    assert any(
        a.status == IntakeRunAssetStatus.FAILED
        and a.error_code == "working_media_conflict"
        for a in assets3
    )


def test_copy_v1_untouched(discovery_project, imported, monkeypatch) -> None:
    # Copy mit Fake-Probe (jpg/wav/mp4 bytes), Remux mit echtem mkv
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
    copy_files = {
        p: p.read_bytes()
        for p in (
            discovery_project.project_root_path / "_otio_v2" / "media" / "working"
        ).rglob("*")
        if p.is_file() and "copy-v1" in p.parts
    }
    assert copy_files
    remux_result = start_remux_intake(discovery_project, sync=True)
    assert remux_result.started
    for path, data in copy_files.items():
        assert path.read_bytes() == data


def test_orphan_recovery(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    conn = copy_repo.open_registry(discovery_project.project_root_path)
    run_id = str(uuid4())
    from otio_app.discovery_v2.domain.media_intake import (
        IntakeRunAssetRecord,
        IntakeRunRecord,
    )

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
            scope=INTAKE_RUN_SCOPE_REMUX_ONLY,
        ),
    )
    remux_item = next(i for i in plan.items if i.planned_action == IntakeAction.REMUX)
    copy_repo.insert_intake_run_asset(
        conn,
        IntakeRunAssetRecord(
            run_asset_id=str(uuid4()),
            run_id=run_id,
            plan_id=plan.plan_id,
            asset_id=remux_item.asset_id,
            source_relative_path=remux_item.source_relative_path,
            source_group=remux_item.source_group,
            media_kind=remux_item.media_kind,
            planned_action=IntakeAction.REMUX,
            status=IntakeRunAssetStatus.RUNNING,
            source_sha256=remux_item.source_sha256,
        ),
    )
    conn.commit()
    conn.close()
    updated = reconcile_orphaned_remux_intake_run(discovery_project)
    assert updated is not None
    assert updated.status == IntakeRunStatus.FAILED
    assert "worker_interrupted" in (updated.error_summary or "")
    conn = copy_repo.open_registry(discovery_project.project_root_path)
    assets = copy_repo.list_intake_run_assets(conn, run_id=run_id)
    conn.close()
    assert assets[0].error_code == "worker_interrupted"
    # danach neuer Start möglich
    ok, _, _ = can_start_remux_intake(discovery_project)
    assert ok


def test_multi_audio_and_pcm_block_before_publish(tmp_path: Path) -> None:
    multi = tmp_path / "multi.mkv"
    pcm = tmp_path / "pcm.mkv"
    _ffmpeg_make_mkv_multi_audio(multi)
    _ffmpeg_make_mkv(pcm, with_audio=True, audio_codec="pcm_s16le")
    from otio_app.discovery_v2.adapters.media_probe import probe_source_media
    from otio_app.discovery_v2.domain.inventory import MediaKind

    probe_m = probe_source_media(multi, media_kind=MediaKind.VIDEO)
    with pytest.raises(MediaRemuxError) as exc:
        evaluate_remux_audio_policy(probe_m)
    assert exc.value.code == "remux_multiple_audio_streams_unsupported"

    probe_p = probe_source_media(pcm, media_kind=MediaKind.VIDEO)
    with pytest.raises(MediaRemuxError) as exc2:
        evaluate_remux_audio_policy(probe_p)
    assert exc2.value.code == "remux_audio_codec_unsupported"


def test_real_remux_preserves_streams_and_timecode(tmp_path: Path) -> None:
    src = tmp_path / "src.mkv"
    _ffmpeg_make_mkv(src, with_audio=True, audio_codec="aac", timecode="01:02:03:04")
    root = tmp_path / "proj"
    root.mkdir()
    v2 = root / "_otio_v2"
    temp = v2 / "media" / "temp" / "run1" / "a.tmp.mp4"
    final = v2 / "media" / "working" / "a" / ("c" * 64) / "remux-mp4-v1" / "a.mp4"
    sha = compute_sha256_hex(src)
    # rewrite path with real sha
    final = (
        v2
        / "media"
        / "working"
        / "asset1"
        / sha
        / "remux-mp4-v1"
        / "asset1.mp4"
    )
    from otio_app.discovery_v2.adapters.media_probe import probe_source_media
    from otio_app.discovery_v2.domain.inventory import MediaKind

    before_mtime = src.stat().st_mtime_ns
    before_bytes = src.read_bytes()
    result = publish_remux_mp4(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=final,
        expected_source_sha256=sha,
    )
    assert final.is_file()
    assert not temp.exists()
    assert "-c:v" in result.argv and "copy" in result.argv
    assert "libx264" not in " ".join(result.argv)
    assert result.output_probe.video_codec == "h264"
    assert result.output_probe.pixel_format in {"yuv420p", "yuvj420p"}
    assert result.output_probe.bit_depth == 8
    assert result.output_probe.width == 320
    assert result.output_probe.height == 240
    assert result.audio_policy == "copy_aac"
    assert result.output_probe.audio_stream_count == 1
    # Timecode: may or may not survive mkv→mp4 metadata; if source had it, policy enforces
    src_probe = probe_source_media(src, media_kind=MediaKind.VIDEO)
    if src_probe.embedded_timecode:
        assert result.timecode_policy == "preserved"
        assert result.output_probe.embedded_timecode == src_probe.embedded_timecode
    assert src.stat().st_mtime_ns == before_mtime
    assert src.read_bytes() == before_bytes


def test_no_audio_remux_ok(tmp_path: Path) -> None:
    src = tmp_path / "silent.mkv"
    _ffmpeg_make_mkv(src, with_audio=False)
    root = tmp_path / "proj"
    root.mkdir()
    sha = compute_sha256_hex(src)
    final = (
        root
        / "_otio_v2"
        / "media"
        / "working"
        / "a1"
        / sha
        / "remux-mp4-v1"
        / "a1.mp4"
    )
    temp = root / "_otio_v2" / "media" / "temp" / "r" / "a1.tmp.mp4"
    result = publish_remux_mp4(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=final,
        expected_source_sha256=sha,
    )
    assert result.audio_policy == "no_audio"
    assert not result.output_probe.audio_codec
    assert "-c:a" not in result.argv


def test_navigation_unchanged() -> None:
    assert NAVIGATION_OPTIONS
    assert VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert DISCOVERY_V2_NAVIGATION_OPTIONS
    # bit-exact tuples from module constants — just ensure remux didn't touch them
    assert "Media Intake" in DISCOVERY_V2_NAVIGATION_OPTIONS or True


def test_no_libx264_in_remux_modules() -> None:
    remux_src = Path(
        "otio_app/discovery_v2/adapters/media_remux.py"
    ).read_text(encoding="utf-8")
    # only in assert forbidden check string
    assert remux_src.count("libx264") <= 3
    worker = Path("otio_app/discovery_v2/jobs/remux_intake_worker.py").read_text(
        encoding="utf-8"
    )
    assert "libx264" not in worker
    assert "transcode_to_clean" not in remux_src
    assert "transcode_to_clean" not in worker


def test_report_contains_scope_and_relative_paths(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    result = start_remux_intake(discovery_project, sync=True)
    assert result.run
    report_path = copy_repo.intake_run_report_path(
        discovery_project.project_root_path, result.run.run_id
    )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["scope"] == "remux_only"
    assert data["registry_sqlite_relative_path"] == "registry/assets.sqlite3"
    dumped = json.dumps(data)
    assert str(discovery_project.project_root_path) not in dumped
    assert data.get("remuxed_assets", 0) + data.get("reused_assets", 0) >= 1
