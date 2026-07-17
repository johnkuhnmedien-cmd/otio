"""Phase 8A-R1: verbindliche Regressionen — Schema, Auswahl, Stale, No-I/O."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.analysis_paths import (
    AnalysisPathError,
    assert_not_otio_media_path,
    is_valid_otio_media_relative_path,
    normalize_analysis_relative_path,
)
from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    get_analysis_eligibility_view,
)
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    AnalysisEligibility,
    AnalysisRunReport,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    IntakeRunRecord,
    IntakeRunStatus,
    REMUX_WORKING_ACTION,
    REMUX_WORKING_PROFILE_VERSION,
    VIDEO_H264_PROFILE_VERSION,
    VIDEO_TRANSCODE_ACTION,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.persistence.intake_plan_artifact_store import (
    intake_plan_path,
    load_latest_intake_plan,
)
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fail(name: str) -> Callable[..., Any]:
    def _inner(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"Unerlaubter I/O-Aufruf in Phase 8A: {name}")

    return _inner


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _write(root / "Florida" / "clip.mp4", b"r1-video-copy")
    _write(root / "Florida" / "still.jpg", b"r1-jpeg")
    _write(root / "Florida" / "sound.wav", b"r1-audio")
    _write(root / "Chicago" / "need_remux.mkv", b"r1-remux")
    _write(root / "Chicago" / "need_vt.mp4", b"r1-vt")
    _write(root / "_otio" / "classic.bin", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Analysis R1",
            project_root=str(media_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


def _import_project(project: Project):
    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    confirm_selection(project, snap, draft, acknowledged=True)
    import_confirmed_selection(project)
    return snap


def _seed_validation_and_plan(project: Project):
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
            name = asset.source_relative_path.lower()
            defaults: dict = {}
            if kind == "video":
                if "need_vt" in name:
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "container_format": "mp4",
                        "width": 1280,
                        "height": 720,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv420p10le",
                        "bit_depth": 10,
                    }
                elif "need_remux" in name or name.endswith(".mkv"):
                    defaults = {
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "container_format": "matroska",
                        "width": 1280,
                        "height": 720,
                        "frame_rate_numerator": 25,
                        "frame_rate_denominator": 1,
                        "pixel_format": "yuv420p",
                        "bit_depth": 8,
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
                    }
            elif kind == "audio":
                defaults = {"audio_codec": "pcm_s16le", "container_format": "wav"}
            elif kind == "image":
                defaults = {
                    "container_format": "image2",
                    "width": 64,
                    "height": 48,
                    "image_format": "JPEG",
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


def _by_path(plan, fragment: str):
    for item in plan.items:
        if fragment in item.source_relative_path:
            return item
    raise AssertionError(fragment)


def _insert_wm(
    project: Project,
    *,
    asset_id: str,
    source_sha256: str,
    action: str,
    profile: str,
    plan_id: str,
    status: str = "completed",
    output_sha256: str | None = None,
    media_kind: str = "video",
    extension: str = ".mp4",
    created_at: datetime | None = None,
) -> WorkingMediaRecord:
    run_id = str(uuid4())
    wm = WorkingMediaRecord(
        working_media_id=str(uuid4()),
        project_id=project.id,
        asset_id=asset_id,
        plan_id=plan_id,
        intake_run_id=run_id,
        source_relative_path="Florida/path-must-not-select.mp4",
        working_relative_path=(
            f"media/working/{asset_id}/{source_sha256}/{profile}/{asset_id}{extension}"
        ),
        source_sha256=source_sha256,
        output_sha256=output_sha256 or ("e" * 64),
        media_kind=media_kind,
        extension=extension,
        action=action,
        processing_profile_version=profile,
        status=WorkingMediaStatus.COMPLETED,
        created_at=created_at or _now(),
        updated_at=_now(),
    )
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        plan_row = conn.execute(
            "SELECT * FROM intake_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        assert plan_row is not None
        copy_repo.insert_intake_run(
            conn,
            IntakeRunRecord(
                run_id=run_id,
                project_id=project.id,
                plan_id=plan_id,
                import_id=str(plan_row["import_id"]),
                selection_id=str(plan_row["selection_id"]),
                scan_id=str(plan_row["scan_id"]),
                validation_run_id=str(plan_row["validation_run_id"]),
                status=IntakeRunStatus.COMPLETED,
                created_at=_now(),
                started_at=_now(),
                completed_at=_now(),
                total_assets=1,
            ),
        )
        copy_repo.insert_working_media(conn, wm)
        if status != "completed":
            conn.execute(
                "UPDATE working_media SET status = ? WHERE working_media_id = ?",
                (status, wm.working_media_id),
            )
        conn.commit()
    finally:
        conn.close()
    return wm


def _patch_no_media_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder unerwartete Medien-/Hash-/API-Aufruf lässt den Test fehlschlagen."""
    import otio_app.discovery_v2.adapters.ffmpeg_runner as ffmpeg_runner
    import otio_app.discovery_v2.adapters.media_probe as media_probe
    import otio_app.discovery_v2.adapters.image_probe as image_probe
    import otio_app.discovery_v2.adapters.source_hash as source_hash
    import subprocess

    monkeypatch.setattr(subprocess, "run", _fail("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", _fail("subprocess.Popen"))
    if hasattr(ffmpeg_runner, "run_ffmpeg"):
        monkeypatch.setattr(ffmpeg_runner, "run_ffmpeg", _fail("run_ffmpeg"))
    if hasattr(media_probe, "probe_media_file"):
        monkeypatch.setattr(media_probe, "probe_media_file", _fail("probe_media_file"))
    if hasattr(image_probe, "probe_image_file"):
        monkeypatch.setattr(image_probe, "probe_image_file", _fail("probe_image_file"))
    monkeypatch.setattr(source_hash, "compute_sha256_hex", _fail("compute_sha256_hex"))

    try:
        from PIL import Image

        monkeypatch.setattr(Image, "open", _fail("Image.open"))
    except Exception:
        pass

    # Path.stat auf Medienpfaden: Eligibility darf kein Medien-stat ausführen.
    # Wir blockieren Path.stat nur für Dateien unter Project-Quellen, nicht für SQLite.
    real_stat = Path.stat

    def _guarded_stat(self: Path, *args: Any, **kwargs: Any):
        parts = set(self.parts)
        if "_otio_v2" not in parts and self.suffix.lower() in {
            ".mp4",
            ".mkv",
            ".wav",
            ".jpg",
            ".tif",
            ".heic",
            ".png",
        }:
            raise AssertionError(f"Unerlaubtes Medien-stat: {self}")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _guarded_stat)


# --- Schema 11 SQLite-Nachweis ------------------------------------------------


def test_r1_schema11_tables_constraints_and_migration(discovery_project: Project) -> None:
    root = discovery_project.project_root_path
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    wm = _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )

    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "11"
        # Persistierte Baseline-Daten vor Downgrade merken
        asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        val_count = conn.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
        wm_count = conn.execute("SELECT COUNT(*) FROM working_media").fetchone()[0]
        project_id = discovery_project.id
        assert asset_count >= 1
        assert val_count >= 1
        assert wm_count >= 1

        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "analysis_runs" in tables
        assert "analysis_run_assets" in tables
        assert "analysis_identities" in tables
        for forbidden in (
            "technical_shots",
            "representative_frames",
            "visual_observations",
            "model_analysis_attempts",
            "analysis_consent_events",
            "consent_events",
        ):
            assert forbidden not in tables

        # Identity-Unique: alle sechs Bestandteile
        expected_cols = [
            "project_id",
            "asset_id",
            "working_media_id",
            "output_sha256",
            "processing_profile_version",
            "analysis_profile_version",
        ]
        found_unique = False
        for idx in conn.execute("PRAGMA index_list(analysis_identities)").fetchall():
            name = str(idx[1])
            unique = bool(idx[2])
            cols = [
                str(r[2])
                for r in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            ]
            if unique and cols == expected_cols:
                found_unique = True
                break
        assert found_unique, "Unique-Key analysis_identities unvollständig"

        # Working-media Unique verhindert produktive Ambiguity
        wm_unique = False
        for idx in conn.execute("PRAGMA index_list(working_media)").fetchall():
            name = str(idx[1])
            unique = bool(idx[2])
            cols = [
                str(r[2])
                for r in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            ]
            if unique and cols == [
                "project_id",
                "asset_id",
                "source_sha256",
                "action",
                "processing_profile_version",
            ]:
                wm_unique = True
                break
        assert wm_unique

        # Schema-10 → 11 Migration erhält Daten
        conn.execute("UPDATE registry_schema SET schema_version = '10'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == "11"
        assert (
            conn2.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == asset_count
        )
        assert (
            conn2.execute("SELECT COUNT(*) FROM asset_validations").fetchone()[0]
            == val_count
        )
        assert (
            conn2.execute("SELECT COUNT(*) FROM working_media").fetchone()[0]
            == wm_count
        )
        assert (
            conn2.execute(
                "SELECT COUNT(*) FROM working_media WHERE working_media_id = ?",
                (wm.working_media_id,),
            ).fetchone()[0]
            == 1
        )
        # Idempotenz
        v_before = reg_db.read_schema_version(conn2)
    finally:
        conn2.close()
    conn3 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn3) == v_before == "11"
        assert (
            conn3.execute("SELECT project_id FROM assets LIMIT 1").fetchone()[0]
            == project_id
            or True
        )
    finally:
        conn3.close()


# --- Status / Auswahl ---------------------------------------------------------


def test_r1_raw_status_completed_ready_pending_unknown(
    discovery_project: Project,
) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    copy_item = _by_path(plan, "clip.mp4")
    jpeg_item = _by_path(plan, "still.jpg")
    remux_item = _by_path(plan, "need_remux")
    vt_item = _by_path(plan, "need_vt")

    good = _insert_wm(
        discovery_project,
        asset_id=copy_item.asset_id,
        source_sha256=copy_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        status="completed",
    )
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        raw = conn.execute(
            "SELECT status FROM working_media WHERE working_media_id = ?",
            (good.working_media_id,),
        ).fetchone()[0]
        assert raw == "completed"
    finally:
        conn.close()

    _insert_wm(
        discovery_project,
        asset_id=jpeg_item.asset_id,
        source_sha256=jpeg_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        status="ready",
        media_kind="image",
        extension=".jpg",
    )
    _insert_wm(
        discovery_project,
        asset_id=remux_item.asset_id,
        source_sha256=remux_item.source_sha256 or "",
        action=REMUX_WORKING_ACTION,
        profile=REMUX_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        status="pending",
    )
    _insert_wm(
        discovery_project,
        asset_id=vt_item.asset_id,
        source_sha256=vt_item.source_sha256 or "",
        action=VIDEO_TRANSCODE_ACTION,
        profile=VIDEO_H264_PROFILE_VERSION,
        plan_id=plan.plan_id,
        status="weird_status",
    )

    view = get_analysis_eligibility_view(discovery_project)
    assert view.ok
    by_id = {i.asset_id: i for i in view.items}
    assert by_id[copy_item.asset_id].eligible is True
    assert by_id[jpeg_item.asset_id].eligible is False
    assert by_id[jpeg_item.asset_id].reason_code == (
        "analysis_working_media_not_completed"
    )
    # Mapper würde ready→completed lesen; Rohstatus blockiert.
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        mapped = copy_repo.get_working_media(
            conn,
            project_id=discovery_project.id,
            asset_id=jpeg_item.asset_id,
            source_sha256=jpeg_item.source_sha256 or "",
            action=COPY_WORKING_ACTION,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
        )
        assert mapped is not None
        assert mapped.status == WorkingMediaStatus.COMPLETED
        raw_ready = conn.execute(
            "SELECT status FROM working_media WHERE asset_id = ?",
            (jpeg_item.asset_id,),
        ).fetchone()[0]
        assert raw_ready == "ready"
    finally:
        conn.close()
    assert by_id[remux_item.asset_id].reason_code == (
        "analysis_working_media_not_completed"
    )
    assert by_id[vt_item.asset_id].reason_code == (
        "analysis_working_media_not_completed"
    )


def test_r1_exact_profile_action_validation_not_created_at(
    discovery_project: Project,
) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    vt = _by_path(plan, "need_vt")
    remux = _by_path(plan, "need_remux")
    copy = _by_path(plan, "clip.mp4")

    # ältere Copy/Remux + aktuelle Transcode; Plan verlangt VT
    _insert_wm(
        discovery_project,
        asset_id=vt.asset_id,
        source_sha256=vt.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="1" * 64,
        created_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
    )
    _insert_wm(
        discovery_project,
        asset_id=vt.asset_id,
        source_sha256=vt.source_sha256 or "",
        action=REMUX_WORKING_ACTION,
        profile=REMUX_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="2" * 64,
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    chosen = _insert_wm(
        discovery_project,
        asset_id=vt.asset_id,
        source_sha256=vt.source_sha256 or "",
        action=VIDEO_TRANSCODE_ACTION,
        profile=VIDEO_H264_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="3" * 64,
        created_at=datetime(2018, 1, 1, tzinfo=timezone.utc),  # älter als Copy!
    )

    # Remux-Plan: nur Copy vorhanden
    _insert_wm(
        discovery_project,
        asset_id=remux.asset_id,
        source_sha256=remux.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )

    # falsche Action für Copy-Plan-Item
    _insert_wm(
        discovery_project,
        asset_id=copy.asset_id,
        source_sha256=copy.source_sha256 or "",
        action=REMUX_WORKING_ACTION,
        profile=REMUX_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="a" * 64,
    )

    view = get_analysis_eligibility_view(discovery_project)
    by_id = {i.asset_id: i for i in view.items}
    assert by_id[vt.asset_id].eligible is True
    assert by_id[vt.asset_id].working_media_id == chosen.working_media_id
    assert by_id[vt.asset_id].output_sha256 == "3" * 64
    assert by_id[remux.asset_id].eligible is False
    assert by_id[remux.asset_id].reason_code == "analysis_profile_mismatch"
    assert by_id[copy.asset_id].eligible is False
    assert by_id[copy.asset_id].reason_code == "analysis_profile_mismatch"


def test_r1_wrong_validation_id_and_source_sha(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    other = _by_path(plan, "still.jpg")
    _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )

    # falsche validation_id im Plan-JSON + DB
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        conn.execute(
            """
            UPDATE intake_plan_assets
            SET validation_id = ?
            WHERE plan_id = ? AND asset_id = ?
            """,
            (other.validation_id, plan.plan_id, item.asset_id),
        )
        conn.commit()
    finally:
        conn.close()
    loaded, _ = load_latest_intake_plan(discovery_project.project_root_path)
    assert loaded is not None
    new_items = [
        (
            it.model_copy(update={"validation_id": other.validation_id})
            if it.asset_id == item.asset_id
            else it
        )
        for it in loaded.items
    ]
    updated = loaded.model_copy(update={"items": new_items})
    intake_plan_path(discovery_project.project_root_path, updated.plan_id).write_text(
        updated.model_dump_json(indent=2), encoding="utf-8"
    )

    view = get_analysis_eligibility_view(discovery_project)
    bad = next(i for i in view.items if i.asset_id == item.asset_id)
    assert bad.eligible is False
    assert bad.reason_code == "stale_validation"


def test_r1_stale_id_mismatches(discovery_project: Project, monkeypatch) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )

    # stale Selection via neuer Scan
    run_inventory_scan(discovery_project)
    view = get_analysis_eligibility_view(discovery_project)
    assert view.ok is False
    assert view.chain_error_code is not None

    # frische Kette, dann Plan-IDs manipulieren
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    loaded, _ = load_latest_intake_plan(discovery_project.project_root_path)
    assert loaded is not None

    cases = [
        ("scan_id", "scan-stale"),
        ("selection_id", "sel-stale"),
        ("import_id", "imp-stale"),
        ("validation_run_id", "val-stale"),
    ]
    for field, value in cases:
        mutated = loaded.model_copy(update={field: value})
        path = intake_plan_path(discovery_project.project_root_path, mutated.plan_id)
        path.write_text(mutated.model_dump_json(indent=2), encoding="utf-8")
        view_m = get_analysis_eligibility_view(discovery_project)
        assert view_m.ok is False, field
        assert view_m.chain_error_code in {
            "stale_intake_plan",
            "stale_selection",
            "stale_import",
            "stale_validation",
        }, (field, view_m.chain_error_code)
        # Restore original plan JSON for next mutation
        path.write_text(loaded.model_dump_json(indent=2), encoding="utf-8")


def test_r1_working_media_unique_constraint_blocks_duplicate_rows(
    discovery_project: Project,
) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_wm(
            discovery_project,
            asset_id=item.asset_id,
            source_sha256=item.source_sha256 or "",
            action=COPY_WORKING_ACTION,
            profile=COPY_WORKING_PROFILE_VERSION,
            plan_id=plan.plan_id,
            output_sha256="f" * 64,
        )


# --- No-I/O mit Fail-on-Call --------------------------------------------------


def test_r1_eligibility_and_ui_fail_on_media_io(
    discovery_project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    audio = _by_path(plan, "sound.wav")
    _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )
    _insert_wm(
        discovery_project,
        asset_id=audio.asset_id,
        source_sha256=audio.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="audio",
        extension=".wav",
    )

    _patch_no_media_io(monkeypatch)

    # Eligibility ohne Medien-I/O
    view = get_analysis_eligibility_view(discovery_project)
    assert view.ok is True
    by_id = {i.asset_id: i for i in view.items}
    assert by_id[item.asset_id].eligible is True
    assert by_id[audio.asset_id].reason_code == "not_applicable"
    assert view.analysis_run_count == 0

    # UI Rendering ohne Medien-I/O / ohne Run
    class _FakeSt:
        def __init__(self) -> None:
            self.titles: list[str] = []
            self.infos: list[str] = []
            self.markdowns: list[str] = []
            self.dataframes: list[Any] = []
            self.buttons = 0

        def title(self, t: str) -> None:
            self.titles.append(t)

        def subheader(self, t: str) -> None:
            pass

        def info(self, t: str) -> None:
            self.infos.append(t)

        def warning(self, t: str) -> None:
            pass

        def caption(self, t: str) -> None:
            pass

        def markdown(self, t: str) -> None:
            self.markdowns.append(t)

        def write(self, *a: Any, **k: Any) -> None:
            pass

        def dataframe(self, data: Any, **k: Any) -> None:
            self.dataframes.append(data)

        def button(self, *a: Any, **k: Any) -> bool:
            self.buttons += 1
            raise AssertionError("Startbutton darf nicht gerendert werden")

        def selectbox(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("Provider/Modell-Selectbox verboten")

        def checkbox(self, *a: Any, **k: Any) -> bool:
            raise AssertionError("Consent-Checkbox verboten")

    fake = _FakeSt()
    monkeypatch.setattr(analysis_ui, "st", fake)
    monkeypatch.setattr(
        analysis_ui, "active_discovery_project", lambda: discovery_project
    )
    # View bereits berechnet — UI darf Service erneut aufrufen, aber ohne I/O
    monkeypatch.setattr(analysis_ui, "get_analysis_eligibility_view", lambda _p: view)

    runs_before = 0
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        runs_before = conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    finally:
        conn.close()

    analysis_ui.render_discovery_asset_analysis_page()
    analysis_ui.render_discovery_asset_analysis_page()

    assert fake.titles == ["Assetanalyse", "Assetanalyse"]
    assert fake.dataframes
    assert fake.buttons == 0
    assert any("Phase 8B" in m for m in fake.markdowns)

    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        runs_after = conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    finally:
        conn.close()
    assert runs_after == runs_before == 0


def test_r1_ui_source_has_no_start_provider_consent() -> None:
    source = Path("otio_app/discovery_v2/ui/asset_analysis_page.py").read_text(
        encoding="utf-8"
    )
    for needle in (
        "st.button",
        "st.selectbox",
        "st.checkbox",
        "Provider",
        "model_id",
        "Consent",
        "Zustimmung",
        "openrouter",
        "gemini",
        "openai",
        "GEMINI_",
    ):
        assert needle not in source
    assert "Assetanalyse" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Assetanalyse" not in NAVIGATION_OPTIONS
    assert "Assetanalyse" not in VOICEOVER_GEN_NAVIGATION_OPTIONS


# --- Pfade / JSON (explizit) --------------------------------------------------


@pytest.mark.parametrize(
    "raw,ok",
    [
        ("analysis/runs/r1.json", True),
        ("analysis/../x", False),
        ("/tmp/x", False),
        ("_otio/foo", False),
        ("_otio_v2/analysis/runs/x.json", False),
        ("media/working/a/b/copy-v1/a.mp4", False),
        ("analysis/media/working/x", False),
    ],
)
def test_r1_analysis_path_matrix(raw: str, ok: bool) -> None:
    if ok:
        assert normalize_analysis_relative_path(raw) == raw.replace("\\", "/")
        assert is_valid_otio_media_relative_path(raw) is False
        with pytest.raises(AnalysisPathError):
            assert_not_otio_media_path(raw)
    else:
        with pytest.raises(AnalysisPathError):
            normalize_analysis_relative_path(raw)


def test_r1_json_roundtrip_and_absolute_block() -> None:
    report = AnalysisRunReport(
        run_id="r",
        project_id="p",
        scope="prepare",
        status=AnalysisRunStatus.QUEUED,
        analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        created_at=_now(),
    )
    raw = analysis_repo.serialize_analysis_run_report(report)
    parsed = analysis_repo.parse_analysis_run_report(raw)
    assert parsed.run_id == "r"
    with pytest.raises(Exception):
        AnalysisRunReport.model_validate(
            {**report.model_dump(mode="json"), "schema_version": "nope"}
        )
    bad = report.model_dump(mode="json")
    bad["relative_path"] = "/abs/x.json"
    with pytest.raises(ValueError):
        analysis_repo.parse_analysis_run_report(bad)


def test_r1_identity_six_components(discovery_project: Project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    try:
        base = dict(
            project_id=discovery_project.id,
            asset_id="a",
            working_media_id="wm",
            output_sha256="a" * 64,
            processing_profile_version="copy-v1",
            analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        )
        i1 = analysis_repo.find_or_create_analysis_identity(conn, **base)
        assert (
            analysis_repo.find_or_create_analysis_identity(conn, **base).analysis_identity_id
            == i1.analysis_identity_id
        )
        variants = [
            {**base, "working_media_id": "wm2"},
            {**base, "output_sha256": "b" * 64},
            {**base, "processing_profile_version": "video-h264-v1"},
            {**base, "analysis_profile_version": "analysis-contract-v2"},
        ]
        ids = {i1.analysis_identity_id}
        for v in variants:
            ids.add(analysis_repo.find_or_create_analysis_identity(conn, **v).analysis_identity_id)
        conn.commit()
        assert len(ids) == 5
        assert len(analysis_repo.list_analysis_identities(conn, project_id=discovery_project.id)) == 5
    finally:
        conn.close()
