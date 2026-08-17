"""UI: sequenzieller Enhanced-Auto-Lauf + Speicherorte der Sprach-Standards."""

from __future__ import annotations

import streamlit as st

from otio_app.models import Project, ProjectMode
from otio_app.project_repository import get_project_by_id
from otio_app.services.job_registry import any_job_running
from otio_app.ui.voiceover_generation.language_standards_ui import (
    render_language_standards_expander,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    JobStatus,
    get_enhanced_auto_run_job_manager,
)
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY
from otio_app.ui.polling import poll_while_running


def render_enhanced_auto_run_sidebar() -> None:
    """Start/Stop in der Sidebar — nur Enhanced-Projekte."""
    project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
    if not project_id:
        return
    project = get_project_by_id(str(project_id))
    if project is None or project.project_mode != ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
        return
    _render_auto_run_controls(project, key_scope="sidebar")
    render_language_standards_expander()


def render_enhanced_auto_run_banner(project_id: str) -> None:
    """Fortschritt auf Enhanced-Seiten, inkl. Stop."""
    manager = get_enhanced_auto_run_job_manager()
    state = manager.get_state(project_id)
    if state is None:
        return

    if state.status == JobStatus.RUNNING:
        extra = " **(Stop angefordert …)**" if state.cancel_requested else ""
        detail = state.message or state.step_label or "läuft"
        if state.item_total > 0 and state.item_label:
            detail = (
                f"{state.step_label}: {state.item_label} "
                f"({state.item_index}/{state.item_total})"
            )
        col_info, col_stop = st.columns([5, 1])
        with col_info:
            st.info(
                f"▶ Auto-Lauf sequenziell — Schritt {state.step_index}/"
                f"{state.step_total} · {detail}{extra}"
            )
            if state.step_total > 0:
                st.progress(min(1.0, max(0.0, state.step_index / state.step_total)))
        with col_stop:
            if st.button(
                "⏹ Stoppen",
                key=f"global_stop_auto_run_{project_id}",
                disabled=state.cancel_requested,
            ):
                manager.request_cancel(project_id)
                st.rerun()
        poll_while_running(
            lambda: None,
            lambda: manager.is_running(project_id),
            refresh_key=f"auto_run_refresh_{project_id}",
        )
        return

    if state.status == JobStatus.CANCELLED:
        st.warning(state.message or "Auto-Lauf gestoppt — Teilergebnisse bleiben.")
    elif state.status == JobStatus.FAILED:
        st.error(f"Auto-Lauf fehlgeschlagen: {state.error or 'Unbekannt'}")
    elif state.status == JobStatus.COMPLETED:
        skipped = f" · übersprungen: {len(state.skipped)}" if state.skipped else ""
        st.success(
            (state.message or "Auto-Lauf fertig.")
            + skipped
            + " Als Nächstes manuell: Funnel."
        )
    if st.button("Hinweis schließen", key=f"auto_run_dismiss_{project_id}"):
        manager.dismiss(project_id)
        st.rerun()


def _render_auto_run_controls(project: Project, *, key_scope: str) -> None:
    manager = get_enhanced_auto_run_job_manager()
    state = manager.get_state(project.id)
    running = state is not None and state.status == JobStatus.RUNNING
    other_running = (not running) and any_job_running(project.id)

    st.markdown("**Auto-Lauf**")
    st.caption(
        "Ein Button, **sequenziell** (nie parallel): Brief + Titel → Style → "
        "Dramaturgie (auto-bestätigen) → Kapitel-Skripte → Script Lock → "
        "Intro (erste gültige Variante) → TTS → Intro-Cut → alle Kapitel-Cuts. "
        "Stoppt **vor** Funnel / Timing / Musik / Export. Fertige Schritte "
        "werden übersprungen."
    )
    start_disabled = running or other_running
    help_text = None
    if running:
        help_text = "Auto-Lauf läuft bereits."
    elif other_running:
        help_text = "Ein anderer Hintergrund-Job läuft — zuerst stoppen."
    if st.button(
        "▶ Alle Schritte nacheinander",
        key=f"enh_auto_run_start_{key_scope}_{project.id}",
        type="primary",
        disabled=start_disabled,
        help=help_text,
    ):
        if manager.start(project):
            st.rerun()
        else:
            st.warning("Auto-Lauf läuft bereits.")
    if running:
        if st.button(
            "⏹ Auto-Lauf stoppen",
            key=f"enh_auto_run_stop_{key_scope}_{project.id}",
            disabled=bool(state and state.cancel_requested),
        ):
            manager.request_cancel(project.id)
            st.rerun()
        if state is not None:
            st.caption(state.message or state.step_label)
