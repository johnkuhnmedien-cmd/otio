"""Wiederanlaufvertrag für Discovery Remux-/Intake-Jobs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.intake_job_launcher import get_intake_job_launcher
from otio_app.discovery_v2.domain.media_intake import (
    WORKER_INTERRUPTED_INTAKE_ERROR_CODE,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_report_from_intake_run,
    find_active_intake_run,
    list_intake_run_assets,
    open_registry,
    save_intake_run_report,
    update_intake_run,
    update_intake_run_asset,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_intake_run(project: Project) -> IntakeRunRecord | None:
    """Markiert verwaiste queued/running Intake-Runs als failed."""
    launcher = get_intake_job_launcher()
    if launcher.is_active(project.id):
        return None

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError:
        return None

    try:
        active = find_active_intake_run(conn, project_id=project.id)
        if active is None:
            return None
        if launcher.is_active(project.id):
            return None

        for asset in list_intake_run_assets(conn, run_id=active.run_id):
            if asset.status in {
                IntakeRunAssetStatus.PENDING,
                IntakeRunAssetStatus.RUNNING,
            }:
                update_intake_run_asset(
                    conn,
                    asset.model_copy(
                        update={
                            "status": IntakeRunAssetStatus.FAILED,
                            "error_code": WORKER_INTERRUPTED_INTAKE_ERROR_CODE,
                            "error_message": (
                                "Intake-Worker ist nicht mehr aktiv "
                                "(Prozessabbruch oder Neustart)."
                            ),
                            "processed_at": _now(),
                        }
                    ),
                )

        updated = active.model_copy(
            update={
                "status": IntakeRunStatus.FAILED,
                "completed_at": _now(),
                "error_summary": (
                    f"{WORKER_INTERRUPTED_INTAKE_ERROR_CODE}: "
                    "Intake-Worker ist nicht mehr aktiv "
                    "(Prozessabbruch oder Neustart)."
                ),
            }
        )
        update_intake_run(conn, updated)
        conn.commit()
        try:
            assets = list_intake_run_assets(conn, run_id=updated.run_id)
            save_intake_run_report(
                project.project_root_path,
                build_report_from_intake_run(updated, assets=assets),
            )
        except InventoryArtifactError:
            pass
        return updated
    finally:
        conn.close()


# Alias für Remux-Services.
reconcile_orphaned_remux_intake_run = reconcile_orphaned_intake_run
