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
    get_project_by_id,
    update_project_selection,
    update_project_status,
)
from otio_app.services.asset_analysis_job import get_asset_analysis_job_manager
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.inventory_loader import sync_folder_inventories_from_cache
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    list_assets_missing_successful_cache,
)
from otio_app.services.voice_analyzer import analyze_voice_over
from otio_app.services.whisper_transcriber import (
    WhisperNotAvailableError,
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
    list_open_folder_names,
)
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.manual_folder_completion import is_manually_complete, set_manually_complete
from otio_app.ui.asset_analysis_job_ui import render_asset_analysis_job_monitor
from otio_app.ui.project_context import (
    render_file_paths,
    render_output_status,
    render_project_selector,
    render_workflow_progress,
)


def _run_with_feedback(action_label: str, callback) -> bool:
    with st.spinner(action_label):
        try:
            callback()
            st.success(f"{action_label} abgeschlossen.")
            return True
        except GeminiNotConfiguredError as exc:
            st.error(str(exc))
        except WhisperNotAvailableError as exc:
            st.error(str(exc))
        except FileNotFoundError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")
    return False


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

    selected_folders = st.multiselect(
        "Zu bearbeitende Asset-Ordner",
        options=project.asset_subdir_names,
        format_func=lambda name: format_folder_with_status(project, name),
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
            st.session_state[folder_state_key] = list_open_folder_names(
                project, project.asset_subdir_names
            )
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
    counts = count_folder_states(project, project.asset_subdir_names)
    st.caption(
        f"🟢 {counts[FolderAnalysisState.COMPLETE]} fertig · "
        f"🟡 {counts[FolderAnalysisState.PARTIAL]} teilweise · "
        f"⚪ {counts[FolderAnalysisState.PENDING]} offen · "
        f"➖ {counts[FolderAnalysisState.EMPTY]} leer"
    )

    for folder_name in project.asset_subdir_names:
        state = get_folder_analysis_state(project, folder_name)
        label = format_folder_with_status(project, folder_name)
        col_status, col_action = st.columns([5, 1])
        with col_status:
            if state == FolderAnalysisState.COMPLETE:
                st.success(label)
            elif state == FolderAnalysisState.PARTIAL:
                st.warning(label)
                gaps = list_missing_or_failed_assets(project, folder_name)
                if gaps:
                    with st.expander(f"Details · {folder_name}", expanded=False):
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
            if folder_is_fully_analyzed(project, folder_name):
                st.caption("✓")
            elif is_manually_complete(project, folder_name):
                if st.button(
                    "↩",
                    key=f"unmanual_{project.id}_{folder_name}",
                    help="Manuelle Markierung aufheben",
                ):
                    set_manually_complete(project, folder_name, complete=False)
                    st.rerun()
            elif state in {FolderAnalysisState.PARTIAL, FolderAnalysisState.PENDING}:
                if st.button(
                    "✓",
                    key=f"manual_{project.id}_{folder_name}",
                    help="Manuell als fertig markieren",
                ):
                    set_manually_complete(project, folder_name, complete=True)
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
    api_confirmed: bool,
) -> None:
    folder_state_key = f"workbench_folders_{project.id}"
    asset_job_running = get_asset_analysis_job_manager().is_running(project.id)

    def _analyze_voice_for_project() -> None:
        current = get_project_by_id(project.id)
        assert current is not None
        analyze_voice_over(
            current,
            use_api=True,
            backend=selected_voice_backend,
            model=selected_model,
            whisper_model=selected_whisper_model,
        )

    def _run_voice_analysis() -> None:
        _analyze_voice_for_project()
        update_project_status(project.id, ProjectStatus.READY)

    st.markdown("**Voice-over** — lokal mit Whisper (Standard) oder optional Gemini.")
    if st.button("🎙️ Voice-over analysieren", key=f"voice_{project.id}", type="primary"):
        if selected_voice_backend == VOICE_BACKEND_GEMINI and not api_confirmed:
            st.warning("Bitte Gemini-API-Aufrufe in den Einstellungen bestätigen.")
        elif selected_voice_backend == VOICE_BACKEND_GEMINI and not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt in `.env`.")
        elif selected_voice_backend == VOICE_BACKEND_WHISPER and not is_whisper_available():
            st.error("Whisper nicht installiert — `pip install -r requirements.txt`.")
        else:
            update_project_status(project.id, ProjectStatus.ANALYZING)
            if _run_with_feedback("Voice-over-Analyse", _run_voice_analysis):
                st.rerun()

    st.divider()
    st.markdown("**Asset-Ordner** — Gemini analysiert nur Frame-Bilder (kostenpflichtig).")
    if asset_job_running:
        st.caption(
            "Asset-Analyse läuft im Hintergrund — Fortschritt siehe oben. "
            "Du kannst zu **③ Schnittplan** wechseln."
        )
    if selected_folders:
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
    if st.button(
        "📁 Ausgewählte Ordner analysieren",
        key=f"assets_{project.id}",
        disabled=asset_job_running,
    ):
        if not selected_folders:
            st.warning("Bitte mindestens einen Ordner unter „Ordner“ auswählen.")
        elif not api_confirmed:
            st.warning("Bitte Gemini-API-Aufrufe in den Einstellungen bestätigen.")
        else:
            folders = list(selected_folders)
            update_project_selection(project.id, folders)
            if _start_asset_analysis_background(project, folders, selected_model):
                st.rerun()

    st.divider()
    if st.button(
        "⚡ Voice-over + alle Ordner",
        key=f"all_run_{project.id}",
        disabled=asset_job_running,
    ):
        if not api_confirmed:
            st.warning("Bitte Gemini-API-Aufrufe bestätigen (für Asset-Ordner).")
        elif not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt in `.env`.")
        elif (
            selected_voice_backend == VOICE_BACKEND_WHISPER
            and not is_whisper_available()
        ):
            st.error("Whisper nicht installiert.")
        else:
            all_folders = list(project.asset_subdir_names)
            st.session_state[folder_state_key] = all_folders
            update_project_selection(project.id, all_folders)
            try:
                with st.spinner("Voice-over-Analyse …"):
                    _analyze_voice_for_project()
                    update_project_status(project.id, ProjectStatus.READY)
                if _start_asset_analysis_background(project, all_folders, selected_model):
                    st.rerun()
            except WhisperNotAvailableError as exc:
                st.error(str(exc))
            except GeminiNotConfiguredError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Fehler: {exc}")


def render_project_workbench() -> None:
    st.header("① Analysen")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="analysis")
    render_asset_analysis_job_monitor(project)
    created_inventories, sync_statuses = sync_folder_inventories_from_cache(project)
    if created_inventories:
        st.success(
            "Ordner-Inventare erstellt: "
            + ", ".join(f"`{name}`" for name in created_inventories)
        )
    with st.expander("Inventar-Sync (Diagnose)", expanded=not project.inventory_dir.is_dir()):
        st.caption(f"Zielordner: `{project.inventory_dir}`")
        st.caption(f"Cache: `{project.work_dir_path / 'cache' / 'inventory'}`")
        if project.inventory_path.is_file():
            st.caption(f"Legacy: `{project.inventory_path}` wird beim Sync aufgeteilt.")
        if not sync_statuses:
            st.write("Noch keine Asset-Ordner gescannt.")
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
        if st.button("Inventar jetzt aus Cache aufbauen", key=f"sync_inv_{project.id}"):
            created, refreshed = sync_folder_inventories_from_cache(project)
            if created:
                st.success("Erstellt: " + ", ".join(created))
            else:
                st.warning("Keine neuen Ordner-Inventare erstellt — siehe Status oben.")
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
                st.warning("GEMINI_API_KEY fehlt — Asset-Analysen nicht möglich.")
            if not is_whisper_available():
                st.caption("Whisper fehlt — nur Voice-over via Gemini möglich.")

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
            api_confirmed = st.checkbox(
                "Kostenpflichtige Gemini-Aufrufe bestätigen (Asset-Ordner)",
                key=f"confirm_api_{project.id}",
            )

        voice_backend, whisper_model, gemini_model = _init_model_settings()
        api_confirmed = st.session_state.get(f"confirm_api_{project.id}", False)

        _render_analysis_actions(
            project,
            selected_folders,
            voice_backend,
            whisper_model,
            gemini_model,
            api_confirmed,
        )

        st.caption(f"Aktuell {len(selected_folders)} Ordner für Asset-Analyse ausgewählt.")

    with tab_results:
        if project.voice_analysis_path.is_file():
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
