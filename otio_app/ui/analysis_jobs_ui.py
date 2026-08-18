"""UI für laufende Analyse-Jobs (Voice-over + Assets)."""

from __future__ import annotations

import streamlit as st

from otio_app.services.analysis_log import read_analysis_log_tail
from otio_app.services.analysis_progress import VoiceAnalysisRunReport
from otio_app.services.asset_analysis_job import (
    AssetAnalysisJobState,
    JobStatus,
    get_asset_analysis_job_manager,
)
from otio_app.services.clean_media_job import (
    JobStatus as CleanJobStatus,
    get_clean_media_job_manager,
)
from otio_app.services.supplement_recovery_job import (
    RecoveryJobStatus,
    SupplementRecoveryJobState,
    get_supplement_recovery_job_manager,
)
from otio_app.services.voice_analysis_job import (
    VoiceAnalysisJobState,
    get_voice_analysis_job_manager,
)
from otio_app.ui.analysis_report import render_analysis_report
from otio_app.ui.polling import poll_while_running


def _format_asset_progress_line(state: AssetAnalysisJobState) -> str:
    phase = state.phase
    data = state.phase_data
    if phase == "media_start":
        return (
            f"Asset `{data.get('media_name', '…')}` "
            f"({data.get('media_index', '?')}/{data.get('media_count', '?')} "
            f"in {data.get('folder', '…')})"
        )
    if phase == "folder_start":
        return (
            f"Ordner **{data.get('folder', '…')}** "
            f"({data.get('folder_index', '?')}/{data.get('folder_count', '?')})"
        )
    if phase == "media_done":
        return f"Asset fertig: `{data.get('media_name', '…')}`"
    return "Asset-Analyse läuft …"


def _format_voice_progress_line(state: VoiceAnalysisJobState) -> str:
    phase = state.phase
    data = state.phase_data
    if phase == "file_start":
        return (
            f"Voice `{data.get('file_name', '…')}` "
            f"({data.get('file_index', '?')}/{data.get('file_count', '?')})"
        )
    if phase == "file_done":
        return f"Voice fertig: `{data.get('file_name', '…')}`"
    return "Voice-over-Analyse läuft …"


def _render_voice_report(report: VoiceAnalysisRunReport) -> None:
    st.markdown("**Voice-over-Bericht**")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Neu transkribiert", report.files_analyzed)
    with col2:
        st.metric("Aus Cache", report.files_cached)
    with col3:
        st.metric("Fehler", report.files_failed)
    if report.cancelled:
        st.warning("Voice-over-Analyse wurde vorzeitig gestoppt.")
    if report.output_written:
        st.caption("Teilergebnis in voice_over_analysis.json gespeichert.")
    if report.failures:
        for line in report.failures[:10]:
            st.caption(f"• {line}")


def _render_running_voice_job(state: VoiceAnalysisJobState, *, stop_key: str) -> None:
    total = max(state.total_files, 1)
    done = min(state.done_files, total)
    st.progress(done / total, text=f"{done} / {total} Audiodateien")
    st.caption(_format_voice_progress_line(state))
    if state.cancel_requested:
        st.warning(
            "Stop angefordert — aktuelle Whisper-/Gemini-Transkription wird noch beendet."
        )
    if st.button("⏹ Voice-over stoppen", key=stop_key, disabled=state.cancel_requested):
        get_voice_analysis_job_manager().request_cancel(state.project_id)
        st.rerun()


def _render_running_asset_job(state: AssetAnalysisJobState, *, stop_key: str) -> None:
    total = max(state.total_media, 1)
    done = min(state.done_media, total)
    st.progress(done / total, text=f"{done} / {total} Assets")
    st.caption(_format_asset_progress_line(state))
    if state.cancel_requested:
        st.warning(
            "Stop angefordert — aktueller FFmpeg-/Gemini-Schritt wird noch beendet."
        )
    if st.button("⏹ Asset-Analyse stoppen", key=stop_key, disabled=state.cancel_requested):
        get_asset_analysis_job_manager().request_cancel(state.project_id)
        st.rerun()


def _format_recovery_progress_line(state: SupplementRecoveryJobState) -> str:
    if state.current_media:
        position = f"{min(state.done + 1, max(state.total, 1))}/{state.total or '?'}"
        folder = f" → {state.current_folder}" if state.current_folder else ""
        return f"Beschafftes Asset `{state.current_media}`{folder} ({position})"
    return "Bestandsaufnahme läuft …"


def _render_running_recovery_job(
    state: SupplementRecoveryJobState,
    *,
    stop_key: str,
) -> None:
    st.progress(
        state.fraction,
        text=f"{state.done} / {state.total or '?'} beschaffte Assets",
    )
    st.caption(_format_recovery_progress_line(state))
    if state.cancel_requested:
        st.warning(
            "Stop angefordert — das laufende Asset wird noch beendet. "
            "Bereits nachgetragene Assets bleiben im Inventar."
        )
    if st.button(
        "⏹ Bestandsaufnahme stoppen",
        key=stop_key,
        disabled=state.cancel_requested,
    ):
        get_supplement_recovery_job_manager().request_cancel(state.project_id)
        st.rerun()


def _render_recovery_report(state: SupplementRecoveryJobState) -> None:
    report = state.report
    if report is None:
        return
    st.markdown("**Bestandsaufnahme beschaffter Assets**")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Nachgetragen", report.recovered)
    with col2:
        st.metric("Neu analysiert", report.analyzed)
    with col3:
        st.metric("Fehler", report.failed)
    if report.already_complete:
        st.caption(f"{report.already_complete} Asset(s) kamen aus dem Cache.")
    if report.recovered_by_folder:
        details = ", ".join(
            f"{folder}: {count}"
            for folder, count in sorted(report.recovered_by_folder.items())
        )
        st.caption(f"Pro Ordner — {details}")
    for line in report.failures[:10]:
        st.caption(f"• {line}")


def _format_clean_progress_line(state) -> str:
    folder = (state.phase_data or {}).get("folder") or ""
    extra = f" · {folder}" if folder else ""
    return f"{state.done_media}/{state.total_media or '?'} Medien{extra}"


def _banner_fraction(done: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return min(max(done / total, 0.0), 1.0)


def _banner_skip(skip_kinds: tuple[str, ...] | list[str] | set[str]) -> set[str]:
    return {str(kind).strip().lower() for kind in skip_kinds if str(kind).strip()}


def _banner_job_running(project_id: str, skip: set[str]) -> bool:
    if "clean" not in skip and get_clean_media_job_manager().is_running(project_id):
        return True
    if "voice" not in skip and get_voice_analysis_job_manager().is_running(project_id):
        return True
    if "assets" not in skip and get_asset_analysis_job_manager().is_running(project_id):
        return True
    if "recovery" not in skip and get_supplement_recovery_job_manager().is_running(
        project_id
    ):
        return True
    return False


def _render_banner_stop_row(
    *,
    message: str,
    stop_key: str,
    cancel_requested: bool,
    on_stop,
    progress: float | None = None,
    progress_text: str = "",
) -> None:
    extra = " **(Stop angefordert …)**" if cancel_requested else ""
    col_info, col_stop = st.columns([5, 1])
    with col_info:
        st.info(f"{message}{extra}")
        if progress is not None:
            fraction = min(max(float(progress), 0.0), 1.0)
            if progress_text:
                st.progress(fraction, text=progress_text)
            else:
                st.progress(fraction)
    with col_stop:
        if st.button("⏹ Stoppen", key=stop_key, disabled=cancel_requested):
            on_stop()
            st.rerun()


def _render_running_jobs_banner(project_id: str, skip: set[str]) -> None:
    if "clean" not in skip:
        clean_state = get_clean_media_job_manager().get_state(project_id)
        if clean_state is not None and clean_state.status == CleanJobStatus.RUNNING:
            _render_banner_stop_row(
                message=(
                    f"🧹 Clean Media läuft — {_format_clean_progress_line(clean_state)}"
                ),
                stop_key=f"global_stop_clean_{project_id}",
                cancel_requested=clean_state.cancel_requested,
                on_stop=lambda: get_clean_media_job_manager().request_cancel(project_id),
                progress=_banner_fraction(clean_state.done_media, clean_state.total_media),
                progress_text=(
                    f"{clean_state.done_media} / {clean_state.total_media or '?'} Medien"
                ),
            )
    if "voice" not in skip:
        voice_state = get_voice_analysis_job_manager().get_state(project_id)
        if voice_state is not None and voice_state.status == JobStatus.RUNNING:
            _render_banner_stop_row(
                message=f"🎙️ Voice-over läuft — {_format_voice_progress_line(voice_state)}",
                stop_key=f"global_stop_voice_{project_id}",
                cancel_requested=voice_state.cancel_requested,
                on_stop=lambda: get_voice_analysis_job_manager().request_cancel(
                    project_id
                ),
                progress=_banner_fraction(voice_state.done_files, voice_state.total_files),
                progress_text=(
                    f"{voice_state.done_files} / {voice_state.total_files or '?'} Audiodateien"
                ),
            )
    if "assets" not in skip:
        asset_state = get_asset_analysis_job_manager().get_state(project_id)
        if asset_state is not None and asset_state.status == JobStatus.RUNNING:
            _render_banner_stop_row(
                message=(
                    f"⏳ Asset-Analyse läuft — {_format_asset_progress_line(asset_state)} "
                    "Du kannst die Seite wechseln."
                ),
                stop_key=f"global_stop_assets_{project_id}",
                cancel_requested=asset_state.cancel_requested,
                on_stop=lambda: get_asset_analysis_job_manager().request_cancel(
                    project_id
                ),
                progress=_banner_fraction(asset_state.done_media, asset_state.total_media),
                progress_text=(
                    f"{asset_state.done_media} / {asset_state.total_media or '?'} Assets"
                ),
            )
    if "recovery" not in skip:
        recovery_state = get_supplement_recovery_job_manager().get_state(project_id)
        if (
            recovery_state is not None
            and recovery_state.status == RecoveryJobStatus.RUNNING
        ):
            _render_banner_stop_row(
                message=(
                    f"📥 Bestandsaufnahme läuft — {recovery_state.done}/"
                    f"{recovery_state.total or '?'} beschaffte Assets"
                ),
                stop_key=f"global_stop_recovery_{project_id}",
                cancel_requested=recovery_state.cancel_requested,
                on_stop=lambda: get_supplement_recovery_job_manager().request_cancel(
                    project_id
                ),
                progress=recovery_state.fraction,
                progress_text=(
                    f"{recovery_state.done} / {recovery_state.total or '?'} beschaffte Assets"
                ),
            )


def render_analysis_jobs_banner(
    project_id: str,
    *,
    skip_kinds: tuple[str, ...] | list[str] | set[str] = (),
) -> None:
    """Globales Banner — Clean Media, Voice, Assets, Bestandsaufnahme."""
    skip = _banner_skip(skip_kinds)
    skip_key = "_".join(sorted(skip)) or "all"
    if _banner_job_running(project_id, skip):
        poll_while_running(
            lambda: _render_running_jobs_banner(project_id, skip),
            lambda: _banner_job_running(project_id, skip),
            refresh_key=f"jobs_banner_refresh_{project_id}_{skip_key}",
        )
        return

    if "clean" not in skip:
        clean_state = get_clean_media_job_manager().get_state(project_id)
        if clean_state is not None and clean_state.status != CleanJobStatus.RUNNING:
            if clean_state.status == CleanJobStatus.CANCELLED:
                st.warning("Clean-Media-Lauf wurde abgebrochen.")
            elif clean_state.status == CleanJobStatus.FAILED:
                st.error(
                    f"Clean-Media-Lauf fehlgeschlagen: {clean_state.error or 'Unbekannt'}"
                )
            elif clean_state.status == CleanJobStatus.COMPLETED:
                st.success("Clean-Media-Lauf abgeschlossen.")
            if st.button(
                "Clean-Media-Hinweis schließen",
                key=f"dismiss_clean_job_{project_id}",
            ):
                get_clean_media_job_manager().dismiss(project_id)
                st.rerun()
            return

    voice_state = get_voice_analysis_job_manager().get_state(project_id)
    asset_state = get_asset_analysis_job_manager().get_state(project_id)

    if "voice" not in skip and voice_state is not None and voice_state.status != JobStatus.RUNNING:
        if voice_state.status == JobStatus.CANCELLED:
            st.warning("Voice-over-Analyse wurde gestoppt.")
        elif voice_state.status == JobStatus.FAILED:
            st.error(f"Voice-over fehlgeschlagen: {voice_state.error or 'Unbekannt'}")
        elif voice_state.status == JobStatus.COMPLETED:
            st.success("Voice-over-Analyse abgeschlossen.")
        if st.button("Voice-Hinweis schließen", key=f"dismiss_voice_job_{project_id}"):
            get_voice_analysis_job_manager().dismiss(project_id)
            st.rerun()
        return

    if "assets" not in skip and asset_state is not None and asset_state.status != JobStatus.RUNNING:
        if asset_state.status == JobStatus.CANCELLED:
            st.warning("Asset-Analyse wurde gestoppt.")
        elif asset_state.status == JobStatus.FAILED:
            st.error(f"Asset-Analyse fehlgeschlagen: {asset_state.error or 'Unbekannt'}")
        elif asset_state.status == JobStatus.COMPLETED:
            st.success("Asset-Analyse abgeschlossen.")
        if st.button("Asset-Hinweis schließen", key=f"dismiss_asset_job_{project_id}"):
            get_asset_analysis_job_manager().dismiss(project_id)
            st.rerun()
        return

    if "recovery" not in skip:
        recovery_state = get_supplement_recovery_job_manager().get_state(project_id)
        if (
            recovery_state is not None
            and recovery_state.status != RecoveryJobStatus.RUNNING
        ):
            if recovery_state.status == RecoveryJobStatus.CANCELLED:
                st.warning("Bestandsaufnahme wurde gestoppt.")
            elif recovery_state.status == RecoveryJobStatus.FAILED:
                st.error(
                    f"Bestandsaufnahme fehlgeschlagen: {recovery_state.error or 'Unbekannt'}"
                )
            elif recovery_state.status == RecoveryJobStatus.COMPLETED:
                st.success("Bestandsaufnahme abgeschlossen.")
            if st.button(
                "Bestands-Hinweis schließen",
                key=f"dismiss_recovery_job_{project_id}",
            ):
                get_supplement_recovery_job_manager().dismiss(project_id)
                st.rerun()


def _analysis_job_is_running(project_id: str) -> bool:
    return (
        get_voice_analysis_job_manager().is_running(project_id)
        or get_asset_analysis_job_manager().is_running(project_id)
        or get_supplement_recovery_job_manager().is_running(project_id)
    )


def render_analysis_jobs_monitor(project, *, expanded: bool = True) -> None:
    """Fortschritt Voice + Assets auf der Analyse-Seite."""
    voice_manager = get_voice_analysis_job_manager()
    asset_manager = get_asset_analysis_job_manager()
    recovery_manager = get_supplement_recovery_job_manager()
    voice_state = voice_manager.get_state(project.id)
    asset_state = asset_manager.get_state(project.id)
    recovery_state = recovery_manager.get_state(project.id)

    any_running = (
        (voice_state is not None and voice_state.status == JobStatus.RUNNING)
        or (asset_state is not None and asset_state.status == JobStatus.RUNNING)
        or (
            recovery_state is not None
            and recovery_state.status == RecoveryJobStatus.RUNNING
        )
    )

    if any_running:
        poll_while_running(
            lambda: _render_analysis_jobs_running_panel(project),
            lambda: _analysis_job_is_running(project.id),
            refresh_key=f"analysis_refresh_poll_{project.id}",
        )
        return

    if voice_state is not None and expanded:
        if voice_state.status == JobStatus.CANCELLED:
            st.warning("Voice-over gestoppt — Teilergebnisse gespeichert.")
        elif voice_state.status == JobStatus.FAILED:
            st.error(f"Voice-over fehlgeschlagen: {voice_state.error or 'Unbekannt'}")
        elif voice_state.status == JobStatus.COMPLETED:
            st.success("Voice-over-Analyse abgeschlossen.")
        if voice_state.report is not None:
            _render_voice_report(voice_state.report)
        if st.button("Voice-Bericht schließen", key=f"dismiss_voice_report_{project.id}"):
            voice_manager.dismiss(project.id)
            st.rerun()

    if asset_state is not None and expanded:
        if asset_state.status == JobStatus.CANCELLED:
            st.warning("Asset-Analyse gestoppt — Teilergebnisse gespeichert.")
        elif asset_state.status == JobStatus.FAILED:
            st.error(f"Asset-Analyse fehlgeschlagen: {asset_state.error or 'Unbekannter Fehler'}")
        elif asset_state.status == JobStatus.COMPLETED:
            st.success("Asset-Analyse abgeschlossen.")
        if asset_state.report is not None:
            render_analysis_report(asset_state.report)
        log_tail = read_analysis_log_tail(project)
        if log_tail:
            failures = asset_state.report.failures if asset_state.report else []
            with st.expander("Analyse-Protokoll (Details)", expanded=bool(failures)):
                st.code(log_tail)
        if st.button("Asset-Bericht schließen", key=f"dismiss_asset_report_{project.id}"):
            asset_manager.dismiss(project.id)
            st.rerun()

    if recovery_state is not None and expanded:
        if recovery_state.status == RecoveryJobStatus.CANCELLED:
            st.warning(
                f"Bestandsaufnahme gestoppt nach {recovery_state.done} von "
                f"{recovery_state.total} Assets — die erledigten sind im Inventar, "
                "ein neuer Lauf macht dort weiter."
            )
        elif recovery_state.status == RecoveryJobStatus.FAILED:
            st.error(
                f"Bestandsaufnahme fehlgeschlagen: {recovery_state.error or 'Unbekannt'}"
            )
        elif recovery_state.status == RecoveryJobStatus.COMPLETED:
            st.success("Bestandsaufnahme abgeschlossen.")
        _render_recovery_report(recovery_state)
        if st.button(
            "Bestands-Bericht schließen",
            key=f"dismiss_recovery_report_{project.id}",
        ):
            recovery_manager.dismiss(project.id)
            st.rerun()


def _render_analysis_jobs_running_panel(project) -> None:
    voice_state = get_voice_analysis_job_manager().get_state(project.id)
    asset_state = get_asset_analysis_job_manager().get_state(project.id)
    recovery_state = get_supplement_recovery_job_manager().get_state(project.id)

    if voice_state is not None and voice_state.status == JobStatus.RUNNING:
        st.markdown("**🎙️ Voice-over-Analyse**")
        _render_running_voice_job(voice_state, stop_key=f"stop_voice_{project.id}")

    if asset_state is not None and asset_state.status == JobStatus.RUNNING:
        st.markdown("**📁 Asset-Analyse**")
        _render_running_asset_job(asset_state, stop_key=f"stop_assets_{project.id}")

    if (
        recovery_state is not None
        and recovery_state.status == RecoveryJobStatus.RUNNING
    ):
        st.markdown("**📥 Bestandsaufnahme beschaffter Assets**")
        _render_running_recovery_job(
            recovery_state, stop_key=f"stop_recovery_{project.id}"
        )


# Abwärtskompatibilität
render_asset_analysis_job_monitor = render_analysis_jobs_monitor
render_asset_analysis_job_banner = render_analysis_jobs_banner
