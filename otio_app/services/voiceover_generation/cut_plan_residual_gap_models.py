"""Residual Gap Requests (Nutzervorgabe, Juli 2026): Datenmodelle für den
dritten, eigenständigen Reparaturpfad zwischen Supplement (Item hat noch
KEIN Asset, siehe cut_plan_supplement_models.py) und Validation Repair
(kleine Lücke, per Nachbar-Kürzung reparierbar, siehe
cut_plan_validation_repair_models.py).

Ein Residual Gap Request entsteht für ein Item, das bereits versorgt ist
(PRIMARY_USED/BACKUP_USED/SUPPLEMENT_USED/GENERIC_FALLBACK_USED/
MANUAL_ASSET_USED — hat also mindestens ein VisualSegment), dessen
tatsächliche Abdeckung aber nicht bis zum erwarteten Ende reicht (eigenes
Timeline-Ende ODER, falls aktiviert, das per Visual Window verlängerte
Fenster bis zum nächsten Satz) — UND bei dem eine reine Nachbar-Kürzung
(siehe compute_black_gap_repair_plan) die Lücke NICHT sicher schließen
kann (zu groß oder kein Kürzungs-Spielraum). Siehe
cut_plan_visual_gap_analysis.GAP_KIND_RESIDUAL_ITEM_GAP."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_STATUS_OPEN,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAutoResolveAttempt,
)

__all__ = [
    "CutPlanResidualGapRequest",
    "CutPlanResidualGapRequestsDocument",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CutPlanResidualGapRequest(BaseModel):
    """Isolierter Reparatur-Bedarf für GENAU EINE Rest-Lücke eines bereits
    versorgten Items. Identifiziert über cut_item_id — mehrere Rest-Lücken
    für dasselbe Item werden zu EINEM Request mit dem umfassenden
    [gap_start_sec, gap_end_sec]-Fenster zusammengeführt (siehe
    build_residual_gap_requests_from_cut_plan), analog zu
    CutPlanValidationRepairRequest."""

    request_id: str
    cut_item_id: str
    source_scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    text: str = ""
    visual_intent: str = ""

    gap_start_sec: float = 0.0
    gap_end_sec: float = 0.0
    needed_duration_sec: float = 0.0

    # Erwartetes Abdeckungsfenster des Items zum Zeitpunkt der Erzeugung —
    # nur zur Diagnose/Anzeige (Visual-Window-Ende kann sich bei künftigen
    # Settings-Änderungen verschieben).
    expected_start_sec: float = 0.0
    expected_end_sec: float = 0.0

    # Zustand des Items zum Zeitpunkt der Erzeugung — rein informativ.
    existing_asset_id: str = ""
    existing_asset_status: str = ""

    repair_mode: str = CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY  # PATCH_GAP_ONLY|REPLACE_ITEM_VISUAL
    reason: str = ""

    status: str = CUT_PLAN_RESIDUAL_GAP_STATUS_OPEN  # OPEN|CANDIDATES_FOUND|ACCEPTED|NO_MATCH|FAILED
    created_at: datetime = Field(default_factory=_utcnow)

    accepted_candidate_id: str = ""
    accepted_asset_id: str = ""
    accepted_asset_path: str = ""

    # Nutzervorgabe (Juli 2026, "wie bei der normalen Supplement-Pipeline
    # will ich, dass ein einmal gefundenes Asset nicht erneut gesucht
    # wird"): Signatur aus (cut_item_id, gerundetes Gap-Fenster,
    # repair_mode) zum Zeitpunkt der Akzeptanz — erlaubt
    # merge_prior_residual_gap_request_state zu erkennen, ob ein
    # akzeptiertes Asset auch bei leicht verschobenen Gap-Zeiten für
    # DENSELBEN Satz noch als vertrauenswürdiger Treffer gilt.
    accepted_for_cache_signature: str = ""

    llm_queries: list[str] = Field(default_factory=list)
    llm_query_status: str = ""
    llm_query_run_id: str = ""
    llm_query_error: str = ""
    auto_resolve_status: str = ""
    auto_resolve_attempts: list[CutPlanSupplementAutoResolveAttempt] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)


class CutPlanResidualGapRequestsDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    requests: list[CutPlanResidualGapRequest] = Field(default_factory=list)
