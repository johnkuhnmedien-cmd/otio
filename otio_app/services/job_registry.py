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


def _job_managers() -> tuple[object, ...]:
    return (
        get_clean_media_job_manager(),
        get_voice_analysis_job_manager(),
        get_asset_analysis_job_manager(),
        get_otio_export_job_manager(),
        get_supplement_funnel_job_manager(),
        get_enhanced_auto_run_job_manager(),
        get_map_render_job_manager(),
        get_language_auto_run_queue_manager(),
    )


def _manager_memory_ids(manager: object) -> set[str]:
    """Projekt-IDs mit Job-Zustand im RAM — ohne JSON auf der Platte zu lesen."""
    ids: set[str] = set()
    lock = getattr(manager, "_lock", None)
    names = ("_jobs", "_states")

    def _read() -> None:
        for name in names:
            mapping = getattr(manager, name, None)
            if isinstance(mapping, dict):
                ids.update(str(key) for key in mapping)

    if lock is not None:
        with lock:
            _read()
    else:
        _read()
    return ids


def reconcile_all_jobs() -> None:
    """Markiert hängende Jobs als beendet, wenn der Thread nicht mehr läuft.

    Nur Jobs, die schon im RAM liegen — kein Scan aller Projektordner
    (NAS/Movies) und kein Nachladen von Karten-Job-JSON.
    """
    try:
        import streamlit as st

        if st.session_state.get(_RECONCILE_FLAG):
            return
        st.session_state[_RECONCILE_FLAG] = True
    except Exception:  # noqa: BLE001 — Tests / CLI ohne Streamlit-Session
        pass
    for manager in _job_managers():
        reconcile = getattr(manager, "reconcile_stuck_job", None)
        if not callable(reconcile):
            continue
        for project_id in _manager_memory_ids(manager):
            try:
                reconcile(project_id)
            except Exception:  # noqa: BLE001 — ein Job darf Tabwechsel nicht crashen
                continue


def _manager_has_running(manager: object) -> bool:
    is_running = getattr(manager, "is_running", None)
    if not callable(is_running):
        return False
    return any(is_running(project_id) for project_id in _manager_memory_ids(manager))


def any_job_running(project_id: str | None = None, *, reconcile: bool = True) -> bool:
    if reconcile:
        reconcile_all_jobs()
    managers = _job_managers()
    if project_id is None:
        return any(_manager_has_running(manager) for manager in managers)
    return any(manager.is_running(project_id) for manager in managers)


def collect_job_activity() -> list[JobActivity]:
    reconcile_all_jobs()
    activities: list[JobActivity] = []

    clean_manager = get_clean_media_job_manager()
    voice_manager = get_voice_analysis_job_manager()
    asset_manager = get_asset_analysis_job_manager()
    otio_manager = get_otio_export_job_manager()
    funnel_manager = get_supplement_funnel_job_manager()
    auto_manager = get_enhanced_auto_run_job_manager()
    map_manager = get_map_render_job_manager()
    queue_manager = get_language_auto_run_queue_manager()

    clean_ids = _manager_memory_ids(clean_manager)
    voice_ids = _manager_memory_ids(voice_manager)
    asset_ids = _manager_memory_ids(asset_manager)
    otio_ids = _manager_memory_ids(otio_manager)
    funnel_ids = _manager_memory_ids(funnel_manager)
    auto_ids = _manager_memory_ids(auto_manager)
    map_ids = _manager_memory_ids(map_manager)
    queue_ids = _manager_memory_ids(queue_manager)
    known_ids = (
        clean_ids
        | voice_ids
        | asset_ids
        | otio_ids
        | funnel_ids
        | auto_ids
        | map_ids
        | queue_ids
    )

    for project_id in sorted(known_ids):
        if project_id in clean_ids:
            clean_state = clean_manager.get_state(project_id)
            if clean_state is not None:
                activities.append(
                    JobActivity(
                        kind="Clean Media",
                        project_id=project_id,
                        status=clean_state.status.value,
                        detail=f"{clean_state.done_media}/{clean_state.total_media} Medien",
                        thread_alive=clean_manager.thread_alive(project_id),
                    )
                )

        if project_id in voice_ids:
            voice_state = voice_manager.get_state(project_id)
            if voice_state is not None:
                activities.append(
                    JobActivity(
                        kind="Voice-over",
                        project_id=project_id,
                        status=voice_state.status.value,
                        detail=f"{voice_state.done_files}/{voice_state.total_files} Dateien",
                        thread_alive=voice_manager.thread_alive(project_id),
                    )
                )

        if project_id in asset_ids:
            asset_state = asset_manager.get_state(project_id)
            if asset_state is not None:
                activities.append(
                    JobActivity(
                        kind="Asset-Analyse",
                        project_id=project_id,
                        status=asset_state.status.value,
                        detail=f"{asset_state.done_media}/{asset_state.total_media} Assets",
                        thread_alive=asset_manager.thread_alive(project_id),
                    )
                )

        if project_id in otio_ids:
            otio_state = otio_manager.get_state(project_id)
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
                        thread_alive=otio_manager.thread_alive(project_id),
                    )
                )

        if project_id in funnel_ids:
            funnel_state = funnel_manager.get_state(project_id)
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
                        thread_alive=funnel_manager.thread_alive(project_id),
                    )
                )

        if project_id in auto_ids:
            auto_state = auto_manager.get_state(project_id)
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
                        thread_alive=auto_manager.thread_alive(project_id),
                    )
                )

        if project_id in map_ids:
            try:
                map_state = map_manager.get_state(project_id)
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
                        thread_alive=map_manager.thread_alive(project_id),
                    )
                )

        if project_id in queue_ids:
            queue_state = queue_manager.get_state(project_id)
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
                        thread_alive=queue_manager.thread_alive(project_id),
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
