"""Phase 9.1: Datenmodelle für die isolierte EditPlan-Bridge.

Bewusst GETRENNT von `cut_plan_models.py` (eigener Namensraum für die Brücke
zwischen Cut Plan und der bestehenden Produktions-`EditPlanDocument`-Welt).
Diese Modelle werden ausschließlich unter
`_otio/voiceover_generation/cut_plan/edit_plan_bridge/` persistiert."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
    READINESS_SEVERITY_WARNING,
)

__all__ = [
    "EditPlanBridgeValidationError",
    "EditPlanBridgeValidationReport",
    "EditPlanBridgeTraceEntry",
    "EditPlanBridgeTraceDocument",
    "BridgeAudioPlanItem",
    "BridgeAudioPlanDocument",
    "EditPlanBridgeConfirmManifest",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EditPlanBridgeValidationError(BaseModel):
    type: str
    severity: str = READINESS_SEVERITY_WARNING  # WARNING|BLOCKER
    scope: str = "project"  # project|audio|visual|timeline|asset|frame
    cut_item_id: str = ""
    visual_segment_id: str = ""
    timeline_item_id: str = ""
    message: str = ""
    fix_hint: str = ""


class EditPlanBridgeValidationReport(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    edit_plan_hash: str = ""
    status: str = EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS  # PASS|WARNING|BLOCKED
    warnings: list[EditPlanBridgeValidationError] = Field(default_factory=list)
    blockers: list[EditPlanBridgeValidationError] = Field(default_factory=list)


class EditPlanBridgeTraceEntry(BaseModel):
    """Zeigt für EIN TimelineItem, aus welchem Cut-Plan-Element es entstand
    und ob/wie seine Zeiten frame-normalisiert wurden."""

    trace_id: str
    cut_item_id: str = ""
    visual_segment_id: str = ""
    source_scope: str = ""  # intro|folder
    folder_name: str = ""
    source_sentence_id: str = ""
    source_hook_beat_id: str = ""
    timeline_item_id: str = ""
    timeline_item_type: str = ""
    track: str = ""
    asset_id: str = ""
    asset_path: str = ""
    # timeline_in/out_sec und source_in/out_sec sind die FINALEN, nach
    # Boundary-Chaining (Phase 9.2) im edit_plan tatsächlich verwendeten
    # Werte — identisch zum entsprechenden TimelineItem.
    timeline_in_sec: float = 0.0
    timeline_out_sec: float = 0.0
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    frame_rounded: bool = False
    frame_rounding_delta_sec: float = 0.0
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)
    # Additiv über das im Architekturplan §4 skizzierte JSON-Schema hinaus,
    # aber explizit in §3 („Ziel“) gefordert:
    original_chosen_asset_id: str = ""
    duration_strategy: str = ""
    # Phase 9.2: die drei Stufen der Zeit-Transformation nachvollziehbar
    # machen — Rohwert aus dem Cut Plan, Ergebnis der reinen Frame-Rundung
    # (vor Boundary-Chaining) und ob/wie stark das Boundary-Chaining bzw. die
    # Source-Dauer-Anpassung das Ergebnis danach noch verändert hat.
    original_timeline_in_sec: float = 0.0
    original_timeline_out_sec: float = 0.0
    rounded_timeline_in_sec: float = 0.0
    rounded_timeline_out_sec: float = 0.0
    boundary_chained: bool = False
    boundary_chain_delta_sec: float = 0.0
    source_duration_adjusted: bool = False
    source_duration_delta_sec: float = 0.0


class EditPlanBridgeTraceDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    edit_plan_hash: str = ""
    entries: list[EditPlanBridgeTraceEntry] = Field(default_factory=list)


class BridgeAudioPlanItem(BaseModel):
    """Phase 9.2: strukturierte, providerunabhängige Beschreibung EINES
    Audio-Elements des Bridge-Drafts — vermeidet, dass eine spätere Phase aus
    dem TimelineItem-Sondertyp 'voiceover_audio' raten muss."""

    scope: str = ""  # intro|folder
    folder_name: str = ""
    audio_path: str = ""
    timeline_in_sec: float = 0.0
    timeline_out_sec: float = 0.0
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    duration_sec: float = 0.0
    track: str = "A1"
    source_cut_plan_audio_index: int = 0
    # Additiv, für die Bridge-Validation (§5): eindeutige Korrelation mit dem
    # entsprechenden Audio-TimelineItem im edit_plan.
    timeline_item_id: str = ""


class BridgeAudioPlanDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    items: list[BridgeAudioPlanItem] = Field(default_factory=list)


class EditPlanBridgeConfirmManifest(BaseModel):
    """Phase 9.3: Manifest EINES bestätigten/eingefrorenen Bridge-Snapshots.
    Weiterhin ein isolierter Snapshot — KEIN Produktions-EditPlan, KEIN
    locked Plan, KEIN OTIO-Export."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    confirmed_at: datetime = Field(default_factory=_utcnow)
    status: str = EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED
    source_cut_plan_hash: str = ""
    edit_plan_hash: str = ""
    bridge_audio_plan_hash: str = ""
    bridge_trace_hash: str = ""
    validation_report_hash: str = ""
    source_files: dict[str, str] = Field(default_factory=dict)
    confirmed_files: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
