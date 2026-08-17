"""Optionen für Enhanced Cut Plan (LLM-Lauf 2/3 + Python/OTIO)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from otio_app.defaults import (
    CUT_PLAN_DEFAULT_FOLDER_TITLE_DURATION_SEC,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_ENABLED,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FADE_IN_SEC,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FADE_OUT_SEC,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT_SIZE,
    CUT_PLAN_DEFAULT_MAX_ASSET_USAGE,
    CUT_PLAN_DEFAULT_SHOT_MAX_SEC,
    CUT_PLAN_DEFAULT_SHOT_MIN_SEC,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    VOICEOVER_GEN_ENHANCED_CUT_DEFAULT_MODEL,
    VOICEOVER_GEN_ENHANCED_CUT_DEFAULT_PROVIDER,
)
from otio_app.models import Project
from otio_app.services.still_image_export_style import (
    DEFAULT_STILL_IMAGE_ZOOM,
    STILL_BACKGROUND_NONE,
    STILL_BACKGROUND_PAPER_EDGE,
    STILL_BACKGROUND_VINTAGE,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import cut_plan_options_path

# Soft cap: zu viele JPEGs pro Kapitel-Call sprengen Kontext/Kosten.
DEFAULT_MAX_MIDDLE_FRAMES_PER_CHAPTER = 40
DEFAULT_MAX_CANDIDATES_PER_GAP = 20
DEFAULT_MAX_FULL_DOWNLOAD_ATTEMPTS_PER_GAP = 3

# Enhanced-spezifische Defaults (Reuse-Gap bewusst 4, nicht Classic-0).
ENHANCED_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS = 4
ENHANCED_DEFAULT_VOICEOVER_PREROLL_SEC = 1.0
ENHANCED_DEFAULT_VOICEOVER_POSTROLL_SEC = 5.0
ENHANCED_DEFAULT_SHORT_ASSET_TOLERANCE_SEC = 1.0
# LLM-Planung: von jeder Motion-Asset-Nutzdauer abziehen (Frame-Drift / Nachlauf).
LLM_ASSET_DURATION_SAFETY_SEC = 1.0

TIMING_MODE_FIXED = "fixed"
TIMING_MODE_LLM = "llm"
TimingMode = Literal["fixed", "llm"]
TIMING_MODE_CHOICES: tuple[str, ...] = (TIMING_MODE_FIXED, TIMING_MODE_LLM)

CUT_PLAN_MODE_LEGACY = "legacy"
CUT_PLAN_MODE_UNIFIED = "unified"
CutPlanMode = Literal["legacy", "unified"]
CUT_PLAN_MODE_CHOICES: tuple[str, ...] = (CUT_PLAN_MODE_LEGACY, CUT_PLAN_MODE_UNIFIED)

# Unified-Stil: Rhythmus vs Keyword-Sync vs Keyword-Flow vs Keyword-Flow-Free.
# Alle Modi erhalten Word-Timestamps + Cut-Settings (inkl. shot_min/max).
UNIFIED_CUT_STYLE_RHYTHM = "rhythm"
UNIFIED_CUT_STYLE_KEYWORD_SYNC = "keyword_sync"
UNIFIED_CUT_STYLE_KEYWORD_FLOW = "keyword_flow"
UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE = "keyword_flow_free"
UnifiedCutStyle = Literal[
    "rhythm", "keyword_sync", "keyword_flow", "keyword_flow_free"
]
UNIFIED_CUT_STYLE_CHOICES: tuple[str, ...] = (
    UNIFIED_CUT_STYLE_RHYTHM,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
)
# Keyword-Onset-Toleranz (nur keyword_flow, Python-seitig).
KEYWORD_FLOW_ONSET_TOLERANCE_SEC = 1.5
KEYWORD_FLOW_PAUSE_SAFETY_FRAMES = 5
KEYWORD_FLOW_MAP_OPENER_SEC = 9.0
# Alte Pläne mit pause_directives sind für Keyword Flow blockiert (keine
# Pausenverlängerung mehr). Meldung unverändert an UI/Timing/OTIO durchreichen.
KEYWORD_FLOW_UNSUPPORTED_PAUSE_EXTENSIONS_MESSAGE = (
    "Dieser Keyword-Flow-Plan enthält nicht mehr unterstützte "
    "Pausenverlängerungen. Bitte den LLM-Cut für dieses Kapitel neu erzeugen."
)

STILL_BACKGROUND_CHOICES = (
    STILL_BACKGROUND_VINTAGE,
    STILL_BACKGROUND_PAPER_EDGE,
    STILL_BACKGROUND_NONE,
)


# Dynamischer Still-Zoom (Ken Burns): Endfaktor relativ zum Start (1.0 = aus).
DEFAULT_STILL_DYNAMIC_ZOOM_FACTOR = 1.12
STILL_DYNAMIC_ZOOM_FACTOR_MIN = 1.02
STILL_DYNAMIC_ZOOM_FACTOR_MAX = 1.35

# Horizontaler Still-Schwenk (Cover-Fill + Extra-Zoom gegen schwarze Ränder).
STILL_PAN_MODE_OFF = "off"
STILL_PAN_MODE_LTR = "ltr"
STILL_PAN_MODE_RTL = "rtl"
STILL_PAN_MODE_ALTERNATE = "alternate"
STILL_PAN_MODE_CHOICES = (
    STILL_PAN_MODE_OFF,
    STILL_PAN_MODE_LTR,
    STILL_PAN_MODE_RTL,
    STILL_PAN_MODE_ALTERNATE,
)
# Sehr subtiler Default (~2 % Frame-Breite); 0.12/0.04 wirkten zu stark.
DEFAULT_STILL_PAN_TRAVEL = 0.02
LEGACY_STILL_PAN_TRAVELS = (0.12, 0.04)
STILL_PAN_TRAVEL_MIN = 0.01
STILL_PAN_TRAVEL_MAX = 0.30
# Cover+Pan wenn Bild-Seitenverhältnis nahe 16:9 (sonst Paper-Edge/Vintage).
DEFAULT_STILL_PAN_MIN_ASPECT = 1.50  # ~3:2
DEFAULT_STILL_PAN_MAX_ASPECT = 2.05  # etwas breiter als 16:9
STILL_PAN_FALLBACK_ZOOM = 0.8
# Intro-only Vorlauf/Nachlauf (LLM-Prompt + Python Timing; unabhängig von Kapiteln).
DEFAULT_INTRO_VOICEOVER_PREROLL_SEC = 4.0
DEFAULT_INTRO_VOICEOVER_POSTROLL_SEC = 6.5
DEFAULT_INTRO_VOICEOVER_POSTROLL_MIN_SEC = 5.0
DEFAULT_INTRO_VOICEOVER_POSTROLL_MAX_SEC = 8.0
CUT_PLAN_OPTIONS_SCHEMA_VERSION = "1.12"
DEFAULT_MAX_SFX_PER_CHAPTER = 3
MAX_SFX_PER_CHAPTER_MIN = 0
MAX_SFX_PER_CHAPTER_MAX = 5
DEFAULT_SFX_PLANNER_MODEL = "openai:gpt-5.6-sol"
DEFAULT_LLM_CUT_MODEL = (
    f"{VOICEOVER_GEN_ENHANCED_CUT_DEFAULT_PROVIDER}:"
    f"{VOICEOVER_GEN_ENHANCED_CUT_DEFAULT_MODEL}"
)
# ElevenLabs Music: total pieces including Intro. 4 = Intro + first 3 body chapters.
DEFAULT_ELEVENLABS_MUSIC_COUNT = 4
ELEVENLABS_MUSIC_COUNT_MIN = 1
ELEVENLABS_MUSIC_COUNT_MAX = 40


class CutPlanOptions(BaseModel):
    schema_version: str = CUT_PLAN_OPTIONS_SCHEMA_VERSION
    # Phase 7: Unified (1 LLM) vs Legacy (Rough + Final).
    cut_plan_mode: CutPlanMode = CUT_PLAN_MODE_LEGACY
    # Unified Stil: Rhythmus (Default) oder Keyword-Sync (Wort↔Bild).
    unified_cut_style: UnifiedCutStyle = UNIFIED_CUT_STYLE_RHYTHM
    # Keyword Flow: bei True Timing trotz Onset-Verschiebung > ±1.5s akzeptieren
    # (Clamp-Zeiten behalten, als Repair/Warnung loggen). Default strikt.
    keyword_flow_allow_onset_overflow: bool = False
    # Phase 6: optionaler Mini-Repair nach Gap-Merge (Default aus).
    enable_unified_mini_repair: bool = False
    unified_mini_repair_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    # Combined id (openai:gpt-5.6-terra). Empty = inherit from model_settings.
    llm_cut_model: str = ""
    # ElevenLabs SFX MVP: planner model (independent of Final/Unified Cut model).
    sfx_planner_model: str = DEFAULT_SFX_PLANNER_MODEL
    # Hard maximum per chapter/intro scope — not a target. Prefer fewer.
    max_sfx_per_chapter: int = Field(
        default=DEFAULT_MAX_SFX_PER_CHAPTER,
        ge=MAX_SFX_PER_CHAPTER_MIN,
        le=MAX_SFX_PER_CHAPTER_MAX,
    )
    # ElevenLabs Music pieces to generate: Intro counts as 1, then body chapters
    # in film order. 4 = Intro + first 3 chapters. 1 = Intro only.
    elevenlabs_music_count: int = Field(
        default=DEFAULT_ELEVENLABS_MUSIC_COUNT,
        ge=ELEVENLABS_MUSIC_COUNT_MIN,
        le=ELEVENLABS_MUSIC_COUNT_MAX,
    )
    include_middle_frames: bool = False
    max_middle_frames_per_chapter: int = Field(
        default=DEFAULT_MAX_MIDDLE_FRAMES_PER_CHAPTER,
        ge=1,
        le=200,
    )
    max_candidates_per_gap: int = Field(
        default=DEFAULT_MAX_CANDIDATES_PER_GAP,
        ge=1,
        le=20,
    )
    max_full_download_attempts_per_gap: int = Field(
        default=DEFAULT_MAX_FULL_DOWNLOAD_ATTEMPTS_PER_GAP,
        ge=1,
        le=3,
    )

    shot_min_sec: float = Field(default=CUT_PLAN_DEFAULT_SHOT_MIN_SEC, ge=0.4, le=60.0)
    shot_max_sec: float = Field(default=CUT_PLAN_DEFAULT_SHOT_MAX_SEC, ge=0.4, le=120.0)
    video_head_trim_sec: float = Field(
        default=CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC, ge=0.0, le=10.0
    )
    max_asset_usage: int = Field(default=CUT_PLAN_DEFAULT_MAX_ASSET_USAGE, ge=1, le=50)
    min_asset_reuse_distance_shots: int = Field(
        default=ENHANCED_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS, ge=0, le=100
    )

    # Vorlauf: Bild vor Voice-over — gilt pro Kapitel/Folder.
    voiceover_preroll_sec: float = Field(
        default=ENHANCED_DEFAULT_VOICEOVER_PREROLL_SEC, ge=0.0, le=30.0
    )
    voiceover_preroll_mode: TimingMode = TIMING_MODE_FIXED
    # Nachlauf: letzter Shot nach Voice-over-Ende — gilt pro Kapitel/Folder.
    voiceover_postroll_sec: float = Field(
        default=ENHANCED_DEFAULT_VOICEOVER_POSTROLL_SEC, ge=0.0, le=60.0
    )
    voiceover_postroll_mode: TimingMode = TIMING_MODE_FIXED
    # Intro-Hüllen (Opening vor VO / Closing nach VO) — nicht Kapitel-Settings.
    intro_voiceover_preroll_sec: float = Field(
        default=DEFAULT_INTRO_VOICEOVER_PREROLL_SEC, ge=0.0, le=30.0
    )
    intro_voiceover_postroll_sec: float = Field(
        default=DEFAULT_INTRO_VOICEOVER_POSTROLL_SEC, ge=0.0, le=60.0
    )
    intro_voiceover_postroll_min_sec: float = Field(
        default=DEFAULT_INTRO_VOICEOVER_POSTROLL_MIN_SEC, ge=0.0, le=60.0
    )
    intro_voiceover_postroll_max_sec: float = Field(
        default=DEFAULT_INTRO_VOICEOVER_POSTROLL_MAX_SEC, ge=0.0, le=60.0
    )
    # Asset darf bis zu dieser Unterlänge trotzdem genutzt werden:
    # Shortfall geht an Nachbar-Clips (shot_max darf dabei überschritten werden).
    short_asset_tolerance_sec: float = Field(
        default=ENHANCED_DEFAULT_SHORT_ASSET_TOLERANCE_SEC, ge=0.0, le=30.0
    )

    folder_title_enabled: bool = CUT_PLAN_DEFAULT_FOLDER_TITLE_ENABLED
    folder_title_font: str = CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT
    folder_title_duration_sec: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_DURATION_SEC, ge=0.5, le=30.0
    )
    folder_title_font_size: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT_SIZE, ge=0.0, le=400.0
    )
    folder_title_fade_in_sec: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_FADE_IN_SEC, ge=0.0, le=10.0
    )
    folder_title_fade_out_sec: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_FADE_OUT_SEC, ge=0.0, le=10.0
    )

    still_image_style_enabled: bool = True
    still_image_zoom: float = Field(default=DEFAULT_STILL_IMAGE_ZOOM, ge=0.05, le=1.0)
    still_image_background_style: str = STILL_BACKGROUND_VINTAGE
    # Ken-Burns-Zoom über die Shot-Dauer (OTIO Still-Hold-Video).
    still_image_dynamic_zoom_enabled: bool = False
    still_image_dynamic_zoom_factor: float = Field(
        default=DEFAULT_STILL_DYNAMIC_ZOOM_FACTOR,
        ge=STILL_DYNAMIC_ZOOM_FACTOR_MIN,
        le=STILL_DYNAMIC_ZOOM_FACTOR_MAX,
    )
    # Cover-Fill 16:9 + horizontaler Schwenk (kein Letterbox / keine schwarzen Ränder).
    # Default: abwechselnd L→R / R→L — nur Pan, kein Zoom.
    still_image_pan_mode: str = STILL_PAN_MODE_ALTERNATE
    still_image_pan_travel: float = Field(
        default=DEFAULT_STILL_PAN_TRAVEL,
        ge=STILL_PAN_TRAVEL_MIN,
        le=STILL_PAN_TRAVEL_MAX,
    )
    # Cover+Pan in diesem Aspect-Fenster (Breite/Höhe); außerhalb Paper-Edge.
    still_image_pan_min_aspect: float = Field(
        default=DEFAULT_STILL_PAN_MIN_ASPECT, ge=1.0, le=3.0
    )
    still_image_pan_max_aspect: float = Field(
        default=DEFAULT_STILL_PAN_MAX_ASPECT, ge=1.0, le=4.0
    )


def default_cut_plan_options() -> CutPlanOptions:
    return CutPlanOptions()


def _clamp_float(value: Any, *, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _normalize_mode(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower()
    return text if text in TIMING_MODE_CHOICES else default


def _normalize_still_pan_mode(value: Any, *, default: str = STILL_PAN_MODE_OFF) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    aliases = {
        "left_to_right": STILL_PAN_MODE_LTR,
        "left→right": STILL_PAN_MODE_LTR,
        "right_to_left": STILL_PAN_MODE_RTL,
        "right→left": STILL_PAN_MODE_RTL,
        "both": STILL_PAN_MODE_ALTERNATE,
        "lr": STILL_PAN_MODE_LTR,
        "rl": STILL_PAN_MODE_RTL,
    }
    text = aliases.get(text, text)
    return text if text in STILL_PAN_MODE_CHOICES else default


def resolve_still_pan_direction(
    mode: str,
    *,
    shot_id: str = "",
    shot_index: int = 0,
) -> str | None:
    """Liefert ``ltr``/``rtl`` oder ``None`` wenn Pan aus ist."""
    del shot_id  # Alternate nutzt Timeline-Index, nicht shot_id-Hash.
    normalized = _normalize_still_pan_mode(mode)
    if normalized == STILL_PAN_MODE_OFF:
        return None
    if normalized == STILL_PAN_MODE_LTR:
        return STILL_PAN_MODE_LTR
    if normalized == STILL_PAN_MODE_RTL:
        return STILL_PAN_MODE_RTL
    # alternate: Timeline-Reihenfolge — gerade L→R, ungerade R→L.
    parity = int(shot_index) % 2
    return STILL_PAN_MODE_LTR if parity == 0 else STILL_PAN_MODE_RTL


def _normalize_cut_plan_mode(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower()
    return text if text in CUT_PLAN_MODE_CHOICES else default


def _normalize_unified_cut_style(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    aliases = {
        "keyword": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        "keywordsync": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        "buzzword": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        "keywordflow": UNIFIED_CUT_STYLE_KEYWORD_FLOW,
        "semantic_keyword_flow": UNIFIED_CUT_STYLE_KEYWORD_FLOW,
        "semantickeywordflow": UNIFIED_CUT_STYLE_KEYWORD_FLOW,
        "keywordflowfree": UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
        "keyword_flow_free": UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
        "free_keyword_flow": UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
        "freekeywordflow": UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
    }
    text = aliases.get(text, text)
    return text if text in UNIFIED_CUT_STYLE_CHOICES else default


def is_keyword_sync_unified_style(options: CutPlanOptions | None) -> bool:
    """True wenn Unified Keyword-Sync aktiv (separater Prompt; Settings gelten)."""
    if options is None:
        return False
    return (
        str(options.unified_cut_style or "").strip().lower()
        == UNIFIED_CUT_STYLE_KEYWORD_SYNC
    )


def is_keyword_flow_unified_style(options: CutPlanOptions | None) -> bool:
    """True wenn Unified Keyword-Flow aktiv (context-first, ohne Pausenverlängerung)."""
    if options is None:
        return False
    return (
        str(options.unified_cut_style or "").strip().lower()
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )


def is_keyword_flow_free_unified_style(options: CutPlanOptions | None) -> bool:
    """True wenn Unified Keyword-Flow-Free aktiv (kontinuierlicher Wortfluss)."""
    if options is None:
        return False
    return (
        str(options.unified_cut_style or "").strip().lower()
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE
    )


def uses_keyword_onset_timing_rules(options: CutPlanOptions | None) -> bool:
    """True für Keyword Flow und Keyword Flow Free (gemeinsame Onset-Pipeline).

    Additive helper: bestehendes ``is_keyword_flow_unified_style`` bleibt unverändert
    und trifft nur ``keyword_flow``.
    """
    return is_keyword_flow_unified_style(options) or is_keyword_flow_free_unified_style(
        options
    )


def plan_has_unsupported_keyword_flow_pause_directives(plan: object | None) -> bool:
    """True wenn Plan nicht-leere Pause-Directives enthält (KF: veraltet)."""
    if plan is None:
        return False
    for directive in list(getattr(plan, "pause_directives", None) or []):
        function = str(getattr(directive, "pause_function", "") or "").strip().lower()
        if function and function != "no_pause":
            return True
        # Auch Roh-Dicts aus Persistenz abdecken.
        if isinstance(directive, dict):
            function = str(directive.get("pause_function") or "").strip().lower()
            if function and function != "no_pause":
                return True
    return False


def _normalize_payload(raw: dict[str, Any]) -> CutPlanOptions:
    defaults = default_cut_plan_options()
    background = str(
        raw.get(
            "still_image_background_style",
            defaults.still_image_background_style,
        )
        or defaults.still_image_background_style
    ).strip().lower()
    if background not in STILL_BACKGROUND_CHOICES:
        background = defaults.still_image_background_style

    shot_min = _clamp_float(
        raw.get("shot_min_sec", defaults.shot_min_sec),
        default=defaults.shot_min_sec,
        lo=0.4,
        hi=60.0,
    )
    shot_max = _clamp_float(
        raw.get("shot_max_sec", defaults.shot_max_sec),
        default=defaults.shot_max_sec,
        lo=0.4,
        hi=120.0,
    )
    if shot_max < shot_min:
        shot_max = shot_min

    options = CutPlanOptions(
        schema_version=str(raw.get("schema_version") or defaults.schema_version),
        cut_plan_mode=_normalize_cut_plan_mode(  # type: ignore[arg-type]
            raw.get("cut_plan_mode", defaults.cut_plan_mode),
            default=defaults.cut_plan_mode,
        ),
        unified_cut_style=_normalize_unified_cut_style(  # type: ignore[arg-type]
            raw.get("unified_cut_style", defaults.unified_cut_style),
            default=defaults.unified_cut_style,
        ),
        keyword_flow_allow_onset_overflow=bool(
            raw.get(
                "keyword_flow_allow_onset_overflow",
                defaults.keyword_flow_allow_onset_overflow,
            )
        ),
        enable_unified_mini_repair=bool(
            raw.get(
                "enable_unified_mini_repair",
                defaults.enable_unified_mini_repair,
            )
        ),
        unified_mini_repair_threshold=_clamp_float(
            raw.get(
                "unified_mini_repair_threshold",
                defaults.unified_mini_repair_threshold,
            ),
            default=defaults.unified_mini_repair_threshold,
            lo=0.0,
            hi=1.0,
        ),
        llm_cut_model=str(raw.get("llm_cut_model") or "").strip(),
        sfx_planner_model=str(
            raw.get("sfx_planner_model", defaults.sfx_planner_model)
            or defaults.sfx_planner_model
        ).strip()
        or defaults.sfx_planner_model,
        max_sfx_per_chapter=_clamp_int(
            raw.get("max_sfx_per_chapter", defaults.max_sfx_per_chapter),
            default=defaults.max_sfx_per_chapter,
            lo=MAX_SFX_PER_CHAPTER_MIN,
            hi=MAX_SFX_PER_CHAPTER_MAX,
        ),
        elevenlabs_music_count=_clamp_int(
            raw.get("elevenlabs_music_count", defaults.elevenlabs_music_count),
            default=defaults.elevenlabs_music_count,
            lo=ELEVENLABS_MUSIC_COUNT_MIN,
            hi=ELEVENLABS_MUSIC_COUNT_MAX,
        ),
        include_middle_frames=bool(
            raw.get("include_middle_frames", defaults.include_middle_frames)
        ),
        max_middle_frames_per_chapter=_clamp_int(
            raw.get(
                "max_middle_frames_per_chapter",
                defaults.max_middle_frames_per_chapter,
            ),
            default=defaults.max_middle_frames_per_chapter,
            lo=1,
            hi=200,
        ),
        max_candidates_per_gap=_clamp_int(
            raw.get("max_candidates_per_gap", defaults.max_candidates_per_gap),
            default=defaults.max_candidates_per_gap,
            lo=1,
            hi=20,
        ),
        max_full_download_attempts_per_gap=_clamp_int(
            raw.get(
                "max_full_download_attempts_per_gap",
                defaults.max_full_download_attempts_per_gap,
            ),
            default=defaults.max_full_download_attempts_per_gap,
            lo=1,
            hi=3,
        ),
        shot_min_sec=shot_min,
        shot_max_sec=shot_max,
        video_head_trim_sec=_clamp_float(
            raw.get("video_head_trim_sec", defaults.video_head_trim_sec),
            default=defaults.video_head_trim_sec,
            lo=0.0,
            hi=10.0,
        ),
        max_asset_usage=_clamp_int(
            raw.get("max_asset_usage", defaults.max_asset_usage),
            default=defaults.max_asset_usage,
            lo=1,
            hi=50,
        ),
        min_asset_reuse_distance_shots=_clamp_int(
            raw.get(
                "min_asset_reuse_distance_shots",
                defaults.min_asset_reuse_distance_shots,
            ),
            default=defaults.min_asset_reuse_distance_shots,
            lo=0,
            hi=100,
        ),
        voiceover_preroll_sec=_clamp_float(
            raw.get("voiceover_preroll_sec", defaults.voiceover_preroll_sec),
            default=defaults.voiceover_preroll_sec,
            lo=0.0,
            hi=30.0,
        ),
        voiceover_preroll_mode=_normalize_mode(  # type: ignore[arg-type]
            raw.get("voiceover_preroll_mode", defaults.voiceover_preroll_mode),
            default=defaults.voiceover_preroll_mode,
        ),
        voiceover_postroll_sec=_clamp_float(
            raw.get("voiceover_postroll_sec", defaults.voiceover_postroll_sec),
            default=defaults.voiceover_postroll_sec,
            lo=0.0,
            hi=60.0,
        ),
        voiceover_postroll_mode=_normalize_mode(  # type: ignore[arg-type]
            raw.get("voiceover_postroll_mode", defaults.voiceover_postroll_mode),
            default=defaults.voiceover_postroll_mode,
        ),
        intro_voiceover_preroll_sec=_clamp_float(
            raw.get(
                "intro_voiceover_preroll_sec",
                defaults.intro_voiceover_preroll_sec,
            ),
            default=defaults.intro_voiceover_preroll_sec,
            lo=0.0,
            hi=30.0,
        ),
        intro_voiceover_postroll_sec=_clamp_float(
            raw.get(
                "intro_voiceover_postroll_sec",
                defaults.intro_voiceover_postroll_sec,
            ),
            default=defaults.intro_voiceover_postroll_sec,
            lo=0.0,
            hi=60.0,
        ),
        intro_voiceover_postroll_min_sec=_clamp_float(
            raw.get(
                "intro_voiceover_postroll_min_sec",
                defaults.intro_voiceover_postroll_min_sec,
            ),
            default=defaults.intro_voiceover_postroll_min_sec,
            lo=0.0,
            hi=60.0,
        ),
        intro_voiceover_postroll_max_sec=_clamp_float(
            raw.get(
                "intro_voiceover_postroll_max_sec",
                defaults.intro_voiceover_postroll_max_sec,
            ),
            default=defaults.intro_voiceover_postroll_max_sec,
            lo=0.0,
            hi=60.0,
        ),
        short_asset_tolerance_sec=_clamp_float(
            raw.get("short_asset_tolerance_sec", defaults.short_asset_tolerance_sec),
            default=defaults.short_asset_tolerance_sec,
            lo=0.0,
            hi=30.0,
        ),
        folder_title_enabled=bool(
            raw.get("folder_title_enabled", defaults.folder_title_enabled)
        ),
        folder_title_font=str(
            raw.get("folder_title_font", defaults.folder_title_font)
            or defaults.folder_title_font
        ).strip()
        or defaults.folder_title_font,
        folder_title_duration_sec=_clamp_float(
            raw.get("folder_title_duration_sec", defaults.folder_title_duration_sec),
            default=defaults.folder_title_duration_sec,
            lo=0.5,
            hi=30.0,
        ),
        folder_title_font_size=_clamp_float(
            raw.get("folder_title_font_size", defaults.folder_title_font_size),
            default=defaults.folder_title_font_size,
            lo=0.0,
            hi=400.0,
        ),
        folder_title_fade_in_sec=_clamp_float(
            raw.get("folder_title_fade_in_sec", defaults.folder_title_fade_in_sec),
            default=defaults.folder_title_fade_in_sec,
            lo=0.0,
            hi=10.0,
        ),
        folder_title_fade_out_sec=_clamp_float(
            raw.get("folder_title_fade_out_sec", defaults.folder_title_fade_out_sec),
            default=defaults.folder_title_fade_out_sec,
            lo=0.0,
            hi=10.0,
        ),
        still_image_style_enabled=bool(
            raw.get("still_image_style_enabled", defaults.still_image_style_enabled)
        ),
        still_image_zoom=_clamp_float(
            raw.get("still_image_zoom", defaults.still_image_zoom),
            default=defaults.still_image_zoom,
            lo=0.05,
            hi=1.0,
        ),
        still_image_background_style=background,
        still_image_dynamic_zoom_enabled=bool(
            raw.get(
                "still_image_dynamic_zoom_enabled",
                defaults.still_image_dynamic_zoom_enabled,
            )
        ),
        still_image_dynamic_zoom_factor=_clamp_float(
            raw.get(
                "still_image_dynamic_zoom_factor",
                defaults.still_image_dynamic_zoom_factor,
            ),
            default=defaults.still_image_dynamic_zoom_factor,
            lo=STILL_DYNAMIC_ZOOM_FACTOR_MIN,
            hi=STILL_DYNAMIC_ZOOM_FACTOR_MAX,
        ),
        still_image_pan_mode=_normalize_still_pan_mode(
            raw.get("still_image_pan_mode", defaults.still_image_pan_mode)
        ),
        still_image_pan_travel=_clamp_float(
            raw.get("still_image_pan_travel", defaults.still_image_pan_travel),
            default=defaults.still_image_pan_travel,
            lo=STILL_PAN_TRAVEL_MIN,
            hi=STILL_PAN_TRAVEL_MAX,
        ),
        still_image_pan_min_aspect=_clamp_float(
            raw.get(
                "still_image_pan_min_aspect", defaults.still_image_pan_min_aspect
            ),
            default=defaults.still_image_pan_min_aspect,
            lo=1.0,
            hi=3.0,
        ),
        still_image_pan_max_aspect=_clamp_float(
            raw.get(
                "still_image_pan_max_aspect", defaults.still_image_pan_max_aspect
            ),
            default=defaults.still_image_pan_max_aspect,
            lo=1.0,
            hi=4.0,
        ),
    )
    return _migrate_cut_plan_options(options)


def _migrate_cut_plan_options(options: CutPlanOptions) -> CutPlanOptions:
    """Einmalige Defaults: alte Schwenk-Defaults → aktueller subtiler Wert."""
    updates: dict[str, Any] = {}
    legacy_schema = str(options.schema_version or "") != CUT_PLAN_OPTIONS_SCHEMA_VERSION
    if legacy_schema:
        updates["schema_version"] = CUT_PLAN_OPTIONS_SCHEMA_VERSION
        # Nur frühere Defaults ersetzen — bewusst gesetzte Werte bleiben.
        travel = float(options.still_image_pan_travel)
        if any(abs(travel - legacy) < 1e-9 for legacy in LEGACY_STILL_PAN_TRAVELS):
            updates["still_image_pan_travel"] = DEFAULT_STILL_PAN_TRAVEL
    if options.shot_max_sec < options.shot_min_sec:
        updates["shot_max_sec"] = options.shot_min_sec
    # Intro-Nachlauf: min ≤ preferred ≤ max.
    post_min = float(options.intro_voiceover_postroll_min_sec)
    post_max = float(options.intro_voiceover_postroll_max_sec)
    if post_max < post_min:
        post_min, post_max = post_max, post_min
        updates["intro_voiceover_postroll_min_sec"] = post_min
        updates["intro_voiceover_postroll_max_sec"] = post_max
    preferred = float(options.intro_voiceover_postroll_sec)
    clamped_preferred = max(post_min, min(post_max, preferred))
    if abs(clamped_preferred - preferred) > 1e-9:
        updates["intro_voiceover_postroll_sec"] = clamped_preferred
    if not updates:
        return options
    return options.model_copy(update=updates)


def intro_hold_timings(
    options: CutPlanOptions | None = None,
) -> tuple[float, float, float, float]:
    """Intro Opening/Closing: ``(preroll, postroll_default, post_min, post_max)``."""
    opts = options if options is not None else default_cut_plan_options()
    preroll = max(0.0, float(opts.intro_voiceover_preroll_sec))
    post_min = max(0.0, float(opts.intro_voiceover_postroll_min_sec))
    post_max = max(post_min, float(opts.intro_voiceover_postroll_max_sec))
    post_default = max(
        post_min, min(post_max, float(opts.intro_voiceover_postroll_sec))
    )
    return preroll, post_default, post_min, post_max


def load_cut_plan_options(project: Project) -> CutPlanOptions:
    path = cut_plan_options_path(project)
    if not path.is_file():
        from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
            default_cut_plan_options_for_project,
        )

        return default_cut_plan_options_for_project(project)
    loaded = load_model(path, CutPlanOptions)
    if loaded is None:
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return _normalize_payload(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        return default_cut_plan_options()
    return _migrate_cut_plan_options(loaded)


def save_cut_plan_options(project: Project, options: CutPlanOptions) -> CutPlanOptions:
    if options.shot_max_sec < options.shot_min_sec:
        options = options.model_copy(update={"shot_max_sec": options.shot_min_sec})
    write_json(cut_plan_options_path(project), options)
    return options


def _fallback_llm_cut_model_id(project: Project) -> str:
    from otio_app.services.voiceover_generation.model_settings_service import (
        combined_model_id,
        load_model_settings,
    )

    settings = load_model_settings(project)
    for role in (settings.enhanced_rough_cut, settings.enhanced_final_cut):
        combined = combined_model_id(role)
        if combined:
            return combined
    return DEFAULT_LLM_CUT_MODEL


def resolve_llm_cut_model_id(project: Project) -> str:
    """Unified/Intro/Kapitel-LLM-Cut: CutPlanOptions, sonst model_settings."""
    configured = str(load_cut_plan_options(project).llm_cut_model or "").strip()
    if configured:
        return configured
    return _fallback_llm_cut_model_id(project)


def persist_cut_plan_options(project: Project, options: CutPlanOptions) -> CutPlanOptions:
    """Speichert Cut Plan Settings und spiegelt LLM-Cut + SFX in model_settings."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        save_model_settings,
        split_llm_model_id,
    )
    from otio_app.services.voiceover_generation.models import LlmRoleSettings

    llm_id = str(options.llm_cut_model or "").strip()
    if not llm_id:
        llm_id = _fallback_llm_cut_model_id(project)
        options = options.model_copy(update={"llm_cut_model": llm_id})
    saved = save_cut_plan_options(project, options)
    _mirror_llm_cut_roles(project, llm_id)

    sfx_id = str(saved.sfx_planner_model or "").strip()
    if sfx_id:
        settings = load_model_settings(project)
        sfx_prov, sfx_mod = split_llm_model_id(sfx_id)
        save_model_settings(
            project,
            settings.model_copy(
                update={
                    "enhanced_sfx_planner": LlmRoleSettings(
                        provider=sfx_prov, model=sfx_mod
                    )
                }
            ),
        )
    return saved


def persist_llm_cut_model(project: Project, model_id: str) -> CutPlanOptions:
    """Nur das LLM-Cut-Modell schreiben — SFX-Planner in model_settings bleibt."""
    llm_id = str(model_id or "").strip() or _fallback_llm_cut_model_id(project)
    saved = save_cut_plan_options(
        project,
        load_cut_plan_options(project).model_copy(update={"llm_cut_model": llm_id}),
    )
    _mirror_llm_cut_roles(project, llm_id)
    return saved


def _mirror_llm_cut_roles(project: Project, model_id: str) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        save_model_settings,
        split_llm_model_id,
    )
    from otio_app.services.voiceover_generation.models import LlmRoleSettings

    provider, model = split_llm_model_id(model_id)
    role = LlmRoleSettings(provider=provider, model=model)
    settings = load_model_settings(project)
    save_model_settings(
        project,
        settings.model_copy(
            update={"enhanced_rough_cut": role, "enhanced_final_cut": role}
        ),
    )


def format_shot_constraints_for_prompt(options: CutPlanOptions) -> str:
    """Gemeinsamer Prompt-Block für LLM-Lauf 2 und 3."""
    preroll = float(options.voiceover_preroll_sec)
    postroll = float(options.voiceover_postroll_sec)
    if options.voiceover_preroll_mode == TIMING_MODE_LLM:
        preroll_rule = (
            f"- Voice-over preroll (Vorlauf) mode=llm: choose voiceover_preroll_sec in "
            f"[0, {preroll:.1f}]s. Whatever value you choose, every chapter still needs "
            f"an OPENING SHOT that covers that many seconds of picture before VO starts."
        )
    else:
        preroll_rule = (
            f"- Voice-over preroll (Vorlauf) is fixed at {preroll:.1f}s per chapter."
        )
    if options.voiceover_postroll_mode == TIMING_MODE_LLM:
        postroll_rule = (
            f"- Voice-over postroll (Nachlauf) mode=llm: choose voiceover_postroll_sec in "
            f"[0, {postroll:.1f}]s. Whatever value you choose, every chapter still needs "
            f"a CLOSING SHOT that continues that many seconds after VO ends."
        )
    else:
        postroll_rule = (
            f"- Voice-over postroll (Nachlauf) is fixed at {postroll:.1f}s per chapter."
        )

    opening_closing_rules = f"""
OPENING / CLOSING SHOTS PER CHAPTER (BINDING — closes edge gaps):

- Plan shots for EVERY chapter/folder that has narration. Skipping a chapter
  is forbidden (Python fails closed / preview-only export).
- For EVERY chapter/folder, plan an OPENING SHOT at the chapter start:
  - It is a dedicated first shot that runs for the preroll/Vorlauf of
    {preroll:.1f}s (or your chosen llm preroll) at the beginning of the
    chapter — picture before / into the voice-over.
  - It must also cover the chapter's first narration start (no leading visual
    gap while narration is already running).
  - Its asset_id MUST differ from the immediately following shot.
  - Prefer an establishing / orientation / atmosphere asset with enough usable
    duration for that opening beat.
  - Mark it with editorial_function like "opening", "establishing", or
    "chapter_open" when the schema allows free editorial_function text.
- For EVERY chapter/folder, plan a CLOSING SHOT at the chapter end:
  - It is a dedicated last shot that continues for the postroll/Nachlauf of
    {postroll:.1f}s (or your chosen llm postroll) AFTER the voice-over ends.
  - It must also end at the chapter's last narration end (no trailing visual
    gap while narration is still running).
  - Its asset_id MUST differ from the immediately preceding shot.
  - Across chapter boundaries, the closing asset of chapter N must also differ
    from the opening asset of chapter N+1.
  - Choose an asset with enough usable duration (or an intentional hold/still)
    for that tail.
  - Mark it with editorial_function like "closing", "chapter_close", or
    "outro" when the schema allows free editorial_function text.
- ALWAYS also set top-level closing_fallback_asset_id (BINDING reserve closer):
  - A LOCAL ASSETS id that Python Timing may append ONLY if the last planned
    shot ends before VO/audio end (closing coverage shortfall).
  - Prefer a photo/still or a long atmospheric motion clip that can also carry
    the postroll/Nachlauf ({postroll:.1f}s) after VO ends.
  - MUST differ from the last slot's local_asset_id and from the opening asset.
  - If the last shot already covers VO end, Python leaves this unused.
- Do NOT leave the first or last spoken seconds of a chapter without a planned
  shot. Python will fail closed on leading/trailing narration gaps; opening and
  closing shots are how you prevent those gaps.
- Opening/closing shots still obey shot_min/shot_max for the narration-covered
  portion; do not invent one giant shot for the whole chapter.
- Opening and closing shots COUNT toward max asset usage and the asset reuse
  gap — no exemption. closing_fallback_asset_id also counts if Python uses it.
"""

    return f"""
SHOT / ASSET CONSTRAINTS (PROJECT SETTINGS — BINDING):

- Aim for each visual shot to cover roughly {options.shot_min_sec:.1f}s–{options.shot_max_sec:.1f}s of narration time.
- Do not plan a single shot longer than {options.shot_max_sec:.1f}s; split long spans into multiple shots.
- Each LOCAL ASSET / SUPPLEMENT entry includes duration_seconds and description — use both.
- MANDATORY planning usable length for EVERY motion asset:
  planning_usable = max(0, duration_seconds - usable_in_s - {LLM_ASSET_DURATION_SAFETY_SEC:.1f})
  Always subtract this {LLM_ASSET_DURATION_SAFETY_SEC:.1f}s safety margin before judging fit
  (covers frame rounding, closing span drift, and Nachlauf/postroll). Never plan a
  tight fit that only works with the raw usable length.
- Prefer assets whose planning_usable is >= the intended shot span (for first/last
  slots: include Vorlauf/preroll or Nachlauf/postroll in that span).
- Short-asset tolerance: after applying the {LLM_ASSET_DURATION_SAFETY_SEC:.1f}s safety
  margin, an asset may be up to {options.short_asset_tolerance_sec:.1f}s shorter than
  the planned shot. Within that tolerance you may keep the asset — Python will
  shorten that shot and lengthen a neighbor (even past shot_max). Beyond it choose
  another asset, shorten the span, or emit a coverage_gap. Python will NOT freeze-pad / tpad motion video.
- Never plan a motion-video shot longer than planning_usable. There is no video hold.
{preroll_rule}
{postroll_rule}
{opening_closing_rules}
- Do not use the same non-intro asset more than {options.max_asset_usage} times across the whole film.
- Intro assets do not count toward max asset usage.
- Asset reuse gap: when reusing a non-intro asset, leave at least {options.min_asset_reuse_distance_shots} other shots in between (default target: 4). Never place the same non-intro asset on two consecutive shots.
- If no other suitable local asset can satisfy max usage / reuse distance for a
  beat, emit asset_fit \"none\" with coverage_gap_id + needed_visual +
  search_concepts — do NOT place an early reuse just to fill the slot. Python
  demotes illegal early reuses to coverage gaps so you can stock-search.
- Keep Intro chapter coverage complete — do not drop Intro visuals/audio.
"""


def resolve_timing_seconds(
    *,
    mode: str,
    setting_max: float,
    llm_value: float | None,
) -> float:
    """Fester Wert oder LLM-Wert (auf Setting-Max geklemmt)."""
    ceiling = max(0.0, float(setting_max))
    if mode == TIMING_MODE_LLM:
        if llm_value is None:
            return ceiling
        try:
            return max(0.0, min(ceiling, float(llm_value)))
        except (TypeError, ValueError):
            return ceiling
    return ceiling
