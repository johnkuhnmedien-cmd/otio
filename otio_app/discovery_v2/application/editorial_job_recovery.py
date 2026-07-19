"""Recovery for orphaned Discovery V2 editorial runs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    get_editorial_job_launcher,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_WORKER_INTERRUPTED,
    ACTIVE_EDITORIAL_RUN_STATUSES,
    EditorialAttemptStatus,
    EditorialRunStatus,
)
from otio_app.discovery_v2.persistence.editorial_repository import (
    cleanup_editorial_temp,
    find_active_editorial_run,
    list_editorial_attempts,
    open_editorial_registry,
    update_editorial_attempt,
    update_editorial_run,
)
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_editorial_run(project: Project) -> None:
    """Mark queued/running DB rows as failed if no launcher thread owns them."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return
    launcher = get_editorial_job_launcher()
    if launcher.is_active(project.id):
        return
    try:
        conn = open_editorial_registry(project.project_root_path)
    except RegistryDatabaseError:
        return
    try:
        active = find_active_editorial_run(conn, project_id=project.id)
        if active is None or active.status not in ACTIVE_EDITORIAL_RUN_STATUSES:
            return
        failed = active.model_copy(
            update={
                "status": EditorialRunStatus.FAILED,
                "error_code": EDITORIAL_ERROR_WORKER_INTERRUPTED,
                "error_message": "Editorial worker was interrupted.",
                "finished_at": _now(),
            }
        )
        update_editorial_run(conn, failed)
        for attempt in list_editorial_attempts(conn, run_id=active.run_id):
            if attempt.status in {
                EditorialAttemptStatus.COMPLETED,
                EditorialAttemptStatus.FAILED,
                EditorialAttemptStatus.REUSED,
                EditorialAttemptStatus.INTERRUPTED,
            }:
                continue
            update_editorial_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": EditorialAttemptStatus.INTERRUPTED,
                        "error_code": EDITORIAL_ERROR_WORKER_INTERRUPTED,
                        "error_message": "Editorial worker was interrupted.",
                        "completed_at": _now(),
                    }
                ),
            )
        conn.commit()
    finally:
        conn.close()
    cleanup_editorial_temp(project.project_root_path, run_id=active.run_id)


__all__ = ["reconcile_orphaned_editorial_run"]
