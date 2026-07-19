"""Recovery for orphaned Discovery V2 visual edit runs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.visual_edit_job_launcher import get_visual_edit_job_launcher
from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.domain.visual_edit import (
    ACTIVE_VISUAL_EDIT_RUN_STATUSES,
    VISUAL_EDIT_ERROR_WORKER_INTERRUPTED,
    VisualEditRunStatus,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_visual_edit_run(project: Project) -> None:
    """Mark queued/running visual-edit rows failed if no launcher owns them."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError:
        return
    launcher = get_visual_edit_job_launcher()
    if launcher.is_active(project.id):
        return
    try:
        conn = repo.open_visual_edit_registry(project.project_root_path)
    except RegistryDatabaseError:
        return
    active = None
    try:
        active = repo.find_active_visual_edit_run(conn, project_id=project.id)
        if active is None or active.status not in ACTIVE_VISUAL_EDIT_RUN_STATUSES:
            return
        failed = active.model_copy(
            update={
                "status": VisualEditRunStatus.FAILED,
                "error_code": VISUAL_EDIT_ERROR_WORKER_INTERRUPTED,
                "error_message": "Visual edit worker was interrupted.",
                "finished_at": _now(),
            }
        )
        repo.update_visual_edit_run(conn, failed)
        conn.commit()
    finally:
        conn.close()
    if active is not None:
        repo.cleanup_visual_edit_temp(project.project_root_path, run_id=active.run_id)


__all__ = ["reconcile_orphaned_visual_edit_run"]
