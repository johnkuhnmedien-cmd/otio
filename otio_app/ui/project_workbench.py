"""Streamlit-UI: Projekt bearbeiten und Analysen starten."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.models import ProjectStatus
from otio_app.project_repository import (
    get_project_by_id,
    list_projects,
    update_project_selection,
    update_project_status,
)
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.gemini_client import GeminiNotConfiguredError, is_gemini_configured
from otio_app.services.voice_analyzer import analyze_voice_over


def _output_status(path: Path, label: str) -> None:
    if path.is_file():
        st.success(f"{label} vorhanden: `{path}`")
    else:
        st.caption(f"{label} noch nicht erstellt: `{path}`")


def _run_with_feedback(action_label: str, callback) -> bool:
    with st.spinner(action_label):
        try:
            callback()
            st.success(f"{action_label} abgeschlossen.")
            return True
        except GeminiNotConfiguredError as exc:
            st.error(str(exc))
        except FileNotFoundError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")
    return False


def render_project_workbench() -> None:
    st.header("Projekt bearbeiten")

    projects = list_projects()
    if not projects:
        st.info("Noch kein Projekt vorhanden. Lege zuerst ein Projekt an.")
        return

    labels = {project.id: project.name for project in projects}
    default_id = st.session_state.get("workbench_project_id", projects[0].id)
    if default_id not in labels:
        default_id = projects[0].id

    selected_id = st.selectbox(
        "Projekt wählen",
        options=list(labels.keys()),
        format_func=lambda pid: labels[pid],
        index=list(labels.keys()).index(default_id),
    )
    st.session_state["workbench_project_id"] = selected_id
    project = get_project_by_id(selected_id)
    if project is None:
        st.error("Projekt konnte nicht geladen werden.")
        return

    folder_state_key = f"workbench_folders_{project.id}"

    st.subheader(project.name)
    st.caption(
        f"Status: {project.status.value} · "
        f"{len(project.selected_asset_subdirs)} Ordner gespeichert"
    )

    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Projektordner:** `{project.project_root}`")
        st.write(f"**Voice-over:** `{project.voice_over_dir}`")
    with col2:
        _output_status(project.voice_analysis_path, "Voice-over-Analyse")
        _output_status(project.inventory_path, "Inventar")

    if not is_gemini_configured():
        st.warning(
            "GEMINI_API_KEY ist nicht gesetzt. Analysen sind erst nach Eintrag in `.env` möglich."
        )

    st.markdown("### Ordnerauswahl")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("Alle Ordner auswählen", key=f"all_{project.id}"):
            st.session_state[folder_state_key] = list(project.asset_subdir_names)
            st.rerun()
    with btn_col2:
        if st.button("Gespeicherte Auswahl laden", key=f"reload_{project.id}"):
            st.session_state[folder_state_key] = list(project.selected_asset_subdirs)
            st.rerun()

    if folder_state_key not in st.session_state:
        st.session_state[folder_state_key] = list(project.selected_asset_subdirs)

    selected_folders = st.multiselect(
        "Zu bearbeitende Asset-Ordner",
        options=project.asset_subdir_names,
        key=folder_state_key,
    )
    st.caption(f"{len(selected_folders)} von {len(project.asset_subdir_names)} Ordnern ausgewählt")

    if st.button("Auswahl im Projekt speichern", key=f"save_sel_{project.id}"):
        update_project_selection(project.id, selected_folders)
        st.success("Ordnerauswahl gespeichert.")
        st.rerun()

    st.markdown("### Analysen")
    st.info(
        "Kostenpflichtige Gemini-Aufrufe starten erst nach Bestätigung. "
        "Es werden nur Voice-over-Audios bzw. extrahierte Frame-Bilder gesendet — keine Videos."
    )

    api_confirmed = st.checkbox(
        "Ich bestätige kostenpflichtige Gemini-API-Aufrufe",
        key=f"confirm_api_{project.id}",
    )

    col_v, col_s, col_all = st.columns(3)

    with col_v:
        if st.button("🎙️ Voice-over analysieren", key=f"voice_{project.id}"):
            if not api_confirmed:
                st.warning("Bitte API-Aufrufe bestätigen.")
            else:
                update_project_status(project.id, ProjectStatus.ANALYZING)

                def _voice_job() -> None:
                    current = get_project_by_id(project.id)
                    assert current is not None
                    analyze_voice_over(current, use_api=True)
                    update_project_status(project.id, ProjectStatus.READY)

                if _run_with_feedback("Voice-over-Analyse", _voice_job):
                    st.rerun()

    with col_s:
        if st.button("📁 Ausgewählte Ordner (Gemini)", key=f"assets_{project.id}"):
            if not selected_folders:
                st.warning("Bitte mindestens einen Ordner auswählen.")
            elif not api_confirmed:
                st.warning("Bitte API-Aufrufe bestätigen.")
            else:
                folders = list(selected_folders)
                update_project_selection(project.id, folders)
                update_project_status(project.id, ProjectStatus.ANALYZING)

                def _asset_job() -> None:
                    current = get_project_by_id(project.id)
                    assert current is not None
                    analyze_asset_folders(current, folders, use_api=True)
                    update_project_status(project.id, ProjectStatus.READY)

                if _run_with_feedback("Asset-Analyse", _asset_job):
                    st.rerun()

    with col_all:
        if st.button("⚡ Alles bearbeiten", key=f"all_run_{project.id}"):
            if not api_confirmed:
                st.warning("Bitte API-Aufrufe bestätigen.")
            else:
                all_folders = list(project.asset_subdir_names)
                st.session_state[folder_state_key] = all_folders
                update_project_selection(project.id, all_folders)
                update_project_status(project.id, ProjectStatus.ANALYZING)

                def _full_job() -> None:
                    current = get_project_by_id(project.id)
                    assert current is not None
                    analyze_voice_over(current, use_api=True)
                    analyze_asset_folders(current, all_folders, use_api=True)
                    update_project_status(project.id, ProjectStatus.READY)

                if _run_with_feedback("Vollständige Analyse", _full_job):
                    st.rerun()

    with st.expander("Ausgabe-Vorschau"):
        if project.voice_analysis_path.is_file():
            st.markdown("**voice_over_analysis.json**")
            st.code(project.voice_analysis_path.read_text(encoding="utf-8")[:4000])
        if project.inventory_path.is_file():
            st.markdown("**inventory.json**")
            st.code(project.inventory_path.read_text(encoding="utf-8")[:4000])
