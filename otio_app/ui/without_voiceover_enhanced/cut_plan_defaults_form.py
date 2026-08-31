"""Cut-Plan-Felder für den Sprachstandards-Hub (ohne Projekt)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    ENHANCED_CUT_LLM_MODEL_CHOICES,
    ENHANCED_CUT_LLM_MODEL_LABELS,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_CHOICES,
    CUT_PLAN_MODE_UNIFIED,
    CUT_PLAN_OPTIONS_SCHEMA_VERSION,
    DEFAULT_ELEVENLABS_MUSIC_COUNT,
    DEFAULT_LLM_CUT_MODEL,
    DEFAULT_MAX_SFX_PER_CHAPTER,
    DEFAULT_SFX_PLANNER_MODEL,
    ELEVENLABS_MUSIC_COUNT_MAX,
    ELEVENLABS_MUSIC_COUNT_MIN,
    LLM_CUT_PREFIX_COUNT_MAX,
    LLM_CUT_PREFIX_COUNT_MIN,
    MAX_SFX_PER_CHAPTER_MAX,
    MAX_SFX_PER_CHAPTER_MIN,
    STILL_BACKGROUND_CHOICES,
    STILL_PAN_MODE_CHOICES,
    TIMING_MODE_CHOICES,
    UNIFIED_CUT_STYLE_CHOICES,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
    CutPlanOptions,
)


def _k(suffix: str, name: str) -> str:
    return f"lang_hub_cut_{name}_{suffix}"


def render_cut_plan_defaults_form(
    current: CutPlanOptions, *, key_suffix: str
) -> CutPlanOptions:
    """Widget-Formular für alle speicherbaren CutPlanOptions-Felder."""
    cut_mode_labels = {
        "legacy": "Legacy (Rough + Final, 2 LLM-Läufe)",
        "unified": "Unified (1 LLM-Lauf + Python-Timing)",
    }
    mode_options = list(CUT_PLAN_MODE_CHOICES)
    cut_plan_mode = st.radio(
        "Cut-Plan-Modus",
        options=mode_options,
        format_func=lambda m: cut_mode_labels.get(m, m),
        index=(
            mode_options.index(current.cut_plan_mode)
            if current.cut_plan_mode in mode_options
            else 0
        ),
        key=_k(key_suffix, "cut_mode"),
        horizontal=True,
    )
    style_labels = {
        "rhythm": "Rhythmus (shot_min/max)",
        "keyword_sync": "Keyword-Sync (Wort↔Bild)",
        "keyword_flow": "Keyword Flow",
        "keyword_flow_free": "Keyword Flow Free",
    }
    style_options = list(UNIFIED_CUT_STYLE_CHOICES)
    unified_cut_style = st.radio(
        "Unified-Stil",
        options=style_options,
        format_func=lambda s: style_labels.get(s, s),
        index=(
            style_options.index(current.unified_cut_style)
            if current.unified_cut_style in style_options
            else 0
        ),
        key=_k(key_suffix, "unified_style"),
        horizontal=True,
        disabled=cut_plan_mode != CUT_PLAN_MODE_UNIFIED,
    )
    keyword_flow_allow_onset_overflow = bool(current.keyword_flow_allow_onset_overflow)
    if (
        cut_plan_mode == CUT_PLAN_MODE_UNIFIED
        and unified_cut_style
        in {UNIFIED_CUT_STYLE_KEYWORD_FLOW, UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE}
    ):
        overflow_key = (
            "kff_onset_overflow"
            if unified_cut_style == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE
            else "kf_onset_overflow"
        )
        keyword_flow_allow_onset_overflow = st.checkbox(
            "Keyword-Onset-Toleranz überschreitbar (Timing trotzdem akzeptieren)",
            value=bool(current.keyword_flow_allow_onset_overflow),
            key=_k(key_suffix, overflow_key),
        )
    enable_unified_mini_repair = st.checkbox(
        "Unified Mini-Repair nach Gap-Merge (optional, Default aus)",
        value=bool(current.enable_unified_mini_repair),
        key=_k(key_suffix, "mini_repair"),
        disabled=cut_plan_mode != CUT_PLAN_MODE_UNIFIED,
        help=(
            "Nicht für zu kurze Clips. Extra-LLM nach Gap-Merge, nur wenn zu "
            "viele offene Lücken da sind. Mini-Lücken unter 1s löst Python "
            "Timing über „Toleranz Asset zu kurz“ (erst Nachbarn, dann "
            "deren Nachbarn)."
        ),
    )
    unified_mini_repair_threshold = st.number_input(
        "Mini-Repair-Schwellwert",
        min_value=0.0,
        max_value=1.0,
        value=float(current.unified_mini_repair_threshold),
        step=0.05,
        key=_k(key_suffix, "mini_repair_thr"),
        disabled=cut_plan_mode != CUT_PLAN_MODE_UNIFIED
        or not enable_unified_mini_repair,
    )

    st.markdown("##### LLM Cut")
    cut_model_options = list(ENHANCED_CUT_LLM_MODEL_CHOICES)
    current_llm_cut_model = str(current.llm_cut_model or "").strip() or DEFAULT_LLM_CUT_MODEL
    if current_llm_cut_model not in cut_model_options:
        cut_model_options = [current_llm_cut_model, *cut_model_options]
    llm_cut_model = st.selectbox(
        "Standard-Modell (Unified Cut / Auto-Lauf)",
        options=cut_model_options,
        index=cut_model_options.index(current_llm_cut_model),
        format_func=lambda m: ENHANCED_CUT_LLM_MODEL_LABELS.get(m, m),
        key=_k(key_suffix, "llm_cut_model"),
    )
    llm_cut_prefix_count = st.number_input(
        "Erste N Cuts mit anderem Modell (inkl. Intro)",
        min_value=LLM_CUT_PREFIX_COUNT_MIN,
        max_value=LLM_CUT_PREFIX_COUNT_MAX,
        value=int(current.llm_cut_prefix_count or 0),
        step=1,
        key=_k(key_suffix, "prefix_count"),
        help=(
            "Intro zählt als 1, danach Körper-Kapitel. 4 = Intro + erste drei "
            "Kapitel. 0 = aus."
        ),
    )
    prefix_model_options = [""] + list(cut_model_options)
    current_prefix_model = str(current.llm_cut_prefix_model or "").strip()
    if current_prefix_model and current_prefix_model not in prefix_model_options:
        prefix_model_options = ["", current_prefix_model, *cut_model_options]
    llm_cut_prefix_model = st.selectbox(
        "Modell für die ersten N",
        options=prefix_model_options,
        index=(
            prefix_model_options.index(current_prefix_model)
            if current_prefix_model in prefix_model_options
            else 0
        ),
        format_func=lambda m: (
            "— aus (nur Standard-Modell)"
            if not m
            else ENHANCED_CUT_LLM_MODEL_LABELS.get(m, m)
        ),
        key=_k(key_suffix, "prefix_model"),
    )

    st.markdown("##### Sound Effects / Music")
    sfx_model_options = list(ENHANCED_CUT_LLM_MODEL_CHOICES)
    current_sfx_model = str(
        current.sfx_planner_model or DEFAULT_SFX_PLANNER_MODEL
    ).strip()
    if current_sfx_model not in sfx_model_options:
        sfx_model_options = [current_sfx_model, *sfx_model_options]
    sfx_planner_model = st.selectbox(
        "SFX Planner Model",
        options=sfx_model_options,
        index=sfx_model_options.index(current_sfx_model),
        format_func=lambda m: ENHANCED_CUT_LLM_MODEL_LABELS.get(m, m),
        key=_k(key_suffix, "sfx_planner"),
    )
    max_sfx_per_chapter = st.number_input(
        "Maximum SFX per chapter",
        min_value=MAX_SFX_PER_CHAPTER_MIN,
        max_value=MAX_SFX_PER_CHAPTER_MAX,
        value=int(current.max_sfx_per_chapter or DEFAULT_MAX_SFX_PER_CHAPTER),
        step=1,
        key=_k(key_suffix, "max_sfx"),
    )
    elevenlabs_music_count = st.number_input(
        "Anzahl Music-Stücke (inkl. Intro)",
        min_value=ELEVENLABS_MUSIC_COUNT_MIN,
        max_value=ELEVENLABS_MUSIC_COUNT_MAX,
        value=int(current.elevenlabs_music_count or DEFAULT_ELEVENLABS_MUSIC_COUNT),
        step=1,
        key=_k(key_suffix, "music_count"),
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        shot_min_sec = st.number_input(
            "Shot Min (s)",
            min_value=0.4,
            max_value=60.0,
            value=float(current.shot_min_sec),
            step=0.5,
            key=_k(key_suffix, "shot_min"),
        )
        shot_max_sec = st.number_input(
            "Shot Max (s)",
            min_value=0.4,
            max_value=120.0,
            value=float(current.shot_max_sec),
            step=0.5,
            key=_k(key_suffix, "shot_max"),
        )
        video_head_trim_sec = st.number_input(
            "Video Head Trim (s)",
            min_value=0.0,
            max_value=10.0,
            value=float(current.video_head_trim_sec),
            step=0.1,
            key=_k(key_suffix, "head_trim"),
        )
        short_asset_tolerance_sec = st.number_input(
            "Toleranz Asset zu kurz (s)",
            min_value=0.0,
            max_value=30.0,
            value=float(current.short_asset_tolerance_sec),
            step=0.5,
            key=_k(key_suffix, "short_tol"),
            help=(
                "Fehlt nutzbare Dauer: Python verlängert zuerst den Clip "
                "davor und/oder danach, sonst die nächsten daneben. "
                "Rest = roter Platzhalter. Default-Toleranz 1s (Mini-Reste)."
            ),
        )
    with col2:
        max_asset_usage = st.number_input(
            "Max Asset Usage (Intro zählt nicht)",
            min_value=1,
            max_value=50,
            value=int(current.max_asset_usage),
            step=1,
            key=_k(key_suffix, "max_usage"),
        )
        min_asset_reuse_distance_shots = st.number_input(
            "Asset-Reuse-Abstand (Shots)",
            min_value=0,
            max_value=100,
            value=int(current.min_asset_reuse_distance_shots),
            step=1,
            key=_k(key_suffix, "reuse_distance"),
        )
        include_middle_frames = st.checkbox(
            "Mittel-Frames an LLM 2 (Vision)",
            value=bool(current.include_middle_frames),
            key=_k(key_suffix, "middle_frames"),
        )
        max_middle_frames_per_chapter = st.number_input(
            "Max. Mittel-Frames / Kapitel",
            min_value=1,
            max_value=200,
            value=int(current.max_middle_frames_per_chapter),
            step=1,
            key=_k(key_suffix, "max_frames"),
            disabled=not include_middle_frames,
        )
    with col3:
        max_candidates_per_gap = st.number_input(
            "Funnel: Max Kandidaten / Gap",
            min_value=1,
            max_value=20,
            value=int(current.max_candidates_per_gap),
            step=1,
            key=_k(key_suffix, "funnel_cands"),
        )
        max_full_download_attempts_per_gap = st.number_input(
            "Funnel: Max Downloads / Gap",
            min_value=1,
            max_value=3,
            value=int(current.max_full_download_attempts_per_gap),
            step=1,
            key=_k(key_suffix, "funnel_dl"),
        )

    st.markdown("##### Voice-over Vorlauf / Nachlauf (pro Kapitel)")
    mode_labels = {"fixed": "Fest", "llm": "LLM entscheidet (0…Max)"}
    v1, v2, v3, v4 = st.columns(4)
    with v1:
        voiceover_preroll_sec = st.number_input(
            "Vorlauf Max/Fest (s)",
            min_value=0.0,
            max_value=30.0,
            value=float(current.voiceover_preroll_sec),
            step=0.5,
            key=_k(key_suffix, "preroll"),
        )
    with v2:
        preroll_modes = list(TIMING_MODE_CHOICES)
        voiceover_preroll_mode = st.selectbox(
            "Vorlauf-Modus",
            options=preroll_modes,
            index=(
                preroll_modes.index(current.voiceover_preroll_mode)
                if current.voiceover_preroll_mode in preroll_modes
                else 0
            ),
            format_func=lambda m: mode_labels.get(m, m),
            key=_k(key_suffix, "preroll_mode"),
        )
    with v3:
        voiceover_postroll_sec = st.number_input(
            "Nachlauf Max/Fest (s)",
            min_value=0.0,
            max_value=60.0,
            value=float(current.voiceover_postroll_sec),
            step=0.5,
            key=_k(key_suffix, "postroll"),
        )
    with v4:
        postroll_modes = list(TIMING_MODE_CHOICES)
        voiceover_postroll_mode = st.selectbox(
            "Nachlauf-Modus",
            options=postroll_modes,
            index=(
                postroll_modes.index(current.voiceover_postroll_mode)
                if current.voiceover_postroll_mode in postroll_modes
                else 0
            ),
            format_func=lambda m: mode_labels.get(m, m),
            key=_k(key_suffix, "postroll_mode"),
        )

    st.markdown("##### Intro Vorlauf / Nachlauf")
    i1, i2, i3, i4 = st.columns(4)
    with i1:
        intro_voiceover_preroll_sec = st.number_input(
            "Intro Vorlauf (s)",
            min_value=0.0,
            max_value=30.0,
            value=float(current.intro_voiceover_preroll_sec),
            step=0.5,
            key=_k(key_suffix, "intro_preroll"),
        )
    with i2:
        intro_voiceover_postroll_sec = st.number_input(
            "Intro Nachlauf Preferenz (s)",
            min_value=0.0,
            max_value=60.0,
            value=float(current.intro_voiceover_postroll_sec),
            step=0.5,
            key=_k(key_suffix, "intro_postroll"),
        )
    with i3:
        intro_voiceover_postroll_min_sec = st.number_input(
            "Intro Nachlauf Min (s)",
            min_value=0.0,
            max_value=60.0,
            value=float(current.intro_voiceover_postroll_min_sec),
            step=0.5,
            key=_k(key_suffix, "intro_postroll_min"),
        )
    with i4:
        intro_voiceover_postroll_max_sec = st.number_input(
            "Intro Nachlauf Max (s)",
            min_value=0.0,
            max_value=60.0,
            value=float(current.intro_voiceover_postroll_max_sec),
            step=0.5,
            key=_k(key_suffix, "intro_postroll_max"),
        )

    st.markdown("##### Ordner-Titel (OTIO)")
    folder_title_enabled = st.checkbox(
        "Ordner-Titel einblenden",
        value=bool(current.folder_title_enabled),
        key=_k(key_suffix, "folder_title"),
    )
    from otio_app.services.font_utils import FOLDER_TITLE_FONT_OPTIONS

    font_options = list(FOLDER_TITLE_FONT_OPTIONS)
    current_font = str(current.folder_title_font or font_options[0])
    if current_font not in font_options:
        font_options = [current_font, *font_options]
    t1, t2, t3 = st.columns(3)
    with t1:
        folder_title_font = st.selectbox(
            "Titel-Font",
            options=font_options,
            index=font_options.index(current_font),
            key=_k(key_suffix, "title_font"),
            disabled=not folder_title_enabled,
        )
    with t2:
        folder_title_duration_sec = st.number_input(
            "Titel-Dauer (s)",
            min_value=0.5,
            max_value=30.0,
            value=float(current.folder_title_duration_sec),
            step=0.5,
            key=_k(key_suffix, "title_dur"),
            disabled=not folder_title_enabled,
        )
    with t3:
        folder_title_font_size = st.number_input(
            "Titel-Schriftgröße (0 = auto)",
            min_value=0.0,
            max_value=400.0,
            value=float(current.folder_title_font_size),
            step=1.0,
            key=_k(key_suffix, "title_size"),
            disabled=not folder_title_enabled,
        )
    f1, f2 = st.columns(2)
    with f1:
        folder_title_fade_in_sec = st.number_input(
            "Fade-In (s)",
            min_value=0.0,
            max_value=10.0,
            value=float(current.folder_title_fade_in_sec),
            step=0.05,
            key=_k(key_suffix, "title_fade_in"),
            disabled=not folder_title_enabled,
        )
    with f2:
        folder_title_fade_out_sec = st.number_input(
            "Fade-Out (s)",
            min_value=0.0,
            max_value=10.0,
            value=float(current.folder_title_fade_out_sec),
            step=0.05,
            key=_k(key_suffix, "title_fade_out"),
            disabled=not folder_title_enabled,
        )

    st.markdown("##### Still-Bilder (OTIO-Export)")
    still_image_style_enabled = st.checkbox(
        "Still-Style aktiv (Background + Skalierung)",
        value=bool(current.still_image_style_enabled),
        key=_k(key_suffix, "still_style"),
    )
    s1, s2 = st.columns(2)
    with s1:
        still_image_zoom = st.number_input(
            "Still Zoom (fit-Anteil)",
            min_value=0.05,
            max_value=1.0,
            value=float(current.still_image_zoom),
            step=0.05,
            key=_k(key_suffix, "still_zoom"),
            disabled=not still_image_style_enabled,
        )
    with s2:
        bg_options = list(STILL_BACKGROUND_CHOICES)
        bg_labels = {
            "vintage": "vintage — Papiertextur",
            "paper_edge": "paper_edge — Papiertextur + Papierrand",
            "none": "none — schwarzer Hintergrund",
        }
        still_image_background_style = st.selectbox(
            "Still Background",
            options=bg_options,
            index=(
                bg_options.index(current.still_image_background_style)
                if current.still_image_background_style in bg_options
                else 0
            ),
            format_func=lambda v: bg_labels.get(v, v),
            key=_k(key_suffix, "still_bg"),
            disabled=not still_image_style_enabled,
        )
    still_image_dynamic_zoom_enabled = st.checkbox(
        "Dynamischer Zoom (Ken Burns)",
        value=bool(current.still_image_dynamic_zoom_enabled),
        key=_k(key_suffix, "still_dyn_zoom"),
    )
    still_image_dynamic_zoom_factor = st.number_input(
        "Dynamischer Zoom-Faktor (Ende)",
        min_value=1.02,
        max_value=1.35,
        value=float(current.still_image_dynamic_zoom_factor),
        step=0.01,
        key=_k(key_suffix, "still_dyn_zoom_factor"),
        disabled=not still_image_dynamic_zoom_enabled,
    )
    pan_labels = {
        "off": "Aus (Cover + abwechselnd)",
        "ltr": "Links → Rechts",
        "rtl": "Rechts → Links",
        "alternate": "Abwechselnd L→R / R→L",
    }
    pan_options = list(STILL_PAN_MODE_CHOICES)
    still_image_pan_mode = st.selectbox(
        "Still-Schwenk (Cover 16:9 + Pan)",
        options=pan_options,
        index=(
            pan_options.index(current.still_image_pan_mode)
            if current.still_image_pan_mode in pan_options
            else 0
        ),
        format_func=lambda v: pan_labels.get(v, v),
        key=_k(key_suffix, "still_pan"),
    )
    still_image_pan_travel = st.number_input(
        "Schwenk-Weg (Anteil der Bildbreite)",
        min_value=0.01,
        max_value=0.30,
        value=float(current.still_image_pan_travel),
        step=0.01,
        key=_k(key_suffix, "still_pan_travel"),
    )
    pan_a1, pan_a2 = st.columns(2)
    with pan_a1:
        still_image_pan_min_aspect = st.number_input(
            "Cover ab Aspect min (Breite/Höhe)",
            min_value=1.0,
            max_value=3.0,
            value=float(current.still_image_pan_min_aspect),
            step=0.05,
            key=_k(key_suffix, "still_pan_min_ar"),
        )
    with pan_a2:
        still_image_pan_max_aspect = st.number_input(
            "Cover bis Aspect max (Breite/Höhe)",
            min_value=1.0,
            max_value=4.0,
            value=float(current.still_image_pan_max_aspect),
            step=0.05,
            key=_k(key_suffix, "still_pan_max_ar"),
        )

    return CutPlanOptions(
        schema_version=CUT_PLAN_OPTIONS_SCHEMA_VERSION,
        cut_plan_mode=str(cut_plan_mode),  # type: ignore[arg-type]
        unified_cut_style=str(unified_cut_style),  # type: ignore[arg-type]
        keyword_flow_allow_onset_overflow=bool(keyword_flow_allow_onset_overflow),
        enable_unified_mini_repair=bool(enable_unified_mini_repair),
        unified_mini_repair_threshold=float(unified_mini_repair_threshold),
        llm_cut_model=str(llm_cut_model),
        llm_cut_prefix_count=int(llm_cut_prefix_count),
        llm_cut_prefix_model=str(llm_cut_prefix_model or "").strip(),
        sfx_planner_model=str(sfx_planner_model),
        max_sfx_per_chapter=int(max_sfx_per_chapter),
        elevenlabs_music_count=int(elevenlabs_music_count),
        include_middle_frames=bool(include_middle_frames),
        max_middle_frames_per_chapter=int(max_middle_frames_per_chapter),
        max_candidates_per_gap=int(max_candidates_per_gap),
        max_full_download_attempts_per_gap=int(max_full_download_attempts_per_gap),
        shot_min_sec=float(shot_min_sec),
        shot_max_sec=float(shot_max_sec),
        video_head_trim_sec=float(video_head_trim_sec),
        max_asset_usage=int(max_asset_usage),
        min_asset_reuse_distance_shots=int(min_asset_reuse_distance_shots),
        voiceover_preroll_sec=float(voiceover_preroll_sec),
        voiceover_preroll_mode=str(voiceover_preroll_mode),  # type: ignore[arg-type]
        voiceover_postroll_sec=float(voiceover_postroll_sec),
        voiceover_postroll_mode=str(voiceover_postroll_mode),  # type: ignore[arg-type]
        intro_voiceover_preroll_sec=float(intro_voiceover_preroll_sec),
        intro_voiceover_postroll_sec=float(intro_voiceover_postroll_sec),
        intro_voiceover_postroll_min_sec=float(intro_voiceover_postroll_min_sec),
        intro_voiceover_postroll_max_sec=float(intro_voiceover_postroll_max_sec),
        short_asset_tolerance_sec=float(short_asset_tolerance_sec),
        folder_title_enabled=bool(folder_title_enabled),
        folder_title_font=str(folder_title_font or current.folder_title_font),
        folder_title_duration_sec=float(folder_title_duration_sec),
        folder_title_font_size=float(folder_title_font_size),
        folder_title_fade_in_sec=float(folder_title_fade_in_sec),
        folder_title_fade_out_sec=float(folder_title_fade_out_sec),
        still_image_style_enabled=bool(still_image_style_enabled),
        still_image_zoom=float(still_image_zoom),
        still_image_background_style=str(still_image_background_style),
        still_image_dynamic_zoom_enabled=bool(still_image_dynamic_zoom_enabled),
        still_image_dynamic_zoom_factor=float(still_image_dynamic_zoom_factor),
        still_image_pan_mode=str(still_image_pan_mode),
        still_image_pan_travel=float(still_image_pan_travel),
        still_image_pan_min_aspect=float(still_image_pan_min_aspect),
        still_image_pan_max_aspect=float(still_image_pan_max_aspect),
    )
