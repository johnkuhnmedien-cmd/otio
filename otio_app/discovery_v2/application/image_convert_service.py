"""Application-Service: Discovery-V2 TIFF→PNG Image-Convert (Phase 7C3A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

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
    IMAGE_CONVERT_WORKER_VERSION,
    IMAGE_PNG_PROFILE_VERSION,
    INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
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
    read_schema_version,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
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

_TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})


@dataclass(frozen=True)
class ImageConvertPlanItemView:
    """UI-Viewmodell: Plan-Item + persistierte Image-Validation."""

    item: IntakePlanItem
    image_format: str | None = None
    image_mode: str | None = None
    image_bit_depth: int | None = None
    image_frame_count: int | None = None
    has_alpha: bool | None = None
    has_icc_profile: bool | None = None
    exif_orientation: int | None = None
    image_is_bigtiff: bool | None = None
    width: int | None = None
    height: int | None = None


class ImageConvertServiceError(InventoryServiceError):
    """Fachlicher Fehler des Image-Convert-Intakes."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_extension(extension: str | None, relative_path: str) -> str:
    raw = (extension or "").strip().lower()
    if raw and not raw.startswith("."):
        raw = f".{raw}"
    if raw:
        return raw
    return PurePosixPath(relative_path).suffix.lower()


def _is_tiff_image_convert_item(item: IntakePlanItem) -> bool:
    if item.planned_action != IntakeAction.TRANSCODE:
        return False
    if item.status != IntakePlanItemStatus.PLANNED:
        return False
    if (item.media_kind or "").strip().lower() != MediaKind.IMAGE.value:
        return False
    ext = _normalize_extension(item.extension, item.source_relative_path)
    if ext not in _TIFF_EXTENSIONS:
        return False
    return True


def _tiff_items_with_profile(plan: IntakePlan) -> list[IntakePlanItem]:
    return [
        item
        for item in plan.items
        if _is_tiff_image_convert_item(item)
        and (item.proposed_target_extension or "").strip().lower() == ".png"
        and (item.processing_profile_version or "").strip() == IMAGE_PNG_PROFILE_VERSION
    ]


def _tiff_items_missing_profile(plan: IntakePlan) -> list[IntakePlanItem]:
    missing: list[IntakePlanItem] = []
    for item in plan.items:
        if not _is_tiff_image_convert_item(item):
            continue
        target = (item.proposed_target_extension or "").strip().lower()
        profile = (item.processing_profile_version or "").strip()
        if target != ".png" or profile != IMAGE_PNG_PROFILE_VERSION:
            missing.append(item)
    return missing


def can_start_image_convert_intake(
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
            or "Keine gültige Selection/Import/Validation-Basis für TIFF-Konvertierung.",
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
            "Bitte einen neuen Plan erstellen, bevor TIFF-Konvertierung startet.",
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

    missing_profile = _tiff_items_missing_profile(plan)
    if missing_profile:
        return (
            False,
            (
                "image_conversion_profile_missing: Der aktuelle Plan enthält "
                "TIFF-Positionen ohne Profil image-png-v1 / Ziel .png. "
                "Bitte einen neuen Intake-Plan erzeugen."
            ),
            {
                **plan_ctx,
                "plan_id": plan.plan_id,
                "image_convert_item_count": 0,
                "image_conversion_profile_missing": len(missing_profile),
            },
        )

    items = _tiff_items_with_profile(plan)
    if not items:
        return (
            False,
            "Der aktuelle Plan enthält keine geplanten TIFF→PNG-Konvertierungs-Assets.",
            {
                **plan_ctx,
                "plan_id": plan.plan_id,
                "image_convert_item_count": 0,
            },
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return False, str(exc), None

    try:
        schema = read_schema_version(conn)
        if schema != REGISTRY_SCHEMA_VERSION:
            return (
                False,
                (
                    f"Registry-Schema inkompatibel: {schema} "
                    f"(erwartet {REGISTRY_SCHEMA_VERSION})."
                ),
                None,
            )
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
                    "image_convert_item_count": len(items),
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
            "image_convert_item_count": len(items),
        },
    )


def start_image_convert_intake(
    project: Project,
    *,
    sync: bool = False,
) -> IntakeRunStartResult:
    project = require_discovery_project(project)
    reconcile_orphaned_intake_run(project)
    ok, message, ctx = can_start_image_convert_intake(project)
    if not ok or ctx is None:
        return IntakeRunStartResult(
            started=False,
            message=message or "TIFF-Konvertierung kann nicht gestartet werden.",
        )

    plan, is_stale, _ = get_current_intake_plan(project)
    if plan is None or is_stale:
        return IntakeRunStartResult(
            started=False,
            message="Intake-Plan ist nicht mehr gültig (stale_plan).",
        )

    items = _tiff_items_with_profile(plan)
    if not items:
        return IntakeRunStartResult(
            started=False,
            message="Keine TIFF→PNG-Assets im Plan.",
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise ImageConvertServiceError(str(exc)) from exc

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
            worker_version=IMAGE_CONVERT_WORKER_VERSION,
            scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
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
        raise ImageConvertServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_intake_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="image_convert",
        sync=sync,
    )
    if not launched and not sync:
        return IntakeRunStartResult(
            started=False,
            message=(
                "Image-Convert-Worker konnte nicht gestartet werden (bereits aktiv)."
            ),
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
            message="TIFF-Konvertierung abgeschlossen.",
            run=final,
        )

    return IntakeRunStartResult(
        started=True,
        message="TIFF-Konvertierung gestartet.",
        run=run,
    )


def get_image_convert_status(
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
            scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
        )
        assets = list_intake_run_assets(conn, run_id=run.run_id) if run else []
        working = [
            wm
            for wm in list_working_media(conn, project_id=project.id)
            if wm.action == "transcode"
            and wm.processing_profile_version == IMAGE_PNG_PROFILE_VERSION
        ]
        return run, assets, working, None
    finally:
        conn.close()


def list_open_image_convert_plan_items(project: Project) -> list[IntakePlanItem]:
    plan, stale, _ = get_current_intake_plan(project)
    if plan is None or stale:
        return []
    return _tiff_items_with_profile(plan)


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


def list_image_convert_plan_item_views(
    project: Project,
) -> list[ImageConvertPlanItemView]:
    """UI-View aus persistierter Plan-Validation — kein Live-Open."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return []

    plan, stale, _ = get_current_intake_plan(project)
    if plan is None or stale:
        return []
    # Anzeige: alle TIFF-Transcode-Items inkl. fehlendem Profil.
    items = [i for i in plan.items if _is_tiff_image_convert_item(i)]
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
                    validation_by_asset[record.asset_id] = record
        finally:
            conn.close()
    except RegistryDatabaseError:
        validation_by_asset = {}

    views: list[ImageConvertPlanItemView] = []
    for item in items:
        validation = validation_by_asset.get(item.asset_id)
        if validation is not None and validation.validation_id != item.validation_id:
            validation = None
        views.append(
            ImageConvertPlanItemView(
                item=item,
                image_format=validation.image_format if validation else None,
                image_mode=validation.image_mode if validation else None,
                image_bit_depth=validation.image_bit_depth if validation else None,
                image_frame_count=(
                    validation.image_frame_count if validation else None
                ),
                has_alpha=validation.has_alpha if validation else None,
                has_icc_profile=(
                    validation.has_icc_profile if validation else None
                ),
                exif_orientation=(
                    validation.exif_orientation if validation else None
                ),
                image_is_bigtiff=(
                    validation.image_is_bigtiff if validation else None
                ),
                width=(
                    validation.width
                    if validation and validation.width is not None
                    else item.width
                ),
                height=(
                    validation.height
                    if validation and validation.height is not None
                    else item.height
                ),
            )
        )
    return views
