"""Ein-Klick: Sprache am gleichen Medienordner anlegen und Auto-Lauf starten."""

from __future__ import annotations

import streamlit as st

from otio_app.models import Project
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    clone_project_for_language,
    missing_sibling_languages,
    sibling_project_name,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY
from otio_app.ui.routing import PENDING_SWITCH_URL_PATH_KEY

_AUTO_LAUF_URL_PATH = "auto-lauf"


def render_language_sibling_actions(
    project: Project,
    *,
    siblings: list[Project],
) -> None:
    """Sprach-Buttons sichtbar ohne Details-Expander (Enhanced + Auto-Lauf)."""
    if not project.is_without_voiceover_enhanced:
        return
    others = [
        item
        for item in siblings
        if item.id != project.id and item.project_mode == project.project_mode
    ]
    if others:
        labels = ", ".join(
            f"{normalize_brief_language(item.language)} · {item.name}"
            for item in others
        )
        st.caption(f"Gleicher Ordner schon: {labels}")

    missing = missing_sibling_languages(project, siblings)
    if not missing:
        return

    st.caption(
        "Andere Sprache: gleicher Ordner, Clean Media und Analysen. "
        "Legt das Projekt an und startet den Auto-Lauf."
    )
    place_ok = bool(str(project.video_place or "").strip())
    if not place_ok:
        st.warning(
            "Land / Region fehlt — zuerst nachtragen, dann Sprache + Auto-Lauf."
        )
    columns = st.columns(len(missing))
    for column, lang in zip(columns, missing):
        preview = sibling_project_name(project.name, project.language, lang)
        with column:
            clicked = st.button(
                f"{lang} ▶",
                key=f"lang_sibling_{project.id}_{lang}",
                disabled=not place_ok,
                help=(
                    f"Legt „{preview}“ an und startet den Auto-Lauf. "
                    "Clean Media und Analysen bleiben geteilt."
                ),
                use_container_width=True,
            )
        if clicked:
            _create_and_start(project, lang)


def _create_and_start(project: Project, language: str) -> None:
    try:
        sibling = clone_project_for_language(
            project, language, start_auto_run=True
        )
    except LanguageSiblingError as exc:
        st.error(str(exc))
        return
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state[ACTIVE_PROJECT_KEY] = sibling.id
    st.session_state["workbench_project_id"] = sibling.id
    if hasattr(st, "navigation") and hasattr(st, "switch_page"):
        st.session_state[PENDING_SWITCH_URL_PATH_KEY] = _AUTO_LAUF_URL_PATH
    st.rerun()
