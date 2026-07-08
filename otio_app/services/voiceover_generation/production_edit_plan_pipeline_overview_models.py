"""Phase 10.9: Datenmodelle für das Gesamt-Übersichts-Dashboard.

Bewusst GETRENNT von allen übrigen production_edit_plan_*-Modellen — dieses
Modell aggregiert rein lesend den bereits vorhandenen Status aller
vorherigen Phasen (Staging, Validation, Promote-Readiness/Dry-Run, Promote,
Voice-Folder-Mapping-Merge, OTIO-Export-Readiness). Es wird NICHT
persistiert — der Overview wird bei jedem Aufruf live aus den bestehenden
Artefakten neu berechnet, damit er niemals selbst veraltet sein kann."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED

__all__ = [
    "PipelineStageOverview",
    "ProductionEditPlanPipelineOverview",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineStageOverview(BaseModel):
    """Status EINER Phase im Gesamt-Workflow."""

    stage_id: str = ""
    label: str = ""
    exists: bool = False
    status: str = ""  # phasen-eigener Status-String, oder NOT_STARTED
    is_stale: bool = False
    detail: str = ""


class ProductionEditPlanPipelineOverview(BaseModel):
    """Gesamt-Überblick über den kompletten Production-EditPlan-Workflow —
    reine, live berechnete Aggregation, kein eigenes Artefakt."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    overall_status: str = PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED
    stages: list[PipelineStageOverview] = Field(default_factory=list)
