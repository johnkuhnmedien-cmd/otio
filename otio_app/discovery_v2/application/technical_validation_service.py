"""Application-Service: technische Prüfung der Registry-Quellen starten/lesen."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from otio_app.discovery_v2.adapters.validation_job_launcher import (
    get_validation_job_launcher,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    require_discovery_project,
)
from otio_app.discovery_v2.application.selection_service import (
    get_latest_confirmed_selection,
)
from otio_app.discovery_v2.domain.selection import SelectionStatus
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    ValidationRunRecord,
    ValidationRunReport,
    ValidationRunStatus,
    ValidationStartResult,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.asset_registry_repository import (
    load_latest_import_report,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    find_active_run,
    find_import,
    find_latest_import,
    get_latest_run,
    get_run,
    insert_run,
    list_asset_validations,
    list_duplicate_groups,
    load_latest_validation_report,
    open_registry,
    validation_report_path,
)
from otio_app.models import Project


class TechnicalValidationServiceError(InventoryServiceError):
    """Fachlicher Fehler der technischen Prüfung."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def can_start_technical_validation(
    project: Project,
) -> tuple[bool, str | None, dict | None]:
    """Prüft Startvoraussetzungen ohne Seiteneffekte.

    Returns (ok, error_message, context).
    """
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
            "bevor eine technische Prüfung starten kann.",
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
            "Der Registry-Import gehört nicht zur aktuell bestätigten Auswahl. "
            "Bitte die aktuelle Auswahl erneut in die Registry übernehmen.",
            None,
        )
    if report.scan_id != snapshot.scan_id or report.scan_id != selection.scan_id:
        return (
            False,
            "Registry-Import und aktuelle Bestandsaufnahme stimmen nicht überein. "
            "Bitte Scan, Auswahl und Import erneut durchführen.",
            None,
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return False, str(exc), None

    try:
        active = find_active_run(conn, project_id=project.id)
        if active is not None:
            return (
                False,
                f"Es läuft bereits eine technische Prüfung ({active.status.value}).",
                {
                    "import_id": report.import_id,
                    "selection_id": report.selection_id,
                    "scan_id": report.scan_id,
                    "asset_count": report.asset_count,
                    "active_run_id": active.run_id,
                },
            )

        import_row = find_import(conn, import_id=report.import_id)
        if import_row is None:
            return False, "Registry-Import in der SQLite-DB nicht gefunden.", None
    finally:
        conn.close()

    return (
        True,
        None,
        {
            "import_id": report.import_id,
            "selection_id": report.selection_id,
            "scan_id": report.scan_id,
            "asset_count": report.asset_count,
        },
    )


def start_technical_validation(
    project: Project,
    *,
    sync: bool = False,
) -> ValidationStartResult:
    """Legt einen Prüfauftrag an und startet den Worker (oder sync für Tests)."""
    project = require_discovery_project(project)
    ok, message, ctx = can_start_technical_validation(project)
    if not ok or ctx is None:
        return ValidationStartResult(
            started=False,
            message=message or "Technische Prüfung kann nicht gestartet werden.",
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise TechnicalValidationServiceError(str(exc)) from exc

    try:
        # Erneute Sperre gegen Race
        active = find_active_run(conn, project_id=project.id)
        if active is not None:
            return ValidationStartResult(
                started=False,
                message=(
                    f"Es läuft bereits eine technische Prüfung ({active.status.value})."
                ),
                run=active,
            )

        latest = find_latest_import(conn, project_id=project.id)
        if latest is None:
            raise TechnicalValidationServiceError("Kein Registry-Import vorhanden.")
        import_id, selection_id, scan_id, asset_count = latest
        if import_id != ctx["import_id"] or selection_id != ctx["selection_id"]:
            raise TechnicalValidationServiceError(
                "Registry-Import stimmt nicht mit der aktuellen Auswahl überein."
            )

        run = ValidationRunRecord(
            run_id=str(uuid4()),
            project_id=project.id,
            import_id=import_id,
            selection_id=selection_id,
            scan_id=scan_id,
            status=ValidationRunStatus.QUEUED,
            created_at=_now(),
            total_assets=int(asset_count),
        )
        insert_run(conn, run)
        conn.commit()
    except TechnicalValidationServiceError:
        conn.close()
        raise
    except Exception as exc:  # noqa: BLE001
        conn.close()
        raise TechnicalValidationServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_validation_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        sync=sync,
    )
    if not launched and not sync:
        return ValidationStartResult(
            started=False,
            message="Prüf-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
        )

    # Nach sync den finalen Status laden
    if sync:
        try:
            conn = open_registry(project.project_root_path)
            final = get_run(conn, run_id=run.run_id) or run
            conn.close()
        except RegistryDatabaseError:
            final = run
        return ValidationStartResult(
            started=True,
            message="Technische Prüfung abgeschlossen.",
            run=final,
        )

    return ValidationStartResult(
        started=True,
        message="Technische Prüfung gestartet.",
        run=run,
    )


def get_validation_status(
    project: Project,
) -> tuple[ValidationRunRecord | None, list[AssetValidationRecord], str | None]:
    """Liest den letzten/aktiven Run und dessen Asset-Ergebnisse (kein Start)."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return None, [], str(exc)

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return None, [], str(exc)

    try:
        run = find_active_run(conn, project_id=project.id)
        if run is None:
            run = get_latest_run(conn, project_id=project.id)
        validations: list[AssetValidationRecord] = []
        if run is not None:
            validations = list_asset_validations(conn, run_id=run.run_id)
        return run, validations, None
    finally:
        conn.close()


def get_validation_summary(
    validations: list[AssetValidationRecord],
) -> dict[str, int]:
    summary = {
        "successful": 0,
        "failed": 0,
        "source_missing": 0,
        "source_changed": 0,
        "potential_duplicates": 0,
        "probe_failed": 0,
        "unsupported": 0,
        "validation_error": 0,
    }
    seen_groups: set[str] = set()
    for item in validations:
        if item.status == AssetValidationStatus.PROBE_SUCCEEDED:
            summary["successful"] += 1
        else:
            summary["failed"] += 1
        if item.status == AssetValidationStatus.SOURCE_MISSING:
            summary["source_missing"] += 1
        elif item.status == AssetValidationStatus.SOURCE_CHANGED:
            summary["source_changed"] += 1
        elif item.status == AssetValidationStatus.PROBE_FAILED:
            summary["probe_failed"] += 1
        elif item.status == AssetValidationStatus.UNSUPPORTED_MEDIA_KIND:
            summary["unsupported"] += 1
        elif item.status == AssetValidationStatus.VALIDATION_ERROR:
            summary["validation_error"] += 1
        if item.duplicate_group_id and item.duplicate_group_id not in seen_groups:
            seen_groups.add(item.duplicate_group_id)
            summary["potential_duplicates"] += 1
    return summary


def get_latest_report(
    project: Project,
) -> tuple[ValidationRunReport | None, str | None]:
    try:
        require_discovery_project(project)
    except InventoryServiceError as exc:
        return None, str(exc)
    return load_latest_validation_report(project.project_root_path)


def report_path_for_run(project: Project, run_id: str) -> str:
    path = validation_report_path(project.project_root_path, run_id)
    return str(path)


def duplicate_group_count(project: Project, run_id: str) -> int:
    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError:
        return 0
    try:
        return len(list_duplicate_groups(conn, run_id=run_id))
    finally:
        conn.close()
