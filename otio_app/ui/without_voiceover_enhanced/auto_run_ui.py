"""UI: sequenzieller Enhanced-Auto-Lauf + Speicherorte der Sprach-Standards."""

from __future__ import annotations

import streamlit as st

from otio_app.models import Project, ProjectMode
from otio_app.project_repository import get_project_by_id
from otio_app.services.job_registry import any_job_running
from otio_app.services.language_auto_run_queue import (
    get_language_auto_run_queue_manager,
)
from otio_app.ui.voiceover_generation.language_standards_ui import (
    render_language_standards_expander,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    EnhancedAutoRunJobState,
    JobStatus,
    get_enhanced_auto_run_job_manager,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    list_auto_run_step_statuses,
)
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY
from otio_app.ui.polling import poll_while_running


def auto_run_progress_fraction(state: EnhancedAutoRunJobState) -> float:
    """Schritt plus Kapitelanteil — nicht nur 4/10, während Naxos 1/18 steht."""
    if state.step_total <= 0:
        return 0.0
    completed_steps = max(0, state.step_index - 1)
    fraction = completed_steps / state.step_total
    if state.item_total > 0:
        fraction += (max(0, state.item_index) / state.item_total) / state.step_total
    elif state.step_index > 0:
        fraction = state.step_index / state.step_total
    return min(1.0, max(0.0, fraction))


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


def render_enhanced_auto_run_embedded(project: Project, *, key_scope: str) -> None:
    """Start-Button + Fortschritt — wiederverwendbar (Seite, Tab, Panel)."""
    _render_auto_run_controls(project, key_scope=key_scope)
    render_enhanced_auto_run_banner(str(project.id), key_scope=key_scope)


def render_enhanced_auto_run_page() -> None:
    """Eigene Sidebar-Seite — steht in der Navigationsliste zwischen Analysen und Brief."""
    st.header("▶ Auto-Lauf")
    st.write(
        "Ein Klick startet **Schritt für Schritt** alles ab Project Brief "
        "bis zum gewählten Ziel: **Supplement-Funnel** oder **YouTube Publish**. "
        "Kapitel-Skripte zuerst komplett, danach "
        "Freitext-Nachbearbeitung, erst dann Script Lock. **⓪ Clean Media**, "
        "**① Analysen** und **SFX** bleiben manuell. Danach: Stocksuche (Wikimedia, Openverse, "
        "Archive.org) → alle offenen Gaps → (optional) Python Timing (Kapitel parallel) → "
        "ElevenLabs Music → OTIO → YouTube-Metadaten. Quiz bleibt manuell unter Final Output."
    )
    from otio_app.ui.project_context import render_project_selector

    project = render_project_selector()
    if project is None:
        return
    if not project.is_without_voiceover_enhanced:
        st.warning("Auto-Lauf gibt es nur für **Enhanced MVP**-Projekte.")
        return
    with st.container(border=True):
        render_enhanced_auto_run_embedded(project, key_scope="auto_page")


def render_enhanced_auto_run_page_panel(project_id: str) -> None:
    """Start-Button oben auf jeder Enhanced-Seite — nicht nur in der Sidebar."""
    project = get_project_by_id(str(project_id))
    if project is None or project.project_mode != ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
        return
    with st.container(border=True):
        render_enhanced_auto_run_embedded(project, key_scope="auto_panel")


def _render_running_auto_run_status(project_id: str, key_scope: str) -> None:
    """Nur der laufende Fortschritt — aktualisiert sich alle 2 Sekunden selbst."""
    manager = get_enhanced_auto_run_job_manager()

    def _body() -> None:
        state = manager.get_state(project_id)
        if state is None or state.status != JobStatus.RUNNING:
            return
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
            st.progress(auto_run_progress_fraction(state))
        with col_stop:
            if st.button(
                "⏹ Stoppen",
                key=f"global_stop_auto_run_{key_scope}_{project_id}",
                disabled=state.cancel_requested,
            ):
                manager.request_cancel(project_id)
                st.rerun()

    poll_while_running(
        _body,
        lambda: manager.is_running(project_id),
        refresh_key=f"auto_run_refresh_{key_scope}_{project_id}",
    )


def render_enhanced_auto_run_banner(project_id: str, *, key_scope: str = "page") -> None:
    """Fortschritt auf Enhanced-Seiten, inkl. Stop."""
    manager = get_enhanced_auto_run_job_manager()
    state = manager.get_state(project_id)
    if state is None:
        return

    if state.status == JobStatus.RUNNING:
        _render_running_auto_run_status(project_id, key_scope)
        return

    if state.status == JobStatus.CANCELLED:
        st.warning(state.message or "Auto-Lauf gestoppt — Teilergebnisse bleiben.")
    elif state.status == JobStatus.FAILED:
        loc = " · ".join(
            part for part in (state.step_label, state.item_label) if (part or "").strip()
        )
        detail = (state.error or "Unbekannt").strip()
        heading = (
            f"Auto-Lauf fehlgeschlagen — {loc}" if loc else "Auto-Lauf fehlgeschlagen"
        )
        st.error(f"{heading}\n\n{detail}")
    elif state.status == JobStatus.COMPLETED:
        skipped = f" · übersprungen: {len(state.skipped)}" if state.skipped else ""
        st.success(
            (state.message or "Auto-Lauf fertig.")
            + skipped
        )
    if st.button(
        "Hinweis schließen",
        key=f"auto_run_dismiss_{key_scope}_{project_id}",
    ):
        manager.dismiss(project_id)
        st.rerun()


def _render_auto_run_controls(project: Project, *, key_scope: str) -> None:
    manager = get_enhanced_auto_run_job_manager()
    state = manager.get_state(project.id)
    running = state is not None and state.status == JobStatus.RUNNING
    queue_running = get_language_auto_run_queue_manager().any_running()
    other_auto = (not running) and manager.any_running()
    other_running = (not running) and (
        any_job_running(project.id, reconcile=False) or queue_running or other_auto
    )

    if key_scope == "sidebar":
        st.markdown("**▶ Auto-Lauf**")
    else:
        st.subheader("▶ Auto-Lauf")
    st.caption(
        "Ein Button, **Schritt für Schritt**: Brief + Titel → Style → "
        "Dramaturgie (auto-bestätigen) → Kapitel-Skripte → Freitext-Nachbearbeitung "
        "→ Script Lock → Intro (erste gültige Variante) → TTS → Intro-Cut → "
        "alle Kapitel-Cuts → Stocksuche (Wikimedia/Openverse/Archive.org) → "
        "alle offenen Gaps. **bis Funnel** stoppt dort; **bis YouTube** macht "
        "weiter mit Python Timing (Kapitel parallel, max. 8) → ElevenLabs Music → "
        "OTIO-Export → YouTube-Metadaten. Quiz bleibt manuell unter Final Output. "
        "LLM-/TTS-Calls bleiben einzeln. Offene Gaps nach dem Funnel gelten als Fehler. "
        "Fertige Schritte werden übersprungen."
    )
    start_disabled = running or other_running
    help_text = None
    if running:
        help_text = "Auto-Lauf läuft bereits."
    elif other_running:
        help_text = (
            "Ein anderer Auto-Lauf oder die Sprachen-Queue läuft — zuerst stoppen."
        )
    col_funnel, col_youtube = st.columns(2)
    with col_funnel:
        if st.button(
            "▶ bis Funnel",
            key=f"enh_auto_run_start_funnel_{key_scope}_{project.id}",
            disabled=start_disabled,
            help=help_text or "Stoppt nach Stocksuche und Supplement-Funnel.",
            use_container_width=True,
        ):
            if manager.start(project, stop_after="funnel"):
                st.rerun()
            else:
                st.warning("Auto-Lauf läuft bereits.")
    with col_youtube:
        if st.button(
            "▶ bis YouTube",
            key=f"enh_auto_run_start_youtube_{key_scope}_{project.id}",
            type="primary",
            disabled=start_disabled,
            help=help_text or "Kompletter Auto-Lauf bis YouTube Publish.",
            use_container_width=True,
        ):
            if manager.start(project, stop_after="youtube"):
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
    if key_scope == "auto_page":
        _render_auto_run_status_overview(project)


def _render_auto_run_status_overview(project: Project) -> None:
    """✓/— je Auto-Lauf-Schritt, inkl. Stock, Funnel, Timing, Music, OTIO, YouTube."""
    st.markdown("**Statusübersicht**")
    rows = list_auto_run_step_statuses(project)
    if not rows:
        return
    chunk_size = 5
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        columns = st.columns(chunk_size)
        for column, item in zip(columns, chunk):
            with column:
                st.metric(item.short_label, "✓" if item.done else "—")
