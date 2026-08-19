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
from otio_app.services.language_auto_run_queue import (
    get_language_auto_run_queue_manager,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    get_enhanced_auto_run_job_manager,
)
from otio_app.services.without_voiceover_enhanced.maps.map_render_job import (
    get_map_render_job_manager,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_job import (
    get_supplement_funnel_job_manager,
)


@dataclass(frozen=True)
class JobActivity:
    kind: str
    project_id: str
    status: str
    detail: str
    thread_alive: bool | None


_RECONCILE_FLAG = "_otio_jobs_reconciled_this_run"


def begin_ui_script_run() -> None:
    """Pro Streamlit-Rerun einmal Job-Reconcile erlauben (Tabwechsel)."""
    try:
        import streamlit as st

        st.session_state.pop(_RECONCILE_FLAG, None)
    except Exception:  # noqa: BLE001 — Tests / CLI ohne Streamlit-Session
        pass


def reconcile_all_jobs() -> None:
    """Markiert hängende Jobs als beendet, wenn der Thread nicht mehr läuft."""
    try:
        import streamlit as st

        if st.session_state.get(_RECONCILE_FLAG):
            return
        st.session_state[_RECONCILE_FLAG] = True
    except Exception:  # noqa: BLE001 — Tests / CLI ohne Streamlit-Session
        pass
    clean_manager = get_clean_media_job_manager()
    for project in list_projects():
        for reconcile in (
            clean_manager.reconcile_stuck_job,
            get_voice_analysis_job_manager().reconcile_stuck_job,
            get_asset_analysis_job_manager().reconcile_stuck_job,
            get_otio_export_job_manager().reconcile_stuck_job,
            get_supplement_funnel_job_manager().reconcile_stuck_job,
            get_enhanced_auto_run_job_manager().reconcile_stuck_job,
            get_language_auto_run_queue_manager().reconcile_stuck_job,
        ):
            try:
                reconcile(project.id)
            except Exception:  # noqa: BLE001 — ein Projekt darf Tabwechsel nicht crashen
                continue
        if project.is_without_voiceover_enhanced:
            try:
                get_map_render_job_manager().reconcile_stuck_job(project.id)
            except Exception:  # noqa: BLE001
                continue


def any_job_running(project_id: str | None = None, *, reconcile: bool = True) -> bool:
    if reconcile:
        reconcile_all_jobs()
    managers = (
        get_clean_media_job_manager(),
        get_voice_analysis_job_manager(),
        get_asset_analysis_job_manager(),
        get_otio_export_job_manager(),
        get_supplement_funnel_job_manager(),
        get_enhanced_auto_run_job_manager(),
        get_map_render_job_manager(),
        get_language_auto_run_queue_manager(),
    )
    if project_id is None:
        if get_language_auto_run_queue_manager().any_running():
            return True
        if get_enhanced_auto_run_job_manager().any_running():
            return True
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

        funnel_state = get_supplement_funnel_job_manager().get_state(project_id)
        if funnel_state is not None:
            detail = funnel_state.message or funnel_state.phase or "Funnel"
            if funnel_state.gap_total > 0:
                detail = (
                    f"Gap {funnel_state.gap_index}/{funnel_state.gap_total} · "
                    f"{detail}"
                )
            activities.append(
                JobActivity(
                    kind="Supplement-Funnel",
                    project_id=project_id,
                    status=funnel_state.status.value,
                    detail=detail,
                    thread_alive=get_supplement_funnel_job_manager().thread_alive(
                        project_id
                    ),
                )
            )

        auto_state = get_enhanced_auto_run_job_manager().get_state(project_id)
        if auto_state is not None:
            detail = auto_state.message or auto_state.step_label or "Auto-Lauf"
            if auto_state.step_total > 0:
                detail = (
                    f"Schritt {auto_state.step_index}/{auto_state.step_total} · {detail}"
                )
            activities.append(
                JobActivity(
                    kind="Enhanced Auto-Lauf",
                    project_id=project_id,
                    status=auto_state.status.value,
                    detail=detail,
                    thread_alive=get_enhanced_auto_run_job_manager().thread_alive(
                        project_id
                    ),
                )
            )

        map_state = None
        if project.is_without_voiceover_enhanced:
            try:
                map_state = get_map_render_job_manager().get_state(project_id)
            except Exception:  # noqa: BLE001
                map_state = None
        if map_state is not None:
            done = sum(1 for item in map_state.items.values() if item.status == "done")
            total = max(len(map_state.items), 1)
            detail = map_state.message or f"{done}/{total} Karten"
            activities.append(
                JobActivity(
                    kind="Karten",
                    project_id=project_id,
                    status=map_state.status.value,
                    detail=detail,
                    thread_alive=get_map_render_job_manager().thread_alive(project_id),
                )
            )

        queue_state = get_language_auto_run_queue_manager().get_state(project_id)
        if queue_state is not None:
            current = queue_state.current_language or "—"
            total = len(queue_state.languages)
            detail = f"Sprache {queue_state.current_index + 1}/{total}: {current}"
            if queue_state.completed_languages:
                detail += f" · fertig: {', '.join(queue_state.completed_languages)}"
            activities.append(
                JobActivity(
                    kind="Sprachen-Queue",
                    project_id=project_id,
                    status=queue_state.status,
                    detail=detail,
                    thread_alive=get_language_auto_run_queue_manager().thread_alive(
                        project_id
                    ),
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
    funnel_manager = get_supplement_funnel_job_manager()
    auto_manager = get_enhanced_auto_run_job_manager()
    map_manager = get_map_render_job_manager()
    queue_manager = get_language_auto_run_queue_manager()
    count += queue_manager.force_reset_all()

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
        if funnel_manager.is_running(project_id):
            funnel_manager.force_reset(project_id)
            count += 1
        if auto_manager.is_running(project_id):
            auto_manager.force_reset(project_id)
            count += 1
        if map_manager.is_running(project_id):
            map_manager.force_reset(project_id)
            count += 1
    return count


def running_job_count() -> int:
    return sum(
        1
        for activity in collect_job_activity()
        if activity.status == JobStatus.RUNNING.value
    )
