"""Ein-Klick: Sprache am gleichen Medienordner anlegen und Auto-Lauf starten."""

from __future__ import annotations

import streamlit as st

from otio_app.models import Project
from otio_app.services.language_auto_run_queue import (
    LanguageAutoRunQueueBusyError,
    get_language_auto_run_queue_manager,
)
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    clone_project_for_language,
    missing_sibling_languages,
    open_languages_for_auto_run,
    selected_languages_in_order,
    sibling_project_name,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    get_enhanced_auto_run_job_manager,
)
from otio_app.ui.active_project_session import set_active_project_id
from otio_app.ui.polling import poll_while_running
from otio_app.ui.routing import PENDING_SWITCH_URL_PATH_KEY

_AUTO_LAUF_URL_PATH = "auto-lauf"


def _pick_checkbox_key(project_id: str, language: str) -> str:
    return f"lang_queue_pick_{project_id}_{language}"


def _language_pick_help(project: Project, language: str, missing: list[str]) -> str:
    preview = sibling_project_name(project.name, project.language, language)
    if language in missing:
        return f"Noch kein Projekt — legt „{preview}“ an und startet den Auto-Lauf."
    return f"Vorhanden, Auto-Lauf noch offen — setzt „{preview}“ mit skip-done fort."


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
    open_langs = open_languages_for_auto_run(project, siblings)
    queue_manager = get_language_auto_run_queue_manager()
    auto_manager = get_enhanced_auto_run_job_manager()
    queue_state = queue_manager.get_state(project.id)
    queue_running = queue_state is not None and queue_state.status == "running"
    jobs_busy = (not queue_running) and (
        queue_manager.any_running() or auto_manager.any_running()
    )

    if not missing and not open_langs and queue_state is None:
        return

    place_ok = bool(str(project.video_place or "").strip())
    if not place_ok and (missing or open_langs):
        st.warning(
            "Land / Region fehlt — zuerst nachtragen, dann Sprache + Auto-Lauf."
        )

    if queue_state is not None:
        _render_queue_status(project, queue_state, queue_manager, auto_manager)

    if jobs_busy and not queue_running:
        st.caption(
            "Ein Auto-Lauf oder die Sprachen-Queue läuft bereits — "
            "erst fertig werden oder stoppen."
        )

    if open_langs and not queue_running:
        st.caption(
            "Sprachen wählen, dann nacheinander (nie parallel). "
            "Fehlende Projekte werden angelegt, unfertige fortgesetzt. "
            "Stoppt bei Fehler, vor Funnel."
        )
        pending_key = f"lang_queue_pick_pending_{project.id}"
        pending_picks = st.session_state.pop(pending_key, None)
        if isinstance(pending_picks, dict):
            for lang, checked in pending_picks.items():
                st.session_state[_pick_checkbox_key(project.id, lang)] = bool(checked)

        pick_columns = st.columns(len(open_langs))
        picked: list[str] = []
        for column, lang in zip(pick_columns, open_langs):
            with column:
                checked = st.checkbox(
                    lang,
                    key=_pick_checkbox_key(project.id, lang),
                    help=_language_pick_help(project, lang, missing),
                )
            if checked:
                picked.append(lang)
        selected = selected_languages_in_order(open_langs, picked)

        action_cols = st.columns([1, 1, 3])
        with action_cols[0]:
            if st.button(
                "Alle",
                key=f"lang_queue_pick_all_{project.id}",
                disabled=jobs_busy,
                help="Offene Sprachen ankreuzen.",
                use_container_width=True,
            ):
                st.session_state[pending_key] = {lang: True for lang in open_langs}
                st.rerun()
        with action_cols[1]:
            if st.button(
                "Keine",
                key=f"lang_queue_pick_none_{project.id}",
                disabled=jobs_busy,
                help="Auswahl leeren.",
                use_container_width=True,
            ):
                st.session_state[pending_key] = {lang: False for lang in open_langs}
                st.rerun()
        with action_cols[2]:
            start_disabled = (not place_ok) or jobs_busy or not selected
            help_text = (
                "Reihenfolge: "
                + ", ".join(selected)
                + ". Bleibt auf Gespeicherte Projekte; Fortschritt mit Aktualisieren holen."
            )
            if not place_ok:
                help_text = "Land / Region fehlt."
            elif jobs_busy:
                help_text = "Ein Auto-Lauf oder die Sprachen-Queue läuft bereits."
            elif not selected:
                help_text = "Zuerst Sprachen ankreuzen."
            if st.button(
                f"Gewählte Sprachen ▶ ({len(selected)})",
                key=f"lang_queue_start_{project.id}",
                type="primary",
                disabled=start_disabled,
                help=help_text,
                use_container_width=True,
            ):
                _start_open_language_queue(project, selected)

    if not missing:
        return

    st.caption(
        "Einzelne Sprache: gleicher Ordner, Clean Media und Analysen. "
        "Legt das Projekt an und startet den Auto-Lauf."
    )
    columns = st.columns(len(missing))
    for column, lang in zip(columns, missing):
        preview = sibling_project_name(project.name, project.language, lang)
        with column:
            clicked = st.button(
                f"{lang} ▶",
                key=f"lang_sibling_{project.id}_{lang}",
                disabled=(not place_ok) or jobs_busy or queue_running,
                help=(
                    f"Legt „{preview}“ an und startet den Auto-Lauf. "
                    "Clean Media und Analysen bleiben geteilt."
                ),
                use_container_width=True,
            )
        if clicked:
            _create_and_start(project, lang)


def _render_queue_status(project, queue_state, queue_manager, auto_manager) -> None:
    total = len(queue_state.languages) or 1
    done = len(queue_state.completed_languages)
    if queue_state.status == "running":
        extra = " **(Stop angefordert …)**" if queue_state.cancel_requested else ""
        current = queue_state.current_language or "—"
        col_info, col_stop = st.columns([5, 1])
        with col_info:
            st.info(
                f"▶ Sprachen-Queue sequenziell — {current} "
                f"({queue_state.current_index + 1}/{total}, {done} fertig){extra}"
            )
            st.progress(min(1.0, max(0.0, done / total)))
            if queue_state.current_project_id:
                auto_state = auto_manager.get_state(queue_state.current_project_id)
                if auto_state is not None:
                    st.caption(
                        auto_state.message
                        or auto_state.step_label
                        or "Auto-Lauf läuft…"
                    )
        with col_stop:
            if st.button(
                "⏹ Stoppen",
                key=f"lang_queue_stop_{project.id}",
                disabled=queue_state.cancel_requested,
            ):
                queue_manager.request_cancel(project.id)
                st.rerun()
        poll_while_running(
            lambda: None,
            lambda: queue_manager.is_running(project.id),
            refresh_key=f"lang_queue_refresh_{project.id}",
        )
        return

    if queue_state.status == "cancelled":
        st.warning(queue_state.error or "Sprachen-Queue gestoppt.")
    elif queue_state.status == "failed":
        failed = queue_state.failed_language or "unbekannte Sprache"
        st.error(
            f"Sprachen-Queue abgebrochen bei {failed}: "
            f"{queue_state.error or 'Unbekannt'}"
        )
    elif queue_state.status == "completed":
        finished = ", ".join(queue_state.completed_languages) or "keine"
        st.success(
            f"Sprachen-Queue fertig ({finished}). "
            "Als Nächstes manuell: Funnel je Sprache."
        )
    if st.button(
        "Hinweis schließen",
        key=f"lang_queue_dismiss_{project.id}",
    ):
        queue_manager.dismiss(project.id)
        st.rerun()


def _start_open_language_queue(project: Project, languages: list[str]) -> None:
    try:
        get_language_auto_run_queue_manager().start(project, languages)
    except (LanguageSiblingError, LanguageAutoRunQueueBusyError, ValueError) as exc:
        st.error(str(exc))
        return
    st.rerun()


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
    set_active_project_id(sibling.id)
    st.session_state["workbench_project_id"] = sibling.id
    if hasattr(st, "navigation") and hasattr(st, "switch_page"):
        st.session_state[PENDING_SWITCH_URL_PATH_KEY] = _AUTO_LAUF_URL_PATH
    st.rerun()
