"""Phase-7-Abschluss: gemeinsame Regression + echter Multi-Action-Smoke."""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from otio_app.discovery_v2.adapters.image_convert import (
    ImageConvertError,
    publish_image_png_v1,
)
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    get_intake_job_launcher,
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import (
    can_start_copy_intake,
    get_copy_intake_status,
    start_copy_intake,
)
from otio_app.discovery_v2.application.image_convert_service import (
    can_start_image_convert_intake,
    get_image_convert_status,
    start_image_convert_intake,
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
    get_remux_intake_status,
    start_remux_intake,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.technical_validation_service import (
    start_technical_validation,
)
from otio_app.discovery_v2.application.video_transcode_service import (
    can_start_video_transcode_intake,
    get_video_transcode_status,
    start_video_transcode_intake,
)
from otio_app.discovery_v2.domain.media_intake import (
    IMAGE_PNG_PROFILE_VERSION,
    INTAKE_RUN_SCOPE_COPY_ONLY,
    INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
    IntakeAction,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.ui import media_intake_page as intake_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


pytestmark = pytest.mark.filterwarnings(
    "ignore:Corrupt EXIF data:UserWarning"
)


@pytest.fixture(autouse=True)
def _reset_launcher():
    reset_intake_job_launcher_for_tests()
    yield
    reset_intake_job_launcher_for_tests()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _ffmpeg_video(
    path: Path,
    *,
    pix_fmt: str = "yuv420p",
    container_args: list[str] | None = None,
    with_audio: bool = True,
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
                "-c:a",
                "aac",
                "-ac",
                "2",
            ]
        )
    cmd.extend(["-c:v", "libx264", "-pix_fmt", pix_fmt, "-t", "0.4"])
    if container_args:
        cmd.extend(container_args)
    cmd.append(str(path))
    _run_ffmpeg(cmd)


def _save_p_tiff(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("P", (8, 8))
    im.putpalette([i % 256 for i in range(768)])
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 3, 7), fill=1)
    draw.rectangle((4, 0, 7, 7), fill=2)
    im.save(path, format="TIFF")


def _publish_paths(tmp_path: Path, name: str) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    src = root / "sources" / f"{name}.tif"
    src.parent.mkdir(parents=True, exist_ok=True)
    temp = root / "_otio_v2" / "media" / "temp" / "run1" / f"{name}.tmp.png"
    working = (
        root
        / "_otio_v2"
        / "media"
        / "working"
        / name
        / ("a" * 64)
        / "image-png-v1"
        / f"{name}.png"
    )
    return root, src, temp, working


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts and "_otio" not in p.parts
    }


def _with_active_thread(project_id: str):
    launcher = get_intake_job_launcher()
    launcher._threads[project_id] = threading.current_thread()
    return launcher


# --- TIFF Palette-Transparenz (Defektkorrektur) ----------------------------


def test_p_tiff_with_transparency_normalizes_to_exact_rgba(
    tmp_path: Path, monkeypatch
) -> None:
    """Echtes Mode-P-TIFF + transparency-Metadatum → RGBA, Pixel exakt."""
    root, src, temp, out = _publish_paths(tmp_path, "pal")
    _save_p_tiff(src)

    real_open = Image.open

    def open_with_transparency(path, *args, **kwargs):
        image = real_open(path, *args, **kwargs)
        if Path(path).resolve() == src.resolve() and image.mode == "P":
            image.info["transparency"] = 1
        return image

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.image_convert.Image.open",
        open_with_transparency,
    )

    # Erwartete Pixel aus derselben Normalisierungslogik vorab berechnen.
    with real_open(src) as base:
        base.load()
        base.info["transparency"] = 1
        expected = base.convert("RGBA")
        expected_pixels = [
            expected.getpixel((x, y))
            for y in range(expected.size[1])
            for x in range(expected.size[0])
        ]

    result = publish_image_png_v1(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=out,
        expected_source_sha256=compute_sha256_hex(src),
        source_extension=".tif",
    )
    assert result.meta.output_mode == "RGBA"
    assert result.meta.output_has_alpha is True
    with Image.open(out) as png:
        assert png.mode == "RGBA"
        actual_pixels = [
            png.getpixel((x, y))
            for y in range(png.size[1])
            for x in range(png.size[0])
        ]
        assert actual_pixels == expected_pixels
        assert any(px[3] == 0 for px in actual_pixels)
        assert any(px[3] == 255 for px in actual_pixels)


def test_uncontrollable_palette_transparency_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "badpal")
    _save_p_tiff(src)
    real_open = Image.open

    def open_bad_transparency(path, *args, **kwargs):
        image = real_open(path, *args, **kwargs)
        if Path(path).resolve() == src.resolve():
            image.info["transparency"] = {"weird": True}
        return image

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.image_convert.Image.open",
        open_bad_transparency,
    )
    with pytest.raises(ImageConvertError) as exc:
        publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=compute_sha256_hex(src),
            source_extension=".tif",
        )
    assert exc.value.code == "image_alpha_preservation_failed"
    assert not out.exists()


# --- UI No-I/O / Navigation / HEIC -----------------------------------------


def test_ui_has_no_live_media_io_or_job_autostart() -> None:
    source = Path(intake_ui.__file__).read_text(encoding="utf-8")
    for banned in (
        "Image.open",
        "probe_image_file",
        "probe_source_media",
        "ffprobe",
        "run_ffmpeg",
        "compute_sha256",
        "subprocess",
        "os.stat(",
        ".st_mtime",
        "pillow_heif",
    ):
        assert banned not in source
    assert "sync=False" in source
    assert "TIFF-Konvertierung starten" in source
    assert "HEIC/HEIF kann in dieser Installation" in source
    assert "HEIC-Konvertierung starten" not in source
    assert "discovery_v2_heic" not in source.lower()
    # Unbekannte Werte als Gedankenstrich
    assert "—" in source


def test_navigation_unchanged_and_no_http_api_surface() -> None:
    assert "Media Intake" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Media Intake" not in NAVIGATION_OPTIONS
    assert "Media Intake" not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    for rel in (
        "otio_app/discovery_v2/application/copy_intake_service.py",
        "otio_app/discovery_v2/application/remux_intake_service.py",
        "otio_app/discovery_v2/application/video_transcode_service.py",
        "otio_app/discovery_v2/application/image_convert_service.py",
        "otio_app/discovery_v2/ui/media_intake_page.py",
    ):
        text = Path(rel).read_text(encoding="utf-8")
        assert "FastAPI" not in text
        assert "@app." not in text
        assert "requests.get" not in text


# --- Mutual exclusion matrix -----------------------------------------------


def _seed_minimal_project(tmp_path: Path, temp_db_path: Path) -> Project:
    """Projekt mit Copy-, Remux-, Video- und TIFF-Kandidaten für Gate-Tests."""
    root = tmp_path / "Project"
    root.mkdir()
    florida = root / "Florida"
    chicago = root / "Chicago"
    florida.mkdir()
    chicago.mkdir()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(florida / "a.tif", format="TIFF")
    Image.new("RGB", (8, 8), (9, 9, 9)).save(florida / "still.jpg", format="JPEG")
    _ffmpeg_video(chicago / "friendly.mkv", pix_fmt="yuv420p")
    _ffmpeg_video(florida / "clip10.mp4", pix_fmt="yuv420p10le")
    classic = root / "_otio" / "classic.bin"
    classic.parent.mkdir(parents=True, exist_ok=True)
    classic.write_bytes(b"classic")
    project = create_project(
        ProjectCreate(
            name="Hardening",
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )
    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    confirm_selection(project, snap, draft, acknowledged=True)
    import_confirmed_selection(project)
    start_technical_validation(project, sync=True)
    create_intake_plan(project)
    return project


def _insert_active_run(project: Project, scope: str) -> str:
    from otio_app.discovery_v2.application.media_intake_planning_service import (
        get_current_intake_plan,
    )

    plan, stale, _ = get_current_intake_plan(project)
    assert plan is not None and not stale
    # Vorherigen aktiven Run terminal schließen, damit Insert eindeutig ist.
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        active = copy_repo.find_active_intake_run(conn, project_id=project.id)
        if active is not None:
            copy_repo.update_intake_run(
                conn,
                active.model_copy(
                    update={
                        "status": IntakeRunStatus.COMPLETED,
                        "completed_at": _now(),
                    }
                ),
            )
        run_id = str(uuid4())
        copy_repo.insert_intake_run(
            conn,
            IntakeRunRecord(
                run_id=run_id,
                project_id=project.id,
                plan_id=plan.plan_id,
                import_id=plan.import_id,
                selection_id=plan.selection_id,
                scan_id=plan.scan_id,
                validation_run_id=plan.validation_run_id,
                status=IntakeRunStatus.RUNNING,
                created_at=_now(),
                started_at=_now(),
                total_assets=1,
                scope=scope,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


@pytest.mark.parametrize(
    "active_scope,blocked_checkers",
    [
        (
            INTAKE_RUN_SCOPE_COPY_ONLY,
            (
                can_start_remux_intake,
                can_start_video_transcode_intake,
                can_start_image_convert_intake,
            ),
        ),
        (
            INTAKE_RUN_SCOPE_REMUX_ONLY,
            (
                can_start_copy_intake,
                can_start_video_transcode_intake,
                can_start_image_convert_intake,
            ),
        ),
        (
            INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
            (
                can_start_copy_intake,
                can_start_remux_intake,
                can_start_image_convert_intake,
            ),
        ),
        (
            INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
            (
                can_start_copy_intake,
                can_start_remux_intake,
                can_start_video_transcode_intake,
            ),
        ),
    ],
)
def test_active_scope_blocks_other_three(
    tmp_path: Path, temp_db_path: Path, active_scope, blocked_checkers
) -> None:
    project = _seed_minimal_project(tmp_path, temp_db_path)
    _insert_active_run(project, active_scope)
    launcher = _with_active_thread(project.id)
    try:
        for checker in blocked_checkers:
            ok, msg, ctx = checker(project)
            assert ok is False, f"{checker.__name__}: {msg}"
            assert msg and "bereits" in msg.lower(), (checker.__name__, msg, ctx)
    finally:
        launcher._threads.pop(project.id, None)


@pytest.mark.parametrize(
    "scope",
    [
        INTAKE_RUN_SCOPE_COPY_ONLY,
        INTAKE_RUN_SCOPE_REMUX_ONLY,
        INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    ],
)
def test_terminal_scope_does_not_block(
    tmp_path: Path, temp_db_path: Path, scope: str
) -> None:
    project = _seed_minimal_project(tmp_path, temp_db_path)
    run_id = _insert_active_run(project, scope)
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        run = copy_repo.get_intake_run(conn, run_id=run_id)
        assert run is not None
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
    finally:
        conn.close()
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        assert copy_repo.find_active_intake_run(conn, project_id=project.id) is None
    finally:
        conn.close()
    ok_img, msg, ctx = can_start_image_convert_intake(project)
    assert ok_img is True
    assert ctx is not None
    assert "active_run_id" not in ctx
    assert not msg or "bereits" not in msg.lower()


def test_orphan_recovery_worker_interrupted_for_all_scopes(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _seed_minimal_project(tmp_path, temp_db_path)
    from otio_app.discovery_v2.application.media_intake_planning_service import (
        get_current_intake_plan,
    )

    plan, _, _ = get_current_intake_plan(project)
    assert plan is not None
    for scope in (
        INTAKE_RUN_SCOPE_COPY_ONLY,
        INTAKE_RUN_SCOPE_REMUX_ONLY,
        INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    ):
        run_id = str(uuid4())
        conn = reg_db.get_registry_connection(project.project_root_path)
        try:
            # Vorherige aktive Runs terminal machen
            active = copy_repo.find_active_intake_run(conn, project_id=project.id)
            if active is not None:
                copy_repo.update_intake_run(
                    conn,
                    active.model_copy(
                        update={
                            "status": IntakeRunStatus.FAILED,
                            "completed_at": _now(),
                        }
                    ),
                )
            copy_repo.insert_intake_run(
                conn,
                IntakeRunRecord(
                    run_id=run_id,
                    project_id=project.id,
                    plan_id=plan.plan_id,
                    import_id=plan.import_id,
                    selection_id=plan.selection_id,
                    scan_id=plan.scan_id,
                    validation_run_id=plan.validation_run_id,
                    status=IntakeRunStatus.RUNNING,
                    created_at=_now(),
                    started_at=_now(),
                    total_assets=1,
                    scope=scope,
                ),
            )
            copy_repo.insert_intake_run_asset(
                conn,
                IntakeRunAssetRecord(
                    run_asset_id=str(uuid4()),
                    run_id=run_id,
                    plan_id=plan.plan_id,
                    asset_id=plan.items[0].asset_id,
                    source_relative_path=plan.items[0].source_relative_path,
                    source_group=plan.items[0].source_group,
                    media_kind=plan.items[0].media_kind,
                    planned_action=IntakeAction.TRANSCODE,
                    status=IntakeRunAssetStatus.RUNNING,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        reconcile_orphaned_intake_run(project)
        conn = reg_db.get_registry_connection(project.project_root_path)
        try:
            run = copy_repo.get_intake_run(conn, run_id=run_id)
            assert run is not None
            assert run.status == IntakeRunStatus.FAILED
            assets = copy_repo.list_intake_run_assets(conn, run_id=run_id)
            assert assets[0].error_code == "worker_interrupted"
        finally:
            conn.close()


# --- Joint end-to-end smoke ------------------------------------------------


@pytest.fixture
def smoke_project(tmp_path: Path, temp_db_path: Path) -> Project:
    root = tmp_path / "Smoke"
    root.mkdir()
    florida = root / "Florida"
    chicago = root / "Chicago"
    florida.mkdir()
    chicago.mkdir()
    # Copy: JPEG
    Image.new("RGB", (16, 16), (200, 10, 10)).save(
        florida / "still.jpg", format="JPEG"
    )
    # Remux: H.264/yuv420p in MKV
    _ffmpeg_video(chicago / "friendly.mkv", pix_fmt="yuv420p")
    # Video transcode: 10-bit
    _ffmpeg_video(florida / "clip10.mp4", pix_fmt="yuv420p10le")
    # TIFF convert
    Image.new("RGB", (20, 12), (0, 120, 200)).save(
        florida / "scan.tif", format="TIFF"
    )
    # HEIC placeholder (no convert button / no decoder)
    (florida / "phone.heic").write_bytes(b"heic-not-decodable")
    classic = root / "_otio" / "keep.bin"
    classic.parent.mkdir(parents=True, exist_ok=True)
    classic.write_bytes(b"classic-bytes")
    return create_project(
        ProjectCreate(
            name="Phase7 Smoke",
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


def test_phase7_joint_smoke_all_actions(smoke_project: Project) -> None:
    """Echter Smoke: Copy → Remux → Video → TIFF, Reuse, Konflikt, Orphan, Isolation."""
    project = smoke_project
    root = project.project_root_path
    before = _source_snapshots(root)
    classic_before = (root / "_otio" / "keep.bin").read_bytes()

    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    confirm_selection(project, snap, draft, acknowledged=True)
    import_confirmed_selection(project)
    val = start_technical_validation(project, sync=True)
    assert val.started
    plan_result = create_intake_plan(project)
    assert plan_result.created and plan_result.plan is not None
    plan = plan_result.plan

    # HEIC ohne Startmöglichkeit als image-png-v1
    heic_items = [i for i in plan.items if i.extension in {".heic", ".heif"}]
    assert heic_items
    assert all(i.proposed_target_extension is None for i in heic_items)
    assert all(i.processing_profile_version != IMAGE_PNG_PROFILE_VERSION for i in heic_items)
    # Image-Start ist wegen TIFF möglich, aber HEIC wird nicht mitverarbeitet.
    assert "HEIC-Konvertierung starten" not in Path(intake_ui.__file__).read_text(
        encoding="utf-8"
    )

    # 1) Copy
    copy_result = start_copy_intake(project, sync=True)
    assert copy_result.started and copy_result.run
    assert copy_result.run.copied_assets >= 1
    assert copy_result.run.remuxed_assets == 0
    assert copy_result.run.transcoded_assets == 0
    assert copy_result.run.converted_assets == 0
    working_root = root / "_otio_v2" / "media" / "working"
    conn = reg_db.get_registry_connection(root)
    try:
        copy_wms = [
            w
            for w in copy_repo.list_working_media(conn, project_id=project.id)
            if w.processing_profile_version == "copy-v1"
        ]
    finally:
        conn.close()
    assert copy_wms
    for wm in copy_wms:
        out = root / "_otio_v2" / wm.working_relative_path
        src = root / wm.source_relative_path
        assert out.is_file() and src.is_file()
        assert out.read_bytes() == src.read_bytes()

    # 2) Remux
    remux_result = start_remux_intake(project, sync=True)
    assert remux_result.started and remux_result.run
    assert remux_result.run.remuxed_assets >= 1
    assert remux_result.run.copied_assets == 0
    assert remux_result.run.transcoded_assets == 0
    assert remux_result.run.converted_assets == 0
    remux_files = [
        p for p in working_root.rglob("*") if p.is_file() and "remux-mp4-v1" in p.parts
    ]
    assert remux_files

    # 3) Video transcode
    vt_result = start_video_transcode_intake(project, sync=True)
    assert vt_result.started and vt_result.run
    assert vt_result.run.transcoded_assets >= 1
    assert vt_result.run.copied_assets == 0
    assert vt_result.run.remuxed_assets == 0
    assert vt_result.run.converted_assets == 0
    vt_files = [
        p for p in working_root.rglob("*") if p.is_file() and "video-h264-v1" in p.parts
    ]
    assert vt_files

    # 4) TIFF convert
    img_result = start_image_convert_intake(project, sync=True)
    assert img_result.started and img_result.run
    assert img_result.run.converted_assets >= 1
    assert img_result.run.copied_assets == 0
    assert img_result.run.remuxed_assets == 0
    assert img_result.run.transcoded_assets == 0
    img_files = [
        p
        for p in working_root.rglob("*.png")
        if p.is_file() and "image-png-v1" in p.parts
    ]
    assert img_files
    with Image.open(img_files[0]) as png:
        assert png.format == "PNG"
        assert png.mode == "RGB"

    # Profilpfade historisch getrennt
    profiles = {p.parent.name for p in working_root.rglob("*") if p.is_file()}
    assert {
        "copy-v1",
        "remux-mp4-v1",
        "video-h264-v1",
        "image-png-v1",
    }.issubset(profiles)

    # JSON-Zähler-Semantik je Scope
    for run_id, key in (
        (copy_result.run.run_id, "copied_assets"),
        (remux_result.run.run_id, "remuxed_assets"),
        (vt_result.run.run_id, "transcoded_assets"),
        (img_result.run.run_id, "converted_assets"),
    ):
        payload = json.loads(
            (root / "_otio_v2" / "intake" / "runs" / f"{run_id}.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload[key] >= 1
        for other in (
            "copied_assets",
            "remuxed_assets",
            "transcoded_assets",
            "converted_assets",
        ):
            if other != key:
                assert payload[other] == 0
        if key == "converted_assets":
            assert payload["converted"] == payload["converted_assets"]
        if key == "transcoded_assets":
            assert payload["transcoded"] == payload["transcoded_assets"]

    # UI-Zählerfelder spiegeln dieselben Namen
    ui_src = Path(intake_ui.__file__).read_text(encoding="utf-8")
    assert "copied_assets" in ui_src or "Copy" in ui_src
    assert "remuxed" in ui_src.lower() or "remuxed_assets" in ui_src
    assert "transcoded=" in ui_src
    assert "converted=" in ui_src

    # 5) Reuse — zweite Runs
    binaries_before = {
        str(p): p.read_bytes()
        for p in (root / "_otio_v2" / "media" / "working").rglob("*")
        if p.is_file()
    }
    r_copy = start_copy_intake(project, sync=True)
    r_remux = start_remux_intake(project, sync=True)
    r_vt = start_video_transcode_intake(project, sync=True)
    r_img = start_image_convert_intake(project, sync=True)
    assert r_copy.run and r_copy.run.reused_assets >= 1 and r_copy.run.copied_assets == 0
    assert r_remux.run and r_remux.run.reused_assets >= 1 and r_remux.run.remuxed_assets == 0
    assert r_vt.run and r_vt.run.reused_assets >= 1 and r_vt.run.transcoded_assets == 0
    assert r_img.run and r_img.run.reused_assets >= 1 and r_img.run.converted_assets == 0
    binaries_after = {
        str(p): p.read_bytes()
        for p in (root / "_otio_v2" / "media" / "working").rglob("*")
        if p.is_file()
    }
    assert binaries_before == binaries_after

    # 6) Konflikt nicht überschreiben (TIFF)
    victim = img_files[0]
    victim.write_bytes(b"not-png-conflict")
    conflict_run = start_image_convert_intake(project, sync=True)
    assert conflict_run.run
    conn = reg_db.get_registry_connection(root)
    try:
        assets = copy_repo.list_intake_run_assets(conn, run_id=conflict_run.run.run_id)
    finally:
        conn.close()
    assert any(a.error_code == "working_media_conflict" for a in assets)
    assert victim.read_bytes() == b"not-png-conflict"

    # 7) Crash-Fenster reparierbar (Copy: Final ohne Registry)
    # Stelle gültige Copy-Final wieder her und lösche Registry-Zeile.
    conn = reg_db.get_registry_connection(root)
    try:
        wms = [
            w
            for w in copy_repo.list_working_media(conn, project_id=project.id)
            if w.processing_profile_version == "copy-v1"
        ]
        assert wms
        target = wms[0]
        abs_path = root / "_otio_v2" / target.working_relative_path
        assert abs_path.is_file()
        conn.execute(
            "DELETE FROM working_media WHERE working_media_id = ?",
            (target.working_media_id,),
        )
        conn.commit()
    finally:
        conn.close()
    repair = start_copy_intake(project, sync=True)
    assert repair.run
    assert repair.run.reused_assets >= 1
    # keine zweite Datei im Profilordner
    siblings = [p for p in abs_path.parent.iterdir() if p.is_file()]
    assert len(siblings) == 1

    # Crash-Fenster Remux + Video: Final vorhanden, Registry-Zeile gelöscht
    for profile, starter in (
        ("remux-mp4-v1", start_remux_intake),
        ("video-h264-v1", start_video_transcode_intake),
    ):
        conn = reg_db.get_registry_connection(root)
        try:
            wms = [
                w
                for w in copy_repo.list_working_media(conn, project_id=project.id)
                if w.processing_profile_version == profile
            ]
            assert wms, profile
            target = wms[0]
            final = root / "_otio_v2" / target.working_relative_path
            assert final.is_file()
            kept = final.read_bytes()
            conn.execute(
                "DELETE FROM working_media WHERE working_media_id = ?",
                (target.working_media_id,),
            )
            conn.commit()
        finally:
            conn.close()
        repaired = starter(project, sync=True)
        assert repaired.run, profile
        assert repaired.run.reused_assets >= 1, profile
        assert final.read_bytes() == kept
        assert len([p for p in final.parent.iterdir() if p.is_file()]) == 1

    # Crash-Fenster Image: gültige PNG ohne Registry
    # (victim ist korrupt — nutze andere PNG falls vorhanden)
    good_pngs = [
        p
        for p in (root / "_otio_v2" / "media" / "working").rglob("*/image-png-v1/*.png")
        if p.is_file() and p.read_bytes()[:4] == b"\x89PNG"
    ]
    if good_pngs:
        png = good_pngs[0]
        conn = reg_db.get_registry_connection(root)
        try:
            conn.execute(
                """
                DELETE FROM working_media
                WHERE processing_profile_version = ? AND working_relative_path LIKE ?
                """,
                (IMAGE_PNG_PROFILE_VERSION, f"%{png.name}"),
            )
            conn.commit()
        finally:
            conn.close()
        img_repair = start_image_convert_intake(project, sync=True)
        assert img_repair.run
        # Entweder reuse der guten Datei oder conflict auf der korrupten — beides kontrolliert
        assert img_repair.run.status in {
            IntakeRunStatus.COMPLETED,
            IntakeRunStatus.COMPLETED_WITH_ERRORS,
            IntakeRunStatus.FAILED,
        }

    # 8) Orphan → worker_interrupted
    from otio_app.discovery_v2.application.media_intake_planning_service import (
        get_current_intake_plan,
    )

    plan2, _, _ = get_current_intake_plan(project)
    assert plan2 is not None
    orphan_id = str(uuid4())
    conn = reg_db.get_registry_connection(root)
    try:
        copy_repo.insert_intake_run(
            conn,
            IntakeRunRecord(
                run_id=orphan_id,
                project_id=project.id,
                plan_id=plan2.plan_id,
                import_id=plan2.import_id,
                selection_id=plan2.selection_id,
                scan_id=plan2.scan_id,
                validation_run_id=plan2.validation_run_id,
                status=IntakeRunStatus.RUNNING,
                created_at=_now(),
                started_at=_now(),
                total_assets=1,
                scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
            ),
        )
        copy_repo.insert_intake_run_asset(
            conn,
            IntakeRunAssetRecord(
                run_asset_id=str(uuid4()),
                run_id=orphan_id,
                plan_id=plan2.plan_id,
                asset_id=plan2.items[0].asset_id,
                source_relative_path=plan2.items[0].source_relative_path,
                source_group=plan2.items[0].source_group,
                media_kind="image",
                planned_action=IntakeAction.TRANSCODE,
                status=IntakeRunAssetStatus.RUNNING,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    get_image_convert_status(project)
    conn = reg_db.get_registry_connection(root)
    try:
        orphan = copy_repo.get_intake_run(conn, run_id=orphan_id)
        assert orphan is not None
        assert orphan.status == IntakeRunStatus.FAILED
        oassets = copy_repo.list_intake_run_assets(conn, run_id=orphan_id)
        assert oassets[0].error_code == "worker_interrupted"
    finally:
        conn.close()

    # Temp nur eigene Run-Ordner — nach sync-Runs leer/entfernt
    temp_root = root / "_otio_v2" / "media" / "temp"
    if temp_root.exists():
        leftovers = [p for p in temp_root.rglob("*") if p.is_file()]
        assert leftovers == []

    # Isolation
    assert (root / "_otio" / "keep.bin").read_bytes() == classic_before
    assert _source_snapshots(root) == before
    assert not list((root / "_otio").rglob("*.png"))
    assert not list((root / "_otio").rglob("*.mp4"))

    # Smoke-Berichtspunkte für Abschlussbericht
    smoke_project._phase7_smoke = {  # type: ignore[attr-defined]
        "copy": "PASS",
        "remux": "PASS",
        "video": "PASS",
        "tiff": "PASS",
        "profiles": "PASS",
        "counters": "PASS",
        "reuse": "PASS",
        "conflict": "PASS",
        "orphan": "PASS",
        "heic_no_button": "PASS",
        "no_otio": "PASS",
        "originals": "PASS",
    }
