"""Streamlit-UI: Schnittplan erstellen und freigeben."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, EditPlanShot, SupplementRequest, TimelineItem, VoiceoverPlan
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SECTION_OUTRO_SEC,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LABELS,
    GEMINI_MODEL_CHOICES,
    SUPPLEMENT_SOURCE_LABELS,
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
from otio_app.services.asset_usage import validate_max_asset_usage_blockers
from otio_app.services.inventory_hash import current_folder_inventory_hash, inventory_hash_is_stale
from otio_app.services.edit_plan_validator import ValidationStatus, validate_timeline_items
from otio_app.services.opening_title_renderer import (
    ensure_opening_titles_rendered,
    title_render_is_stale,
)
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED
from otio_app.services.supplement_pipeline import search_supplement_candidates
from otio_app.services.supplement_search import request_with_keyword_query
from otio_app.services.supplement_requests import load_supplement_requests, update_request, upsert_requests
from otio_app.services.title_style import extract_title_style
from otio_app.services.timeline_plan_builder import build_voiceover_plan
from otio_app.services.generic_outro_selector import section_id_for_folder
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
from otio_app.services.edit_plan_timing_settings import (
    DEFAULT_TEXT_SPLIT_INPUT,
    EditPlanTimingSettings,
    load_edit_plan_timing_settings,
    save_edit_plan_timing_settings,
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


def _number_input_with_seeded_state(
    label: str,
    *,
    key: str,
    default: float,
    min_value: float,
    max_value: float,
    step: float,
    help: str | None = None,
) -> None:
    kwargs = {
        "min_value": min_value,
        "max_value": max_value,
        "step": step,
        "key": key,
    }
    if help:
        kwargs["help"] = help
    if key not in st.session_state:
        kwargs["value"] = float(default)
    st.number_input(label, **kwargs)


def _seed_timing_widgets(project) -> None:
    """Lädt gespeicherte Timing-/Gemini-Werte in die Widgets (einmalig pro Session).

    Vorher wurden Min./Max. Shot, Text-Trenner und Gemini-Modell NIE persistiert
    und fielen nach jedem Reload/Neustart stillschweigend auf die Defaults
    zurück — unabhängig davon, was zuvor eingestellt war. Jetzt werden alle
    Timing-/Gemini-Werte aus `edit_plan_timing_settings.json` geladen.
    """
    saved = load_edit_plan_timing_settings(project)
    seed_map = {
        f"plan_min_{project.id}": float(saved.shot_min_sec),
        f"plan_max_{project.id}": float(saved.shot_max_sec),
        f"plan_offset_{project.id}": float(saved.audio_offset_sec),
        f"plan_outro_{project.id}": float(saved.section_outro_sec),
        f"plan_split_{project.id}": saved.text_splitters,
        f"plan_gemini_{project.id}": saved.gemini_model,
    }
    for key, value in seed_map.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _persist_timing_widgets(project) -> None:
    """Speichert die aktuellen Timing-/Gemini-Widget-Werte dauerhaft, sobald der
    Regeln-Tab gerendert wird — analog zum automatischen Speichern der Regeln."""
    settings = EditPlanTimingSettings(
        shot_min_sec=_plan_number_setting(project.id, "min", DEFAULT_SHOT_MIN_SEC),
        shot_max_sec=_plan_number_setting(project.id, "max", DEFAULT_SHOT_MAX_SEC),
        audio_offset_sec=_plan_number_setting(project.id, "offset", DEFAULT_AUDIO_OFFSET_SEC),
        section_outro_sec=_plan_number_setting(project.id, "outro", DEFAULT_SECTION_OUTRO_SEC),
        text_splitters=_plan_text_setting(project.id, "split", DEFAULT_TEXT_SPLIT_INPUT),
        gemini_model=_plan_gemini_model(project.id),
    )
    save_edit_plan_timing_settings(project, settings)


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
        "(je max. **Max. Shot** Sek., siehe unten). Der Export übernimmt `timeline_items` unverändert."
    )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        _number_input_with_seeded_state(
            "Min. Shot (Sek.)",
            default=float(DEFAULT_SHOT_MIN_SEC),
            min_value=1.0,
            max_value=30.0,
            step=0.5,
            key=f"plan_min_{project.id}",
        )
    with col2:
        _number_input_with_seeded_state(
            "Max. Shot (Sek.)",
            default=float(DEFAULT_SHOT_MAX_SEC),
            min_value=1.0,
            max_value=60.0,
            step=0.5,
            key=f"plan_max_{project.id}",
        )
    with col3:
        _number_input_with_seeded_state(
            "Audio-Start (+Sek.)",
            default=float(DEFAULT_AUDIO_OFFSET_SEC),
            min_value=0.0,
            max_value=10.0,
            step=0.5,
            key=f"plan_offset_{project.id}",
            help="Voice-over startet so viele Sekunden nach dem ersten Asset eines Ordners.",
        )
    with col4:
        _number_input_with_seeded_state(
            "Ordner-Ausklingen (Sek.)",
            default=float(DEFAULT_SECTION_OUTRO_SEC),
            min_value=0.0,
            max_value=30.0,
            step=0.5,
            key=f"plan_outro_{project.id}",
            help="Letztes Asset eines Ordners bleibt auf der Timeline so viele Sekunden länger (nur OTIO-Export).",
        )

    current_min = _plan_number_setting(project.id, "min", DEFAULT_SHOT_MIN_SEC)
    current_max = _plan_number_setting(project.id, "max", DEFAULT_SHOT_MAX_SEC)
    if current_min > current_max:
        st.error(
            f"⚠️ Min. Shot ({current_min:.1f}s) ist größer als Max. Shot ({current_max:.1f}s) — "
            "das erzeugt zwangsläufig Shots, die die Max.-Regel verletzen. Bitte Min. Shot "
            "senken oder Max. Shot erhöhen."
        )

    split_key = f"plan_split_{project.id}"
    split_kwargs = {"key": split_key}
    if split_key not in st.session_state:
        split_kwargs["value"] = DEFAULT_TEXT_SPLIT_INPUT
    st.text_input("Text-Trenner (kommagetrennt)", **split_kwargs)
    st.caption("Fallback-Reihenfolge (Adobe Stock / Pexels / KI folgen später):")
    for source in DEFAULT_FALLBACK_ORDER:
        st.write(f"- {FALLBACK_SOURCE_LABELS.get(source, source)}")

    gemini_key = f"plan_gemini_{project.id}"
    model_choices = list(GEMINI_MODEL_CHOICES)
    selectbox_kwargs = {
        "options": model_choices,
        "format_func": format_gemini_model_label,
        "key": gemini_key,
    }
    if gemini_key not in st.session_state:
        default_model = get_default_gemini_model()
        selectbox_kwargs["index"] = (
            model_choices.index(default_model) if default_model in model_choices else 0
        )
    elif st.session_state[gemini_key] not in model_choices:
        st.session_state[gemini_key] = get_default_gemini_model()
    st.selectbox("Gemini-Modell (Motiv → Asset)", **selectbox_kwargs)

    st.caption("💾 Timing- und Gemini-Einstellungen werden automatisch gespeichert.")
    _persist_timing_widgets(project)


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

    timeline_items, title_notes = ensure_opening_titles_rendered(
        project,
        document.timeline_items,
    )
    document = document.model_copy(update={"timeline_items": timeline_items})
    notes.extend(title_notes)

    if document.inventory_hash_at_plan_time and inventory_hash_is_stale(
        project,
        selected_folder,
        document.inventory_hash_at_plan_time,
    ):
        raise ValueError(
            "Inventory geändert — bitte Schnittplan mit neuen Assets neu vorschlagen."
        )

    validation = validate_timeline_items(
        document.timeline_items,
        settings=document.settings,
        voiceover=document.voiceover,
        opening_title_required=rules.folder_title_enabled,
        require_rendered_media=False,
        rules_doc=rules_doc,
        work_dir_path=project.work_dir_path,
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

    splitters = _plan_text_setting(project.id, "split", DEFAULT_TEXT_SPLIT_INPUT)
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
                    timeline_items, title_notes = ensure_opening_titles_rendered(
                        project,
                        document.timeline_items,
                    )
                    document = document.model_copy(update={"timeline_items": timeline_items})
            _set_draft(document, selected_folder)
            st.success(f"{len(document.shots)} Shots vorgeschlagen.")
            title_item = next(
                (item for item in document.timeline_items if item.type == "opening_title"),
                None,
            )
            if title_item is not None and title_item.title_style is not None:
                style = title_item.title_style
                st.caption(
                    f"Titel: **{style.text}** · {style.requested_font_family} "
                    f"→ {style.resolved_font_family} · **{int(style.font_size_px)}px** · "
                    f"{style.duration_sec:.1f}s · hash `{style.render_hash}`"
                )
                if style.font_fallback_used:
                    st.warning("Font-Fallback aktiv — siehe validation_report.json.")
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

    rules_doc = get_edit_plan_rules_for_project(project)
    rules = export_rule_options(rules_doc)
    if draft.inventory_hash_at_plan_time and inventory_hash_is_stale(
        project,
        selected_folder,
        draft.inventory_hash_at_plan_time,
    ):
        st.error(
            "Inventory changed — please regenerate cut plan. "
            f"(Plan-Hash `{draft.inventory_hash_at_plan_time}`, "
            f"aktuell `{current_folder_inventory_hash(project, selected_folder)}`)"
        )

    usage_blockers = validate_max_asset_usage_blockers(
        timeline_items=draft.timeline_items,
        rules_doc=rules_doc,
    )
    if usage_blockers:
        st.error("max_asset_usage verletzt — Bestätigung blockiert:")
        for violation in usage_blockers:
            st.caption(
                f"• `{violation.asset_id}`: {violation.usage_count}× "
                f"(max {violation.max_allowed})"
            )

    supplement_beats = [
        coverage
        for coverage in draft.segment_coverage
        if coverage.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    ]
    weak_with_asset = [
        shot
        for shot in draft.shots
        if shot.asset_path
        and shot.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    ]
    if supplement_beats:
        st.warning(
            f"{len(supplement_beats)} Beat(s) mit SUPPLEMENT_REQUIRED — "
            "bitte unter **②½ Supplement Assets** ergänzen."
        )
    if weak_with_asset:
        st.warning(
            f"{len(weak_with_asset)} Shot(s) nutzen trotz Supplement-Bedarf ein schwaches lokales Asset."
        )
    google_items = [
        item
        for item in draft.timeline_items
        if item.rights_status == "NEEDS_LICENSE_REVIEW"
    ]
    if google_items:
        st.warning(
            f"{len(google_items)} Google-Search-Asset(s) ohne Rechtefreigabe — "
            "locked edit_plan blockiert."
        )

    if not draft.timeline_items:
        st.warning(
            "Dieser Schnittplan ist veraltet (`timeline_items` fehlt). "
            "Bitte unter **Vorschlag** erneut **Schnittplan vorschlagen**."
        )

    title_item = next((item for item in draft.timeline_items if item.type == "opening_title"), None)
    if title_item is not None:
        try:
            style = extract_title_style(title_item, project)
            stale = title_render_is_stale(title_item, project)
            st.markdown("**Opening Title**")
            st.caption(
                f"Text: **{style.text}** · Schrift: {style.requested_font_family} "
                f"→ {style.resolved_font_family} · **{int(style.font_size_px)} px** · "
                f"{style.duration_sec:.1f}s · Position: {style.position}"
            )
            st.caption(
                f"Shadow: {'ja' if style.shadow_enabled else 'nein'} "
                f"({style.shadow_opacity:.0%}, offset {style.shadow_offset_x:.0f}/"
                f"{style.shadow_offset_y:.0f}) · Hash: `{style.render_hash or '—'}`"
            )
            if style.font_fallback_used:
                st.warning(f"Font-Fallback: {style.font_resolution_warning or style.resolved_font_family}")
            if stale:
                st.warning(
                    "Title settings changed — title render is stale. "
                    "Bitte Schnittplan neu vorschlagen oder **Titel neu rendern**."
                )
            if style.output_png_path and Path(style.output_png_path).is_file():
                st.image(style.output_png_path, caption="Titel-Preview (PNG mit Alpha)")
            rerender_col1, _ = st.columns([1, 3])
            with rerender_col1:
                if st.button(
                    "Titel neu rendern",
                    key=f"rerender_title_{project.id}_{safe_folder_slug(selected_folder)}",
                ):
                    with st.spinner("Titel wird neu gerendert …"):
                        items, notes = ensure_opening_titles_rendered(
                            project,
                            draft.timeline_items,
                            force=True,
                        )
                        draft = draft.model_copy(update={"timeline_items": items})
                        _set_draft(draft, selected_folder)
                    for note in notes:
                        st.caption(f"• {note}")
                    st.rerun()
        except ValueError as exc:
            st.error(str(exc))

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

    missing_shots = [
        (index, shot)
        for index, shot in enumerate(draft.shots, start=1)
        if not shot.asset_path
    ]
    if missing_shots:
        st.markdown("### Fehlende Assets supplementieren")
        st.caption(
            f"{len(missing_shots)} Shot(s) ohne Asset. "
            "Hier kannst du für alle fehlenden Shots in einem Durchlauf Supplement-Kandidaten suchen."
        )
        source = st.selectbox(
            "Quelle für alle fehlenden Assets",
            options=list(SUPPLEMENT_SOURCE_LABELS.keys()),
            format_func=lambda key: SUPPLEMENT_SOURCE_LABELS[key],
            key=f"batch_supplement_source_{project.id}_{safe_folder_slug(selected_folder)}",
        )
        if source == "google_search":
            st.warning(
                "Google Suche ist nur Discovery. Gefundene Assets brauchen manuelle Rechtefreigabe."
            )
        if source == "adobe_stock":
            st.warning(
                "Adobe Stock: Diese Suche lizenziert nichts. Kauf/Lizenzierung braucht später einen separaten Button."
            )

        if st.button(
            "Alle fehlenden Supplement-Kandidaten suchen",
            key=f"batch_supplement_search_{project.id}_{safe_folder_slug(selected_folder)}",
        ):
            try:
                existing = load_supplement_requests(project)
                existing_ids = {request.supplement_request_id for request in existing.requests}
                requests: list[SupplementRequest] = []
                for shot_index, shot in missing_shots:
                    request_id = (
                        shot.supplement_request_id
                        or f"supp_req_{safe_folder_slug(selected_folder)}_{shot_index:03d}"
                    )
                    if request_id in existing_ids:
                        existing_request = next(
                            request
                            for request in existing.requests
                            if request.supplement_request_id == request_id
                        )
                        request = existing_request.model_copy(
                            update={
                                "selected_source": source,
                                "status": "SOURCE_SELECTED",
                            }
                        )
                    else:
                        passage = shot.passage_text or shot.motif or f"Shot {shot_index}"
                        request = SupplementRequest(
                            supplement_request_id=request_id,
                            section_id=section_id_for_folder(selected_folder),
                            folder_name=selected_folder,
                            beat_id=shot.beat_id or f"shot_{shot_index:03d}",
                            passage_text=passage,
                            visual_requirement=shot.motif or passage,
                            duration_needed_sec=max(0.1, shot.duration_sec),
                            reason=(
                                "Schnittplan enthält für dieses Voice-over-Segment kein Asset. "
                                "Batch-Suche aus dem Schnittplan gestartet."
                            ),
                            local_best_asset_id=shot.asset_id,
                            local_best_match_score=0.0,
                            selected_source=source,
                            status="SOURCE_SELECTED",
                        )
                    request = request_with_keyword_query(request)
                    request = request.model_copy(
                        update={
                            "query_used": request.search_queries.get("en", [""])[0],
                            "location_name": selected_folder,
                        }
                    )
                    requests.append(request)
                upsert_requests(project, requests)

                total_candidates = 0
                for request in requests:
                    updated = update_request(
                        project,
                        request.supplement_request_id,
                        selected_source=source,
                        status="SOURCE_SELECTED",
                    )
                    if updated is None:
                        continue
                    total_candidates += len(search_supplement_candidates(project, updated))

                st.success(
                    f"{len(requests)} Supplement Request(s) verarbeitet, "
                    f"{total_candidates} Kandidat(en) gefunden. "
                    "Öffne ②½ Supplement Assets zum Auswählen/Download/Generieren."
                )
                st.rerun()
            except (OSError, ValueError, PermissionError) as exc:
                st.error(str(exc))

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
                if shot.asset_origin and shot.asset_origin != "local_original":
                    st.caption(
                        f"Supplement: {shot.provider or shot.asset_origin} · "
                        f"rights={shot.rights_status or '—'}"
                    )
            else:
                st.warning(
                    "Kein Asset — bitte unter **②½ Supplement Assets** ergänzen "
                    "oder Schnittplan neu vorschlagen."
                )

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
            if usage_blockers:
                st.error("Bestätigung blockiert wegen max_asset_usage-Verstoß.")
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
