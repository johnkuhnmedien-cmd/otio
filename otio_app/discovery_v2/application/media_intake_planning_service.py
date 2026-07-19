"""Application-Service: deterministische Media-Intake-Planung (Phase 7A)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from otio_app.discovery_v2.adapters.intake_decision import (
    IntakeDecisionSource,
    build_plan_item,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    require_discovery_project,
)
from otio_app.discovery_v2.application.selection_service import (
    get_latest_confirmed_selection,
)
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_PLANNER_VERSION,
    IntakeAction,
    IntakePlan,
    IntakePlanCreateResult,
    IntakePlanStatus,
)
from otio_app.discovery_v2.domain.selection import SelectionStatus
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationStatus,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.asset_registry_repository import (
    load_latest_import_report,
)
from otio_app.discovery_v2.persistence.intake_plan_artifact_store import (
    load_latest_intake_plan,
    write_latest_plan_pointer,
    write_plan_json_only,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.media_intake_repository import (
    count_intake_plans,
    get_latest_intake_plan_record,
    insert_intake_plan,
    open_registry,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    find_import,
    get_latest_run,
    get_run,
    list_asset_validations,
    list_assets_for_import,
)
from otio_app.models import Project


class MediaIntakePlanningServiceError(InventoryServiceError):
    """Fachlicher Fehler der Media-Intake-Planung."""


_PLANNING_TERMINAL_STATUSES = frozenset(
    {
        ValidationRunStatus.COMPLETED,
        ValidationRunStatus.COMPLETED_WITH_ERRORS,
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def can_create_intake_plan(
    project: Project,
) -> tuple[bool, str | None, dict | None]:
    """Prüft Planungsvoraussetzungen (Application-Schicht, nicht nur UI)."""
    try:
        require_discovery_project(project)
    except InventoryServiceError as exc:
        return False, str(exc), None

    snapshot, snap_warn = get_latest_inventory(project)
    if snapshot is None:
        return False, snap_warn or "Kein Inventory-Snapshot vorhanden.", None

    selection, status, sel_warn = get_latest_confirmed_selection(
        project, current_scan_id=snapshot.scan_id
    )
    if selection is None:
        return False, sel_warn or "Bestätige zuerst deine Medienauswahl.", None
    if status == SelectionStatus.STALE or selection.scan_id != snapshot.scan_id:
        return (
            False,
            "Die bestätigte Auswahl ist veraltet. "
            "Bitte Bestandsaufnahme und Auswahl erneut bestätigen, "
            "bevor ein Media-Intake-Plan erstellt werden kann.",
            None,
        )

    report, import_warn = load_latest_import_report(project.project_root_path)
    if report is None:
        return (
            False,
            import_warn
            or "Kein Registry-Import vorhanden. "
            "Übernehme zuerst die bestätigte Auswahl in die Asset Registry.",
            None,
        )

    if report.selection_id != selection.selection_id:
        return (
            False,
            "Der Registry-Import gehört nicht zur aktuell bestätigten Auswahl.",
            None,
        )
    if report.scan_id != snapshot.scan_id or report.scan_id != selection.scan_id:
        return (
            False,
            "Registry-Import und aktuelle Bestandsaufnahme stimmen nicht überein.",
            None,
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return False, str(exc), None

    try:
        import_row = find_import(conn, import_id=report.import_id)
        if import_row is None:
            return False, "Registry-Import in der SQLite-DB nicht gefunden.", None

        run = get_latest_run(conn, project_id=project.id)
        if run is None:
            return (
                False,
                "Kein Validation-Run vorhanden. "
                "Führe zuerst die technische Prüfung durch.",
                None,
            )

        if run.status not in _PLANNING_TERMINAL_STATUSES:
            return (
                False,
                (
                    f"Validation-Run ist nicht abgeschlossen "
                    f"(Status: {run.status.value}). "
                    "Ein Intake-Plan erfordert completed oder "
                    "completed_with_errors."
                ),
                {
                    "import_id": report.import_id,
                    "selection_id": report.selection_id,
                    "scan_id": report.scan_id,
                    "validation_run_id": run.run_id,
                    "validation_status": run.status.value,
                },
            )

        if (
            run.project_id != project.id
            or run.import_id != report.import_id
            or run.selection_id != selection.selection_id
            or run.scan_id != snapshot.scan_id
        ):
            return (
                False,
                "Der aktuelle Validation-Run gehört nicht zur aktuellen "
                "Selection/Import/Scan-Kombination.",
                {
                    "import_id": report.import_id,
                    "selection_id": report.selection_id,
                    "scan_id": report.scan_id,
                    "validation_run_id": run.run_id,
                },
            )

        validations = list_asset_validations(conn, run_id=run.run_id)
        successful = sum(
            1
            for v in validations
            if v.status == AssetValidationStatus.PROBE_SUCCEEDED
        )
        blocked = len(validations) - successful
    finally:
        conn.close()

    return (
        True,
        None,
        {
            "import_id": report.import_id,
            "selection_id": report.selection_id,
            "scan_id": report.scan_id,
            "validation_run_id": run.run_id,
            "validation_status": run.status.value,
            "asset_count": len(validations),
            "successful_assets": successful,
            "blocked_assets": blocked,
        },
    )


def create_intake_plan(project: Project) -> IntakePlanCreateResult:
    """Erzeugt einen neuen unveränderlichen Intake-Plan (expliziter Aufruf)."""
    project = require_discovery_project(project)
    ok, message, ctx = can_create_intake_plan(project)
    if not ok or ctx is None:
        return IntakePlanCreateResult(
            created=False,
            message=message or "Media-Intake-Plan kann nicht erstellt werden.",
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise MediaIntakePlanningServiceError(str(exc)) from exc

    try:
        run = get_run(conn, run_id=ctx["validation_run_id"])
        if run is None or run.status not in _PLANNING_TERMINAL_STATUSES:
            raise MediaIntakePlanningServiceError(
                "Validation-Run ist für die Planung nicht verfügbar."
            )
        if (
            run.import_id != ctx["import_id"]
            or run.selection_id != ctx["selection_id"]
            or run.scan_id != ctx["scan_id"]
        ):
            raise MediaIntakePlanningServiceError(
                "Validation-Run stimmt nicht mehr mit dem Planungskontext überein."
            )

        assets = {
            a.asset_id: a
            for a in list_assets_for_import(conn, import_id=run.import_id)
        }
        validations = list_asset_validations(conn, run_id=run.run_id)
        items = []
        for validation in validations:
            asset = assets.get(validation.asset_id)
            extension = asset.extension if asset is not None else ""
            source_group = (
                (asset.source_group if asset is not None else None)
                or validation.source_group
                or "__root__"
            )
            items.append(
                build_plan_item(
                    IntakeDecisionSource(
                        validation=validation,
                        extension=extension,
                        source_group=source_group,
                    )
                )
            )

        copy_count = sum(1 for i in items if i.planned_action == IntakeAction.COPY)
        remux_count = sum(1 for i in items if i.planned_action == IntakeAction.REMUX)
        transcode_count = sum(
            1 for i in items if i.planned_action == IntakeAction.TRANSCODE
        )
        blocked_count = sum(
            1 for i in items if i.planned_action == IntakeAction.BLOCKED
        )
        duplicate_warning_count = sum(
            1 for i in items if i.duplicate_group_id
        )

        if blocked_count == len(items) and items:
            plan_status = IntakePlanStatus.BLOCKED
        elif blocked_count > 0:
            plan_status = IntakePlanStatus.READY_WITH_BLOCKED_ASSETS
        else:
            plan_status = IntakePlanStatus.READY

        plan = IntakePlan(
            planner_version=INTAKE_PLANNER_VERSION,
            plan_id=str(uuid4()),
            project_id=project.id,
            import_id=run.import_id,
            selection_id=run.selection_id,
            scan_id=run.scan_id,
            validation_run_id=run.run_id,
            created_at=_now(),
            status=plan_status,
            total_assets=len(items),
            copy_count=copy_count,
            remux_count=remux_count,
            transcode_count=transcode_count,
            blocked_count=blocked_count,
            duplicate_warning_count=duplicate_warning_count,
            items=items,
        )

        # 1) DB zuerst in einer Transaktion
        insert_intake_plan(conn, plan)
        conn.commit()
    except MediaIntakePlanningServiceError:
        conn.rollback()
        conn.close()
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        raise MediaIntakePlanningServiceError(str(exc)) from exc
    else:
        conn.close()

    # 2) JSON-Plan, 3) Pointer erst danach — bei JSON-Fehler alter Pointer bleibt.
    try:
        write_plan_json_only(project.project_root_path, plan)
        write_latest_plan_pointer(project.project_root_path, plan)
    except InventoryArtifactError as exc:
        raise MediaIntakePlanningServiceError(str(exc)) from exc

    return IntakePlanCreateResult(
        created=True,
        message="Media-Intake-Plan erstellt. Es wurden noch keine Medien kopiert oder verändert.",
        plan=plan,
    )


def _ids_match_current(plan: IntakePlan, ctx: dict) -> bool:
    return (
        plan.import_id == ctx.get("import_id")
        and plan.selection_id == ctx.get("selection_id")
        and plan.scan_id == ctx.get("scan_id")
        and plan.validation_run_id == ctx.get("validation_run_id")
    )


def get_current_intake_plan(
    project: Project,
) -> tuple[IntakePlan | None, bool, str | None]:
    """Lädt den letzten Plan und markiert ihn ggf. als stale (ohne Neuplanung).

    Returns (plan, is_stale, warning).
    """
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return None, False, str(exc)

    plan, warn = load_latest_intake_plan(project.project_root_path)
    if plan is None:
        # Fallback SQLite
        try:
            conn = open_registry(project.project_root_path)
        except RegistryDatabaseError as exc:
            return None, False, warn or str(exc)
        try:
            plan = get_latest_intake_plan_record(conn, project_id=project.id)
        finally:
            conn.close()
        if plan is None:
            return None, False, warn

    ok, _, ctx = can_create_intake_plan(project)
    if not ok or ctx is None or not _ids_match_current(plan, ctx):
        # Historischen Plan als stale ausweisen (Objekt unverändert in DB).
        stale_plan = plan.model_copy(update={"status": IntakePlanStatus.STALE})
        return stale_plan, True, warn

    return plan, False, warn


def planning_context_summary(project: Project) -> dict | None:
    ok, _, ctx = can_create_intake_plan(project)
    return ctx if ok else None


def stored_plan_count(project: Project) -> int:
    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError:
        return 0
    try:
        return count_intake_plans(conn, project_id=project.id)
    finally:
        conn.close()
