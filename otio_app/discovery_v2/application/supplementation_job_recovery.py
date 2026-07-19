"""Recovery for orphaned Discovery V2 supplementation runs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    get_supplementation_job_launcher,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.supplementation import (
    ACTIVE_SUPPLEMENTATION_RUN_STATUSES,
    SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED,
    SupplementationAttemptStatus,
    SupplementationRunStatus,
)
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_supplementation_run(project: Project) -> None:
    """Mark queued/running DB rows failed if no launcher owns them.

    Recovery never calls a stock gateway and only cleans the interrupted run temp dir.
    """

    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return
    launcher = get_supplementation_job_launcher()
    if launcher.is_active(project.id):
        return
    try:
        conn = repo.open_supplementation_registry(project.project_root_path)
    except RegistryDatabaseError:
        return
    try:
        active = repo.find_active_supplementation_run(conn, project_id=project.id)
        if active is None or active.status not in ACTIVE_SUPPLEMENTATION_RUN_STATUSES:
            return
        failed = active.model_copy(
            update={
                "status": SupplementationRunStatus.FAILED,
                "error_code": SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED,
                "error_message": "Supplementation worker was interrupted.",
                "finished_at": _now(),
            }
        )
        repo.update_supplementation_run(conn, failed)
        for attempt in repo.list_supplementation_attempts(conn, run_id=active.run_id):
            if attempt.status in {
                SupplementationAttemptStatus.COMPLETED,
                SupplementationAttemptStatus.FAILED,
                SupplementationAttemptStatus.REUSED,
                SupplementationAttemptStatus.INTERRUPTED,
            }:
                continue
            repo.update_supplementation_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": SupplementationAttemptStatus.INTERRUPTED,
                        "error_code": SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED,
                        "error_message": "Supplementation worker was interrupted.",
                        "completed_at": _now(),
                    }
                ),
            )
        conn.commit()
    finally:
        conn.close()
    repo.cleanup_supplementation_temp(project.project_root_path, run_id=active.run_id)


__all__ = ["reconcile_orphaned_supplementation_run"]
