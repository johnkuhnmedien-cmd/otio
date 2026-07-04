"""Streamlit-Einstiegspunkt — OTIO Schnittplaner."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from otio_app.defaults import DEFAULT_FRAMES_PER_SHOT, DEFAULT_VOICE_OVER_SUBDIR
from otio_app.models import ProjectCreate
from otio_app.paths import create_work_dir, normalize_path
from otio_app.project_layout import default_work_dir, safe_path_is_dir
from otio_app.project_repository import create_project, list_projects
from otio_app.system_checks import run_all_checks

st.set_page_config(
    page_title="OTIO Schnittplaner",
    page_icon="🎬",
    layout="wide",
)

PAGE_NEW = "Neues Projekt"
PAGE_LIST = "Gespeicherte Projekte"
PAGE_STATUS = "Systemstatus"

PENDING_KEY = "pending_project"


def _show_saved_project(saved) -> None:
    st.success(
        f"Projekt '{saved.name}' gespeichert (Status: {saved.status.value})."
    )
    asset_names = saved.asset_subdir_names
    st.json(
        {
            "id": saved.id,
            "project_root": saved.project_root,
            "work_dir": saved.work_dir,
            "voice_over_dir": str(saved.voice_over_dir),
            "asset_subdirs": asset_names,
            "inventory_path": str(saved.inventory_path),
            "voice_analysis_path": str(saved.voice_analysis_path),
            "frames_per_shot": saved.frames_per_shot,
        }
    )


def _save_pending_project() -> None:
    raw = st.session_state.pop(PENDING_KEY, None)
    if raw is None:
        return
    project_data = ProjectCreate.model_validate(raw)
    work_path = normalize_path(project_data.work_dir)
    if not work_path.exists():
        create_work_dir(work_path)
    saved = create_project(project_data)
    _show_saved_project(saved)


with st.sidebar:
    st.title("OTIO Schnittplaner")
    st.caption("Meilenstein 1 — Projektordner-Workflow")
    page = st.radio(
        "Navigation",
        [PAGE_NEW, PAGE_LIST, PAGE_STATUS],
        label_visibility="collapsed",
    )

if page == PAGE_NEW:
    st.header("Neues Projekt anlegen")

    st.markdown(
        """
        Gib den **Projektordner** an (z. B. `.../USA`). Darin liegen die
        Asset-Unterordner (Grand Canyon, Yellowstone, …) und der Voice-over-Ordner.
        """
    )

    with st.form("project_form"):
        name = st.text_input("Projektname *")
        project_root = st.text_input(
            "Projektordner *",
            help="Hauptordner des Projekts, z. B. /Users/.../USA",
        )
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
        submitted = st.form_submit_button("Projekt speichern")

    if submitted:
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
            st.session_state.pop(PENDING_KEY, None)
            for error in exc.errors():
                st.error(error["msg"])
        else:
            with st.spinner("Projektordner wird geprüft …"):
                asset_names = project_data.asset_subdir_names
            st.subheader("Erkannte Struktur")
            st.write(f"**Asset-Unterordner ({len(asset_names)}):**")
            if asset_names:
                st.write(", ".join(f"`{name}`" for name in asset_names))
            else:
                st.info(
                    "Keine Asset-Unterordner gefunden. "
                    "Bei iCloud-Ordnern ggf. Dateien erst lokal laden."
                )

            voice_dir = project_data.voice_over_dir
            if safe_path_is_dir(voice_dir):
                st.success(f"Voice-over-Ordner gefunden: `{voice_dir}`")
            else:
                st.warning(
                    f"Voice-over-Ordner noch nicht vorhanden: `{voice_dir}` "
                    "(wird später für die Audio-Analyse benötigt)."
                )

            st.caption(
                f"Inventar später: `{project_data.inventory_path}` · "
                f"Voice-over-Analyse später: `{project_data.voice_analysis_path}`"
            )

            work_path = normalize_path(project_data.work_dir)
            if work_path.exists():
                saved = create_project(project_data)
                _show_saved_project(saved)
            else:
                st.session_state[PENDING_KEY] = project_data.model_dump(mode="json")

    if PENDING_KEY in st.session_state:
        pending = ProjectCreate.model_validate(st.session_state[PENDING_KEY])
        work_path = normalize_path(pending.work_dir)
        default_path = default_work_dir(normalize_path(pending.project_root))
        st.warning(f"Der Arbeitsordner existiert noch nicht:\n`{work_path}`")
        if work_path == default_path:
            st.info(
                "Standard-Arbeitsordner `_otio` wird für Cache, Frames und "
                "Zwischenergebnisse verwendet. Originalmedien bleiben unverändert."
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
            asset_names = project.asset_subdir_names
            with st.expander(f"{project.name}  ({project.status.value})"):
                st.write(f"**ID:** `{project.id}`")
                st.write(f"**Projektordner:** `{project.project_root}`")
                st.write(f"**Arbeitsordner:** `{project.work_dir}`")
                st.write(f"**Voice-over:** `{project.voice_over_dir}`")
                st.write(
                    f"**Asset-Unterordner ({len(asset_names)}):** "
                    + (", ".join(f"`{n}`" for n in asset_names) if asset_names else "—")
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

else:
    st.header("Systemstatus")
    for result in run_all_checks():
        icon = "✅" if result.ok else "❌"
        st.subheader(f"{icon} {result.name}")
        st.write(result.message)
        if result.version:
            st.caption(f"Version: {result.version}")
