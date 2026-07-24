"""Schritt 7 Enhanced Cut Plan MVP — drei Aktionen hintereinander (R1)."""

from __future__ import annotations

import json
from pathlib import Path
import time

import streamlit as st

from otio_app.defaults import (
    ENHANCED_CUT_LLM_MODEL_CHOICES,
    ENHANCED_CUT_LLM_MODEL_LABELS,
    ENHANCED_FUNNEL_LLM_MODEL_CHOICES,
    ENHANCED_FUNNEL_LLM_MODEL_LABELS,
)
from otio_app.services.voiceover_generation.llm_pricing import (
    estimate_call_cost_usd,
    estimate_tokens_from_text,
    format_usd,
)
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    resolve_llm_model_id,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import LlmRoleSettings
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_CHOICES,
    CUT_PLAN_MODE_LEGACY,
    CUT_PLAN_MODE_UNIFIED,
    STILL_BACKGROUND_CHOICES,
    TIMING_MODE_CHOICES,
    TIMING_MODE_FIXED,
    TIMING_MODE_LLM,
    CutPlanOptions,
    load_cut_plan_options,
    save_cut_plan_options,
)  # TIMING_MODE_* used in settings UI labels/defaults
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    accept_supplement_candidates,
    generate_all_final_cuts,
    generate_all_rough_cuts,
    generate_unified_cut_plan,
    list_cut_plan_chapter_names,
    merge_and_persist_final_cuts,
    merge_and_persist_rough_cuts,
    resolve_unified_cut_plan_timeline,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.timing_error_summary import (
    classify_timing_errors,
    format_timing_error_overview,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    UnifiedTimelineError,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_job import (
    JobStatus as FunnelJobStatus,
    get_supplement_funnel_job_manager,
)
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    list_open_funnel_gap_ids,
)
from otio_app.ui.polling import poll_while_running
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    LocalMediaError,
    assign_local_media_path,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.manual_gap_assign_service import (
    ManualGapAssignError,
    assign_local_file_to_open_gap,
    gap_search_queries,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGapsDocument,
    FinalCutPlanDocument,
    GapMergeReport,
    NarrationTimelineDocument,
    ResolvedTimelineDocument,
    RoughCutPlanDocument,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.ui.without_voiceover_enhanced.timeline_view import render_realtime_timeline
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    IntroCutError,
    export_intro_otio,
    generate_intro_unified_cut,
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
    resolve_intro_timeline,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
    export_portable_otio_package,
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    final_cut_plan_path,
    gap_merge_report_path,
    narration_timeline_path,
    resolved_timeline_path,
    rough_cut_plan_path,
    script_locked_path,
    segment_timings_path,
    stock_search_results_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    PROVIDER_UI_LABELS,
    SUPPORTED_STOCK_PROVIDERS,
    load_stock_providers_config,
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
)
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
    render_llm_model_selectbox,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project

_ROUGH_CUT_OUTPUT_DEFAULT = 16_384
_FINAL_CUT_OUTPUT_DEFAULT = 16_384
_OUTPUT_TOKENS_MIN = 2_048
_OUTPUT_TOKENS_MAX = 65_536
_OUTPUT_TOKENS_STEP = 1_024
_STOCK_PASSAGE_LABEL_LEN = 110


def _gap_passage_map(coverage: CoverageGapsDocument | None) -> dict[str, str]:
    if coverage is None:
        return {}
    out: dict[str, str] = {}
    for gap in coverage.gaps:
        text = (
            (gap.needed_visual or "").strip()
            or (gap.subject or "").strip()
            or (gap.reason or "").strip()
        )
        out[gap.gap_id] = text
    return out


def _stock_candidate_checkbox_label(
    candidate: StockCandidate,
    gap_passages: dict[str, str],
    *,
    max_passage_len: int = _STOCK_PASSAGE_LABEL_LEN,
) -> str:
    """Vorschau: Textpassage + Gap statt Link/Titel-URL."""
    gap_id = (candidate.gap_id or "").strip()
    passage = (gap_passages.get(gap_id) or "").strip()
    if not passage:
        title = (candidate.title or "").strip()
        # URLs als Titel sind unbrauchbar — dann nur Gap zeigen.
        if title and not title.startswith(("http://", "https://")):
            passage = title
    if len(passage) > max_passage_len:
        passage = passage[: max_passage_len - 1].rstrip() + "…"
    gap_part = f"Gap {gap_id}" if gap_id else "Gap ?"
    passage_part = passage or "(keine Textpassage)"
    license_label = candidate.license or "unknown"
    return (
        f"{candidate.provider}: {passage_part} · {gap_part} "
        f"({candidate.media_type}, license={license_label})"
    )


def _estimate_path_tokens(path) -> int:
    if path is None or not path.is_file():
        return 0
    try:
        return estimate_tokens_from_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return 0


def _cut_chapter_count(project) -> int:
    locked = load_locked_script(project)
    if locked is None:
        return 1
    names = list_cut_plan_chapter_names(project, locked)
    return max(1, len(names))


def _estimate_rough_cut_input_tokens(project) -> tuple[int, int]:
    """Returns (tokens_per_chapter_estimate, chapter_count)."""
    chapters = _cut_chapter_count(project)
    whole = (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(segment_timings_path(project))
        + 3_000  # style + dramaturgy + assets overhead
    )
    per_chapter = max(400, whole // chapters)
    return per_chapter, chapters


def _estimate_final_cut_input_tokens(project) -> tuple[int, int]:
    chapters = _cut_chapter_count(project)
    whole = (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(rough_cut_plan_path(project))
        + _estimate_path_tokens(narration_timeline_path(project))
        + 4_000  # pauses + assets + supplements + style overhead
    )
    per_chapter = max(400, whole // chapters)
    return per_chapter, chapters


def _render_cost_caption(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_ceiling: int,
    chapter_count: int = 1,
) -> None:
    estimate = estimate_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens_ceiling=output_ceiling,
    )
    total_input = estimate.input_cost_usd * chapter_count
    total_output = estimate.output_ceiling_cost_usd * chapter_count
    total = estimate.total_ceiling_usd * chapter_count
    st.caption(
        f"**Kostenschätzung** ({estimate.price.label}) · "
        f"{chapter_count} Kapitel-Call(s): "
        f"Input ≈ {estimate.input_tokens:,} Tok/Kap. → "
        f"{format_usd(estimate.input_cost_usd)} × {chapter_count} = "
        f"{format_usd(total_input)} · "
        f"Output-Worst-Case {estimate.output_tokens_ceiling:,} Tok/Kap. → "
        f"{format_usd(total_output)} · "
        f"**Summe-Ceiling ≈ {format_usd(total)}**"
    )
    st.caption(
        "Hinweis: Abgerechnet werden nur tatsächlich erzeugte Tokens — "
        "nicht automatisch das volle Output-Limit. "
        "Lauf 2/3: ein LLM-Call pro Dramaturgie-Kapitel."
    )


def _render_enhanced_cut_model(
    project,
    *,
    role_attr: str,
    label: str,
    key_prefix: str,
    input_info: str,
    input_tokens: int,
    default_output_tokens: int,
    chapter_count: int = 1,
) -> tuple[str, str, int]:
    settings = load_model_settings(project)
    role_settings: LlmRoleSettings = getattr(settings, role_attr)
    with st.expander(f"⚙️ {label}", expanded=True):
        updated = render_llm_model_selectbox(
            label=label,
            role_settings=role_settings,
            key=f"{key_prefix}_model_{project.id}",
            input_info=input_info,
            options=ENHANCED_CUT_LLM_MODEL_CHOICES,
            labels=ENHANCED_CUT_LLM_MODEL_LABELS,
            show_estimated_costs=True,
        )
        if st.button("Modell speichern", key=f"{key_prefix}_model_save_{project.id}"):
            save_model_settings(
                project, settings.model_copy(update={role_attr: updated})
            )
            st.success(f"{label} gespeichert.")

        token_key = f"{key_prefix}_max_tokens_{project.id}"
        if token_key not in st.session_state:
            st.session_state[token_key] = default_output_tokens
        max_tokens = st.slider(
            "Max. Output-Tokens (Ceiling)",
            min_value=_OUTPUT_TOKENS_MIN,
            max_value=_OUTPUT_TOKENS_MAX,
            step=_OUTPUT_TOKENS_STEP,
            key=token_key,
            help=(
                "Obergrenze für die Antwortlänge pro Kapitel-Call. "
                "Du zahlst nur die tatsächlich erzeugten Output-Tokens — "
                "nicht automatisch das volle Limit."
            ),
        )
        _render_cost_caption(
            provider=updated.provider,
            model=updated.model,
            input_tokens=input_tokens,
            output_ceiling=int(max_tokens),
            chapter_count=chapter_count,
        )
    return updated.provider, updated.model, int(max_tokens)


def _render_lightweight_funnel_monitor(project) -> None:
    """Schlanke Seite während der Funnel läuft — Abbrechen ohne schweren Rerun."""
    mgr = get_supplement_funnel_job_manager()
    state = mgr.get_state(project.id)
    if state is None or state.status != FunnelJobStatus.RUNNING:
        return

    st.subheader("Supplement-Funnel läuft")
    st.progress(
        min(1.0, max(0.0, float(state.fraction))),
        text=(state.message or "Funnel läuft…")[:120],
    )
    st.info(state.message or "Funnel läuft im Hintergrund…")
    if state.model:
        st.caption(f"Modell: `{state.model}`")
    if state.gap_total:
        st.caption(
            f"Gap {state.gap_index}/{state.gap_total}"
            + (f" · `{state.gap_id}`" if state.gap_id else "")
        )

    if state.cancel_requested:
        st.warning(
            "Abbruch angefordert. Der aktuelle Gemini-/Download-Schritt "
            "(oft Thumbnail-Batch mit bis zu 10 Bildern) wird noch beendet — "
            "danach stoppt der Funnel. Bereits erfüllte Gaps bleiben."
        )
    else:
        st.caption(
            "Abbrechen wirkt nach dem laufenden LLM-/Download-Schritt, "
            "nicht mitten im API-Call."
        )

    cols = st.columns(2)
    with cols[0]:
        if st.button(
            "⏹ Funnel abbrechen",
            key=f"enh_funnel_cancel_lite_{project.id}",
            disabled=state.cancel_requested,
            type="primary",
        ):
            mgr.request_cancel(project.id)
            st.rerun()
    with cols[1]:
        if st.button(
            "🔄 Aktualisieren",
            key=f"enh_funnel_refresh_lite_{project.id}",
        ):
            st.rerun()

    if state.log_lines:
        with st.expander("Letzte Fortschrittszeilen", expanded=False):
            st.caption("\n".join(state.log_lines[-20:]))

    st.caption(
        "Leichte Ansicht während der Funnel läuft "
        "(Cut-Plan-Details ausgeblendet, damit Abbrechen schnell reagiert)."
    )
    # Kurzes Auto-Refresh, damit Stop ohne manuelles Klicken sichtbar wird.
    time.sleep(1.5)
    st.rerun()




_SECTION_ROUGH = "1 · Rough Cut (LLM 2)"
_SECTION_UNIFIED = "1 · Unified Cut (1 LLM)"
_SECTION_FUNNEL = "2 · Supplements / Funnel"
_SECTION_FINAL = "3 · Final Cut (LLM 3)"
_SECTION_OPTIONS_LEGACY = (_SECTION_ROUGH, _SECTION_FUNNEL, _SECTION_FINAL)
# Unified: kein LLM-3-Final — Timing/Merge laufen in Schritt 1 (Python).
_SECTION_OPTIONS_UNIFIED = (_SECTION_UNIFIED, _SECTION_FUNNEL)


def _json_mtime_count_cache(
    project_id: str, path: Path, *, list_key: str
) -> int:
    """Zählt Listeneinträge in JSON mit Session-Cache (mtime)."""
    if not path.is_file():
        return 0
    cache_key = f"_json_count_{project_id}_{path.name}_{list_key}"
    mtime = path.stat().st_mtime_ns
    cached = st.session_state.get(cache_key)
    if isinstance(cached, tuple) and cached[0] == mtime:
        return int(cached[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        st.session_state[cache_key] = (mtime, 0)
        return 0
    count = len(payload.get(list_key) or []) if isinstance(payload, dict) else 0
    st.session_state[cache_key] = (mtime, count)
    return int(count)


def _stock_candidate_count(project) -> int:
    return _json_mtime_count_cache(
        project.id, stock_search_results_path(project), list_key="candidates"
    )


def _accepted_count(project) -> int:
    return _json_mtime_count_cache(
        project.id, accepted_supplements_path(project), list_key="supplements"
    )


def _funnel_report_top_summary(project) -> dict | None:
    """Nur Top-Level-Felder — kein Pydantic über 2MB Candidate-Records."""
    path = supplement_funnel_report_path(project)
    if not path.is_file():
        return None
    cache_key = f"_funnel_top_{project.id}"
    mtime = path.stat().st_mtime_ns
    cached = st.session_state.get(cache_key)
    if isinstance(cached, tuple) and cached[0] == mtime:
        return cached[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        st.session_state[cache_key] = (mtime, None)
        return None
    if not isinstance(raw, dict):
        st.session_state[cache_key] = (mtime, None)
        return None
    summary = {
        "message": str(raw.get("message") or ""),
        "llm_model": str(raw.get("llm_model") or ""),
        "requested": len(raw.get("requested_gap_ids") or []),
        "filled": len(raw.get("filled_gap_ids") or []),
        "open": len(raw.get("open_gap_ids") or []),
        "downloads": int(raw.get("full_download_count") or 0),
        "invalid": int(raw.get("technically_invalid_count") or 0),
        "fallbacks": int(raw.get("fallback_used_count") or 0),
        "open_gap_ids": list(raw.get("open_gap_ids") or [])[:12],
    }
    st.session_state[cache_key] = (mtime, summary)
    return summary


def _default_cut_section(project) -> str:
    if stock_search_results_path(project).is_file():
        return _SECTION_FUNNEL
    if final_cut_plan_path(project).is_file():
        return _SECTION_FINAL
    return _SECTION_ROUGH


def _render_cut_plan_settings(project) -> CutPlanOptions:
    """Gemeinsame Settings für Lauf 2/3 + Python/OTIO (vor den Bereichen)."""
    current = load_cut_plan_options(project)
    with st.expander("Cut Plan Settings", expanded=False):
        st.caption(
            "Shot/Usage/Reuse/Vorlauf/Nachlauf/Toleranz → LLM 2+3 + Python. "
            "Head-Trim → nur Python. Titel + Still → OTIO (Titel-Einblendung folgt)."
        )
        cut_mode_labels = {
            CUT_PLAN_MODE_LEGACY: "Legacy (Rough + Final, 2 LLM-Läufe)",
            CUT_PLAN_MODE_UNIFIED: "Unified (1 LLM-Lauf + Python-Timing)",
        }
        mode_options = list(CUT_PLAN_MODE_CHOICES)
        mode_index = (
            mode_options.index(current.cut_plan_mode)
            if current.cut_plan_mode in mode_options
            else 0
        )
        cut_plan_mode = st.radio(
            "Cut-Plan-Modus",
            options=mode_options,
            format_func=lambda m: cut_mode_labels.get(m, m),
            index=mode_index,
            key=f"enh_opt_cut_mode_{project.id}",
            horizontal=True,
            help=(
                "Unified: ein Call/Kapitel → Timing sofort. "
                "Legacy: LLM 2 (Rough) + LLM 3 (Final)."
            ),
        )
        enable_unified_mini_repair = st.checkbox(
            "Unified Mini-Repair nach Gap-Merge (optional, Default aus)",
            value=bool(current.enable_unified_mini_repair),
            key=f"enh_opt_mini_repair_{project.id}",
            help=(
                "Nur wenn (offene none + Review) / Slots > Schwellwert. "
                "Repariert betroffene Slots ± Nachbarn."
            ),
            disabled=cut_plan_mode != CUT_PLAN_MODE_UNIFIED,
        )
        unified_mini_repair_threshold = st.number_input(
            "Mini-Repair-Schwellwert",
            min_value=0.0,
            max_value=1.0,
            value=float(current.unified_mini_repair_threshold),
            step=0.05,
            key=f"enh_opt_mini_repair_thr_{project.id}",
            disabled=cut_plan_mode != CUT_PLAN_MODE_UNIFIED
            or not enable_unified_mini_repair,
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            shot_min_sec = st.number_input(
                "Shot Min (s)",
                min_value=0.4,
                max_value=60.0,
                value=float(current.shot_min_sec),
                step=0.5,
                key=f"enh_opt_shot_min_{project.id}",
            )
            shot_max_sec = st.number_input(
                "Shot Max (s)",
                min_value=0.4,
                max_value=120.0,
                value=float(current.shot_max_sec),
                step=0.5,
                key=f"enh_opt_shot_max_{project.id}",
                help="Python kürzt längere Shots hart auf diesen Wert.",
            )
            video_head_trim_sec = st.number_input(
                "Video Head Trim (s)",
                min_value=0.0,
                max_value=10.0,
                value=float(current.video_head_trim_sec),
                step=0.1,
                key=f"enh_opt_head_trim_{project.id}",
                help="Nur Video — Anfang der Source-Range wird übersprungen.",
            )
            short_asset_tolerance_sec = st.number_input(
                "Toleranz Asset zu kurz (s)",
                min_value=0.0,
                max_value=30.0,
                value=float(current.short_asset_tolerance_sec),
                step=0.5,
                key=f"enh_opt_short_tol_{project.id}",
                help="Innerhalb dieser Unterlänge: Shot kürzen statt hart failen.",
            )
        with col2:
            max_asset_usage = st.number_input(
                "Max Asset Usage (Intro zählt nicht)",
                min_value=1,
                max_value=50,
                value=int(current.max_asset_usage),
                step=1,
                key=f"enh_opt_max_usage_{project.id}",
            )
            min_asset_reuse_distance_shots = st.number_input(
                "Asset-Reuse-Abstand (Shots)",
                min_value=0,
                max_value=100,
                value=int(current.min_asset_reuse_distance_shots),
                step=1,
                key=f"enh_opt_reuse_distance_{project.id}",
                help="Standard 4. Geht an LLM; Unterschreitung → Resolve-Hinweis.",
            )
            include_middle_frames = st.checkbox(
                "Mittel-Frames an LLM 2 (Vision)",
                value=bool(current.include_middle_frames),
                key=f"enh_opt_middle_frames_{project.id}",
            )
            max_middle_frames_per_chapter = st.number_input(
                "Max. Mittel-Frames / Kapitel",
                min_value=1,
                max_value=200,
                value=int(current.max_middle_frames_per_chapter),
                step=1,
                key=f"enh_opt_max_frames_{project.id}",
                disabled=not include_middle_frames,
            )
        with col3:
            max_candidates_per_gap = st.number_input(
                "Funnel: Max Kandidaten / Gap",
                min_value=1,
                max_value=20,
                value=int(current.max_candidates_per_gap),
                step=1,
                key=f"enh_opt_funnel_cands_{project.id}",
            )
            max_full_download_attempts_per_gap = st.number_input(
                "Funnel: Max Downloads / Gap",
                min_value=1,
                max_value=3,
                value=int(current.max_full_download_attempts_per_gap),
                step=1,
                key=f"enh_opt_funnel_dl_{project.id}",
            )

        st.markdown("##### Voice-over Vorlauf / Nachlauf (pro Kapitel)")
        mode_labels = {
            TIMING_MODE_FIXED: "Fest",
            TIMING_MODE_LLM: "LLM entscheidet (0…Max)",
        }
        v1, v2, v3, v4 = st.columns(4)
        with v1:
            voiceover_preroll_sec = st.number_input(
                "Vorlauf Max/Fest (s)",
                min_value=0.0,
                max_value=30.0,
                value=float(current.voiceover_preroll_sec),
                step=0.5,
                key=f"enh_opt_preroll_{project.id}",
                help="Bild vor Voice-over — gilt für jedes Kapitel/Folder erneut.",
            )
        with v2:
            preroll_modes = list(TIMING_MODE_CHOICES)
            voiceover_preroll_mode = st.selectbox(
                "Vorlauf-Modus",
                options=preroll_modes,
                index=preroll_modes.index(current.voiceover_preroll_mode)
                if current.voiceover_preroll_mode in preroll_modes
                else 0,
                format_func=lambda m: mode_labels.get(m, m),
                key=f"enh_opt_preroll_mode_{project.id}",
            )
        with v3:
            voiceover_postroll_sec = st.number_input(
                "Nachlauf Max/Fest (s)",
                min_value=0.0,
                max_value=60.0,
                value=float(current.voiceover_postroll_sec),
                step=0.5,
                key=f"enh_opt_postroll_{project.id}",
                help="Bild nach letztem Narrationsclip — gilt für jedes Kapitel/Folder erneut.",
            )
        with v4:
            postroll_modes = list(TIMING_MODE_CHOICES)
            voiceover_postroll_mode = st.selectbox(
                "Nachlauf-Modus",
                options=postroll_modes,
                index=postroll_modes.index(current.voiceover_postroll_mode)
                if current.voiceover_postroll_mode in postroll_modes
                else 0,
                format_func=lambda m: mode_labels.get(m, m),
                key=f"enh_opt_postroll_mode_{project.id}",
            )

        st.markdown("##### Ordner-Titel (OTIO)")
        folder_title_enabled = st.checkbox(
            "Ordner-Titel einblenden",
            value=bool(current.folder_title_enabled),
            key=f"enh_opt_folder_title_{project.id}",
            help="Gespeichert für den OTIO-Export; Einblendung wird noch verdrahtet.",
        )
        t1, t2, t3 = st.columns(3)
        with t1:
            folder_title_font = st.text_input(
                "Titel-Font",
                value=current.folder_title_font,
                key=f"enh_opt_title_font_{project.id}",
                disabled=not folder_title_enabled,
            )
        with t2:
            folder_title_duration_sec = st.number_input(
                "Titel-Dauer (s)",
                min_value=0.5,
                max_value=30.0,
                value=float(current.folder_title_duration_sec),
                step=0.5,
                key=f"enh_opt_title_dur_{project.id}",
                disabled=not folder_title_enabled,
            )
        with t3:
            folder_title_font_size = st.number_input(
                "Titel-Schriftgröße (0 = auto)",
                min_value=0.0,
                max_value=400.0,
                value=float(current.folder_title_font_size),
                step=1.0,
                key=f"enh_opt_title_size_{project.id}",
                disabled=not folder_title_enabled,
            )

        st.markdown("##### Still-Bilder (OTIO-Export)")
        still_image_style_enabled = st.checkbox(
            "Still-Style aktiv (Background + Skalierung)",
            value=bool(current.still_image_style_enabled),
            key=f"enh_opt_still_style_{project.id}",
        )
        s1, s2 = st.columns(2)
        with s1:
            still_image_zoom = st.number_input(
                "Still Zoom (fit-Anteil)",
                min_value=0.05,
                max_value=1.0,
                value=float(current.still_image_zoom),
                step=0.05,
                key=f"enh_opt_still_zoom_{project.id}",
                disabled=not still_image_style_enabled,
            )
        with s2:
            bg_options = list(STILL_BACKGROUND_CHOICES)
            bg_index = (
                bg_options.index(current.still_image_background_style)
                if current.still_image_background_style in bg_options
                else 0
            )
            still_image_background_style = st.selectbox(
                "Still Background",
                options=bg_options,
                index=bg_index,
                key=f"enh_opt_still_bg_{project.id}",
                disabled=not still_image_style_enabled,
            )

        draft = CutPlanOptions(
            schema_version="1.3",
            cut_plan_mode=str(cut_plan_mode),  # type: ignore[arg-type]
            enable_unified_mini_repair=bool(enable_unified_mini_repair),
            unified_mini_repair_threshold=float(unified_mini_repair_threshold),
            include_middle_frames=bool(include_middle_frames),
            max_middle_frames_per_chapter=int(max_middle_frames_per_chapter),
            max_candidates_per_gap=int(max_candidates_per_gap),
            max_full_download_attempts_per_gap=int(
                max_full_download_attempts_per_gap
            ),
            shot_min_sec=float(shot_min_sec),
            shot_max_sec=float(shot_max_sec),
            video_head_trim_sec=float(video_head_trim_sec),
            max_asset_usage=int(max_asset_usage),
            min_asset_reuse_distance_shots=int(min_asset_reuse_distance_shots),
            voiceover_preroll_sec=float(voiceover_preroll_sec),
            voiceover_preroll_mode=str(voiceover_preroll_mode),  # type: ignore[arg-type]
            voiceover_postroll_sec=float(voiceover_postroll_sec),
            voiceover_postroll_mode=str(voiceover_postroll_mode),  # type: ignore[arg-type]
            short_asset_tolerance_sec=float(short_asset_tolerance_sec),
            folder_title_enabled=bool(folder_title_enabled),
            folder_title_font=str(folder_title_font or current.folder_title_font),
            folder_title_duration_sec=float(folder_title_duration_sec),
            folder_title_font_size=float(folder_title_font_size),
            still_image_style_enabled=bool(still_image_style_enabled),
            still_image_zoom=float(still_image_zoom),
            still_image_background_style=str(still_image_background_style),
        )
        if st.button(
            "Cut Plan Settings speichern",
            key=f"enh_opt_save_{project.id}",
            type="primary",
        ):
            saved = save_cut_plan_options(project, draft)
            st.success(
                f"Gespeichert: mode={saved.cut_plan_mode} · "
                f"shot {saved.shot_min_sec}–{saved.shot_max_sec}s · "
                f"reuse≥{saved.min_asset_reuse_distance_shots} · "
                f"preroll {saved.voiceover_preroll_sec}s/{saved.voiceover_preroll_mode} · "
                f"postroll {saved.voiceover_postroll_sec}s/{saved.voiceover_postroll_mode}"
            )
            st.rerun()
        # Live-Modus sofort für Bereichs-Radio nutzen (Persistenz erst beim Speichern).
        return draft


def _render_timing_error_summary(messages) -> None:
    """Gruppierte, verständliche Timing-Fehler statt Roh-Blob."""
    groups = classify_timing_errors(messages)
    if not groups:
        return
    overview = format_timing_error_overview(messages)
    st.error(
        "**Python-Timing: Probleme gefunden** "
        "(LLM-Plan bleibt erhalten).\n\n"
        f"{overview}"
    )
    st.caption(
        "Kurz: Das LLM plant redaktionell ohne exakte Sekunden. "
        "Python prüft danach echte Clip-Dauern gegen die Narration — "
        "zu kurze Clips sind Planungs-/Dauer-Konflikte, kein falsches Datei-Mapping."
    )
    for group in groups:
        st.markdown(f"**{group.title}** · {len(group.items)}")
        st.caption(group.explanation)
        st.caption(f"Nächster Schritt: {group.next_step}")
        with st.expander(f"Details ({len(group.items)})", expanded=len(groups) == 1):
            for item in group.items[:40]:
                st.markdown(f"- {item}")
            if len(group.items) > 40:
                st.caption(f"… +{len(group.items) - 40} weitere")


def _render_slim_status(project) -> None:
    """Zeigt, ob vorhandene ``{folder}.slim.json`` für den LLM-Prompt bereitliegen."""
    from otio_app.project_layout import get_folder_inventory_path
    from otio_app.services.inventory_prompt_view import (
        load_slim_folder_inventory_file,
        slim_inventory_path_for,
    )

    folders = [f for f in (project.selected_asset_subdirs or []) if f]
    if not folders:
        st.caption("Keine Kapitel-Ordner ausgewählt.")
        return

    ready = 0
    total_assets = 0
    missing: list[str] = []
    for folder in folders:
        slim_path = slim_inventory_path_for(
            get_folder_inventory_path(project.work_dir_path, folder)
        )
        doc = load_slim_folder_inventory_file(slim_path)
        if doc is None:
            missing.append(folder)
            continue
        ready += 1
        total_assets += len(doc.get("assets") or [])

    if missing:
        shown = ", ".join(missing[:8])
        more = "…" if len(missing) > 8 else ""
        st.warning(
            f"Slim-Inventar fehlt für {len(missing)} Kapitel: {shown}{more}. "
            "Erwartet: `inventory/{folder}.slim.json` (wird vom LLM-Prompt geladen)."
        )
    else:
        st.caption(
            f"Slim-Inventar bereit: {ready}/{len(folders)} Kapitel · "
            f"{total_assets} Assets für den LLM-Prompt."
        )


def _render_intro_cut_section(project, *, provider: str, model: str) -> None:
    """Separater Intro-Pfad vor den Kapitel-Unified-Buttons."""
    st.markdown("##### 0. Intro Cut (separat)")
    st.caption(
        "Nur Intro: alle Kapitel-Inventare gebündelt · nur asset_fit=strong · "
        "Opening 4s vor VO · Closing 5–8s nach VO · eigene OTIO. "
        "Kapitel-Unified-Buttons lassen Intro unverändert."
    )
    intro_basename = st.text_input(
        "Intro-OTIO Dateiname",
        value=f"{project.name}_intro",
        key=f"enh_intro_otio_basename_{project.id}",
    )
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        run_intro_llm = st.button(
            "Intro: LLM Schnitt",
            type="primary",
            key=f"enh_intro_cut_llm_{project.id}",
            use_container_width=True,
        )
    with col_b:
        run_intro_timing = st.button(
            "Intro: Python Timing",
            key=f"enh_intro_cut_resolve_{project.id}",
            use_container_width=True,
        )
    with col_c:
        run_intro_otio = st.button(
            "Intro: OTIO exportieren",
            key=f"enh_intro_cut_otio_{project.id}",
            use_container_width=True,
        )

    if run_intro_llm:
        try:
            with st.spinner("Intro Unified-LLM…"):
                result = generate_intro_unified_cut(
                    project, provider=provider, model=model
                )
            st.success(
                f"Intro-Cut: {result.slot_count} Slots · "
                f"{result.gap_count} none-Gaps (strong-only) · "
                f"Inventar {result.bundled_inventory.get('asset_count', 0)} Assets / "
                f"{result.bundled_inventory.get('chapter_count', 0)} Kapitel."
            )
            if result.gap_count:
                st.warning(
                    "Intro-Gaps → Supplement-Funnel. Danach Intro-LLM/Timing erneut."
                )
            st.rerun()
        except IntroCutError as exc:
            st.error(str(exc))
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Intro-LLM-Fehler: {exc}")

    if run_intro_timing:
        try:
            with st.spinner("Intro Python-Timing…"):
                resolved = resolve_intro_timeline(
                    project, provider=provider, model=model
                )
            st.success(
                f"Intro-Timeline: {resolved.total_duration_seconds:.2f}s · "
                f"{len(resolved.shots)} Shots · "
                f"{len(resolved.audio_segments)} Audio "
                "(Opening 4s + Closing-Hold)."
            )
            if resolved.repairs:
                with st.expander("Intro Repair-Log", expanded=False):
                    for note in resolved.repairs[:40]:
                        st.caption(note)
            st.rerun()
        except IntroCutError as exc:
            st.error(str(exc))
        except UnifiedTimelineError as exc:
            _render_timing_error_summary(exc.errors or exc)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Intro-Timing-Fehler: {exc}")

    if run_intro_otio:
        try:
            with st.spinner("Intro-OTIO…"):
                path = export_intro_otio(
                    project,
                    basename=(intro_basename or "").strip() or "enhanced_intro",
                )
            st.success(f"Intro-OTIO geschrieben: `{path}`")
        except EnhancedOtioExportError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Intro-OTIO-Fehler: {exc}")

    intro_plan = load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)
    intro_resolved = load_model(
        intro_resolved_timeline_path(project), ResolvedTimelineDocument
    )
    if intro_plan is not None:
        st.caption(
            f"Intro-Plan: {len(intro_plan.slots)} Slots · "
            f"Resolved: {'ja' if intro_resolved is not None else 'nein'}"
        )
    st.divider()


def _render_section_unified(project) -> None:
    st.subheader("1. Unified Cut Plan (LLM) → 2. Python Timing")
    _render_slim_status(project)
    chapters = list_cut_plan_chapter_names(project)
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        is_intro_folder_name,
    )

    body_chapters = [c for c in chapters if not is_intro_folder_name(c)]
    chapter_count = max(1, len(body_chapters))
    rough_provider, rough_model, _rough_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_rough_cut",
        label="Modell (Unified Cut)",
        key_prefix="enh_unified",
        input_info=LLM_INPUT_INFO.get("enhanced_rough_cut", ""),
        input_tokens=_estimate_rough_cut_input_tokens(project)[0],
        default_output_tokens=_ROUGH_CUT_OUTPUT_DEFAULT,
        chapter_count=chapter_count,
    )
    _render_intro_cut_section(
        project, provider=rough_provider, model=rough_model
    )
    st.caption(
        f"Kapitel Schritt 1: **ein LLM-Call pro Kapitel** "
        f"({chapter_count} Kapitel, ohne Intro) → "
        "`unified_cut_plan.json`. Schritt 2: Python-Timing getrennt starten "
        "(kein erneuter LLM-Call)."
    )
    cut_options = load_cut_plan_options(project)
    if cut_options.include_middle_frames:
        st.caption(
            "Vision aktiv: Mittel-Frames "
            f"(max. {cut_options.max_middle_frames_per_chapter}/Kapitel)."
        )

    col_llm, col_timing = st.columns(2)
    with col_llm:
        run_llm = st.button(
            "1 · Unified Cut Plan erzeugen (LLM)",
            type="primary",
            key="enh_unified_cut_llm",
            use_container_width=True,
        )
    with col_timing:
        run_timing = st.button(
            "2 · Python Timing auflösen",
            key="enh_unified_cut_timing",
            use_container_width=True,
            help=(
                "Liest den gespeicherten Unified Cut Plan und erzeugt die "
                "aufgelöste Timeline. Kein LLM-Call."
            ),
        )

    if run_llm:
        try:
            progress = st.empty()

            def _unified_progress(folder_name: str, index: int, total: int) -> None:
                progress.info(
                    f"Unified LLM · Kapitel {index}/{total}: „{folder_name}“ "
                    f"({resolve_llm_model_id(rough_provider, rough_model)})…"
                )

            with st.spinner("Unified Cut Plan (nur LLM)…"):
                plan = generate_unified_cut_plan(
                    project,
                    provider=rough_provider,
                    model=rough_model,
                    progress_callback=_unified_progress,
                )
            progress.empty()
            gaps = sum(
                1 for s in plan.slots if str(s.asset_fit) in {"weak", "none"}
            )
            st.success(
                f"LLM-Plan gespeichert: {len(plan.slots)} Slots · "
                f"{len(plan.boundaries)} Grenzen · "
                f"{gaps} weak/none Gaps. "
                "Als Nächstes „Python Timing auflösen“."
            )
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"LLM-Fehler: {exc}")

    if run_timing:
        try:
            with st.spinner("Python Timing…"):
                plan, resolved, merge_report = resolve_unified_cut_plan_timeline(
                    project,
                    run_gap_merge=True,
                    provider=rough_provider,
                    model=rough_model,
                )
            st.success(
                f"Timing ok: {len(resolved.shots)} Shots · "
                f"{resolved.total_duration_seconds:.1f}s · "
                f"{len(plan.slots)} Slots im Plan."
            )
            if resolved.repairs:
                with st.expander(
                    f"Repairs / Hinweise ({len(resolved.repairs)})", expanded=False
                ):
                    for note in resolved.repairs[:40]:
                        st.caption(note)
            if merge_report is not None:
                st.info(merge_report.message or "Gap-Merge ausgeführt.")
                if merge_report.open_none_gap_ids:
                    st.warning(
                        "Offene none-Gaps: "
                        + ", ".join(merge_report.open_none_gap_ids)
                    )
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except UnifiedTimelineError as exc:
            _render_timing_error_summary(exc.errors or exc)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Timing-Fehler: {exc}")

    plan = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if plan is not None:
        st.caption(
            f"Gespeicherter Unified Plan: {len(plan.slots)} Slots · "
            f"{len(plan.pause_directives)} Pausen."
        )
        for slot in plan.slots[:40]:
            st.caption(
                f"{slot.slot_id}: fit={slot.asset_fit} · "
                f"asset={slot.local_asset_id or '—'} · "
                f"gap={slot.coverage_gap_id or '—'}"
            )
        if len(plan.slots) > 40:
            st.caption(f"… +{len(plan.slots) - 40} weitere Slots")
    else:
        st.caption("Noch kein Unified Cut Plan — zuerst Schritt 1 (LLM).")

    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is not None:
        st.caption(
            f"Aufgelöste Timeline: {len(resolved.shots)} Shots · "
            f"{resolved.total_duration_seconds:.1f}s · "
            f"{len(resolved.errors)} Fehler · {len(resolved.repairs)} Repairs."
        )
        if resolved.errors:
            _render_timing_error_summary(resolved.errors)
        elif resolved.repairs:
            short_repairs = [
                r for r in resolved.repairs if "zu kurz" in r.lower() or "als Gap" in r
            ]
            if short_repairs:
                st.info(
                    f"{len(short_repairs)} Slot(s) als Gap markiert "
                    "(Asset zu kurz für Narrationsdauer) — im Funnel ersetzen."
                )

    merge_report = load_model(gap_merge_report_path(project), GapMergeReport)
    if merge_report is not None:
        st.markdown("##### Gap-Merge Status")
        st.caption(merge_report.message or "")
        cols = st.columns(4)
        cols[0].metric("Merged", len(merge_report.merged_shot_ids))
        cols[1].metric("Weak behalten", len(merge_report.kept_local_shot_ids))
        cols[2].metric("none offen", len(merge_report.open_none_gap_ids))
        cols[3].metric("Review", len(merge_report.review_shot_ids))

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is not None and coverage.gaps:
        st.caption(f"Coverage Gaps (Funnel): {len(coverage.gaps)}")


def _render_section_rough(project) -> None:
    st.subheader("1. Groben Cut Plan und Pausen erzeugen")
    _render_slim_status(project)
    rough_tokens, rough_chapters = _estimate_rough_cut_input_tokens(project)
    rough_provider, rough_model, _rough_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_rough_cut",
        label="Modell (LLM-Lauf 2)",
        key_prefix="enh_rough",
        input_info=LLM_INPUT_INFO["enhanced_rough_cut"],
        input_tokens=rough_tokens,
        default_output_tokens=_ROUGH_CUT_OUTPUT_DEFAULT,
        chapter_count=rough_chapters,
    )
    st.caption(
        f"Lauf 2 läuft sequenziell: **ein LLM-Call pro Kapitel** "
        f"({rough_chapters} Kapitel)."
    )
    cut_options = load_cut_plan_options(project)
    if cut_options.include_middle_frames:
        st.caption(
            "Vision aktiv (Cut Plan Settings): Mittel-Frames an LLM 2 "
            f"(max. {cut_options.max_middle_frames_per_chapter}/Kapitel)."
        )
    else:
        st.caption(
            "Vision aus — Mittel-Frames unter „Cut Plan Settings“ aktivierbar."
        )
    if st.button("LLM-Lauf 2 starten", type="primary", key="enh_rough_cut"):
        try:
            progress = st.empty()

            def _rough_progress(folder_name: str, index: int, total: int) -> None:
                progress.info(
                    f"LLM-Lauf 2 · Kapitel {index}/{total}: „{folder_name}“ "
                    f"({resolve_llm_model_id(rough_provider, rough_model)})…"
                )

            with st.spinner("Pausen + grober Cut — Kapitel nacheinander…"):
                results = generate_all_rough_cuts(
                    project,
                    provider=rough_provider,
                    model=rough_model,
                    progress_callback=_rough_progress,
                )
                rough, coverage = merge_and_persist_rough_cuts(project, results)
            progress.empty()
            ok = [r for r in results if r.status == "PASS"]
            fail = [r for r in results if r.status != "PASS"]
            st.success(
                f"{len(ok)}/{len(results)} Kapitel · {len(rough.shots)} Shots · "
                f"{len(rough.pause_directives)} Pausen · "
                f"{len(coverage.gaps)} Coverage Gaps."
            )
            for result in fail:
                st.error(f"„{result.folder_name}“: {result.error}")
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    show_timeline_key = f"enh_show_timeline_{project.id}"
    st.checkbox(
        "Echtzeit-Timeline laden",
        key=show_timeline_key,
        help="Standard aus — große HTML-Timeline nur bei Bedarf.",
    )
    if st.session_state.get(show_timeline_key):
        rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
        timeline = load_model(
            narration_timeline_path(project), NarrationTimelineDocument
        )
        final_preview = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
        resolved = load_model(
            resolved_timeline_path(project), ResolvedTimelineDocument
        )
        render_realtime_timeline(
            narration_timeline=timeline,
            rough=rough,
            final=final_preview,
            resolved=resolved,
        )

    show_rough_key = f"enh_show_rough_details_{project.id}"
    rough_meta = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    if rough_meta is not None:
        st.checkbox(
            (
                f"Rough-Cut Details laden · {len(rough_meta.shots)} Shots · "
                f"{len(rough_meta.pause_directives)} Pausen"
            ),
            key=show_rough_key,
        )
        if st.session_state.get(show_rough_key):
            for shot in rough_meta.shots:
                start = shot.start_anchor
                end = shot.end_anchor
                start_label = (
                    f"pause after {start.after_segment_id}@{start.position}"
                    if start.type == "pause"
                    else f"{start.segment_id}@{start.position}"
                )
                end_label = (
                    f"pause after {end.after_segment_id}@{end.position}"
                    if end.type == "pause"
                    else f"{end.segment_id}@{end.position}"
                )
                st.caption(
                    f"{shot.shot_id}: {start_label}→{end_label} · "
                    f"asset={shot.local_asset_id or shot.asset_id} · "
                    f"fit={shot.asset_fit}"
                )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is not None and coverage.gaps:
        gap_status = summarize_gap_status(project)
        filled_ids = set(gap_status.filled_gap_ids)
        st.info(
            f"Gaps: **offen {gap_status.open_count}** · "
            f"**erfüllt {gap_status.filled_count}** · "
            f"**gesamt {gap_status.total}**"
        )
        if gap_status.message:
            st.caption(gap_status.message)
        show_gaps_key = f"enh_show_coverage_gaps_{project.id}"
        st.checkbox(
            f"Coverage-Gap-Liste laden ({gap_status.total})",
            key=show_gaps_key,
        )
        if st.session_state.get(show_gaps_key):
            for gap in coverage.gaps:
                queries = gap.search_concepts or gap.search_queries
                status = "offen" if gap.gap_id not in filled_ids else "erfüllt"
                st.caption(
                    f"{gap.gap_id}: {gap.needed_visual or gap.subject} · "
                    f"Status: {status} · queries={queries}"
                )


def _render_section_funnel(project) -> None:
    st.subheader("2. Supplements suchen und auswählen")

    st.markdown("**Stockanbieter verwenden:**")
    config = load_stock_providers_config(project)
    enabled_draft: dict[str, bool] = {}
    cols = st.columns(len(SUPPORTED_STOCK_PROVIDERS))
    for index, provider_name in enumerate(SUPPORTED_STOCK_PROVIDERS):
        current = config.providers[provider_name].enabled
        widget_key = f"enh_provider_{project.id}_{provider_name}"
        if widget_key not in st.session_state:
            st.session_state[widget_key] = current
        with cols[index]:
            enabled_draft[provider_name] = st.checkbox(
                PROVIDER_UI_LABELS[provider_name],
                key=widget_key,
            )
    if st.button("Anbieterauswahl speichern", key="enh_save_providers"):
        saved = save_stock_providers_config(project, enabled_draft)
        st.success(
            "Anbieterauswahl gespeichert: "
            + ", ".join(
                f"{PROVIDER_UI_LABELS[n]}="
                f"{'an' if saved.providers[n].enabled else 'aus'}"
                for n in SUPPORTED_STOCK_PROVIDERS
            )
        )
        st.rerun()

    if st.button("Stock suchen", key="enh_stock_search"):
        try:
            progress_bar = st.progress(0.0, text="Stocksuche startet…")
            status_box = st.empty()

            def _search_progress(fraction: float, message: str) -> None:
                progress_bar.progress(
                    min(1.0, max(0.0, float(fraction))),
                    text=(message or "Stocksuche…")[:120],
                )
                status_box.info(message)

            results = search_supplements_for_gaps(
                project,
                progress_callback=_search_progress,
            )
            # Count-/Status-Caches aktualisieren (ohne späteren Full-Parse).
            st.session_state[
                f"_json_count_{project.id}_"
                f"{stock_search_results_path(project).name}_candidates"
            ] = (
                stock_search_results_path(project).stat().st_mtime_ns,
                len(results.candidates),
            )
            st.session_state[f"_stock_provider_status_{project.id}"] = dict(
                results.provider_status or {}
            )
            progress_bar.progress(
                1.0,
                text=f"{len(results.candidates)} Kandidaten gefunden",
            )
            status_box.success(f"{len(results.candidates)} Kandidaten gefunden.")
            if results.message:
                st.warning(results.message)
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    stock_path = stock_search_results_path(project)
    has_stock = stock_path.is_file()
    candidate_count = _stock_candidate_count(project) if has_stock else 0
    # Provider-Status nur aus Cache / leichter Teil — nicht die ganze Stock-JSON.
    provider_status = st.session_state.get(f"_stock_provider_status_{project.id}")
    if isinstance(provider_status, dict) and provider_status:
        st.caption(
            "Provider-Status: "
            + ", ".join(f"{k}={v}" for k, v in provider_status.items())
        )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    # Fix 4: Run-ID-aware Zähler (weak offen bis Merge; stale Funnel ignorieren).
    gap_status = summarize_gap_status(project)
    open_gap_ids = list(gap_status.open_gap_ids)
    open_gaps_count = gap_status.open_count
    filled_gaps_count = gap_status.filled_count
    total_gaps = gap_status.total if coverage is not None else 0

    st.markdown("**Coverage Gaps automatisch auflösen**")
    st.caption(
        f"Aktuell: offen **{open_gaps_count}** · "
        f"erfüllt **{filled_gaps_count}** · "
        f"gesamt **{total_gaps}**"
    )
    if gap_status.message:
        st.caption(gap_status.message)

    funnel_settings = load_model_settings(project)
    funnel_role = funnel_settings.enhanced_supplement_funnel
    with st.expander("⚙️ Funnel-Modell (Text + Thumbnail)", expanded=False):
        funnel_updated = render_llm_model_selectbox(
            label="Funnel-Modell",
            role_settings=funnel_role,
            key=f"enh_funnel_model_{project.id}",
            input_info=LLM_INPUT_INFO["enhanced_supplement_funnel"],
            options=ENHANCED_FUNNEL_LLM_MODEL_CHOICES,
            labels=ENHANCED_FUNNEL_LLM_MODEL_LABELS,
            show_estimated_costs=True,
        )
        if st.button(
            "Funnel-Modell speichern",
            key=f"enh_funnel_model_save_{project.id}",
        ):
            save_model_settings(
                project,
                funnel_settings.model_copy(
                    update={"enhanced_supplement_funnel": funnel_updated}
                ),
            )
            st.success("Funnel-Modell gespeichert.")
        st.caption(
            f"Aktiv: **{funnel_updated.model}** · "
            "Für günstige Tests: Gemini 3.1 Flash Lite."
        )
    funnel_model_id = funnel_updated.model

    gap_by_id = {g.gap_id: g for g in (coverage.gaps if coverage else [])}
    select_key = f"enh_funnel_gap_multiselect_{project.id}"
    for gap_id in list(gap_by_id):
        legacy_key = f"enh_funnel_gap_select_{project.id}_{gap_id}"
        if legacy_key in st.session_state:
            del st.session_state[legacy_key]

    selected_open_ids: list[str] = []
    if open_gap_ids:
        pending_deselect_key = f"enh_funnel_pending_deselect_{project.id}"
        pending_filled = st.session_state.pop(pending_deselect_key, None)
        if pending_filled and select_key in st.session_state:
            filled = set(pending_filled)
            current_sel = st.session_state.get(select_key) or []
            if isinstance(current_sel, list):
                st.session_state[select_key] = [
                    gid for gid in current_sel if gid not in filled
                ]

        def _format_open_gap(gap_id: str) -> str:
            gap = gap_by_id.get(gap_id)
            visual = ""
            if gap is not None:
                visual = (gap.needed_visual or gap.subject or "").strip()
            visual = visual or "—"
            if len(visual) > 80:
                visual = visual[:77] + "…"
            return f"{gap_id} · {visual}"

        show_pills_key = f"enh_show_open_gap_pills_{project.id}"
        st.checkbox(
            f"Offene Coverage Gaps auswählen laden · {len(open_gap_ids)}",
            key=show_pills_key,
            help="Pills nur bei Bedarf — sonst schnellerer Rerun.",
        )
        if st.session_state.get(show_pills_key):
            selected_raw = st.pills(
                "Offene Coverage Gaps auswählen",
                options=open_gap_ids,
                selection_mode="multi",
                format_func=_format_open_gap,
                key=select_key,
                help="Nur offene Gaps. Erfüllte Gaps erscheinen nicht.",
                label_visibility="collapsed",
            )
            open_set = set(open_gap_ids)
            if isinstance(selected_raw, list):
                selected_open_ids = [gid for gid in selected_raw if gid in open_set]
            elif selected_raw and str(selected_raw) in open_set:
                selected_open_ids = [str(selected_raw)]
            else:
                selected_open_ids = []
            if selected_open_ids:
                st.caption(
                    "Ausgewählt: " + ", ".join(selected_open_ids[:12])
                    + ("…" if len(selected_open_ids) > 12 else "")
                )
        else:
            st.caption(
                f"Gap-Auswahl ausgeblendet ({len(open_gap_ids)} offen). "
                "Checkbox aktivieren zum Auswählen einzelner Gaps."
            )
    else:
        st.info("Keine offenen Coverage Gaps.")
        if select_key in st.session_state:
            del st.session_state[select_key]

    funnel_job_mgr = get_supplement_funnel_job_manager()
    funnel_running = funnel_job_mgr.is_running(project.id)

    def _start_funnel_job(gap_ids: list[str]) -> None:
        started = funnel_job_mgr.start(
            project,
            gap_ids=gap_ids,
            model=funnel_model_id,
        )
        if not started:
            st.warning("Funnel läuft bereits — bitte Abbrechen oder warten.")
        st.rerun()

    def _render_funnel_job_panel() -> None:
        state = funnel_job_mgr.get_state(project.id)
        if state is None:
            return
        if state.status == FunnelJobStatus.RUNNING:
            st.progress(
                min(1.0, max(0.0, float(state.fraction))),
                text=(state.message or "Funnel läuft…")[:120],
            )
            st.info(state.message or "Funnel läuft im Hintergrund…")
            if state.cancel_requested:
                st.warning(
                    "Abbruch angefordert — aktueller LLM-/Download-Schritt "
                    "wird noch beendet, danach stoppt der Funnel."
                )
            if st.button(
                "⏹ Funnel abbrechen",
                key=f"enh_funnel_cancel_{project.id}",
                disabled=state.cancel_requested,
                type="primary",
            ):
                funnel_job_mgr.request_cancel(project.id)
                st.rerun()
            return

        if state.status == FunnelJobStatus.CANCELLED:
            st.warning(
                state.message
                or "Funnel abgebrochen. Bereits erfüllte Gaps bleiben erhalten."
            )
        elif state.status == FunnelJobStatus.FAILED:
            st.error(state.error or "Funnel fehlgeschlagen.")
        elif state.status == FunnelJobStatus.COMPLETED:
            st.success(state.message or "Funnel abgeschlossen.")

        if state.report is not None:
            st.session_state[f"enh_funnel_pending_deselect_{project.id}"] = list(
                state.report.filled_gap_ids or []
            )
        if st.button(
            "Hinweis schließen",
            key=f"enh_funnel_dismiss_{project.id}",
        ):
            funnel_job_mgr.dismiss(project.id)
            st.rerun()

    funnel_state = funnel_job_mgr.get_state(project.id)
    if funnel_state is not None and funnel_state.status == FunnelJobStatus.RUNNING:
        poll_while_running(
            _render_funnel_job_panel,
            lambda: funnel_job_mgr.is_running(project.id),
            refresh_key=f"enh_funnel_poll_{project.id}",
        )
    elif funnel_state is not None:
        _render_funnel_job_panel()

    cols_funnel = st.columns(2)
    with cols_funnel[0]:
        all_disabled = (not open_gap_ids) or funnel_running
        if st.button(
            "Alle offenen Gaps automatisch auflösen",
            type="primary",
            key="enh_funnel_all_open",
            disabled=all_disabled,
            help=(
                "Verarbeitet alle aktuell offenen Coverage Gaps sequenziell. "
                "Mehrfachauswahl wird ignoriert. Läuft im Hintergrund — "
                "Abbrechen möglich."
            ),
        ):
            # Service-Liste erst beim Start (lädt Report) — nicht bei jedem Rerun.
            _start_funnel_job(list_open_funnel_gap_ids(project))
    with cols_funnel[1]:
        selected_disabled = (not selected_open_ids) or funnel_running
        if st.button(
            "Ausgewählte Gaps automatisch auflösen",
            key="enh_funnel_selected",
            disabled=selected_disabled,
            help=(
                "Verarbeitet nur ausgewählte offene Gaps. "
                "Gleicher Funnel-Service wie „Alle“. Läuft im Hintergrund."
            ),
        ):
            current_open = set(list_open_funnel_gap_ids(project))
            valid_selected = [
                gid for gid in selected_open_ids if gid in current_open
            ]
            if not valid_selected:
                st.warning("Keine gültige Gap-Auswahl.")
            else:
                _start_funnel_job(valid_selected)

    st.markdown("**Offene Gaps manuell zuordnen**")
    st.caption(
        "Links Gap · Mitte Search-Queries (kopierbar) · "
        "Rechts lokaler Dateipfad → wird nach stock/downloads kopiert, "
        "export_ready gesetzt und inventarisiert."
    )
    show_manual_gaps_key = f"enh_show_manual_gap_assign_{project.id}"
    st.checkbox(
        f"Offene Gaps manuell zuordnen laden ({open_gaps_count})",
        key=show_manual_gaps_key,
        help=(
            "Zeigt offene Gaps mit Search-Queries und Pfadfeld. "
            "Nur bei Bedarf — sonst schnellerer Rerun."
        ),
        disabled=not open_gap_ids,
    )
    if open_gap_ids and st.session_state.get(show_manual_gaps_key):
        head_l, head_m, head_r = st.columns([1.2, 1.4, 1.2])
        with head_l:
            st.caption("Offene Gap")
        with head_m:
            st.caption("Search Queries (kopieren)")
        with head_r:
            st.caption("Lokaler Dateipfad")
        for gap_id in open_gap_ids:
            gap = gap_by_id.get(gap_id)
            if gap is None:
                continue
            col_gap, col_queries, col_path = st.columns([1.2, 1.4, 1.2])
            with col_gap:
                st.markdown(f"`{gap.gap_id}`")
                visual = (gap.needed_visual or gap.subject or "").strip()
                if visual:
                    st.caption(visual if len(visual) <= 160 else visual[:157] + "…")
                if (gap.reason or "").strip():
                    st.caption(f"Grund: {gap.reason.strip()[:120]}")
            with col_queries:
                queries = gap_search_queries(gap)
                if queries:
                    for query in queries:
                        st.code(query, language=None)
                else:
                    st.caption("Keine Search-Queries hinterlegt.")
            with col_path:
                path_key = f"enh_manual_gap_path_{project.id}_{gap.gap_id}"
                path_value = st.text_input(
                    f"Pfad für {gap.gap_id}",
                    value="",
                    key=path_key,
                    label_visibility="collapsed",
                    placeholder="/pfad/zur/datei.mp4",
                    help="Lokaler Dateipfad — keine http(s)-URL.",
                )
                if st.button(
                    "Zuordnen & inventarisieren",
                    key=f"enh_manual_gap_assign_{project.id}_{gap.gap_id}",
                ):
                    try:
                        result = assign_local_file_to_open_gap(
                            project,
                            gap_id=gap.gap_id,
                            source_path=path_value,
                        )
                        assigned = result.candidate
                        st.session_state[
                            f"enh_funnel_pending_deselect_{project.id}"
                        ] = [gap.gap_id]
                        flash_key = f"enh_manual_gap_flash_{project.id}"
                        flash = [
                            (
                                "success",
                                f"`{gap.gap_id}` → `{assigned.candidate_id}` "
                                f"({assigned.media_validation_status})",
                            )
                        ]
                        if result.hint:
                            flash.append(("info", result.hint))
                        st.session_state[flash_key] = flash
                        st.rerun()
                    except ManualGapAssignError as exc:
                        st.error(str(exc))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Fehler: {exc}")
    elif open_gap_ids:
        st.caption(
            f"Manuelle Gap-Zuordnung ausgeblendet ({open_gaps_count} offen). "
            "Checkbox aktivieren zum Bearbeiten."
        )

    flash_key = f"enh_manual_gap_flash_{project.id}"
    for level, message in st.session_state.pop(flash_key, []) or []:
        if level == "info":
            st.info(message)
        else:
            st.success(message)

    # Schwere Stock-JSON nur bei Opt-in laden (nicht nur Checkboxen).
    show_manual_key = f"enh_show_manual_candidates_{project.id}"
    st.checkbox(
        f"Kandidaten manuell prüfen laden ({candidate_count})",
        key=show_manual_key,
        help=(
            "Lädt die komplette Stock-JSON und baut Checkboxen. "
            "Standard aus — sonst sehr langsam."
        ),
        disabled=not has_stock,
    )
    if has_stock and st.session_state.get(show_manual_key):
        results = load_model(stock_path, StockSearchResultsDocument)
        if results is not None:
            gap_passages = _gap_passage_map(coverage)
            selected_ids: list[str] = []
            for index, candidate in enumerate(results.candidates):
                checked = st.checkbox(
                    _stock_candidate_checkbox_label(candidate, gap_passages),
                    value=candidate.selected,
                    key=(
                        f"enh_stock_{project.id}_{index}_"
                        f"{candidate.candidate_id}"
                    ),
                )
                if checked:
                    selected_ids.append(candidate.candidate_id)
            if st.button("Auswahl akzeptieren", key="enh_accept_stock"):
                try:
                    accepted = accept_supplement_candidates(project, selected_ids)
                    st.success(
                        f"{len(accepted.supplements)} Supplements "
                        "akzeptiert (manuell — ohne Download/LLM)."
                    )
                    st.rerun()
                except CutPlanError as exc:
                    st.error(str(exc))
    elif has_stock:
        st.caption(
            f"Stock-Kandidaten nicht geladen ({candidate_count}). "
            "Checkbox aktivieren zum manuellen Prüfen."
        )
    else:
        st.caption("Noch keine Stocksuche — zuerst „Stock suchen“.")

    show_report_key = f"enh_show_funnel_report_{project.id}"
    report_path = supplement_funnel_report_path(project)
    if report_path.is_file():
        st.checkbox(
            "Funnel-Abschlussdetails laden",
            key=show_report_key,
            help="Lädt den großen Funnel-Report inkl. Gap-Zeilen — nur bei Bedarf.",
        )
        if st.session_state.get(show_report_key):
            summary = _funnel_report_top_summary(project)
            if summary is not None:
                st.caption(summary["message"])
                if summary["llm_model"]:
                    st.caption(f"Modell: `{summary['llm_model']}`")
                st.write(
                    f"Angefordert: **{summary['requested']}** · "
                    f"erfüllt: **{summary['filled']}** · "
                    f"offen: **{summary['open']}** · "
                    f"Voll-Downloads: **{summary['downloads']}** · "
                    f"technisch ungültig: **{summary['invalid']}** · "
                    f"Fallbacks: **{summary['fallbacks']}**"
                )
            funnel_report = load_model(report_path, SupplementFunnelReport)
            if funnel_report is not None:
                for gap_rep in funnel_report.gaps:
                    ready = gap_rep.export_ready_candidate_id
                    st.write(
                        f"`{gap_rep.gap_id}` · "
                        + (
                            f"export_ready `{ready}`"
                            if ready
                            else (gap_rep.message or "offen")
                        )
                    )
                if funnel_report.open_gap_ids:
                    st.warning(
                        "Offene Gaps: "
                        + ", ".join(funnel_report.open_gap_ids[:12])
                    )
    else:
        st.caption("Noch kein Funnel-Report vorhanden.")

    accepted_n = _accepted_count(project)
    if accepted_n:
        st.info(f"Akzeptiert: {accepted_n} Supplements")
    show_local_key = f"enh_show_local_assign_{project.id}"
    st.checkbox(
        f"Lokale Dateizuordnung laden ({accepted_n})",
        key=show_local_key,
        help="Optional — Funnel-Downloads sind meist schon export_ready.",
        disabled=accepted_n == 0,
    )
    if accepted_n and st.session_state.get(show_local_key):
        accepted = load_model(
            accepted_supplements_path(project), AcceptedSupplementsDocument
        )
        if accepted is not None:
            for supplement in accepted.supplements:
                refreshed = refresh_supplement_validation(supplement)
                st.write(
                    f"`{refreshed.candidate_id}` · "
                    f"status=`{refreshed.media_validation_status}`"
                )
                if refreshed.media_validation_error:
                    st.caption(refreshed.media_validation_error)
                path_value = st.text_input(
                    f"local_media_path für {refreshed.candidate_id}",
                    value=refreshed.local_media_path or "",
                    key=f"enh_local_{project.id}_{refreshed.candidate_id}",
                    help="Lokaler Dateipfad — keine http(s)-URL.",
                )
                if st.button(
                    f"Lokale Datei zuordnen & validieren ({refreshed.candidate_id})",
                    key=f"enh_assign_{project.id}_{refreshed.candidate_id}",
                ):
                    try:
                        updated = assign_local_media_path(
                            project, refreshed.candidate_id, path_value
                        )
                        st.success(
                            f"{updated.candidate_id} → "
                            f"{updated.media_validation_status}"
                        )
                        st.rerun()
                    except LocalMediaError as exc:
                        st.error(str(exc))


def _render_section_final(project) -> None:
    st.subheader("3. Finalen Cut Plan erzeugen und technisch auflösen")
    final_tokens, final_chapters = _estimate_final_cut_input_tokens(project)
    final_provider, final_model, _final_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_final_cut",
        label="Modell (LLM-Lauf 3)",
        key_prefix="enh_final",
        input_info=LLM_INPUT_INFO["enhanced_final_cut"],
        input_tokens=final_tokens,
        default_output_tokens=_FINAL_CUT_OUTPUT_DEFAULT,
        chapter_count=final_chapters,
    )
    st.caption(
        f"Lauf 3 und Python-Auflösung sind getrennt: "
        f"LLM-Lauf 3 = **ein Call pro Kapitel** ({final_chapters} Kapitel) → "
        f"`final_cut_plan.json`. Python-Finalisierung liest diesen Plan und "
        f"schreibt `resolved_timeline.json`."
    )

    col_llm, col_py = st.columns(2)
    with col_llm:
        run_llm3 = st.button(
            "LLM-Lauf 3 starten",
            type="primary",
            key="enh_final_cut_llm",
            help="Nur Final Cut Plan erzeugen/überschreiben. Keine Timeline-Auflösung.",
        )
    with col_py:
        existing_final = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
        run_python = st.button(
            "Python-Finalisierung starten",
            key="enh_final_cut_python",
            disabled=existing_final is None,
            help=(
                "Technische Auflösung aus vorhandenem final_cut_plan.json."
                if existing_final is not None
                else "Zuerst LLM-Lauf 3 ausführen (final_cut_plan.json fehlt)."
            ),
        )

    if run_llm3:
        try:
            progress = st.empty()

            def _final_progress(folder_name: str, index: int, total: int) -> None:
                progress.info(
                    f"LLM-Lauf 3 · Kapitel {index}/{total}: „{folder_name}“ "
                    f"({resolve_llm_model_id(final_provider, final_model)})…"
                )

            with st.spinner("Finaler Cut — Kapitel nacheinander…"):
                results = generate_all_final_cuts(
                    project,
                    provider=final_provider,
                    model=final_model,
                    progress_callback=_final_progress,
                )
                final = merge_and_persist_final_cuts(project, results)
            progress.empty()
            ok = [r for r in results if r.status == "PASS"]
            fail = [r for r in results if r.status != "PASS"]
            st.success(
                f"LLM-Lauf 3 fertig: {len(ok)}/{len(results)} Kapitel · "
                f"{len(final.shots)} finale Shots. "
                "Als Nächstes „Python-Finalisierung starten“."
            )
            for result in fail:
                st.error(f"„{result.folder_name}“: {result.error}")
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    if run_python:
        try:
            with st.spinner("Technische Auflösung (Python)…"):
                resolved = resolve_final_timeline(project)
            st.success(
                f"Python-Finalisierung: Timeline {resolved.total_duration_seconds:.2f}s · "
                f"{len(resolved.shots)} Shots · Reparaturen: {len(resolved.repairs)} · "
                f"Fehler: {len(resolved.errors)}"
            )
            if resolved.repairs:
                with st.expander("Reparaturen", expanded=False):
                    for repair in resolved.repairs:
                        st.caption(repair)
            if resolved.errors:
                st.warning(
                    f"{len(resolved.errors)} Resolve-Fehler — "
                    "Produktions-OTIO bleibt gesperrt, bis sie behoben sind."
                )
            st.rerun()
        except TimelineResolveError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    show_final_key = f"enh_show_final_shots_{project.id}"
    final = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
    if final is not None:
        st.checkbox(
            f"Finale Shot-Liste laden ({len(final.shots)})",
            key=show_final_key,
        )
        if st.session_state.get(show_final_key):
            st.write(f"Finaler Plan: {len(final.shots)} Shots")
            for shot in final.shots:
                st.caption(
                    f"{shot.shot_id}: {shot.asset_id} · "
                    f"{shot.narration_start_anchor.segment_id}→"
                    f"{shot.narration_end_anchor.segment_id}"
                )

    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is not None:
        gate_errors = validate_resolved_timeline_for_production(project, resolved)
        # Fix 5: gleiche Meldung in resolved.errors + Gate nicht doppelt zählen.
        all_errors = list(dict.fromkeys(list(resolved.errors) + list(gate_errors)))
        has_errors = bool(all_errors)
        st.caption(
            f"Aufgelöste Timeline: {len(resolved.shots)} Shots · "
            f"{resolved.total_duration_seconds:.1f}s · "
            f"Fehler: {len(all_errors)}"
        )
        if has_errors:
            st.warning(
                f"{len(all_errors)} Resolve-/Export-Fehler — "
                "Produktions-OTIO gesperrt. Test-Export mit Lücken möglich."
            )
            with st.expander("Resolve-/Export-Fehler", expanded=False):
                for err in all_errors[:40]:
                    st.write(f"- {err}")
                remaining = len(all_errors) - 40
                if remaining > 0:
                    st.caption(f"… +{remaining} weitere")

        st.markdown("##### OTIO exportieren")
        default_otio_name = f"{(project.name or 'enhanced').strip() or 'enhanced'}_enhanced"
        otio_basename = st.text_input(
            "Dateiname / Export-Basename",
            value=default_otio_name,
            key=f"enh_final_otio_basename_{project.id}",
            help="Ohne Endung. Lokaler Export schreibt `<Name>.otio` ohne Medienkopien.",
        )
        basename = (otio_basename or default_otio_name).strip() or default_otio_name

        st.markdown("###### Lokaler Produktions-Export")
        if st.button(
            "Lokale Produktions-OTIO erzeugen",
            type="primary",
            key=f"enh_final_otio_export_local_{project.id}",
            disabled=has_errors,
            help=(
                "Fail-closed: blockiert bei Resolve-/Medienfehlern."
                if has_errors
                else (
                    "Verwendet die validierten Originaldateien. "
                    "Vorhandene Videos werden nicht kopiert."
                )
            ),
        ):
            try:
                path = export_otio_from_resolved_timeline(
                    project,
                    basename=basename,
                    allow_errors=False,
                )
                st.success(f"Lokale Produktions-OTIO geschrieben: `{path}`")
            except EnhancedOtioExportError as exc:
                st.error(str(exc))

        st.markdown("###### Portables Paket (optional)")
        st.caption(
            "Für Transfer oder Archivierung. Kann Hardlinks oder Kopien nach "
            "`media/` erzeugen und erheblichen Speicherplatz benötigen."
        )
        if st.button(
            "Portables Paket erzeugen",
            key=f"enh_final_otio_export_portable_{project.id}",
            disabled=has_errors,
            help=(
                "Fail-closed: blockiert bei Resolve-/Medienfehlern."
                if has_errors
                else "Speicherintensiv: Hardlinks oder Kopien nach media/."
            ),
        ):
            try:
                package_dir = export_portable_otio_package(
                    project,
                    basename=basename,
                    allow_errors=False,
                )
                media_count = len(list((package_dir / "media").glob("*")))
                st.warning(
                    f"Portables Paket geschrieben: `{package_dir}` "
                    f"({media_count} Medien). Speicherplatz prüfen."
                )
            except EnhancedOtioExportError as exc:
                st.error(str(exc))

        if has_errors and st.button(
            "Test-OTIO mit Lücken erzeugen",
            key=f"enh_final_test_otio_gaps_{project.id}",
            help=(
                "Exportiert bereits aufgelöste Shots; fehlende bleiben Gaps. "
                "Auch unter ⑧ Final Output."
            ),
        ):
            try:
                path = export_otio_from_resolved_timeline(
                    project,
                    basename=f"{basename}_preview_gaps",
                    allow_errors=True,
                )
                st.success(f"Test-OTIO: `{path}`")
            except EnhancedOtioExportError as exc:
                st.error(str(exc))

    show_timeline_key = f"enh_show_timeline_final_{project.id}"
    st.checkbox(
        "Echtzeit-Timeline laden",
        key=show_timeline_key,
    )
    if st.session_state.get(show_timeline_key):
        rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
        timeline = load_model(
            narration_timeline_path(project), NarrationTimelineDocument
        )
        render_realtime_timeline(
            narration_timeline=timeline,
            rough=rough,
            final=final,
            resolved=resolved,
        )


def render_enhanced_cut_plan_page() -> None:
    st.header("⑦ Cut Plan (Enhanced MVP)")
    st.caption(
        "Bereiche getrennt laden — nur der aktive Bereich wird aufgebaut. "
        "Kein Satz = ein Asset."
    )
    project = get_enhanced_project()
    if project is None:
        return

    funnel_mgr_early = get_supplement_funnel_job_manager()
    if funnel_mgr_early.is_running(project.id):
        _render_lightweight_funnel_monitor(project)
        return

    options = _render_cut_plan_settings(project)
    unified_mode = options.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    section_options = (
        list(_SECTION_OPTIONS_UNIFIED)
        if unified_mode
        else list(_SECTION_OPTIONS_LEGACY)
    )

    # Wichtig: st.tabs führt ALLE Tabs aus — daher Radio + nur ein Renderer.
    section_key = f"enh_cut_section_{project.id}"
    if section_key not in st.session_state or st.session_state[section_key] not in section_options:
        st.session_state[section_key] = (
            _SECTION_UNIFIED if unified_mode else _default_cut_section(project)
        )
    section = st.radio(
        "Bereich",
        options=section_options,
        key=section_key,
        horizontal=True,
        help=(
            "Nur der gewählte Bereich wird geladen. "
            "So bleiben Modellwechsel und Funnel-Klicks schnell."
        ),
    )
    if section == _SECTION_UNIFIED:
        _render_section_unified(project)
    elif section == _SECTION_ROUGH:
        _render_section_rough(project)
    elif section == _SECTION_FUNNEL:
        _render_section_funnel(project)
    else:
        _render_section_final(project)
