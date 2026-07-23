"""Validation Repair (Nutzervorgabe, Juli 2026): Datenmodelle für den
eigenständigen, dem regulären Supplement-Bereich NACHGESCHALTETEN
Reparatur-Schritt.

Bewusst GETRENNT von `CutPlanSupplementRequest`/
`CutPlanSupplementRequestsDocument` (cut_plan_supplement_models.py) — ein
Validation-Repair-Request hat eine andere Semantik: er reagiert auf einen
KONKRETEN, bereits attribuierten Validierungs-Blocker (BLACK_GAP_DURING_
VOICEOVER, ASSET_REUSE_DISTANCE_TOO_SHORT) NACH der vollständigen
Cut-Plan-Validierung, nicht auf ein generisch fehlendes Asset. Für
BLACK_GAP ist das Ziel ein Zeitfenster-Reparatur-Segment (ggf. mit
Kürzung angrenzender Segmente), NICHT der Ersatz des gesamten Items."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAutoResolveAttempt,
)

__all__ = [
    "CutPlanValidationRepairRequest",
    "CutPlanValidationRepairRequestsDocument",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CutPlanValidationRepairRequest(BaseModel):
    """Isolierter Reparatur-Bedarf für GENAU EINEN Validierungs-Blocker
    (identifiziert über repair_type + cut_item_id — mehrere überlappende
    BLACK_GAP-Teilbereiche für dasselbe Item werden zu EINEM Request mit
    dem umfassenden [gap_start_sec, gap_end_sec]-Fenster zusammengeführt,
    siehe build_validation_repair_requests_from_cut_plan)."""

    repair_id: str
    repair_type: str  # BLACK_GAP|ASSET_REUSE_DISTANCE
    cut_item_id: str
    source_scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    text: str = ""
    visual_intent: str = ""
    # Nur für repair_type=BLACK_GAP: exakte Timeline-Grenzen der visuellen
    # Lücke, siehe CutPlanValidationError.gap_start_sec/gap_end_sec. Für
    # ASSET_REUSE_DISTANCE bleiben beide 0.0 (kein Zeitfenster-Problem —
    # das GANZE Segment/Item braucht ein anderes Asset).
    gap_start_sec: float = 0.0
    gap_end_sec: float = 0.0
    # Phase 4: das TATSÄCHLICHE Reparatur-Fenster (kann größer sein als
    # [gap_start_sec, gap_end_sec], wenn Nachbarsegmente gekürzt werden
    # mussten, damit das neue Segment mindestens shot_min_sec lang ist).
    # 0.0/0.0 (Default), solange die Berechnung noch nicht gelaufen ist.
    repair_window_start_sec: float = 0.0
    repair_window_end_sec: float = 0.0
    needed_duration_sec: float = 0.0
    reason: str = ""
    # Ursprüngliche Validierungsmeldung — nur zur Diagnose/Anzeige, keine
    # redaktionelle Bedeutung.
    source_error_message: str = ""
    status: str = CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING  # PENDING|ACCEPTED|FAILED|NO_MATCH
    created_at: datetime = Field(default_factory=_utcnow)
    accepted_asset_id: str = ""
    accepted_asset_path: str = ""
    llm_queries: list[str] = Field(default_factory=list)
    llm_query_status: str = ""
    llm_query_run_id: str = ""
    llm_query_error: str = ""
    auto_resolve_status: str = ""
    auto_resolve_attempts: list[CutPlanSupplementAutoResolveAttempt] = Field(default_factory=list)


class CutPlanValidationRepairRequestsDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    requests: list[CutPlanValidationRepairRequest] = Field(default_factory=list)
