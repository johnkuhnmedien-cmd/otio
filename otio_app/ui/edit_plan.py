"""Streamlit-UI: Schnittplan erstellen und freigeben."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SECTION_OUTRO_SEC,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LABELS,
    GEMINI_MODEL_CHOICES,
)
from otio_app.project_layout import get_otio_export_path, safe_folder_slug
from otio_app.services.edit_plan_builder import (
    EditPlanLocationState,
    EditPlanLocationStatus,
    build_edit_plan,
    load_edit_plan,
    resolve_edit_plan_location_state,
    save_edit_plan,
)
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.edit_plan_rules import (
    list_custom_rules,
    rule_description,
    rule_label,
    validate_shots_against_rules,
)
from otio_app.services.otio_exporter import export_otio_timeline, merge_confirmed_edit_plans
from otio_app.services.otio_export_settings import (
    OtioExportSettings,
    load_otio_export_settings,
    save_otio_export_settings,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.edit_plan_rules_ui import (
    get_edit_plan_rules_for_project,
    render_edit_plan_rules_manager,
)
from otio_app.ui.project_context import (
    render_file_paths,
    render_project_selector,
    render_workflow_progress,
)


def _plan_state_key(project_id: str, folder_name: str) -> str:
    return f"edit_plan_draft_{project_id}_{safe_folder_slug(folder_name)}"


def _folder_state_key(project_id: str) -> str:
    return f"edit_plan_active_folder_{project_id}"


def _get_draft(project_id: str, folder_name: str) -> EditPlanDocument | None:
    raw = st.session_state.get(_plan_state_key(project_id, folder_name))
    if not raw:
        return None
    return EditPlanDocument.model_validate(raw)


def _set_draft(document: EditPlanDocument, folder_name: str) -> None:
    st.session_state[_plan_state_key(document.project_id, folder_name)] = (
        document.model_dump(mode="json")
    )


def _location_state_label(state: EditPlanLocationState) -> str:
    labels = {
        EditPlanLocationState.CONFIRMED: "Abgeschlossen",
        EditPlanLocationState.DRAFT: "In Arbeit",
        EditPlanLocationState.OPEN: "Offen",
    }
    return labels[state]


def _location_state_icon(state: EditPlanLocationState) -> str:
    icons = {
        EditPlanLocationState.CONFIRMED: "✅",
        EditPlanLocationState.DRAFT: "📝",
        EditPlanLocationState.OPEN: "⬜",
    }
    return icons[state]


def _collect_location_statuses(
    project,
    project_id: str,
    mapped_folders: list[str],
) -> list[EditPlanLocationStatus]:
    statuses: list[EditPlanLocationStatus] = []
    for folder_name in mapped_folders:
        saved = load_edit_plan(project, folder_name)
        draft = _get_draft(project_id, folder_name)
        status = resolve_edit_plan_location_state(folder_name, saved, draft)
        statuses.append(
            EditPlanLocationStatus(
                folder_name=folder_name,
                state=status.state,
                shot_count=status.shot_count,
            )
        )
    return statuses


def _folder_select_label(
    project,
    project_id: str,
    folder_name: str,
) -> str:
    saved = load_edit_plan(project, folder_name)
    draft = _get_draft(project_id, folder_name)
    status = resolve_edit_plan_location_state(folder_name, saved, draft)
    return f"{_location_state_icon(status.state)} {folder_name} · {_location_state_label(status.state)}"


def _render_location_progress(project, project_id: str, mapped_folders: list[str]) -> None:
    statuses = _collect_location_statuses(project, project_id, mapped_folders)
    confirmed = [item for item in statuses if item.state == EditPlanLocationState.CONFIRMED]
    drafts = [item for item in statuses if item.state == EditPlanLocationState.DRAFT]
    open_items = [item for item in statuses if item.state == EditPlanLocationState.OPEN]

    st.markdown("**Fortschritt pro Ort**")
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    with metric_col1:
        st.metric("Abgeschlossen", f"{len(confirmed)}/{len(mapped_folders)}")
    with metric_col2:
        st.metric("In Arbeit", len(drafts))
    with metric_col3:
        st.metric("Offen", len(open_items))

    done_col, progress_col, open_col = st.columns(3)
    with done_col:
        st.markdown("**✅ Abgeschlossen**")
        if confirmed:
            for item in confirmed:
                st.success(f"**{item.folder_name}** · {item.shot_count} Shots")
        else:
            st.caption("Noch kein Ort abgeschlossen.")

    with progress_col:
        st.markdown("**📝 In Arbeit**")
        if drafts:
            for item in drafts:
                st.info(f"**{item.folder_name}** · {item.shot_count} Shots · noch nicht bestätigt")
        else:
            st.caption("Keine Entwürfe.")

    with open_col:
        st.markdown("**⬜ Noch offen**")
        if open_items:
            for item in open_items:
                st.warning(f"**{item.folder_name}** · noch kein Schnittplan")
        else:
            st.caption("Alle Orte haben mindestens einen Entwurf.")

    if len(confirmed) == len(mapped_folders):
        st.success("Alle Orte abgeschlossen — Schnittplan für das gesamte Projekt fertig.")


def _edit_plan_tab_key(project_id: str) -> str:
    return f"edit_plan_active_tab_{project_id}"


TAB_RULES = "⚙️ Regeln"
TAB_GENERATE = "▶️ Vorschlag"
TAB_REVIEW = "✅ Prüfen & Speichern"
TAB_EXPORT = "📤 OTIO Export"
EDIT_PLAN_TABS = (TAB_RULES, TAB_GENERATE, TAB_REVIEW, TAB_EXPORT)


def _plan_number_setting(project_id: str, suffix: str, default: float) -> float:
    return float(st.session_state.get(f"plan_{suffix}_{project_id}", default))


def _plan_text_setting(project_id: str, suffix: str, default: str) -> str:
    return str(st.session_state.get(f"plan_{suffix}_{project_id}", default))


def _plan_gemini_model(project_id: str) -> str:
    default_model = get_default_gemini_model()
    return str(st.session_state.get(f"plan_gemini_{project_id}", default_model))


def _render_tab_settings(project) -> None:
    render_edit_plan_rules_manager(project)
    st.divider()
    st.markdown("**Timing & Gemini**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.number_input(
            "Min. Shot (Sek.)",
            value=float(DEFAULT_SHOT_MIN_SEC),
            min_value=1.0,
            max_value=30.0,
            step=0.5,
            key=f"plan_min_{project.id}",
        )
    with col2:
        st.number_input(
            "Max. Shot (Sek.)",
            value=float(DEFAULT_SHOT_MAX_SEC),
            min_value=1.0,
            max_value=60.0,
            step=0.5,
            key=f"plan_max_{project.id}",
        )
    with col3:
        st.number_input(
            "Audio-Start (+Sek.)",
            value=float(DEFAULT_AUDIO_OFFSET_SEC),
            min_value=0.0,
            max_value=10.0,
            step=0.5,
            key=f"plan_offset_{project.id}",
            help="Voice-over startet so viele Sekunden nach dem ersten Asset eines Ordners.",
        )
    with col4:
        st.number_input(
            "Ordner-Ausklingen (Sek.)",
            value=float(DEFAULT_SECTION_OUTRO_SEC),
            min_value=0.0,
            max_value=30.0,
            step=0.5,
            key=f"plan_outro_{project.id}",
            help="Letztes Asset eines Ordners bleibt auf der Timeline so viele Sekunden länger.",
        )

    st.text_input(
        "Text-Trenner (kommagetrennt)",
        value=", und ,, , und ",
        key=f"plan_split_{project.id}",
    )
    st.caption("Fallback-Reihenfolge (Adobe Stock / Pexels / KI folgen später):")
    for source in DEFAULT_FALLBACK_ORDER:
        st.write(f"- {FALLBACK_SOURCE_LABELS.get(source, source)}")

    default_model = get_default_gemini_model()
    st.selectbox(
        "Gemini-Modell (Motiv → Asset)",
        options=list(GEMINI_MODEL_CHOICES),
        index=list(GEMINI_MODEL_CHOICES).index(default_model),
        format_func=format_gemini_model_label,
        key=f"plan_gemini_{project.id}",
    )


def _render_tab_generate(project, selected_folder: str, saved: EditPlanDocument | None) -> None:
    st.markdown(
        f"Vorschlag für **{selected_folder}** — Gemini erhält **nur Text** (Whisper) "
        "+ **Asset-Beschreibungen** und schlägt Shots vor."
    )
    if not is_gemini_configured():
        st.warning("Ohne GEMINI_API_KEY wird nur eine einfache Text-Trennung genutzt.")

    splitters = _plan_text_setting(project.id, "split", ", und ,, , und ")
    if st.button("Schnittplan vorschlagen", key=f"build_plan_{project.id}", type="primary"):
        use_gemini = is_gemini_configured()
        settings = EditPlanSettings(
            shot_min_sec=_plan_number_setting(project.id, "min", DEFAULT_SHOT_MIN_SEC),
            shot_max_sec=_plan_number_setting(project.id, "max", DEFAULT_SHOT_MAX_SEC),
            audio_offset_sec=_plan_number_setting(project.id, "offset", DEFAULT_AUDIO_OFFSET_SEC),
            section_outro_sec=_plan_number_setting(project.id, "outro", DEFAULT_SECTION_OUTRO_SEC),
            text_splitters=[
                piece.strip()
                for piece in splitters.split(",")
                if piece.strip()
            ],
            fallback_order=list(DEFAULT_FALLBACK_ORDER),
            gemini_model=_plan_gemini_model(project.id),
        )
        try:
            with st.spinner(f"Schnittplan für {selected_folder} wird erstellt…"):
                document = build_edit_plan(
                    project,
                    settings,
                    use_api=use_gemini,
                    folder_names=[selected_folder],
                )
            _set_draft(document, selected_folder)
            st.success(f"{len(document.shots)} Shots vorgeschlagen.")
            st.rerun()
        except (GeminiNotConfiguredError, ValueError, FileNotFoundError) as exc:
            st.error(str(exc))

    draft = _get_draft(project.id, selected_folder) or saved
    if draft is not None:
        rules_doc = get_edit_plan_rules_for_project(project)
        missing = sum(1 for shot in draft.shots if shot.asset_source == "missing")
        violations = validate_shots_against_rules(draft.shots, rules_doc)
        st.caption(
            f"{len(draft.shots)} Shots · {missing} ohne passendes lokales Asset"
        )
        if violations:
            st.warning("Regelverletzungen — ggf. unter „Regeln“ anpassen und neu generieren:")
            for line in violations[:15]:
                st.caption(f"• {line}")
            if len(violations) > 15:
                st.caption(f"… und {len(violations) - 15} weitere")


def _render_tab_review(
    project,
    selected_folder: str,
    saved: EditPlanDocument | None,
    plan_path: Path,
) -> None:
    draft = _get_draft(project.id, selected_folder) or saved
    if draft is None or not draft.shots:
        st.info(
            f"Noch kein Vorschlag für **{selected_folder}** — "
            "zuerst unter „Vorschlag“ generieren."
        )
        return

    st.markdown(
        f"**{selected_folder}** · {len(draft.shots)} Shots "
        f"— Audio-Offset: {draft.settings.audio_offset_sec}s"
    )
    rules_doc = get_edit_plan_rules_for_project(project)
    violations = validate_shots_against_rules(draft.shots, rules_doc)
    custom_rules = list_custom_rules(rules_doc, enabled_only=True)
    if custom_rules:
        st.markdown("**Deine Regeln (Checkliste)**")
        for rule in custom_rules:
            st.caption(f"• **{rule_label(rule)}** — {rule_description(rule)}")
    if violations:
        st.warning(f"{len(violations)} Regelverletzung(en) im aktuellen Vorschlag.")
    for index, shot in enumerate(draft.shots):
        icon = "🟢" if shot.asset_path else "🟡"
        with st.expander(
            f"{icon} Shot {index + 1} · {shot.folder} · {shot.duration_sec:.1f}s",
            expanded=index < 2,
        ):
            st.write(f"**Motiv:** {shot.motif or '—'}")
            st.write(f"**Voice:** {shot.voice_start_sec:.1f}–{shot.voice_end_sec:.1f}s")
            st.caption(shot.passage_text)
            if shot.asset_path:
                st.write(f"**Asset:** `{Path(shot.asset_path).name}`")
            else:
                st.warning("Kein lokales Asset — Fallback folgt später (Adobe Stock / Pexels / KI).")

    confirm = st.checkbox(
        f"Schnittplan für {selected_folder} geprüft und bestätigt",
        key=f"confirm_plan_{project.id}_{safe_folder_slug(selected_folder)}",
    )
    if st.button(
        "Schnittplan speichern",
        key=f"save_plan_{project.id}_{safe_folder_slug(selected_folder)}",
        type="primary",
    ):
        if not confirm:
            st.warning("Bitte bestätigen.")
        else:
            confirmed = draft.model_copy(update={"confirmed": True, "folder_name": selected_folder})
            for shot in confirmed.shots:
                if not shot.asset_path:
                    shot.asset_source = "missing"
            save_edit_plan(project, confirmed, selected_folder)
            _set_draft(confirmed, selected_folder)
            st.success(f"Gespeichert: `{plan_path}`")
            st.rerun()

    with st.expander("JSON-Vorschau", expanded=False):
        st.code(draft.model_dump_json(indent=2)[:6000])


def _render_tab_export(project, mapped_folders: list[str]) -> None:
    default_export_path = get_otio_export_path(project.work_dir_path, project.name)
    saved_export_settings = load_otio_export_settings(project)
    st.markdown("**OTIO-Timeline aus bestätigten Schnittplänen**")
    st.caption(
        "Orte werden in Voice-over-Reihenfolge zusammengeführt. "
        f"Export nach `{default_export_path}` · "
        f"Einstellungen in `{project.work_dir_path / 'otio_export_settings.json'}`"
    )

    export_folders = st.multiselect(
        "Orte exportieren (leer = alle bestätigten)",
        options=mapped_folders,
        default=[
            folder_name
            for folder_name in mapped_folders
            if (plan := load_edit_plan(project, folder_name)) is not None and plan.confirmed
        ],
        key=f"otio_export_folders_{project.id}",
    )

    preview = merge_confirmed_edit_plans(
        project,
        folder_names=export_folders or None,
    )
    if preview.included_folders:
        st.success(
            "Enthalten: "
            + ", ".join(f"**{name}**" for name in preview.included_folders)
            + f" · **{len(preview.shots)}** Shots"
        )
    if preview.skipped_folders:
        st.warning(
            "Noch nicht bestätigt: "
            + ", ".join(f"`{name}`" for name in preview.skipped_folders)
        )
    for warning in preview.warnings:
        st.caption(f"• {warning}")

    if not preview.ready:
        st.info(
            "Export noch nicht möglich — wähle mindestens einen **bestätigten** Ort "
            "oder bestätige Schnittpläne unter „Prüfen & Speichern“."
        )

    if st.button(
        "📤 OTIO-Timeline exportieren",
        key=f"export_otio_{project.id}",
        type="primary",
        disabled=not preview.ready,
        use_container_width=True,
    ):
        export_audio_offset = float(
            st.session_state.get(f"export_audio_offset_{project.id}", saved_export_settings.audio_offset_sec)
        )
        export_section_outro = float(
            st.session_state.get(
                f"export_section_outro_{project.id}",
                saved_export_settings.section_outro_sec,
            )
        )
        try:
            export_settings = OtioExportSettings(
                audio_offset_sec=export_audio_offset,
                section_outro_sec=export_section_outro,
            )
            save_otio_export_settings(project, export_settings)
            export_path = export_otio_timeline(
                project,
                preview,
                export_settings=export_settings,
            )
            st.success(f"Timeline exportiert: `{export_path}`")
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    st.divider()
    timing_col1, timing_col2 = st.columns(2)
    with timing_col1:
        export_audio_offset = st.number_input(
            "Audio-Start je Ordner (+Sek.)",
            min_value=0.0,
            max_value=30.0,
            step=0.5,
            value=float(saved_export_settings.audio_offset_sec),
            key=f"export_audio_offset_{project.id}",
            help="Nächstes Voice-over startet so viele Sekunden nach dem ersten Asset des Ordners.",
        )
    with timing_col2:
        export_section_outro = st.number_input(
            "Ordner-Ausklingen (Sek.)",
            min_value=0.0,
            max_value=60.0,
            step=0.5,
            value=float(saved_export_settings.section_outro_sec),
            key=f"export_section_outro_{project.id}",
            help="Letztes Asset eines Ordners wird auf der Timeline verlängert (Ausklingen).",
        )

    if preview.ready:
        export_cfg = OtioExportSettings(
            audio_offset_sec=float(export_audio_offset),
            section_outro_sec=float(export_section_outro),
        )
        from otio_app.services.otio_exporter import _compute_timeline_sections

        timeline_sections = _compute_timeline_sections(
            preview.shots,
            preview.settings.model_copy(
                update={
                    "audio_offset_sec": export_cfg.audio_offset_sec,
                    "section_outro_sec": export_cfg.section_outro_sec,
                }
            ),
        )
        total_duration = sum(section.video_duration_sec for section in timeline_sections)
        st.caption(
            f"Geschätzte Videospur: {total_duration:.1f}s · "
            f"Audio-Start: {export_cfg.audio_offset_sec}s · "
            f"Ausklingen: {export_cfg.section_outro_sec}s · "
            f"{project.fps} fps"
        )
        for section in timeline_sections:
            st.caption(
                f"• **{section.folder}** — Video ab {section.video_start_sec:.1f}s "
                f"({section.video_duration_sec:.1f}s), Voice ab {section.voice_start_sec:.1f}s"
            )

    st.markdown("**In Resolve / Premiere / OTIO**")
    st.caption(
        "Video (**V1**) startet bei 0. Pro Voice-over-Datei eine **eigene Audiospur** — "
        "Originaldatei ab Sekunde 0, **nicht** pro Shot geschnitten. "
        "Die Länge pro Abschnitt verhindert Überlappungen (keine Verzerrung durch mehrere VO gleichzeitig). "
        "In DaVinci Resolve: **File → Import → Timeline → OpenTimelineIO**."
    )


def render_edit_plan_page() -> None:
    st.header("③ Schnittplan")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="edit_plan")

    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        st.warning("Bitte zuerst unter „② Zuordnung“ die Voice-over-Zuordnung bestätigen.")
        render_file_paths(project)
        return

    mapped_folders = sorted(
        {entry.folder for entry in mapping.entries if entry.folder and entry.confirmed}
    )
    if not mapped_folders:
        st.warning("Keine bestätigten Voice-over-Zuordnungen zu Asset-Ordnern.")
        render_file_paths(project)
        return

    folder_key = _folder_state_key(project.id)
    default_folder = st.session_state.get(folder_key, mapped_folders[0])
    if default_folder not in mapped_folders:
        default_folder = mapped_folders[0]

    _render_location_progress(project, project.id, mapped_folders)
    st.divider()

    st.markdown("**Ort bearbeiten**")
    selected_folder = st.selectbox(
        "Asset-Ordner",
        options=mapped_folders,
        index=mapped_folders.index(default_folder),
        format_func=lambda folder_name: _folder_select_label(project, project.id, folder_name),
        key=f"plan_folder_select_{project.id}",
        label_visibility="collapsed",
    )
    st.session_state[folder_key] = selected_folder

    plan_path = project.folder_edit_plan_path(selected_folder)
    st.caption(f"Speicherort: `{plan_path}`")

    saved = load_edit_plan(project, selected_folder)
    if saved is not None and saved.confirmed:
        st.success(f"Schnittplan für **{selected_folder}** bestätigt.")

    active_tab = st.radio(
        "Schnittplan-Schritt",
        options=EDIT_PLAN_TABS,
        horizontal=True,
        key=_edit_plan_tab_key(project.id),
        label_visibility="collapsed",
    )
    st.divider()

    with st.container(key=f"edit-plan-panel-{project.id}"):
        if active_tab == TAB_RULES:
            _render_tab_settings(project)
        elif active_tab == TAB_GENERATE:
            _render_tab_generate(project, selected_folder, saved)
        elif active_tab == TAB_REVIEW:
            _render_tab_review(project, selected_folder, saved, plan_path)
        elif active_tab == TAB_EXPORT:
            _render_tab_export(project, mapped_folders)

    render_file_paths(project)
