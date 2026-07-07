"""Streamlit-UI: Schnittplan erstellen und freigeben."""

from __future__ import annotations

from pathlib import Path

from collections import Counter

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
    MATCH_QUALITY_LABELS,
    MATCH_QUALITY_MITTEL,
    MATCH_QUALITY_SEHR_GUT,
    MATCH_QUALITY_GUT,
    MATCH_QUALITY_UNPASSEND,
    MAX_GEMINI_PLAN_ATTEMPTS,
    SUPPLEMENT_SOURCE_LABELS,
)
from otio_app.project_layout import (
    default_otio_export_basename,
    resolve_otio_export_path,
    safe_folder_slug,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.services.edit_plan_cache import collect_folder_statuses
from otio_app.services.edit_plan_builder import (
    EditPlanLocationState,
    EditPlanLocationStatus,
    build_edit_plan,
    EditPlanBuildStatus,
    load_edit_plan,
    load_voice_analysis,
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
from otio_app.services.edit_plan_gap_fill import GAP_FILLABLE_TYPES, fill_missing_timeline_assets
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_hash import current_folder_inventory_hash, inventory_hash_is_stale
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.edit_plan_validator import (
    ASSET_RULE_ERROR_TYPES,
    PlanValidationError,
    ValidationStatus,
    plan_validation_error_to_message,
)
from otio_app.services.plan_validation_reports import (
    format_used_rules_summary,
    format_validation_error_entries,
    gemini_attempts_label,
    latest_retry_attempt_summary,
    load_edit_plan_validation_report,
    plan_is_confirmable,
    validate_document_for_confirm,
    validation_status_label,
)
from otio_app.services.opening_title_renderer import (
    ensure_opening_titles_rendered,
    title_render_is_stale,
)
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED
from otio_app.services.supplement_pipeline import search_supplement_candidates
from otio_app.services.supplement_search import request_with_keyword_query
from otio_app.services.supplement_requests import load_supplement_requests, update_request, upsert_requests
from otio_app.services.title_style import extract_title_style
from otio_app.services.timeline_plan_builder import build_voiceover_plan, shots_from_timeline_items
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


def _confirm_result_key(project_id: str, folder_name: str) -> str:
    return f"confirm_result_{project_id}_{safe_folder_slug(folder_name)}"


def _generate_result_key(project_id: str, folder_name: str) -> str:
    return f"generate_result_{project_id}_{safe_folder_slug(folder_name)}"


def _get_draft(project_id: str, folder_name: str) -> EditPlanDocument | None:
    raw = st.session_state.get(_plan_state_key(project_id, folder_name))
    if not raw:
        return None
    return EditPlanDocument.model_validate(raw)


def _set_draft(document: EditPlanDocument, folder_name: str) -> None:
    st.session_state[_plan_state_key(document.project_id, folder_name)] = (
        document.model_dump(mode="json")
    )


def _clear_draft(project_id: str, folder_name: str) -> None:
    st.session_state.pop(_plan_state_key(project_id, folder_name), None)


def _effective_draft(
    project_id: str,
    folder_name: str,
    saved: EditPlanDocument | None,
) -> EditPlanDocument | None:
    """Bevorzugt den NEUEREN Stand zwischen In-Session-Entwurf und
    gespeicherter Datei (nach generated_at).

    Ohne diesen Vergleich gewann der Session-Entwurf immer bedingungslos —
    das führte dazu, dass ein extern (z. B. von der ②½ Supplement-Assets-
    Seite per Auto-Replan) frisch auf die Festplatte geschriebener,
    aktuellerer Schnittplan im Tab „Prüfen & Speichern“ nicht sichtbar war,
    solange im Browser noch ein älterer Entwurf im session_state lag —
    inklusive dessen veraltetem inventory_hash_at_plan_time und
    segment_coverage (z. B. „Inventory changed“ / „Beats mit
    SUPPLEMENT_REQUIRED“, obwohl der neue Plan das längst behoben hat).
    """
    draft = _get_draft(project_id, folder_name)
    if draft is None:
        return saved
    if saved is None:
        return draft
    if saved.generated_at > draft.generated_at:
        return saved
    return draft


def _sync_draft_from_saved_if_newer(
    project,
    folder_name: str,
    saved: EditPlanDocument | None,
) -> None:
    """Übernimmt einen frisch gespeicherten Plan (z. B. nach Supplement-Replan)
    in den Session-Entwurf, wenn der Browser noch einen veralteten Stand hat."""
    if saved is None:
        return
    draft = _get_draft(project.id, folder_name)
    should_replace = draft is None
    if draft is not None:
        if saved.generated_at > draft.generated_at:
            should_replace = True
        elif (
            draft.inventory_hash_at_plan_time
            and inventory_hash_is_stale(
                project,
                folder_name,
                draft.inventory_hash_at_plan_time,
            )
            and saved.inventory_hash_at_plan_time
            and not inventory_hash_is_stale(
                project,
                folder_name,
                saved.inventory_hash_at_plan_time,
            )
        ):
            should_replace = True
    if should_replace:
        _set_draft(saved, folder_name)


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


def _match_quality_label(match_quality: str) -> str:
    if not match_quality:
        return ""
    return MATCH_QUALITY_LABELS.get(match_quality, match_quality)


def _shot_expander_title(index: int, shot: EditPlanShot) -> str:
    base = f"Shot {index + 1} · {shot.folder} · {shot.duration_sec:.1f}s"
    label = _match_quality_label(shot.match_quality)
    if shot.section_outro:
        if label:
            return f"{base} · Ordner-Ausklingen · Passung: {label}"
        return f"{base} · Ordner-Ausklingen"
    if label:
        return f"{base} · Passung: {label}"
    return f"{base} · Passung: —"


def _voice_segment_count_for_folder(project, folder_name: str) -> int:
    """Anzahl Whisper-Segmente für den Ordner (nur zur Fortschritts-Anzeige)."""
    try:
        mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
        voice_doc = load_voice_analysis(project)
    except (OSError, ValueError, FileNotFoundError):
        return 0
    if mapping is None:
        return 0
    voice_files = {
        entry.voice_file
        for entry in mapping.entries
        if entry.folder == folder_name and entry.confirmed
    }
    total = 0
    for voice_file in voice_files:
        voice_entry = next((entry for entry in voice_doc.files if entry.path == voice_file), None)
        if voice_entry is None:
            continue
        total += sum(1 for segment in voice_entry.segments if segment.text.strip())
    return total


def _render_match_quality_badge(shot: EditPlanShot) -> None:
    if shot.section_outro:
        st.caption("**Ordner-Ausklingen** — im selben Gemini-Call mitgeplant.")
    if shot.match_quality == MATCH_QUALITY_SEHR_GUT:
        st.success("**Passung (Gemini): Sehr gut**")
    elif shot.match_quality == MATCH_QUALITY_GUT:
        st.info("**Passung (Gemini): Gut**")
    elif shot.match_quality == MATCH_QUALITY_MITTEL:
        st.warning("**Passung (Gemini): Mittel**")
    elif shot.match_quality == MATCH_QUALITY_UNPASSEND:
        st.error("**Passung (Gemini): Unpassend**")
        st.caption(
            "Platzhalter-Asset (Establishing/Luftaufnahme) — für ein passendes Motiv "
            "kannst du unten **Supplement Assets** starten oder unter **②½** ergänzen."
        )
    else:
        st.warning(
            "Für diesen Narration-Shot fehlt die Gemini-Bewertung — bitte unter "
            "**Vorschlag** den Schnittplan **neu vorschlagen**."
        )


def _render_match_quality_summary(shots: list[EditPlanShot]) -> None:
    rated_shots = [shot for shot in shots if shot.match_quality]
    if not rated_shots:
        st.warning(
            "Keine Gemini-Passungsbewertungen in diesem Schnittplan — bitte unter "
            "**Vorschlag** den Schnittplan **neu generieren**."
        )
        return
    counts = Counter(shot.match_quality for shot in rated_shots)
    parts = [
        f"**Sehr gut:** {counts.get(MATCH_QUALITY_SEHR_GUT, 0)}",
        f"**Gut:** {counts.get(MATCH_QUALITY_GUT, 0)}",
        f"**Mittel:** {counts.get(MATCH_QUALITY_MITTEL, 0)}",
        f"**Unpassend:** {counts.get(MATCH_QUALITY_UNPASSEND, 0)}",
    ]
    st.markdown("**Gemini-Passung (alle Shots inkl. Ausklingen):** " + " · ".join(parts))
    unrated = [shot for shot in shots if not shot.match_quality and not shot.section_outro]
    if unrated:
        st.warning(
            f"{len(unrated)} Narration-Shot(s) ohne Bewertung — bitte unter **Vorschlag** "
            "den Schnittplan **neu vorschlagen**."
        )


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


_TIMING_NUMERIC_SUFFIXES = ("min", "max", "offset", "outro")
# Widget-Untergrenzen von Min./Max. Shot, Audio-Start, Ausklingen (siehe
# _number_input_with_seeded_state-Aufrufe unten). Wenn ALLE VIER
# gleichzeitig exakt auf diesem Minimum stehen, ist das praktisch nie eine
# bewusste Nutzer-Eingabe (z. B. "1 Sekunde pro Shot" ist unbrauchbar) —
# sondern ein Indiz dafür, dass die Widgets aus unbekanntem Grund
# "leer"/frisch gerendert wurden, statt aus session_state/Datei befüllt zu
# werden. Wir behandeln das defensiv als Reset-Artefakt: nie persistieren,
# und beim Seeden aus der zuletzt gespeicherten Datei überschreiben.
_TIMING_WIDGET_FLOORS = (1.0, 1.0, 0.0, 0.0)


def _looks_like_widget_reset_artifact(values: tuple[float, float, float, float]) -> bool:
    return values == _TIMING_WIDGET_FLOORS


def _seed_timing_widgets(project) -> None:
    """Lädt gespeicherte Timing-/Gemini-Werte in die Widgets (einmalig pro Session).

    Vorher wurden Min./Max. Shot, Text-Trenner und Gemini-Modell NIE persistiert
    und fielen nach jedem Reload/Neustart stillschweigend auf die Defaults
    zurück — unabhängig davon, was zuvor eingestellt war. Jetzt werden alle
    Timing-/Gemini-Werte aus `edit_plan_timing_settings.json` geladen.

    Selbstheilung: Falls die Widgets bereits einen Wert im session_state
    haben, der wie ein Reset-Artefakt aussieht (Min./Max. Shot, Audio-Start
    und Ausklingen ALLE gleichzeitig auf ihrem Minimum 1.0/1.0/0.0/0.0),
    UND die gespeicherte Datei andere, plausible Werte enthält, werden die
    betroffenen Keys verworfen und aus der Datei neu geseedet.
    """
    saved = load_edit_plan_timing_settings(project)
    numeric_keys = [f"plan_{suffix}_{project.id}" for suffix in _TIMING_NUMERIC_SUFFIXES]
    if all(key in st.session_state for key in numeric_keys):
        current_values = tuple(float(st.session_state[key]) for key in numeric_keys)
        saved_values = (
            float(saved.shot_min_sec),
            float(saved.shot_max_sec),
            float(saved.audio_offset_sec),
            float(saved.section_outro_sec),
        )
        if _looks_like_widget_reset_artifact(current_values) and saved_values != _TIMING_WIDGET_FLOORS:
            for key in numeric_keys:
                del st.session_state[key]

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
    Regeln-Tab gerendert wird — analog zum automatischen Speichern der Regeln.

    Schreibt NICHT, wenn Min./Max. Shot, Audio-Start und Ausklingen
    gleichzeitig auf ihrem Widget-Minimum stehen (1.0/1.0/0.0/0.0) — dieser
    praktisch unbrauchbare Zustand ("1 Sekunde pro Shot") deutet auf ein
    Reset-Artefakt hin und würde sonst eine zuvor korrekt gespeicherte
    Konfiguration dauerhaft überschreiben (siehe _seed_timing_widgets).
    """
    current_values = (
        _plan_number_setting(project.id, "min", DEFAULT_SHOT_MIN_SEC),
        _plan_number_setting(project.id, "max", DEFAULT_SHOT_MAX_SEC),
        _plan_number_setting(project.id, "offset", DEFAULT_AUDIO_OFFSET_SEC),
        _plan_number_setting(project.id, "outro", DEFAULT_SECTION_OUTRO_SEC),
    )
    if _looks_like_widget_reset_artifact(current_values):
        return
    settings = EditPlanTimingSettings(
        shot_min_sec=current_values[0],
        shot_max_sec=current_values[1],
        audio_offset_sec=current_values[2],
        section_outro_sec=current_values[3],
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


def _plan_gemini_model(project_id: str, default: str | None = None) -> str:
    fallback = default if default is not None else get_default_gemini_model()
    return str(st.session_state.get(f"plan_gemini_{project_id}", fallback))


def _current_timing_settings(project) -> EditPlanTimingSettings:
    """Liefert die aktuell wirksamen Timing-/Gemini-Werte für den nächsten
    Schnittplan-Vorschlag: bevorzugt den Live-Widget-Wert aus dem Regeln-Tab
    (session_state), fällt aber — falls der Widget-Key aus irgendeinem Grund
    fehlt — auf die zuletzt GESPEICHERTE Datei zurück statt auf die globalen
    App-Defaults. Ohne diesen Fallback konnte z. B. ein ausgewähltes
    Gemini-Modell (etwa „gemini-3.1-pro-preview“) unbemerkt auf den
    App-Default zurückspringen, sobald der jeweilige session_state-Key aus
    irgendeinem Grund nicht (mehr) gesetzt war.
    """
    saved = load_edit_plan_timing_settings(project)
    return EditPlanTimingSettings(
        shot_min_sec=_plan_number_setting(project.id, "min", saved.shot_min_sec),
        shot_max_sec=_plan_number_setting(project.id, "max", saved.shot_max_sec),
        audio_offset_sec=_plan_number_setting(project.id, "offset", saved.audio_offset_sec),
        section_outro_sec=_plan_number_setting(project.id, "outro", saved.section_outro_sec),
        text_splitters=_plan_text_setting(project.id, "split", saved.text_splitters),
        gemini_model=_plan_gemini_model(project.id, saved.gemini_model),
    )


def _render_tab_settings(project) -> None:
    render_edit_plan_rules_manager(project)
    st.divider()
    st.markdown("**Timing & Gemini**")
    st.caption(
        "Min./Max. Shot und Gemini-Modell gelten beim **Schnittplan vorschlagen**. "
        "**Audio-Start** beim OTIO-Export (auch im Gemini-Prompt als Kontext). "
        "**Ordner-Ausklingen** wird im **selben Gemini-Call** mitgeplant "
        "(Dauer hier, Asset + Passung von Gemini; je max. **Max. Shot** Sek. "
        "aufgeteilt). Bei Timing-Fehlern: automatischer Korrektur-Lauf an Gemini "
        f"(max. {MAX_GEMINI_PLAN_ATTEMPTS} Versuche). Heuristik nur als Fallback."
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


def _render_plan_validation_panel(
    project,
    draft: EditPlanDocument | None,
    *,
    work_dir: Path | None = None,
) -> None:
    """Zeigt verwendete Regeln, Gemini-Versuche und Validierungsstatus."""
    if draft is None:
        return

    used_rules = draft.used_rules or {}
    report = load_edit_plan_validation_report(work_dir or project.work_dir_path)
    if not used_rules and report:
        used_rules = report.get("used_rules") or {}

    status = draft.validation_status or (report or {}).get("final_status", "")
    candidate = draft.candidate_status or (report or {}).get("candidate_status", "")
    attempts = draft.gemini_retry_attempts or (report or {}).get("retry_attempts", 0)

    blocked = candidate == "BLOCKED" or status == "FAIL"
    ok = status in {"PASS", "OK"} and not blocked
    label = validation_status_label(ok=ok, blocked=blocked)

    st.markdown("**Plan-Validierung**")
    st.caption(
        f"Status: **{label}**"
        + (f" · Gemini-Versuche: **{gemini_attempts_label(attempts)}**" if attempts else "")
    )
    for line in format_used_rules_summary(used_rules):
        st.caption(f"• {line}")

    retry_summary = latest_retry_attempt_summary(work_dir or project.work_dir_path)
    if retry_summary:
        st.caption(f"Letzter Gemini-Lauf: {retry_summary}")

    if blocked:
        st.error(
            "Dieser Schnittplan ist BLOCKED — bitte unter **Vorschlag** neu generieren. "
            "Bestätigung und OTIO-Export sind gesperrt."
        )


def _finalize_plan_for_confirm(
    project,
    draft: EditPlanDocument,
    selected_folder: str,
) -> tuple[EditPlanDocument, list[str]]:
    """Voice-over-Block ergänzen, Opening Titles vorab rendern, Timeline prüfen."""
    notes: list[str] = []
    if not plan_is_confirmable(draft):
        raise ValueError(
            "Schnittplan ist BLOCKED oder fehlgeschlagen — bitte unter „Vorschlag“ "
            "neu generieren."
        )
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

    # Manuelles Bestätigen darf nicht daran scheitern, dass für einzelne
    # Shots kein Supplement-Asset gefunden wurde: statt hart zu blockieren,
    # wird automatisch das inhaltlich nächstbeste verfügbare Asset aus
    # demselben Ordner zugewiesen (mit deutlicher Warnung als Hinweis).
    missing_before = sum(
        1
        for item in document.timeline_items
        if item.type in GAP_FILLABLE_TYPES and not item.resolved_media_path and not item.allow_black
    )
    if missing_before:
        rules_doc_for_fill = get_edit_plan_rules_for_project(project)
        folder_names = {item.folder_name for item in document.timeline_items if item.folder_name}
        folder_assets: dict[str, list[dict[str, str]]] = {}
        for folder_name in folder_names:
            inventory = load_folder_inventory(project, folder_name)
            folder_assets[folder_name] = [
                {
                    "path": asset.path,
                    "description": asset.description,
                    "asset_id": asset.asset_id or asset_id_for_path(asset.path),
                    "asset_origin": asset.asset_origin or "local_original",
                }
                for asset in inventory.assets
                if asset.path
            ]
        filled_items, fill_notes = fill_missing_timeline_assets(
            document.timeline_items,
            folder_assets=folder_assets,
            rules_doc=rules_doc_for_fill,
        )
        document = document.model_copy(
            update={
                "timeline_items": filled_items,
                "shots": shots_from_timeline_items(filled_items),
            }
        )
        notes.extend(fill_notes)

    if document.inventory_hash_at_plan_time and inventory_hash_is_stale(
        project,
        selected_folder,
        document.inventory_hash_at_plan_time,
    ):
        raise ValueError(
            "Inventory geändert — bitte Schnittplan mit neuen Assets neu vorschlagen."
        )

    validation = validate_document_for_confirm(
        document,
        rules_doc=rules_doc,
        allow_asset_rule_overrides=True,
    )
    if not validation.ok:
        preview = "; ".join(
            format_validation_error_entries(validation.errors)[:6]
        )
        raise ValueError(
            f"Schnittplan-Validierung fehlgeschlagen — bitte zuerst beheben: {preview}"
        )

    asset_override_errors = [
        error for error in validation.errors if error.type in ASSET_RULE_ERROR_TYPES
    ]
    if asset_override_errors:
        for line in format_validation_error_entries(asset_override_errors)[:6]:
            notes.append(f"Regel-Hinweis (manuell bestätigt): {line}")

    for shot in document.shots:
        if not shot.asset_path:
            shot.asset_source = "missing"

    return document, notes


def _render_tab_generate(project, selected_folder: str, saved: EditPlanDocument | None) -> None:
    st.markdown(
        f"Vorschlag für **{selected_folder}** — Gemini erhält **alle Whisper-Segmente**, "
        "**alle Asset-Beschreibungen** und deine **Zusatzhinweise** in **einem** "
        "gesamtheitlichen Call (Tab Regeln)."
    )
    if not is_gemini_configured():
        st.warning("Ohne GEMINI_API_KEY wird nur eine einfache Text-Trennung genutzt.")

    generate_result_key = _generate_result_key(project.id, selected_folder)
    pending_generate_result = st.session_state.pop(generate_result_key, None)
    if pending_generate_result is not None:
        if pending_generate_result["ok"]:
            st.success(pending_generate_result["message"])
            status = pending_generate_result.get("validation_status")
            attempts = pending_generate_result.get("gemini_retry_attempts", 0)
            if status or attempts:
                st.caption(
                    f"Validierung: **{status or 'PASS'}**"
                    + (
                        f" · Gemini-Versuche: **{gemini_attempts_label(attempts)}**"
                        if attempts
                        else ""
                    )
                )
            for line in format_used_rules_summary(pending_generate_result.get("used_rules")):
                st.caption(f"Regel: {line}")
            for note in pending_generate_result.get("notes", []):
                st.caption(f"• {note}")
        else:
            st.error(pending_generate_result["message"])
            for line in pending_generate_result.get("validation_errors", []):
                st.caption(f"• {line}")
            for line in format_used_rules_summary(pending_generate_result.get("used_rules")):
                st.caption(f"Regel: {line}")
            for note in pending_generate_result.get("notes", []):
                st.caption(f"• {note}")
            if pending_generate_result.get("traceback"):
                with st.expander("Technische Details (Traceback)", expanded=False):
                    st.code(pending_generate_result["traceback"])

    timing = _current_timing_settings(project)
    st.caption(
        f"Gemini-Modell für diesen Vorschlag: **{format_gemini_model_label(timing.gemini_model)}** "
        f"· Min/Max Shot: {timing.shot_min_sec:.1f}s/{timing.shot_max_sec:.1f}s "
        "(Tab **Regeln → Timing & Gemini** ändern)."
    )
    if st.button("Schnittplan vorschlagen", key=f"build_plan_{project.id}", type="primary"):
        use_gemini = is_gemini_configured()
        settings = EditPlanSettings(
            shot_min_sec=timing.shot_min_sec,
            shot_max_sec=timing.shot_max_sec,
            audio_offset_sec=timing.audio_offset_sec,
            section_outro_sec=timing.section_outro_sec,
            text_splitters=[
                piece.strip()
                for piece in timing.text_splitters.split(",")
                if piece.strip()
            ],
            fallback_order=list(DEFAULT_FALLBACK_ORDER),
            gemini_model=timing.gemini_model,
        )
        progress_bar = None
        progress_text = None
        try:
            rules_doc = get_edit_plan_rules_for_project(project)
            save_edit_plan_rules(project, rules_doc)
            export_opts = export_rule_options(rules_doc)
            title_notes: list[str] = []
            progress_bar = st.progress(0.0)
            progress_text = st.empty()
            segment_count = _voice_segment_count_for_folder(project, selected_folder)

            def _on_plan_progress(folder_name: str, index: int, total: int) -> None:
                if index <= 0:
                    progress_bar.progress(0.08)
                    progress_text.markdown(
                        f"**Ein Gemini-Call** für **{folder_name}** — "
                        f"**{segment_count}** Whisper-Segmente + alle Asset-Beschreibungen "
                        f"gebündelt ({format_gemini_model_label(timing.gemini_model)}). "
                        "Es wird **nicht** segmentweise aufgerufen — bitte warten …"
                    )
                else:
                    progress_bar.progress(1.0)
                    progress_text.caption(
                        f"Gemini-Antwort für **{folder_name}** wird verarbeitet …"
                    )

            with st.spinner(
                f"Gemini plant {selected_folder} gesamtheitlich "
                f"({segment_count} Segmente in einem Call) …"
            ):
                result = build_edit_plan(
                    project,
                    settings,
                    use_api=use_gemini,
                    folder_names=[selected_folder],
                    rules_doc=rules_doc,
                    progress_callback=_on_plan_progress,
                )
            progress_bar.empty()
            progress_text.empty()
            if result.status == EditPlanBuildStatus.BLOCKED or result.document is None:
                error_lines = [
                    plan_validation_error_to_message(PlanValidationError.from_dict(entry))
                    if isinstance(entry, dict)
                    else str(entry)
                    for entry in (result.validation_errors or [])
                ]
                st.session_state[generate_result_key] = {
                    "ok": False,
                    "message": f"Schnittplan BLOCKED nach {result.retry_attempts} Versuchen.",
                    "blocked": True,
                    "validation_errors": error_lines[:12],
                    "used_rules": result.used_rules or {},
                    "notes": list(result.plan_generation_notes or []),
                }
                st.rerun()
            document = result.document
            export_opts = export_rule_options(rules_doc)
            if export_opts.folder_title_enabled:
                timeline_items, title_notes = ensure_opening_titles_rendered(
                    project,
                    document.timeline_items,
                )
                document = document.model_copy(update={"timeline_items": timeline_items})
            _set_draft(document, selected_folder)
            st.toast(f"✅ {len(document.shots)} Shots für {selected_folder} vorgeschlagen.", icon="✅")
            generate_notes = list(title_notes)
            generate_notes.extend(document.plan_generation_notes)
            title_item = next(
                (item for item in document.timeline_items if item.type == "opening_title"),
                None,
            )
            if title_item is not None and title_item.title_style is not None:
                style = title_item.title_style
                generate_notes.insert(
                    0,
                    f"Titel: **{style.text}** · {style.requested_font_family} "
                    f"→ {style.resolved_font_family} · **{int(style.font_size_px)}px** · "
                    f"{style.duration_sec:.1f}s · hash `{style.render_hash}`"
                    + (" · Font-Fallback aktiv (siehe validation_report.json)." if style.font_fallback_used else ""),
                )
            st.session_state[generate_result_key] = {
                "ok": True,
                "message": f"{len(document.shots)} Shots vorgeschlagen.",
                "notes": generate_notes,
                "used_rules": document.used_rules,
                "validation_status": document.validation_status,
                "gemini_retry_attempts": document.gemini_retry_attempts,
            }
            st.rerun()
        except Exception as exc:  # noqa: BLE001 — Nutzer muss IMMER eine
            # sichtbare Fehlermeldung bekommen (vorher wurden nur
            # GeminiNotConfiguredError/ValueError/FileNotFoundError
            # abgefangen — jeder andere Fehler führte zu einem
            # unbehandelten Absturz).
            import traceback

            if progress_bar is not None:
                progress_bar.empty()
            if progress_text is not None:
                progress_text.empty()
            st.toast(f"❌ Fehler beim Vorschlagen: {exc}", icon="❌")
            st.session_state[generate_result_key] = {
                "ok": False,
                "message": f"Fehler beim Vorschlagen: {exc}",
                "traceback": traceback.format_exc(),
            }
            st.rerun()

    draft = _effective_draft(project.id, selected_folder, saved)
    if draft is not None:
        rules_doc = get_edit_plan_rules_for_project(project)
        missing, coverage_gap, rule_blocked = _missing_asset_breakdown(draft)
        violations = validate_shots_against_rules(draft.shots, rules_doc)
        st.caption(f"{len(draft.shots)} Shots · {missing} ohne Asset")
        if missing:
            st.caption(
                f"↳ davon {coverage_gap} durch Coverage-Lücke (→ ②½ Supplement Assets) "
                f"· {rule_blocked} durch Wiederverwendungsregel blockiert "
                "(→ Regeln lockern oder mehr lokale Assets, Supplementieren hilft hier nicht)"
            )
        if violations:
            st.warning("Regelverletzungen — ggf. unter „Regeln“ anpassen und neu generieren:")
            for line in violations[:15]:
                st.caption(f"• {line}")
            if len(violations) > 15:
                st.caption(f"… und {len(violations) - 15} weitere")


def _missing_asset_breakdown(draft: EditPlanDocument) -> tuple[int, int, int]:
    """Zerlegt „Shots ohne Asset“ in zwei unterschiedliche Ursachen.

    Bisher zeigten Vorschlag-Tab (Shot-Anzahl), Prüfen & Speichern-Tab
    (Beat-Anzahl mit SUPPLEMENT_REQUIRED) und Supplement-Assets-Tab
    (Request-Anzahl) unterschiedliche Zahlen (z. B. 11 / 4 / 4) — was wie ein
    Fehler wirkte. Tatsächlich zählen sie unterschiedliche Dinge:

    - coverage_gap: Shot gehört zu einem Beat, für den lokal kein inhaltlich
      passendes Asset gefunden wurde (SUPPLEMENT_REQUIRED). Hier hilft
      Supplementieren unter ②½.
    - rule_blocked: Für den Beat GÄBE es inhaltlich ein passendes Asset,
      aber eine Wiederverwendungsregel (Max. Asset-Nutzung / Min. Abstand)
      hat es blockiert. Hier hilft NUR Regeln lockern oder mehr lokale
      Assets — Supplementieren für DIESEN Beat bringt nichts, da die
      inhaltliche Abdeckung bereits ausreicht.

    Ein einzelner Beat kann mehrere Shots erzeugen (Textaufteilung) — daher
    ist die Shot-Anzahl grundsätzlich >= der Beat-/Request-Anzahl.
    """
    missing_shots = [shot for shot in draft.shots if not shot.asset_path]
    coverage_gap = sum(
        1 for shot in missing_shots if shot.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    )
    rule_blocked = len(missing_shots) - coverage_gap
    return len(missing_shots), coverage_gap, rule_blocked


def _render_tab_review(
    project,
    selected_folder: str,
    saved: EditPlanDocument | None,
    plan_path: Path,
) -> None:
    draft = _effective_draft(project.id, selected_folder, saved)
    if draft is None or (not draft.shots and not draft.timeline_items):
        st.info(
            f"Noch kein Vorschlag für **{selected_folder}** — "
            "zuerst unter „Vorschlag“ generieren."
        )
        return

    rules_doc = get_edit_plan_rules_for_project(project)
    rules = export_rule_options(rules_doc)
    _render_plan_validation_panel(project, draft)

    final_validation = validate_document_for_confirm(
        draft,
        rules_doc=rules_doc,
        allow_asset_rule_overrides=True,
    )
    blocking_validation_errors = [
        error
        for error in final_validation.errors
        if error.type not in ASSET_RULE_ERROR_TYPES
    ]
    if not plan_is_confirmable(draft):
        st.error("Bestätigung blockiert — Schnittplan ist BLOCKED oder fehlgeschlagen.")
    elif blocking_validation_errors:
        st.error("Finale Validierung fehlgeschlagen — Bestätigung blockiert:")
        for line in format_validation_error_entries(blocking_validation_errors)[:12]:
            st.caption(f"• {line}")

    if draft.inventory_hash_at_plan_time and inventory_hash_is_stale(
        project,
        selected_folder,
        draft.inventory_hash_at_plan_time,
    ):
        st.warning(
            "Inventory geändert — bitte Schnittplan mit neuen Assets neu vorschlagen. "
            f"(Plan-Hash `{draft.inventory_hash_at_plan_time}`, "
            f"aktuell `{current_folder_inventory_hash(project, selected_folder)}`)"
        )

    usage_blockers = validate_max_asset_usage_blockers(
        timeline_items=draft.timeline_items,
        rules_doc=rules_doc,
    )
    confirm_blocked = not plan_is_confirmable(draft) or bool(blocking_validation_errors)
    if usage_blockers:
        st.warning("max_asset_usage verletzt — Bestätigung trotzdem möglich:")
        for violation in usage_blockers:
            st.caption(
                f"• `{violation.asset_id}`: {violation.usage_count}× "
                f"(max {violation.max_allowed})"
            )

    unpassend_shots = [
        shot for shot in draft.shots if shot.match_quality == MATCH_QUALITY_UNPASSEND
    ]
    if unpassend_shots:
        st.warning(
            f"{len(unpassend_shots)} Shot(s) als **unpassend** bewertet — "
            "nutzen ein Platzhalter-Asset. Du kannst unten **Supplement Assets** starten."
        )

    weak_with_asset = [
        shot
        for shot in draft.shots
        if shot.asset_path
        and shot.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    ]
    missing, coverage_gap, rule_blocked = _missing_asset_breakdown(draft)
    supplement_beats = [
        coverage
        for coverage in draft.segment_coverage
        if coverage.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    ]
    if supplement_beats and not unpassend_shots:
        st.warning(
            f"{len(supplement_beats)} Beat(s) mit SUPPLEMENT_REQUIRED — "
            "bitte unter **②½ Supplement Assets** ergänzen."
        )
    if missing:
        st.caption(
            f"Insgesamt {missing} Shot(s) ohne Asset — davon {coverage_gap} durch "
            f"Coverage-Lücke (s. o.) · {rule_blocked} durch Wiederverwendungsregel "
            "(Max. Asset-Nutzung / Min. Abstand) blockiert, obwohl inhaltlich "
            "ausreichend lokale Assets vorhanden wären. Ein einzelner Beat kann "
            "mehrere Shots erzeugen — daher ist die Shot-Anzahl hier höher als "
            "die Beat-/Request-Anzahl oben bzw. unter ②½."
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
    _render_match_quality_summary(draft.shots)
    rules_doc = get_edit_plan_rules_for_project(project)
    violations = validate_shots_against_rules(draft.shots, rules_doc)
    if rules_doc.gemini_prompt.strip():
        st.caption("Gemini-Zusatzhinweise sind aktiv — beim Neu-Generieren berücksichtigt.")
    if violations:
        st.warning(f"{len(violations)} Regelverletzung(en) im aktuellen Vorschlag.")

    supplementable_shots = [
        (index, shot)
        for index, shot in enumerate(draft.shots, start=1)
        if not shot.asset_path or shot.match_quality == MATCH_QUALITY_UNPASSEND
    ]
    if supplementable_shots:
        unpassend_count = sum(
            1 for _, shot in supplementable_shots if shot.match_quality == MATCH_QUALITY_UNPASSEND
        )
        missing_count = len(supplementable_shots) - unpassend_count
        st.markdown("### Supplement Assets starten")
        detail_parts: list[str] = []
        if unpassend_count:
            detail_parts.append(f"{unpassend_count} unpassend (Platzhalter aktiv)")
        if missing_count:
            detail_parts.append(f"{missing_count} ohne Asset")
        st.caption(
            f"{len(supplementable_shots)} Shot(s) — "
            + " · ".join(detail_parts)
            + ". Batch-Suche startet Supplement-Kandidaten für alle."
        )
        source = st.selectbox(
            "Quelle für Supplement-Suche",
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
            "Supplement-Kandidaten suchen",
            key=f"batch_supplement_search_{project.id}_{safe_folder_slug(selected_folder)}",
        ):
            try:
                existing = load_supplement_requests(project)
                existing_ids = {request.supplement_request_id for request in existing.requests}
                requests: list[SupplementRequest] = []
                for shot_index, shot in supplementable_shots:
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
                        reason = (
                            "Gemini bewertete das Asset als unpassend — Supplement-Suche gestartet."
                            if shot.match_quality == MATCH_QUALITY_UNPASSEND
                            else "Schnittplan enthält für dieses Voice-over-Segment kein Asset. "
                            "Batch-Suche aus dem Schnittplan gestartet."
                        )
                        request = SupplementRequest(
                            supplement_request_id=request_id,
                            section_id=section_id_for_folder(selected_folder),
                            folder_name=selected_folder,
                            beat_id=shot.beat_id or f"shot_{shot_index:03d}",
                            passage_text=passage,
                            visual_requirement=shot.motif or passage,
                            duration_needed_sec=max(0.1, shot.duration_sec),
                            reason=reason,
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
        with st.expander(
            _shot_expander_title(index, shot),
            expanded=index < 2 and not shot.section_outro,
        ):
            _render_match_quality_badge(shot)
            st.write(f"**Motiv:** {shot.motif or '—'}")
            st.write(f"**Voice:** {shot.voice_start_sec:.1f}–{shot.voice_end_sec:.1f}s")
            st.caption(shot.passage_text)
            if shot.asset_path:
                st.write(f"**Asset:** `{Path(shot.asset_path).name}`")
                if shot.match_quality == MATCH_QUALITY_UNPASSEND:
                    st.caption("Platzhalter-Asset (Establishing/Luftaufnahme)")
                if shot.asset_origin and shot.asset_origin != "local_original":
                    st.caption(
                        f"Supplement: {shot.provider or shot.asset_origin} · "
                        f"rights={shot.rights_status or '—'}"
                    )
            else:
                st.warning(
                    "Kein Asset — bitte unter **②½ Supplement Assets** ergänzen "
                    "oder Schnittplan neu vorschlagen. Beim Bestätigen wird "
                    "andernfalls automatisch das nächstbeste verfügbare Asset "
                    "aus dem Ordner verwendet."
                )

    # Ergebnis eines vorherigen Bestätigen-Klicks prominent anzeigen — auch
    # nach dem durch st.rerun() ausgelösten Neuladen. Auf einer langen Seite
    # (viele Shot-Expander oberhalb) kann eine Inline-Meldung direkt nach dem
    # Button sonst unbemerkt bleiben, wenn der Browser beim Rerun nach oben
    # scrollt — der Nutzer hat dann den Eindruck, der Klick habe "nichts
    # gemacht", obwohl im Hintergrund ein Fehler aufgetreten ist.
    result_key = _confirm_result_key(project.id, selected_folder)
    pending_result = st.session_state.pop(result_key, None)
    if pending_result is not None:
        if pending_result["ok"]:
            st.success(pending_result["message"])
            for note in pending_result.get("notes", []):
                st.caption(f"• {note}")
        else:
            st.error(pending_result["message"])
            if pending_result.get("traceback"):
                with st.expander("Technische Details (Traceback)", expanded=False):
                    st.code(pending_result["traceback"])

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
            st.toast("⚠️ Bitte zuerst die Checkbox aktivieren.", icon="⚠️")
            st.warning(
                "Bitte zuerst die Checkbox **oben** aktivieren — "
                "ohne Bestätigung wird der Schnittplan nicht exportierbar gespeichert."
            )
        elif confirm_blocked:
            st.toast("❌ Bestätigung blockiert (Validierung).", icon="❌")
            st.error(
                "Bestätigung blockiert — bitte Validierungsfehler oben beheben "
                "oder Schnittplan neu vorschlagen."
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
                st.toast(f"✅ {selected_folder} bestätigt und gespeichert.", icon="✅")
                st.session_state[result_key] = {
                    "ok": True,
                    "message": f"Bestätigt und gespeichert: `{plan_path}`",
                    "notes": finalize_notes,
                }
                st.rerun()
            except Exception as exc:  # noqa: BLE001 — Nutzer muss IMMER eine
                # sichtbare Fehlermeldung bekommen, egal welcher Fehlertyp
                # auftritt (vorher wurden nur OSError/ValueError abgefangen —
                # jeder andere Fehler, z. B. beim Opening-Title-Rendering,
                # führte zu einem unbehandelten Absturz, der wie "nichts
                # passiert" wirken konnte, wenn die Meldung überscrollt war).
                import traceback

                st.toast(f"❌ Fehler beim Bestätigen: {exc}", icon="❌")
                st.session_state[result_key] = {
                    "ok": False,
                    "message": f"Fehler beim Bestätigen: {exc}",
                    "traceback": traceback.format_exc(),
                }
                st.rerun()

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


def _export_warning_is_error(warning: str) -> bool:
    return warning.startswith("Validierung:") and not warning.startswith(
        "Regel-Hinweis (Export trotzdem möglich):"
    )


def _sync_export_filename_widget(
    project_id: str,
    folder_selection: tuple[str, ...],
    project_name: str,
) -> str:
    """Hält den Dateinamen-Default synchron, wenn sich die Ortsauswahl ändert."""
    name_key = f"otio_export_name_{project_id}"
    folders_key = f"otio_export_name_folders_{project_id}"
    default_basename = default_otio_export_basename(
        project_name=project_name,
        folder_names=folder_selection,
    )
    if (
        name_key not in st.session_state
        or st.session_state.get(folders_key) != folder_selection
    ):
        st.session_state[name_key] = default_basename
        st.session_state[folders_key] = folder_selection
    return str(st.session_state[name_key])


def _render_tab_export(project, mapped_folders: list[str]) -> None:
    export_timing = _export_timing_settings(project)
    st.markdown("**OTIO-Timeline aus bestätigten Schnittplänen**")
    st.caption(
        "Orte wählen, Dateiname anpassen, dann **OTIO exportieren** — Vorschau ist optional. "
        "Audio-Start und Ausklingen sind **pro Ort fest im Schnittplan verankert** "
        "(Wert beim **Schnittplan vorschlagen/bestätigen**, Tab **Regeln → Timing & Gemini**) — "
        "der Export übernimmt sie unverändert je Ort, unabhängig von der aktuell "
        "eingestellten globalen Regel. Um Audio-Start/Ausklingen für einen Ort zu ändern, "
        "Schnittplan dort neu vorschlagen und erneut bestätigen."
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
    export_basename = _sync_export_filename_widget(
        project.id,
        folder_selection,
        project.name,
    )
    export_basename = st.text_input(
        "Dateiname (ohne .otio)",
        key=f"otio_export_name_{project.id}",
        help="Standard bei genau einem Ort: Ordnername. Bei mehreren Orten: Projektname.",
    )
    export_path = resolve_otio_export_path(project.work_dir_path, basename=export_basename)
    st.caption(f"Ziel: `{export_path}`")
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
                        if _export_warning_is_error(warning):
                            st.error(warning)
                        elif warning.startswith("Regel-Hinweis (Export trotzdem möglich):"):
                            st.warning(warning)
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
                        output_path=export_path,
                    )
                    st.success(f"Timeline exportiert: `{export_result.path}`")
                    for warning in merged.warnings:
                        if warning.startswith("Regel-Hinweis (Export trotzdem möglich):"):
                            st.warning(warning)
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
            if _export_warning_is_error(warning):
                st.error(warning)
            elif warning.startswith("Regel-Hinweis (Export trotzdem möglich):"):
                st.warning(warning)
            else:
                st.caption(f"• {warning}")

        if preview.ready:
            from otio_app.services.otio_exporter import _compute_timeline_sections

            # Zeigt die TATSÄCHLICH je Ort fest verankerten Werte (aus dem
            # jeweiligen bestätigten Schnittplan) — nicht die aktuelle globale
            # Regeln-Einstellung, die inzwischen davon abweichen kann.
            timeline_sections = _compute_timeline_sections(
                preview.timeline_items,
                preview.settings,
                preview.voiceovers,
            )
            total_duration = sum(section.video_duration_sec for section in timeline_sections)
            st.caption(f"Geschätzte Videospur: {total_duration:.1f}s · {project.fps} fps")
            for section in timeline_sections:
                voice_start = section.video_start_sec + section.voiceover.timeline_start_sec
                st.caption(
                    f"• **{section.folder}** — Video ab {section.video_start_sec:.1f}s "
                    f"({section.video_duration_sec:.1f}s), Voice ab {voice_start:.1f}s "
                    f"(Audio-Start {section.voiceover.timeline_start_sec:.1f}s, "
                    f"im Schnittplan verankert)"
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
        "Bei aktivem Auto-Zoom (⓪ Clean Media) entstehen Dateien wie `Asset03_3840x2160.mp4` — "
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
    _sync_draft_from_saved_if_newer(project, selected_folder, saved)
    if saved is not None and saved.confirmed:
        inventory_stale = (
            saved.inventory_hash_at_plan_time
            and inventory_hash_is_stale(
                project,
                selected_folder,
                saved.inventory_hash_at_plan_time,
            )
        )
        if inventory_stale:
            st.warning(
                f"Schnittplan für **{selected_folder}** war bestätigt, "
                "aber das Inventory hat sich geändert — bitte unter **Vorschlag** "
                "neu vorschlagen und erneut bestätigen."
            )
        else:
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
