"""Phase 8.4-9.1: Cut-Plan-Tab — Validierung, Visual Coverage, Supplement Bridge,
Confirm+Trace, isolierte EditPlan-Bridge.

Baut auf Phase 8.2 (Timeline-/Audio-Platzierung) und Phase 8.3 (Asset-
Auswahl, Fallback, Dauer-/Split-/Merge) auf und ergänzt die vollständige
Cut-Plan-Validierung (siehe cut_plan_validator.py). Phase 8.5 ergänzt den
Visual-Coverage-Fix (siehe cut_plan_visual_coverage.py), der bereits
während der Asset-Auswahl automatisch angewendet wird, damit initialer
Audio-Vorlauf und Sektions-Pausen nicht zu Schwarzbild führen. Phase 8.6
ergänzt eine isolierte Supplement Bridge (siehe cut_plan_supplement_bridge.py):
Kandidatensuche und Downloads laufen AUSSCHLIESSLICH bei explizitem
Nutzerklick, niemals automatisch. Phase 8.7 ergänzt Confirm + Trace (siehe
cut_plan_confirm_service.py/cut_plan_trace_service.py): ein bereits
validierter Draft kann explizit als unveränderlicher Snapshot bestätigt
werden. Phase 9.1 ergänzt eine isolierte EditPlan-Bridge (siehe
cut_plan_edit_plan_bridge.py/cut_plan_edit_plan_trace.py): der bestätigte
Cut Plan wird deterministisch in einen EditPlanDocument-kompatiblen Draft
übersetzt — komplett getrennt von der bestehenden Produktions-EditPlan-
Pipeline. Phase 9.2 härtet diese Bridge: Boundary-Chaining pro Track
verhindert 1-Frame-Gaps/-Overlaps, eine strukturierte bridge_audio_plan.json
ersetzt das Rätselraten am TimelineItem-Sondertyp 'voiceover_audio', und die
Bridge-Validierung wurde verschärft. Phase 9.3 ergänzt Confirm/Freeze (siehe
cut_plan_edit_plan_confirm_service.py): ein bereits validierter Bridge-Draft
kann explizit als unveränderlicher Snapshot eingefroren werden — weiterhin
KEIN Produktions-EditPlan, KEIN locked Produktionsplan, KEIN OTIO-Export,
kein Render, keine neue LLM-Planung."""

from __future__ import annotations

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_AUTO_RESOLVE_PROVIDER_LOCAL_REUSE,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING,
    PLAN_STATUS_READY_FOR_CUT,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED,
    VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED,
    VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_SKIP_INTRO,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_STATUS_STAGED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING,
    PROVIDER_STATUS_READY,
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_LABELS,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_draft_path,
    get_cut_plan_edit_plan_bridge_audio_plan_path,
    get_cut_plan_edit_plan_bridge_draft_path,
    get_cut_plan_settings_path,
    get_cut_plan_supplement_requests_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
    get_folder_edit_plan_path,
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_production_edit_plan_promote_manifest_path,
    get_production_edit_plan_validation_report_path,
    get_production_edit_plan_voice_folder_mapping_patch_path,
)
from otio_app.services.supplement_search import build_keyword_query
from otio_app.services.supplement_sources import get_provider_readiness
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    is_cut_plan_draft_stale,
    is_cut_plan_settings_stale,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import (
    can_confirm_cut_plan,
    confirm_cut_plan,
    is_confirmed_cut_plan_stale,
    load_confirmed_cut_plan,
    unconfirm_cut_plan,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanSettings,
    CutPlanValidationError,
    CutPlanValidationReport,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    load_cut_plan_settings,
    save_cut_plan_settings,
)
from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import (
    apply_generic_fallback_for_cut_plan_request,
    apply_manual_asset_for_cut_item,
    apply_manual_asset_for_cut_plan_request,
    list_manual_asset_options_for_cut_item,
    list_manual_asset_options_for_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
    AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
    AUTO_RESOLVE_PROGRESS_GENERIC_FALLBACK_STARTED,
    AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_FINISHED,
    AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_STARTED,
    AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED,
    AUTO_RESOLVE_PROGRESS_REQUEST_STARTED,
    AUTO_RESOLVE_PROGRESS_STAGE_FAILED,
    AUTO_RESOLVE_PROGRESS_STAGE_NO_RESULTS,
    AUTO_RESOLVE_PROGRESS_STAGE_STARTED,
    AUTO_RESOLVE_STATUS_ACCEPTED,
    AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
    AUTO_RESOLVE_STATUS_NO_MATCH,
    AutoResolveProgressEvent,
    auto_resolve_all_cut_plan_supplement_requests,
    auto_resolve_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    build_supplement_requests_from_cut_plan,
    count_unapplied_accepted_supplement_requests,
    effective_cut_plan_supplement_request_status,
    is_open_cut_plan_supplement_request,
    load_cut_plan_supplement_candidates_for_request,
    load_cut_plan_supplement_requests,
    merge_prior_supplement_request_state,
    reapply_accepted_supplements_to_cut_plan,
    save_cut_plan_supplement_requests,
    search_candidates_for_cut_plan_request,
    unaccept_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_apply import (
    reapply_accepted_residual_gap_assets,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_auto_resolve_service import (
    AUTO_RESOLVE_STATUS_ACCEPTED as RESIDUAL_GAP_AUTO_RESOLVE_STATUS_ACCEPTED,
    AUTO_RESOLVE_STATUS_NO_MATCH as RESIDUAL_GAP_AUTO_RESOLVE_STATUS_NO_MATCH,
    auto_resolve_all_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    build_residual_gap_requests_from_cut_plan,
    count_unapplied_accepted_residual_gap_requests,
    load_residual_gap_requests,
    merge_prior_residual_gap_request_state,
    save_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    build_validation_repair_requests_from_cut_plan,
    count_black_gap_blockers_on_draft,
    count_black_gap_items_without_gap_bounds,
    load_cut_plan_validation_repair_requests,
    save_cut_plan_validation_repair_requests,
)
from otio_app.services.voiceover_generation.cut_plan_visual_gap_analysis import (
    GAP_KIND_FULL_ITEM_MISSING,
    GAP_KIND_MINI_REPAIRABLE_GAP,
    GAP_KIND_RESIDUAL_ITEM_GAP,
    GAP_KIND_UNATTRIBUTED_GAP,
    analyze_visual_gaps,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_resolve_service import (
    auto_resolve_all_validation_repair_requests,
    auto_resolve_validation_repair_request,
)
from otio_app.services.voiceover_generation.cut_plan_workflow_state import (
    CUT_PLAN_WORKFLOW_ACTION_APPLY_ASSET_SELECTION,
    CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_RESIDUAL_GAPS,
    CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_SUPPLEMENTS,
    CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_VALIDATION_REPAIR,
    CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT,
    CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS,
    CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS,
    CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS,
    CUT_PLAN_WORKFLOW_ACTION_REAPPLY_RESIDUAL_GAP_ASSETS,
    CUT_PLAN_WORKFLOW_ACTION_REAPPLY_SUPPLEMENT_ASSETS,
    CUT_PLAN_WORKFLOW_ACTION_VALIDATE,
    CUT_PLAN_WORKFLOW_STATUS_BLOCKED,
    CUT_PLAN_WORKFLOW_STATUS_DONE,
    CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
    CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
    CUT_PLAN_WORKFLOW_STATUS_READY,
    CUT_PLAN_WORKFLOW_STATUS_STALE,
    compute_cut_plan_workflow_state,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    is_edit_plan_bridge_stale,
    load_bridge_audio_plan,
    load_edit_plan_bridge_draft,
    load_edit_plan_bridge_validation_report,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import (
    can_confirm_edit_plan_bridge,
    confirm_edit_plan_bridge,
    is_confirmed_edit_plan_bridge_stale,
    load_confirmed_bridge_audio_plan,
    load_confirmed_bridge_trace,
    load_confirmed_edit_plan_bridge,
    load_edit_plan_bridge_confirm_manifest,
    unconfirm_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    load_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_trace_service import load_cut_plan_trace
from otio_app.services.voiceover_generation.cut_plan_validator import (
    content_hash_of_cut_plan_content,
    group_cut_plan_errors_by_type,
    load_cut_plan_validation_report,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    load_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
    build_voice_folder_mapping_patch,
    can_promote_production_edit_plans,
    is_production_edit_plan_promote_manifest_stale,
    load_production_edit_plan_promote_manifest,
    load_voice_folder_mapping_patch,
    promote_production_edit_plans,
    save_production_edit_plan_promote_manifest,
    save_voice_folder_mapping_patch,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge import (
    can_merge_voice_folder_mapping,
    is_voice_folder_mapping_merge_manifest_stale,
    load_voice_folder_mapping_merge_manifest,
    merge_voice_folder_mapping,
    save_voice_folder_mapping_merge_manifest,
)
from otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness import (
    build_otio_export_readiness_report,
    load_otio_export_readiness_report,
    save_otio_export_readiness_report,
)
from otio_app.services.voiceover_generation.production_edit_plan_pipeline_overview import (
    build_production_edit_plan_pipeline_overview,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_readiness import (
    build_production_edit_plan_promote_dry_run_trace,
    build_production_edit_plan_promote_readiness,
    is_production_edit_plan_promote_readiness_stale,
    load_production_edit_plan_promote_dry_run_trace,
    load_production_edit_plan_promote_readiness,
    save_production_edit_plan_promote_dry_run_trace,
    save_production_edit_plan_promote_readiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    can_build_production_edit_plan_staging,
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import (
    load_production_edit_plan_mapping_trace,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    is_production_edit_plan_validation_report_stale,
    load_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    render_llm_model_selectbox,
    require_without_voiceover_mode,
)

import streamlit as st

# Nutzervorgabe (Juli 2026): ALLE neuen Buttons, die im Rahmen des
# "Visual Window bis zum nächsten Satz"-Feature-Sets entstehen (Phase 1-8,
# u. a. der spätere manuelle "Als Fortsetzung desselben Clips erlauben"-
# Button), sollen sich optisch klar von den bestehenden Buttons abheben.
# Konvention: jeder neue Button dieses Feature-Sets bekommt einen `key`,
# der mit diesem Präfix beginnt — die untenstehende CSS-Regel färbt genau
# diese Buttons grün (über Streamlits `st-key-<key>`-CSS-Klasse, siehe
# https://docs.streamlit.io -> "Style specific elements via key"). Bewusst
# NICHT global auf alle Buttons angewendet, damit bestehende `type="primary"`-
# Buttons (Theme-Farbe) unverändert bleiben.
NEW_FEATURE_BUTTON_KEY_PREFIX = "cut_plan_vwindow_"


def _inject_new_feature_button_css() -> None:
    """Färbt alle Buttons mit `key.startswith(NEW_FEATURE_BUTTON_KEY_PREFIX)`
    grün — einmal pro Seitenaufruf aufrufen (idempotent, reines CSS)."""
    st.markdown(
        f"""
        <style>
        [class*="st-key-{NEW_FEATURE_BUTTON_KEY_PREFIX}"] button {{
            background-color: #1e7e34 !important;
            color: #ffffff !important;
            border-color: #1c7430 !important;
        }}
        [class*="st-key-{NEW_FEATURE_BUTTON_KEY_PREFIX}"] button:hover {{
            background-color: #218838 !important;
            border-color: #1c7430 !important;
            color: #ffffff !important;
        }}
        [class*="st-key-{NEW_FEATURE_BUTTON_KEY_PREFIX}"] button:disabled {{
            background-color: #a3c9ad !important;
            border-color: #a3c9ad !important;
            color: #f0f0f0 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_source_plan_status(project: Project) -> None:
    st.subheader("Voraussetzung: bestätigter Voice-over-Projektplan")
    plan = load_confirmed_voiceover_project_plan(project)

    if plan is None:
        st.warning(
            "Noch kein `confirmed_voiceover_project_plan.json` vorhanden. "
            "Bitte zuerst im Tab „⑦ Final Output“ einen finalen Plan erstellen."
        )
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Projekt", plan.project_title or "(ohne Titel)")
    with col2:
        st.metric("Sprache", plan.language)
    with col3:
        st.metric("Project Plan Status", plan.status)
    with col4:
        st.metric("Ready for Cut", "✅ Ja" if plan.status == PLAN_STATUS_READY_FOR_CUT else "❌ Nein")

    if plan.status != PLAN_STATUS_READY_FOR_CUT:
        st.info(
            "Der Voice-over-Projektplan ist noch nicht vollständig schnittbereit. "
            "Ein Cut-Plan-Entwurf wird in einer späteren Phase auch aus einem "
            "noch nicht vollständig bereiten Plan erzeugbar sein (zur Diagnose), "
            "eine Bestätigung des Cut Plans wird aber erst möglich sein, wenn "
            "„Ready for Cut“ hier ja ist."
        )


def _render_settings_editor(project: Project) -> CutPlanSettings:
    st.subheader("Cut Plan Settings")
    st.caption(
        "Eigenständige Einstellungen für diese Pipeline — unabhängig von den "
        "Schnittplan-Regeln des „Projekt mit Voice-Over“-Workflows."
    )
    settings = load_cut_plan_settings(project)

    col1, col2, col3 = st.columns(3)
    with col1:
        initial_audio_offset_sec = st.number_input(
            "Initial Audio Offset (s)",
            min_value=0.0, max_value=10.0, value=settings.initial_audio_offset_sec, step=0.1,
            key=f"cut_plan_initial_offset_{project.id}",
            help="Gilt einmalig am Anfang des gesamten Videos, nicht pro Sektion.",
        )
        pause_between_sections_sec = st.number_input(
            "Pause zwischen Sektionen (s)",
            min_value=0.0, max_value=5.0, value=settings.pause_between_sections_sec, step=0.05,
            key=f"cut_plan_pause_{project.id}",
            help="Pause zwischen Intro und Foldern bzw. zwischen Foldern. "
            "Kein Schwarzbild — das letzte Visual der vorherigen Sektion wird gehalten.",
        )
        section_visual_preroll_sec = st.number_input(
            "Visueller Vorlauf je Sektion (s)",
            min_value=0.0, max_value=5.0, value=settings.section_visual_preroll_sec, step=0.1,
            key=f"cut_plan_preroll_{project.id}",
        )
        video_head_trim_sec = st.number_input(
            "Video Head Trim (s) — nur Video, nie Bild/Audio",
            min_value=0.0, max_value=5.0, value=settings.video_head_trim_sec, step=0.1,
            key=f"cut_plan_head_trim_{project.id}",
        )
    with col2:
        shot_min_sec = st.number_input(
            "Shot Min (s)", min_value=0.5, max_value=30.0, value=settings.shot_min_sec, step=0.5,
            key=f"cut_plan_shot_min_{project.id}",
        )
        shot_max_sec = st.number_input(
            "Shot Max (s)", min_value=0.5, max_value=60.0, value=settings.shot_max_sec, step=0.5,
            key=f"cut_plan_shot_max_{project.id}",
        )
        max_asset_usage = st.number_input(
            "Max Asset Usage (global, Intro zählt mit)",
            min_value=1, max_value=20, value=settings.max_asset_usage, step=1,
            key=f"cut_plan_max_usage_{project.id}",
        )
        min_asset_reuse_distance_shots = st.number_input(
            "Min. Wiederverwendungsabstand (Shots)",
            min_value=0, max_value=50, value=settings.min_asset_reuse_distance_shots, step=1,
            key=f"cut_plan_min_reuse_distance_{project.id}",
        )
        black_gap_auto_hold_max_sec = st.number_input(
            "Black-Gap Auto-Hold max. (s)",
            min_value=0.0, max_value=5.0, value=settings.black_gap_auto_hold_max_sec, step=0.1,
            key=f"cut_plan_black_gap_auto_hold_{project.id}",
            help="Lücken bis zu dieser Länge zwischen zwei VisualSegments werden "
            "automatisch durch Halten des VORHERIGEN Bildes/Videos geschlossen "
            "(keine Supplement-Suche nötig). Größere Lücken bleiben als "
            "BLACK_GAP_DURING_VOICEOVER sichtbar. Höhere Werte schließen mehr "
            "natürliche Sprechpausen automatisch, können aber Standbilder länger "
            "halten.",
        )
        extend_visual_window_to_next_sentence = st.checkbox(
            "Visuelles Fenster bis zum nächsten Satz ausdehnen",
            value=settings.extend_visual_window_to_next_sentence,
            key=f"cut_plan_extend_visual_window_{project.id}",
            help="Phase 1/Vorbereitung: berechnet pro Satz ein erweitertes visuelles "
            "Fenster bis zum Start des NÄCHSTEN Satzes (statt exakt am eigenen "
            "Satzende zu enden) — deckt die Sprechpause dazwischen mit ab, "
            "eliminiert dadurch viele BLACK_GAP_DURING_VOICEOVER-Fälle bereits bei "
            "der Asset-Auswahl. Wirkt sich aktuell NUR auf die reine Berechnung "
            "aus; die Split-/Asset-Auswahl-Logik selbst nutzt dieses Fenster erst "
            "ab der nächsten Ausbaustufe.",
        )
        max_sentence_pause_extension_sec = st.number_input(
            "Max. Satzpausen-Ausdehnung (s)",
            min_value=0.0, max_value=15.0, value=settings.max_sentence_pause_extension_sec, step=0.5,
            key=f"cut_plan_max_sentence_pause_extension_{project.id}",
            disabled=not extend_visual_window_to_next_sentence,
            help="Obergrenze, wie viel von der Pause vor dem nächsten Satz in das "
            "visuelle Fenster des aktuellen Satzes hineingezogen wird. Längere "
            "Pausen (z. B. Kapitelwechsel) werden NICHT komplett überbrückt und "
            "bleiben als BLACK_GAP_DURING_VOICEOVER sichtbar.",
        )
    with col3:
        timeline_fps = st.number_input(
            "Timeline FPS", min_value=1, max_value=120, value=settings.timeline_fps, step=1,
            key=f"cut_plan_fps_{project.id}",
        )
        timeline_width = st.number_input(
            "Timeline Breite (px)", min_value=1, max_value=10000, value=settings.timeline_width, step=1,
            key=f"cut_plan_width_{project.id}",
        )
        timeline_height = st.number_input(
            "Timeline Höhe (px)", min_value=1, max_value=10000, value=settings.timeline_height, step=1,
            key=f"cut_plan_height_{project.id}",
        )

    updated = settings.model_copy(
        update={
            "initial_audio_offset_sec": float(initial_audio_offset_sec),
            "pause_between_sections_sec": float(pause_between_sections_sec),
            "section_visual_preroll_sec": float(section_visual_preroll_sec),
            "video_head_trim_sec": float(video_head_trim_sec),
            "shot_min_sec": float(shot_min_sec),
            "shot_max_sec": float(shot_max_sec),
            "max_asset_usage": int(max_asset_usage),
            "min_asset_reuse_distance_shots": int(min_asset_reuse_distance_shots),
            "black_gap_auto_hold_max_sec": float(black_gap_auto_hold_max_sec),
            "extend_visual_window_to_next_sentence": bool(extend_visual_window_to_next_sentence),
            "max_sentence_pause_extension_sec": float(max_sentence_pause_extension_sec),
            "timeline_fps": int(timeline_fps),
            "timeline_width": int(timeline_width),
            "timeline_height": int(timeline_height),
        }
    )

    col_save, col_reload = st.columns(2)
    with col_save:
        if st.button("Cut Plan Settings speichern", key=f"cut_plan_settings_save_{project.id}"):
            save_cut_plan_settings(project, updated)
            st.success("Cut Plan Settings gespeichert.")
            st.rerun()
    with col_reload:
        if st.button("Neu laden", key=f"cut_plan_settings_reload_{project.id}"):
            st.rerun()

    return updated


def _render_future_artifact_paths(project: Project) -> None:
    st.subheader("Künftige Artefakt-Pfade")
    st.caption(
        "cut_plan_settings.json und cut_plan.draft.json existieren bereits, sobald "
        "gespeichert bzw. erzeugt. Die übrigen Dateien folgen in späteren Sub-Phasen."
    )
    st.caption(f"Cut Plan Settings: `{get_cut_plan_settings_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Draft: `{get_cut_plan_draft_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Validation Report: `{get_cut_plan_validation_report_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Confirmed: `{get_cut_plan_confirmed_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Trace: `{get_cut_plan_trace_path(project.work_dir_path)}`")
    st.caption(
        f"Supplement Requests (isoliert): `{get_cut_plan_supplement_requests_path(project.work_dir_path)}`"
    )


def _render_grouped_cut_plan_errors(
    blockers: list[CutPlanValidationError], warnings: list[CutPlanValidationError]
) -> None:
    """Phase D (Nutzervorgabe): kompakte, nach Fehlertyp + Root-Cause-
    Kategorie gruppierte Übersicht statt einer langen Liste von
    Einzelmeldungen (siehe group_cut_plan_errors_by_type) — bei vielen
    offenen Cut-Plan-Items können sonst hunderte bis tausende
    Einzelzeilen entstehen (siehe Cut-Plan-Diagnose, Juli 2026), die die
    eigentlichen wenigen Ursachen unkenntlich machen."""
    if blockers:
        st.markdown(f"**Blocker nach Typ** ({len(blockers)} insgesamt)")
        st.dataframe(group_cut_plan_errors_by_type(blockers), use_container_width=True, hide_index=True)
    if warnings:
        st.markdown(f"**Warnungen nach Typ** ({len(warnings)} insgesamt)")
        st.dataframe(group_cut_plan_errors_by_type(warnings), use_container_width=True, hide_index=True)


def _render_visual_segments(item) -> None:
    if not item.planned_visual_segments:
        st.caption("Keine VisualSegments geplant.")
        return
    rows = [
        {
            "segment_id": segment.segment_id,
            "timeline_in_sec": segment.timeline_in_sec,
            "timeline_out_sec": segment.timeline_out_sec,
            "duration_sec": segment.duration_sec,
            "asset_id": segment.asset_id,
            "asset_type": segment.asset_type,
            "source_in_sec": segment.source_in_sec,
            "source_out_sec": segment.source_out_sec,
            "reason": segment.reason,
        }
        for segment in item.planned_visual_segments
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_cut_plan_draft(project: Project, draft: CutPlanDocument) -> None:
    st.subheader("Cut Plan Draft")

    if is_cut_plan_draft_stale(project, draft):
        st.warning("Der Cut Plan Draft ist veraltet. Bitte neu erzeugen.")
    if is_cut_plan_settings_stale(project, draft):
        st.warning("Die Cut-Plan-Settings wurden seit Draft-Erzeugung geändert. Bitte Draft neu erzeugen.")

    status_counts = {
        CUT_PLAN_ASSET_SELECTION_PRIMARY_USED: 0,
        CUT_PLAN_ASSET_SELECTION_BACKUP_USED: 0,
        CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED: 0,
        CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED: 0,
        CUT_PLAN_ASSET_SELECTION_BLOCKED: 0,
    }
    for item in draft.items:
        if item.asset_selection_status in status_counts:
            status_counts[item.asset_selection_status] += 1
    total_segments = sum(len(item.planned_visual_segments) for item in draft.items)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Status", draft.status)
    with col2:
        st.metric("Audio Items", len(draft.audio_items))
    with col3:
        st.metric("Cut Plan Items", len(draft.items))
    with col4:
        st.metric("VisualSegments", total_segments)

    col5, col6, col7, col8, col9 = st.columns(5)
    with col5:
        st.metric("PRIMARY_USED", status_counts[CUT_PLAN_ASSET_SELECTION_PRIMARY_USED])
    with col6:
        st.metric("BACKUP_USED", status_counts[CUT_PLAN_ASSET_SELECTION_BACKUP_USED])
    with col7:
        st.metric("SUPPLEMENT_REQUIRED", status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED])
    with col8:
        st.metric("SUPPLEMENT_USED", status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED])
    with col9:
        st.metric("BLOCKED", status_counts[CUT_PLAN_ASSET_SELECTION_BLOCKED])

    col10, col11 = st.columns(2)
    with col10:
        st.metric("Warnings", len(draft.warnings))
    with col11:
        st.metric("Blocker", len(draft.blockers))

    with st.expander("Audio Items (A1)", expanded=False):
        if not draft.audio_items:
            st.caption("Keine Audio Items vorhanden.")
        else:
            rows = [
                {
                    "scope": item.scope,
                    "folder_name": item.folder_name,
                    "audio_path": item.audio_path,
                    "timeline_start_sec": item.timeline_start_sec,
                    "timeline_end_sec": item.timeline_end_sec,
                    "duration_sec": item.duration_sec,
                    "track": item.track,
                }
                for item in draft.audio_items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Cut Plan Items", expanded=True):
        if not draft.items:
            st.caption("Keine Cut Plan Items vorhanden.")
        else:
            rows = [
                {
                    "cut_item_id": item.cut_item_id,
                    "scope": item.source_scope,
                    "folder_name": item.folder_name,
                    # Nutzervorgabe (Juli 2026): der Closing Shot hat bewusst
                    # keinen gesprochenen Text (siehe ClosingVisualPlan) —
                    # ohne diesen Marker wirkt die Zeile wie ein Datenfehler.
                    "text": item.text or ("🎬 (Closing Shot — kein Text)" if item.is_closing_shot else ""),
                    "timeline_start_sec": item.timeline_start_sec,
                    "timeline_end_sec": item.timeline_end_sec,
                    "primary_asset_id": item.primary_asset_id,
                    "chosen_asset_id": item.chosen_asset_id or "—",
                    "asset_selection_status": item.asset_selection_status,
                    "duration_strategy": item.duration_strategy or "—",
                    "number_of_visual_segments": len(item.planned_visual_segments),
                    "asset_selection_reason": item.asset_selection_reason or "—",
                    "fallback_reason": item.fallback_reason or "—",
                    "warnings": ", ".join(item.warnings) or "—",
                    "blockers": ", ".join(item.blockers) or "—",
                }
                for item in draft.items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

        with st.expander("VisualSegments je Item", expanded=False):
            for item in draft.items:
                st.caption(f"**{item.cut_item_id}** — {item.text[:80]}")
                _render_visual_segments(item)

    if draft.asset_usage_summary:
        with st.expander("Asset Usage Summary", expanded=False):
            rows = [
                {"asset_id": asset_id, "usage_count": count}
                for asset_id, count in sorted(draft.asset_usage_summary.items())
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    if draft.blockers or draft.warnings:
        with st.expander("Warnungen / Blocker", expanded=bool(draft.blockers)):
            _render_grouped_cut_plan_errors(draft.blockers, draft.warnings)
            if st.checkbox(
                "Alle Einzelmeldungen anzeigen",
                value=False,
                key=f"cut_plan_draft_error_details_{project.id}",
            ):
                for error in draft.blockers:
                    location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
                    st.error(f"[{error.type}]{location}: {error.message}")
                for error in draft.warnings:
                    location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
                    st.warning(f"[{error.type}]{location}: {error.message}")
            if any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in draft.blockers):
                st.markdown("---")
                st.markdown("**Direkt hier reparieren**")
                _render_black_gap_manual_repair(project, draft, compact=True)

    st.divider()
    _render_validation_report(project, draft)
    st.caption("🚧 Confirm/Lock, EditPlanDocument-Übersetzung, OTIO-Export folgen in späteren Sub-Phasen.")


def _render_validation_report(project: Project, draft: CutPlanDocument) -> None:
    st.subheader("Validation Report")
    report: CutPlanValidationReport | None = load_cut_plan_validation_report(project)

    if report is None:
        st.info("Noch kein Validation Report vorhanden. Bitte „Cut Plan validieren“ klicken.")
        return

    current_hash = content_hash_of_cut_plan_content(draft)
    if report.cut_plan_hash != current_hash:
        st.warning(
            "Der Validation Report ist veraltet — der Cut Plan hat sich seit der letzten "
            "Validierung geändert (z. B. durch erneute Asset-Auswahl). Bitte erneut validieren."
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        if report.status == CUT_PLAN_VALIDATION_STATUS_PASS:
            st.metric("Validation Status", "✅ PASS")
        elif report.status == CUT_PLAN_VALIDATION_STATUS_WARNING:
            st.metric("Validation Status", "⚠️ WARNING")
        elif report.status == CUT_PLAN_VALIDATION_STATUS_BLOCKED:
            st.metric("Validation Status", "❌ BLOCKED")
        else:
            st.metric("Validation Status", report.status)
    with col2:
        st.metric("Warnings", len(report.warnings))
    with col3:
        st.metric("Blockers", len(report.blockers))

    if not report.errors:
        st.success("Keine Warnungen oder Blocker.")
        return

    _render_grouped_cut_plan_errors(report.blockers, report.warnings)

    with st.expander("Alle Einzelmeldungen (Tabelle)", expanded=False):
        rows = [
            {
                "type": error.type,
                "severity": error.severity,
                "scope": error.scope,
                "cut_item_id": error.cut_item_id or "—",
                "folder_name": error.folder_name or "—",
                "message": error.message,
                "fix_hint": error.fix_hint or "—",
                "must_be_fixed_by": error.must_be_fixed_by,
                "is_retryable_by_llm": error.is_retryable_by_llm,
            }
            for error in report.errors
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _supplement_provider_readiness() -> tuple[bool, bool]:
    """Phase 12.4a: reine Statusermittlung (kein Netzwerkaufruf, nur lokale
    Key-Prüfung) — ohne UI-Ausgabe, für Wiederverwendung an mehreren Stellen
    der Seite (Einzel- und Sammel-Aktionen). Gibt (adobe_search_ready,
    pexels_search_ready) zurück."""
    adobe_ready = get_provider_readiness(SUPPLEMENT_SOURCE_ADOBE).status == PROVIDER_STATUS_READY
    pexels_ready = get_provider_readiness(SUPPLEMENT_SOURCE_PEXELS).status == PROVIDER_STATUS_READY
    return adobe_ready, pexels_ready


def _render_provider_readiness_status() -> tuple[bool, bool]:
    """Phase 12.4a: zeigt Adobe-/Pexels-Bereitschaft für die Supplement-
    Suche an. readiness() prüft nur lokal vorhandene API-Keys/Access-Token
    (kein Netzwerkaufruf) und ist daher jederzeit ohne eigenen Button sicher
    aufrufbar. Adobe unterscheidet zusätzlich zwischen 'Suche möglich' (nur
    ADOBE_STOCK_API_KEY nötig) und 'Lizenzierung/Download möglich' (zusätzlich
    ADOBE_STOCK_ACCESS_TOKEN nötig, siehe Phase 12.1/12.3)."""
    adobe_readiness = get_provider_readiness(SUPPLEMENT_SOURCE_ADOBE)
    pexels_readiness = get_provider_readiness(SUPPLEMENT_SOURCE_PEXELS)
    adobe_search_ready = adobe_readiness.status == PROVIDER_STATUS_READY
    pexels_search_ready = pexels_readiness.status == PROVIDER_STATUS_READY

    adobe_icon = "✅" if adobe_readiness.acquire_enabled else ("🟡" if adobe_search_ready else "⚪")
    pexels_icon = "✅" if pexels_search_ready else "⚪"
    st.caption(f"{adobe_icon} **Adobe Stock** — {adobe_readiness.message}")
    st.caption(f"{pexels_icon} **Pexels** — {pexels_readiness.message}")
    if not adobe_search_ready and not pexels_search_ready:
        st.warning(
            "Weder Adobe Stock noch Pexels ist konfiguriert — die Kandidatensuche liefert keine "
            "Treffer (nur der generische Ordner-Fallback bleibt möglich)."
        )
    return adobe_search_ready, pexels_search_ready


def _render_supplement_query_model_settings(project: Project) -> tuple[str, str]:
    """Phase 11.1: eigene, kleine Modellwahl NUR für die Suchquery-
    Generierung (Standard: Gemini 3.1 Flash Lite) — bewusst getrennt von den
    Autor-/Review-Modellen der Folder-Voice-overs-Seite, da diese Rolle nur
    kurze Suchqueries statt redaktioneller Texte erzeugt."""
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell (Suchqueries)", expanded=False):
        query_settings = render_llm_model_selectbox(
            label="Modell (LLM-Suchqueries)",
            role_settings=settings.cut_plan_supplement_query,
            key=f"cut_plan_supplement_query_model_{project.id}",
        )
        if st.button("Modell speichern", key=f"cut_plan_supplement_query_model_save_{project.id}"):
            updated = settings.model_copy(update={"cut_plan_supplement_query": query_settings})
            save_model_settings(project, updated)
            st.success("Modell-Einstellung gespeichert.")
    return query_settings.provider, query_settings.model


def _render_supplement_requests(project: Project, draft: CutPlanDocument) -> None:
    """Phase 8.6: isolierte Supplement Bridge. Externe Provider-Suche und
    Downloads laufen ausschließlich bei explizitem Klick auf „Kandidaten
    suchen“ bzw. „Akzeptieren“ — niemals automatisch beim Laden dieser Seite.

    Phase 11.1/11.2: Vor der Provider-Suche wird (sofern ein Modell gewählt
    ist) ein LLM-Aufruf ausgelöst, der bis zu drei englische, ortsbasierte
    Suchqueries generiert; die Suche selbst deckt jetzt Video UND Foto ab
    und schlägt bis zu 5 statt 3 Kandidaten vor."""
    st.subheader("Supplement Requests")
    st.caption(
        "Isolierte Supplement Requests aus dem Cut Plan — getrennt von der "
        "produktionsseitigen Supplement-Pipeline (`_otio/supplement/`). Suche und "
        "Download laufen ausschließlich bei explizitem Klick, nie automatisch."
    )
    query_llm_provider, query_llm_model = _render_supplement_query_model_settings(project)

    requests_document = load_cut_plan_supplement_requests(project)
    current_hash = content_hash_of_model(draft)

    if requests_document is None:
        if st.button(
            "Supplement Requests aus Cut Plan erzeugen",
            key=f"cut_plan_supplement_build_requests_{project.id}",
        ):
            prior_document = load_cut_plan_supplement_requests(project)
            new_document = build_supplement_requests_from_cut_plan(project, draft)
            new_document = merge_prior_supplement_request_state(new_document, prior_document)
            save_cut_plan_supplement_requests(project, new_document)
            st.success(f"{len(new_document.requests)} Supplement Request(s) erzeugt.")
            st.rerun()
        return

    if requests_document.source_cut_plan_hash != current_hash:
        st.warning(
            "Die Supplement Requests wurden aus einer älteren Version des Cut Plans erzeugt. "
            "Bitte bei Bedarf neu erzeugen."
        )
    if st.button(
        "Supplement Requests neu erzeugen",
        key=f"cut_plan_supplement_rebuild_requests_{project.id}",
    ):
        prior_document = requests_document
        new_document = build_supplement_requests_from_cut_plan(project, draft)
        new_document = merge_prior_supplement_request_state(new_document, prior_document)
        save_cut_plan_supplement_requests(project, new_document)
        st.success(f"{len(new_document.requests)} Supplement Request(s) erzeugt.")
        st.rerun()

    unapplied_accepted_count = count_unapplied_accepted_supplement_requests(draft, requests_document)
    if unapplied_accepted_count:
        st.warning(
            f"{unapplied_accepted_count} Request(s) haben bereits ein akzeptiertes Asset, das im aktuellen "
            "Cut Plan Draft aber noch nicht übernommen ist (z. B. nach Draft-Neu-Erzeugung)."
        )
        if st.button(
            "✅ Akzeptierte Supplement-Assets auf Cut Plan anwenden (ohne erneute Suche)",
            key=f"cut_plan_supplement_reapply_accepted_{project.id}",
            help="Übernimmt alle bereits akzeptierten Supplement-Dateien erneut in den Draft — "
            "spart externe Suchen/Lizenzierungen in Testläufen.",
        ):
            with st.spinner("Akzeptierte Supplement-Assets werden übernommen…"):
                _, applied, skipped = reapply_accepted_supplements_to_cut_plan(project)
            st.success(f"{len(applied)} Item(s) übernommen.")
            if skipped:
                st.warning(f"{len(skipped)} Item(s) übersprungen (Datei fehlt oder Asset zu kurz).")
            st.info("Bitte Cut Plan erneut validieren.")
            st.rerun()

    if not requests_document.requests:
        st.info("Keine Supplement Requests — kein Item benötigt aktuell ein Supplement-Asset.")
        return

    rows = [
        {
            "request_id": request.request_id,
            "cut_item_id": request.cut_item_id,
            "source_scope": request.source_scope,
            "folder_name": request.folder_name or "—",
            "text": request.text,
            "visual_intent": request.visual_intent or "—",
            "needed_duration_sec": request.needed_duration_sec,
            "reason": request.reason,
            "status": effective_cut_plan_supplement_request_status(request),
        }
        for request in requests_document.requests
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    _render_persisted_auto_resolve_trace(project)

    adobe_search_ready, pexels_search_ready = _render_provider_readiness_status()
    any_search_ready = adobe_search_ready or pexels_search_ready
    provider_labels = {SUPPLEMENT_SOURCE_ADOBE: "Adobe Stock", SUPPLEMENT_SOURCE_PEXELS: "Pexels"}

    for request in requests_document.requests:
        with st.expander(
            f"{request.request_id} — {effective_cut_plan_supplement_request_status(request)}",
            expanded=False,
        ):
            st.write(f"**Cut Item:** {request.cut_item_id} ({request.source_scope}, {request.folder_name or '—'})")
            st.write(f"**Text:** {request.text}")
            st.write(f"**Visual Intent:** {request.visual_intent or '—'}")
            st.write(f"**Needed duration:** {request.needed_duration_sec:.2f}s")
            st.write(f"**Reason:** {request.reason}")
            if request.supplement_search_hint:
                st.caption(
                    "🟢 Bereits beim Skriptschreiben vorbereiteter Suchvorschlag "
                    f"(wird als bevorzugte Query verwendet): `{request.supplement_search_hint}`"
                )
            if request.accepted_asset_id:
                st.success(
                    f"Akzeptiertes Asset: `{request.accepted_asset_id}` "
                    f"(`{request.accepted_asset_path}`)."
                )
                if st.button(
                    "↩️ Übernahme zurücknehmen",
                    key=f"cut_plan_supplement_unaccept_{project.id}_{request.request_id}",
                    help="Setzt dieses Cut-Item auf den Zustand vor der Übernahme zurück. "
                    "Löscht keine bereits heruntergeladenen Dateien.",
                ):
                    try:
                        with st.spinner("Übernahme wird zurückgenommen…"):
                            unaccept_cut_plan_supplement_request(project, request.request_id)
                        st.success("Übernahme zurückgenommen. Bitte Cut Plan erneut validieren.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

            provider_options = [SUPPLEMENT_SOURCE_ADOBE, SUPPLEMENT_SOURCE_PEXELS]
            default_provider = SUPPLEMENT_SOURCE_ADOBE if adobe_search_ready else SUPPLEMENT_SOURCE_PEXELS
            selected_provider = st.selectbox(
                "Provider für diese Suche",
                options=provider_options,
                index=provider_options.index(default_provider),
                format_func=lambda p: provider_labels.get(p, p),
                key=f"cut_plan_supplement_search_provider_{project.id}_{request.request_id}",
                help="Nutzervorgabe: Adobe Stock wird bevorzugt (sofortige Lizenzierung, "
                "unlimited Plan) — Pexels bleibt als kostenlose Ausweichquelle wählbar.",
            )
            selected_provider_ready = (
                adobe_search_ready if selected_provider == SUPPLEMENT_SOURCE_ADOBE else pexels_search_ready
            )
            if not selected_provider_ready:
                st.caption(
                    f"⚪ {provider_labels.get(selected_provider, selected_provider)} ist nicht konfiguriert "
                    "— Suche liefert keine Treffer."
                )
            st.caption(f"Provider: {provider_labels.get(selected_provider, selected_provider)} (Video & Foto)")
            if request.llm_queries:
                st.caption(
                    "Zuletzt verwendete LLM-Suchqueries: "
                    + " · ".join(f"`{query}`" for query in request.llm_queries)
                )
            elif request.llm_query_status and request.llm_query_status != "PASS":
                st.caption(
                    f"LLM-Suchqueries zuletzt fehlgeschlagen ({request.llm_query_status}: "
                    f"{request.llm_query_error or 'unbekannter Fehler'}) — Suche nutzt "
                    "stattdessen die deterministische Ersatz-Query."
                )
            else:
                fallback_query_preview = build_keyword_query(
                    folder_name=request.folder_name,
                    visual_requirement=request.visual_intent or request.reason,
                    passage_text=request.text,
                )
                st.caption(
                    "Noch keine LLM-Suchqueries generiert — nächste Suche erzeugt bis zu 3 "
                    f"englische Queries per LLM (Ersatz ohne LLM: `{fallback_query_preview}`)."
                )
            st.caption(
                "⚠️ Die Suche kann API-Kontingent beim Provider (und ggf. beim LLM) "
                "verbrauchen. Wird nur bei Klick ausgelöst."
            )

            if st.button(
                "Supplement-Kandidaten suchen",
                key=f"cut_plan_supplement_search_{project.id}_{request.request_id}",
                disabled=not selected_provider_ready,
            ):
                with st.spinner("Suchqueries werden erzeugt und Kandidaten gesucht…"):
                    candidates_document = search_candidates_for_cut_plan_request(
                        project,
                        request.request_id,
                        {"provider": selected_provider},
                        query_llm_provider=query_llm_provider,
                        query_llm_model=query_llm_model,
                    )
                if candidates_document.status == "FAILED":
                    st.error(f"Suche fehlgeschlagen: {candidates_document.error_message}")
                elif not candidates_document.candidates:
                    st.info("Keine Kandidaten gefunden.")
                else:
                    st.success(f"{len(candidates_document.candidates)} Kandidat(en) gefunden.")
                st.rerun()

            # Phase 11.6: deckt jetzt auch generischen Fallback und manuelle
            # Zuweisung ab (nicht mehr nur request.status == ACCEPTED, das
            # nur bei Stock-Akzeptanz gesetzt wird) — konsistent mit der
            # verschärften Absicherung in accept_cut_plan_supplement_
            # candidate/apply_generic_fallback_for_cut_plan_request/apply_
            # manual_asset_for_cut_plan_request.
            is_already_accepted_for_auto = bool(request.accepted_asset_id)
            if st.button(
                "🤖 Automatisch lösen (Suche + Download + Prüfung + Akzeptieren)",
                key=f"cut_plan_supplement_auto_resolve_{project.id}_{request.request_id}",
                disabled=not any_search_ready or is_already_accepted_for_auto,
                help=(
                    "Bestätigten Kandidaten überspringen"
                    if is_already_accepted_for_auto
                    else "Versucht zuerst Adobe Stock (sofortige Lizenzierung), dann Pexels — lädt "
                    "herunter und prüft Kandidaten der Reihe nach per Gemini — übernimmt automatisch "
                    "NUR bei bestandener Prüfung (Status PASS)."
                ),
            ):
                request_labels = {
                    entry.request_id: f"{entry.folder_name or '—'} · {entry.cut_item_id}"
                    for entry in requests_document.requests
                }
                _emit_started = AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_REQUEST_STARTED,
                    request_id=request.request_id,
                    request_index=1,
                    request_total=1,
                )
                progress_handler, progress_bar, log_placeholder, get_trace_lines = _make_auto_resolve_progress_handler(
                    request_labels=request_labels,
                    is_batch=False,
                )
                progress_handler(_emit_started)
                auto_result = auto_resolve_cut_plan_supplement_request(
                    project,
                    request.request_id,
                    query_llm_provider=query_llm_provider,
                    query_llm_model=query_llm_model,
                    progress_callback=progress_handler,
                )
                _persist_auto_resolve_trace(
                    project,
                    get_trace_lines(),
                    summary=(
                        f"Einzel-Request „{request_labels.get(request.request_id, request.request_id)}“: "
                        f"{auto_result.status}"
                    ),
                )
                progress_bar.empty()
                log_placeholder.empty()
                if auto_result.status == AUTO_RESOLVE_STATUS_ACCEPTED:
                    st.success(
                        f"Automatisch akzeptiert: Kandidat `{auto_result.accepted_candidate_id}` "
                        f"(nach {len(auto_result.attempts)} geprüftem/n Kandidaten). "
                        "Bitte Cut Plan erneut validieren."
                    )
                elif auto_result.status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED:
                    st.success(
                        f"Kein Stock-Kandidat hat bestanden — stattdessen generisches Ordner-Asset "
                        f"`{auto_result.accepted_asset_id}` verwendet. Bitte Cut Plan erneut validieren."
                    )
                elif auto_result.status == AUTO_RESOLVE_STATUS_NO_MATCH:
                    st.warning(
                        f"Kein Stock-Kandidat hat die Prüfung bestanden ({len(auto_result.attempts)} geprüft) "
                        "und auch kein passendes generisches Ordner-Asset gefunden. Kein Asset wurde "
                        "automatisch übernommen."
                    )
                else:
                    st.error(f"Automatisches Lösen fehlgeschlagen: {auto_result.error}")
                st.rerun()

            if st.button(
                "Generisches Ordner-Asset verwenden (ohne Stock-Suche)",
                key=f"cut_plan_supplement_generic_fallback_{project.id}_{request.request_id}",
                disabled=is_already_accepted_for_auto,
                help="Sucht KEINE Stock-Kandidaten — wählt direkt ein neutrales, bereits vorhandenes "
                "Asset aus demselben Ordner-Inventory.",
            ):
                with st.spinner("Generisches Asset wird gesucht…"):
                    _, generic_candidate = apply_generic_fallback_for_cut_plan_request(project, request.request_id)
                if generic_candidate is not None:
                    st.success(
                        f"Generisches Ordner-Asset `{generic_candidate.asset_id}` verwendet. "
                        "Bitte Cut Plan erneut validieren."
                    )
                else:
                    st.warning("Kein passendes generisches Asset im Ordner-Inventory gefunden.")
                st.rerun()

            manual_options = list_manual_asset_options_for_request(
                project, request, needed_duration_sec=request.needed_duration_sec
            )
            if manual_options:
                with st.expander("Vorhandenes Asset manuell zuweisen", expanded=False):
                    option_labels = {
                        option.asset_id: (
                            f"{option.asset_id} · {option.media_type or '—'} · "
                            f"{option.duration_sec:.1f}s"
                            + ("" if option.likely_usable else " · ⚠️ evtl. zu kurz")
                        )
                        for option in manual_options
                    }
                    selected_asset_id = st.selectbox(
                        "Ordner-Asset",
                        options=list(option_labels.keys()),
                        format_func=lambda asset_id: option_labels.get(asset_id, asset_id),
                        key=f"cut_plan_supplement_manual_select_{project.id}_{request.request_id}",
                    )
                    if st.button(
                        "Manuell zuweisen",
                        key=f"cut_plan_supplement_manual_assign_{project.id}_{request.request_id}",
                        disabled=is_already_accepted_for_auto,
                        help="Ordnet dieses Asset direkt zu — ohne Stock-Suche, ohne Gemini-Prüfung "
                        "(das Material liegt bereits im eigenen Inventory).",
                    ):
                        selected_option = next(
                            option for option in manual_options if option.asset_id == selected_asset_id
                        )
                        try:
                            with st.spinner("Asset wird zugewiesen…"):
                                apply_manual_asset_for_cut_plan_request(
                                    project,
                                    request.request_id,
                                    asset_id=selected_option.asset_id,
                                    asset_path=selected_option.path,
                                )
                            st.success(
                                f"Asset `{selected_option.asset_id}` manuell zugewiesen. "
                                "Bitte Cut Plan erneut validieren."
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))

            if request.auto_resolve_status:
                with st.expander("Letzter Auto-Resolve-Versuch", expanded=False):
                    st.caption(f"Ergebnis: **{request.auto_resolve_status}**")
                    if request.auto_resolve_attempts:
                        st.dataframe(
                            [
                                {
                                    "candidate_id": attempt.candidate_id,
                                    "provider": attempt.provider,
                                    "asset_type": attempt.asset_type,
                                    "validation_status": attempt.validation_status,
                                    "validation_score": attempt.validation_score,
                                    "validation_reason": attempt.validation_reason,
                                }
                                for attempt in request.auto_resolve_attempts
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )

            candidates_document = load_cut_plan_supplement_candidates_for_request(project, request.request_id)
            if candidates_document is None:
                st.caption("Noch keine Kandidatensuche durchgeführt.")
                continue
            if candidates_document.status == "FAILED":
                st.error(f"Letzte Suche fehlgeschlagen: {candidates_document.error_message}")
                continue
            if not candidates_document.candidates:
                st.caption("Letzte Suche ergab keine Kandidaten.")
                continue

            is_already_accepted = request.status == CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED
            for candidate in candidates_document.candidates:
                candidate_cols = st.columns([3, 1])
                with candidate_cols[0]:
                    st.write(f"**{candidate.title or candidate.candidate_id}** ({candidate.provider})")
                    st.caption(
                        f"asset_type={candidate.asset_type} · duration={candidate.duration_sec:.2f}s · "
                        f"{candidate.width}x{candidate.height} · score={candidate.score:.2f} · "
                        f"license={candidate.license or '—'}"
                    )
                    if candidate.preview_url:
                        st.caption(f"Preview: {candidate.preview_url}")
                    if candidate.source_url:
                        st.caption(f"Quelle: {candidate.source_url}")
                    if candidate.risks:
                        st.warning(f"Risiken: {', '.join(candidate.risks)}")
                    if is_already_accepted and candidate.candidate_id == request.accepted_candidate_id:
                        st.caption("✅ Dies ist der aktuell akzeptierte Kandidat.")
                with candidate_cols[1]:
                    if is_already_accepted:
                        # Vorab-Hardening (Phase 8.7): ein bereits akzeptierter
                        # Request darf nicht still durch einen zweiten Klick
                        # überschrieben werden — nur ein expliziter,
                        # klar gewarnter „Ersetzen“-Button ist erlaubt.
                        st.warning("Bereits akzeptiert.")
                        if st.button(
                            "Akzeptierten Candidate ersetzen",
                            key=(
                                f"cut_plan_supplement_replace_{project.id}_"
                                f"{request.request_id}_{candidate.candidate_id}"
                            ),
                            help="Ersetzt bewusst das bereits akzeptierte Supplement-Asset dieses Requests.",
                        ):
                            try:
                                with st.spinner("Kandidat wird ersetzt…"):
                                    accept_cut_plan_supplement_candidate(
                                        project, request.request_id, candidate.candidate_id, force_replace=True
                                    )
                                st.success(
                                    "Supplement-Asset ersetzt. Bitte Cut Plan erneut validieren."
                                )
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                    elif st.button(
                        "Akzeptieren",
                        key=f"cut_plan_supplement_accept_{project.id}_{request.request_id}_{candidate.candidate_id}",
                    ):
                        try:
                            with st.spinner("Kandidat wird übernommen…"):
                                accept_cut_plan_supplement_candidate(
                                    project, request.request_id, candidate.candidate_id
                                )
                            st.success(
                                "Supplement-Asset übernommen. Bitte Cut Plan erneut validieren."
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))

    _render_bulk_auto_resolve_action(
        project,
        requests_document,
        query_llm_provider=query_llm_provider,
        query_llm_model=query_llm_model,
    )


def _auto_resolve_trace_session_key(project: Project) -> str:
    return f"cut_plan_auto_resolve_trace_{project.id}"


def _persist_auto_resolve_trace(project: Project, lines: list[str], *, summary: str = "") -> None:
    """Phase 12.9: hält den Live-Trace nach st.rerun() in session_state."""
    st.session_state[_auto_resolve_trace_session_key(project)] = {
        "lines": lines,
        "summary": summary,
    }


def _render_persisted_auto_resolve_trace(project: Project) -> None:
    payload = st.session_state.get(_auto_resolve_trace_session_key(project))
    if not payload:
        return
    with st.expander("Letzter Auto-Resolve-Live-Trace", expanded=True):
        if payload.get("summary"):
            st.caption(payload["summary"])
        trace_lines = payload.get("lines", [])
        if trace_lines:
            st.markdown("\n\n".join(trace_lines))
        else:
            st.caption("Kein Trace-Inhalt vorhanden.")
        if st.button(
            "Trace schließen",
            key=f"cut_plan_auto_resolve_trace_dismiss_{project.id}",
        ):
            del st.session_state[_auto_resolve_trace_session_key(project)]
            st.rerun()


def _format_auto_resolve_asset_type(asset_type: str) -> str:
    return {"video": "Video", "image": "Foto"}.get(asset_type, asset_type or "—")


def _format_auto_resolve_provider(provider: str) -> str:
    if provider == CUT_PLAN_AUTO_RESOLVE_PROVIDER_LOCAL_REUSE:
        return "Lokale Wiederverwendung"
    return SUPPLEMENT_SOURCE_LABELS.get(provider, provider or "—")


def _format_auto_resolve_progress_line(
    event: AutoResolveProgressEvent, *, request_labels: dict[str, str] | None = None
) -> str:
    """Phase 12.8/12.9: eine Zeile für das Live-Trace-Log während Auto-Resolve."""
    if event.event_type == AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_STARTED:
        return "  🧠 LLM-Suchqueries werden erzeugt…"
    if event.event_type == AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_FINISHED:
        status = f" ({event.validation_status})" if event.validation_status else ""
        err = f" — {event.message}" if event.message else ""
        return f"  🧠 Queries{status}: `{event.query}`{err}"
    if event.event_type == AUTO_RESOLVE_PROGRESS_REQUEST_STARTED:
        label = (request_labels or {}).get(event.request_id, event.request_id)
        if event.request_total:
            return f"▶ Request {event.request_index}/{event.request_total}: „{label}“"
        return f"▶ Request „{label}“"
    if event.event_type == AUTO_RESOLVE_PROGRESS_STAGE_STARTED:
        provider = _format_auto_resolve_provider(event.provider)
        media = _format_auto_resolve_asset_type(event.asset_type)
        stage = f"Stufe {event.stage_index}/{event.stage_total}" if event.stage_total else "Stufe"
        return f"  🔍 {stage}: {provider} · {media} · Query `{event.query}` — {event.message}"
    if event.event_type == AUTO_RESOLVE_PROGRESS_STAGE_FAILED:
        provider = _format_auto_resolve_provider(event.provider)
        media = _format_auto_resolve_asset_type(event.asset_type)
        return f"  ⚠️ {provider} · {media}: Suche fehlgeschlagen — {event.message}"
    if event.event_type == AUTO_RESOLVE_PROGRESS_STAGE_NO_RESULTS:
        provider = _format_auto_resolve_provider(event.provider)
        media = _format_auto_resolve_asset_type(event.asset_type)
        detail = f" — {event.message}" if event.message else ""
        return f"  ⏭️ {provider} · {media} · Query `{event.query}`: keine Treffer{detail}"
    if event.event_type == AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT:
        provider = _format_auto_resolve_provider(event.provider)
        media = _format_auto_resolve_asset_type(event.asset_type)
        title = event.candidate_title or event.candidate_id
        duration = (
            f"{event.duration_sec:.2f}s"
            if event.asset_type == "video" and event.duration_sec > 0
            else ("gestreckt" if event.asset_type == "image" else "—")
        )
        reason = f" — {event.validation_reason}" if event.validation_reason else ""
        return (
            f"  · {provider}/{media} `{title}` ({duration}) → "
            f"**{event.validation_status}**{reason}"
        )
    if event.event_type == AUTO_RESOLVE_PROGRESS_GENERIC_FALLBACK_STARTED:
        return "  📁 Generischer Ordner-Fallback wird versucht…"
    if event.event_type == AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED:
        return f"✅ Ergebnis: **{event.result_status}** — {event.message}"
    return ""


def _make_auto_resolve_progress_handler(
    *,
    request_labels: dict[str, str] | None = None,
    is_batch: bool = False,
):
    """Erzeugt einen progress_callback mit st.progress + Live-Log."""
    progress_bar = st.progress(0.0, text="Auto-Resolve startet…")
    log_placeholder = st.empty()
    lines: list[str] = []
    max_lines = 40
    current_request_index = 0
    current_request_total = 0

    def _render_log() -> None:
        visible = lines[-max_lines:]
        log_placeholder.markdown("\n\n".join(visible) if visible else "_Warte auf Fortschritt…_")

    def _handle(event: AutoResolveProgressEvent) -> None:
        nonlocal current_request_index, current_request_total
        line = _format_auto_resolve_progress_line(event, request_labels=request_labels)
        if line:
            lines.append(line)
            _render_log()

        if event.event_type == AUTO_RESOLVE_PROGRESS_REQUEST_STARTED:
            current_request_index = event.request_index
            current_request_total = event.request_total
            if is_batch and event.request_total:
                done = max(0, event.request_index - 1)
                progress_bar.progress(
                    done / event.request_total,
                    text=f"Request {event.request_index}/{event.request_total}",
                )
        elif event.event_type == AUTO_RESOLVE_PROGRESS_STAGE_STARTED and not is_batch and event.stage_total:
            progress_bar.progress(
                (event.stage_index - 1) / event.stage_total,
                text=f"Suchstufe {event.stage_index}/{event.stage_total}",
            )
        elif event.event_type == AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED:
            if is_batch and current_request_total:
                progress_bar.progress(
                    current_request_index / current_request_total,
                    text=f"Request {current_request_index}/{current_request_total} abgeschlossen",
                )
            else:
                progress_bar.progress(1.0, text="Abgeschlossen")

    return _handle, progress_bar, log_placeholder, lambda: list(lines)


def _render_bulk_auto_resolve_action(
    project: Project,
    requests_document,
    *,
    query_llm_provider: str,
    query_llm_model: str,
) -> None:
    """Phase 11.5: Batch-Variante von 'Automatisch lösen' — läuft
    sequenziell über ALLE noch nicht versorgten Supplement Requests (Suche
    + Download + Gemini-Prüfung + ggf. generischer Ordner-Fallback pro
    Request), mit Fortschrittsanzeige. Läuft — wie alle anderen 'Alle X'-
    Sammel-Aktionen dieser Pipeline — blockierend ohne Abbrechen-Button."""
    open_requests = [entry for entry in requests_document.requests if is_open_cut_plan_supplement_request(entry)]

    st.subheader("Alle offenen Supplement Requests")
    st.caption(
        f"{len(open_requests)} von {len(requests_document.requests)} Request(s) noch ohne übernommenes Asset. "
        "Läuft sequenziell — ein Request nach dem anderen, ohne Abbrechen-Möglichkeit."
    )
    adobe_ready, pexels_ready = _supplement_provider_readiness()
    any_ready = adobe_ready or pexels_ready
    if st.button(
        "🤖 Alle fehlenden Supplement-Assets automatisch suchen",
        key=f"cut_plan_supplement_auto_resolve_all_{project.id}",
        disabled=not any_ready or not open_requests,
        help="Versucht pro Request zuerst Adobe Stock (sofortige Lizenzierung), dann Pexels.",
    ):
        request_labels = {
            entry.request_id: f"{entry.folder_name or '—'} · {entry.cut_item_id}"
            for entry in requests_document.requests
        }
        progress_handler, progress_bar, log_placeholder, get_trace_lines = _make_auto_resolve_progress_handler(
            request_labels=request_labels,
            is_batch=True,
        )

        results = auto_resolve_all_cut_plan_supplement_requests(
            project,
            query_llm_provider=query_llm_provider,
            query_llm_model=query_llm_model,
            progress_callback=progress_handler,
        )
        accepted_count = sum(1 for result in results if result.status == AUTO_RESOLVE_STATUS_ACCEPTED)
        generic_count = sum(1 for result in results if result.status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED)
        no_match_count = sum(1 for result in results if result.status == AUTO_RESOLVE_STATUS_NO_MATCH)
        _persist_auto_resolve_trace(
            project,
            get_trace_lines(),
            summary=(
                f"{len(results)} Request(s): {accepted_count} akzeptiert, "
                f"{generic_count} generischer Fallback, {no_match_count} ohne Treffer"
            ),
        )
        progress_bar.empty()
        log_placeholder.empty()

        st.success(
            f"{len(results)} Request(s) bearbeitet: {accepted_count} automatisch akzeptiert, "
            f"{generic_count} generisches Ordner-Asset verwendet, {no_match_count} ohne Treffer "
            "(manuelle Prüfung nötig)."
        )
        for result in results:
            if result.status == AUTO_RESOLVE_STATUS_NO_MATCH:
                label = request_labels.get(result.request_id, result.request_id)
                st.warning(f"„{label}“: kein passendes Asset gefunden — bitte manuell prüfen.")
        st.rerun()


def _black_gap_repair_feedback_key(project: Project) -> str:
    return f"cut_plan_black_gap_repair_feedback_{project.id}"


def _dedupe_black_gap_errors(errors: list[CutPlanValidationError]) -> list[CutPlanValidationError]:
    """Pro cut_item_id nur den informativsten Black-Gap behalten
    (mit Gap-Zeiten bevorzugt gegenüber „aus Asset-Auswahl übernommen“)."""
    by_key: dict[str, CutPlanValidationError] = {}
    orphans: list[CutPlanValidationError] = []
    for error in errors:
        if error.type != CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER:
            continue
        if not error.cut_item_id:
            orphans.append(error)
            continue
        existing = by_key.get(error.cut_item_id)
        if existing is None:
            by_key[error.cut_item_id] = error
            continue
        existing_has_gap = existing.gap_end_sec > existing.gap_start_sec
        new_has_gap = error.gap_end_sec > error.gap_start_sec
        if new_has_gap and not existing_has_gap:
            by_key[error.cut_item_id] = error
        elif new_has_gap == existing_has_gap and "aus Asset-Auswahl" in existing.message:
            by_key[error.cut_item_id] = error
    return list(by_key.values()) + orphans


def _render_black_gap_manual_repair(
    project: Project, draft: CutPlanDocument, *, compact: bool = False
) -> None:
    """Diagnose + manueller Asset-Tausch für BLACK_GAP_DURING_VOICEOVER —
    inkl. Sektionspausen (Closing) und Satz-Löcher (zu kurzes Fallback)."""
    from otio_app.services.voiceover_generation.cut_plan_visual_coverage import (
        diagnose_section_pause_hold_failure,
    )

    black_gaps = _dedupe_black_gap_errors(list(draft.blockers))
    if not black_gaps:
        return

    feedback_key = _black_gap_repair_feedback_key(project)
    feedback = st.session_state.pop(feedback_key, None)
    if isinstance(feedback, dict):
        level = feedback.get("level", "info")
        text = str(feedback.get("text", ""))
        if level == "success":
            st.success(text)
        elif level == "error":
            st.error(text)
        else:
            st.info(text)

    if not compact:
        st.subheader("Black-Gap Diagnose & manuell tauschen")
    st.caption(
        "Je Black-Gap: Auslöser + Asset aus dem Ordner wählen. "
        "✅ = Länge reicht voraussichtlich, ⚠️ = vermutlich zu kurz. "
        "Nach Übernahme erscheint hier eine Erfolgs-/Fehlermeldung — danach „Cut Plan validieren“. "
        "Hinweis: Die Voice-over-Audiospur endet oft etwas nach dem letzten Wort; "
        "das Bild muss diese Zeit mit abdecken."
    )

    for index, error in enumerate(black_gaps):
        gap_label = (
            f"{error.gap_start_sec:.2f}s–{error.gap_end_sec:.2f}s"
            if error.gap_end_sec > error.gap_start_sec
            else "Gap-Zeiten nach Validierung unvollständig"
        )
        title = error.cut_item_id or "(ohne Item-Zuordnung)"
        with st.expander(
            f"🔴 {title} — {gap_label} — {error.folder_name or error.scope}",
            expanded=True,
        ):
            st.write(error.message)
            if error.fix_hint:
                st.caption(f"Hinweis: {error.fix_hint}")

            target_item = next(
                (entry for entry in draft.items if entry.cut_item_id == error.cut_item_id),
                None,
            )
            diagnosis = None
            if error.gap_end_sec > error.gap_start_sec:
                diagnosis = diagnose_section_pause_hold_failure(
                    draft,
                    error.gap_start_sec,
                    error.gap_end_sec,
                    responsible_item=target_item,
                )
                st.info(
                    f"**Diagnose:** {diagnosis.summary}\n\n"
                    f"- Zuständig: `{diagnosis.responsible_cut_item_id or '—'}`"
                    f"{' (Closing Shot)' if diagnosis.responsible_is_closing_shot else ''}\n"
                    f"- Hold-Asset: `{diagnosis.hold_candidate_asset_id or '—'}` "
                    f"({diagnosis.hold_candidate_asset_type or '—'})\n"
                    f"- Benötigte Extra-Länge: {diagnosis.needed_extend_sec:.2f}s\n"
                    f"- Nutzbare Reserve: "
                    f"{'∞ (Bild)' if diagnosis.usable_extend_sec is None and diagnosis.hold_candidate_asset_type == 'image' else (f'{diagnosis.usable_extend_sec:.2f}s' if diagnosis.usable_extend_sec is not None else '—')}\n"
                    f"- Fehlgrund: `{diagnosis.failure_reason}`"
                )
            elif target_item is not None:
                seg_dur = sum(segment.duration_sec for segment in target_item.planned_visual_segments)
                st.info(
                    f"**Diagnose (Satz-Loch):** Item braucht ca. **{target_item.duration_sec:.2f}s** Bild. "
                    f"Aktuell gewählt: `{target_item.chosen_asset_id or '—'}` "
                    f"({target_item.asset_selection_status}), "
                    f"Segmentdauer summiert {seg_dur:.2f}s. "
                    f"Warnings: {', '.join(target_item.warnings) or '—'}. "
                    "Typische Ursache: Generic Fallback / Clip zu kurz für die "
                    "Voice-over-Zeit dieses Satzes (Audio kann etwas über das letzte Wort hinausreichen)."
                )

            target_cut_item_id = error.cut_item_id or (
                diagnosis.responsible_cut_item_id if diagnosis is not None else ""
            )
            if not target_cut_item_id:
                st.warning("Keine Item-Zuordnung — manueller Tausch hier nicht möglich.")
                continue

            if target_item is None:
                target_item = next(
                    (entry for entry in draft.items if entry.cut_item_id == target_cut_item_id),
                    None,
                )
            if target_item is None or not target_item.folder_name:
                st.warning(f"Item `{target_cut_item_id}` hat keinen Ordner für Inventory-Auswahl.")
                continue

            needed = float(target_item.duration_sec)
            if error.gap_end_sec > error.gap_start_sec:
                needed = max(needed, error.gap_end_sec - error.gap_start_sec)
            if diagnosis is not None:
                needed = max(needed, diagnosis.needed_extend_sec)

            options = list_manual_asset_options_for_cut_item(
                project, draft, target_cut_item_id, needed_duration_sec=needed
            )
            if not options:
                st.warning("Kein Asset im Ordner-Inventory gefunden.")
                continue

            options = sorted(options, key=lambda option: (not option.likely_usable, -option.duration_sec))
            usable_count = sum(1 for option in options if option.likely_usable)
            st.caption(
                f"Benötigte Länge: **{needed:.2f}s** — "
                f"{usable_count}/{len(options)} Assets im Ordner sind voraussichtlich lang genug."
            )
            if usable_count == 0:
                st.warning(
                    "Kein Ordner-Asset ist lang genug. Übernahme eines ⚠️-Assets schlägt mit "
                    "„Asset zu kurz“ fehl — bitte Stock/Supplement oder längeres Material nutzen."
                )

            labels = [
                f"{'✅' if option.likely_usable else '⚠️ zu kurz'} {option.asset_id} "
                f"({option.media_type}, {option.duration_sec:.1f}s) — {option.description[:60]}"
                for option in options
            ]
            selected_label = st.selectbox(
                "Ersatz-Asset wählen",
                options=labels,
                key=f"cut_plan_black_gap_manual_select_{project.id}_{target_cut_item_id}_{index}",
            )
            selected = options[labels.index(selected_label)]
            if not selected.likely_usable:
                st.warning(
                    f"`{selected.asset_id}` ist nur {selected.duration_sec:.1f}s lang, "
                    f"benötigt werden ≥ {needed:.2f}s — Übernahme wird voraussichtlich abgelehnt."
                )
            if st.button(
                "Asset in Cut Plan übernehmen",
                key=f"cut_plan_black_gap_manual_apply_{project.id}_{target_cut_item_id}_{index}",
                type="primary",
            ):
                try:
                    apply_manual_asset_for_cut_item(
                        project,
                        target_cut_item_id,
                        asset_id=selected.asset_id,
                        asset_path=selected.path,
                        needed_duration_sec=needed,
                    )
                except ValueError as exc:
                    st.session_state[feedback_key] = {
                        "level": "error",
                        "text": (
                            f"Übernahme fehlgeschlagen für `{target_cut_item_id}` ← `{selected.asset_id}`: "
                            f"{exc}"
                        ),
                    }
                else:
                    st.session_state[feedback_key] = {
                        "level": "success",
                        "text": (
                            f"Übernommen: `{selected.asset_id}` → `{target_cut_item_id}` "
                            f"(Ordner „{target_item.folder_name}“). Bitte jetzt „Cut Plan validieren“."
                        ),
                    }
                st.rerun()


def _render_residual_gap_requests(project: Project, draft: CutPlanDocument) -> None:
    """Residual Gap Requests (Nutzervorgabe, Juli 2026): dritter,
    eigenständiger Reparaturpfad für Items, die bereits ein Asset haben
    (PRIMARY_USED/BACKUP_USED/SUPPLEMENT_USED/…), deren visuelle Abdeckung
    aber nicht bis zum erwarteten Fenster-Ende reicht UND deren Lücke zu
    groß für eine sichere Nachbar-Kürzung (Validation Repair) ist. Bewusst
    GETRENNT sowohl von Supplement Requests (Item hat noch KEIN Asset) als
    auch von Validation Repair (kleine, per Nachbar-Kürzung reparierbare
    Lücke) — siehe cut_plan_visual_gap_analysis.py.

    Zeigt die erkannten Rest-Lücken inkl. berechneter Gap-Zeiten an,
    erzeugt/aktualisiert die persistierten Requests ohne bereits
    akzeptierte Assets zu verlieren (siehe
    merge_prior_residual_gap_request_state) und bietet Suche
    (Adobe/Pexels + lokale Wiederverwendung) sowie Übernahme
    (mit/ohne erneute Suche) an."""
    st.subheader("Residual Gap Repair")
    st.caption(
        "Dritter Reparatur-Schritt für Items, die bereits ein Asset haben, dessen visuelle Abdeckung "
        "aber nicht bis zum erwarteten Fenster-Ende reicht (Visual Window, Video zu kurz, …) — anders als bei "
        "Supplement (Item hat noch KEIN Asset) oder Validation Repair (kleine, per Nachbar-Kürzung "
        "reparierbare Lücke). Gap-Zeiten werden direkt aus dem Draft berechnet, nicht aus Fehlermeldungen "
        "geparst."
    )

    requests_document = load_residual_gap_requests(project)
    current_hash = content_hash_of_model(draft)

    if requests_document is None:
        if st.button("Residual Gap Requests erzeugen", key=f"cut_plan_residual_gap_build_{project.id}"):
            new_document = build_residual_gap_requests_from_cut_plan(project, draft)
            save_residual_gap_requests(project, new_document)
            st.success(f"{len(new_document.requests)} Residual Gap Request(s) erzeugt.")
            st.rerun()
        return

    if requests_document.source_cut_plan_hash != current_hash:
        st.warning(
            "Die Residual Gap Requests wurden aus einer älteren Version des Cut Plans erzeugt. "
            "Bitte bei Bedarf neu erzeugen."
        )
    if st.button("Residual Gap Requests neu erzeugen", key=f"cut_plan_residual_gap_rebuild_{project.id}"):
        prior_document = requests_document
        new_document = build_residual_gap_requests_from_cut_plan(project, draft)
        new_document = merge_prior_residual_gap_request_state(new_document, prior_document)
        save_residual_gap_requests(project, new_document)
        st.success(f"{len(new_document.requests)} Residual Gap Request(s) erzeugt.")
        st.rerun()

    if not requests_document.requests:
        st.info("Keine Residual Gap Requests — keine Rest-Lücken bei bereits versorgten Items gefunden.")
        return

    unapplied_count = count_unapplied_accepted_residual_gap_requests(draft, requests_document)
    if unapplied_count:
        st.warning(
            f"{unapplied_count} Request(s) haben bereits ein akzeptiertes Asset, das im aktuellen Cut Plan "
            "Draft aber noch nicht übernommen ist."
        )
        if st.button(
            "✅ Akzeptierte Residual-Gap-Assets auf Cut Plan anwenden (ohne erneute Suche)",
            key=f"cut_plan_residual_gap_reapply_{project.id}",
            help="Übernimmt alle bereits akzeptierten Residual-Gap-Dateien erneut in den Draft — "
            "spart externe Suchen/Lizenzierungen in Testläufen.",
        ):
            with st.spinner("Akzeptierte Residual-Gap-Assets werden übernommen…"):
                try:
                    _, applied, skipped = reapply_accepted_residual_gap_assets(project)
                    st.success(f"{len(applied)} Item(s) übernommen.")
                    if skipped:
                        st.warning(f"{len(skipped)} Item(s) übersprungen (Datei fehlt oder Asset zu kurz).")
                    st.info("Bitte Cut Plan erneut validieren.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    repair_mode_labels = {
        CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY: "Patch (Pause/Überhang)",
        CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL: "Ersatz (Satzmitte)",
    }
    rows = [
        {
            "request_id": request.request_id,
            "cut_item_id": request.cut_item_id,
            "folder_name": request.folder_name or "—",
            "gap": f"{request.gap_start_sec:.2f}s–{request.gap_end_sec:.2f}s",
            "needed_duration_sec": request.needed_duration_sec,
            "existing_asset_status": request.existing_asset_status,
            "repair_mode": repair_mode_labels.get(request.repair_mode, request.repair_mode),
            "status": request.status,
            "warnings": ", ".join(request.warnings) or "—",
        }
        for request in requests_document.requests
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(
        "Bereits akzeptierte Assets bleiben beim Neu-Erzeugen erhalten (siehe Warnungen-Spalte, falls "
        "eine zwischengespeicherte Datei fehlt oder zu kurz geworden ist)."
    )

    open_requests = [request for request in requests_document.requests if not request.accepted_asset_id]
    if st.button(
        "🤖 Alle offenen Residual Gap Requests automatisch suchen",
        key=f"cut_plan_residual_gap_auto_resolve_all_{project.id}",
        disabled=not open_requests,
        help="Prüft pro Request zuerst bereits heruntergeladene Supplement-Assets im selben Ordner, dann "
        "Adobe Stock/Pexels (Foto vor Video bei reinen Pausen-Patches, Video vor Foto bei Satzmitte-Ersatz).",
    ):
        with st.spinner("Residual Gap Requests werden bearbeitet…"):
            results = auto_resolve_all_residual_gap_requests(project)
        accepted_count = sum(1 for result in results if result.status == RESIDUAL_GAP_AUTO_RESOLVE_STATUS_ACCEPTED)
        no_match_count = sum(1 for result in results if result.status == RESIDUAL_GAP_AUTO_RESOLVE_STATUS_NO_MATCH)
        st.success(
            f"{len(results)} Request(s) bearbeitet: {accepted_count} automatisch akzeptiert, "
            f"{no_match_count} ohne Treffer (manuelle Prüfung nötig)."
        )
        st.info("Bitte Cut Plan erneut validieren.")
        st.rerun()


def _render_validation_repair(project: Project, draft: CutPlanDocument) -> None:
    """Validation Repair (Nutzervorgabe, Juli 2026): eigenständiger, dem
    regulären Supplement-Bereich NACHGESCHALTETER Reparatur-Schritt für
    Rest-Blocker, die erst NACH der vollständigen Cut-Plan-Validierung
    sichtbar werden (BLACK_GAP_DURING_VOICEOVER, ASSET_REUSE_DISTANCE_
    TOO_SHORT). Bewusst GETRENNT von den Supplement Requests oben —
    andere Semantik: für BLACK_GAP wird KEIN Ersatz für das GANZE Item
    gesucht, sondern ein passend großes Reparatur-Fenster berechnet
    (inkl. kontrollierter Kürzung der angrenzenden Segmente, damit das
    neue Segment nicht unter die Mindest-Shot-Länge fällt — siehe
    cut_plan_validation_repair_apply.py). ASSET_REUSE_DISTANCE bleibt ein
    voller Segment-Ersatz, da hier tatsächlich ein anderes Asset für das
    gesamte Segment benötigt wird."""
    st.subheader("Validation Repair")
    st.caption(
        "Eigenständiger Reparatur-Schritt für Rest-Blocker, die erst nach der vollständigen Cut-Plan-"
        "Validierung sichtbar werden (schwarze Lücken, zu früh wiederverwendete Assets). Für schwarze "
        "Lücken wird — anders als bei den Supplement Requests oben — kein Ersatz für das ganze Item "
        "gesucht, sondern ein passend großes Reparatur-Fenster berechnet (inkl. Kürzung der angrenzenden "
        "Segmente, falls nötig). Suche und Download laufen ausschließlich bei explizitem Klick."
    )

    requests_document = load_cut_plan_validation_repair_requests(project)
    current_hash = content_hash_of_model(draft)

    def _warn_about_skipped_stale_black_gaps() -> None:
        """Bugfix (Nutzervorgabe Juli 2026, "gap 0.00s-0.00s"): weist
        explizit darauf hin, wenn für manche BLACK_GAP-Blocker bewusst KEIN
        Request gebaut wurde, weil keine verwertbare Gap-Zeit vorlag —
        sonst sähe es so aus, als seien diese Black Gaps einfach
        verschwunden, statt dass eine Neu-Validierung nötig ist."""
        skipped = count_black_gap_items_without_gap_bounds(draft)
        if skipped:
            st.warning(
                f"{skipped} Item(s) mit einer schwarzen Lücke ohne genaue Zeitangabe übersprungen "
                "(vermutlich ein veralteter Blocker aus einem früheren Validierungslauf). Bitte "
                "„Cut Plan validieren“ erneut ausführen und diese Requests danach neu erzeugen."
            )

    if requests_document is None:
        if st.button(
            "Validation Repair Requests aus Cut Plan erzeugen",
            key=f"cut_plan_validation_repair_build_{project.id}",
        ):
            new_document = build_validation_repair_requests_from_cut_plan(project, draft)
            save_cut_plan_validation_repair_requests(project, new_document)
            st.success(f"{len(new_document.requests)} Validation Repair Request(s) erzeugt.")
            _warn_about_skipped_stale_black_gaps()
            st.rerun()
        return

    if requests_document.source_cut_plan_hash != current_hash:
        st.warning(
            "Die Validation Repair Requests wurden aus einer älteren Version des Cut Plans erzeugt "
            "(z. B. vor einer erneuten Validierung). Bitte bei Bedarf neu erzeugen."
        )
    if st.button(
        "Validation Repair Requests neu erzeugen",
        key=f"cut_plan_validation_repair_rebuild_{project.id}",
    ):
        new_document = build_validation_repair_requests_from_cut_plan(project, draft)
        save_cut_plan_validation_repair_requests(project, new_document)
        st.success(f"{len(new_document.requests)} Validation Repair Request(s) erzeugt.")
        _warn_about_skipped_stale_black_gaps()
        st.rerun()

    if not requests_document.requests:
        st.info("Keine Validation Repair Requests — keine reparierbaren Rest-Blocker im aktuellen Draft gefunden.")
        total_black_gaps = count_black_gap_blockers_on_draft(draft)
        if total_black_gaps:
            settings = CutPlanSettings(project_id=project.id, **draft.settings_snapshot)
            gaps = analyze_visual_gaps(draft, settings)
            unattributed = sum(1 for gap in gaps if gap.gap_kind == GAP_KIND_UNATTRIBUTED_GAP)
            full_item_missing = sum(1 for gap in gaps if gap.gap_kind == GAP_KIND_FULL_ITEM_MISSING)
            residual = sum(1 for gap in gaps if gap.gap_kind == GAP_KIND_RESIDUAL_ITEM_GAP)
            mini_repairable = sum(1 for gap in gaps if gap.gap_kind == GAP_KIND_MINI_REPAIRABLE_GAP)
            st.warning(
                f"Trotzdem {total_black_gaps} BLACK_GAP-Blocker im Draft sichtbar. Direkt aus dem Draft "
                f"berechnete Aufschlüsselung: {unattributed} ohne Satz-Zuordnung (manuelle Prüfung nötig), "
                f"{full_item_missing} Item(s) ohne jedes Asset (→ Supplement Requests), "
                f"{residual} Rest-Lücke(n) bei bereits versorgten Items (→ Residual Gap Requests), "
                f"{mini_repairable} klein genug für Validation Repair (nach erneutem Erzeugen sichtbar). "
                "Bitte nach jeder Übernahme/Reparatur zuerst „Cut Plan validieren“, dann diese Requests "
                "neu erzeugen."
            )
            if residual:
                st.caption(
                    f"➡️ Empfehlung: {residual} Rest-Lücke(n) gehören zu „Residual Gap Repair“ oben — "
                    "dort „Residual Gap Requests erzeugen“ klicken."
                )
            if full_item_missing:
                st.caption(
                    f"➡️ Empfehlung: {full_item_missing} Item(s) ohne Asset gehören zu „Supplement Requests“ "
                    "weiter oben."
                )
        return

    type_labels = {
        CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP: "Schwarze Lücke",
        CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE: "Asset zu früh wiederverwendet",
    }
    rows = [
        {
            "repair_id": request.repair_id,
            "type": type_labels.get(request.repair_type, request.repair_type),
            "cut_item_id": request.cut_item_id,
            "folder_name": request.folder_name or "—",
            "gap": (
                f"{request.gap_start_sec:.2f}s–{request.gap_end_sec:.2f}s"
                if request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP
                else "—"
            ),
            "needed_duration_sec": request.needed_duration_sec,
            "status": request.status,
        }
        for request in requests_document.requests
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    _render_provider_readiness_status()

    open_requests = [
        request for request in requests_document.requests
        if request.status != CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED
    ]
    if st.button(
        "🤖 Alle offenen Validation Repair Requests automatisch reparieren",
        key=f"cut_plan_validation_repair_auto_resolve_all_{project.id}",
        disabled=not open_requests,
        help="Prüft pro Request zuerst bereits heruntergeladene Supplement-Assets im selben Ordner (für "
        "schwarze Lücken wird Foto vor Video bevorzugt), dann Adobe Stock/Pexels.",
    ):
        with st.spinner("Validation Repair Requests werden bearbeitet…"):
            results = auto_resolve_all_validation_repair_requests(project)
        accepted_count = sum(1 for result in results if result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED)
        no_match_count = sum(1 for result in results if result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH)
        unsafe_count = sum(
            1 for result in results if result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR
        )
        st.success(
            f"{len(results)} Request(s) bearbeitet: {accepted_count} automatisch repariert, "
            f"{no_match_count} ohne Treffer, {unsafe_count} nicht sicher reparierbar (siehe Hinweis je Request)."
        )
        st.info("Bitte Cut Plan erneut validieren, um das Ergebnis zu sehen.")
        st.rerun()

    for request in requests_document.requests:
        with st.expander(f"{request.repair_id} — {request.status}", expanded=False):
            st.write(f"**Typ:** {type_labels.get(request.repair_type, request.repair_type)}")
            st.write(f"**Cut Item:** {request.cut_item_id} ({request.folder_name or '—'})")
            if request.text:
                st.write(f"**Text:** {request.text}")
            if request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP:
                st.write(f"**Lücke:** {request.gap_start_sec:.2f}s – {request.gap_end_sec:.2f}s")
            st.write(f"**Reason:** {request.reason}")

            if request.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR:
                st.warning(
                    "Die angrenzenden Segmente haben nicht genug Spielraum, um diese Lücke sicher zu "
                    "reparieren, ohne selbst unter die Mindest-Shot-Länge zu fallen. Bitte stattdessen "
                    "einen normalen Supplement Request für dieses Item nutzen (siehe Bereich oben)."
                )
            if request.accepted_asset_id:
                st.success(
                    f"Reparatur übernommen: `{request.accepted_asset_id}` (`{request.accepted_asset_path}`)."
                )

            if st.button(
                "🤖 Reparieren",
                key=f"cut_plan_validation_repair_resolve_{project.id}_{request.repair_id}",
                disabled=request.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
            ):
                try:
                    with st.spinner("Reparatur wird versucht…"):
                        result = auto_resolve_validation_repair_request(project, request.repair_id)
                    if result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED:
                        st.success(
                            f"Reparatur erfolgreich — Asset `{result.accepted_asset_id}` übernommen. "
                            "Bitte Cut Plan erneut validieren."
                        )
                    elif result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR:
                        st.warning(
                            "Nicht sicher reparierbar — angrenzende Segmente haben nicht genug Spielraum."
                        )
                    else:
                        st.warning("Kein passendes Asset gefunden — bitte manuell prüfen.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

            if request.auto_resolve_attempts:
                attempt_rows = [
                    {
                        "candidate_id": attempt.candidate_id,
                        "provider": attempt.provider,
                        "asset_type": attempt.asset_type,
                        "validation_status": attempt.validation_status,
                        "validation_score": attempt.validation_score,
                        "validation_reason": attempt.validation_reason,
                    }
                    for attempt in request.auto_resolve_attempts
                ]
                st.dataframe(attempt_rows, use_container_width=True, hide_index=True)


def _render_confirmed_cut_plan(project: Project, draft: CutPlanDocument) -> None:
    """Phase 8.7: Confirm + Trace. Kein Rebuild, keine erneute Asset-Auswahl,
    keine erneute Validierung — nur der bereits validierte Draft wird als
    unveränderlicher Snapshot übernommen. Ein bestehender bestätigter Cut
    Plan wird NIEMALS automatisch durch Draft-/Asset-Auswahl-/Validierungs-
    Änderungen ersetzt, nur durch einen expliziten, klar gewarnten Klick."""
    st.subheader("Bestätigter Cut Plan")

    confirmed = load_confirmed_cut_plan(project)
    trace = load_cut_plan_trace(project)

    if confirmed is not None:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", confirmed.status)
        with col2:
            st.metric("Cut Plan Items", len(confirmed.items))
        with col3:
            st.metric("Audio Items", len(confirmed.audio_items))
        with col4:
            total_segments = sum(len(item.planned_visual_segments) for item in confirmed.items)
            st.metric("VisualSegments", total_segments)

        st.caption(f"generated_at: `{confirmed.generated_at}` · confirmed_at: `{confirmed.confirmed_at or '—'}`")
        st.caption(f"source_plan_hash: `{confirmed.source_plan_hash}`")
        st.caption(f"Trace vorhanden: {'✅ Ja' if trace is not None else '❌ Nein'}")

        if is_confirmed_cut_plan_stale(project, confirmed):
            st.warning(
                "Es gibt einen neueren/anderen Draft (der bestätigte Voice-over-Projektplan oder die "
                "Cut-Plan-Settings haben sich seit der Bestätigung geändert). Confirmed Plan bleibt "
                "unverändert."
            )
        st.info(
            "Es gibt bereits einen bestätigten Cut Plan. Änderungen am Draft ersetzen ihn nicht automatisch."
        )

        if st.button("Bestätigung zurücknehmen", key=f"cut_plan_unconfirm_{project.id}"):
            unconfirm_cut_plan(project)
            st.success("Bestätigung zurückgenommen — cut_plan.confirmed.json entfernt.")
            st.rerun()
    else:
        st.info("Noch kein bestätigter Cut Plan vorhanden.")

    st.divider()
    st.subheader("Cut Plan bestätigen")

    report = load_cut_plan_validation_report(project)
    eligible, reasons = can_confirm_cut_plan(project, draft, report)

    if eligible:
        st.success("Alle Confirm-Bedingungen sind erfüllt.")
    else:
        st.warning("Confirm-Bedingungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    if confirmed is not None:
        st.warning(
            "Achtung: Dies ersetzt den bisherigen bestätigten Cut Plan unwiderruflich mit dem aktuellen "
            "Draft-Stand."
        )
        button_label = "Aktuellen Draft bestätigen und bestätigten Cut Plan ersetzen"
    else:
        button_label = "Cut Plan bestätigen"

    if st.button(
        button_label,
        key=f"cut_plan_confirm_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Übernimmt den bereits validierten Draft unverändert als cut_plan.confirmed.json und "
        "schreibt cut_plan.trace.json. Kein Rebuild, keine erneute Asset-Auswahl, keine erneute "
        "Validierung. Kein EditPlanDocument, kein OTIO-Export.",
    ):
        try:
            with st.spinner("Cut Plan wird bestätigt…"):
                confirm_cut_plan(project)
            st.success("Cut Plan bestätigt — cut_plan.confirmed.json und cut_plan.trace.json wurden geschrieben.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    if trace is not None and trace.entries:
        st.divider()
        st.subheader("Trace")
        rows = []
        for entry in trace.entries:
            first_ref = entry.source_refs[0] if entry.source_refs else None
            rows.append(
                {
                    "cut_item_id": entry.cut_item_id,
                    "source_scope": first_ref.source_scope if first_ref else "—",
                    "folder_name": first_ref.folder_name if first_ref else "—",
                    "chosen_asset_id": entry.chosen_asset_id or "—",
                    "fallback_used": entry.fallback_used,
                    "used_supplement_asset": entry.used_supplement_asset,
                    "duration_strategy": entry.duration_strategy or "—",
                    "visual_segment_count": entry.visual_segment_count,
                    "validation_warnings": ", ".join(entry.validation_warnings) or "—",
                    "validation_blockers": ", ".join(entry.validation_blockers) or "—",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.caption("Die isolierte EditPlan-Bridge (Phase 9.1) folgt im nächsten Bereich unten.")


def _render_edit_plan_bridge(project: Project) -> None:
    """Phase 9.1/9.2: isolierte EditPlan-Bridge. Übersetzt ausschließlich einen
    bereits BESTÄTIGTEN Cut Plan deterministisch in einen EditPlanDocument-
    kompatiblen Draft — kein Rebuild, keine Asset-Auswahl, keine Supplement-
    Suche, keine LLM-Aufrufe. Kein locked EditPlan, kein OTIO-Export."""
    st.subheader("EditPlan Bridge")
    st.caption(
        "Übersetzt den bestätigten Cut Plan deterministisch in einen isolierten, "
        "EditPlanDocument-kompatiblen Draft — getrennt von `_otio/edit_plan/` und "
        "`_otio/exports/`. Keine neue Asset-Auswahl, keine Supplement-Suche, kein LLM-Aufruf."
    )
    st.warning(
        "Dieser Bridge-Draft ist noch kein locked Produktions-EditPlan und nicht OTIO-exportbereit."
    )

    confirmed = load_confirmed_cut_plan(project)
    if confirmed is None:
        st.info("Noch kein bestätigter Cut Plan vorhanden — bitte zuerst im Bereich oben bestätigen.")
        return

    if is_confirmed_cut_plan_stale(project, confirmed):
        st.warning(
            "Der bestätigte Cut Plan ist veraltet (Voice-over-Projektplan oder Cut-Plan-Settings haben "
            "sich seit der Bestätigung geändert). Die Bridge kann trotzdem erzeugt werden, spiegelt dann "
            "aber einen veralteten Stand wider."
        )

    existing_bridge_draft = load_edit_plan_bridge_draft(project)
    if existing_bridge_draft is not None and is_edit_plan_bridge_stale(project, existing_bridge_draft):
        st.warning(
            "Der EditPlan-Bridge-Draft basiert auf einem älteren bestätigten Cut Plan. Bitte bei Bedarf "
            "neu erzeugen."
        )

    button_label = (
        "EditPlan Draft aus bestätigtem Cut Plan erzeugen"
        if existing_bridge_draft is None
        else "EditPlan Draft neu erzeugen"
    )
    if st.button(button_label, key=f"cut_plan_edit_plan_bridge_build_{project.id}", type="primary"):
        try:
            with st.spinner("EditPlan Bridge Draft wird erstellt…"):
                edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
                edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
                audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
                audio_plan = save_bridge_audio_plan(project, audio_plan)
                trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
                save_edit_plan_bridge_trace(project, trace)
                report = validate_edit_plan_bridge(project, edit_plan)
            audio_count = sum(1 for item in edit_plan.timeline_items if item.track == "A1")
            visual_count = len(edit_plan.timeline_items) - audio_count
            st.success(
                f"EditPlan Bridge Draft erzeugt: {len(edit_plan.timeline_items)} TimelineItems "
                f"({audio_count} Audio, {visual_count} Visual), {len(audio_plan.items)} BridgeAudioPlanItem(s), "
                f"{len(report.warnings)} Warnings, {len(report.blockers)} Blocker."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    bridge_draft = load_edit_plan_bridge_draft(project)
    if bridge_draft is None:
        return

    audio_items = [item for item in bridge_draft.timeline_items if item.track == "A1"]
    visual_items = [item for item in bridge_draft.timeline_items if item.track != "A1"]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("TimelineItems", len(bridge_draft.timeline_items))
    with col2:
        st.metric("Audio Items", len(audio_items))
    with col3:
        st.metric("Visual Items", len(visual_items))

    st.caption(f"Bridge Draft Pfad: `{get_cut_plan_edit_plan_bridge_draft_path(project.work_dir_path)}`")

    audio_plan = load_bridge_audio_plan(project)
    trace = load_edit_plan_bridge_trace(project)
    rounded_count = sum(1 for entry in trace.entries if entry.frame_rounded) if trace is not None else 0
    boundary_chained_count = (
        sum(1 for entry in trace.entries if entry.boundary_chained) if trace is not None else 0
    )

    col7, col8, col9, col10 = st.columns(4)
    with col7:
        st.metric("Bridge Audio Plan", "✅ Ja" if audio_plan is not None else "❌ Nein")
    with col8:
        st.metric("AudioPlanItems", len(audio_plan.items) if audio_plan is not None else 0)
    with col9:
        st.metric("Frame-gerundete Items", rounded_count)
    with col10:
        st.metric("Boundary-gechainte Items", boundary_chained_count)
    st.caption(
        "Boundary-Chaining angewendet: "
        + ("✅ Ja" if boundary_chained_count > 0 else "— Nein (keine Anpassung nötig)")
    )
    if audio_plan is not None:
        st.caption(f"Bridge Audio Plan Pfad: `{get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path)}`")

    report = load_edit_plan_bridge_validation_report(project)
    if report is not None:
        col4, col5, col6 = st.columns(3)
        with col4:
            if report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS:
                st.metric("Validation Status", "✅ PASS")
            elif report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING:
                st.metric("Validation Status", "⚠️ WARNING")
            elif report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED:
                st.metric("Validation Status", "❌ BLOCKED")
            else:
                st.metric("Validation Status", report.status)
        with col5:
            st.metric("Warnings", len(report.warnings))
        with col6:
            st.metric("Blockers", len(report.blockers))

        if report.warnings or report.blockers:
            rows = [
                {
                    "type": error.type,
                    "severity": error.severity,
                    "scope": error.scope,
                    "cut_item_id": error.cut_item_id or "—",
                    "visual_segment_id": error.visual_segment_id or "—",
                    "timeline_item_id": error.timeline_item_id or "—",
                    "message": error.message,
                    "fix_hint": error.fix_hint or "—",
                }
                for error in (report.blockers + report.warnings)
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("Keine Warnungen oder Blocker.")
    else:
        st.info("Noch kein Bridge Validation Report vorhanden.")

    if trace is not None and trace.entries:
        with st.expander("EditPlan Bridge Trace", expanded=False):
            rows = [
                {
                    "cut_item_id": entry.cut_item_id or "—",
                    "visual_segment_id": entry.visual_segment_id or "—",
                    "source_scope": entry.source_scope or "—",
                    "folder_name": entry.folder_name or "—",
                    "timeline_item_id": entry.timeline_item_id,
                    "timeline_item_type": entry.timeline_item_type,
                    "track": entry.track,
                    "asset_id": entry.asset_id or "—",
                    "original_timeline_in_sec": entry.original_timeline_in_sec,
                    "original_timeline_out_sec": entry.original_timeline_out_sec,
                    "rounded_timeline_in_sec": entry.rounded_timeline_in_sec,
                    "rounded_timeline_out_sec": entry.rounded_timeline_out_sec,
                    "timeline_in_sec": entry.timeline_in_sec,
                    "timeline_out_sec": entry.timeline_out_sec,
                    "source_in_sec": entry.source_in_sec,
                    "source_out_sec": entry.source_out_sec,
                    "frame_rounded": entry.frame_rounded,
                    "frame_rounding_delta_sec": entry.frame_rounding_delta_sec,
                    "boundary_chained": entry.boundary_chained,
                    "boundary_chain_delta_sec": entry.boundary_chain_delta_sec,
                    "source_duration_adjusted": entry.source_duration_adjusted,
                    "source_duration_delta_sec": entry.source_duration_delta_sec,
                    "reason": entry.reason or "—",
                }
                for entry in trace.entries
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    if audio_plan is not None and audio_plan.items:
        with st.expander("Bridge Audio Plan", expanded=False):
            rows = [
                {
                    "scope": item.scope,
                    "folder_name": item.folder_name or "—",
                    "timeline_item_id": item.timeline_item_id,
                    "timeline_in_sec": item.timeline_in_sec,
                    "timeline_out_sec": item.timeline_out_sec,
                    "source_in_sec": item.source_in_sec,
                    "source_out_sec": item.source_out_sec,
                    "duration_sec": item.duration_sec,
                    "track": item.track,
                    "source_cut_plan_audio_index": item.source_cut_plan_audio_index,
                }
                for item in audio_plan.items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    _render_edit_plan_bridge_confirm(project)

    st.caption("Kein OTIO-Button, kein Lock-Button — diese Schritte folgen erst in einer späteren Sub-Phase.")


def _render_edit_plan_bridge_confirm(project: Project) -> None:
    """Phase 9.3: EditPlan Bridge Confirm/Freeze. Übernimmt ausschließlich
    bereits validierte Bridge-Dateien unverändert als Snapshot — kein
    Rebuild, keine erneute Übersetzung, keine erneute Validierung. Weiterhin
    ein isolierter Bridge-Snapshot: KEIN Produktions-EditPlan, KEIN locked
    Produktionsplan, KEIN OTIO-Export."""
    st.subheader("EditPlan Bridge bestätigen")

    manifest = load_edit_plan_bridge_confirm_manifest(project)

    if manifest is not None:
        confirmed_edit_plan = load_confirmed_edit_plan_bridge(project)
        confirmed_audio_plan = load_confirmed_bridge_audio_plan(project)
        confirmed_trace = load_confirmed_bridge_trace(project)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", manifest.status)
        with col2:
            st.metric("TimelineItems", len(confirmed_edit_plan.timeline_items) if confirmed_edit_plan else 0)
        with col3:
            st.metric("AudioPlanItems", len(confirmed_audio_plan.items) if confirmed_audio_plan else 0)
        with col4:
            st.metric("TraceEntries", len(confirmed_trace.entries) if confirmed_trace else 0)

        st.caption(f"confirmed_at: `{manifest.confirmed_at}`")
        st.caption(f"source_cut_plan_hash: `{manifest.source_cut_plan_hash}`")
        st.caption(f"edit_plan_hash: `{manifest.edit_plan_hash}`")
        st.caption(f"bridge_audio_plan_hash: `{manifest.bridge_audio_plan_hash}`")
        for label, path_str in manifest.confirmed_files.items():
            st.caption(f"{label}: `{path_str}`")

        if is_confirmed_edit_plan_bridge_stale(project):
            st.warning("Der bestätigte Bridge-Snapshot ist veraltet. Bitte neu bestätigen.")
        st.info(
            "Es gibt einen bestätigten Bridge-Snapshot. Aktuelle Draft-Änderungen ersetzen ihn nicht automatisch."
        )

        if st.button("Bridge-Bestätigung zurücknehmen", key=f"cut_plan_edit_plan_bridge_unconfirm_{project.id}"):
            unconfirm_edit_plan_bridge(project)
            st.success("Bridge-Bestätigung zurückgenommen — confirmed-Dateien entfernt.")
            st.rerun()
    else:
        st.info("Noch kein bestätigter EditPlan-Bridge-Snapshot vorhanden.")

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    if eligible:
        st.success("Alle Confirm-Bedingungen sind erfüllt.")
    else:
        st.warning("Confirm-Bedingungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    if manifest is not None:
        st.warning(
            "Achtung: Dies ersetzt den bisherigen bestätigten Bridge-Snapshot unwiderruflich mit dem "
            "aktuellen Draft-Stand."
        )
        button_label = "Aktuellen Bridge Draft bestätigen und bestehenden Snapshot ersetzen"
    else:
        button_label = "EditPlan Bridge bestätigen"

    if st.button(
        button_label,
        key=f"cut_plan_edit_plan_bridge_confirm_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Übernimmt die bereits validierten Bridge-Dateien unverändert als Snapshot. Kein Rebuild, "
        "keine erneute Übersetzung/Validierung. Kein Produktions-EditPlan, kein OTIO-Export.",
    ):
        try:
            with st.spinner("EditPlan Bridge wird bestätigt…"):
                confirm_edit_plan_bridge(project)
            st.success(
                "EditPlan Bridge bestätigt — edit_plan_from_cut_plan.confirmed.json, "
                "bridge_audio_plan.confirmed.json, edit_plan_bridge_trace.confirmed.json und "
                "edit_plan_bridge_confirm_manifest.json wurden geschrieben."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.caption(
        "Dieser Bridge-Snapshot ist noch kein Produktions-EditPlan und nicht OTIO-exportbereit."
    )


def _render_staged_edit_plan_preview(project: Project, section: object) -> None:
    """Phase 10.4: Vorschau EINES gestagten EditPlanDocument — rein lesend,
    lädt nur bereits vorhandene Staging-Dateien."""
    document = load_staged_edit_plan(project, section.staging_section_id)
    if document is None:
        st.caption("staged edit_plan.json konnte nicht geladen werden.")
        return

    if document.voiceover is not None:
        voiceover = document.voiceover
        st.write("**Voiceover**")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption(f"path: `{voiceover.path}`")
            st.caption(f"timeline_start_sec: {voiceover.timeline_start_sec:.3f}")
        with col2:
            st.caption(f"timeline_end_sec: {voiceover.timeline_end_sec:.3f}")
            st.caption(f"duration_sec: {voiceover.duration_sec:.3f}")
        with col3:
            st.caption(f"duration_source: `{voiceover.duration_source}`")
            st.caption(f"trim_policy: `{voiceover.trim_policy}`")
    else:
        st.caption("Kein Voiceover in diesem gestagten EditPlanDocument.")

    st.write("**TimelineItems**")
    if not document.timeline_items:
        st.caption("Keine TimelineItems.")
    else:
        rows = [
            {
                "timeline_item_id": item.timeline_item_id,
                "type": item.type,
                "section_id": item.section_id,
                "folder_name": item.folder_name,
                "asset_id": item.asset_id or "—",
                "asset_path": item.resolved_media_path or "—",
                "timeline_in_sec": item.timeline_in_sec,
                "timeline_out_sec": item.timeline_out_sec,
                "source_in_sec": item.source_in_sec,
                "source_out_sec": item.source_out_sec,
                "selection_reason": item.selection_reason or "—",
            }
            for item in document.timeline_items
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.write("**Shots**")
    if not document.shots:
        st.caption("Keine Shots.")
    else:
        rows = [
            {
                "shot_id": getattr(shot, "shot_id", "") or "—",
                "asset_id": shot.asset_id or "—",
                "asset_path": shot.asset_path or "—",
                "duration_sec": shot.duration_sec,
                "voice_start_sec": shot.voice_start_sec,
                "voice_end_sec": shot.voice_end_sec,
                "beat_id": shot.beat_id or "—",
                "motif": shot.motif or "—",
                "passage_text": shot.passage_text or "—",
            }
            for shot in document.shots
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_production_plan_readonly_hint(project: Project, section: object) -> None:
    """Phase 10.4 §10: rein lesender Hinweis, ob für diese Sektion bereits ein
    Produktionsplan existiert — kein Blockieren, kein Merge, kein Schreiben."""
    if section.is_intro:
        st.caption("Intro ist eine synthetische Staging-Sektion und hat noch keinen Produktions-Zielpfad.")
        return
    existing_path = get_folder_edit_plan_path(project.work_dir_path, section.folder_name)
    exists = existing_path.is_file()
    st.caption(
        f"Produktionsplan existiert bereits: {'✅ Ja' if exists else '❌ Nein'} (`{existing_path}`)"
    )


def _render_production_edit_plan_pipeline_overview(project: Project) -> None:
    """Phase 10.9: Gesamt-Übersichts-Dashboard — rein lesende, live
    berechnete Aggregation des Status aller Phasen (Staging/Validation/
    Promote-Readiness/Promote/Voice-Folder-Mapping-Merge/OTIO-Export-
    Readiness). Kein neues Artefakt, kein Seiteneffekt. Bewusst als
    EIGENSTÄNDIGER Abschnitt VOR „Production EditPlan Staging“ platziert
    (nicht als verschachtelter Aufruf innerhalb dieser Funktion), damit
    Build-/Validate-/Promote-Button-Handler dieser und nachgelagerter
    Abschnitte unberührt bleiben."""
    st.subheader("Production EditPlan Pipeline — Übersicht")
    st.caption(
        "Gesamt-Status aller Phasen auf einen Blick — rein lesend, keine Aktion, kein neues Artefakt."
    )
    overview = build_production_edit_plan_pipeline_overview(project)

    overall_status_label = {
        PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED: "⚪ Noch nicht gestartet",
        PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS: "🔵 In Bearbeitung",
        PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED: "❌ Blockiert",
        PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE: "✅ Vollständig durchlaufen",
    }.get(overview.overall_status, overview.overall_status)

    st.markdown(f"**Gesamt-Status: {overall_status_label}**")

    columns = st.columns(len(overview.stages))
    for column, stage in zip(columns, overview.stages):
        with column:
            if not stage.exists:
                st.metric(stage.label, "—")
            else:
                stage_label = stage.status
                if stage.is_stale:
                    stage_label = f"{stage_label} ⚠️"
                st.metric(stage.label, stage_label)
            st.caption(stage.detail)

    if any(stage.is_stale for stage in overview.stages if stage.exists):
        st.warning(
            "Mindestens eine Phase ist veraltet (⚠️) — bitte die betroffene Phase unten erneut ausführen, "
            "bevor du dich auf nachgelagerte Phasen verlässt."
        )

    not_started_stages = [stage for stage in overview.stages if not stage.exists]
    if not_started_stages and overview.overall_status != PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED:
        st.caption(
            "Noch nicht ausgeführt: " + ", ".join(f"„{stage.label}“" for stage in not_started_stages)
        )


def _render_production_edit_plan_staging(project: Project) -> None:
    """Phase 10.4: UI für das isolierte Production-EditPlan-Staging (Phase
    10.1-10.3). Rein anzeigend/erzeugend/validierend — kein Promote nach
    `_otio/edit_plan/`, kein Lock, kein OTIO-Export, kein Render, kein Aufruf
    der Save- oder Build-Funktionen der bestehenden Produktions-EditPlan-
    Pipeline, keine Produktions-Dateien werden überschrieben."""
    st.subheader("Production EditPlan Staging")
    st.warning(
        "Dieses Staging-Paket ist noch kein Produktions-EditPlan. Es wird nicht nach "
        "`_otio/edit_plan/` geschrieben und ist noch nicht OTIO-exportbereit."
    )
    st.caption("Promote nach `_otio/edit_plan/` erfolgt erst in einer späteren Phase.")

    st.markdown("**Voraussetzungen**")
    eligible, reasons = can_build_production_edit_plan_staging(project)
    if eligible:
        st.success("Alle Voraussetzungen sind erfüllt.")
    else:
        st.warning("Voraussetzungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    package = load_production_edit_plan_staging_package(project)

    col_build, col_validate = st.columns(2)
    with col_build:
        if st.button(
            "Production EditPlan Staging erzeugen",
            key=f"production_edit_plan_staging_build_{project.id}",
            disabled=not eligible,
            type="primary",
            help="Übersetzt den bestätigten EditPlan-Bridge-Snapshot in ein isoliertes "
            "Staging-Paket (Package + gestagte EditPlanDocuments + Mapping-Trace). Kein "
            "Rebuild der Bridge, kein Promote, kein OTIO-Export. Führt keine automatische "
            "Validierung durch — dafür bitte separat unten „validieren“ klicken.",
        ):
            try:
                with st.spinner("Production EditPlan Staging wird erzeugt…"):
                    new_package = build_and_save_production_edit_plan_staging(project)
                package = new_package
                total_timeline_items = sum(section.timeline_item_count for section in new_package.sections)
                st.success(
                    f"Staging erzeugt: {len(new_package.sections)} Sektion(en), "
                    f"{len(new_package.sections)} gestagte(s) EditPlanDocument(e), "
                    f"{total_timeline_items} TimelineItem(s) gesamt, Package-Status "
                    f"{new_package.status}. Pfad: "
                    f"`{get_production_edit_plan_package_path(project.work_dir_path)}`"
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_validate:
        if st.button(
            "Production EditPlan Staging validieren",
            key=f"production_edit_plan_staging_validate_{project.id}",
            disabled=package is None,
            help="Führt die vollständige Revalidierung des Staging-Pakets durch (Phase "
            "10.3) und speichert production_edit_plan_validation_report.json. Rein "
            "prüfend — keine automatische Reparatur, kein Promote, kein OTIO-Export. "
            "Verändert production_edit_plan_package.json nicht.",
        ):
            try:
                with st.spinner("Staging wird validiert…"):
                    validation_report = validate_production_edit_plan_staging(project)
                if validation_report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS:
                    st.success("Validierung: PASS.")
                elif validation_report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING:
                    st.warning(f"Validierung: WARNING ({len(validation_report.warnings)} Warnings).")
                else:
                    st.error(f"Validierung: BLOCKED ({len(validation_report.blockers)} Blocker).")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if package is None:
        st.info("Noch kein Production EditPlan Staging erzeugt.")
        return

    package_stale = is_production_edit_plan_staging_stale(project, package)
    report = load_production_edit_plan_validation_report(project)
    report_stale = report is not None and is_production_edit_plan_validation_report_stale(project, report)

    # --- §11 Status-Logik ---
    if report is None:
        st.info("Staging erzeugt, aber noch nicht validiert.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS:
        st.success("Staging ist validiert und bereit für spätere Promote-Planung.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING:
        st.warning("Staging ist validiert, enthält aber Warnungen.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED:
        st.error("Staging ist blockiert. Bitte Fehler prüfen.")
    if package_stale:
        st.warning("Das Staging-Paket ist veraltet. Bitte neu erzeugen.")
    if report_stale:
        st.warning("Der Validation Report ist veraltet. Bitte erneut validieren.")

    # --- §6 Package-Anzeige ---
    st.markdown("**Package**")
    total_timeline_items = sum(section.timeline_item_count for section in package.sections)
    total_shots = sum(section.shot_count for section in package.sections)
    sections_with_voiceover = sum(1 for section in package.sections if section.has_voiceover)

    package_status_label = {
        PRODUCTION_EDIT_PLAN_STATUS_STAGED: "✅ STAGED",
        PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW: "⚠️ NEEDS_REVIEW",
        PRODUCTION_EDIT_PLAN_STATUS_BLOCKED: "❌ BLOCKED",
    }.get(package.status, package.status)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Package Status", package_status_label)
    with col2:
        st.metric("Sektionen", len(package.sections))
    with col3:
        st.metric("TimelineItems gesamt", total_timeline_items)
    with col4:
        st.metric("Shots gesamt", total_shots)

    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("Sektionen mit Voiceover", sections_with_voiceover)
    with col6:
        st.metric("Staging veraltet", "⚠️ Ja" if package_stale else "✅ Nein")
    with col7:
        st.caption(f"source_bridge_manifest_hash: `{package.source_bridge_manifest_hash}`")
        st.caption(f"source_cut_plan_hash: `{package.source_cut_plan_hash}`")

    section_rows = [
        {
            "staging_section_id": section.staging_section_id,
            "production_section_id": section.production_section_id,
            "folder_name": section.folder_name,
            "is_intro": section.is_intro,
            "shot_count": section.shot_count,
            "timeline_item_count": section.timeline_item_count,
            "has_voiceover": section.has_voiceover,
            "staged_edit_plan_hash": section.staged_edit_plan_hash,
            "warnings": len(section.warnings),
            "blockers": len(section.blockers),
            "staged_edit_plan_path": section.staged_edit_plan_path,
        }
        for section in package.sections
    ]
    st.dataframe(section_rows, use_container_width=True, hide_index=True)

    # --- §7 Staged EditPlan Preview + §10 Read-only Produktionsplan-Hinweis ---
    st.markdown("**Gestagte EditPlanDocuments**")
    for section in package.sections:
        with st.expander(
            f"{section.staging_section_id} — {section.folder_name or 'Intro'}", expanded=False
        ):
            _render_production_plan_readonly_hint(project, section)
            _render_staged_edit_plan_preview(project, section)

    # --- §8 Validation Report Anzeige ---
    st.markdown("**Validation Report**")
    if report is None:
        st.info("Noch kein Validation Report vorhanden.")
    else:
        report_status_label = {
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS: "✅ PASS",
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING: "⚠️ WARNING",
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED: "❌ BLOCKED",
        }.get(report.status, report.status)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Validation Status", report_status_label)
        with col2:
            st.metric("Warnings", len(report.warnings))
        with col3:
            st.metric("Blockers", len(report.blockers))
        st.caption(f"generated_at: `{report.generated_at}`")
        st.caption(f"package_hash: `{report.package_hash}`")
        st.caption(f"source_bridge_manifest_hash: `{report.source_bridge_manifest_hash}`")
        st.caption(
            f"Validation Report Pfad: `{get_production_edit_plan_validation_report_path(project.work_dir_path)}`"
        )

        if report.warnings or report.blockers:
            rows = [
                {
                    "type": error.type,
                    "severity": error.severity,
                    "scope": error.scope,
                    "staging_section_id": error.staging_section_id or "—",
                    "production_section_id": error.production_section_id or "—",
                    "timeline_item_id": error.timeline_item_id or "—",
                    "message": error.message,
                    "fix_hint": error.fix_hint or "—",
                }
                for error in (report.blockers + report.warnings)
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("Keine Warnungen oder Blocker.")

        if report_stale:
            st.warning("Der Validation Report ist veraltet. Bitte Production EditPlan Staging erneut validieren.")

    # --- §9 Mapping Trace Anzeige ---
    trace = load_production_edit_plan_mapping_trace(project)
    if trace is not None and trace.entries:
        with st.expander("Mapping Trace", expanded=False):
            rows = [
                {
                    "trace_id": entry.trace_id,
                    "source_bridge_timeline_item_id": entry.source_bridge_timeline_item_id or "—",
                    "source_bridge_audio_plan_index": (
                        entry.source_bridge_audio_plan_index
                        if entry.source_bridge_audio_plan_index is not None
                        else "—"
                    ),
                    "source_cut_item_id": entry.source_cut_item_id or "—",
                    "source_visual_segment_id": entry.source_visual_segment_id or "—",
                    "resulting_staging_section_id": entry.resulting_staging_section_id,
                    "resulting_production_section_id": entry.resulting_production_section_id,
                    "resulting_timeline_item_id": entry.resulting_timeline_item_id or "—",
                    "folder_name": entry.folder_name,
                    "is_intro": entry.is_intro,
                    "original_timeline_in_sec": entry.original_timeline_in_sec,
                    "original_timeline_out_sec": entry.original_timeline_out_sec,
                    "local_timeline_in_sec": entry.local_timeline_in_sec,
                    "local_timeline_out_sec": entry.local_timeline_out_sec,
                    "asset_id": entry.asset_id or "—",
                    "asset_path": entry.asset_path or "—",
                    "mapping_reason": entry.mapping_reason,
                    "fields_defaulted": ", ".join(entry.fields_defaulted) or "—",
                    "fields_dropped": ", ".join(entry.fields_dropped) or "—",
                    "warnings": ", ".join(entry.warnings) or "—",
                }
                for entry in trace.entries
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
    elif get_production_edit_plan_mapping_trace_path(project.work_dir_path).is_file():
        st.caption("Mapping Trace existiert, enthält aber keine Einträge.")

    st.divider()
    _render_production_edit_plan_promote_readiness(project, package, report)

    st.divider()
    _render_production_edit_plan_promote_execute(project)

    st.divider()
    _render_voice_folder_mapping_merge(project)

    st.divider()
    _render_otio_export_readiness(project)

    st.caption(
        "Kein OTIO-Export-Button, kein Lock-Button — diese Schritte folgen erst in einer späteren Phase."
    )


def _render_production_edit_plan_promote_readiness(
    project: Project, package: object, report: object
) -> None:
    """Phase 10.5: Promote Readiness / Dry Run — rein prüfend, was ein
    SPÄTERER Promote nach `_otio/edit_plan/` tun würde. Kein tatsächliches
    Kopieren, kein Lock, kein OTIO-Export, kein Render, kein Aufruf der
    Save- oder Build-Funktionen der bestehenden Produktions-EditPlan-
    Pipeline, keine Änderung an `voice_folder_mapping.json`."""
    st.subheader("Promote Readiness / Dry Run")
    st.caption(
        "Dieser Dry Run schreibt nichts nach `_otio/edit_plan/`. Er zeigt nur, was ein späterer "
        "Promote tun würde."
    )

    if st.button(
        "Promote Dry Run ausführen",
        key=f"production_edit_plan_promote_dry_run_{project.id}",
        disabled=package is None or report is None,
        type="primary",
        help="Prüft rein lesend, ob und wie das validierte Staging-Paket später nach "
        "_otio/edit_plan/ übertragen werden könnte — inkl. Kollisionsprüfung gegen "
        "bereits existierende Produktionspläne. Schreibt ausschließlich "
        "production_edit_plan_promote_readiness.json und "
        "production_edit_plan_promote_dry_run_trace.json.",
    ):
        readiness = build_production_edit_plan_promote_readiness(project)
        readiness = save_production_edit_plan_promote_readiness(project, readiness)
        dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
        save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
        st.success(f"Promote Dry Run ausgeführt — Status {readiness.status}.")
        st.rerun()

    readiness = load_production_edit_plan_promote_readiness(project)
    if readiness is None:
        st.info("Noch kein Promote Dry Run ausgeführt.")
        return

    if is_production_edit_plan_promote_readiness_stale(project, readiness):
        st.warning("Der Promote Dry Run ist veraltet (Staging oder Validation Report haben sich geändert).")

    would_create = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE
    )
    would_overwrite = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
    )
    would_skip_intro = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_SKIP_INTRO
    )
    would_block = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    )

    readiness_status_label = {
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY: "✅ READY",
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW: "⚠️ NEEDS_REVIEW",
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED: "❌ BLOCKED",
    }.get(readiness.status, readiness.status)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Status", readiness_status_label)
    with col2:
        st.metric("WOULD_CREATE", would_create)
    with col3:
        st.metric("WOULD_OVERWRITE", would_overwrite)
    with col4:
        st.metric("WOULD_SKIP_INTRO", would_skip_intro)

    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("BLOCKED", would_block)
    with col6:
        st.metric("Warnings", len(readiness.warnings))
    with col7:
        st.metric("Blockers", len(readiness.blockers))

    if readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY:
        st.success("Dry Run bereit. Ein späterer Promote könnte neue Produktions-EditPlans erzeugen.")
    elif readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW:
        if would_overwrite:
            st.warning(
                "Für mindestens einen Ordner existiert bereits ein Produktionsplan. Ein späterer "
                "Promote müsste ausdrücklich bestätigt werden."
            )
        else:
            st.warning("Dry Run enthält Warnungen. Bitte prüfen.")
    elif readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED:
        st.error("Dry Run blockiert. Bitte Staging/Validation prüfen.")

    section_rows = [
        {
            "staging_section_id": section.staging_section_id,
            "folder_name": section.folder_name,
            "is_intro": section.is_intro,
            "promote_action": section.promote_action,
            "target_exists": section.target_exists,
            "target_edit_plan_path": section.target_edit_plan_path or "—",
            "existing_confirmed": section.existing_confirmed if section.existing_confirmed is not None else "—",
            "existing_candidate_status": section.existing_candidate_status or "—",
            "existing_shot_count": section.existing_shot_count if section.existing_shot_count is not None else "—",
            "existing_timeline_item_count": (
                section.existing_timeline_item_count if section.existing_timeline_item_count is not None else "—"
            ),
            "warnings": len(section.warnings),
            "blockers": len(section.blockers),
        }
        for section in readiness.sections
    ]
    st.dataframe(section_rows, use_container_width=True, hide_index=True)

    if readiness.warnings or readiness.blockers:
        with st.expander("Dry Run Warnungen / Blocker", expanded=bool(readiness.blockers)):
            for blocker in readiness.blockers:
                st.error(blocker)
            for warning in readiness.warnings:
                st.warning(warning)

    dry_run_trace = load_production_edit_plan_promote_dry_run_trace(project)
    if dry_run_trace is not None and dry_run_trace.entries:
        with st.expander("Promote Dry Run Trace", expanded=False):
            rows = [
                {
                    "trace_id": entry.trace_id,
                    "staging_section_id": entry.staging_section_id,
                    "production_section_id": entry.production_section_id,
                    "folder_name": entry.folder_name,
                    "is_intro": entry.is_intro,
                    "target_edit_plan_path": entry.target_edit_plan_path or "—",
                    "promote_action": entry.promote_action,
                    "reason": entry.reason,
                    "would_write": entry.would_write,
                    "would_overwrite": entry.would_overwrite,
                    "warnings": ", ".join(entry.warnings) or "—",
                    "blockers": ", ".join(entry.blockers) or "—",
                }
                for entry in dry_run_trace.entries
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_production_edit_plan_promote_execute(project: Project) -> None:
    """Phase 10.6: Actual Production EditPlan Promote — Backup, Manifest,
    Kollisionsschutz. Dies ist die EINZIGE Stelle, an der nach
    `_otio/edit_plan/` geschrieben wird — ausschließlich über
    promote_production_edit_plans() in production_edit_plan_promote_execute
    .py. Kein OTIO-Export, kein Render, kein Lock-Konzept, keine
    LLM-Planung, keine automatische Neuplanung, keine automatische
    Supplement-Suche."""
    st.subheader("Production EditPlan Promote")
    st.warning("Dieser Schritt schreibt nach `_otio/edit_plan/`.")
    st.caption("Bestehende Produktionspläne werden nur überschrieben, wenn du das explizit bestätigst.")
    st.caption("Intro wird in dieser Phase nicht promotet.")
    st.caption("Es wird kein OTIO exportiert.")

    readiness = load_production_edit_plan_promote_readiness(project)
    if readiness is None:
        st.info("Bitte zuerst oben einen Promote Dry Run ausführen.")
        return

    readiness_status_label = {
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY: "✅ READY",
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW: "⚠️ NEEDS_REVIEW",
        PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED: "❌ BLOCKED",
    }.get(readiness.status, readiness.status)
    would_create = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE
    )
    would_overwrite_sections = [
        s for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
    ]
    would_skip_intro = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_SKIP_INTRO
    )
    would_block = sum(
        1 for s in readiness.sections if s.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Readiness Status", readiness_status_label)
    with col2:
        st.metric("WOULD_CREATE", would_create)
    with col3:
        st.metric("WOULD_OVERWRITE", len(would_overwrite_sections))
    with col4:
        st.metric("WOULD_SKIP_INTRO", would_skip_intro)
    with col5:
        st.metric("BLOCKED", would_block)

    allow_overwrite_section_ids: list[str] = []
    if would_overwrite_sections:
        st.markdown("**Overwrite-Bestätigung erforderlich**")
        rows = [
            {
                "staging_section_id": section.staging_section_id,
                "folder_name": section.folder_name,
                "target_edit_plan_path": section.target_edit_plan_path or "—",
                "existing_confirmed": section.existing_confirmed if section.existing_confirmed is not None else "—",
            }
            for section in would_overwrite_sections
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        overwrite_confirmed = st.checkbox(
            "Ich bestätige, dass bestehende Produktionspläne für die ausgewählten Folder überschrieben "
            "werden dürfen.",
            key=f"production_edit_plan_promote_overwrite_confirm_{project.id}",
        )
        for section in would_overwrite_sections:
            checked = st.checkbox(
                f"Überschreiben erlauben: {section.folder_name} ({section.staging_section_id})",
                key=f"production_edit_plan_promote_overwrite_{project.id}_{section.staging_section_id}",
                disabled=not overwrite_confirmed,
            )
            if overwrite_confirmed and checked:
                allow_overwrite_section_ids.append(section.staging_section_id)

    eligible, reasons = can_promote_production_edit_plans(
        project, allow_overwrite_section_ids=allow_overwrite_section_ids
    )
    if not eligible:
        with st.expander("Promote-Voraussetzungen nicht erfüllt", expanded=False):
            for reason in reasons:
                st.write(f"❌ {reason}")

    if st.button(
        "Production EditPlans promoten",
        key=f"production_edit_plan_promote_execute_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Schreibt alle Nicht-Intro-Sections des validierten Staging-Pakets nach "
        "_otio/edit_plan/{folder}.json (confirmed=true). Vor jedem Überschreiben wird ein Backup "
        "erzeugt. Kein OTIO-Export, kein Render, kein Lock. voice_folder_mapping.json wird nicht "
        "verändert — stattdessen wird ein Vorbereitungs-Patch geschrieben.",
    ):
        try:
            with st.spinner("Production EditPlans werden promotet…"):
                manifest = promote_production_edit_plans(
                    project, allow_overwrite_section_ids=allow_overwrite_section_ids
                )
                manifest = save_production_edit_plan_promote_manifest(project, manifest)
                patch = build_voice_folder_mapping_patch(project, manifest)
                save_voice_folder_mapping_patch(project, patch)
            st.success(
                f"Promote abgeschlossen: {manifest.created_count} neu erstellt, "
                f"{manifest.overwritten_count} überschrieben, {manifest.skipped_intro_count} Intro "
                f"übersprungen. Backup-Verzeichnis: `{manifest.backup_dir or '—'}`. Manifest: "
                f"`{get_production_edit_plan_promote_manifest_path(project.work_dir_path)}`. "
                f"Mapping Patch: `{get_production_edit_plan_voice_folder_mapping_patch_path(project.work_dir_path)}`."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    # --- §12 Manifest-Anzeige ---
    manifest = load_production_edit_plan_promote_manifest(project)
    if manifest is not None:
        st.markdown("**Promote Manifest**")
        if is_production_edit_plan_promote_manifest_stale(project, manifest):
            st.warning(
                "Das Promote Manifest ist veraltet (Readiness/Staging/Validation haben sich seit diesem "
                "Promote-Lauf geändert)."
            )

        manifest_status_label = {
            PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED: "✅ PROMOTED",
            PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_NEEDS_REVIEW: "⚠️ NEEDS_REVIEW",
            PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED: "❌ BLOCKED",
        }.get(manifest.status, manifest.status)

        mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
        with mcol1:
            st.metric("Status", manifest_status_label)
        with mcol2:
            st.metric("promote_run_id", manifest.promote_run_id or "—")
        with mcol3:
            st.metric("Created", manifest.created_count)
        with mcol4:
            st.metric("Overwritten", manifest.overwritten_count)
        with mcol5:
            st.metric("Skipped Intro", manifest.skipped_intro_count)
        st.metric("Blocked", manifest.blocked_count)
        st.caption(f"backup_dir: `{manifest.backup_dir or '—'}`")

        section_rows = [
            {
                "staging_section_id": section.staging_section_id,
                "folder_name": section.folder_name,
                "is_intro": section.is_intro,
                "promote_action": section.promote_action,
                "target_edit_plan_path": section.target_edit_plan_path or "—",
                "source_hash": section.source_hash,
                "target_hash_after": section.target_hash_after or "—",
                "backup_path": section.backup_path or "—",
                "backup_hash": section.backup_hash or "—",
                "confirmed_set_to_true": section.confirmed_set_to_true,
                "warnings": len(section.warnings),
                "blockers": len(section.blockers),
            }
            for section in manifest.sections
        ]
        st.dataframe(section_rows, use_container_width=True, hide_index=True)

        # --- §12 Mapping Patch Anzeige ---
        patch = load_voice_folder_mapping_patch(project)
        if patch is not None:
            st.markdown("**Voice Folder Mapping Patch**")
            st.caption(
                "voice_folder_mapping.json wurde nicht geändert. Dieser Patch ist nur Vorbereitung "
                "für eine spätere Mapping-/Export-Phase."
            )
            patch_action_label = {
                PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD: "WOULD_ADD",
                PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT: "ALREADY_PRESENT",
                PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW: "NEEDS_REVIEW",
            }
            patch_rows = [
                {
                    "folder_name": entry.folder_name,
                    "edit_plan_path": entry.edit_plan_path,
                    "voiceover_path": entry.voiceover_path or "—",
                    "voiceover_duration_sec": entry.voiceover_duration_sec,
                    "action": patch_action_label.get(entry.action, entry.action),
                    "reason": entry.reason,
                }
                for entry in patch.entries
            ]
            st.dataframe(patch_rows, use_container_width=True, hide_index=True)
            for warning in patch.warnings:
                st.warning(warning)


def _render_voice_folder_mapping_merge(project: Project) -> None:
    """Phase 10.7: Voice Folder Mapping Merge — explizit bestätigte,
    selektive Übernahme des Vorbereitungs-Patches (Phase 10.6) in die echte
    `voice_folder_mapping.json`. Dies ist die EINZIGE Stelle, an der diese
    Datei tatsächlich verändert wird — ausschließlich über
    merge_voice_folder_mapping() in
    production_edit_plan_voice_folder_mapping_merge.py. Kein OTIO-Export,
    kein Render, kein Lock, keine automatische Neuplanung."""
    st.subheader("Voice Folder Mapping übernehmen")
    st.warning("Dieser Schritt schreibt nach `voice_folder_mapping.json`.")
    st.caption("Konflikte (NEEDS_REVIEW) müssen explizit aufgelöst werden, bevor der Merge ausgeführt werden kann.")
    st.caption(
        "Die Gesamt-Zuordnung wird dadurch NICHT automatisch als vollständig bestätigt markiert — das bleibt "
        "Aufgabe des Tabs „② Zuordnung“."
    )

    patch = load_voice_folder_mapping_patch(project)
    if patch is None:
        st.info("Bitte zuerst oben einen Production EditPlan Promote ausführen (erzeugt den Mapping Patch).")
        return

    needs_review_entries = [
        entry for entry in patch.entries if entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW
    ]
    folder_resolutions: dict[str, str] = {}
    if needs_review_entries:
        st.markdown("**Konflikte auflösen**")
        for entry in needs_review_entries:
            st.write(f"**{entry.folder_name}**: {entry.reason}")
            choice = st.radio(
                f"Auflösung für {entry.folder_name}",
                options=[
                    "Nicht entschieden",
                    "Neuen Voice-over übernehmen (APPLY)",
                    "Bestehenden Eintrag behalten (SKIP)",
                ],
                key=f"voice_folder_mapping_merge_resolution_{project.id}_{entry.folder_name}",
                label_visibility="collapsed",
            )
            if choice.startswith("Neuen"):
                folder_resolutions[entry.folder_name] = VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY
            elif choice.startswith("Bestehenden"):
                folder_resolutions[entry.folder_name] = VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP

    mark_entries_confirmed = st.checkbox(
        "Übernommene Einträge zusätzlich als bestätigt markieren (überspringt die manuelle Prüfung im Tab "
        "„② Zuordnung“).",
        key=f"voice_folder_mapping_merge_mark_confirmed_{project.id}",
    )

    eligible, reasons = can_merge_voice_folder_mapping(project, folder_resolutions=folder_resolutions)
    if not eligible:
        with st.expander("Merge-Voraussetzungen nicht erfüllt", expanded=False):
            for reason in reasons:
                st.write(f"❌ {reason}")

    if st.button(
        "Voice Folder Mapping aktualisieren",
        key=f"voice_folder_mapping_merge_execute_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Übernimmt den Mapping Patch selektiv in voice_folder_mapping.json — mit Backup vor jedem "
        "Überschreiben. Kein OTIO-Export, kein Render, kein Lock.",
    ):
        try:
            with st.spinner("Voice Folder Mapping wird aktualisiert…"):
                manifest = merge_voice_folder_mapping(
                    project,
                    folder_resolutions=folder_resolutions,
                    mark_entries_confirmed=mark_entries_confirmed,
                )
                manifest = save_voice_folder_mapping_merge_manifest(project, manifest)
            st.success(
                f"Merge abgeschlossen: {manifest.added_count} neu hinzugefügt, "
                f"{manifest.updated_count} aktualisiert, {manifest.skipped_count} übersprungen. "
                f"Backup: `{manifest.backup_path or '—'}`."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    manifest = load_voice_folder_mapping_merge_manifest(project)
    if manifest is not None:
        st.markdown("**Voice Folder Mapping Merge Manifest**")
        if is_voice_folder_mapping_merge_manifest_stale(project, manifest):
            st.warning(
                "Das Merge Manifest ist veraltet (Mapping Patch oder voice_folder_mapping.json haben sich seit "
                "diesem Merge-Lauf geändert)."
            )

        manifest_status_label = {
            VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED: "✅ MERGED",
            VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED: "❌ BLOCKED",
        }.get(manifest.status, manifest.status)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", manifest_status_label)
        with col2:
            st.metric("Added", manifest.added_count)
        with col3:
            st.metric("Updated", manifest.updated_count)
        with col4:
            st.metric("Skipped", manifest.skipped_count)
        st.caption(f"backup_path: `{manifest.backup_path or '—'}`")

        rows = [
            {
                "folder_name": entry.folder_name,
                "action": entry.action,
                "previous_voice_file": entry.previous_voice_file or "—",
                "new_voice_file": entry.new_voice_file or "—",
                "applied": entry.applied,
                "reason": entry.reason,
            }
            for entry in manifest.entries
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(
            "Bitte im Tab „② Zuordnung“ prüfen, ob die Gesamt-Zuordnung weiterhin vollständig bestätigt ist."
        )


def _render_otio_export_readiness(project: Project) -> None:
    """Phase 10.8: OTIO Export Readiness Check — rein lesende, vollständig
    isolierte Struktur-Diagnose für bereits promotete (Phase 10.6) UND
    gemappte (Phase 10.7) Folder. Ruft KEINE Funktion der bestehenden
    Produktions-Export-Pipeline auf — prüft eigenständig dieselben
    grundlegenden Voraussetzungen (bestätigte Zuordnung, bestätigter
    EditPlan, nicht-leere Timeline/Shots). Kein Export, keine .otio-Datei,
    kein ffprobe, kein Render, kein Lock."""
    st.subheader("OTIO Export Readiness (promotete Folder)")
    st.caption(
        "Prüft rein lesend und vollständig isoliert, ob die grundlegenden Voraussetzungen für einen "
        "späteren OTIO-Export erfüllt sind — exportiert selbst nichts, schreibt keine .otio-Datei und "
        "ruft die bestehende Export-Pipeline nicht auf."
    )

    if st.button(
        "OTIO Export Readiness prüfen",
        key=f"otio_export_readiness_check_{project.id}",
        help="Rein strukturelle, vollständig isolierte Prüfung (bestätigte Zuordnung, bestätigter "
        "EditPlan, nicht-leere Timeline/Shots) für die zuletzt promoteten Folder — kein Export, kein "
        "ffprobe, kein Schreiben, kein Aufruf der Produktions-Export-Pipeline.",
    ):
        with st.spinner("OTIO Export Readiness wird geprüft…"):
            report = build_otio_export_readiness_report(project)
            save_otio_export_readiness_report(project, report)
        st.rerun()

    report = load_otio_export_readiness_report(project)
    if report is None:
        st.info("Noch kein OTIO Export Readiness Check ausgeführt.")
        return

    report_status_label = {
        PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY: "✅ READY",
        PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY: "⚠️ NOT_READY",
        PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED: "❌ BLOCKED",
    }.get(report.status, report.status)

    if report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY:
        st.success("READY — die grundlegenden Voraussetzungen sind für alle geprüften Folder erfüllt.")
    elif report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY:
        st.warning("NOT_READY — mindestens ein geprüfter Folder erfüllt die Voraussetzungen noch nicht.")
    else:
        st.error("BLOCKED — die grundlegenden Voraussetzungen sind aktuell nicht erfüllt.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Status", report_status_label)
    with col2:
        st.metric("Geprüfte Folder", len(report.checked_folders))
    with col3:
        st.metric("TimelineItems (Vorschau)", report.total_timeline_items)
    with col4:
        st.metric("Shots gesamt", report.total_shots)
    st.caption(f"voice_folder_mapping.json bestätigt: {'✅ Ja' if report.mapping_confirmed else '❌ Nein'}")

    rows = [
        {
            "folder_name": folder.folder_name,
            "status": folder.status,
            "edit_plan_exists": folder.edit_plan_exists,
            "edit_plan_confirmed": folder.edit_plan_confirmed,
            "in_confirmed_mapping": folder.in_confirmed_mapping,
            "has_voiceover": folder.has_voiceover,
            "shot_count": folder.shot_count,
            "timeline_item_count": folder.timeline_item_count,
            "warnings": ", ".join(folder.warnings) or "—",
        }
        for folder in report.folders
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if report.warnings:
        with st.expander("Warnungen", expanded=False):
            for warning in report.warnings:
                st.warning(warning)

    st.caption(
        "Für eine verbindliche Aussage und einen tatsächlichen Export bitte den bestehenden Tab "
        "„③ Schnittplan → 📤 OTIO Export“ verwenden."
    )


_WORKFLOW_STATUS_ICONS: dict[str, str] = {
    CUT_PLAN_WORKFLOW_STATUS_DONE: "✅",
    CUT_PLAN_WORKFLOW_STATUS_READY: "🟡",
    CUT_PLAN_WORKFLOW_STATUS_STALE: "🟡",
    CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED: "⚪",
    CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED: "⚫",
    CUT_PLAN_WORKFLOW_STATUS_BLOCKED: "🔴",
}

def _run_cut_plan_workflow_action(project: Project, action_key: str) -> str:
    """Führt GENAU EINE Workflow-Aktion aus (dispatcht über den
    maschinenlesbaren `next_action_key`, siehe cut_plan_workflow_state.py)
    und gibt eine kurze Erfolgsmeldung zurück. Wirft ValueError/RuntimeError
    unverändert weiter — der Aufrufer zeigt sie als st.error an.

    Bewusst dieselben Funktionen wie die Detail-Buttons weiter unten im
    Tab — keine Logik-Duplikation, nur ein zentraler Dispatch-Punkt."""
    if action_key == CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT:
        new_draft = build_cut_plan_draft(project)
        save_cut_plan_draft(project, new_draft)
        return "Cut Plan Draft erstellt."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_APPLY_ASSET_SELECTION:
        apply_asset_selection_to_draft(project)
        return "Asset-Auswahl angewendet."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_VALIDATE:
        validate_cut_plan_draft(project)
        return "Validierung abgeschlossen."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS:
        draft = load_cut_plan_draft(project)
        if draft is None:
            raise ValueError("Kein Cut Plan Draft vorhanden.")
        prior_document = load_cut_plan_supplement_requests(project)
        new_document = build_supplement_requests_from_cut_plan(project, draft)
        new_document = merge_prior_supplement_request_state(new_document, prior_document)
        save_cut_plan_supplement_requests(project, new_document)
        return f"{len(new_document.requests)} Supplement Request(s) erzeugt."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_REAPPLY_SUPPLEMENT_ASSETS:
        _, applied, skipped = reapply_accepted_supplements_to_cut_plan(project)
        return f"{len(applied)} Item(s) übernommen, {len(skipped)} übersprungen."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_SUPPLEMENTS:
        model_settings = load_model_settings(project)
        results = auto_resolve_all_cut_plan_supplement_requests(
            project,
            query_llm_provider=model_settings.cut_plan_supplement_query.provider,
            query_llm_model=model_settings.cut_plan_supplement_query.model,
        )
        accepted = sum(1 for result in results if result.status == AUTO_RESOLVE_STATUS_ACCEPTED)
        return f"{len(results)} Request(s) bearbeitet, {accepted} automatisch akzeptiert."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS:
        draft = load_cut_plan_draft(project)
        if draft is None:
            raise ValueError("Kein Cut Plan Draft vorhanden.")
        prior_document = load_residual_gap_requests(project)
        new_document = build_residual_gap_requests_from_cut_plan(project, draft)
        new_document = merge_prior_residual_gap_request_state(new_document, prior_document)
        save_residual_gap_requests(project, new_document)
        return f"{len(new_document.requests)} Residual Gap Request(s) erzeugt."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_REAPPLY_RESIDUAL_GAP_ASSETS:
        _, applied, skipped = reapply_accepted_residual_gap_assets(project)
        return f"{len(applied)} Item(s) übernommen, {len(skipped)} übersprungen."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_RESIDUAL_GAPS:
        results = auto_resolve_all_residual_gap_requests(project)
        accepted = sum(1 for result in results if result.status == RESIDUAL_GAP_AUTO_RESOLVE_STATUS_ACCEPTED)
        return f"{len(results)} Request(s) bearbeitet, {accepted} automatisch akzeptiert."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS:
        draft = load_cut_plan_draft(project)
        if draft is None:
            raise ValueError("Kein Cut Plan Draft vorhanden.")
        new_document = build_validation_repair_requests_from_cut_plan(project, draft)
        save_cut_plan_validation_repair_requests(project, new_document)
        return f"{len(new_document.requests)} Validation Repair Request(s) erzeugt."

    if action_key == CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_VALIDATION_REPAIR:
        results = auto_resolve_all_validation_repair_requests(project)
        accepted = sum(1 for result in results if result.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED)
        return f"{len(results)} Request(s) bearbeitet, {accepted} automatisch repariert."

    raise ValueError(f"Unbekannte Workflow-Aktion: '{action_key}'.")


def _render_cut_plan_workflow_dashboard(project: Project) -> None:
    """Nutzervorgabe (Juli 2026): konsolidierte Checklist + EIN empfohlener
    nächster Schritt oben im Cut-Plan-Tab, damit der Nutzer nicht mehr
    selbst herausfinden muss, welcher der vielen Detail-Buttons weiter
    unten als nächstes relevant ist. Der primäre Button ruft für JEDEN
    Schritt dieselbe Funktion auf wie der jeweilige Detail-Bereich weiter
    unten (siehe _run_cut_plan_workflow_action) — keine Logik-Duplikation,
    die Detail-Bereiche bleiben für Feineinstellungen (Provider-Auswahl,
    einzelne Requests) vollständig erhalten."""
    st.subheader("📋 Cut-Plan Workflow")
    state = compute_cut_plan_workflow_state(project)

    for step in state.steps:
        icon = _WORKFLOW_STATUS_ICONS.get(step.status, "⚪")
        line = f"{icon} **{step.label}**"
        if step.summary:
            line += f" — {step.summary}"
        st.markdown(line)

    if state.all_done:
        st.success("✅ Cut Plan vollständig durchlaufen — aktuelle Validierung zeigt 0 Blocker.")
        return
    if state.has_unresolvable_blockers:
        st.error(
            f"🔴 {state.unresolvable_blocker_count} Blocker verbleiben, die durch keinen der obigen "
            "Automatik-Schritte abgedeckt sind. Bitte die Blocker-Tabelle weiter unten prüfen."
        )
        return
    if not state.next_step_id:
        return

    st.info(f"➡️ **Nächster empfohlener Schritt:** {state.next_action_label}\n\n{state.next_reason}")
    if not state.next_action_key:
        st.caption("Diesen Schritt weiter unten im entsprechenden Abschnitt ausführen.")
        return

    if st.button(
        state.next_action_label,
        key=f"cut_plan_workflow_next_action_{project.id}_{state.next_step_id}",
        type="primary",
    ):
        try:
            with st.spinner(f"{state.next_action_label}…"):
                message = _run_cut_plan_workflow_action(project, state.next_action_key)
            st.success(message)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def render_cut_plan_page() -> None:
    st.header("⑧ Cut Plan")
    _inject_new_feature_button_css()

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    st.info(
        "Dieser Schritt baut aus dem bestätigten Voice-over-Projektplan einen "
        "technischen Cut-Plan-Entwurf. Er erzeugt kein EditPlanDocument, keinen "
        "gesperrten Plan und keinen OTIO-Export. Der bestätigte Voice-over-"
        "Projektplan bleibt die redaktionelle Quelle der Wahrheit — der Cut Plan "
        "übersetzt ihn nur in eine technische Struktur, ohne redaktionell "
        "neu zu planen."
    )

    _render_cut_plan_workflow_dashboard(project)
    st.divider()

    _render_source_plan_status(project)
    st.divider()
    _render_settings_editor(project)
    st.divider()
    _render_future_artifact_paths(project)
    st.divider()

    source_plan = load_confirmed_voiceover_project_plan(project)
    existing_draft = load_cut_plan_draft(project)
    settings_stale = existing_draft is not None and is_cut_plan_settings_stale(project, existing_draft)

    col_generate, col_asset_selection, col_validate = st.columns(3)
    with col_generate:
        button_label = "Cut Plan Draft erzeugen" if existing_draft is None else "Cut Plan Draft neu erzeugen"
        if st.button(
            button_label,
            key=f"cut_plan_generate_draft_{project.id}",
            disabled=source_plan is None,
            type="primary",
            help="Baut Timeline-/Audio-Platzierung und Cut-Plan-Item-Skelette aus dem "
            "bestätigten Voice-over-Projektplan neu (Asset-Auswahl geht dabei verloren).",
        ):
            try:
                with st.spinner("Cut Plan Draft wird erstellt…"):
                    new_draft = build_cut_plan_draft(project)
                    save_cut_plan_draft(project, new_draft)
                st.success("Cut Plan Draft erstellt.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_asset_selection:
        if st.button(
            "Asset-Auswahl anwenden",
            key=f"cut_plan_apply_asset_selection_{project.id}",
            disabled=existing_draft is None or settings_stale,
            help="Wendet Asset-Auswahl, Fallback-Logik sowie Dauer-/Split-/Merge-"
            "Strategie auf den bestehenden Draft an (verwendet cut_plan.settings_snapshot). "
            "Noch keine Supplement-Suche, keine vollständige Validierung, kein Confirm/Lock."
            + (" Deaktiviert, da die Cut-Plan-Settings seit Draft-Erzeugung geändert wurden." if settings_stale else ""),
        ):
            try:
                with st.spinner("Asset-Auswahl wird angewendet…"):
                    updated_draft = apply_asset_selection_to_draft(project)
                status_counts = {
                    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_BACKUP_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED: 0,
                    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_BLOCKED: 0,
                }
                for item in updated_draft.items:
                    if item.asset_selection_status in status_counts:
                        status_counts[item.asset_selection_status] += 1
                total_segments = sum(len(item.planned_visual_segments) for item in updated_draft.items)
                st.success(
                    f"Asset-Auswahl angewendet: {len(updated_draft.items)} Items, "
                    f"{total_segments} VisualSegments, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_PRIMARY_USED]} PRIMARY_USED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_BACKUP_USED]} BACKUP_USED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED]} SUPPLEMENT_REQUIRED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_BLOCKED]} BLOCKED, "
                    f"{len(updated_draft.warnings)} Warnings, {len(updated_draft.blockers)} Blocker."
                )
                coverage_extended_count = sum(
                    1
                    for item in updated_draft.items
                    for segment in item.planned_visual_segments
                    if "initial_preroll_extension" in segment.reason.split("+")
                    or "section_pause_hold" in segment.reason.split("+")
                )
                if coverage_extended_count:
                    st.info(
                        "Visuelle Coverage für Start-Offset/Pausen wurde angewendet "
                        f"({coverage_extended_count} VisualSegment(s) verlängert, um Schwarzbild während "
                        "initial_audio_offset_sec/pause_between_sections_sec zu vermeiden)."
                    )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_validate:
        if st.button(
            "Cut Plan validieren",
            key=f"cut_plan_validate_{project.id}",
            disabled=existing_draft is None,
            help="Führt die vollständige Cut-Plan-Validierung durch (Phase 8.4) und "
            "speichert cut_plan.validation_report.json. Kein Confirm/Lock, kein OTIO.",
        ):
            try:
                with st.spinner("Cut Plan wird validiert…"):
                    validated_draft, report = validate_cut_plan_draft(project)
                if report.status == CUT_PLAN_VALIDATION_STATUS_PASS:
                    st.success(f"Validierung: PASS — Status {validated_draft.status}.")
                elif report.status == CUT_PLAN_VALIDATION_STATUS_WARNING:
                    st.warning(
                        f"Validierung: WARNING ({len(report.warnings)} Warnings) — Status {validated_draft.status}."
                    )
                else:
                    st.error(
                        f"Validierung: BLOCKED ({len(report.blockers)} Blocker) — Status {validated_draft.status}."
                    )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.caption(
        "🚧 Locked EditPlan, Render und OTIO-Export folgen erst in einer späteren Sub-Phase (9.2ff)."
    )

    if existing_draft is not None:
        st.divider()
        _render_supplement_requests(project, existing_draft)
        st.divider()
        _render_black_gap_manual_repair(project, existing_draft)
        st.divider()
        _render_residual_gap_requests(project, existing_draft)
        st.divider()
        _render_validation_repair(project, existing_draft)
        st.divider()
        _render_cut_plan_draft(project, existing_draft)
        st.divider()
        _render_confirmed_cut_plan(project, existing_draft)
        st.divider()
        _render_edit_plan_bridge(project)
        st.divider()
        _render_production_edit_plan_pipeline_overview(project)
        st.divider()
        _render_production_edit_plan_staging(project)
    elif source_plan is not None:
        st.info("Noch kein Cut Plan Draft vorhanden.")
    else:
        st.caption(
            f"Beim Klick würde standardmäßig hier gespeichert: "
            f"`{get_cut_plan_draft_path(project.work_dir_path)}`"
        )
