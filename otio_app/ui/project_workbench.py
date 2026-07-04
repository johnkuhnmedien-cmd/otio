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
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.voice_analyzer import analyze_voice_over
from otio_app.services.whisper_transcriber import (
    WhisperNotAvailableError,
    get_default_whisper_model,
    is_whisper_available,
)
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


def _render_folder_selection(project) -> list[str]:
    folder_state_key = f"workbench_folders_{project.id}"
    if folder_state_key not in st.session_state:
        st.session_state[folder_state_key] = list(project.selected_asset_subdirs)

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("Alle Ordner auswählen", key=f"all_{project.id}"):
            st.session_state[folder_state_key] = list(project.asset_subdir_names)
            st.rerun()
    with btn_col2:
        if st.button("Gespeicherte Auswahl laden", key=f"reload_{project.id}"):
            st.session_state[folder_state_key] = list(project.selected_asset_subdirs)
            st.rerun()

    selected_folders = st.multiselect(
        "Zu bearbeitende Asset-Ordner",
        options=project.asset_subdir_names,
        key=folder_state_key,
    )
    st.caption(
        f"{len(selected_folders)} von {len(project.asset_subdir_names)} Ordnern ausgewählt"
    )

    if st.button("Auswahl speichern", key=f"save_sel_{project.id}"):
        update_project_selection(project.id, selected_folders)
        st.success("Ordnerauswahl gespeichert.")
        st.rerun()

    return selected_folders


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
    if st.button("📁 Ausgewählte Ordner analysieren", key=f"assets_{project.id}"):
        if not selected_folders:
            st.warning("Bitte mindestens einen Ordner unter „Ordner“ auswählen.")
        elif not api_confirmed:
            st.warning("Bitte Gemini-API-Aufrufe in den Einstellungen bestätigen.")
        else:
            folders = list(selected_folders)
            update_project_selection(project.id, folders)
            update_project_status(project.id, ProjectStatus.ANALYZING)

            def _asset_job() -> None:
                current = get_project_by_id(project.id)
                assert current is not None
                analyze_asset_folders(
                    current, folders, use_api=True, model=selected_model
                )
                update_project_status(project.id, ProjectStatus.READY)

            if _run_with_feedback("Asset-Analyse", _asset_job):
                st.rerun()

    st.divider()
    if st.button("⚡ Voice-over + alle Ordner", key=f"all_run_{project.id}"):
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
            update_project_status(project.id, ProjectStatus.ANALYZING)

            def _full_job() -> None:
                _analyze_voice_for_project()
                current = get_project_by_id(project.id)
                assert current is not None
                analyze_asset_folders(
                    current, all_folders, use_api=True, model=selected_model
                )
                update_project_status(project.id, ProjectStatus.READY)

            if _run_with_feedback("Vollständige Analyse", _full_job):
                st.rerun()


def render_project_workbench() -> None:
    st.header("① Analysen")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="analysis")
    render_output_status(project)
    st.caption(
        f"Status: {project.status.value} · "
        f"{len(project.selected_asset_subdirs)} Ordner gespeichert"
    )

    tab_folders, tab_run, tab_results = st.tabs(
        ["📁 Ordner", "▶️ Analysen starten", "📄 Ergebnisse"]
    )

    with tab_folders:
        st.markdown("Welche Asset-Ordner sollen beschrieben werden?")
        selected_folders = _render_folder_selection(project)

    with tab_run:
        selected_folders = st.session_state.get(
            f"workbench_folders_{project.id}",
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
        if project.inventory_path.is_file():
            st.markdown("**inventory.json**")
            st.code(project.inventory_path.read_text(encoding="utf-8")[:4000])
        else:
            st.caption("Inventar noch nicht erstellt.")

    render_file_paths(project)
