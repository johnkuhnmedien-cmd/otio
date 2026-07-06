"""Streamlit-UI: Schnittplan erstellen und freigeben."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, EditPlanShot, TimelineItem, VoiceoverPlan
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
from otio_app.services.edit_plan_cache import collect_folder_statuses
from otio_app.services.edit_plan_builder import (
    EditPlanLocationState,
    EditPlanLocationStatus,
    build_edit_plan,
    load_edit_plan,
    save_edit_plan,
)
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.edit_plan_rules import (
    export_rule_options,
    save_edit_plan_rules,
    validate_shots_against_rules,
)
from otio_app.services.edit_plan_validator import ValidationStatus, validate_timeline_items
from otio_app.services.opening_title_renderer import apply_opening_titles_to_plan
from otio_app.services.timeline_plan_builder import build_voiceover_plan
from otio_app.services.otio_exporter import (
    MergedEditPlanResult,
    export_otio_timeline,
    merge_confirmed_edit_plans,
    verify_timeline_media_paths,
)
from otio_app.services.otio_export_settings import (
    OtioExportSettings,
    load_otio_export_settings,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.edit_plan_rules_ui import (
    get_edit_plan_rules_for_project,
    render_edit_plan_rules_manager,
)
from otio_app.ui.activity import log_heavy_operation
from otio_app.ui.navigation import PAGE_EDIT_PLAN
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
    return collect_folder_statuses(
        project,
        project_id,
        mapped_folders,
        get_draft=_get_draft,
    )


def _folder_label_from_status(folder_name: str, status: EditPlanLocationStatus) -> str:
    return (
        f"{_location_state_icon(status.state)} {folder_name} · "
        f"{_location_state_label(status.state)}"
    )


def _render_location_progress(statuses: list[EditPlanLocationStatus], mapped_folders: list[str]) -> None:
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


def _seed_timing_widgets(project) -> None:
    """Lädt gespeicherte Export-Timing-Werte in die Widgets (einmalig pro Session)."""
    saved = load_otio_export_settings(project)
    offset_key = f"plan_offset_{project.id}"
    outro_key = f"plan_outro_{project.id}"
    if offset_key not in st.session_state:
        st.session_state[offset_key] = float(saved.audio_offset_sec)
    if outro_key not in st.session_state:
        st.session_state[outro_key] = float(saved.section_outro_sec)


def _export_timing_settings(project) -> OtioExportSettings:
    """Audio-Start und Ausklingen aus Tab „Timing & Gemini“ (Fallback: gespeicherte JSON)."""
    saved = load_otio_export_settings(project)
    return OtioExportSettings(
        audio_offset_sec=_plan_number_setting(project.id, "offset", saved.audio_offset_sec),
        section_outro_sec=_plan_number_setting(project.id, "outro", saved.section_outro_sec),
    )


def _plan_text_setting(project_id: str, suffix: str, default: str) -> str:
    return str(st.session_state.get(f"plan_{suffix}_{project_id}", default))


def _plan_gemini_model(project_id: str) -> str:
    default_model = get_default_gemini_model()
    return str(st.session_state.get(f"plan_gemini_{project_id}", default_model))


def _render_tab_settings(project) -> None:
    render_edit_plan_rules_manager(project)
    st.divider()
    st.markdown("**Timing & Gemini**")
    st.caption(
        "Min./Max. Shot und Gemini-Modell gelten beim **Schnittplan vorschlagen**. "
        "**Audio-Start** beim OTIO-Export. **Ordner-Ausklingen** wird beim "
        "**Schnittplan vorschlagen** als eigene(s) Element(e) aus dem Ordner geplant "
        "(je max. 8 s). Der Export übernimmt `timeline_items` unverändert."
    )
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
            help="Letztes Asset eines Ordners bleibt auf der Timeline so viele Sekunden länger (nur OTIO-Export).",
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


def _export_blockers_message(merged: MergedEditPlanResult, folder_selection: tuple[str, ...]) -> str:
    if not folder_selection:
        return (
            "Kein Ort zum Export ausgewählt — wähle mindestens einen **bestätigten** Ort "
            "oder bestätige Schnittpläne unter „Prüfen & Speichern“."
        )
    if merged.skipped_folders and not merged.included_folders:
        return (
            "Keine bestätigten Schnittpläne für die gewählten Orte — "
            "unter **Prüfen & Speichern** die Checkbox aktivieren und "
            "**Bestätigen & speichern** klicken."
        )
    if not merged.timeline_items:
        return (
            "Keine `timeline_items` im Schnittplan — bitte unter **Vorschlag** "
            "den Schnittplan **neu generieren** und erneut bestätigen."
        )
    if merged.validation_status != ValidationStatus.OK.value:
        return (
            f"Schnittplan-Validierung: **{merged.validation_status}** — "
            "Details unten. Oft hilft: Schnittplan neu vorschlagen und bestätigen."
        )
    return "Export nicht möglich — bitte Validierungsmeldungen prüfen."


def _finalize_plan_for_confirm(
    project,
    draft: EditPlanDocument,
    selected_folder: str,
) -> tuple[EditPlanDocument, list[str]]:
    """Voice-over-Block ergänzen, Opening Titles vorab rendern, Timeline prüfen."""
    notes: list[str] = []
    document = draft.model_copy(update={"confirmed": True, "folder_name": selected_folder})

    if not document.timeline_items:
        raise ValueError(
            "Kein moderner Schnittplan (`timeline_items` fehlt). "
            "Bitte unter „Vorschlag“ **Schnittplan vorschlagen** und erneut bestätigen."
        )

    voice_files = {item.voice_file for item in document.timeline_items if item.voice_file}
    if document.voiceover is None and voice_files:
        document = document.model_copy(
            update={"voiceover": build_voiceover_plan(next(iter(voice_files)), document.settings)}
        )
        notes.append("Voice-over-Block aus WAV-Datei ergänzt.")

    rules_doc = get_edit_plan_rules_for_project(project)
    rules = export_rule_options(rules_doc)
    if rules.folder_title_enabled and not any(
        item.type == "opening_title" for item in document.timeline_items
    ):
        raise ValueError(
            "Ordner-Titel-Regel ist aktiv, aber der Schnittplan enthält kein opening_title. "
            "Bitte unter „Vorschlag“ den Schnittplan neu generieren."
        )

    timeline_items, title_notes = apply_opening_titles_to_plan(
        project,
        document.timeline_items,
        folder_name=selected_folder,
        export_opts=rules,
    )
    document = document.model_copy(update={"timeline_items": timeline_items})
    notes.extend(title_notes)

    validation = validate_timeline_items(
        document.timeline_items,
        settings=document.settings,
        voiceover=document.voiceover,
        opening_title_required=rules.folder_title_enabled,
        require_rendered_media=False,
    )
    if validation.status == ValidationStatus.BLOCKED:
        preview = "; ".join(validation.errors[:6])
        raise ValueError(f"Schnittplan ungültig — bitte zuerst beheben: {preview}")

    for shot in document.shots:
        if not shot.asset_path:
            shot.asset_source = "missing"

    return document, notes


def _render_tab_generate(project, selected_folder: str, saved: EditPlanDocument | None) -> None:
    st.markdown(
        f"Vorschlag für **{selected_folder}** — Gemini erhält **Whisper-Text**, "
        "**Asset-Beschreibungen** und deine **Zusatzhinweise** (Tab Regeln)."
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
            rules_doc = get_edit_plan_rules_for_project(project)
            save_edit_plan_rules(project, rules_doc)
            export_opts = export_rule_options(rules_doc)
            title_notes: list[str] = []
            with st.spinner(f"Schnittplan für {selected_folder} wird erstellt…"):
                document = build_edit_plan(
                    project,
                    settings,
                    use_api=use_gemini,
                    folder_names=[selected_folder],
                    rules_doc=rules_doc,
                )
                export_opts = export_rule_options(rules_doc)
                if export_opts.folder_title_enabled:
                    timeline_items, title_notes = apply_opening_titles_to_plan(
                        project,
                        document.timeline_items,
                        folder_name=selected_folder,
                        export_opts=export_opts,
                    )
                    document = document.model_copy(update={"timeline_items": timeline_items})
            _set_draft(document, selected_folder)
            st.success(f"{len(document.shots)} Shots vorgeschlagen.")
            if export_opts.folder_title_enabled and title_notes:
                for note in title_notes:
                    st.caption(f"• {note}")
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
    if draft is None or (not draft.shots and not draft.timeline_items):
        st.info(
            f"Noch kein Vorschlag für **{selected_folder}** — "
            "zuerst unter „Vorschlag“ generieren."
        )
        return

    if not draft.timeline_items:
        st.warning(
            "Dieser Schnittplan ist veraltet (`timeline_items` fehlt). "
            "Bitte unter **Vorschlag** erneut **Schnittplan vorschlagen**."
        )

    st.markdown(
        f"**{selected_folder}** · {len(draft.shots)} Shots "
        f"· {len(draft.timeline_items)} Timeline-Items "
        f"— Audio-Offset: {draft.settings.audio_offset_sec}s"
    )
    rules_doc = get_edit_plan_rules_for_project(project)
    violations = validate_shots_against_rules(draft.shots, rules_doc)
    if rules_doc.gemini_prompt.strip():
        st.caption("Gemini-Zusatzhinweise sind aktiv — beim Neu-Generieren berücksichtigt.")
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
        f"Ich habe den Schnittplan für {selected_folder} geprüft und möchte ihn bestätigen",
        key=f"confirm_plan_{project.id}_{safe_folder_slug(selected_folder)}",
    )
    st.caption(
        "Nur mit aktivierter Checkbox wird der Ort als **bestätigt** gespeichert "
        "und steht für den OTIO-Export bereit."
    )
    if st.button(
        "Bestätigen & speichern",
        key=f"save_plan_{project.id}_{safe_folder_slug(selected_folder)}",
        type="primary",
    ):
        if not confirm:
            st.warning(
                "Bitte zuerst die Checkbox **oben** aktivieren — "
                "ohne Bestätigung wird der Schnittplan nicht exportierbar gespeichert."
            )
        else:
            try:
                with st.spinner("Schnittplan prüfen und speichern …"):
                    confirmed, finalize_notes = _finalize_plan_for_confirm(
                        project,
                        draft,
                        selected_folder,
                    )
                    save_edit_plan(project, confirmed, selected_folder)
                    _set_draft(confirmed, selected_folder)
                st.success(f"Bestätigt und gespeichert: `{plan_path}`")
                for note in finalize_notes:
                    st.caption(f"• {note}")
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(str(exc))

    with st.expander("JSON-Vorschau", expanded=False):
        st.code(draft.model_dump_json(indent=2)[:6000])


def _export_preview_cache_key(project_id: str) -> str:
    return f"otio_export_preview_{project_id}"


def _export_preview_folders_key(project_id: str) -> str:
    return f"otio_export_preview_folders_{project_id}"


def _cache_export_preview(
    project_id: str,
    preview: MergedEditPlanResult,
    folders: tuple[str, ...],
) -> None:
    st.session_state[_export_preview_cache_key(project_id)] = {
        "timeline_items": [item.model_dump(mode="json") for item in preview.timeline_items],
        "shots": [shot.model_dump(mode="json") for shot in preview.shots],
        "settings": preview.settings.model_dump(mode="json"),
        "included_folders": preview.included_folders,
        "skipped_folders": preview.skipped_folders,
        "warnings": preview.warnings,
        "validation_status": preview.validation_status,
        "voiceovers": [vo.model_dump(mode="json") for vo in preview.voiceovers],
    }
    st.session_state[_export_preview_folders_key(project_id)] = list(folders)


def _load_cached_export_preview(project_id: str) -> MergedEditPlanResult | None:
    raw = st.session_state.get(_export_preview_cache_key(project_id))
    if not raw:
        return None
    return MergedEditPlanResult(
        timeline_items=[
            TimelineItem.model_validate(item) for item in raw.get("timeline_items", [])
        ],
        shots=[EditPlanShot.model_validate(shot) for shot in raw["shots"]],
        settings=EditPlanSettings.model_validate(raw["settings"]),
        voiceovers=[
            VoiceoverPlan.model_validate(vo) for vo in raw.get("voiceovers", [])
        ],
        included_folders=list(raw["included_folders"]),
        skipped_folders=list(raw["skipped_folders"]),
        warnings=list(raw["warnings"]),
        validation_status=str(raw.get("validation_status", "OK")),
    )


def _render_tab_export(project, mapped_folders: list[str]) -> None:
    default_export_path = get_otio_export_path(project.work_dir_path, project.name)
    export_timing = _export_timing_settings(project)
    st.markdown("**OTIO-Timeline aus bestätigten Schnittplänen**")
    st.caption(
        "Orte und Timing wählen, dann **OTIO exportieren** — Vorschau ist optional. "
        f"Audio-Start und Ausklingen aus Tab **Regeln → Timing & Gemini** "
        f"({export_timing.audio_offset_sec}s / {export_timing.section_outro_sec}s). "
        f"Ausklingen ist der letzte Shot pro Ordner im Schnittplan. "
        f"Ziel: `{default_export_path}` · "
        f"Einstellungen: `{project.work_dir_path / 'otio_export_settings.json'}`"
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

    folder_selection = tuple(sorted(export_folders)) if export_folders else tuple(
        sorted(
            folder_name
            for folder_name in mapped_folders
            if (plan := load_edit_plan(project, folder_name)) is not None and plan.confirmed
        )
    )
    cached_folders = tuple(st.session_state.get(_export_preview_folders_key(project.id), []))
    preview = _load_cached_export_preview(project.id)
    preview_stale = preview is not None and cached_folders != folder_selection

    export_clicked = st.button(
        "📤 OTIO exportieren",
        key=f"export_otio_{project.id}",
        type="primary",
        use_container_width=True,
    )
    preview_clicked = st.button(
        "📋 Vorschau anzeigen (optional)",
        key=f"export_preview_{project.id}",
        use_container_width=True,
    )

    if preview_stale:
        st.caption("Vorschau veraltet — bei Bedarf erneut **Vorschau anzeigen**.")

    if export_clicked:
        try:
            export_settings = export_timing
            with st.spinner("Schnittpläne zusammenführen, Medien prüfen und OTIO schreiben …"):
                merged = merge_confirmed_edit_plans(
                    project,
                    folder_names=list(folder_selection) if folder_selection else None,
                )
                if not merged.ready:
                    st.warning(_export_blockers_message(merged, folder_selection))
                    for warning in merged.warnings:
                        if warning.startswith("Validierung:"):
                            st.error(warning)
                        else:
                            st.caption(f"• {warning}")
                else:
                    log_heavy_operation(
                        f"OTIO-Export ({len(merged.timeline_items)} Timeline-Items)",
                        page=PAGE_EDIT_PLAN,
                    )
                    export_result = export_otio_timeline(
                        project,
                        merged,
                        export_settings=export_settings,
                    )
                    st.success(f"Timeline exportiert: `{export_result.path}`")
                    for note in export_result.aspect_fill_notes:
                        if "Letterboxing" in note or "fehlgeschlagen" in note or "nicht lesbar" in note:
                            st.warning(note)
                        else:
                            st.caption(f"• {note}")
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    if preview_clicked:
        with st.spinner("Schnittpläne zusammenführen …"):
            preview = merge_confirmed_edit_plans(
                project,
                folder_names=list(folder_selection) if folder_selection else None,
            )
            _cache_export_preview(project.id, preview, folder_selection)
        st.rerun()

    if preview is not None and not preview_stale:
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
            if warning.startswith("Validierung:"):
                st.error(warning)
            else:
                st.caption(f"• {warning}")

        if preview.ready:
            from otio_app.services.otio_exporter import _compute_timeline_sections

            timeline_sections = _compute_timeline_sections(
                preview.timeline_items,
                preview.settings.model_copy(
                    update={
                        "audio_offset_sec": export_timing.audio_offset_sec,
                        "section_outro_sec": export_timing.section_outro_sec,
                    }
                ),
                preview.voiceovers,
            )
            total_duration = sum(section.video_duration_sec for section in timeline_sections)
            st.caption(
                f"Geschätzte Videospur: {total_duration:.1f}s · "
                f"Audio-Start: {export_timing.audio_offset_sec}s · "
                f"Ausklingen: {export_timing.section_outro_sec}s · "
                f"{project.fps} fps"
            )
            for section in timeline_sections:
                voice_start = section.video_start_sec + section.voiceover.timeline_start_sec
                st.caption(
                    f"• **{section.folder}** — Video ab {section.video_start_sec:.1f}s "
                    f"({section.video_duration_sec:.1f}s), Voice ab {voice_start:.1f}s"
                )

            if st.button(
                "🔍 Medien tief prüfen (ffmpeg)",
                key=f"export_deep_check_{project.id}",
            ):
                with st.spinner("ffmpeg prüft alle Shot-Medien …"):
                    log_heavy_operation(
                        f"Tiefe Medienprüfung ({len(preview.timeline_items)} Items)",
                        page=PAGE_EDIT_PLAN,
                    )
                    deep_issues = verify_timeline_media_paths(
                        project, preview.timeline_items, strict=True
                    )
                if deep_issues:
                    st.warning("Probleme gefunden:")
                    for line in deep_issues[:15]:
                        st.caption(f"• {line}")
                else:
                    st.success("Alle Shot-Medien Resolve-ready.")
        else:
            st.info(_export_blockers_message(preview, folder_selection))

    st.markdown("**In Resolve / Premiere / OTIO**")
    st.caption(
        "Video (**V1**) startet bei 0. Pro Voice-over-Datei eine **eigene Audiospur** — "
        "Originaldatei ab Sekunde 0, **nicht** pro Shot geschnitten. "
        "Die Länge pro Abschnitt verhindert Überlappungen (keine Verzerrung durch mehrere VO gleichzeitig). "
        "In DaVinci Resolve: **File → Import → Timeline → OpenTimelineIO**. "
        "Bei Zoom-Regeln entstehen Dateien wie `Asset03_3840x2160.mp4` — "
        "alte Clips im Media Pool vor dem Import löschen, sonst verlinkt Resolve evtl. "
        "noch die 4096×2160-Originaldatei."
    )


def render_edit_plan_page() -> None:
    st.header("③ Schnittplan")

    project = render_project_selector()
    if project is None:
        return

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

    location_statuses = _collect_location_statuses(project, project.id, mapped_folders)
    status_by_folder = {item.folder_name: item for item in location_statuses}

    _seed_timing_widgets(project)

    render_workflow_progress(
        project,
        current_step="edit_plan",
        lightweight=True,
        location_statuses=location_statuses,
    )

    folder_key = _folder_state_key(project.id)
    default_folder = st.session_state.get(folder_key, mapped_folders[0])
    if default_folder not in mapped_folders:
        default_folder = mapped_folders[0]

    with st.expander("Fortschritt pro Ort", expanded=False):
        _render_location_progress(location_statuses, mapped_folders)
    st.divider()

    st.markdown("**Ort bearbeiten**")
    selected_folder = st.selectbox(
        "Asset-Ordner",
        options=mapped_folders,
        index=mapped_folders.index(default_folder),
        format_func=lambda folder_name: _folder_label_from_status(
            folder_name,
            status_by_folder[folder_name],
        ),
        key=f"plan_folder_select_{project.id}",
        label_visibility="collapsed",
    )
    st.session_state[folder_key] = selected_folder

    plan_path = project.folder_edit_plan_path(selected_folder)
    st.caption(f"Speicherort: `{plan_path}`")

    saved = load_edit_plan(project, selected_folder)
    if saved is not None and saved.confirmed:
        st.success(f"Schnittplan für **{selected_folder}** bestätigt.")
        st.caption(
            "Regeln geändert? Unter **Vorschlag** erneut **Schnittplan vorschlagen**, "
            "dann unter **Prüfen & Speichern** neu bestätigen."
        )

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
