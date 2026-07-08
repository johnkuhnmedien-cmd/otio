"""Phase 9.1: Datenmodelle für die isolierte EditPlan-Bridge.

Bewusst GETRENNT von `cut_plan_models.py` (eigener Namensraum für die Brücke
zwischen Cut Plan und der bestehenden Produktions-`EditPlanDocument`-Welt).
Diese Modelle werden ausschließlich unter
`_otio/voiceover_generation/cut_plan/edit_plan_bridge/` persistiert."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
    READINESS_SEVERITY_WARNING,
)

__all__ = [
    "EditPlanBridgeValidationError",
    "EditPlanBridgeValidationReport",
    "EditPlanBridgeTraceEntry",
    "EditPlanBridgeTraceDocument",
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


class EditPlanBridgeTraceDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    edit_plan_hash: str = ""
    entries: list[EditPlanBridgeTraceEntry] = Field(default_factory=list)
