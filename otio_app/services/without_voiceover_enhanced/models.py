"""Datenmodelle für without_voiceover_enhanced MVP-Artefakte."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class NarrationAnchor(BaseModel):
    segment_id: str
    offset_seconds: float = 0.0


class ScriptSegment(BaseModel):
    segment_id: str
    text: str
    sequence_index: int
    semantic_function: str = "narration"
    visual_intent_ids: list[str] = Field(default_factory=list)
    fact_check_required: bool = False
    text_changed: bool = False
    # Dramaturgie-Kapitel — leer bei älteren Drafts ohne Ordner-Zuordnung.
    folder_name: str = ""
    folder_order_index: int = 0


class VisualIntent(BaseModel):
    intent_id: str
    description: str
    subject: str = ""
    location: str = ""
    preferred_media_type: str = "video"
    folder_name: str = ""


class VisualBeat(BaseModel):
    beat_id: str
    description: str
    related_segment_ids: list[str] = Field(default_factory=list)
    visual_intent_ids: list[str] = Field(default_factory=list)


class CoverageNeed(BaseModel):
    need_id: str
    visual_intent_id: str = ""
    subject: str = ""
    reason: str = ""
    search_queries: list[str] = Field(default_factory=list)


class FactCheckHint(BaseModel):
    hint_id: str
    related_segment_id: str = ""
    claim: str
    status: str = "fact_check_required"
    note: str = ""


class EnhancedScriptDocument(BaseModel):
    schema_version: str = "enhanced-script-v1"
    script_version: str = "script-v1"
    script_status: str = "draft"  # draft | locked
    narration_full: str = ""
    segments: list[ScriptSegment] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    visual_intents: list[VisualIntent] = Field(default_factory=list)
    coverage_needs: list[CoverageNeed] = Field(default_factory=list)
    fact_check_hints: list[FactCheckHint] = Field(default_factory=list)
    forbidden_phrases_found: list[str] = Field(default_factory=list)
    locked_at: Optional[str] = None
    source_brief_hash: str = ""


class SegmentTiming(BaseModel):
    segment_id: str
    script_version: str
    audio_path: str
    duration_seconds: float
    audio_status: str = "valid"  # valid | stale | missing | unreadable


class SegmentTimingsDocument(BaseModel):
    schema_version: str = "enhanced-segment-timings-v1"
    script_version: str
    segments: list[SegmentTiming] = Field(default_factory=list)


class PauseDirective(BaseModel):
    after_segment_id: str
    pause_function: str
    duration_class: str
    visual_behavior: str = "editorial_choice"
    editorial_reason: str = ""


class NarrationTimelineEntry(BaseModel):
    segment_id: str
    start_seconds: float
    end_seconds: float
    pause_after_seconds: float = 0.0
    next_segment_start_seconds: Optional[float] = None


class NarrationTimelineDocument(BaseModel):
    schema_version: str = "enhanced-narration-timeline-v1"
    script_version: str
    total_duration_seconds: float
    entries: list[NarrationTimelineEntry] = Field(default_factory=list)


class RoughShot(BaseModel):
    shot_id: str
    narration_start_anchor: NarrationAnchor
    narration_end_anchor: NarrationAnchor
    visual_intent_id: str = ""
    asset_id: Optional[str] = None
    candidate_asset_ids: list[str] = Field(default_factory=list)
    editorial_function: str = "orientation"
    editorial_reason: str = ""
    visual_behavior: str = "hold"
    may_overlap_pause: bool = False


class RoughCutPlanDocument(BaseModel):
    schema_version: str = "enhanced-rough-cut-v1"
    script_version: str
    pause_directives: list[PauseDirective] = Field(default_factory=list)
    shots: list[RoughShot] = Field(default_factory=list)


class CoverageGap(BaseModel):
    gap_id: str
    related_shot_ids: list[str] = Field(default_factory=list)
    visual_intent_id: str = ""
    subject: str = ""
    location: str = ""
    action: str = ""
    editorial_function: str = "orientation"
    preferred_media_type: str = "video"
    fallback_media_type: str = "photo"
    minimum_resolution: str = "1920x1080"
    priority: str = "high"
    reason: str = ""
    search_queries: list[str] = Field(default_factory=list)


class CoverageGapsDocument(BaseModel):
    schema_version: str = "enhanced-coverage-gaps-v1"
    script_version: str
    gaps: list[CoverageGap] = Field(default_factory=list)


class StockCandidate(BaseModel):
    candidate_id: str
    provider: str
    provider_asset_id: str = ""
    title: str = ""
    media_type: str = "photo"
    creator: str = ""
    source_page: str = ""
    preview_url: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    license: Optional[str] = None
    attribution: Optional[str] = None
    selected: bool = False
    gap_id: str = ""
    # R1: lokale Mediendatei vor Final Cut / OTIO erforderlich.
    local_media_path: Optional[str] = None
    media_validation_status: str = "selected"
    # selected | local_media_missing | local_media_invalid | export_ready
    media_validation_error: Optional[str] = None


class StockSearchResultsDocument(BaseModel):
    schema_version: str = "enhanced-stock-search-v1"
    script_version: str
    provider_status: dict[str, str] = Field(default_factory=dict)
    candidates: list[StockCandidate] = Field(default_factory=list)
    message: str = ""


class AcceptedSupplementsDocument(BaseModel):
    schema_version: str = "enhanced-accepted-supplements-v1"
    script_version: str
    supplements: list[StockCandidate] = Field(default_factory=list)


class FinalShot(BaseModel):
    shot_id: str
    narration_start_anchor: NarrationAnchor
    narration_end_anchor: NarrationAnchor
    asset_id: str
    editorial_function: str = "narration_support"
    editorial_reason: str = ""
    transition_behavior: str = "straight_cut"
    source_range_intent: str = "representative_middle_section"
    may_overlap_pause: bool = False


class FinalCutPlanDocument(BaseModel):
    schema_version: str = "enhanced-final-cut-v1"
    script_version: str
    shots: list[FinalShot] = Field(default_factory=list)


class ResolvedShot(BaseModel):
    shot_id: str
    asset_id: str
    timeline_start_seconds: float
    timeline_end_seconds: float
    source_start_seconds: float
    source_end_seconds: float
    editorial_function: str = ""
    may_overlap_pause: bool = False


class ResolvedAudioSegment(BaseModel):
    segment_id: str
    audio_path: str
    timeline_start_seconds: float
    timeline_end_seconds: float
    pause_after_seconds: float = 0.0


class ResolvedTimelineDocument(BaseModel):
    schema_version: str = "enhanced-resolved-timeline-v1"
    script_version: str
    fps: float = 25.0
    total_duration_seconds: float
    audio_segments: list[ResolvedAudioSegment] = Field(default_factory=list)
    shots: list[ResolvedShot] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def model_to_jsonable(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
