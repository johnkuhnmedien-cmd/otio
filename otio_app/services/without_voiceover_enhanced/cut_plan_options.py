"""Optionen für Enhanced Cut Plan (LLM-Lauf 2/3)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import cut_plan_options_path

# Soft cap: zu viele JPEGs pro Kapitel-Call sprengen Kontext/Kosten.
DEFAULT_MAX_MIDDLE_FRAMES_PER_CHAPTER = 40


DEFAULT_MAX_CANDIDATES_PER_GAP = 20
DEFAULT_MAX_FULL_DOWNLOAD_ATTEMPTS_PER_GAP = 3


class CutPlanOptions(BaseModel):
    schema_version: str = "1.0"
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


def default_cut_plan_options() -> CutPlanOptions:
    return CutPlanOptions()


def _normalize_payload(raw: dict[str, Any]) -> CutPlanOptions:
    defaults = default_cut_plan_options()
    return CutPlanOptions(
        schema_version=str(raw.get("schema_version") or defaults.schema_version),
        include_middle_frames=bool(
            raw.get("include_middle_frames", defaults.include_middle_frames)
        ),
        max_middle_frames_per_chapter=int(
            raw.get(
                "max_middle_frames_per_chapter",
                defaults.max_middle_frames_per_chapter,
            )
            or defaults.max_middle_frames_per_chapter
        ),
        max_candidates_per_gap=int(
            raw.get(
                "max_candidates_per_gap",
                defaults.max_candidates_per_gap,
            )
            or defaults.max_candidates_per_gap
        ),
        max_full_download_attempts_per_gap=int(
            raw.get(
                "max_full_download_attempts_per_gap",
                defaults.max_full_download_attempts_per_gap,
            )
            or defaults.max_full_download_attempts_per_gap
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
    return loaded


def save_cut_plan_options(project: Project, options: CutPlanOptions) -> CutPlanOptions:
    write_json(cut_plan_options_path(project), options)
    return options
