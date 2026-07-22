"""Datenmodelle für without_voiceover_enhanced MVP-Artefakte."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class NarrationAnchor(BaseModel):
    """Absolute/sekundenbasierte Anker (Final Cut / Timeline-Resolver).

    Mit ``sentence_id`` ist ``offset_seconds`` relativ zum Satzanfang
    (nicht zum Segment). Ohne ``sentence_id`` wie bisher relativ zum Segment.
    """

    segment_id: str
    offset_seconds: float = 0.0
    sentence_id: Optional[str] = None


class EditorialAnchor(BaseModel):
    """Redaktionelle Anker aus LLM-Lauf 2 (keine Sekunden/Frames)."""

    type: str = "segment"  # segment | pause | sentence
    segment_id: str = ""
    after_segment_id: Optional[str] = None
    sentence_id: Optional[str] = None
    position: str = "start"  # start|early|middle|late|end


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
    timestamps_path: str = ""
    alignment_path: str = ""


class SegmentTimingsDocument(BaseModel):
    schema_version: str = "enhanced-segment-timings-v1"
    script_version: str
    segments: list[SegmentTiming] = Field(default_factory=list)


class SentenceTiming(BaseModel):
    """Satzzeiten relativ zur Segment-MP3 (ElevenLabs Character-Timestamps)."""

    sentence_id: str
    segment_id: str
    text: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class SegmentAlignment(BaseModel):
    """Abgeleitetes Alignment eines vertonten Segments inkl. Satzzeiten."""

    segment_id: str
    script_version: str
    audio_path: str
    audio_duration_seconds: float
    tts_text: str
    timestamps_path: str
    alignment_source: str = "elevenlabs_timestamps"
    sentences: list[SentenceTiming] = Field(default_factory=list)
    alignment_warnings: list[str] = Field(default_factory=list)


class SegmentAlignmentsDocument(BaseModel):
    """Index aller Segment-Alignments für Cut-Plan / LLM-Nutzung."""

    schema_version: str = "enhanced-segment-alignments-v1"
    script_version: str
    segments: list[SegmentAlignment] = Field(default_factory=list)


class PauseDirective(BaseModel):
    after_segment_id: str = ""
    # Pause an Satzgrenze INNERHALB eines Segments (optional).
    after_sentence_id: Optional[str] = None
    pause_function: str
    duration_class: str
    visual_behavior: str = "editorial_choice"
    editorial_reason: str = ""


class IntraPauseMarker(BaseModel):
    """Aufgezogene Pause INNERHALB eines Segments (Mitte der Original-Stille)."""

    after_sentence_id: str
    source_split_seconds: float
    pause_seconds: float


class NarrationTimelineEntry(BaseModel):
    segment_id: str
    start_seconds: float
    end_seconds: float
    pause_after_seconds: float = 0.0
    next_segment_start_seconds: Optional[float] = None
    # Rohdauer der Segment-MP3 (ohne aufgezogene Intra-Pausen). Legacy: None.
    audio_duration_seconds: Optional[float] = None
    intra_pauses: list[IntraPauseMarker] = Field(default_factory=list)


class NarrationTimelineDocument(BaseModel):
    schema_version: str = "enhanced-narration-timeline-v1"
    script_version: str
    total_duration_seconds: float
    entries: list[NarrationTimelineEntry] = Field(default_factory=list)


class RoughShot(BaseModel):
    shot_id: str
    start_anchor: EditorialAnchor = Field(default_factory=EditorialAnchor)
    end_anchor: EditorialAnchor = Field(default_factory=EditorialAnchor)
    narrative_function: str = "orientation"
    visual_intent: str = ""
    local_asset_id: Optional[str] = None
    asset_fit: str = "none"
    asset_fit_reason: str = ""
    continuity_notes: str = ""
    coverage_gap_id: Optional[str] = None
    # mid_sentence | sentence_boundary | in_pause — für Rhythmus-Quoten
    start_cut_alignment: str = ""
    # Compat bridge for older UI / Final-Cut consumers:
    narration_start_anchor: NarrationAnchor = Field(
        default_factory=lambda: NarrationAnchor(segment_id="")
    )
    narration_end_anchor: NarrationAnchor = Field(
        default_factory=lambda: NarrationAnchor(segment_id="")
    )
    visual_intent_id: str = ""
    asset_id: Optional[str] = None
    candidate_asset_ids: list[str] = Field(default_factory=list)
    editorial_function: str = "orientation"
    editorial_reason: str = ""
    visual_behavior: str = "hold"
    may_overlap_pause: bool = False


class RoughCutPlanDocument(BaseModel):
    schema_version: str = "enhanced-rough-cut-v2"
    script_version: str
    pause_directives: list[PauseDirective] = Field(default_factory=list)
    shots: list[RoughShot] = Field(default_factory=list)


class CoverageGap(BaseModel):
    gap_id: str
    related_shot_ids: list[str] = Field(default_factory=list)
    needed_visual: str = ""
    editorial_purpose: str = ""
    preferred_media_type: str = "video"
    search_concepts: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    fact_check_required: bool = False
    covered_sentence_ids: list[str] = Field(default_factory=list)
    desired_motion: str = ""
    desired_framing: str = ""
    # Legacy / compat fields (still filled when useful):
    visual_intent_id: str = ""
    subject: str = ""
    location: str = ""
    action: str = ""
    editorial_function: str = "orientation"
    fallback_media_type: str = "photo"
    minimum_resolution: str = "1920x1080"
    priority: str = "high"
    reason: str = ""
    search_queries: list[str] = Field(default_factory=list)


class CoverageGapsDocument(BaseModel):
    schema_version: str = "enhanced-coverage-gaps-v2"
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
    download_url: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    license: Optional[str] = None
    license_url: Optional[str] = None
    attribution: Optional[str] = None
    selected: bool = False
    gap_id: str = ""
    # R1: lokale Mediendatei vor Final Cut / OTIO erforderlich.
    local_media_path: Optional[str] = None
    media_validation_status: str = "selected"
    # selected | local_media_missing | local_media_invalid |
    # license_review_required | export_ready
    media_validation_error: Optional[str] = None
    # True = aus Supplement-Funnel übernommen (R3: Lizenz nur informativ).
    funnel_managed: bool = False
    # Informativ: complete | partial | missing (blockiert export_ready nicht).
    license_metadata_status: str = ""


class StockSearchResultsDocument(BaseModel):
    schema_version: str = "enhanced-stock-search-v1"
    script_version: str
    provider_status: dict[str, str] = Field(default_factory=dict)
    candidates: list[StockCandidate] = Field(default_factory=list)
    message: str = ""


class SupplementResolveAttempt(BaseModel):
    gap_id: str
    candidate_id: str
    provider: str = ""
    status: str = ""  # PASS | FAIL | WEAK_PASS | DOWNLOAD_FAILED | ERROR | SKIPPED
    reason: str = ""
    score: float = 0.0
    description: str = ""
    local_media_path: Optional[str] = None
    frames_used: list[str] = Field(default_factory=list)
    inventory_folder: str = ""


class SupplementResolveGapResult(BaseModel):
    gap_id: str
    filled: bool = False
    accepted_candidate_id: Optional[str] = None
    attempts: list[SupplementResolveAttempt] = Field(default_factory=list)


class SupplementResolveReport(BaseModel):
    schema_version: str = "enhanced-supplement-resolve-v1"
    script_version: str = ""
    max_candidates_per_gap: int = 5
    gaps: list[SupplementResolveGapResult] = Field(default_factory=list)
    filled_gap_ids: list[str] = Field(default_factory=list)
    unfilled_gap_ids: list[str] = Field(default_factory=list)
    message: str = ""
    stopped: bool = False


# Additive Funnel-Status (ohne bestehende Provider-Status zu ändern).
FUNNEL_STATUSES = (
    "discovered",
    "text_ranked",
    "thumbnail_pending",
    "thumbnail_unavailable",
    "thumbnail_scored",
    "finalist",
    "download_pending",
    "download_failed",
    "local_media_invalid",
    "technically_valid",
    "license_metadata_incomplete",
    # Historische Statuswerte bleiben lesbar (kein Auto-Hauptweg mehr).
    "full_review_rejected",
    "manual_review_required",
    "review_ready",
    "selected",
    "license_review_required",
    "export_ready",
)


class FunnelTextScores(BaseModel):
    text_relevance: int = 0
    metadata_quality: int = 0
    media_type_fit: int = 0
    license_metadata_quality: int = 0
    misrepresentation_risk: int = 0
    reason: str = ""


class FunnelThumbnailScores(BaseModel):
    semantic_fit: int = 0
    editorial_function_fit: int = 0
    style_fit: int = 0
    continuity_fit: int = 0
    composition_quality: int = 0
    visual_quality: int = 0
    misrepresentation_risk: int = 0
    reason: str = ""


class FunnelCandidateRecord(BaseModel):
    candidate_id: str
    provider: str = ""
    provider_asset_id: str = ""
    funnel_status: str = "discovered"
    text_scores: FunnelTextScores = Field(default_factory=FunnelTextScores)
    thumbnail_scores: FunnelThumbnailScores = Field(
        default_factory=FunnelThumbnailScores
    )
    preliminary_score: float = 0.0
    final_score: Optional[int] = None
    rank: Optional[int] = None
    decision: str = ""
    reason: str = ""
    preview_status: str = "thumbnail_pending"
    download_status: str = "not_started"
    review_status: str = "not_reviewed"
    local_media_path: Optional[str] = None
    sha256: Optional[str] = None
    license_name: Optional[str] = None
    license_url: Optional[str] = None
    creator: Optional[str] = None
    source_page: Optional[str] = None
    attribution: Optional[str] = None
    fetched_at: Optional[str] = None
    # Informativ: complete | partial | missing
    license_metadata_status: str = ""
    excluded: bool = False
    exclude_reason: str = ""


class SupplementFunnelGapReport(BaseModel):
    gap_id: str
    run_id: str = ""
    inventory_reuse_ids: list[str] = Field(default_factory=list)
    candidates: list[FunnelCandidateRecord] = Field(default_factory=list)
    winner_candidate_id: Optional[str] = None
    # Historisch (R1); neuer Auto-Hauptweg nutzt export_ready_candidate_id.
    review_ready_candidate_id: Optional[str] = None
    export_ready_candidate_id: Optional[str] = None
    filled: bool = False
    full_download_attempts: int = 0
    technically_invalid_count: int = 0
    # Historisch (R2 Fail-Closed); R3 zählt nur noch informativ.
    license_incomplete_count: int = 0
    license_metadata_status: str = ""
    fallback_used: bool = False
    message: str = ""
    # R4: provider-balancierter 20er-Pool (historische Reports ohne Felder bleiben lesbar)
    candidate_pool_limit: int = 20
    eligible_providers: list[str] = Field(default_factory=list)
    provider_candidate_counts: dict[str, int] = Field(default_factory=dict)


class SupplementFunnelReport(BaseModel):
    schema_version: str = "enhanced-supplement-funnel-v4"
    run_id: str = ""
    script_version: str = ""
    max_candidates_per_gap: int = 20
    max_full_download_attempts_per_gap: int = 3
    # Gemini-Modell für Text- + Thumbnail-Ranking (historische Reports: leer).
    llm_model: str = ""
    gaps: list[SupplementFunnelGapReport] = Field(default_factory=list)
    requested_gap_ids: list[str] = Field(default_factory=list)
    skipped_gap_ids: list[str] = Field(default_factory=list)
    open_gap_ids: list[str] = Field(default_factory=list)
    filled_gap_ids: list[str] = Field(default_factory=list)
    full_download_count: int = 0
    technically_invalid_count: int = 0
    # Informativ: akzeptierte Kandidaten mit partial/missing Lizenzdaten.
    license_incomplete_count: int = 0
    fallback_used_count: int = 0
    message: str = ""
    stopped: bool = False


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
    start_cut_alignment: str = ""


class FinalCutPlanDocument(BaseModel):
    schema_version: str = "enhanced-final-cut-v1"
    script_version: str
    shots: list[FinalShot] = Field(default_factory=list)
    # Optional LLM choices (only used when settings mode=llm).
    voiceover_preroll_sec: Optional[float] = None
    voiceover_postroll_sec: Optional[float] = None


class ResolvedShot(BaseModel):
    shot_id: str
    asset_id: str
    timeline_start_seconds: float
    timeline_end_seconds: float
    source_start_seconds: float
    source_end_seconds: float
    editorial_function: str = ""
    may_overlap_pause: bool = False
    # Exakter lokaler Medienpfad — OTIO-Export darf nicht erneut nach ID suchen.
    resolved_media_path: str = ""
    resolved_media_kind: str = ""  # video | image
    resolved_media_duration_seconds: Optional[float] = None
    resolved_available_start_seconds: float = 0.0
    folder_name: str = ""
    # still_available_range | freeze_video | none
    hold_mode: str = ""


class ResolvedAudioSegment(BaseModel):
    segment_id: str
    audio_path: str
    timeline_start_seconds: float
    timeline_end_seconds: float
    pause_after_seconds: float = 0.0
    # Innerhalb der Quell-MP3 (für Intra-Segment-Splits an Satzpausen).
    source_start_seconds: float = 0.0
    source_end_seconds: Optional[float] = None
    split_label: str = ""


class ResolvedTimelineDocument(BaseModel):
    schema_version: str = "enhanced-resolved-timeline-v1"
    script_version: str
    fps: float = 25.0
    total_duration_seconds: float
    audio_segments: list[ResolvedAudioSegment] = Field(default_factory=list)
    shots: list[ResolvedShot] = Field(default_factory=list)
    voiceover_preroll_sec: float = 0.0
    voiceover_postroll_sec: float = 0.0
    repairs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def model_to_jsonable(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
