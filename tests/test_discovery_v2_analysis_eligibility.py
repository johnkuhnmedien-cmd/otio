"""Phase 8A: Eligibility — exakte Working-Media-Bindung, keine Heuristik."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    expected_working_binding,
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
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    IMAGE_CONVERT_ACTION,
    IMAGE_PNG_PROFILE_VERSION,
    IntakeAction,
    IntakePlanItem,
    IntakePlanItemStatus,
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
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _write(root / "Florida" / "clip.mp4", b"video-copy-bytes")
    _write(root / "Florida" / "still.jpg", b"jpeg-copy-bytes")
    _write(root / "Florida" / "scan.tif", b"tiff-bytes-fake")
    _write(root / "Florida" / "sound.wav", b"audio-copy-bytes")
    _write(root / "Florida" / "phone.heic", b"heic-not-supported")
    _write(root / "Chicago" / "need_remux.mkv", b"remux-source")
    _write(root / "Chicago" / "need_vt.mp4", b"transcode-source")
    _write(root / "_otio" / "classic.bin", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Analysis Eligibility",
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
                    "image_format": "TIFF" if name.endswith(".tif") else "JPEG",
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


def _insert_wm(
    project: Project,
    *,
    asset_id: str,
    source_sha256: str,
    action: str,
    profile: str,
    status: str = "completed",
    output_sha256: str | None = None,
    plan_id: str,
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
        source_relative_path="Florida/ignored-for-selection.mp4",
        working_relative_path=(
            f"media/working/{asset_id}/{source_sha256}/{profile}/{asset_id}{extension}"
        ),
        source_sha256=source_sha256,
        output_sha256=output_sha256 or ("f" * 64),
        media_kind=media_kind,
        extension=extension,
        action=action,
        processing_profile_version=profile,
        status=WorkingMediaStatus.COMPLETED
        if status == "completed"
        else WorkingMediaStatus.FAILED,
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
        # Für Status ready/failed: direkt SQL, damit ready nicht gemappt wird.
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


def _by_path(plan, fragment: str):
    for item in plan.items:
        if fragment in item.source_relative_path:
            return item
    raise AssertionError(f"Plan-Item mit {fragment!r} nicht gefunden")


def test_expected_binding_from_plan_item_no_priority() -> None:
    base = dict(
        asset_id="a",
        validation_id="v",
        source_relative_path="Florida/x.mp4",
        source_group="Florida",
        media_kind="video",
        source_sha256="a" * 64,
        extension=".mp4",
        status=IntakePlanItemStatus.PLANNED,
        reason_code="x",
        reason_detail="x",
    )
    copy_item = IntakePlanItem(
        **base, planned_action=IntakeAction.COPY, processing_profile_version="1"
    )
    remux_item = IntakePlanItem(
        **base,
        planned_action=IntakeAction.REMUX,
        processing_profile_version="1",
    )
    vt_item = IntakePlanItem(
        **base,
        planned_action=IntakeAction.TRANSCODE,
        processing_profile_version="1",
    )
    tiff_item = IntakePlanItem(
        **{**base, "media_kind": "image", "extension": ".tif"},
        planned_action=IntakeAction.TRANSCODE,
        processing_profile_version=IMAGE_PNG_PROFILE_VERSION,
    )
    heic_item = IntakePlanItem(
        **{**base, "media_kind": "image", "extension": ".heic"},
        planned_action=IntakeAction.TRANSCODE,
        processing_profile_version="1",
    )
    audio_item = IntakePlanItem(
        **{**base, "media_kind": "audio", "extension": ".wav"},
        planned_action=IntakeAction.COPY,
        processing_profile_version="1",
    )
    assert expected_working_binding(copy_item) == (
        COPY_WORKING_ACTION,
        COPY_WORKING_PROFILE_VERSION,
    )
    assert expected_working_binding(remux_item) == (
        REMUX_WORKING_ACTION,
        REMUX_WORKING_PROFILE_VERSION,
    )
    assert expected_working_binding(vt_item) == (
        VIDEO_TRANSCODE_ACTION,
        VIDEO_H264_PROFILE_VERSION,
    )
    assert expected_working_binding(tiff_item) == (
        IMAGE_CONVERT_ACTION,
        IMAGE_PNG_PROFILE_VERSION,
    )
    assert expected_working_binding(heic_item) is None
    assert expected_working_binding(audio_item) == (
        COPY_WORKING_ACTION,
        COPY_WORKING_PROFILE_VERSION,
    )


def test_eligibility_matrix_and_smoke(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)

    copy_item = _by_path(plan, "clip.mp4")
    remux_item = _by_path(plan, "need_remux")
    vt_item = _by_path(plan, "need_vt")
    tiff_item = _by_path(plan, "scan.tif")
    audio_item = _by_path(plan, "sound.wav")
    heic_item = _by_path(plan, "phone.heic")
    jpeg_item = _by_path(plan, "still.jpg")

    # A: aktuelle vollständige Kette — passende completed WMs
    _insert_wm(
        discovery_project,
        asset_id=copy_item.asset_id,
        source_sha256=copy_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="video",
    )
    _insert_wm(
        discovery_project,
        asset_id=remux_item.asset_id,
        source_sha256=remux_item.source_sha256 or "",
        action=REMUX_WORKING_ACTION,
        profile=REMUX_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="video",
    )
    _insert_wm(
        discovery_project,
        asset_id=vt_item.asset_id,
        source_sha256=vt_item.source_sha256 or "",
        action=VIDEO_TRANSCODE_ACTION,
        profile=VIDEO_H264_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="video",
        output_sha256="1" * 64,
    )
    # B: historische Copy + aktuelle Transcode — Plan verlangt Transcode
    _insert_wm(
        discovery_project,
        asset_id=vt_item.asset_id,
        source_sha256=vt_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="video",
        output_sha256="2" * 64,
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    _insert_wm(
        discovery_project,
        asset_id=tiff_item.asset_id,
        source_sha256=tiff_item.source_sha256 or "",
        action=IMAGE_CONVERT_ACTION,
        profile=IMAGE_PNG_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="image",
        extension=".png",
    )
    _insert_wm(
        discovery_project,
        asset_id=audio_item.asset_id,
        source_sha256=audio_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="audio",
        extension=".wav",
    )
    _insert_wm(
        discovery_project,
        asset_id=jpeg_item.asset_id,
        source_sha256=jpeg_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        media_kind="image",
        extension=".jpg",
    )

    view = get_analysis_eligibility_view(discovery_project)
    assert view.ok is True
    by_id = {i.asset_id: i for i in view.items}

    assert by_id[copy_item.asset_id].eligible is True
    assert by_id[remux_item.asset_id].eligible is True
    assert by_id[vt_item.asset_id].eligible is True
    assert by_id[vt_item.asset_id].actual_processing_profile_version == (
        VIDEO_H264_PROFILE_VERSION
    )
    assert by_id[vt_item.asset_id].output_sha256 == "1" * 64
    assert by_id[tiff_item.asset_id].eligible is True
    assert by_id[jpeg_item.asset_id].eligible is True

    # Audio not_applicable, kein Run
    assert by_id[audio_item.asset_id].eligible is False
    assert by_id[audio_item.asset_id].reason_code == "not_applicable"
    assert view.analysis_run_count == 0

    # HEIC ohne WM
    assert by_id[heic_item.asset_id].eligible is False
    assert by_id[heic_item.asset_id].reason_code in {
        "analysis_working_media_missing",
        "analysis_profile_mismatch",
    }


def test_profile_mismatch_and_failed_ready(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    remux_item = _by_path(plan, "need_remux")
    # C: Plan remux, nur copy vorhanden
    _insert_wm(
        discovery_project,
        asset_id=remux_item.asset_id,
        source_sha256=remux_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )
    view = get_analysis_eligibility_view(discovery_project)
    item = next(i for i in view.items if i.asset_id == remux_item.asset_id)
    assert item.eligible is False
    assert item.reason_code == "analysis_profile_mismatch"

    copy_item = _by_path(plan, "clip.mp4")
    failed = _insert_wm(
        discovery_project,
        asset_id=copy_item.asset_id,
        source_sha256=copy_item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        status="failed",
        plan_id=plan.plan_id,
    )
    ready = _insert_wm(
        discovery_project,
        asset_id=_by_path(plan, "still.jpg").asset_id,
        source_sha256=_by_path(plan, "still.jpg").source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        status="ready",
        plan_id=plan.plan_id,
        media_kind="image",
        extension=".jpg",
    )
    del failed, ready
    view2 = get_analysis_eligibility_view(discovery_project)
    by_id = {i.asset_id: i for i in view2.items}
    assert by_id[copy_item.asset_id].reason_code == (
        "analysis_working_media_not_completed"
    )
    assert by_id[_by_path(plan, "still.jpg").asset_id].reason_code == (
        "analysis_working_media_not_completed"
    )


def test_stale_chain_and_wrong_validation_sha(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    # stale selection: neuer Scan
    run_inventory_scan(discovery_project)
    view = get_analysis_eligibility_view(discovery_project)
    assert view.ok is False
    assert view.chain_error_code in {"stale_selection", "stale_intake_plan", "stale_import"}

    # frische Kette, falscher Source-SHA am Plan-Item
    project = discovery_project
    _import_project(project)
    plan = _seed_validation_and_plan(project)
    item = _by_path(plan, "clip.mp4")
    _insert_wm(
        project,
        asset_id=item.asset_id,
        source_sha256=item.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
    )
    # Plan-Item SHA manipulieren in DB
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        conn.execute(
            """
            UPDATE intake_plan_assets
            SET source_sha256 = ?
            WHERE plan_id = ? AND asset_id = ?
            """,
            ("c" * 64, plan.plan_id, item.asset_id),
        )
        conn.commit()
    finally:
        conn.close()
    # JSON-Plan ebenfalls aktualisieren (historisch immutable — Test schreibt bewusst).
    from otio_app.discovery_v2.persistence.intake_plan_artifact_store import (
        intake_plan_path,
        load_latest_intake_plan,
    )

    loaded, _ = load_latest_intake_plan(project.project_root_path)
    assert loaded is not None
    new_items = []
    for it in loaded.items:
        if it.asset_id == item.asset_id:
            new_items.append(it.model_copy(update={"source_sha256": "c" * 64}))
        else:
            new_items.append(it)
    updated = loaded.model_copy(update={"items": new_items})
    path = intake_plan_path(project.project_root_path, updated.plan_id)
    path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")

    view2 = get_analysis_eligibility_view(project)
    assert view2.ok is True
    bad = next(i for i in view2.items if i.asset_id == item.asset_id)
    assert bad.eligible is False
    assert bad.reason_code in {"stale_validation", "analysis_working_media_missing"}


def test_no_selection_by_source_path_or_priority(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    vt = _by_path(plan, "need_vt")
    # Nur Copy mit gleichem source_relative_path-ähnlichem Namen — Plan will Transcode
    _insert_wm(
        discovery_project,
        asset_id=vt.asset_id,
        source_sha256=vt.source_sha256 or "",
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="9" * 64,
    )
    view = get_analysis_eligibility_view(discovery_project)
    item = next(i for i in view.items if i.asset_id == vt.asset_id)
    assert item.eligible is False
    assert item.reason_code == "analysis_profile_mismatch"
    assert item.output_sha256 == "9" * 64


def test_ambiguous_working_media(discovery_project: Project) -> None:
    _import_project(discovery_project)
    plan = _seed_validation_and_plan(discovery_project)
    item = _by_path(plan, "clip.mp4")
    sha = item.source_sha256 or ""
    # Produktive Unique-Constraint verhindert echte Duplikate (siehe R1-Constraint-Test).
    # Service-Pfad gegen mehrere Repository-Treffer mit Fake-Raw-Liste.
    wm1 = _insert_wm(
        discovery_project,
        asset_id=item.asset_id,
        source_sha256=sha,
        action=COPY_WORKING_ACTION,
        profile=COPY_WORKING_PROFILE_VERSION,
        plan_id=plan.plan_id,
        output_sha256="a" * 64,
    )

    import otio_app.discovery_v2.application.asset_analysis_eligibility_service as svc

    fake = [
        {
            "working_media_id": wm1.working_media_id,
            "project_id": discovery_project.id,
            "asset_id": item.asset_id,
            "source_sha256": sha,
            "output_sha256": "a" * 64,
            "action": COPY_WORKING_ACTION,
            "processing_profile_version": COPY_WORKING_PROFILE_VERSION,
            "status": "completed",
        },
        {
            "working_media_id": str(uuid4()),
            "project_id": discovery_project.id,
            "asset_id": item.asset_id,
            "source_sha256": sha,
            "output_sha256": "b" * 64,
            "action": COPY_WORKING_ACTION,
            "processing_profile_version": COPY_WORKING_PROFILE_VERSION,
            "status": "completed",
        },
    ]

    original = svc._list_working_media_raw

    def _fake(conn, *, project_id, asset_id, source_sha256, action, processing_profile_version):
        if asset_id == item.asset_id and action == COPY_WORKING_ACTION:
            return fake
        return original(
            conn,
            project_id=project_id,
            asset_id=asset_id,
            source_sha256=source_sha256,
            action=action,
            processing_profile_version=processing_profile_version,
        )

    svc._list_working_media_raw = _fake  # type: ignore[assignment]
    try:
        view = get_analysis_eligibility_view(discovery_project)
        el = next(i for i in view.items if i.asset_id == item.asset_id)
        assert el.eligible is False
        assert el.reason_code == "analysis_working_media_ambiguous"
    finally:
        svc._list_working_media_raw = original  # type: ignore[assignment]
