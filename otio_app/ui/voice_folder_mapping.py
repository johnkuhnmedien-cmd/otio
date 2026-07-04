"""Streamlit-UI: Voice-over ↔ Asset-Ordner zuordnen."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import VoiceFolderMappingDocument, VoiceFolderMappingEntry
from otio_app.services.voice_folder_matcher import (
    load_voice_folder_mapping,
    merge_with_saved_mapping,
    save_voice_folder_mapping,
    suggest_voice_folder_mappings,
)
from otio_app.ui.project_context import (
    render_file_paths,
    render_project_selector,
    render_workflow_progress,
)


def _mapping_state_key(project_id: str) -> str:
    return f"voice_folder_mapping_{project_id}"


def _init_mapping_state(project_id: str, entries: list[VoiceFolderMappingEntry]) -> None:
    st.session_state[_mapping_state_key(project_id)] = [
        entry.model_dump(mode="json") for entry in entries
    ]


def _get_mapping_entries(project_id: str) -> list[VoiceFolderMappingEntry]:
    raw_entries = st.session_state.get(_mapping_state_key(project_id), [])
    return [VoiceFolderMappingEntry.model_validate(entry) for entry in raw_entries]


def render_voice_folder_mapping() -> None:
    st.header("② Zuordnung")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="mapping")

    st.markdown(
        "Ordnername im **Voice-over-Dateinamen** erkennen "
        "(z. B. `USA_Florida Keys_VO.wav` → **Florida Keys**), prüfen und bestätigen."
    )

    suggestions = suggest_voice_folder_mappings(project)
    if not suggestions:
        st.warning(f"Keine Voice-over-Dateien in `{project.voice_over_dir}`.")
        render_file_paths(project)
        return

    state_key = _mapping_state_key(project.id)
    if state_key not in st.session_state:
        _init_mapping_state(
            project.id,
            merge_with_saved_mapping(project, suggestions),
        )

    saved = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if saved is not None and saved.confirmed:
        st.success("Zuordnung bestätigt und gespeichert.")
    elif saved is not None:
        st.info("Entwurf vorhanden — bitte prüfen und bestätigen.")

    if st.button("Neu aus Dateinamen erkennen", key=f"rematch_{project.id}"):
        _init_mapping_state(project.id, suggest_voice_folder_mappings(project))
        st.rerun()

    folder_options = ["— nicht zugeordnet —", *project.asset_subdir_names]
    entries = _get_mapping_entries(project.id)
    updated_entries: list[VoiceFolderMappingEntry] = []

    for index, entry in enumerate(entries):
        selected_folder = entry.folder or "— nicht zugeordnet —"
        option_list = folder_options
        if selected_folder not in option_list and selected_folder != "— nicht zugeordnet —":
            option_list = [*option_list, selected_folder]

        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            st.write(f"**{Path(entry.voice_file).name}**")
        with col2:
            choice = st.selectbox(
                "Asset-Ordner",
                options=option_list,
                index=option_list.index(selected_folder),
                key=f"map_folder_{project.id}_{index}",
                label_visibility="collapsed",
            )
            folder_value = None if choice == "— nicht zugeordnet —" else choice
            if folder_value == entry.folder:
                method = entry.match_method
            else:
                method = "manual"
            updated_entries.append(
                VoiceFolderMappingEntry(
                    voice_file=entry.voice_file,
                    folder=folder_value,
                    match_method=method if folder_value else "manual",
                    confirmed=False,
                )
            )
        with col3:
            if folder_value is None:
                st.error("Fehlt")
            elif method == "filename":
                st.success("Auto")
            else:
                st.info("Manuell")

    st.session_state[state_key] = [
        entry.model_dump(mode="json") for entry in updated_entries
    ]

    unassigned = sum(1 for entry in updated_entries if not entry.folder)
    auto_matched = sum(
        1
        for entry in updated_entries
        if entry.folder and entry.match_method == "filename"
    )
    st.caption(
        f"{auto_matched} automatisch · {unassigned} offen · "
        f"{len(updated_entries)} Dateien"
    )

    confirm = st.checkbox(
        "Zuordnung geprüft und bestätigt",
        key=f"confirm_mapping_{project.id}",
    )

    if st.button("Speichern", key=f"save_mapping_{project.id}", type="primary"):
        if unassigned > 0:
            st.warning("Noch Voice-over-Dateien ohne Ordner.")
        elif not confirm:
            st.warning("Bitte bestätigen.")
        else:
            confirmed_entries = [
                entry.model_copy(update={"confirmed": True})
                for entry in updated_entries
            ]
            document = save_voice_folder_mapping(
                project,
                confirmed_entries,
                confirmed=True,
            )
            _init_mapping_state(project.id, document.entries)
            st.success("Gespeichert.")
            st.rerun()

    with st.expander("JSON-Vorschau", expanded=False):
        preview = VoiceFolderMappingDocument(
            project_id=project.id,
            confirmed=bool(saved and saved.confirmed),
            entries=updated_entries,
        )
        st.code(preview.model_dump_json(indent=2)[:4000])

    render_file_paths(project)
