"""Application-Service: Discovery-V2 Video-Transcode-Intake (Phase 7C2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.intake_job_launcher import get_intake_job_launcher
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.media_intake_planning_service import (
    can_create_intake_plan,
    get_current_intake_plan,
)
from otio_app.discovery_v2.application.remux_intake_job_recovery import (
    reconcile_orphaned_intake_run,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
    VIDEO_TRANSCODE_WORKER_VERSION,
    IntakeAction,
    IntakePlan,
    IntakePlanItem,
    IntakePlanItemStatus,
    IntakePlanStatus,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStartResult,
    IntakeRunStatus,
    WorkingMediaRecord,
)
from otio_app.discovery_v2.domain.technical_validation import AssetValidationRecord
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    find_active_intake_run,
    get_intake_run,
    get_latest_intake_run,
    insert_intake_run,
    insert_intake_run_asset,
    list_intake_run_assets,
    list_working_media,
    new_intake_run_id,
    new_run_asset_id,
    open_registry,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    get_run as get_validation_run,
    list_asset_validations,
)
from otio_app.models import Project


@dataclass(frozen=True)
class VideoTranscodePlanItemView:
    """UI-Viewmodell: Plan-Item + persistierte Validation-Felder."""

    item: IntakePlanItem
    audio_stream_count: int | None = None
    audio_channel_count: int | None = None
    rotation_degrees: float | None = None

    @property
    def audio_channels(self) -> int | None:
        """Alias für UI/Tests."""
        return self.audio_channel_count


class VideoTranscodeServiceError(InventoryServiceError):
    """Fachlicher Fehler des Video-Transcode-Intakes."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _video_transcode_items(plan: IntakePlan) -> list[IntakePlanItem]:
    return [
        item
        for item in plan.items
        if item.planned_action == IntakeAction.TRANSCODE
        and item.status == IntakePlanItemStatus.PLANNED
        and (item.media_kind or "").strip().lower() == MediaKind.VIDEO.value
    ]


def can_start_video_transcode_intake(
    project: Project,
) -> tuple[bool, str | None, dict | None]:
    try:
        require_discovery_project(project)
    except InventoryServiceError as exc:
        return False, str(exc), None

    reconcile_orphaned_intake_run(project)

    plan_ok, plan_msg, plan_ctx = can_create_intake_plan(project)
    if not plan_ok or plan_ctx is None:
        return (
            False,
            plan_msg
            or "Keine gültige Selection/Import/Validation-Basis für Video-Transcode.",
            None,
        )

    plan, is_stale, plan_warn = get_current_intake_plan(project)
    if plan is None:
        return (
            False,
            plan_warn
            or "Kein Media-Intake-Plan vorhanden. Bitte zuerst einen Plan erstellen.",
            None,
        )
    if is_stale or plan.status == IntakePlanStatus.STALE:
        return (
            False,
            "Der aktuelle Intake-Plan ist veraltet (stale_plan). "
            "Bitte einen neuen Plan erstellen, bevor Video-Transcode startet.",
            None,
        )

    if (
        plan.project_id != project.id
        or plan.import_id != plan_ctx["import_id"]
        or plan.selection_id != plan_ctx["selection_id"]
        or plan.scan_id != plan_ctx["scan_id"]
        or plan.validation_run_id != plan_ctx["validation_run_id"]
    ):
        return (
            False,
            "Intake-Plan-IDs stimmen nicht mit der aktuellen Basis überein.",
            None,
        )

    items = _video_transcode_items(plan)
    if not items:
        return (
            False,
            "Der aktuelle Plan enthält keine geplanten Video-Transcode-Assets.",
            {
                **plan_ctx,
                "plan_id": plan.plan_id,
                "video_transcode_item_count": 0,
            },
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return False, str(exc), None

    try:
        active = find_active_intake_run(conn, project_id=project.id)
        if active is not None:
            return (
                False,
                (
                    f"Es läuft bereits ein Intake-/Transform-Run "
                    f"({active.scope}/{active.status.value})."
                ),
                {
                    **plan_ctx,
                    "plan_id": plan.plan_id,
                    "video_transcode_item_count": len(items),
                    "active_run_id": active.run_id,
                },
            )
    finally:
        conn.close()

    return (
        True,
        None,
        {
            **plan_ctx,
            "plan_id": plan.plan_id,
            "video_transcode_item_count": len(items),
        },
    )


def start_video_transcode_intake(
    project: Project,
    *,
    sync: bool = False,
) -> IntakeRunStartResult:
    project = require_discovery_project(project)
    reconcile_orphaned_intake_run(project)
    ok, message, ctx = can_start_video_transcode_intake(project)
    if not ok or ctx is None:
        return IntakeRunStartResult(
            started=False,
            message=message or "Video-Transcode kann nicht gestartet werden.",
        )

    plan, is_stale, _ = get_current_intake_plan(project)
    if plan is None or is_stale:
        return IntakeRunStartResult(
            started=False,
            message="Intake-Plan ist nicht mehr gültig (stale_plan).",
        )

    items = _video_transcode_items(plan)
    if not items:
        return IntakeRunStartResult(
            started=False,
            message="Keine Video-Transcode-Assets im Plan.",
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise VideoTranscodeServiceError(str(exc)) from exc

    try:
        active = find_active_intake_run(conn, project_id=project.id)
        if active is not None:
            return IntakeRunStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Intake-/Transform-Run "
                    f"({active.scope}/{active.status.value})."
                ),
                run=active,
            )

        run = IntakeRunRecord(
            run_id=new_intake_run_id(),
            project_id=project.id,
            plan_id=plan.plan_id,
            import_id=plan.import_id,
            selection_id=plan.selection_id,
            scan_id=plan.scan_id,
            validation_run_id=plan.validation_run_id,
            status=IntakeRunStatus.QUEUED,
            created_at=_now(),
            total_assets=len(items),
            worker_version=VIDEO_TRANSCODE_WORKER_VERSION,
            scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        )
        insert_intake_run(conn, run)
        for item in items:
            insert_intake_run_asset(
                conn,
                IntakeRunAssetRecord(
                    run_asset_id=new_run_asset_id(),
                    run_id=run.run_id,
                    plan_id=plan.plan_id,
                    asset_id=item.asset_id,
                    source_relative_path=item.source_relative_path,
                    source_group=item.source_group,
                    media_kind=item.media_kind,
                    planned_action=IntakeAction.TRANSCODE,
                    status=IntakeRunAssetStatus.PENDING,
                    source_sha256=item.source_sha256,
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        raise VideoTranscodeServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_intake_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="video_transcode",
        sync=sync,
    )
    if not launched and not sync:
        return IntakeRunStartResult(
            started=False,
            message="Video-Transcode-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
        )

    if sync:
        try:
            conn = open_registry(project.project_root_path)
            final = get_intake_run(conn, run_id=run.run_id) or run
            conn.close()
        except RegistryDatabaseError:
            final = run
        return IntakeRunStartResult(
            started=True,
            message="Video-Transcode abgeschlossen.",
            run=final,
        )

    return IntakeRunStartResult(
        started=True,
        message="Video-Transcode gestartet.",
        run=run,
    )


def get_video_transcode_status(
    project: Project,
) -> tuple[
    IntakeRunRecord | None,
    list[IntakeRunAssetRecord],
    list[WorkingMediaRecord],
    str | None,
]:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return None, [], [], str(exc)

    reconcile_orphaned_intake_run(project)

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return None, [], [], str(exc)

    try:
        run = get_latest_intake_run(
            conn,
            project_id=project.id,
            scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        )
        assets = list_intake_run_assets(conn, run_id=run.run_id) if run else []
        working = [
            wm
            for wm in list_working_media(conn, project_id=project.id)
            if wm.action == "transcode"
            and wm.processing_profile_version == "video-h264-v1"
        ]
        return run, assets, working, None
    finally:
        conn.close()


def list_open_video_transcode_plan_items(project: Project) -> list[IntakePlanItem]:
    plan, stale, _ = get_current_intake_plan(project)
    if plan is None or stale:
        return []
    return _video_transcode_items(plan)


def _validation_matches_plan(
    validation_run,
    plan: IntakePlan,
    *,
    project_id: str,
) -> bool:
    if validation_run is None:
        return False
    return (
        validation_run.run_id == plan.validation_run_id
        and validation_run.project_id == project_id
        and validation_run.project_id == plan.project_id
        and validation_run.import_id == plan.import_id
        and validation_run.selection_id == plan.selection_id
        and validation_run.scan_id == plan.scan_id
    )


def list_video_transcode_plan_item_views(
    project: Project,
) -> list[VideoTranscodePlanItemView]:
    """UI-View aus persistierter Plan-Validation — kein Live-Probe."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return []

    plan, stale, _ = get_current_intake_plan(project)
    if plan is None or stale:
        return []
    items = _video_transcode_items(plan)
    if not items:
        return []

    validation_by_asset: dict[str, AssetValidationRecord] = {}
    try:
        conn = open_registry(project.project_root_path)
        try:
            validation_run = get_validation_run(
                conn, run_id=plan.validation_run_id
            )
            if _validation_matches_plan(
                validation_run, plan, project_id=project.id
            ):
                for record in list_asset_validations(
                    conn, run_id=plan.validation_run_id
                ):
                    # Nur exakte Validation-IDs aus dem Plan-Item akzeptieren.
                    validation_by_asset[record.asset_id] = record
        finally:
            conn.close()
    except RegistryDatabaseError:
        validation_by_asset = {}

    views: list[VideoTranscodePlanItemView] = []
    for item in items:
        validation = validation_by_asset.get(item.asset_id)
        if validation is not None and validation.validation_id != item.validation_id:
            # Plan-Item verweist auf andere Validation → Felder unbekannt.
            validation = None
        views.append(
            VideoTranscodePlanItemView(
                item=item,
                audio_stream_count=(
                    validation.audio_stream_count if validation else None
                ),
                audio_channel_count=(
                    validation.audio_channel_count if validation else None
                ),
                rotation_degrees=(
                    validation.rotation_degrees if validation else None
                ),
            )
        )
    return views


def format_audio_display(
    audio_stream_count: int | None,
    audio_channel_count: int | None,
) -> str:
    """UI-Formatierung für Audio-Vorschau aus persistierten Werten."""
    if audio_stream_count is None and audio_channel_count is None:
        return "—"
    streams = "—" if audio_stream_count is None else str(audio_stream_count)
    channels = "—" if audio_channel_count is None else str(audio_channel_count)
    stream_label = "Stream" if audio_stream_count == 1 else "Streams"
    return f"{streams} {stream_label}, {channels} Kanäle"


def format_rotation_display(rotation_degrees: float | None) -> str:
    """UI-Formatierung: bekannte Rotation; unbekannt → —."""
    if rotation_degrees is None:
        return "—"
    if abs(rotation_degrees) < 0.01:
        return "0°"
    if float(rotation_degrees).is_integer():
        return f"{int(rotation_degrees)}°"
    return f"{rotation_degrees}°"
