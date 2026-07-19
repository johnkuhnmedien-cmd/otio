"""Recovery for orphaned Discovery V2 narration runs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.narration_job_launcher import get_narration_job_launcher
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.narration import (
    ACTIVE_NARRATION_RUN_STATUSES,
    NARRATION_ERROR_WORKER_INTERRUPTED,
    NarrationAttemptStatus,
    NarrationRunStatus,
)
from otio_app.discovery_v2.persistence import narration_repository as repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_narration_run(project: Project) -> None:
    """Mark queued/running narration rows failed if no launcher owns them."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return
    launcher = get_narration_job_launcher()
    if launcher.is_active(project.id):
        return
    try:
        conn = repo.open_narration_registry(project.project_root_path)
    except RegistryDatabaseError:
        return
    active = None
    try:
        active = repo.find_active_narration_run(conn, project_id=project.id)
        if active is None or active.status not in ACTIVE_NARRATION_RUN_STATUSES:
            return
        failed = active.model_copy(
            update={
                "status": NarrationRunStatus.FAILED,
                "error_code": NARRATION_ERROR_WORKER_INTERRUPTED,
                "error_message": "Narration worker was interrupted.",
                "finished_at": _now(),
            }
        )
        repo.update_voice_run(conn, failed)
        for attempt in repo.list_voice_attempts(conn, run_id=active.run_id):
            if attempt.status in {
                NarrationAttemptStatus.COMPLETED,
                NarrationAttemptStatus.FAILED,
                NarrationAttemptStatus.REUSED,
                NarrationAttemptStatus.INTERRUPTED,
            }:
                continue
            repo.update_voice_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": NarrationAttemptStatus.INTERRUPTED,
                        "error_code": NARRATION_ERROR_WORKER_INTERRUPTED,
                        "error_message": "Narration worker was interrupted.",
                        "completed_at": _now(),
                    }
                ),
            )
        conn.commit()
    finally:
        conn.close()
    if active is not None:
        repo.cleanup_narration_temp(project.project_root_path, run_id=active.run_id)


__all__ = ["reconcile_orphaned_narration_run"]
