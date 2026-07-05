"""Streamlit-UI: Clean Media vor der Analyse."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    CLEAN_STATUS_FAILED,
    CLEAN_STATUS_NEEDS_TRANSCODE,
    CLEAN_STATUS_OK,
    CLEAN_STATUS_PENDING,
    audit_folder_clean_media,
    count_folder_clean_status,
    find_clean_file_for_media,
    folder_manifest_path,
    load_clean_media_manifest,
    path_is_readable_file,
    selected_folders_have_clean_media,
)
from otio_app.services.clean_media_job import (
    CleanMediaJobMode,
    JobStatus,
    get_clean_media_job_manager,
    summarize_manifest,
)
from otio_app.system_checks import check_ffmpeg, check_ffprobe
from otio_app.ui.project_context import (
    render_file_paths,
    render_project_selector,
    render_workflow_progress,
)
from otio_app.ui.polling import running_job_fragment

_STATUS_LABELS = {
    CLEAN_STATUS_OK: "✅ Original OK",
    CLEAN_STATUS_CLEAN: "🔄 Transcodiert",
    CLEAN_STATUS_NEEDS_TRANSCODE: "⚠️ Transcode nötig",
    CLEAN_STATUS_FAILED: "❌ Fehler",
    CLEAN_STATUS_PENDING: "⬜ Offen",
}


def _folder_status_label(project, folder_name: str) -> str:
    """Schnelles Label für Multiselect — ohne ffmpeg pro Datei."""
    counts = count_folder_clean_status(project, folder_name)
    if counts[CLEAN_STATUS_FAILED] > 0:
        return f"❌ {folder_name}"
    if counts[CLEAN_STATUS_NEEDS_TRANSCODE] > 0 or counts[CLEAN_STATUS_PENDING] > 0:
        return f"⚠️ {folder_name}"
    if counts[CLEAN_STATUS_OK] + counts[CLEAN_STATUS_CLEAN] > 0:
        return f"✅ {folder_name}"
    return f"⬜ {folder_name}"


def _render_job_monitor(project) -> None:
    manager = get_clean_media_job_manager()
    manager.reconcile_stuck_job(project.id)
    state = manager.get_state(project.id)
    if state is None:
        return
    if state.status == JobStatus.COMPLETED:
        st.success("Clean-Media-Lauf abgeschlossen.")
        if st.button("Hinweis schließen", key=f"clean_dismiss_{project.id}"):
            manager.dismiss(project.id)
            st.rerun()
        return
    if state.status == JobStatus.CANCELLED:
        st.warning("Clean-Media-Lauf abgebrochen.")
        if st.button("Hinweis schließen", key=f"clean_dismiss_cancel_{project.id}"):
            manager.dismiss(project.id)
            st.rerun()
        return
    if state.status == JobStatus.FAILED:
        st.error(f"Clean-Media-Lauf fehlgeschlagen: {state.error or 'Unbekannter Fehler'}")
        if st.button("Hinweis schließen", key=f"clean_dismiss_fail_{project.id}"):
            manager.dismiss(project.id)
            st.rerun()
        return
    if state.status == JobStatus.RUNNING:
        _clean_media_running_panel(project)


@running_job_fragment()
def _clean_media_running_panel(project) -> None:
    manager = get_clean_media_job_manager()
    state = manager.get_state(project.id)
    if state is None or state.status != JobStatus.RUNNING:
        return

    total = max(state.total_media, 1)
    done = min(state.done_media, total)
    st.progress(done / total, text=f"{done} / {total} Medien")
    phase_data = state.phase_data
    if state.phase == "folder_start":
        st.caption(
            f"Ordner **{phase_data.get('folder', '…')}** "
            f"({phase_data.get('folder_index', '?')}/{phase_data.get('folder_count', '?')})"
        )
    elif state.phase == "media_done":
        st.caption(
            f"`{phase_data.get('media_name', '…')}` → "
            f"{_STATUS_LABELS.get(phase_data.get('status', ''), phase_data.get('status', ''))}"
        )
    if state.cancel_requested:
        st.caption("Abbruch angefordert …")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Abbrechen", key=f"clean_cancel_{project.id}"):
            manager.request_cancel(project.id)
            st.rerun()
    with col2:
        if st.button("Aktualisieren", key=f"clean_refresh_{project.id}"):
            st.rerun()
    with col3:
        if st.button("Job zurücksetzen", key=f"clean_reset_{project.id}"):
            manager.force_reset(project.id)
            st.rerun()


def _render_folder_details(project, folder_name: str) -> None:
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    if manifest is None:
        st.caption("Noch nicht geprüft.")
        return

    counts = summarize_manifest(manifest)
    st.caption(
        f"OK: {counts[CLEAN_STATUS_OK]} · "
        f"Transcodiert: {counts[CLEAN_STATUS_CLEAN]} · "
        f"Nötig: {counts[CLEAN_STATUS_NEEDS_TRANSCODE]} · "
        f"Fehler: {counts[CLEAN_STATUS_FAILED]}"
    )
    for entry in manifest.entries:
        label = _STATUS_LABELS.get(entry.status, entry.status)
        name = Path(entry.original_path).name
        line = f"{label} — `{name}`"
        if entry.probe and entry.probe.video_codec:
            line += f" ({entry.probe.video_codec})"
        if entry.status == CLEAN_STATUS_CLEAN:
            clean = find_clean_file_for_media(project, folder_name, Path(entry.original_path))
            if clean is None and entry.clean_path:
                clean = Path(entry.clean_path)
            if clean is not None:
                disk = "✓" if path_is_readable_file(clean) else "✗ offline"
                line += f" → `{clean.name}` [{disk}]"
        elif entry.status == CLEAN_STATUS_OK:
            disk = "✓" if path_is_readable_file(Path(entry.original_path)) else "✗ offline"
            line += f" [Original {disk}]"
        if entry.error:
            line += f" — {entry.error[:120]}"
        st.caption(line)

    diag_key = f"clean_diag_{project.id}_{folder_name}"
    if st.button("🔍 Tiefe Diagnose (ffmpeg)", key=diag_key):
        st.session_state[f"show_diag_{project.id}_{folder_name}"] = True
    if st.session_state.get(f"show_diag_{project.id}_{folder_name}"):
        with st.spinner("Prüfe Medien mit ffmpeg …"):
            issues = audit_folder_clean_media(project, folder_name, strict=True)
        if issues:
            st.warning("Probleme erkannt:")
            for issue in issues[:20]:
                st.caption(
                    f"• `{issue['media']}` — {issue['issue']} "
                    f"(`{Path(issue['resolved_path']).name}`)"
                )
        else:
            st.success("Alle Medien Resolve-ready.")


def render_clean_media_page() -> None:
    st.header("⓪ Clean Media")

    st.markdown(
        """
        **Erster Schritt vor der Analyse:** Medien werden lokal mit **ffprobe** und **ffmpeg**
        geprüft. Problematische Dateien (z. B. HEVC, ProRes, Decode-Fehler) werden nach
        `_otio/clean/<Ordner>/` als H.264/AAC-MP4 transcodiert — **Originale bleiben unberührt.**

        Analyse, Inventar und OTIO-Export verwenden danach automatisch die Clean-Pfade.
        """
    )

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="clean_media")

    ffmpeg_ok = check_ffmpeg().ok
    ffprobe_ok = check_ffprobe().ok
    if not ffmpeg_ok or not ffprobe_ok:
        st.error("FFmpeg und ffprobe werden für Clean Media benötigt — siehe **Systemstatus**.")
        return

    job_running = get_clean_media_job_manager().is_running(project.id)
    _render_job_monitor(project)

    folder_state_key = f"clean_folders_{project.id}"
    if folder_state_key not in st.session_state:
        st.session_state[folder_state_key] = list(project.selected_asset_subdirs)

    selected_folders = st.multiselect(
        "Asset-Ordner",
        options=project.asset_subdir_names,
        format_func=lambda name: _folder_status_label(project, name),
        key=folder_state_key,
    )
    st.caption(f"{len(selected_folders)} von {len(project.asset_subdir_names)} Ordnern ausgewählt")

    clean_done = selected_folders_have_clean_media(project)
    if clean_done and selected_folders:
        st.success("Ausgewählte Ordner sind clean-media-bereit — du kannst mit **① Analysen** weitermachen.")
    elif selected_folders:
        st.info("Mindestens ein Ordner braucht noch Prüfung oder Transcode.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        validate_clicked = st.button(
            "🔍 Nur prüfen",
            key=f"clean_validate_{project.id}",
            disabled=job_running or not selected_folders,
        )
    with col2:
        process_clicked = st.button(
            "🔄 Prüfen & transcodieren",
            key=f"clean_process_{project.id}",
            type="primary",
            disabled=job_running or not selected_folders,
        )
    with col3:
        repair_clicked = st.button(
            "🔧 Reparieren",
            key=f"clean_repair_{project.id}",
            disabled=job_running or not selected_folders,
            help="Fehlende oder ungültige Clean-Dateien erneut erzeugen und Manifest synchronisieren",
        )
    with col4:
        all_clicked = st.button(
            "Alle Ordner verarbeiten",
            key=f"clean_all_{project.id}",
            disabled=job_running,
        )

    manager = get_clean_media_job_manager()
    if validate_clicked and selected_folders:
        if manager.start(project, selected_folders, mode=CleanMediaJobMode.VALIDATE):
            st.rerun()
        else:
            st.warning("Clean-Media-Job läuft bereits.")
    if process_clicked and selected_folders:
        if manager.start(project, selected_folders, mode=CleanMediaJobMode.PROCESS):
            st.rerun()
        else:
            st.warning("Clean-Media-Job läuft bereits.")
    if repair_clicked and selected_folders:
        if manager.start(project, selected_folders, mode=CleanMediaJobMode.PROCESS):
            st.info("Reparatur-Lauf gestartet — fehlende/ungültige Clean-Dateien werden neu erzeugt.")
            st.rerun()
        else:
            st.warning("Clean-Media-Job läuft bereits.")
    if all_clicked:
        all_folders = list(project.asset_subdir_names)
        st.session_state[folder_state_key] = all_folders
        if manager.start(project, all_folders, mode=CleanMediaJobMode.PROCESS):
            st.rerun()
        else:
            st.warning("Clean-Media-Job läuft bereits.")

    st.divider()
    st.subheader("Status je Ordner")
    for folder_name in project.asset_subdir_names:
        with st.expander(_folder_status_label(project, folder_name), expanded=False):
            manifest_path = folder_manifest_path(project, folder_name)
            st.caption(f"Manifest: `{manifest_path}`")
            clean_dir = project.work_dir_path / "clean" / folder_name.replace(" ", "_")
            st.caption(f"Clean-Ausgabe: `{clean_dir}`")
            _render_folder_details(project, folder_name)

    render_file_paths(project)
