"""Streamlit-Einstiegspunkt — OTIO Schnittplaner."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from otio_app.defaults import DEFAULT_FRAMES_PER_SHOT, DEFAULT_VOICE_OVER_SUBDIR
from otio_app.models import ProjectCreate
from otio_app.paths import clean_user_path_input, create_work_dir, normalize_path
from otio_app.project_layout import (
    classify_subdirectories,
    default_work_dir,
    scan_project_structure,
)
from otio_app.project_repository import create_project, list_projects
from otio_app.services.gemini_client import format_gemini_model_label, get_default_gemini_model
from otio_app.system_checks import run_all_checks
from otio_app.ui.project_workbench import render_project_workbench

st.set_page_config(
    page_title="OTIO Schnittplaner",
    page_icon="🎬",
    layout="wide",
)

PAGE_NEW = "Neues Projekt"
PAGE_LIST = "Gespeicherte Projekte"
PAGE_WORK = "Projekt bearbeiten"
PAGE_STATUS = "Systemstatus"

PREVIEW_KEY = "project_preview"
PENDING_KEY = "pending_project"


def _render_path_diagnostic(diagnostic) -> None:
    with st.expander("Pfad-Diagnose", expanded=True):
        st.write(f"**Eingabe:** `{diagnostic.input_path}`")
        st.write(f"**Aufgelöst:** `{diagnostic.resolved_path}`")
        st.write(f"**Existiert:** {'ja' if diagnostic.exists else 'nein'}")
        st.write(f"**Ist Ordner:** {'ja' if diagnostic.is_directory else 'nein'}")
        st.write(f"**Einträge gesamt:** {diagnostic.total_entries}")
        st.write(f"**iCloud-Pfad:** {'ja' if diagnostic.icloud_path else 'nein'}")
        if diagnostic.subdirectory_names:
            st.write(
                "**Erkannte Unterordner:** "
                + ", ".join(f"`{name}`" for name in diagnostic.subdirectory_names[:20])
            )
        if diagnostic.file_names:
            st.caption(
                "Dateien im Projektroot: "
                + ", ".join(f"`{name}`" for name in diagnostic.file_names[:10])
            )
        if diagnostic.read_error:
            st.error(f"Lesefehler: {diagnostic.read_error}")


def _render_structure_overview(scan) -> None:
    st.subheader("Erkannte Struktur")
    if scan.warning:
        st.warning(scan.warning)
    if scan.diagnostic is not None:
        st.caption(f"Aufgelöster Pfad: `{scan.diagnostic.resolved_path}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Unterordner gesamt", len(scan.all_subdirectory_names))
    with col2:
        st.metric("Asset-Ordner", len(scan.asset_subdir_names))
    with col3:
        st.metric("Voice-over", scan.voice_over_folder_name or "—")

    st.markdown("#### 🎙️ Voice-over-Ordner")
    if scan.voice_over_folder_name and scan.voice_over_dir is not None:
        st.info(f"**Hauptordner:** `{scan.voice_over_dir}`")
        if scan.voice_over_language_dir is not None:
            if scan.voice_over_language_exists:
                st.success(
                    f"**Audios (Sprache {scan.language}):** `{scan.voice_over_language_dir}`"
                )
            else:
                st.warning(
                    f"**Audios (Sprache {scan.language}) fehlen noch:** "
                    f"`{scan.voice_over_language_dir}`"
                )
    else:
        st.error(
            "Kein Voice-over-Ordner erkannt. Bitte unten den richtigen Ordner auswählen."
        )

    if scan.system_folder_names:
        st.markdown("#### ⚙️ Systemordner")
        st.caption(", ".join(f"`{name}`" for name in scan.system_folder_names))

    st.markdown("#### 📁 Asset-Ordner")
    if scan.asset_subdir_names:
        st.write(", ".join(f"`{name}`" for name in scan.asset_subdir_names))
    else:
        st.warning(
            "Keine Asset-Ordner gefunden. Prüfe den Projektpfad, den Voice-over-Ordner "
            "und lade iCloud-Inhalte ggf. im Finder lokal herunter."
        )


def _show_saved_project(saved) -> None:
    st.success(
        f"Projekt '{saved.name}' gespeichert (Status: {saved.status.value})."
    )
    st.json(
        {
            "id": saved.id,
            "project_root": saved.project_root,
            "work_dir": saved.work_dir,
            "voice_over_ordner": saved.voice_over_subdir,
            "voice_over_pfad": str(saved.voice_over_dir),
            "alle_asset_ordner": saved.asset_subdir_names,
            "ausgewaehlte_ordner": saved.selected_asset_subdirs,
            "inventory_path": str(saved.inventory_path),
            "voice_analysis_path": str(saved.voice_analysis_path),
            "frames_per_shot": saved.frames_per_shot,
        }
    )


def _finalize_project_save(
    project_data: ProjectCreate,
    available_assets: list[str],
    selected_assets: list[str],
) -> None:
    if not selected_assets:
        st.error("Bitte mindestens einen Ordner auswählen.")
        return

    work_path = normalize_path(project_data.work_dir)
    if not work_path.exists():
        st.session_state[PENDING_KEY] = {
            "project": project_data.model_dump(mode="json"),
            "available_assets": available_assets,
            "selected_asset_subdirs": selected_assets,
        }
        st.session_state.pop(PREVIEW_KEY, None)
        return

    saved = create_project(
        project_data,
        asset_subdir_names=available_assets,
        selected_asset_subdirs=selected_assets,
    )
    st.session_state.pop(PREVIEW_KEY, None)
    st.session_state.pop(PENDING_KEY, None)
    _show_saved_project(saved)


def _save_pending_project() -> None:
    raw = st.session_state.pop(PENDING_KEY, None)
    if raw is None:
        return
    project_data = ProjectCreate.model_validate(raw["project"])
    work_path = normalize_path(project_data.work_dir)
    if not work_path.exists():
        create_work_dir(work_path)
    saved = create_project(
        project_data,
        asset_subdir_names=raw["available_assets"],
        selected_asset_subdirs=raw["selected_asset_subdirs"],
    )
    _show_saved_project(saved)


with st.sidebar:
    st.title("OTIO Schnittplaner")
    st.caption("Meilenstein 2 — Analyse-Workflow")
    page = st.radio(
        "Navigation",
        [PAGE_NEW, PAGE_LIST, PAGE_WORK, PAGE_STATUS],
        label_visibility="collapsed",
        key="sidebar_nav",
    )

if page == PAGE_NEW:
    st.header("Neues Projekt anlegen")

    st.markdown(
        """
        Gib den **Projektordner** an (z. B. `.../USA`). Danach wählst du aus,
        welche Asset-Unterordner bearbeitet werden sollen.
        """
    )

    with st.form("project_form"):
        project_root = st.text_input(
            "Projektordner *",
            help="Hauptordner des Projekts — ohne Anführungszeichen",
            placeholder="/Users/claudiakuhn/Documents/YT/Unglaubliche Welt/USA",
        )
        name = st.text_input("Projektname *")
        voice_over_subdir = st.text_input(
            "Voice-over-Unterordner",
            value=DEFAULT_VOICE_OVER_SUBDIR,
            help="Name des Unterordners für Audios, Standard: Voice over",
        )
        work_dir = st.text_input(
            "Arbeitsordner (optional)",
            value="",
            help="Leer lassen = automatisch <Projektordner>/_otio für Cache und Frames",
        )

        st.subheader("Projektvorgaben")
        col1, col2 = st.columns(2)
        with col1:
            language = st.text_input("Sprache", value="de")
            fps = st.number_input("FPS", value=25.0, min_value=0.1, step=0.1)
            width = st.number_input("Breite (px)", value=3840, min_value=1, step=1)
            frames_per_shot = st.number_input(
                "Frames pro Shot (Gemini)",
                value=DEFAULT_FRAMES_PER_SHOT,
                min_value=1,
                step=1,
            )
        with col2:
            target_platform = st.text_input("Zielplattform", value="YouTube")
            aspect_ratio = st.text_input("Seitenverhältnis", value="16:9")
            height = st.number_input("Höhe (px)", value=2160, min_value=1, step=1)

        notes = st.text_area("Notizen", value="")
        submitted = st.form_submit_button("Ordner erfassen")

    if submitted:
        if not clean_user_path_input(project_root):
            st.error(
                "Bitte den **Projektordner** eintragen (erstes Feld im Formular). "
                "Tipp: Im Finder Rechtsklick auf den USA-Ordner → "
                "Option gedrückt halten → „Pfadname kopieren“."
            )
        elif not name.strip():
            st.error("Bitte einen **Projektname** eintragen.")
        else:
            try:
                project_data = ProjectCreate(
                    name=name,
                    project_root=project_root,
                    work_dir=work_dir or None,
                    voice_over_subdir=voice_over_subdir,
                    language=language,
                    frames_per_shot=int(frames_per_shot),
                    fps=float(fps),
                    width=int(width),
                    height=int(height),
                    aspect_ratio=aspect_ratio,
                    target_platform=target_platform,
                    notes=notes or None,
                )
            except ValidationError as exc:
                st.session_state.pop(PREVIEW_KEY, None)
                st.session_state.pop(PENDING_KEY, None)
                for error in exc.errors():
                    st.error(error["msg"])
            else:
                scan = scan_project_structure(
                    project_data.project_root_path,
                    project_data.work_dir_path,
                    project_data.voice_over_subdir,
                    project_data.language,
                )
                st.session_state[PREVIEW_KEY] = {
                    "project": project_data.model_dump(mode="json"),
                    "all_subdirs": scan.all_subdirectory_names,
                    "scan_error": scan.error,
                    "scan_warning": scan.warning,
                    "diagnostic": (
                        scan.diagnostic.__dict__
                        if scan.diagnostic is not None
                        else None
                    ),
                }
                st.session_state.pop(PENDING_KEY, None)
                st.rerun()

    if PREVIEW_KEY in st.session_state:
        preview = st.session_state[PREVIEW_KEY]
        project_data = ProjectCreate.model_validate(preview["project"])

        if preview.get("scan_error"):
            st.error(preview["scan_error"])
        if preview.get("scan_warning"):
            st.warning(preview["scan_warning"])
        if preview.get("diagnostic"):
            from otio_app.project_layout import PathDiagnostic

            _render_path_diagnostic(PathDiagnostic(**preview["diagnostic"]))

        all_subdirs = preview.get("all_subdirs", [])
        if all_subdirs:
            st.success(f"{len(all_subdirs)} Unterordner erkannt.")
        elif not preview.get("scan_error"):
            st.warning(
                "Keine Unterordner gefunden. Prüfe die Pfad-Diagnose unten "
                "oder klicke „Erneut scannen“."
            )

        if not all_subdirs:
            if st.button("Erneut scannen"):
                scan = scan_project_structure(
                    project_data.project_root_path,
                    project_data.work_dir_path,
                    project_data.voice_over_subdir,
                    project_data.language,
                )
                st.session_state[PREVIEW_KEY] = {
                    "project": preview["project"],
                    "all_subdirs": scan.all_subdirectory_names,
                    "scan_error": scan.error,
                    "scan_warning": scan.warning,
                    "diagnostic": (
                        scan.diagnostic.__dict__
                        if scan.diagnostic is not None
                        else None
                    ),
                }
                st.rerun()
        else:
            project_root = project_data.project_root_path
            work_dir = project_data.work_dir_path

            default_voice = project_data.voice_over_subdir
            default_index = 0
            for index, name in enumerate(all_subdirs):
                if name.casefold() == default_voice.casefold():
                    default_index = index
                    break

            voice_over_choice = st.selectbox(
                "🎙️ Welcher Unterordner ist Voice-over?",
                options=all_subdirs,
                index=default_index,
                help="Dieser Ordner enthält die Audios und wird nicht als Asset bearbeitet.",
            )

            scan = classify_subdirectories(
                all_subdirs,
                voice_over_choice,
                work_dir,
                project_root,
                project_data.language,
            )
            _render_structure_overview(scan)
            available_assets = scan.asset_subdir_names

            updated_project = ProjectCreate.model_validate(
                {
                    **preview["project"],
                    "voice_over_subdir": voice_over_choice,
                }
            )

            st.subheader("Ordnerauswahl")
            selected_assets = st.multiselect(
                "Zu bearbeitende Asset-Ordner *",
                options=available_assets,
                default=available_assets,
                help="Nur ausgewählte Ordner werden später analysiert und ins Inventar aufgenommen.",
            )
            st.caption(f"Ausgewählt: {len(selected_assets)} von {len(available_assets)}")

            col_save, col_cancel = st.columns(2)
            with col_save:
                if st.button(
                    "Projekt speichern",
                    disabled=not available_assets or not selected_assets,
                ):
                    _finalize_project_save(
                        updated_project,
                        available_assets,
                        selected_assets,
                    )
            with col_cancel:
                if st.button("Auswahl verwerfen"):
                    st.session_state.pop(PREVIEW_KEY, None)
                    st.rerun()

    if PENDING_KEY in st.session_state:
        pending = st.session_state[PENDING_KEY]
        project_data = ProjectCreate.model_validate(pending["project"])
        work_path = normalize_path(project_data.work_dir)
        default_path = default_work_dir(normalize_path(project_data.project_root))
        st.warning(f"Der Arbeitsordner existiert noch nicht:\n`{work_path}`")
        if work_path == default_path:
            st.info(
                "Standard-Arbeitsordner `_otio` wird für Cache, Frames und "
                "Zwischenergebnisse verwendet. Originalmedien bleiben unverändert."
            )
        st.write(
            "**Ausgewählte Ordner:** "
            + ", ".join(f"`{name}`" for name in pending["selected_asset_subdirs"])
        )
        create_confirmed = st.checkbox(
            "Arbeitsordner jetzt anlegen",
            key="confirm_create_work_dir",
        )
        col_save, col_cancel = st.columns(2)
        with col_save:
            if st.button(
                "Arbeitsordner erstellen und Projekt speichern",
                disabled=not create_confirmed,
            ):
                _save_pending_project()
        with col_cancel:
            if st.button("Abbrechen"):
                st.session_state.pop(PENDING_KEY, None)
                st.rerun()

elif page == PAGE_LIST:
    st.header("Gespeicherte Projekte")
    projects = list_projects()
    if not projects:
        st.info("Noch keine Projekte gespeichert.")
    else:
        for project in projects:
            with st.expander(f"{project.name}  ({project.status.value})"):
                st.write(f"**ID:** `{project.id}`")
                st.write(f"**Projektordner:** `{project.project_root}`")
                st.write(f"**Arbeitsordner:** `{project.work_dir}`")
                st.write(f"**🎙️ Voice-over-Ordner:** `{project.voice_over_subdir}`")
                st.write(f"**🎙️ Voice-over Audios:** `{project.voice_over_dir}`")
                st.write(
                    f"**Gefundene Ordner ({len(project.asset_subdir_names)}):** "
                    + (
                        ", ".join(f"`{n}`" for n in project.asset_subdir_names)
                        if project.asset_subdir_names
                        else "—"
                    )
                )
                st.write(
                    f"**Zu bearbeiten ({len(project.selected_asset_subdirs)}):** "
                    + (
                        ", ".join(f"`{n}`" for n in project.selected_asset_subdirs)
                        if project.selected_asset_subdirs
                        else "—"
                    )
                )
                st.write(
                    f"**Geplante Ausgaben:** `{project.inventory_path}`, "
                    f"`{project.voice_analysis_path}`"
                )
                st.write(
                    f"**Vorgaben:** {project.language}, {project.frames_per_shot} Frames/Shot, "
                    f"{project.fps} fps, {project.width}×{project.height}, "
                    f"{project.aspect_ratio}, {project.target_platform}"
                )
                if project.notes:
                    st.write(f"**Notizen:** {project.notes}")
                st.caption(
                    f"Erstellt: {project.created_at.isoformat()} · "
                    f"Aktualisiert: {project.updated_at.isoformat()}"
                )
                if st.button("Projekt bearbeiten", key=f"open_{project.id}"):
                    st.session_state["workbench_project_id"] = project.id
                    st.session_state["sidebar_nav"] = PAGE_WORK
                    st.rerun()

elif page == PAGE_WORK:
    render_project_workbench()

elif page == PAGE_STATUS:
    st.header("Systemstatus")
    for result in run_all_checks():
        icon = "✅" if result.ok else "❌"
        st.subheader(f"{icon} {result.name}")
        st.write(result.message)
        if result.version:
            st.caption(f"Version: {result.version}")

    default_model = get_default_gemini_model()
    st.subheader("🤖 Gemini")
    st.write(
        f"Standardmodell (aus `.env` oder App-Default): "
        f"**{format_gemini_model_label(default_model)}** (`{default_model}`)"
    )
    st.caption(
        "Unter „Projekt bearbeiten“ kann pro Sitzung ein anderes Modell gewählt werden."
    )
