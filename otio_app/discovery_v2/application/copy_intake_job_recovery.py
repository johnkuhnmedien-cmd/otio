"""Wiederanlaufvertrag für Discovery Copy-Intake-Jobs."""

from __future__ import annotations

from otio_app.discovery_v2.application.remux_intake_job_recovery import (
    reconcile_orphaned_intake_run,
)
from otio_app.discovery_v2.domain.media_intake import IntakeRunRecord
from otio_app.models import Project


def reconcile_orphaned_copy_intake_run(
    project: Project,
) -> IntakeRunRecord | None:
    """Markiert verwaiste queued/running Intake-Runs als failed."""
    return reconcile_orphaned_intake_run(project)
