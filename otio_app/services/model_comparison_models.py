"""Datenmodelle für LLM-Modellvergleich / Raw-Analyse."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

PLANNING_MODE_MODEL_COMPARISON_RAW = "model_comparison_raw"
PLANNING_MODE_PRODUCTION_CONSTRAINED = "production_constrained"

PREVIEW_STATUS_OK = "OK"
PREVIEW_STATUS_INVALID = "INVALID_PREVIEW"
PREVIEW_STATUS_PARSE_FAILED = "PARSE_FAILED"
PREVIEW_STATUS_SKIPPED = "SKIPPED"


class ModelComparisonPipelineFlags(BaseModel):
    normalize_parts: bool = False
    allocate_with_constraints: bool = False
    allocate_by_text: bool = True
    merge_short_windows: bool = False
    establishing_fallback: bool = False
    apply_edit_plan_rules: bool = False
    retry_loop: bool = False
    build_technical_preview: bool = True
    allow_locked_plan: bool = False
    allow_otio_export: bool = False


class ModelComparisonEffectiveRules(BaseModel):
    planning_mode: str = PLANNING_MODE_MODEL_COMPARISON_RAW
    shot_rules_enabled: bool = False
    shot_min_sec: float = 0.0
    shot_max_sec: float = 0.0
    shot_min_sec_note: str = "documented only, not applied in model_comparison_raw"
    shot_max_sec_note: str = "documented only, not applied in model_comparison_raw"
    max_asset_usage_enabled: bool = False
    max_asset_usage: int | None = None
    max_asset_usage_note: str = "documented only, not applied in model_comparison_raw"
    asset_reuse_rules_enabled: bool = False
    min_asset_reuse_distance_shots: int = 0
    pipeline: ModelComparisonPipelineFlags = Field(default_factory=ModelComparisonPipelineFlags)


class ParsedLlmPart(BaseModel):
    text: str = ""
    motif: str = ""
    asset_path: str | None = None
    proposed_asset_id: str | None = None
    match_quality: str = ""
    visual_intent: str = ""
    reason: str = ""
    confidence: str | None = None
    desired_duration_sec: float | None = None


class ParsedLlmBeat(BaseModel):
    beat_id: str
    parts: list[ParsedLlmPart] = Field(default_factory=list)


class ParsedLlmCandidate(BaseModel):
    beats: list[ParsedLlmBeat] = Field(default_factory=list)
    proposed_part_count: int = 0
    parse_error: str | None = None
    raw_beats: list[dict[str, Any]] = Field(default_factory=list)


class RawLlmResponseDocument(BaseModel):
    provider: str
    model: str
    attempt_number: int = 1
    raw_text: str = ""
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)


class DeltaChangeEntry(BaseModel):
    beat_id: str
    part_index: int = -1
    field: str
    before: Any = None
    after: Any = None
    reason: str
    function_name: str


class LlmVsPythonDeltaDocument(BaseModel):
    changes_count: int = 0
    note: str = ""
    beat_summaries: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[DeltaChangeEntry] = Field(default_factory=list)


class ModelComparisonRunManifest(BaseModel):
    run_id: str
    comparison_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    planning_mode: str = PLANNING_MODE_MODEL_COMPARISON_RAW
    provider: str
    model: str
    folder_name: str
    voiceover_path: str = ""
    inventory_hash: str = ""
    prompt_hash: str = ""
    effective_rules_hash: str = ""
    preview_status: str = PREVIEW_STATUS_SKIPPED
    validation_status: str = "SKIPPED"


class ModelComparisonSummaryRunEntry(BaseModel):
    run_id: str
    provider: str
    model: str
    raw_part_count: int = 0
    final_part_count: int = 0
    asset_changes_count: int = 0
    duration_changes_count: int = 0
    preview_status: str = PREVIEW_STATUS_SKIPPED
    validation_status: str = "SKIPPED"
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    parse_error: str | None = None


class ModelComparisonSummary(BaseModel):
    comparison_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    folder_name: str
    runs: list[ModelComparisonSummaryRunEntry] = Field(default_factory=list)
