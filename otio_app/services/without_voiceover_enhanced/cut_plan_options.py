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

TIMING_MODE_FIXED = "fixed"
TIMING_MODE_LLM = "llm"
TimingMode = Literal["fixed", "llm"]
TIMING_MODE_CHOICES: tuple[str, ...] = (TIMING_MODE_FIXED, TIMING_MODE_LLM)

CUT_PLAN_MODE_LEGACY = "legacy"
CUT_PLAN_MODE_UNIFIED = "unified"
CutPlanMode = Literal["legacy", "unified"]
CUT_PLAN_MODE_CHOICES: tuple[str, ...] = (CUT_PLAN_MODE_LEGACY, CUT_PLAN_MODE_UNIFIED)

# Unified-Stil: Rhythmus vs Keyword-Sync (Wort↔Bild).
# Beide Modi erhalten Word-Timestamps + Cut-Settings (inkl. shot_min/max).
UNIFIED_CUT_STYLE_RHYTHM = "rhythm"
UNIFIED_CUT_STYLE_KEYWORD_SYNC = "keyword_sync"
UnifiedCutStyle = Literal["rhythm", "keyword_sync"]
UNIFIED_CUT_STYLE_CHOICES: tuple[str, ...] = (
    UNIFIED_CUT_STYLE_RHYTHM,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
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


class CutPlanOptions(BaseModel):
    schema_version: str = "1.5"
    # Phase 7: Unified (1 LLM) vs Legacy (Rough + Final).
    cut_plan_mode: CutPlanMode = CUT_PLAN_MODE_LEGACY
    # Unified Stil: Rhythmus (Default) oder Keyword-Sync (Wort↔Bild).
    unified_cut_style: UnifiedCutStyle = UNIFIED_CUT_STYLE_RHYTHM
    # Phase 6: optionaler Mini-Repair nach Gap-Merge (Default aus).
    enable_unified_mini_repair: bool = False
    unified_mini_repair_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
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


def _normalize_cut_plan_mode(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower()
    return text if text in CUT_PLAN_MODE_CHOICES else default


def _normalize_unified_cut_style(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    aliases = {
        "keyword": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        "keywordsync": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        "buzzword": UNIFIED_CUT_STYLE_KEYWORD_SYNC,
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

    return CutPlanOptions(
        schema_version=str(raw.get("schema_version") or defaults.schema_version),
        cut_plan_mode=_normalize_cut_plan_mode(  # type: ignore[arg-type]
            raw.get("cut_plan_mode", defaults.cut_plan_mode),
            default=defaults.cut_plan_mode,
        ),
        unified_cut_style=_normalize_unified_cut_style(  # type: ignore[arg-type]
            raw.get("unified_cut_style", defaults.unified_cut_style),
            default=defaults.unified_cut_style,
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
    )


def load_cut_plan_options(project: Project) -> CutPlanOptions:
    path = cut_plan_options_path(project)
    if not path.is_file():
        return default_cut_plan_options()
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
    if loaded.shot_max_sec < loaded.shot_min_sec:
        return loaded.model_copy(update={"shot_max_sec": loaded.shot_min_sec})
    return loaded


def save_cut_plan_options(project: Project, options: CutPlanOptions) -> CutPlanOptions:
    if options.shot_max_sec < options.shot_min_sec:
        options = options.model_copy(update={"shot_max_sec": options.shot_min_sec})
    write_json(cut_plan_options_path(project), options)
    return options


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
- Do NOT leave the first or last spoken seconds of a chapter without a planned
  shot. Python will fail closed on leading/trailing narration gaps; opening and
  closing shots are how you prevent those gaps.
- Opening/closing shots still obey shot_min/shot_max for the narration-covered
  portion; do not invent one giant shot for the whole chapter.
- Opening and closing shots COUNT toward max asset usage and the asset reuse
  gap — no exemption.
"""

    return f"""
SHOT / ASSET CONSTRAINTS (PROJECT SETTINGS — BINDING):

- Aim for each visual shot to cover roughly {options.shot_min_sec:.1f}s–{options.shot_max_sec:.1f}s of narration time.
- Do not plan a single shot longer than {options.shot_max_sec:.1f}s; split long spans into multiple shots.
- Each LOCAL ASSET / SUPPLEMENT entry includes duration_seconds and description — use both.
- Prefer assets whose duration_seconds (when known) is >= the intended shot span.
- Short-asset tolerance: an asset may be up to {options.short_asset_tolerance_sec:.1f}s shorter than the planned shot. Within that tolerance you may keep the asset — Python will shorten that shot and lengthen a neighbor (even past shot_max). Beyond it choose another asset, shorten the span, or emit a coverage_gap. Python will NOT freeze-pad / tpad motion video.
- Never plan a motion-video shot longer than the asset's usable length. There is no video hold.
{preroll_rule}
{postroll_rule}
{opening_closing_rules}
- Do not use the same non-intro asset more than {options.max_asset_usage} times across the whole film.
- Intro assets do not count toward max asset usage.
- Asset reuse gap: when reusing a non-intro asset, leave at least {options.min_asset_reuse_distance_shots} other shots in between (default target: 4). Never place the same non-intro asset on two consecutive shots.
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
