"""UI für laufende Asset-Analyse-Jobs (Fortschritt & Stop)."""

from __future__ import annotations

import streamlit as st

from otio_app.services.analysis_log import read_analysis_log_tail
from otio_app.services.asset_analysis_job import (
    AssetAnalysisJobState,
    JobStatus,
    get_asset_analysis_job_manager,
)
from otio_app.ui.analysis_report import render_analysis_report
from otio_app.ui.polling import poll_while_running


def _format_progress_line(state: AssetAnalysisJobState) -> str:
    phase = state.phase
    data = state.phase_data
    if phase == "media_start":
        return (
            f"Analysiere `{data.get('media_name', '…')}` "
            f"({data.get('media_index', '?')}/{data.get('media_count', '?')} "
            f"in {data.get('folder', '…')})"
        )
    if phase == "folder_start":
        return (
            f"Ordner **{data.get('folder', '…')}** "
            f"({data.get('folder_index', '?')}/{data.get('folder_count', '?')})"
        )
    if phase == "media_done":
        return f"Fertig: `{data.get('media_name', '…')}`"
    if phase == "folder_skip":
        return f"Ordner **{data.get('folder', '…')}** übersprungen"
    return "Asset-Analyse läuft …"


def _render_running_job(state: AssetAnalysisJobState, *, stop_key: str) -> None:
    total = max(state.total_media, 1)
    done = min(state.done_media, total)
    st.progress(done / total, text=f"{done} / {total} Assets")
    st.caption(_format_progress_line(state))
    if state.cancel_requested:
        st.warning(
            "Stop angefordert — aktueller FFmpeg-/Gemini-Schritt wird noch beendet, "
            "danach bricht die Analyse ab."
        )
    if st.button("⏹ Asset-Analyse stoppen", key=stop_key, disabled=state.cancel_requested):
        get_asset_analysis_job_manager().request_cancel(state.project_id)
        st.rerun()


def render_asset_analysis_job_banner(project_id: str) -> None:
    """Globales Banner — auf allen Seiten sichtbar, solange ein Job aktiv ist."""
    manager = get_asset_analysis_job_manager()
    state = manager.get_state(project_id)
    if state is None:
        return

    if state.status == JobStatus.RUNNING:
        col_info, col_stop = st.columns([5, 1])
        with col_info:
            line = _format_progress_line(state)
            extra = (
                " **(Stop angefordert …)**"
                if state.cancel_requested
                else ""
            )
            st.info(
                f"⏳ Asset-Analyse läuft im Hintergrund — {line}{extra} "
                "Du kannst zu **③ Schnittplan** wechseln."
            )
        with col_stop:
            if st.button(
                "⏹ Stoppen",
                key=f"global_stop_assets_{project_id}",
                disabled=state.cancel_requested,
            ):
                manager.request_cancel(project_id)
                st.rerun()
        return

    if state.status == JobStatus.CANCELLED:
        st.warning(
            "Asset-Analyse wurde gestoppt. Bereits analysierte Assets sind im Cache gespeichert."
        )
    elif state.status == JobStatus.FAILED:
        st.error(f"Asset-Analyse fehlgeschlagen: {state.error or 'Unbekannter Fehler'}")
    elif state.status == JobStatus.COMPLETED:
        st.success("Asset-Analyse abgeschlossen.")

    if st.button("Hinweis schließen", key=f"dismiss_asset_job_{project_id}"):
        manager.dismiss(project_id)
        st.rerun()


def _render_asset_running_panel(project) -> None:
    state = get_asset_analysis_job_manager().get_state(project.id)
    if state is None or state.status != JobStatus.RUNNING:
        return
    _render_running_job(state, stop_key=f"stop_assets_{project.id}")


def render_asset_analysis_job_monitor(project, *, expanded: bool = True) -> None:
    """Fortschrittsanzeige auf der Analyse-Seite."""
    manager = get_asset_analysis_job_manager()
    state = manager.get_state(project.id)
    if state is None:
        return

    if state.status == JobStatus.RUNNING:
        poll_while_running(
            lambda: _render_asset_running_panel(project),
            lambda: manager.is_running(project.id),
            refresh_key=f"asset_refresh_poll_{project.id}",
        )
        return

    if not expanded:
        return

    if state.status == JobStatus.CANCELLED:
        st.warning("Analyse gestoppt — Teilergebnisse wurden gespeichert.")
    elif state.status == JobStatus.FAILED:
        st.error(f"Analyse fehlgeschlagen: {state.error or 'Unbekannter Fehler'}")
    elif state.status == JobStatus.COMPLETED:
        st.success("Asset-Analyse abgeschlossen.")

    if state.report is not None:
        render_analysis_report(state.report)

    log_tail = read_analysis_log_tail(project)
    if log_tail:
        failures = state.report.failures if state.report else []
        with st.expander("Analyse-Protokoll (Details)", expanded=bool(failures)):
            st.code(log_tail)

    if st.button("Bericht schließen", key=f"dismiss_job_report_{project.id}"):
        manager.dismiss(project.id)
        st.rerun()
