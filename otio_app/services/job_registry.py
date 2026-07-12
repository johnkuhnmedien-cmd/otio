"""Zentrale Übersicht und Bereinigung aller Hintergrund-Jobs."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.project_repository import list_projects
from otio_app.services.asset_analysis_job import (
    JobStatus,
    get_asset_analysis_job_manager,
)
from otio_app.services.clean_media_job import get_clean_media_job_manager
from otio_app.services.otio_export_job import get_otio_export_job_manager
from otio_app.services.voice_analysis_job import get_voice_analysis_job_manager


@dataclass(frozen=True)
class JobActivity:
    kind: str
    project_id: str
    status: str
    detail: str
    thread_alive: bool | None


def reconcile_all_jobs() -> None:
    """Markiert hängende Jobs als beendet, wenn der Thread nicht mehr läuft."""
    clean_manager = get_clean_media_job_manager()
    for project in list_projects():
        clean_manager.reconcile_stuck_job(project.id)
        get_voice_analysis_job_manager().reconcile_stuck_job(project.id)
        get_asset_analysis_job_manager().reconcile_stuck_job(project.id)
        get_otio_export_job_manager().reconcile_stuck_job(project.id)


def any_job_running(project_id: str | None = None) -> bool:
    reconcile_all_jobs()
    managers = (
        get_clean_media_job_manager(),
        get_voice_analysis_job_manager(),
        get_asset_analysis_job_manager(),
        get_otio_export_job_manager(),
    )
    if project_id is None:
        return any(
            manager.is_running(project.id)
            for project in list_projects()
            for manager in managers
        )
    return any(manager.is_running(project_id) for manager in managers)


def collect_job_activity() -> list[JobActivity]:
    reconcile_all_jobs()
    activities: list[JobActivity] = []

    for project in list_projects():
        project_id = project.id
        clean_state = get_clean_media_job_manager().get_state(project_id)
        if clean_state is not None:
            activities.append(
                JobActivity(
                    kind="Clean Media",
                    project_id=project_id,
                    status=clean_state.status.value,
                    detail=f"{clean_state.done_media}/{clean_state.total_media} Medien",
                    thread_alive=get_clean_media_job_manager().thread_alive(project_id),
                )
            )

        voice_state = get_voice_analysis_job_manager().get_state(project_id)
        if voice_state is not None:
            activities.append(
                JobActivity(
                    kind="Voice-over",
                    project_id=project_id,
                    status=voice_state.status.value,
                    detail=f"{voice_state.done_files}/{voice_state.total_files} Dateien",
                    thread_alive=get_voice_analysis_job_manager().thread_alive(project_id),
                )
            )

        asset_state = get_asset_analysis_job_manager().get_state(project_id)
        if asset_state is not None:
            activities.append(
                JobActivity(
                    kind="Asset-Analyse",
                    project_id=project_id,
                    status=asset_state.status.value,
                    detail=f"{asset_state.done_media}/{asset_state.total_media} Assets",
                    thread_alive=get_asset_analysis_job_manager().thread_alive(project_id),
                )
            )

        otio_state = get_otio_export_job_manager().get_state(project_id)
        if otio_state is not None:
            detail = otio_state.message or otio_state.phase
            if otio_state.total > 0:
                detail = f"{otio_state.current}/{otio_state.total} Clips · {detail}"
            activities.append(
                JobActivity(
                    kind="OTIO-Export",
                    project_id=project_id,
                    status=otio_state.status.value,
                    detail=detail,
                    thread_alive=get_otio_export_job_manager().thread_alive(project_id),
                )
            )

    return activities


def force_reset_all_jobs() -> int:
    """Bricht alle laufenden Jobs ab und räumt Zustände auf."""
    count = 0
    clean_manager = get_clean_media_job_manager()
    voice_manager = get_voice_analysis_job_manager()
    asset_manager = get_asset_analysis_job_manager()
    otio_manager = get_otio_export_job_manager()

    for project in list_projects():
        project_id = project.id
        if clean_manager.is_running(project_id):
            clean_manager.force_reset(project_id)
            count += 1
        if voice_manager.is_running(project_id):
            voice_manager.force_reset(project_id)
            count += 1
        if asset_manager.is_running(project_id):
            asset_manager.force_reset(project_id)
            count += 1
        if otio_manager.is_running(project_id):
            otio_manager.force_reset(project_id)
            count += 1
    return count


def running_job_count() -> int:
    return sum(
        1
        for activity in collect_job_activity()
        if activity.status == JobStatus.RUNNING.value
    )
