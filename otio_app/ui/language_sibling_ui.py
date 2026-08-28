"""Gespeicherte Projekte: eine Karte je Medienordner, Auto-Lauf je Sprache."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES, PROJECT_MODE_LABELS
from otio_app.models import Project
from otio_app.project_repository import update_project_video_place
from otio_app.services.language_auto_run_queue import (
    LanguageAutoRunQueueBusyError,
    get_language_auto_run_queue_manager,
)
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    SavedProjectGroup,
    family_language_statuses,
    selected_languages_in_order,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    get_enhanced_auto_run_job_manager,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AUTO_RUN_STOP_AFTER_FUNNEL,
    AUTO_RUN_STOP_AFTER_YOUTUBE,
)
from otio_app.ui.active_project_session import set_active_project_id
from otio_app.ui.navigation import PAGE_ANALYSIS
from otio_app.ui.polling import poll_while_running
from otio_app.ui.routing import PENDING_SWITCH_URL_PATH_KEY


def _pick_checkbox_key(family_id: str, language: str) -> str:
    return f"lang_queue_pick_{family_id}_{language}"


def render_enhanced_saved_family(group: SavedProjectGroup) -> None:
    """Eine Karte für alle Enhanced-Sprachen desselben Medienordners."""
    representative = group.representative
    siblings = list(group.projects)
    langs = sorted(
        {normalize_brief_language(item.language) for item in siblings}
    )
    st.markdown(
        f"**{group.display_name}** · `{representative.status.value}` · "
        f"{len(siblings)} Sprache{'n' if len(siblings) != 1 else ''} "
        f"({', '.join(langs)}) · "
        f"{PROJECT_MODE_LABELS[group.project_mode.value]}"
    )
    st.caption(
        f"Gemeinsamer Ordner: `{representative.project_root}` · "
        f"Land/Region: {representative.video_place or '—'}"
    )
    _render_language_stage_table(siblings)
    st.caption(
        "„anlegen“ heißt: diese Sprache ist am Ordner noch kein Projekt. "
        "Die globalen Sprach-Standards (Brief, Stimme, Cut Plan …) unter `data/` "
        "bleiben davon unberührt."
    )
    _render_open_language_buttons(siblings, family_id=representative.id)
    render_language_sibling_actions(representative, siblings=siblings)
    _render_family_details(group)


def render_language_sibling_actions(
    project: Project,
    *,
    siblings: list[Project],
) -> None:
    """Sprachen wählen und Auto-Lauf bis Funnel oder YouTube starten."""
    if not project.is_without_voiceover_enhanced:
        return

    family_id = project.id
    catalog = list(BRIEF_LANGUAGE_CHOICES)
    queue_manager = get_language_auto_run_queue_manager()
    auto_manager = get_enhanced_auto_run_job_manager()
    queue_state = queue_manager.get_state_for_projects(siblings) or queue_manager.get_state(
        project.id
    )
    queue_running = queue_state is not None and queue_state.status == "running"
    jobs_busy = (not queue_running) and (
        queue_manager.any_running() or auto_manager.any_running()
    )

    place_ok = any(str(item.video_place or "").strip() for item in siblings)
    if not place_ok:
        st.warning(
            "Land / Region fehlt — zuerst nachtragen, dann Sprache + Auto-Lauf."
        )

    if queue_state is not None:
        _render_queue_status(
            queue_state.source_project_id,
            queue_state,
            queue_manager,
            auto_manager,
        )

    if jobs_busy and not queue_running:
        st.caption(
            "Ein Auto-Lauf oder die Sprachen-Queue läuft bereits — "
            "erst fertig werden oder stoppen."
        )

    if queue_running:
        return

    st.caption(
        "Sprachen wählen, dann nacheinander (nie parallel) starten. "
        "Fehlende Projekte werden angelegt, unfertige mit skip-done fortgesetzt. "
        "Eine Sprache darf scheitern — die Queue macht mit der nächsten weiter. "
        "**SFX** bleibt manuell."
    )
    pending_key = f"lang_queue_pick_pending_{family_id}"
    pending_picks = st.session_state.pop(pending_key, None)
    if isinstance(pending_picks, dict):
        for lang, checked in pending_picks.items():
            st.session_state[_pick_checkbox_key(family_id, lang)] = bool(checked)

    pick_columns = st.columns(len(catalog))
    picked: list[str] = []
    for column, lang in zip(pick_columns, catalog):
        with column:
            checked = st.checkbox(
                lang,
                key=_pick_checkbox_key(family_id, lang),
                help=_language_pick_help(lang, siblings),
            )
        if checked:
            picked.append(lang)
    selected = selected_languages_in_order(catalog, picked)

    action_cols = st.columns([1, 1, 2, 2])
    with action_cols[0]:
        if st.button(
            "Alle",
            key=f"lang_queue_pick_all_{family_id}",
            disabled=jobs_busy,
            help="Alle Katalog-Sprachen ankreuzen.",
            use_container_width=True,
        ):
            st.session_state[pending_key] = {lang: True for lang in catalog}
            st.rerun()
    with action_cols[1]:
        if st.button(
            "Keine",
            key=f"lang_queue_pick_none_{family_id}",
            disabled=jobs_busy,
            help="Auswahl leeren.",
            use_container_width=True,
        ):
            st.session_state[pending_key] = {lang: False for lang in catalog}
            st.rerun()
    start_disabled = (not place_ok) or jobs_busy or not selected
    with action_cols[2]:
        help_funnel = _start_help(
            selected,
            place_ok=place_ok,
            jobs_busy=jobs_busy,
            until="Supplement-Funnel",
        )
        if st.button(
            f"▶ bis Funnel ({len(selected)})",
            key=f"lang_queue_start_funnel_{family_id}",
            disabled=start_disabled,
            help=help_funnel,
            use_container_width=True,
        ):
            _start_open_language_queue(
                project, selected, stop_after=AUTO_RUN_STOP_AFTER_FUNNEL
            )
    with action_cols[3]:
        help_youtube = _start_help(
            selected,
            place_ok=place_ok,
            jobs_busy=jobs_busy,
            until="YouTube Publish",
        )
        if st.button(
            f"▶ bis YouTube ({len(selected)})",
            key=f"lang_queue_start_youtube_{family_id}",
            type="primary",
            disabled=start_disabled,
            help=help_youtube,
            use_container_width=True,
        ):
            _start_open_language_queue(
                project, selected, stop_after=AUTO_RUN_STOP_AFTER_YOUTUBE
            )


def _language_pick_help(language: str, siblings: list[Project]) -> str:
    existing = {
        normalize_brief_language(item.language): item for item in siblings
    }.get(language)
    if existing is None:
        return f"Noch kein Projekt — legt {language} an und startet den Auto-Lauf."
    return f"Vorhanden ({existing.name}) — setzt mit skip-done fort."


def _start_help(
    selected: list[str],
    *,
    place_ok: bool,
    jobs_busy: bool,
    until: str,
) -> str:
    if not place_ok:
        return "Land / Region fehlt."
    if jobs_busy:
        return "Ein Auto-Lauf oder die Sprachen-Queue läuft bereits."
    if not selected:
        return "Zuerst Sprachen ankreuzen."
    return (
        f"Bis {until}. Reihenfolge: "
        + ", ".join(selected)
        + ". Bleibt auf Gespeicherte Projekte."
    )


def _render_language_stage_table(siblings: list[Project]) -> None:
    rows = family_language_statuses(siblings)
    st.markdown("**Stand je Sprache**")
    lines = [
        "| Sprache | Angelegt | Stand | Nächster Schritt | Funnel | YouTube |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        planted = (
            f"ja · `{row.project_name}`" if row.exists else "nein"
        )
        stand = (
            f"{row.last_done_label} ({row.done_count}/{row.step_total})"
            if row.exists
            else "—"
        )
        lines.append(
            f"| {row.language} | {planted} | {stand} | {row.next_label} | "
            f"{'✓' if row.funnel_done else '—'} | "
            f"{'✓' if row.youtube_done else '—'} |"
        )
    st.markdown("\n".join(lines))


def _render_open_language_buttons(siblings: list[Project], *, family_id: str) -> None:
    if not siblings:
        return
    ordered = sorted(
        siblings,
        key=lambda item: normalize_brief_language(item.language),
    )
    st.caption("Sprache öffnen (Analysen / Auto-Lauf dieser Sprache):")
    columns = st.columns(max(len(ordered), 1))
    for column, item in zip(columns, ordered):
        lang = normalize_brief_language(item.language)
        with column:
            if st.button(
                f"{lang} öffnen",
                key=f"lang_family_open_{family_id}_{item.id}",
                use_container_width=True,
            ):
                _open_project(item, url_path="analysen")


def _render_family_details(group: SavedProjectGroup) -> None:
    representative = group.representative
    with st.expander("Details"):
        st.write(f"**Anzeigename:** {group.display_name}")
        st.write(f"**Projektmodus:** {PROJECT_MODE_LABELS[group.project_mode.value]}")
        st.write(f"**Projektordner:** `{representative.project_root}`")
        st.write(f"**Arbeitsordner:** `{representative.work_dir}`")
        st.write(
            "**Sprachen / IDs:** "
            + ", ".join(
                f"{normalize_brief_language(item.language)} `{item.id}` ({item.name})"
                for item in group.projects
            )
        )
        st.write(
            f"**Gefundene Ordner ({len(representative.asset_subdir_names)}):** "
            + (
                ", ".join(f"`{n}`" for n in representative.asset_subdir_names)
                if representative.asset_subdir_names
                else "—"
            )
        )
        st.write(
            f"**Zu bearbeiten ({len(representative.selected_asset_subdirs)}):** "
            + (
                ", ".join(f"`{n}`" for n in representative.selected_asset_subdirs)
                if representative.selected_asset_subdirs
                else "—"
            )
        )
        place_key = f"list_video_place_family_{representative.id}"
        if place_key not in st.session_state:
            st.session_state[place_key] = representative.video_place
        edited_place = st.text_input(
            "Land / Region nachtragen (alle Sprachen dieses Ordners)",
            key=place_key,
            help="Wird auf alle Sprachprojekte dieses Medienordners geschrieben.",
        )
        if st.button(
            "Land / Region speichern",
            key=f"save_video_place_family_{representative.id}",
        ):
            if not edited_place.strip():
                st.error("Land / Region darf nicht leer sein.")
            else:
                for item in group.projects:
                    update_project_video_place(item.id, edited_place)
                st.success("Land / Region für alle Sprachen gespeichert.")
                st.rerun()
        st.caption(
            f"Erstellt: {representative.created_at.isoformat()} · "
            f"Aktualisiert: {representative.updated_at.isoformat()}"
        )


def _render_queue_status(project_id, queue_state, queue_manager, auto_manager) -> None:
    if queue_state.status == "running":
        until = (
            "Supplement-Funnel"
            if queue_state.stop_after == AUTO_RUN_STOP_AFTER_FUNNEL
            else "YouTube Publish"
        )

        def _body() -> None:
            state = queue_manager.get_state(project_id) or queue_state
            total = len(state.languages) or 1
            done = len(state.completed_languages)
            extra = " **(Stop angefordert …)**" if state.cancel_requested else ""
            current = state.current_language or "—"
            col_info, col_stop = st.columns([5, 1])
            with col_info:
                st.info(
                    f"▶ Sprachen-Queue sequenziell bis {until} — {current} "
                    f"({state.current_index + 1}/{total}, {done} fertig"
                    + (
                        f", {len(state.failed_languages)} Fehler"
                        if state.failed_languages
                        else ""
                    )
                    + f"){extra}"
                )
                st.progress(min(1.0, max(0.0, done / total)))
                if state.current_project_id:
                    auto_state = auto_manager.get_state(state.current_project_id)
                    if auto_state is not None:
                        st.caption(
                            auto_state.message
                            or auto_state.step_label
                            or "Auto-Lauf läuft…"
                        )
            with col_stop:
                if st.button(
                    "⏹ Stoppen",
                    key=f"lang_queue_stop_{project_id}",
                    disabled=state.cancel_requested,
                ):
                    queue_manager.request_cancel(project_id)
                    st.rerun()

        poll_while_running(
            _body,
            lambda: queue_manager.is_running(project_id),
            refresh_key=f"lang_queue_refresh_{project_id}",
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
        failed = ", ".join(queue_state.failed_languages)
        until = (
            "Supplement-Funnel"
            if queue_state.stop_after == AUTO_RUN_STOP_AFTER_FUNNEL
            else "YouTube"
        )
        if queue_state.failed_languages:
            st.warning(
                f"Sprachen-Queue bis {until} durchgelaufen. Fertig: {finished}. "
                f"Fehler (weitergemacht): {failed}. "
                f"{queue_state.error or ''}".strip()
            )
        else:
            st.success(f"Sprachen-Queue bis {until} fertig ({finished}).")
    if st.button(
        "Hinweis schließen",
        key=f"lang_queue_dismiss_{project_id}",
    ):
        queue_manager.dismiss(project_id)
        st.rerun()


def _start_open_language_queue(
    project: Project,
    languages: list[str],
    *,
    stop_after: str,
) -> None:
    try:
        get_language_auto_run_queue_manager().start(
            project,
            languages,
            stop_after=stop_after,
        )
    except (LanguageSiblingError, LanguageAutoRunQueueBusyError, ValueError) as exc:
        st.error(str(exc))
        return
    st.rerun()


def _open_project(project: Project, *, url_path: str) -> None:
    set_active_project_id(project.id)
    st.session_state["workbench_project_id"] = project.id
    if hasattr(st, "navigation") and hasattr(st, "switch_page"):
        st.session_state[PENDING_SWITCH_URL_PATH_KEY] = url_path
    else:
        st.session_state["sidebar_nav"] = PAGE_ANALYSIS
    st.rerun()
