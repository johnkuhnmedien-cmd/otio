"""Datenmodelle für den technischen Cut Plan (Phase 8).

Liest AUSSCHLIESSLICH `confirmed_voiceover_project_plan.json` (Phase 7) sowie
Folder-Inventories. Erzeugt ein eigenständiges `CutPlanDocument` — KEIN
EditPlanDocument, KEIN locked Plan, KEIN OTIO-Export. Der bestätigte
Voice-over-Projektplan bleibt die redaktionelle Quelle der Wahrheit; dieses
Modul übersetzt ihn (in späteren Sub-Phasen) in eine technische
Schnittplan-Struktur, plant aber nichts redaktionell neu.

Diese Modelle dürfen nicht mit `EditPlanDocument`, `TimelineItem` oder
anderen Modellen der bestehenden Produktions-Schnittplan-Pipeline vermischt
werden. Einzige erlaubte Ausnahme: `SupplementRequest` aus
`otio_app.analysis_models` wird als reines Datenmodell wiederverwendet (siehe
Architekturplan Phase 8, Punkt 8) — es werden dabei keine Funktionen aus der
produktionsseitigen Supplement-Orchestrierung aufgerufen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from otio_app.analysis_models import SupplementRequest
from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC,
    CUT_PLAN_DEFAULT_MAX_ASSET_USAGE,
    CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS,
    CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC,
    CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC,
    CUT_PLAN_DEFAULT_SHOT_MAX_SEC,
    CUT_PLAN_DEFAULT_SHOT_MIN_SEC,
    CUT_PLAN_DEFAULT_TIMELINE_FPS,
    CUT_PLAN_DEFAULT_TIMELINE_HEIGHT,
    CUT_PLAN_DEFAULT_TIMELINE_WIDTH,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    CUT_PLAN_FIX_BY_PYTHON,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    READINESS_SEVERITY_WARNING,
)

__all__ = [
    "CutPlanSettings",
    "CutPlanSourceRef",
    "VisualSegment",
    "CutPlanPlannedSegmentAssetPlan",
    "CutPlanItem",
    "CutPlanAudioItem",
    "CutPlanValidationError",
    "CutPlanDocument",
    "CutPlanTraceEntry",
    "CutPlanTraceDocument",
    "CutPlanValidationReport",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CutPlanSettings(BaseModel):
    """Eigenständige Cut-Plan-Einstellungen — bewusst NICHT aus
    edit_plan_rules.json übernommen (schützt die bestehende Pipeline, §3)."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    initial_audio_offset_sec: float = CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC
    pause_between_sections_sec: float = CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC
    section_visual_preroll_sec: float = CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC
    video_head_trim_sec: float = CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC
    shot_min_sec: float = CUT_PLAN_DEFAULT_SHOT_MIN_SEC
    shot_max_sec: float = CUT_PLAN_DEFAULT_SHOT_MAX_SEC
    max_asset_usage: int = CUT_PLAN_DEFAULT_MAX_ASSET_USAGE
    min_asset_reuse_distance_shots: int = CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS
    timeline_fps: int = CUT_PLAN_DEFAULT_TIMELINE_FPS
    timeline_width: int = CUT_PLAN_DEFAULT_TIMELINE_WIDTH
    timeline_height: int = CUT_PLAN_DEFAULT_TIMELINE_HEIGHT


class CutPlanSourceRef(BaseModel):
    """Verweis auf genau EIN Sentence-/Beat-Item im bestätigten Voice-over-Plan.

    Ein CutPlanItem kann mehrere source_refs enthalten (Merge kurzer Sätze zu
    einem visuellen Schnitt, §5 Nutzerentscheidung)."""

    source_scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    source_sentence_id: str = ""
    source_hook_beat_id: str = ""
    text: str = ""


class VisualSegment(BaseModel):
    """EIN sichtbares Timeline-Segment auf V1. Ein CutPlanItem kann mehrere
    VisualSegments enthalten (Split eines langen Satzes, §6)."""

    segment_id: str
    timeline_in_sec: float = 0.0
    timeline_out_sec: float = 0.0
    duration_sec: float = 0.0
    asset_id: str = ""
    asset_path: str = ""
    asset_type: str = ""  # video|image
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    track: str = "V1"
    transform: dict[str, Any] = Field(default_factory=dict)
    background_style: str = ""  # "vintage" oder ""
    reason: str = ""  # primary_asset|backup_asset|split_long_sentence|merged_short_sentence|supplement


class CutPlanPlannedSegmentAssetPlan(BaseModel):
    """Phase 7 (Cut-Plan-Split-Fix): unabhängig deklarierte Kopie der vom
    Voice-over-Autor pro Satz/Beat geplanten Shot-Aufteilung (siehe
    otio_app.services.voiceover_generation.models.SentenceSegmentAssetPlan)
    — bewusst NICHT dasselbe Modell direkt wiederverwendet (siehe
    Moduldocstring: keine Modell-Vermischung mit der Voice-over-Pipeline),
    nur die für die Asset-Auswahl relevanten Felder.

    Leer (Default) für alle Ein-Shot-Items und für alle vor Phase 7
    erzeugten Sentence-/Intro-Items; die Asset-Auswahl fällt dann auf den
    allgemeinen Fallback-Pool zurück (primary_asset_id + backup_asset_ids +
    second_backup_asset_ids)."""

    segment_order: int = 1
    primary_asset_id: str = ""
    backup_asset_ids: list[str] = Field(default_factory=list)


class CutPlanItem(BaseModel):
    """Eine redaktionelle Schnitt-Einheit — kann durch Merge mehrere
    Sentence-/Beat-Items abdecken (source_refs) oder durch Split mehrere
    VisualSegments erzeugen (planned_visual_segments)."""

    cut_item_id: str
    source_refs: list[CutPlanSourceRef] = Field(default_factory=list)
    source_scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    text: str = ""
    visual_intent: str = ""
    audio_start_sec: float = 0.0
    audio_end_sec: float = 0.0
    timeline_start_sec: float = 0.0
    timeline_end_sec: float = 0.0
    duration_sec: float = 0.0
    duration_strategy: str = ""  # SINGLE_SHOT|SPLIT|MERGED
    planned_visual_segments: list[VisualSegment] = Field(default_factory=list)
    primary_asset_id: str = ""
    backup_asset_ids: list[str] = Field(default_factory=list)
    # Phase 7: WEITERE genuinely passende lokale Ausweichassets (siehe
    # SentenceItem.second_backup_asset_ids) — zählen als echte Alternativen
    # im Fallback-Pool der Asset-Auswahl (cut_plan_asset_selector.py).
    second_backup_asset_ids: list[str] = Field(default_factory=list)
    # Phase 7: vom Autor pro Shot geplante Asset-Zuordnung, siehe
    # CutPlanPlannedSegmentAssetPlan-Docstring.
    planned_segments: list[CutPlanPlannedSegmentAssetPlan] = Field(default_factory=list)
    chosen_asset_id: str = ""
    # PRIMARY_USED|BACKUP_USED|SUPPLEMENT_REQUIRED|BLOCKED|UNRESOLVED
    asset_selection_status: str = CUT_PLAN_ASSET_SELECTION_UNRESOLVED
    asset_selection_reason: str = ""
    fallback_reason: str = ""
    needs_supplement_asset: bool = False
    supplement_reason: str = ""
    supplement_request_id: str = ""
    # Phase 9 (Asset-bewusste Cut-Plan-Vorbereitung): bereits beim
    # Skriptschreiben vom Autor-LLM vorbereiteter, ortsbezogener
    # Suchvorschlag (siehe SentenceItem.visual_asset_plan.
    # supplement_search_hint) — wird der späteren Supplement-Suche als
    # bevorzugte Query mitgegeben. Leer für Intro-Items (IntroHookVisualBeat
    # hat kein visual_asset_plan) und für alle vor Phase 4/9 erzeugten
    # Sentence-Items.
    supplement_search_hint: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class CutPlanAudioItem(BaseModel):
    """Ein Voice-over-Audio-Clip auf A1 (Intro oder ein Folder)."""

    scope: str = AUDIO_SCOPE_INTRO  # intro|folder
    folder_name: str = ""
    audio_path: str = ""
    alignment_ref_path: str = ""  # Rückverweis auf die Phase-7-Alignment-JSON
    source_in_sec: float = 0.0
    timeline_start_sec: float = 0.0
    timeline_end_sec: float = 0.0
    duration_sec: float = 0.0
    track: str = "A1"


class CutPlanValidationError(BaseModel):
    """Analog zu Phase 7s ReadinessError, aber mit expliziter LLM-/Python-/
    User-Klassifizierung statt heuristischem String-Matching (siehe Audit-
    Lehren aus Phase 7: Klassifizierung soll von Anfang an explizit sein)."""

    type: str
    severity: str = READINESS_SEVERITY_WARNING  # WARNING|BLOCKER
    scope: str = "project"  # project|intro|folder|sentence|audio|alignment|asset|timeline
    cut_item_id: str = ""
    folder_name: str = ""
    message: str = ""
    fix_hint: str = ""
    is_retryable_by_llm: bool = False
    must_be_fixed_by: str = CUT_PLAN_FIX_BY_PYTHON  # python|llm|user


class CutPlanDocument(BaseModel):
    """Technischer Cut-Plan-Entwurf aus `confirmed_voiceover_project_plan.json`.

    Eigenständiges Modell — KEIN EditPlanDocument, KEIN locked Plan, KEIN
    OTIO-Export. Der bestätigte Voice-over-Projektplan bleibt die
    redaktionelle Quelle der Wahrheit; dieses Dokument ist eine rein
    technische Ableitung davon."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    project_title: str = ""
    language: str = "DE"
    source_plan_path: str = ""
    source_plan_hash: str = ""
    status: str = CUT_PLAN_STATUS_DRAFT  # DRAFT|VALIDATED|NEEDS_REVIEW|CONFIRMED|BLOCKED
    # Phase 8.7: additiv ergänzt — generated_at bleibt der ursprüngliche
    # Erzeugungszeitpunkt des Drafts, confirmed_at wird erst beim Bestätigen
    # gesetzt (siehe cut_plan_confirm_service.confirm_cut_plan).
    confirmed_at: datetime | None = None
    timeline_fps: int = CUT_PLAN_DEFAULT_TIMELINE_FPS
    initial_audio_offset_sec: float = CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC
    pause_between_sections_sec: float = CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC
    section_visual_preroll_sec: float = CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC
    timeline_width: int = CUT_PLAN_DEFAULT_TIMELINE_WIDTH
    timeline_height: int = CUT_PLAN_DEFAULT_TIMELINE_HEIGHT
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    items: list[CutPlanItem] = Field(default_factory=list)
    audio_items: list[CutPlanAudioItem] = Field(default_factory=list)
    # Wiederverwendung des Produktions-Datenmodells (nur die Datenstruktur,
    # keine Orchestrierungsfunktionen) — isoliert gespeichert unter
    # cut_plan/supplement_requests.from_cut_plan.json, niemals vermischt mit
    # _otio/supplement/supplement_requests.json.
    supplement_requests: list[SupplementRequest] = Field(default_factory=list)
    asset_usage_summary: dict[str, int] = Field(default_factory=dict)
    warnings: list[CutPlanValidationError] = Field(default_factory=list)
    blockers: list[CutPlanValidationError] = Field(default_factory=list)


class CutPlanTraceEntry(BaseModel):
    """Nachvollziehbarkeit pro CutPlanItem: 'Quellplan wollte A, Cut Plan
    machte B, Grund: C'."""

    trace_id: str
    cut_item_id: str = ""
    source_refs: list[CutPlanSourceRef] = Field(default_factory=list)
    original_primary_asset_id: str = ""
    original_backup_asset_ids: list[str] = Field(default_factory=list)
    chosen_asset_id: str = ""
    choice_reason: str = ""
    fallback_used: bool = False
    supplement_request_id: str = ""
    duration_strategy: str = ""
    split_or_merge_decision: str = ""
    timeline_start_sec: float = 0.0
    timeline_end_sec: float = 0.0
    validation_warnings: list[str] = Field(default_factory=list)
    validation_blockers: list[str] = Field(default_factory=list)
    # Phase 8.7: additiv ergänzt.
    asset_selection_status: str = ""
    visual_segment_ids: list[str] = Field(default_factory=list)
    visual_segment_count: int = 0
    used_supplement_asset: bool = False
    fallback_reason: str = ""
    # Distinkte '+'-getrennte reason-Marker aus allen VisualSegments dieses
    # Items, z. B. initial_preroll_extension, section_pause_hold,
    # merged_short_sentence, split_long_sentence_continuation, supplement_asset.
    visual_segment_reason_markers: list[str] = Field(default_factory=list)


class CutPlanTraceDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_plan_hash: str = ""
    cut_plan_hash: str = ""
    entries: list[CutPlanTraceEntry] = Field(default_factory=list)


class CutPlanValidationReport(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    cut_plan_hash: str = ""
    status: str = CUT_PLAN_VALIDATION_STATUS_PASS  # PASS|WARNING|BLOCKED
    errors: list[CutPlanValidationError] = Field(default_factory=list)
    warnings: list[CutPlanValidationError] = Field(default_factory=list)
    blockers: list[CutPlanValidationError] = Field(default_factory=list)
