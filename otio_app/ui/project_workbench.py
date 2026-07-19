"""Streamlit-UI: Projekt analysieren (Voice-over + Assets)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.config import get_voice_backend_from_env
from otio_app.defaults import (
    GEMINI_MODEL_CHOICES,
    VOICE_BACKEND_CHOICES,
    VOICE_BACKEND_GEMINI,
    VOICE_BACKEND_LABELS,
    VOICE_BACKEND_WHISPER,
    WHISPER_MODEL_CHOICES,
    WHISPER_MODEL_LABELS,
)
from otio_app.models import ProjectStatus
from otio_app.project_repository import (
    update_project_selection,
    update_project_status,
)
from otio_app.services.asset_analysis_job import get_asset_analysis_job_manager
from otio_app.services.voice_analysis_job import get_voice_analysis_job_manager
from otio_app.services.gemini_client import (
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.inventory_loader import (
    probe_folder_inventory_statuses,
    sync_folder_inventories_from_cache,
)
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    list_assets_missing_successful_cache,
)
from otio_app.services.whisper_transcriber import (
    get_default_whisper_model,
    is_whisper_available,
)
from otio_app.services.folder_asset_status import (
    AssetAnalysisState,
    list_missing_or_failed_assets,
)
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    count_folder_states,
    format_folder_with_status,
    get_folder_analysis_state,
)
from otio_app.services.manual_folder_completion import is_manually_complete, set_manually_complete
from otio_app.ui.analysis_jobs_ui import render_analysis_jobs_monitor
from otio_app.ui.project_context import (
    get_workflow_status,
    render_file_paths,
    render_output_status,
    render_project_selector,
    render_workflow_progress,
)


def _folder_status_cache_keys(project_id: str) -> tuple[str, str]:
    return (
        f"wb_folder_status_cache_{project_id}",
        f"wb_folder_status_fp_{project_id}",
    )


def _invalidate_folder_status_cache(project_id: str) -> None:
    cache_key, fp_key = _folder_status_cache_keys(project_id)
    st.session_state.pop(cache_key, None)
    st.session_state.pop(fp_key, None)


def _folder_status_fingerprint(project) -> str:
    asset_job = get_asset_analysis_job_manager().get_state(project.id)
    if asset_job is None:
        asset_fp = "none"
    else:
        asset_fp = (
            f"{asset_job.status.value}:{asset_job.done_media}:"
            f"{asset_job.phase}:{asset_job.phase_data.get('folder', '')}"
        )
    return (
        f"{asset_fp}|{len(project.asset_subdir_names)}|"
        f"{project.inventory_dir.is_dir()}"
    )


def _get_folder_status_cache(project) -> dict[str, FolderAnalysisState]:
    """Session-Cache für Ordner-Status — vermeidet Media/Cache-Scans bei jedem Klick."""
    cache_key, fp_key = _folder_status_cache_keys(project.id)
    fingerprint = _folder_status_fingerprint(project)
    cached = st.session_state.get(cache_key)
    if cached is not None and st.session_state.get(fp_key) == fingerprint:
        # Neue Ordner nachziehen, ohne alles neu zu scannen.
        missing = [name for name in project.asset_subdir_names if name not in cached]
        if not missing:
            return cached
        for name in missing:
            cached[name] = get_folder_analysis_state(project, name)
        st.session_state[cache_key] = cached
        return cached

    states = {
        name: get_folder_analysis_state(project, name)
        for name in project.asset_subdir_names
    }
    st.session_state[cache_key] = states
    st.session_state[fp_key] = fingerprint
    return states


def _start_voice_analysis_background(
    project,
    *,
    backend: str,
    whisper_model: str,
    gemini_model: str,
    chain_asset_folders: list[str] | None = None,
    chain_asset_model: str = "",
) -> bool:
    manager = get_voice_analysis_job_manager()
    if manager.is_running(project.id):
        st.warning("Voice-over-Analyse läuft bereits — bitte warten oder stoppen.")
        return False
    if not manager.start(
        project,
        backend=backend,
        whisper_model=whisper_model,
        gemini_model=gemini_model,
        chain_asset_folders=chain_asset_folders,
        chain_asset_model=chain_asset_model,
    ):
        st.warning("Voice-over-Analyse konnte nicht gestartet werden.")
        return False
    update_project_status(project.id, ProjectStatus.ANALYZING)
    return True


def _start_asset_analysis_background(project, folders: list[str], model: str) -> bool:
    """Startet Asset-Analyse im Hintergrund — UI bleibt bedienbar."""
    manager = get_asset_analysis_job_manager()
    if manager.is_running(project.id):
        st.warning("Asset-Analyse läuft bereits — bitte warten oder stoppen.")
        return False
    if not manager.start(project, folders, model):
        st.warning("Asset-Analyse konnte nicht gestartet werden.")
        return False
    update_project_status(project.id, ProjectStatus.ANALYZING)
    return True


def _render_folder_picker(project) -> list[str]:
    """Multiselect und Schnellauswahl — oben in der Analyse-Ansicht."""
    folder_state_key = f"workbench_folders_{project.id}"
    if folder_state_key not in st.session_state:
        st.session_state[folder_state_key] = list(project.selected_asset_subdirs)

    status_cache = _get_folder_status_cache(project)
    label_cache = {
        name: format_folder_with_status(project, name, state=state)
        for name, state in status_cache.items()
    }

    selected_folders = st.multiselect(
        "Zu bearbeitende Asset-Ordner",
        options=project.asset_subdir_names,
        format_func=lambda name: label_cache.get(name, name),
        key=folder_state_key,
    )
    st.caption(
        f"{len(selected_folders)} von {len(project.asset_subdir_names)} Ordnern ausgewählt"
    )

    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
    with btn_col1:
        if st.button("Alle Ordner auswählen", key=f"all_{project.id}"):
            st.session_state[folder_state_key] = list(project.asset_subdir_names)
            st.rerun()
    with btn_col2:
        if st.button("Nur offene Ordner", key=f"open_{project.id}"):
            open_names = [
                name
                for name in project.asset_subdir_names
                if status_cache.get(name)
                in {FolderAnalysisState.PENDING, FolderAnalysisState.PARTIAL}
            ]
            st.session_state[folder_state_key] = open_names
            st.rerun()
    with btn_col3:
        if st.button("Gespeicherte Auswahl", key=f"reload_{project.id}"):
            st.session_state[folder_state_key] = list(project.selected_asset_subdirs)
            st.rerun()
    with btn_col4:
        if st.button("Auswahl speichern", key=f"save_sel_{project.id}"):
            update_project_selection(project.id, selected_folders)
            st.success("Ordnerauswahl gespeichert.")
            st.rerun()

    return selected_folders


def _render_folder_status_overview(project) -> None:
    """Statusübersicht aller Asset-Ordner (Tab „Ordner“)."""
    status_cache = _get_folder_status_cache(project)
    counts = count_folder_states(
        project, project.asset_subdir_names, states=status_cache
    )
    st.caption(
        f"🟢 {counts[FolderAnalysisState.COMPLETE]} fertig · "
        f"🟡 {counts[FolderAnalysisState.PARTIAL]} teilweise · "
        f"⚪ {counts[FolderAnalysisState.PENDING]} offen · "
        f"➖ {counts[FolderAnalysisState.EMPTY]} leer"
    )
    if st.button("Status aktualisieren", key=f"refresh_folder_status_{project.id}"):
        _invalidate_folder_status_cache(project.id)
        st.rerun()

    for folder_name in project.asset_subdir_names:
        state = status_cache.get(
            folder_name, get_folder_analysis_state(project, folder_name)
        )
        label = format_folder_with_status(project, folder_name, state=state)
        col_status, col_action = st.columns([5, 1])
        with col_status:
            if state == FolderAnalysisState.COMPLETE:
                st.success(label)
            elif state == FolderAnalysisState.PARTIAL:
                st.warning(label)
                # Checkbox statt Expander: Inhalt nur bei aktivem Haken ausführen.
                if st.checkbox(
                    f"Details · {folder_name}",
                    key=f"gaps_{project.id}_{folder_name}",
                    value=False,
                ):
                    gaps = list_missing_or_failed_assets(project, folder_name)
                    if not gaps:
                        st.caption("Keine offenen Assets.")
                    for gap in gaps:
                        if gap.state == AssetAnalysisState.MISSING:
                            st.caption(f"⚪ `{gap.path.name}` — noch nicht analysiert")
                        else:
                            st.caption(
                                f"❌ `{gap.path.name}` — {gap.error or 'Fehler ohne Details'}"
                            )
            elif state == FolderAnalysisState.EMPTY:
                st.caption(label)
            else:
                st.info(label)
        with col_action:
            if state == FolderAnalysisState.COMPLETE and not is_manually_complete(
                project, folder_name
            ):
                st.caption("✓")
            elif is_manually_complete(project, folder_name):
                if st.button(
                    "↩",
                    key=f"unmanual_{project.id}_{folder_name}",
                    help="Manuelle Markierung aufheben",
                ):
                    set_manually_complete(project, folder_name, complete=False)
                    _invalidate_folder_status_cache(project.id)
                    st.rerun()
            elif state in {FolderAnalysisState.PARTIAL, FolderAnalysisState.PENDING}:
                if st.button(
                    "✓",
                    key=f"manual_{project.id}_{folder_name}",
                    help="Manuell als fertig markieren",
                ):
                    set_manually_complete(project, folder_name, complete=True)
                    _invalidate_folder_status_cache(project.id)
                    st.rerun()

    st.caption("Rechts: ✓ = manuell als fertig markieren · ↩ = Markierung aufheben")


def _init_model_settings() -> tuple[str, str, str]:
    default_voice_backend = get_voice_backend_from_env()
    if "voice_backend" not in st.session_state:
        st.session_state["voice_backend"] = default_voice_backend
    if st.session_state["voice_backend"] not in VOICE_BACKEND_CHOICES:
        st.session_state["voice_backend"] = default_voice_backend

    default_whisper_model = get_default_whisper_model()
    if "whisper_model" not in st.session_state:
        st.session_state["whisper_model"] = default_whisper_model
    if st.session_state["whisper_model"] not in WHISPER_MODEL_CHOICES:
        st.session_state["whisper_model"] = default_whisper_model

    default_model = get_default_gemini_model()
    if "gemini_model" not in st.session_state:
        st.session_state["gemini_model"] = default_model
    if st.session_state["gemini_model"] not in GEMINI_MODEL_CHOICES:
        st.session_state["gemini_model"] = default_model

    return (
        st.session_state["voice_backend"],
        st.session_state["whisper_model"],
        st.session_state["gemini_model"],
    )


def _render_analysis_actions(
    project,
    selected_folders: list[str],
    selected_voice_backend: str,
    selected_whisper_model: str,
    selected_model: str,
) -> None:
    folder_state_key = f"workbench_folders_{project.id}"
    asset_job_running = get_asset_analysis_job_manager().is_running(project.id)
    voice_job_running = get_voice_analysis_job_manager().is_running(project.id)
    any_job_running = asset_job_running or voice_job_running
    without_voiceover = bool(
        getattr(project, "is_without_voiceover_pipeline", False)
        or getattr(project, "is_without_voiceover", False)
    )

    if not without_voiceover:
        st.markdown("**Voice-over** — lokal mit Whisper (Standard) oder optional Gemini.")
        if voice_job_running:
            st.caption("Voice-over-Analyse läuft im Hintergrund — Fortschritt siehe oben.")
        if st.button(
            "🎙️ Voice-over analysieren",
            key=f"voice_{project.id}",
            type="primary",
            disabled=any_job_running,
        ):
            if selected_voice_backend == VOICE_BACKEND_GEMINI and not is_gemini_configured():
                st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
            elif selected_voice_backend == VOICE_BACKEND_WHISPER and not is_whisper_available():
                st.error("Whisper nicht installiert — `pip install -r requirements.txt`.")
            else:
                if _start_voice_analysis_background(
                    project,
                    backend=selected_voice_backend,
                    whisper_model=selected_whisper_model,
                    gemini_model=selected_model,
                ):
                    st.rerun()

        st.divider()

    st.markdown("**Asset-Ordner** — Gemini analysiert nur Frame-Bilder (kostenpflichtig).")
    if without_voiceover:
        st.caption(
            "Ohne Voice-Over: Hauptassets hier analysieren. Fehlende Cut-Plan-/"
            "`_supplemental/`-Supplements über den Button darunter ins Inventory holen."
        )
    if asset_job_running:
        st.caption(
            "Asset-Analyse läuft im Hintergrund — Fortschritt siehe oben. "
            "Du kannst zu **③ Schnittplan** wechseln."
        )
    if selected_folders and st.checkbox(
        "Fehlende Analysen je Ordner anzeigen",
        key=f"show_missing_assets_{project.id}",
        value=False,
    ):
        for folder_name in selected_folders:
            missing = list_assets_missing_successful_cache(project, folder_name)
            total = len(discover_folder_media_paths(project, folder_name))
            if missing:
                labels = ", ".join(f"`{path.name}`" for path in missing[:8])
                suffix = " …" if len(missing) > 8 else ""
                st.warning(
                    f"**{folder_name}:** {len(missing)} von {total} Assets ohne Analyse-JSON "
                    f"({labels}{suffix})"
                )
            else:
                st.caption(f"**{folder_name}:** alle Assets analysiert ({total})")
    if st.button(
        "📁 Ausgewählte Ordner analysieren",
        key=f"assets_{project.id}",
        disabled=any_job_running,
    ):
        if not selected_folders:
            st.warning("Bitte mindestens einen Ordner unter „Ordner“ auswählen.")
        elif not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
        else:
            folders = list(selected_folders)
            update_project_selection(project.id, folders)
            if _start_asset_analysis_background(project, folders, selected_model):
                st.rerun()

    from otio_app.services.cut_plan_inventory_bridge import (
        analyze_and_import_missing_supplement_assets,
        list_supplement_assets_missing_from_inventory,
    )

    missing_supplements = list_supplement_assets_missing_from_inventory(project)
    if selected_folders:
        missing_supplements = [
            entry
            for entry in missing_supplements
            if entry["folder_name"] in set(selected_folders)
        ]
    st.divider()
    st.markdown("**Supplement-Assets** — noch nicht im Inventory.")
    if missing_supplements:
        st.info(
            f"{len(missing_supplements)} Supplement-Asset(s) fehlen im Inventory "
            "(Cut-Plan und/oder `_supplemental/`)."
        )
        preview = ", ".join(
            f"`{Path(entry['asset_path']).name}`" for entry in missing_supplements[:8]
        )
        suffix = " …" if len(missing_supplements) > 8 else ""
        st.caption(preview + suffix)
    else:
        st.caption("Keine fehlenden Supplement-Assets für die aktuelle Ordnerauswahl.")
    if st.button(
        "🧩 Fehlende Supplement-Assets analysieren & ins Inventory",
        key=f"analyze_missing_supplements_{project.id}",
        disabled=any_job_running or not missing_supplements,
        help=(
            "Analysiert alle Supplement-Dateien, die noch nicht im Folder-Inventory "
            "stehen, und übernimmt sie. Vorhandene LLM-Validierung wird wiederverwendet."
        ),
    ):
        if not missing_supplements:
            st.warning("Keine fehlenden Supplements.")
        else:
            with st.spinner("Analysiere fehlende Supplement-Assets …"):
                report = analyze_and_import_missing_supplement_assets(
                    project,
                    folder_names=list(selected_folders) if selected_folders else None,
                    gemini_model=selected_model,
                )
            if report.imported:
                details = ", ".join(
                    f"{folder}: {count}"
                    for folder, count in sorted(report.imported_by_folder.items())
                )
                st.success(f"{report.imported} Supplement(s) analysiert und übernommen ({details}).")
            else:
                st.warning("Keine Supplements übernommen.")
            for skip in report.skipped[:20]:
                st.caption(f"⚠️ {skip}")
            st.rerun()

    if without_voiceover:
        return

    st.divider()
    if st.button(
        "⚡ Voice-over + alle Ordner",
        key=f"all_run_{project.id}",
        disabled=any_job_running,
    ):
        if not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
        elif (
            selected_voice_backend == VOICE_BACKEND_WHISPER
            and not is_whisper_available()
        ):
            st.error("Whisper nicht installiert.")
        else:
            all_folders = list(project.asset_subdir_names)
            st.session_state[folder_state_key] = all_folders
            update_project_selection(project.id, all_folders)
            if _start_voice_analysis_background(
                project,
                backend=selected_voice_backend,
                whisper_model=selected_whisper_model,
                gemini_model=selected_model,
                chain_asset_folders=all_folders,
                chain_asset_model=selected_model,
            ):
                st.rerun()


def render_project_workbench() -> None:
    st.header("① Analysen")

    project = render_project_selector()
    if project is None:
        return

    without_voiceover = bool(
        getattr(project, "is_without_voiceover_pipeline", False)
        or getattr(project, "is_without_voiceover", False)
    )

    render_workflow_progress(project, current_step="analysis", lightweight=True)
    if not get_workflow_status(project, lightweight=True).clean_media_done:
        st.warning(
            "**Clean Media noch nicht abgeschlossen** — unter **⓪ Clean Media** Medien "
            "prüfen und ggf. transcodieren, bevor du analysierst."
        )
    render_analysis_jobs_monitor(project)
    diag_key = f"inv_sync_diag_{project.id}"
    with st.expander(
        "Inventar-Sync (Diagnose)",
        expanded=not project.inventory_dir.is_dir(),
    ):
        st.caption(f"Zielordner: `{project.inventory_dir}`")
        st.caption(f"Cache: `{project.work_dir_path / 'cache' / 'inventory'}`")
        if project.inventory_path.is_file():
            st.caption(f"Legacy: `{project.inventory_path}` wird beim Sync aufgeteilt.")
        st.caption(
            "Inventar-Sync läuft nicht mehr bei jedem Klick — nur auf Button-Druck."
        )
        sync_statuses = st.session_state.get(diag_key)
        if sync_statuses is None:
            st.write(
                "Noch nicht geprüft. „Status prüfen“ oder „aus Cache aufbauen“ klicken."
            )
        elif not sync_statuses:
            st.write("Noch keine Asset-Ordner gescannt.")
        else:
            for status in sync_statuses:
                label = (
                    f"**{status.folder}** — {status.detail} "
                    f"({status.cache_files} Cache / {status.media_files} Medien)"
                )
                if status.state == "created":
                    st.success(label)
                elif status.state == "exists":
                    st.info(label)
                else:
                    st.warning(label)
        btn_probe, btn_sync = st.columns(2)
        with btn_probe:
            if st.button("Status prüfen", key=f"probe_inv_{project.id}"):
                st.session_state[diag_key] = probe_folder_inventory_statuses(project)
                st.rerun()
        with btn_sync:
            if st.button(
                "Inventar jetzt aus Cache aufbauen",
                key=f"sync_inv_{project.id}",
            ):
                created, refreshed = sync_folder_inventories_from_cache(project)
                st.session_state[diag_key] = refreshed
                _invalidate_folder_status_cache(project.id)
                if created:
                    st.success("Erstellt: " + ", ".join(created))
                else:
                    st.warning(
                        "Keine neuen Ordner-Inventare erstellt — siehe Status oben."
                    )
                st.rerun()
    render_output_status(project)
    st.caption(
        f"Status: {project.status.value} · "
        f"{len(project.selected_asset_subdirs)} Ordner gespeichert"
    )

    selected_folders = _render_folder_picker(project)
    st.divider()

    tab_folders, tab_run, tab_results = st.tabs(
        ["📁 Ordner", "▶️ Analysen starten", "📄 Ergebnisse"]
    )

    with tab_folders:
        st.markdown("Status aller Asset-Ordner")
        _render_folder_status_overview(project)

    with tab_run:
        folder_state_key = f"workbench_folders_{project.id}"
        selected_folders = st.session_state.get(
            folder_state_key,
            list(project.selected_asset_subdirs),
        )

        with st.expander("⚙️ Einstellungen (Modelle & API)", expanded=False):
            if not is_gemini_configured():
                st.warning("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** eintragen.")
            if not without_voiceover and not is_whisper_available():
                st.caption("Whisper fehlt — nur Voice-over via Gemini möglich.")

            if not without_voiceover:
                selected_voice_backend = st.selectbox(
                    "Voice-over-Engine",
                    options=list(VOICE_BACKEND_CHOICES),
                    format_func=lambda value: VOICE_BACKEND_LABELS[value],
                    key="voice_backend",
                )
                st.selectbox(
                    "Whisper-Modell",
                    options=list(WHISPER_MODEL_CHOICES),
                    format_func=lambda value: WHISPER_MODEL_LABELS[value],
                    key="whisper_model",
                    disabled=selected_voice_backend != VOICE_BACKEND_WHISPER,
                )
            st.selectbox(
                "Gemini-Modell",
                options=list(GEMINI_MODEL_CHOICES),
                format_func=format_gemini_model_label,
                key="gemini_model",
            )
            st.caption("API-Schlüssel wechseln: **🔑 API-Schlüssel**")

        voice_backend, whisper_model, gemini_model = _init_model_settings()

        _render_analysis_actions(
            project,
            selected_folders,
            voice_backend,
            whisper_model,
            gemini_model,
        )

        st.caption(f"Aktuell {len(selected_folders)} Ordner für Asset-Analyse ausgewählt.")

    with tab_results:
        if getattr(project, "is_without_voiceover_pipeline", project.is_without_voiceover):
            st.caption(
                "Ohne Voice-Over: keine Voice-over-Analyse. "
                "Fehlende Supplements analysierst du unter **▶️ Analysen starten**."
            )
        elif project.voice_analysis_path.is_file():
            st.markdown("**voice_over_analysis.json**")
            st.code(project.voice_analysis_path.read_text(encoding="utf-8")[:4000])
        else:
            st.caption("Voice-over-Analyse noch nicht erstellt.")
        if project.inventory_dir.is_dir():
            inventory_files = sorted(project.inventory_dir.glob("*.json"))
            if inventory_files:
                st.markdown("**Inventar (pro Ordner)**")
                for inv_file in inventory_files:
                    st.caption(str(inv_file.name))
                    st.code(inv_file.read_text(encoding="utf-8")[:2000])
            else:
                st.caption("Inventar noch nicht erstellt.")
        else:
            st.caption("Inventar noch nicht erstellt.")

    render_file_paths(project)
