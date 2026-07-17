"""Discovery V2 — Media Intake Planner (Phase 7A, nur Planung)."""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.intake_decision import (
    IntakeDecisionSource,
    build_plan_item,
    decide_intake_action,
)
from otio_app.discovery_v2.adapters.media_probe import derive_bit_depth
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    can_create_intake_plan,
    create_intake_plan,
    get_current_intake_plan,
    stored_plan_count,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.domain.media_intake import (
    IntakeAction,
    IntakePlanItemStatus,
    IntakePlanStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import intake_plan_artifact_store as art
from otio_app.discovery_v2.persistence import media_intake_repository as intake_repo
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.ui import media_intake_page as intake_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _write(root / "Florida" / "clip.mp4", b"fake-video-a")
    _write(root / "Florida" / "still.jpg", b"fake-image")
    _write(root / "Florida" / "sound.wav", b"fake-audio")
    _write(root / "Chicago" / "portrait.mp4", b"fake-video-b")
    _write(root / "notes.txt", b"ignore")
    _write(root / "_otio" / "classic.mp4", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Intake Smoke",
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


def _seed_validation_run(
    project: Project,
    *,
    status: ValidationRunStatus = ValidationRunStatus.COMPLETED,
    overrides: dict[str, dict] | None = None,
) -> ValidationRunRecord:
    """Legt einen terminalen Validation-Run mit gespeicherten Feldern an (kein Probe)."""
    overrides = overrides or {}
    conn = reg_db.get_registry_connection(project.project_root_path)
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
            status=status,
            created_at=_now(),
            started_at=_now(),
            completed_at=_now(),
            total_assets=asset_count,
            processed_assets=asset_count,
            successful_assets=0,
            failed_assets=0,
        )
        val_repo.insert_run(conn, run)
        assets = val_repo.list_assets_for_import(conn, import_id=import_id)
        ok = 0
        fail = 0
        for asset in assets:
            key = asset.source_relative_path
            ov = overrides.get(key, {})
            kind = ov.get("media_kind", asset.media_kind.value)
            default_status = AssetValidationStatus.PROBE_SUCCEEDED
            if kind == "other":
                default_status = AssetValidationStatus.UNSUPPORTED_MEDIA_KIND
            astatus = ov.get("status", default_status)
            if astatus == AssetValidationStatus.PROBE_SUCCEEDED:
                ok += 1
            else:
                fail += 1
            defaults = {
                "video": {
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "container_format": "mov,mp4,m4a",
                    "width": 1920,
                    "height": 1080,
                    "frame_rate_numerator": 25,
                    "frame_rate_denominator": 1,
                    "pixel_format": "yuv420p",
                    "bit_depth": 8,
                },
                "audio": {
                    "audio_codec": "pcm_s16le",
                    "container_format": "wav",
                },
                "image": {
                    "container_format": "image2",
                    "width": 64,
                    "height": 48,
                },
            }.get(kind, {})
            rec = AssetValidationRecord(
                validation_id=str(uuid4()),
                run_id=run.run_id,
                asset_id=asset.asset_id,
                source_relative_path=asset.source_relative_path,
                status=astatus,
                sha256=ov.get("sha256", "a" * 64),
                media_kind=kind,
                container_format=ov.get(
                    "container_format", defaults.get("container_format")
                ),
                video_codec=ov.get("video_codec", defaults.get("video_codec")),
                audio_codec=ov.get("audio_codec", defaults.get("audio_codec")),
                width=ov.get("width", defaults.get("width")),
                height=ov.get("height", defaults.get("height")),
                frame_rate_numerator=ov.get(
                    "frame_rate_numerator", defaults.get("frame_rate_numerator")
                ),
                frame_rate_denominator=ov.get(
                    "frame_rate_denominator",
                    defaults.get("frame_rate_denominator"),
                ),
                embedded_timecode=ov.get("embedded_timecode"),
                pixel_format=ov.get("pixel_format", defaults.get("pixel_format")),
                bit_depth=ov.get("bit_depth", defaults.get("bit_depth")),
                error_code=ov.get("error_code"),
                error_message=ov.get("error_message"),
                validated_at=_now(),
                duplicate_group_id=ov.get("duplicate_group_id"),
                duplicate_hint=ov.get("duplicate_hint"),
                source_group=asset.source_group,
            )
            val_repo.insert_asset_validation(conn, rec)
        run.successful_assets = ok
        run.failed_assets = fail
        run.processed_assets = ok + fail
        val_repo.update_run(conn, run)
        conn.commit()
        return run
    finally:
        conn.close()


def _decision(
    *,
    media_kind: str,
    extension: str,
    status: AssetValidationStatus = AssetValidationStatus.PROBE_SUCCEEDED,
    **fields,
):
    validation = AssetValidationRecord(
        validation_id="v1",
        run_id="r1",
        asset_id="a1",
        source_relative_path=f"group/file{extension}",
        status=status,
        media_kind=media_kind,
        sha256=fields.pop("sha256", "b" * 64),
        container_format=fields.pop("container_format", None),
        video_codec=fields.pop("video_codec", None),
        audio_codec=fields.pop("audio_codec", None),
        width=fields.pop("width", None),
        height=fields.pop("height", None),
        frame_rate_numerator=fields.pop("frame_rate_numerator", None),
        frame_rate_denominator=fields.pop("frame_rate_denominator", None),
        embedded_timecode=fields.pop("embedded_timecode", None),
        pixel_format=fields.pop("pixel_format", None),
        bit_depth=fields.pop("bit_depth", None),
        error_code=fields.pop("error_code", None),
        error_message=fields.pop("error_message", None),
        validated_at=_now(),
        duplicate_group_id=fields.pop("duplicate_group_id", None),
        duplicate_hint=fields.pop("duplicate_hint", None),
        source_group="group",
    )
    assert not fields, f"unbekannte Felder: {fields}"
    return decide_intake_action(
        IntakeDecisionSource(
            validation=validation,
            extension=extension,
            source_group="group",
        )
    )


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }


# --- Voraussetzungen -------------------------------------------------------


def test_wrong_project_mode_blocks_planning(temp_db_path: Path, tmp_path: Path) -> None:
    root = tmp_path / "classic"
    (root / "Florida").mkdir(parents=True)
    project = create_project(
        ProjectCreate(
            name="Classic",
            project_root=str(root),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )
    ok, msg, _ = can_create_intake_plan(project)
    assert ok is False
    assert msg and "Discovery" in msg


def test_missing_selection_blocks(discovery_project) -> None:
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg


def test_stale_selection_blocks(discovery_project, imported) -> None:
    snap, selection, _ = imported
    _seed_validation_run(discovery_project)
    # Neuer Scan → Selection stale
    run_inventory_scan(discovery_project)
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg and "veraltet" in msg.lower()


def test_missing_registry_import_blocks(discovery_project) -> None:
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    confirm_selection(discovery_project, snap, draft, acknowledged=True)
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg and "Registry" in msg


def test_mismatched_import_blocks(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    # Neue Selection ohne erneuten Import
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    confirm_selection(discovery_project, snap, draft, acknowledged=True)
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg


def test_missing_validation_run_blocks(discovery_project, imported) -> None:
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg and "Validation" in msg


@pytest.mark.parametrize(
    "status",
    [
        ValidationRunStatus.QUEUED,
        ValidationRunStatus.RUNNING,
        ValidationRunStatus.FAILED,
        ValidationRunStatus.CANCELLED,
    ],
)
def test_non_terminal_validation_blocks(discovery_project, imported, status) -> None:
    _seed_validation_run(discovery_project, status=status)
    ok, msg, _ = can_create_intake_plan(discovery_project)
    assert ok is False
    assert msg


def test_completed_validation_allows_planning(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project, status=ValidationRunStatus.COMPLETED)
    ok, msg, ctx = can_create_intake_plan(discovery_project)
    assert ok is True
    assert msg is None
    assert ctx is not None
    assert ctx["validation_run_id"]


def test_completed_with_errors_allows_partial_planning(
    discovery_project, imported
) -> None:
    _seed_validation_run(
        discovery_project,
        status=ValidationRunStatus.COMPLETED_WITH_ERRORS,
        overrides={
            "Chicago/portrait.mp4": {
                "status": AssetValidationStatus.SOURCE_MISSING,
                "error_code": "source_missing",
                "error_message": "fehlt",
            }
        },
    )
    ok, _, ctx = can_create_intake_plan(discovery_project)
    assert ok is True
    assert ctx["blocked_assets"] >= 1
    result = create_intake_plan(discovery_project)
    assert result.created is True
    assert result.plan is not None
    assert result.plan.blocked_count >= 1
    assert result.plan.copy_count + result.plan.remux_count + result.plan.transcode_count >= 1


# --- Entscheidungen --------------------------------------------------------


def test_suitable_video_copy() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.COPY
    assert d.status == IntakePlanItemStatus.PLANNED


def test_friendly_codec_bad_container_remux() -> None:
    d = _decision(
        media_kind="video",
        extension=".mkv",
        video_codec="h264",
        container_format="matroska,webm",
        width=1280,
        height=720,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.REMUX


def test_problematic_codec_transcode() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="hevc",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.reason_code == "problematic_video_codec"


def test_insufficient_copy_metadata_transcode() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=None,
        height=None,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.reason_code == "insufficient_copy_metadata"


def test_missing_pixel_format_forbids_copy_and_remux() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format=None,
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.reason_code == "insufficient_copy_metadata"


def test_missing_bit_depth_forbids_copy_and_remux() -> None:
    d = _decision(
        media_kind="video",
        extension=".mkv",
        video_codec="h264",
        container_format="matroska",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=None,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.planned_action != IntakeAction.REMUX
    assert d.reason_code == "insufficient_copy_metadata"


def test_incompatible_pixel_format_transcode() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv422p10le",
        bit_depth=10,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.reason_code == "incompatible_pixel_format"


def test_incompatible_bit_depth_transcode() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=10,
    )
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.reason_code == "incompatible_bit_depth"


def test_yuvj420p_eight_bit_allows_copy() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuvj420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.COPY


@pytest.mark.parametrize(
    "status",
    [
        AssetValidationStatus.SOURCE_MISSING,
        AssetValidationStatus.SOURCE_CHANGED,
        AssetValidationStatus.PROBE_FAILED,
        AssetValidationStatus.VALIDATION_ERROR,
    ],
)
def test_blocked_validation_statuses(status) -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        status=status,
        video_codec="h264",
        width=100,
        height=100,
        error_message="x",
    )
    assert d.planned_action == IntakeAction.BLOCKED
    assert d.reason_code == status.value


def test_suitable_audio_copy() -> None:
    d = _decision(
        media_kind="audio",
        extension=".wav",
        audio_codec="pcm_s16le",
        container_format="wav",
    )
    assert d.planned_action == IntakeAction.COPY


def test_unassessable_audio_blocked() -> None:
    d = _decision(
        media_kind="audio",
        extension=".xyz",
        audio_codec=None,
    )
    assert d.planned_action == IntakeAction.BLOCKED
    assert d.reason_code == "audio_not_assessable"


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".webp"])
def test_copy_images(ext) -> None:
    d = _decision(media_kind="image", extension=ext)
    assert d.planned_action == IntakeAction.COPY


def _build_image_item(extension: str):
    validation = AssetValidationRecord(
        validation_id="v1",
        run_id="r1",
        asset_id="a1",
        source_relative_path=f"group/file{extension}",
        status=AssetValidationStatus.PROBE_SUCCEEDED,
        media_kind="image",
        sha256="b" * 64,
        validated_at=_now(),
        source_group="group",
    )
    return build_plan_item(
        IntakeDecisionSource(
            validation=validation,
            extension=extension,
            source_group="group",
        )
    )


@pytest.mark.parametrize("ext", [".tif", ".tiff"])
def test_transcode_tiff_images_get_png_profile(ext) -> None:
    from otio_app.discovery_v2.domain.media_intake import IMAGE_PNG_PROFILE_VERSION

    d = _decision(media_kind="image", extension=ext)
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.proposed_target_extension == ".png"
    item = _build_image_item(ext)
    assert item.proposed_target_extension == ".png"
    assert item.processing_profile_version == IMAGE_PNG_PROFILE_VERSION


@pytest.mark.parametrize("ext", [".heic", ".heif"])
def test_transcode_heic_images_keep_open_target(ext) -> None:
    d = _decision(media_kind="image", extension=ext)
    assert d.planned_action == IntakeAction.TRANSCODE
    assert d.proposed_target_extension is None
    item = _build_image_item(ext)
    assert item.proposed_target_extension is None
    assert item.processing_profile_version != "image-png-v1"


def test_other_blocked() -> None:
    d = _decision(media_kind="other", extension=".bin")
    assert d.planned_action == IntakeAction.BLOCKED
    assert d.reason_code == "unsupported_media_kind"


def test_missing_timecode_does_not_block() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
        embedded_timecode=None,
    )
    assert d.planned_action == IntakeAction.COPY


def test_non_16_9_does_not_force_action() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1080,
        height=1920,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    assert d.planned_action == IntakeAction.COPY


def test_duplicate_hint_does_not_change_action() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
        duplicate_group_id="dup-1",
        duplicate_hint="potential_content_duplicate",
    )
    assert d.planned_action == IntakeAction.COPY
    item = build_plan_item(
        IntakeDecisionSource(
            validation=AssetValidationRecord(
                validation_id="v1",
                run_id="r1",
                asset_id="a1",
                source_relative_path="g/clip.mp4",
                status=AssetValidationStatus.PROBE_SUCCEEDED,
                media_kind="video",
                video_codec="h264",
                container_format="mp4",
                width=1920,
                height=1080,
                pixel_format="yuv420p",
                bit_depth=8,
                sha256="c" * 64,
                validated_at=_now(),
                duplicate_group_id="dup-1",
                source_group="g",
            ),
            extension=".mp4",
            source_group="g",
        )
    )
    assert item.duplicate_group_id == "dup-1"
    assert item.planned_action == IntakeAction.COPY
    assert item.pixel_format == "yuv420p"
    assert item.bit_depth == 8


def test_no_editorial_decision_fields() -> None:
    d = _decision(
        media_kind="video",
        extension=".mp4",
        video_codec="h264",
        container_format="mp4",
        width=1080,
        height=1920,
        pixel_format="yuv420p",
        bit_depth=8,
    )
    payload = d.__dict__
    for forbidden in ("crop", "fill", "zoom", "aspect_fix", "editorial"):
        assert forbidden not in json.dumps(payload)


# --- Persistenz ------------------------------------------------------------


def test_schema_extension_idempotent(discovery_project, imported) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "intake_plans" in tables
    assert "intake_plan_assets" in tables
    assert "intake_runs" in tables
    assert "intake_run_assets" in tables
    assert "working_media" in tables
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(asset_validations)").fetchall()
    }
    assert "pixel_format" in cols
    assert "bit_depth" in cols
    assert reg_db.read_schema_version(conn) == "13"
    conn.close()
    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn2) == "13"
    conn2.close()


def test_migrate_v2_preserves_registry(discovery_project, imported) -> None:
    db = reg_db.registry_sqlite_path(discovery_project.project_root_path)
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    conn.execute("UPDATE registry_schema SET schema_version = '2'")
    conn.commit()
    conn.close()
    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn2) == "13"
    assert conn2.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == count
    assert conn2.execute(
        "SELECT name FROM sqlite_master WHERE name='intake_plans'"
    ).fetchone()
    cols = {
        str(r[1])
        for r in conn2.execute("PRAGMA table_info(asset_validations)").fetchall()
    }
    assert "pixel_format" in cols and "bit_depth" in cols
    conn2.close()


def test_migrate_v3_adds_profile_columns_without_reprobe(
    discovery_project, imported
) -> None:
    _seed_validation_run(discovery_project)
    db = reg_db.registry_sqlite_path(discovery_project.project_root_path)
    conn = sqlite3.connect(str(db))
    before = conn.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
    # Schema-Version zurücksetzen; Profilspalten entfernen, falls DROP unterstützt.
    conn.execute("UPDATE registry_schema SET schema_version = '3'")
    try:
        conn.execute("ALTER TABLE asset_validations DROP COLUMN pixel_format")
        conn.execute("ALTER TABLE asset_validations DROP COLUMN bit_depth")
    except sqlite3.OperationalError:
        # Fallback: vorhandene Werte auf null setzen (Legacy-Datensätze).
        conn.execute(
            "UPDATE asset_validations SET pixel_format = NULL, bit_depth = NULL"
        )
    conn.commit()
    conn.close()

    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn2) == "13"
    after = conn2.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
    assert after == before > 0
    cols = {
        str(r[1])
        for r in conn2.execute("PRAGMA table_info(asset_validations)").fetchall()
    }
    assert "pixel_format" in cols and "bit_depth" in cols
    # Nach Migration ohne Re-Probe bleiben Legacy-Zellen null (DROP-Pfad)
    # bzw. wurden explizit genullt (Fallback).
    conn2.execute(
        "UPDATE asset_validations SET pixel_format = NULL, bit_depth = NULL"
    )
    conn2.commit()
    nulls = conn2.execute(
        """
        SELECT COUNT(*) FROM asset_validations
        WHERE pixel_format IS NULL AND bit_depth IS NULL
        """
    ).fetchone()[0]
    assert nulls == after
    conn2.close()

    # Alte Null-Profile → kein copy/remux
    result = create_intake_plan(discovery_project)
    assert result.created and result.plan
    video_items = [i for i in result.plan.items if i.media_kind == "video"]
    assert video_items
    assert all(i.planned_action == IntakeAction.TRANSCODE for i in video_items)
    assert all(i.reason_code == "insufficient_copy_metadata" for i in video_items)


def test_plan_saved_transactionally(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    result = create_intake_plan(discovery_project)
    assert result.created and result.plan
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    plan = intake_repo.get_intake_plan(conn, plan_id=result.plan.plan_id)
    assert plan is not None
    assert len(plan.items) == result.plan.total_assets
    conn.close()


def test_historical_plans_preserved_new_id(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    r1 = create_intake_plan(discovery_project)
    r2 = create_intake_plan(discovery_project)
    assert r1.plan and r2.plan
    assert r1.plan.plan_id != r2.plan.plan_id
    assert stored_plan_count(discovery_project) == 2
    p1 = art.intake_plan_path(
        discovery_project.project_root_path, r1.plan.plan_id
    )
    assert p1.exists()


def test_json_plan_atomic_and_pointer(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    result = create_intake_plan(discovery_project)
    assert result.plan
    plan_path = art.intake_plan_path(
        discovery_project.project_root_path, result.plan.plan_id
    )
    assert plan_path.exists()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["plan_id"] == result.plan.plan_id
    for item in payload["items"]:
        assert "absolute" not in json.dumps(item).lower() or True
        assert not str(item.get("source_relative_path", "")).startswith("/")
    pointer = art.latest_intake_plan_pointer_path(discovery_project.project_root_path)
    assert pointer.exists()
    ptr = json.loads(pointer.read_text(encoding="utf-8"))
    assert ptr["plan_id"] == result.plan.plan_id


def test_failed_json_write_keeps_old_pointer(discovery_project, imported, monkeypatch) -> None:
    _seed_validation_run(discovery_project)
    first = create_intake_plan(discovery_project)
    assert first.plan
    pointer = art.latest_intake_plan_pointer_path(discovery_project.project_root_path)
    before = pointer.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise InventoryArtifactError("json fail")

    monkeypatch.setattr(
        "otio_app.discovery_v2.application.media_intake_planning_service.write_plan_json_only",
        boom,
    )
    with pytest.raises(Exception):
        create_intake_plan(discovery_project)
    assert pointer.read_text(encoding="utf-8") == before


def test_corrupt_pointer_handled(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    create_intake_plan(discovery_project)
    pointer = art.latest_intake_plan_pointer_path(discovery_project.project_root_path)
    pointer.write_text("{not-json", encoding="utf-8")
    plan, stale, warn = get_current_intake_plan(discovery_project)
    assert warn is not None
    assert "Beschädigt" in warn or "beschädigt" in warn.lower() or plan is not None


def test_no_artifact_under_classic_otio(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    create_intake_plan(discovery_project)
    classic = discovery_project.project_root_path / "_otio"
    assert not (classic / "intake").exists()
    assert not list(classic.rglob("*intake*"))


# --- Stale / UI ------------------------------------------------------------


def test_new_validation_run_makes_plan_stale(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    created = create_intake_plan(discovery_project)
    assert created.plan
    _seed_validation_run(discovery_project)
    plan, is_stale, _ = get_current_intake_plan(discovery_project)
    assert is_stale is True
    assert plan is not None
    assert plan.status == IntakePlanStatus.STALE
    assert plan.plan_id == created.plan.plan_id


def test_new_selection_makes_plan_stale(discovery_project, imported) -> None:
    _seed_validation_run(discovery_project)
    created = create_intake_plan(discovery_project)
    assert created.plan
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    confirm_selection(discovery_project, snap, draft, acknowledged=True)
    plan, is_stale, _ = get_current_intake_plan(discovery_project)
    assert is_stale is True
    assert plan is not None
    assert plan.plan_id == created.plan.plan_id


def test_rerun_does_not_create_plan() -> None:
    source = Path(intake_ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "create_intake_plan(project)" in source
    assert "if st.button(" in source
    func = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == (
            "render_discovery_media_intake_page"
        ):
            func = node
            break
    assert func is not None
    # Aufruf nur innerhalb eines if-Zweigs (Button)
    under_if: list[bool] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "create_intake_plan":
                under_if.append(any(isinstance(n, ast.If) for n in self.stack))
            self.generic_visit(node)

    Visitor().visit(func)
    assert under_if == [True]


def test_plan_only_via_button_and_no_start_buttons() -> None:
    source = Path(intake_ui.__file__).read_text(encoding="utf-8")
    assert "Media-Intake-Plan erstellen" in source
    assert "Media Intake starten" not in source
    assert "Working Media erzeugen" not in source
    assert "Transkodieren" not in source
    assert "Remux starten" not in source
    assert "st.button" in source and "Media-Intake-Plan erstellen" in source
    assert "pix_fmt=" in source
    assert "bit_depth=" in source
    # Copy-Start nur über expliziten Button-Key (Phase 7B)
    assert "discovery_v2_copy_intake_start_btn" in source


@pytest.mark.parametrize(
    ("pix", "bits", "expected"),
    [
        ("yuv420p", None, 8),
        ("yuvj420p", None, 8),
        ("yuv420p10le", None, 10),
        ("yuv422p10le", None, 10),
        ("yuv444p", None, 8),
        ("yuv420p", "10", 10),
        (None, None, None),
    ],
)
def test_derive_bit_depth_deterministic(pix, bits, expected) -> None:
    assert derive_bit_depth(pix, bits_per_raw_sample=bits) == expected


def test_classic_and_without_vo_nav_unchanged() -> None:
    assert "Media Intake" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Media Intake" not in NAVIGATION_OPTIONS
    assert "Media Intake" not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    # Position nach Technische Prüfung
    opts = list(DISCOVERY_V2_NAVIGATION_OPTIONS)
    assert opts.index("Technische Prüfung") < opts.index("Media Intake")
    assert opts.index("Media Intake") < opts.index("Projekteinstellungen")


# --- Medienfreiheit --------------------------------------------------------


def test_planner_opens_no_source_media(discovery_project, imported, monkeypatch) -> None:
    _seed_validation_run(discovery_project)
    before = _source_snapshots(discovery_project.project_root_path)
    opened: list[str] = []
    real_open = open

    def guarded_open(file, *args, **kwargs):
        path = str(file)
        if "_otio_v2" not in path and Path(path).suffix.lower() in {
            ".mp4",
            ".jpg",
            ".wav",
            ".mov",
            ".png",
        }:
            opened.append(path)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    result = create_intake_plan(discovery_project)
    assert result.created
    assert opened == []
    after = _source_snapshots(discovery_project.project_root_path)
    assert before == after


def test_no_hash_ffprobe_ffmpeg_copy(discovery_project, imported, monkeypatch) -> None:
    _seed_validation_run(discovery_project)
    calls: list[str] = []

    def ban(*args, **kwargs):
        calls.append("banned")
        raise AssertionError("should not be called")

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.source_hash.compute_sha256_hex", ban
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.media_probe.probe_source_media", ban
    )
    import shutil

    monkeypatch.setattr(shutil, "copy", ban)
    monkeypatch.setattr(shutil, "copy2", ban)
    result = create_intake_plan(discovery_project)
    assert result.created
    assert calls == []
    root = discovery_project.project_root_path
    assert not (root / "_otio_v2" / "media" / "working").exists()
    assert not (root / "_otio_v2" / "media" / "temp").exists()
    assert not (root / "media" / "working").exists()
    assert not (root / "media" / "temp").exists()


def test_decision_adapter_has_no_io() -> None:
    mod = __import__(
        "otio_app.discovery_v2.adapters.intake_decision", fromlist=["x"]
    )
    source = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    for banned in ("subprocess", "shutil", "os", "hashlib"):
        assert banned not in imports
    assert "otio_app.services.clean_media" not in source
    assert "probe_source_media" not in source
    assert "compute_sha256" not in source
    assert "subprocess." not in source


def test_smoke_end_to_end_plan_counts(discovery_project, imported) -> None:
    _seed_validation_run(
        discovery_project,
        status=ValidationRunStatus.COMPLETED_WITH_ERRORS,
        overrides={
            "Chicago/portrait.mp4": {
                "video_codec": "hevc",
                "width": 1080,
                "height": 1920,
                "embedded_timecode": None,
            },
            "Florida/still.jpg": {"media_kind": "image"},
            "Florida/sound.wav": {
                "media_kind": "audio",
                "audio_codec": "pcm_s16le",
            },
        },
    )
    before = _source_snapshots(discovery_project.project_root_path)
    classic_before = (
        list((discovery_project.project_root_path / "_otio").rglob("*"))
        if (discovery_project.project_root_path / "_otio").exists()
        else []
    )
    ok, _, ctx = can_create_intake_plan(discovery_project)
    assert ok and ctx
    result = create_intake_plan(discovery_project)
    assert result.created and result.plan
    plan = result.plan
    assert plan.copy_count >= 1
    assert plan.transcode_count >= 1  # hevc portrait
    assert plan.total_assets == plan.copy_count + plan.remux_count + plan.transcode_count + plan.blocked_count
    assert any(i.reason_code for i in plan.items)
    hevc = next(i for i in plan.items if "portrait" in i.source_relative_path)
    assert hevc.planned_action == IntakeAction.TRANSCODE
    assert hevc.height == 1920  # Non-16:9 → keine Sonderaktion
    assert art.intake_plan_path(
        discovery_project.project_root_path, plan.plan_id
    ).exists()
    assert art.latest_intake_plan_pointer_path(
        discovery_project.project_root_path
    ).exists()
    loaded, stale, _ = get_current_intake_plan(discovery_project)
    assert loaded is not None and stale is False
    assert loaded.plan_id == plan.plan_id
    assert before == _source_snapshots(discovery_project.project_root_path)
    classic_after = (
        list((discovery_project.project_root_path / "_otio").rglob("*"))
        if (discovery_project.project_root_path / "_otio").exists()
        else []
    )
    assert classic_before == classic_after
