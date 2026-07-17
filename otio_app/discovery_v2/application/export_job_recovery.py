"""Recovery for orphaned Discovery V2 export runs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.export_job_launcher import get_export_job_launcher
from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.domain.export import (
    ACTIVE_EXPORT_RUN_STATUSES,
    EXPORT_ERROR_WORKER_INTERRUPTED,
    OtioExportRunStatus,
)
from otio_app.discovery_v2.persistence import export_repository as repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_export_run(project: Project) -> None:
    """Mark queued/running export rows failed if no launcher owns them."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return
    launcher = get_export_job_launcher()
    if launcher.is_active(project.id):
        return
    try:
        conn = repo.open_export_registry(project.project_root_path)
    except RegistryDatabaseError:
        return
    active = None
    try:
        active = repo.find_active_export_run(conn, project_id=project.id)
        if active is None or active.status not in ACTIVE_EXPORT_RUN_STATUSES:
            return
        failed = active.model_copy(
            update={
                "status": OtioExportRunStatus.FAILED,
                "error_code": EXPORT_ERROR_WORKER_INTERRUPTED,
                "error_message": "Export worker was interrupted.",
                "finished_at": _now(),
            }
        )
        repo.update_otio_export_run(conn, failed)
        conn.commit()
    finally:
        conn.close()
    if active is not None:
        repo.cleanup_export_temp(project.project_root_path, run_id=active.run_id)


__all__ = ["reconcile_orphaned_export_run"]
