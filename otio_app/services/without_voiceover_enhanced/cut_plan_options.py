"""Optionen für Enhanced Cut Plan (LLM-Lauf 2/3 + Python/OTIO)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from otio_app.defaults import (
    CUT_PLAN_DEFAULT_FOLDER_TITLE_DURATION_SEC,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_ENABLED,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT,
    CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT_SIZE,
    CUT_PLAN_DEFAULT_MAX_ASSET_USAGE,
    CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS,
    CUT_PLAN_DEFAULT_SHOT_MAX_SEC,
    CUT_PLAN_DEFAULT_SHOT_MIN_SEC,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
)
from otio_app.models import Project
from otio_app.services.still_image_export_style import (
    DEFAULT_STILL_IMAGE_ZOOM,
    STILL_BACKGROUND_NONE,
    STILL_BACKGROUND_VINTAGE,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import cut_plan_options_path

# Soft cap: zu viele JPEGs pro Kapitel-Call sprengen Kontext/Kosten.
DEFAULT_MAX_MIDDLE_FRAMES_PER_CHAPTER = 40


DEFAULT_MAX_CANDIDATES_PER_GAP = 20
DEFAULT_MAX_FULL_DOWNLOAD_ATTEMPTS_PER_GAP = 3

STILL_BACKGROUND_CHOICES = (
    STILL_BACKGROUND_VINTAGE,
    STILL_BACKGROUND_NONE,
)


class CutPlanOptions(BaseModel):
    schema_version: str = "1.1"
    # Default aus: bisheriges Text-only Verhalten von LLM-Lauf 2.
    include_middle_frames: bool = False
    max_middle_frames_per_chapter: int = Field(
        default=DEFAULT_MAX_MIDDLE_FRAMES_PER_CHAPTER,
        ge=1,
        le=200,
    )
    # Supplement-Funnel: Top-N Kandidaten pro Gap (Text + Thumbnail).
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

    # Shot / Asset (LLM 2+3 + Python-Resolver)
    shot_min_sec: float = Field(default=CUT_PLAN_DEFAULT_SHOT_MIN_SEC, ge=0.4, le=60.0)
    shot_max_sec: float = Field(default=CUT_PLAN_DEFAULT_SHOT_MAX_SEC, ge=0.4, le=120.0)
    video_head_trim_sec: float = Field(
        default=CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC, ge=0.0, le=10.0
    )
    max_asset_usage: int = Field(default=CUT_PLAN_DEFAULT_MAX_ASSET_USAGE, ge=1, le=50)
    min_asset_reuse_distance_shots: int = Field(
        default=CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS, ge=0, le=100
    )

    # Ordner-Titel (nur Python/OTIO — LLM ignoriert)
    folder_title_enabled: bool = CUT_PLAN_DEFAULT_FOLDER_TITLE_ENABLED
    folder_title_font: str = CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT
    folder_title_duration_sec: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_DURATION_SEC, ge=0.5, le=30.0
    )
    folder_title_font_size: float = Field(
        default=CUT_PLAN_DEFAULT_FOLDER_TITLE_FONT_SIZE, ge=0.0, le=400.0
    )

    # Still-Bilder beim OTIO-Export (nur Python)
    still_image_style_enabled: bool = True
    still_image_zoom: float = Field(default=DEFAULT_STILL_IMAGE_ZOOM, ge=0.05, le=1.0)
    still_image_background_style: str = STILL_BACKGROUND_VINTAGE


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
    # Normalize edge cases (max < min) even for valid pydantic loads.
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
    return f"""
SHOT / ASSET CONSTRAINTS (PROJECT SETTINGS — BINDING):

- Aim for each visual shot to cover roughly {options.shot_min_sec:.1f}s–{options.shot_max_sec:.1f}s of narration time.
- Do not plan a single shot longer than {options.shot_max_sec:.1f}s; split long spans into multiple shots.
- Prefer assets whose duration_seconds (when known) is >= the intended shot span.
- If no suitable asset is long enough or fits editorially, create a coverage_gap (rough cut) or choose a shorter span / different asset (final cut).
- Do not use the same non-intro asset more than {options.max_asset_usage} times across the whole film.
- Intro assets do not count toward max asset usage.
- When reusing a non-intro asset, prefer at least {options.min_asset_reuse_distance_shots} other shots in between.
"""
