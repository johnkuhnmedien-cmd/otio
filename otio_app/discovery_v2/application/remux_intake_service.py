"""Application-Service: Discovery-V2 Remux-Intake (Phase 7C1)."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.intake_job_launcher import get_intake_job_launcher
from otio_app.discovery_v2.adapters.media_remux import evaluate_remux_gate
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.media_intake_planning_service import (
    can_create_intake_plan,
    get_current_intake_plan,
)
from otio_app.discovery_v2.application.remux_intake_job_recovery import (
    reconcile_orphaned_remux_intake_run,
)
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    REMUX_INTAKE_WORKER_VERSION,
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
from otio_app.models import Project


class RemuxIntakeServiceError(InventoryServiceError):
    """Fachlicher Fehler des Remux-Intakes."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _remux_items(plan: IntakePlan) -> list[IntakePlanItem]:
    return [
        item
        for item in plan.items
        if item.planned_action == IntakeAction.REMUX
        and item.status == IntakePlanItemStatus.PLANNED
    ]


def _item_passes_remux_gate(item: IntakePlanItem) -> tuple[bool, str | None]:
    gate = evaluate_remux_gate(
        planned_action=item.planned_action,
        media_kind=item.media_kind,
        video_codec=item.video_codec,
        pixel_format=item.pixel_format,
        bit_depth=item.bit_depth,
        extension=item.extension,
        container_format=item.container_format,
        source_relative_path=item.source_relative_path,
        validation_status="probe_succeeded",
    )
    if not gate.ok:
        return False, gate.error_message
    audio = (item.audio_codec or "").strip().lower()
    if audio and audio not in {"aac", "mp3"}:
        return (
            False,
            f"Audiocodec nicht für Remux-Stream-Copy freigegeben: {audio}",
        )
    return True, None


def can_start_remux_intake(
    project: Project,
) -> tuple[bool, str | None, dict | None]:
    """Prüft Startvoraussetzungen im Application-Service."""
    try:
        require_discovery_project(project)
    except InventoryServiceError as exc:
        return False, str(exc), None

    reconcile_orphaned_remux_intake_run(project)

    plan_ok, plan_msg, plan_ctx = can_create_intake_plan(project)
    if not plan_ok or plan_ctx is None:
        return (
            False,
            plan_msg
            or "Keine gültige Selection/Import/Validation-Basis für Remux-Intake.",
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
            "Bitte einen neuen Plan erstellen, bevor Remux-Intake startet.",
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

    remux_items = _remux_items(plan)
    eligible = [i for i in remux_items if _item_passes_remux_gate(i)[0]]
    if not remux_items:
        return (
            False,
            "Der aktuelle Plan enthält keine geplanten Remux-Assets.",
            {
                **plan_ctx,
                "plan_id": plan.plan_id,
                "remux_item_count": 0,
                "eligible_remux_item_count": 0,
            },
        )
    if not eligible:
        return (
            False,
            "Keine Remux-Assets bestehen die Remux-Sicherheitsprüfung.",
            {
                **plan_ctx,
                "plan_id": plan.plan_id,
                "remux_item_count": len(remux_items),
                "eligible_remux_item_count": 0,
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
                    "remux_item_count": len(remux_items),
                    "eligible_remux_item_count": len(eligible),
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
            "remux_item_count": len(remux_items),
            "eligible_remux_item_count": len(eligible),
        },
    )


def start_remux_intake(
    project: Project,
    *,
    sync: bool = False,
) -> IntakeRunStartResult:
    """Legt einen Remux-Intake-Run an und startet den Worker."""
    project = require_discovery_project(project)
    reconcile_orphaned_remux_intake_run(project)
    ok, message, ctx = can_start_remux_intake(project)
    if not ok or ctx is None:
        return IntakeRunStartResult(
            started=False,
            message=message or "Remux-Intake kann nicht gestartet werden.",
        )

    plan, is_stale, _ = get_current_intake_plan(project)
    if plan is None or is_stale:
        return IntakeRunStartResult(
            started=False,
            message="Intake-Plan ist nicht mehr gültig (stale_plan).",
        )

    remux_items = [
        item for item in _remux_items(plan) if _item_passes_remux_gate(item)[0]
    ]
    if not remux_items:
        return IntakeRunStartResult(
            started=False,
            message="Keine zulässigen Remux-Assets im Plan.",
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise RemuxIntakeServiceError(str(exc)) from exc

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
            total_assets=len(remux_items),
            worker_version=REMUX_INTAKE_WORKER_VERSION,
            scope=INTAKE_RUN_SCOPE_REMUX_ONLY,
        )
        insert_intake_run(conn, run)
        for item in remux_items:
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
                    planned_action=IntakeAction.REMUX,
                    status=IntakeRunAssetStatus.PENDING,
                    source_sha256=item.source_sha256,
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        raise RemuxIntakeServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_intake_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="remux",
        sync=sync,
    )
    if not launched and not sync:
        return IntakeRunStartResult(
            started=False,
            message="Remux-Intake-Worker konnte nicht gestartet werden (bereits aktiv).",
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
            message="Remux-Intake abgeschlossen.",
            run=final,
        )

    return IntakeRunStartResult(
        started=True,
        message="Remux-Intake gestartet.",
        run=run,
    )


def get_remux_intake_status(
    project: Project,
) -> tuple[
    IntakeRunRecord | None,
    list[IntakeRunAssetRecord],
    list[WorkingMediaRecord],
    str | None,
]:
    """Liest letzten Remux-Run inkl. Assets und Working Media."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return None, [], [], str(exc)

    reconcile_orphaned_remux_intake_run(project)

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return None, [], [], str(exc)

    try:
        run = get_latest_intake_run(
            conn,
            project_id=project.id,
            scope=INTAKE_RUN_SCOPE_REMUX_ONLY,
        )
        assets = list_intake_run_assets(conn, run_id=run.run_id) if run else []
        working = [
            wm
            for wm in list_working_media(conn, project_id=project.id)
            if wm.action == "remux"
        ]
        return run, assets, working, None
    finally:
        conn.close()


def list_open_remux_plan_items(project: Project) -> list[IntakePlanItem]:
    plan, stale, _ = get_current_intake_plan(project)
    if plan is None or stale:
        return []
    return _remux_items(plan)
