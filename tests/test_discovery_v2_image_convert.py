"""Tests: Discovery V2 TIFF→PNG Image Convert (Phase 7C3A)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from otio_app.discovery_v2.adapters.image_convert import (
    ImageConvertError,
    publish_image_png_v1,
)
from otio_app.discovery_v2.adapters.image_probe import detect_bigtiff, probe_image_file
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import can_start_copy_intake
from otio_app.discovery_v2.application.image_convert_service import (
    can_start_image_convert_intake,
    get_image_convert_status,
    list_image_convert_plan_item_views,
    start_image_convert_intake,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.remux_intake_service import can_start_remux_intake
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.video_transcode_service import (
    can_start_video_transcode_intake,
)
from otio_app.discovery_v2.domain.media_intake import (
    IMAGE_PNG_PROFILE_VERSION,
    INTAKE_RUN_SCOPE_COPY_ONLY,
    INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    IntakeAction,
    IntakePlanItemStatus,
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
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_working_relative_path,
)
from otio_app.discovery_v2.ui import media_intake_page as intake_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project


@pytest.fixture(autouse=True)
def _reset_launcher():
    reset_intake_job_launcher_for_tests()
    yield
    reset_intake_job_launcher_for_tests()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_tiff(path: Path, image: Image.Image, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="TIFF", **kwargs)


def _rgb_tiff(path: Path, size=(32, 24), color=(200, 40, 40)) -> None:
    _save_tiff(path, Image.new("RGB", size, color))


def _rgba_tiff(path: Path, size=(16, 16)) -> None:
    im = Image.new("RGBA", size, (10, 20, 30, 128))
    _save_tiff(path, im)


def _gray_tiff(path: Path) -> None:
    _save_tiff(path, Image.new("L", (12, 10), 180))


def _mode1_tiff(path: Path) -> None:
    _save_tiff(path, Image.new("1", (8, 8), 1))


def _la_tiff(path: Path) -> None:
    _save_tiff(path, Image.new("LA", (10, 10), (100, 200)))


def _palette_tiff(path: Path, *, with_transparency: bool) -> None:
    if with_transparency:
        # TIFF erhält Palette+Alpha zuverlässig als Modus PA.
        im = Image.new("PA", (8, 8), (1, 200))
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 0, 3, 3), fill=(1, 255))
        draw.rectangle((4, 4, 7, 7), fill=(2, 0))
        _save_tiff(path, im)
        return
    im = Image.new("P", (8, 8))
    im.putpalette([i % 256 for i in range(768)])
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 3, 3), fill=1)
    draw.rectangle((4, 4, 7, 7), fill=2)
    _save_tiff(path, im)


def _orient6_tiff(path: Path) -> None:
    im = Image.new("RGB", (20, 10), (0, 255, 0))
    # Linker Streifen rot — nach Orientation 6 (90° CW) oben.
    for y in range(10):
        im.putpixel((0, y), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    _save_tiff(path, im, exif=exif)


def _orient1_tiff(path: Path) -> None:
    im = Image.new("RGB", (14, 14), (0, 0, 255))
    exif = Image.Exif()
    exif[274] = 1
    _save_tiff(path, im, exif=exif)


def _orient8_tiff(path: Path) -> None:
    im = Image.new("RGB", (20, 10), (0, 128, 255))
    exif = Image.Exif()
    exif[274] = 8
    _save_tiff(path, im, exif=exif)


def _multipage_tiff(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [
        Image.new("RGB", (8, 8), (i * 40, 0, 0)) for i in range(3)
    ]
    frames[0].save(
        path,
        format="TIFF",
        save_all=True,
        append_images=frames[1:],
    )


def _i16_tiff(path: Path) -> None:
    _save_tiff(path, Image.new("I;16", (8, 8), 1000))


def _cmyk_tiff(path: Path) -> None:
    _save_tiff(path, Image.new("CMYK", (8, 8), (10, 20, 30, 40)))


def _icc_tiff(path: Path) -> bool:
    """Minimaler ICC-Blob; True wenn Fixture erzeugbar."""
    # Kompakter Fake-ICC reicht für info['icc_profile'] Presence.
    fake_icc = b"ACCT-000025" + b"\x00" * 116
    im = Image.new("RGB", (8, 8), (1, 2, 3))
    im.info["icc_profile"] = fake_icc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, format="TIFF", icc_profile=fake_icc)
        probed = probe_image_file(path)
        return bool(probed.has_icc_profile)
    except Exception:  # noqa: BLE001
        return False


def _corrupt_tiff(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"II*\x00not-a-real-tiff")


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts and "_otio" not in p.parts
    }


def _publish_paths(tmp_path: Path, name: str) -> tuple[Path, Path, Path, Path]:
    """project_root, source, temp, working unter _otio_v2."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    src = root / "sources" / f"{name}.tif"
    src.parent.mkdir(parents=True, exist_ok=True)
    temp = root / "_otio_v2" / "media" / "temp" / "run1" / f"{name}.tmp.png"
    digest = "a" * 64
    working = (
        root
        / "_otio_v2"
        / "media"
        / "working"
        / name
        / digest
        / "image-png-v1"
        / f"{name}.png"
    )
    return root, src, temp, working


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _rgb_tiff(root / "Florida" / "rgb.tif")
    _rgba_tiff(root / "Florida" / "alpha.tiff")
    _gray_tiff(root / "Florida" / "gray.tif")
    _orient6_tiff(root / "Florida" / "orient6.tif")
    _palette_tiff(root / "Florida" / "pal_t.tif", with_transparency=True)
    _multipage_tiff(root / "Florida" / "multi.tif")
    _i16_tiff(root / "Florida" / "deep.tif")
    (root / "Florida" / "photo.jpg").write_bytes(b"\xff\xd8\xfffakejpg")
    (root / "Florida" / "note.heic").write_bytes(b"heic-placeholder")
    classic = root / "_otio" / "classic.bin"
    classic.parent.mkdir(parents=True, exist_ok=True)
    classic.write_bytes(b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Image Convert",
            project_root=str(media_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
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


def _seed_validation_and_plan(project: Project, *, real_image_probe: bool = True):
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
            src = root / asset.source_relative_path
            digest = compute_sha256_hex(src) if src.is_file() else "0" * 64
            size = src.stat().st_size if src.is_file() else 0
            mtime = src.stat().st_mtime_ns if src.is_file() else 0
            kind = asset.media_kind.value
            image_fields: dict = {}
            width = height = None
            if kind == "image" and real_image_probe and src.is_file():
                try:
                    probed = probe_image_file(src)
                    image_fields = {
                        "image_format": probed.image_format,
                        "image_mode": probed.image_mode,
                        "image_frame_count": probed.image_frame_count,
                        "has_alpha": probed.has_alpha,
                        "has_icc_profile": probed.has_icc_profile,
                        "exif_orientation": probed.exif_orientation,
                        "image_bit_depth": probed.image_bit_depth,
                        "image_is_bigtiff": probed.image_is_bigtiff,
                    }
                    width, height = probed.width, probed.height
                except Exception:  # noqa: BLE001
                    image_fields = {}
            val_repo.insert_asset_validation(
                conn,
                AssetValidationRecord(
                    validation_id=str(uuid4()),
                    run_id=run.run_id,
                    asset_id=asset.asset_id,
                    source_relative_path=asset.source_relative_path,
                    status=AssetValidationStatus.PROBE_SUCCEEDED,
                    checked_size_bytes=size,
                    checked_mtime_ns=mtime,
                    sha256=digest,
                    media_kind=kind,
                    width=width,
                    height=height,
                    validated_at=_now(),
                    source_group=asset.source_group,
                    **image_fields,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return create_intake_plan(project)


# --- Schema / Validation ---------------------------------------------------


def test_schema_10_adds_image_columns_and_converted_counter(
    discovery_project,
) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn) == "12"
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(asset_validations)").fetchall()
    }
    for name in (
        "image_format",
        "image_mode",
        "image_frame_count",
        "has_alpha",
        "has_icc_profile",
        "exif_orientation",
        "image_bit_depth",
        "image_is_bigtiff",
    ):
        assert name in cols
    run_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(intake_runs)").fetchall()
    }
    assert "converted_assets" in run_cols
    conn.close()
    # idempotent
    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn2) == "12"
    conn2.close()


def test_migrate_schema_v9_preserves_validation_rows(
    discovery_project, imported
) -> None:
    root = discovery_project.project_root_path
    _seed_validation_and_plan(discovery_project)
    conn = reg_db.get_registry_connection(root)
    before = conn.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
    assert before > 0
    sample = conn.execute(
        "SELECT validation_id, width, sha256 FROM asset_validations LIMIT 1"
    ).fetchone()
    conn.execute("UPDATE registry_schema SET schema_version = '9'")
    conn.commit()
    conn.close()

    conn2 = reg_db.get_registry_connection(root)
    assert reg_db.read_schema_version(conn2) == "12"
    after = conn2.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
    assert after == before
    row = conn2.execute(
        "SELECT width, sha256, image_mode FROM asset_validations WHERE validation_id=?",
        (sample["validation_id"],),
    ).fetchone()
    assert row["sha256"] == sample["sha256"]
    assert row["width"] == sample["width"]
    cols = {
        r[1]
        for r in conn2.execute("PRAGMA table_info(asset_validations)").fetchall()
    }
    assert "image_mode" in cols
    conn2.close()


def test_probe_persists_rgb_rgba_palette_orientation(tmp_path: Path) -> None:
    rgb = tmp_path / "a.tif"
    rgba = tmp_path / "b.tif"
    pal = tmp_path / "c.tif"
    ori = tmp_path / "d.tif"
    _rgb_tiff(rgb)
    _rgba_tiff(rgba)
    _palette_tiff(pal, with_transparency=True)
    _orient6_tiff(ori)
    p1 = probe_image_file(rgb)
    assert p1.image_format == "TIFF"
    assert p1.image_mode == "RGB"
    assert p1.image_frame_count == 1
    assert p1.has_alpha is False
    p2 = probe_image_file(rgba)
    assert p2.has_alpha is True
    assert p2.image_mode == "RGBA"
    p3 = probe_image_file(pal)
    assert p3.has_alpha is True
    p4 = probe_image_file(ori)
    assert p4.exif_orientation == 6


def test_probe_detects_16bit_and_multipage_and_bigtiff_header(tmp_path: Path) -> None:
    deep = tmp_path / "deep.tif"
    multi = tmp_path / "multi.tif"
    _i16_tiff(deep)
    _multipage_tiff(multi)
    p = probe_image_file(deep)
    assert p.image_mode in {"I;16", "I;16L", "I;16B", "I;16N"}
    assert p.image_bit_depth == 16
    m = probe_image_file(multi)
    assert m.image_frame_count == 3
    big = tmp_path / "big.tif"
    big.write_bytes(b"II+\x00" + b"\x00" * 16)
    assert detect_bigtiff(big) is True
    classic = tmp_path / "classic.tif"
    _rgb_tiff(classic)
    assert detect_bigtiff(classic) is False


# --- Planner / Gates -------------------------------------------------------


def test_planner_sets_png_profile(discovery_project, imported) -> None:
    result = _seed_validation_and_plan(discovery_project)
    assert result.created
    plan = result.plan
    assert plan is not None
    tiffs = [
        i
        for i in plan.items
        if i.extension in {".tif", ".tiff"} and i.media_kind == "image"
    ]
    assert tiffs
    for item in tiffs:
        assert item.planned_action == IntakeAction.TRANSCODE
        assert item.proposed_target_extension == ".png"
        assert item.processing_profile_version == IMAGE_PNG_PROFILE_VERSION
    heics = [i for i in plan.items if i.extension in {".heic", ".heif"}]
    for item in heics:
        assert item.proposed_target_extension is None
        assert item.processing_profile_version != IMAGE_PNG_PROFILE_VERSION


def test_old_plan_without_profile_blocked(discovery_project, imported) -> None:
    result = _seed_validation_and_plan(discovery_project)
    plan = result.plan
    assert plan is not None
    from otio_app.discovery_v2.persistence.intake_plan_artifact_store import (
        save_intake_plan_artifact,
    )

    legacy_items = []
    for item in plan.items:
        if item.extension in {".tif", ".tiff"}:
            legacy_items.append(
                item.model_copy(
                    update={
                        "processing_profile_version": "1",
                        "proposed_target_extension": None,
                    }
                )
            )
        else:
            legacy_items.append(item)
    legacy = plan.model_copy(
        update={"plan_id": str(uuid4()), "items": legacy_items}
    )
    save_intake_plan_artifact(discovery_project.project_root_path, legacy)

    ok, msg, _ctx = can_start_image_convert_intake(discovery_project)
    assert ok is False
    assert msg and "image_conversion_profile_missing" in msg


def test_gates_ignore_non_tiff_and_block_active_runs(
    discovery_project, imported
) -> None:
    import threading

    from otio_app.discovery_v2.adapters.intake_job_launcher import (
        get_intake_job_launcher,
    )

    plan_result = _seed_validation_and_plan(discovery_project)
    ok, _, ctx = can_start_image_convert_intake(discovery_project)
    assert ok is True
    assert ctx and ctx["image_convert_item_count"] >= 1

    plan = plan_result.plan
    assert plan is not None
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
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
                started_at=_now(),
                total_assets=1,
                scope=INTAKE_RUN_SCOPE_COPY_ONLY,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    launcher = get_intake_job_launcher()
    launcher._threads[discovery_project.id] = threading.current_thread()
    try:
        ok2, msg2, _ = can_start_image_convert_intake(discovery_project)
        assert ok2 is False
        assert msg2 and ("bereits" in msg2.lower() or "Intake" in msg2)
    finally:
        launcher._threads.pop(discovery_project.id, None)


def test_ui_module_has_no_live_image_open() -> None:
    source = Path(intake_ui.__file__).read_text(encoding="utf-8")
    assert "Image.open" not in source
    assert "probe_image_file" not in source
    assert "pillow_heif" not in source
    assert "HEIC/HEIF kann in dieser Installation" in source
    assert "TIFF-Konvertierung starten" in source
    assert "discovery_v2_heic" not in source.lower()


def test_image_convert_adapter_has_no_ffmpeg() -> None:
    from otio_app.discovery_v2.adapters import image_convert as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "run_ffmpeg" not in source
    assert "ffmpeg_runner" not in source
    assert "subprocess" not in source
    assert "-loop" not in source
    assert "libx264" not in source


# --- Convert modes / blocks / smoke ----------------------------------------


def test_mode_conversions_and_pixel_digest(tmp_path: Path) -> None:
    cases = [
        ("1", _mode1_tiff, "L"),
        ("L", _gray_tiff, "L"),
        ("LA", _la_tiff, "LA"),
        ("RGB", _rgb_tiff, "RGB"),
        ("RGBA", _rgba_tiff, "RGBA"),
    ]
    for label, factory, expected_mode in cases:
        root, src, temp, out = _publish_paths(tmp_path / label, label)
        factory(src)
        sha = compute_sha256_hex(src)
        result = publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=sha,
            source_extension=".tif",
        )
        assert result.meta.output_mode == expected_mode
        assert result.meta.pixel_digest
        with Image.open(out) as im:
            assert im.format == "PNG"
            assert im.mode == expected_mode
            assert getattr(im, "n_frames", 1) == 1


def test_palette_modes(tmp_path: Path) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "p")
    _palette_tiff(src, with_transparency=False)
    publish_image_png_v1(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=out,
        expected_source_sha256=compute_sha256_hex(src),
        source_extension=".tif",
    )
    with Image.open(out) as im:
        assert im.mode == "RGB"

    root2, src2, temp2, out2 = _publish_paths(tmp_path / "pt", "pt")
    _palette_tiff(src2, with_transparency=True)
    publish_image_png_v1(
        project_root=root2,
        source_path=src2,
        temp_path=temp2,
        working_path=out2,
        expected_source_sha256=compute_sha256_hex(src2),
        source_extension=".tif",
    )
    with Image.open(out2) as im:
        assert im.mode == "RGBA"
        assert "A" in im.getbands()


def test_orientation_cases(tmp_path: Path) -> None:
    root1, src1, temp1, out1 = _publish_paths(tmp_path / "o1", "o1")
    _orient1_tiff(src1)
    r1 = publish_image_png_v1(
        project_root=root1,
        source_path=src1,
        temp_path=temp1,
        working_path=out1,
        expected_source_sha256=compute_sha256_hex(src1),
        source_extension=".tif",
    )
    assert r1.meta.orientation_applied is False
    assert r1.meta.output_width == 14

    root6, src6, temp6, out6 = _publish_paths(tmp_path / "o6", "o6")
    _orient6_tiff(src6)
    r6 = publish_image_png_v1(
        project_root=root6,
        source_path=src6,
        temp_path=temp6,
        working_path=out6,
        expected_source_sha256=compute_sha256_hex(src6),
        source_extension=".tif",
    )
    assert r6.meta.orientation_applied is True
    assert (r6.meta.output_width, r6.meta.output_height) == (10, 20)
    with Image.open(out6) as im:
        exif = im.getexif()
        assert exif.get(274) in (None, 1)

    root8, src8, temp8, out8 = _publish_paths(tmp_path / "o8", "o8")
    _orient8_tiff(src8)
    r8 = publish_image_png_v1(
        project_root=root8,
        source_path=src8,
        temp_path=temp8,
        working_path=out8,
        expected_source_sha256=compute_sha256_hex(src8),
        source_extension=".tif",
    )
    assert r8.meta.orientation_applied is True
    assert (r8.meta.output_width, r8.meta.output_height) == (10, 20)


@pytest.mark.parametrize(
    "factory,code",
    [
        (_multipage_tiff, "multipage_tiff_unsupported"),
        (_i16_tiff, "image_bit_depth_unsupported"),
        (_cmyk_tiff, "image_mode_unsupported"),
        (_corrupt_tiff, "image_decode_failed"),
    ],
)
def test_blocked_cases(tmp_path: Path, factory, code) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "x")
    factory(src)
    with pytest.raises(ImageConvertError) as exc:
        publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=compute_sha256_hex(src),
            source_extension=".tif",
        )
    assert exc.value.code == code
    assert not out.exists()


def test_float_and_lab_blocked(tmp_path: Path) -> None:
    root, src, temp, out = _publish_paths(tmp_path / "f", "f")
    Image.new("F", (4, 4), 0.5).save(src, format="TIFF")
    with pytest.raises(ImageConvertError) as exc:
        publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=compute_sha256_hex(src),
            source_extension=".tif",
        )
    assert exc.value.code in {
        "image_bit_depth_unsupported",
        "image_mode_unsupported",
    }


def test_icc_blocked_or_not_executable(tmp_path: Path) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "icc")
    ok = _icc_tiff(src)
    if not ok:
        pytest.skip("ICC-Fixture NOT_EXECUTABLE in dieser Umgebung")
    with pytest.raises(ImageConvertError) as exc:
        publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=compute_sha256_hex(src),
            source_extension=".tif",
        )
    assert exc.value.code == "image_color_profile_preservation_failed"


def test_bigtiff_blocked_via_detect(tmp_path: Path, monkeypatch) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "b")
    _rgb_tiff(src)
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.image_convert.detect_bigtiff",
        lambda _p: True,
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
    assert exc.value.code == "image_format_unsupported"


def test_renamed_non_tiff_blocked(tmp_path: Path) -> None:
    root, src, temp, out = _publish_paths(tmp_path, "fake")
    Image.new("RGB", (4, 4), (1, 2, 3)).save(src, format="PNG")
    with pytest.raises(ImageConvertError) as exc:
        publish_image_png_v1(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=out,
            expected_source_sha256=compute_sha256_hex(src),
            source_extension=".tif",
        )
    assert exc.value.code in {
        "image_format_unsupported",
        "image_decode_failed",
    }


def test_working_profile_whitelist() -> None:
    with pytest.raises(ValueError):
        build_working_relative_path(
            asset_id="a1",
            source_sha256="a" * 64,
            extension=".png",
            profile_version="evil-v1",
        )
    path = build_working_relative_path(
        asset_id="a1",
        source_sha256="a" * 64,
        extension=".png",
        profile_version=IMAGE_PNG_PROFILE_VERSION,
    )
    assert "/image-png-v1/" in path
    assert path.endswith(".png")


# --- End-to-end worker -----------------------------------------------------


def test_e2e_convert_reuse_conflict_and_counters(
    discovery_project, imported
) -> None:
    root = discovery_project.project_root_path
    before = _source_snapshots(root)
    _seed_validation_and_plan(discovery_project)

    result = start_image_convert_intake(discovery_project, sync=True)
    assert result.started
    assert result.run is not None
    run = result.run
    assert run.scope == INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY
    assert run.converted_assets >= 1
    assert run.transcoded_assets == 0
    assert run.status in {
        IntakeRunStatus.COMPLETED,
        IntakeRunStatus.COMPLETED_WITH_ERRORS,
    }

    report_path = root / "_otio_v2" / "intake" / "runs" / f"{run.run_id}.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["scope"] == "image_convert_only"
    assert payload["converted"] == payload["converted_assets"]
    assert payload["converted_assets"] >= 1
    assert payload["transcoded_assets"] == 0
    assert str(root) not in report_path.read_text(encoding="utf-8")

    # Erfolgreiche PNG vorhanden
    working_files = list((root / "_otio_v2" / "media" / "working").rglob("*.png"))
    assert working_files
    for png in working_files:
        with Image.open(png) as im:
            assert im.format == "PNG"
            assert getattr(im, "n_frames", 1) == 1

    # Idempotenz / Reuse
    first_wm_ids = set()
    conn = reg_db.get_registry_connection(root)
    try:
        for wm in copy_repo.list_working_media(conn, project_id=discovery_project.id):
            if wm.processing_profile_version == IMAGE_PNG_PROFILE_VERSION:
                first_wm_ids.add(wm.working_media_id)
    finally:
        conn.close()

    result2 = start_image_convert_intake(discovery_project, sync=True)
    assert result2.started
    assert result2.run is not None
    assert result2.run.reused_assets >= 1
    assert result2.run.converted_assets == 0

    conn = reg_db.get_registry_connection(root)
    try:
        second_ids = {
            wm.working_media_id
            for wm in copy_repo.list_working_media(
                conn, project_id=discovery_project.id
            )
            if wm.processing_profile_version == IMAGE_PNG_PROFILE_VERSION
        }
    finally:
        conn.close()
    assert first_wm_ids == second_ids

    # Konflikt: Final-Datei verderben
    victim = working_files[0]
    victim.write_bytes(b"not-a-png")
    result3 = start_image_convert_intake(discovery_project, sync=True)
    assert result3.started
    assert result3.run is not None
    conn = reg_db.get_registry_connection(root)
    try:
        assets = copy_repo.list_intake_run_assets(conn, run_id=result3.run.run_id)
    finally:
        conn.close()
    assert any(a.error_code == "working_media_conflict" for a in assets)
    # verdorbene Datei nicht „repariert“ durch Überschreiben mit Erfolgspfad
    # (Konflikt lässt Datei stehen)
    assert victim.read_bytes() == b"not-a-png"

    after = _source_snapshots(root)
    assert before == after
    assert not list((root / "_otio").rglob("*.png"))


def test_views_use_persisted_fields_only(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    views = list_image_convert_plan_item_views(discovery_project)
    assert views
    assert any(v.image_mode is not None for v in views)
    # Kein Live-Open in View-Pfad — Felder kommen aus Seed-Probe.


def test_image_run_blocks_copy_remux_video(discovery_project, imported) -> None:
    import threading

    from otio_app.discovery_v2.adapters.intake_job_launcher import (
        get_intake_job_launcher,
    )

    plan_result = _seed_validation_and_plan(discovery_project)
    plan = plan_result.plan
    assert plan is not None
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
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
                started_at=_now(),
                total_assets=1,
                scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    launcher = get_intake_job_launcher()
    launcher._threads[discovery_project.id] = threading.current_thread()
    try:
        assert can_start_copy_intake(discovery_project)[0] is False
        assert can_start_remux_intake(discovery_project)[0] is False
        assert can_start_video_transcode_intake(discovery_project)[0] is False
    finally:
        launcher._threads.pop(discovery_project.id, None)


def test_orphan_recovery_marks_worker_interrupted(
    discovery_project, imported
) -> None:
    plan_result = _seed_validation_and_plan(discovery_project)
    plan = plan_result.plan
    assert plan is not None
    run_id = str(uuid4())
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
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
                started_at=_now(),
                total_assets=1,
                scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
            ),
        )
        from otio_app.discovery_v2.domain.media_intake import IntakeRunAssetRecord

        copy_repo.insert_intake_run_asset(
            conn,
            IntakeRunAssetRecord(
                run_asset_id=str(uuid4()),
                run_id=run_id,
                plan_id=plan.plan_id,
                asset_id=plan.items[0].asset_id,
                source_relative_path=plan.items[0].source_relative_path,
                source_group=plan.items[0].source_group,
                media_kind="image",
                planned_action=IntakeAction.TRANSCODE,
                status=IntakeRunAssetStatus.RUNNING,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Status-Aufruf triggert Recovery (kein aktiver Thread).
    get_image_convert_status(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        run = copy_repo.get_intake_run(conn, run_id=run_id)
        assert run is not None
        assert run.status == IntakeRunStatus.FAILED
        assets = copy_repo.list_intake_run_assets(conn, run_id=run_id)
        assert assets[0].error_code == "worker_interrupted"
    finally:
        conn.close()
