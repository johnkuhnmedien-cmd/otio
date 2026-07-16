"""Wiederanlaufvertrag für Discovery-Validierungsjobs (außerhalb der UI).

Verwaiste ``queued``/``running``-Status nach Prozessabbruch werden kontrolliert
auf ``failed`` mit Fehlercode ``worker_interrupted`` gesetzt.
Keine automatische Wiederaufnahme mitten in einem Asset.
"""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.validation_job_launcher import (
    get_validation_job_launcher,
)
from otio_app.discovery_v2.domain.technical_validation import (
    WORKER_INTERRUPTED_ERROR_CODE,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    build_report_from_run,
    find_active_run,
    open_registry,
    save_validation_report,
    update_run,
)
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_validation_run(
    project: Project,
) -> ValidationRunRecord | None:
    """Erkennt verwaiste aktive Runs und markiert sie als ``failed``.

    Returns den aktualisierten Run oder ``None``, wenn nichts zu tun war.
    Abgeschlossene Runs bleiben unverändert. Läuft kein Live-Worker und der
    DB-Status ist ``queued``/``running``, gilt der Job als unterbrochen.
    """
    launcher = get_validation_job_launcher()
    if launcher.is_active(project.id):
        return None

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError:
        return None

    try:
        active = find_active_run(conn, project_id=project.id)
        if active is None:
            return None
        # Double-check: Worker könnte zwischenzeitlich gestartet sein.
        if launcher.is_active(project.id):
            return None

        updated = active.model_copy(
            update={
                "status": ValidationRunStatus.FAILED,
                "completed_at": _now(),
                "error_summary": (
                    f"{WORKER_INTERRUPTED_ERROR_CODE}: "
                    "Hintergrund-Worker ist nicht mehr aktiv "
                    "(Prozessabbruch oder Neustart)."
                ),
            }
        )
        update_run(conn, updated)
        conn.commit()
        try:
            report = build_report_from_run(conn, run=updated)
            save_validation_report(project.project_root_path, report)
        except InventoryArtifactError:
            pass
        return updated
    finally:
        conn.close()
