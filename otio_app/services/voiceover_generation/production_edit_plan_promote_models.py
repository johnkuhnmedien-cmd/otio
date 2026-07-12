"""Phase 10.5: Datenmodelle für Production-EditPlan-Promote-Readiness/Dry-Run.

Bewusst GETRENNT von `production_edit_plan_models.py` (eigener Namensraum für
die rein lesende Prüfung, was ein SPÄTERER Promote nach `_otio/edit_plan/`
tun würde). Diese Modelle werden ausschließlich unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`
persistiert — NIEMALS unter `_otio/edit_plan/`. Kein tatsächliches Kopieren,
kein Lock, kein OTIO-Export."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED

__all__ = [
    "ProductionEditPlanPromoteSectionReadiness",
    "ProductionEditPlanPromoteReadinessDocument",
    "ProductionEditPlanPromoteDryRunTraceEntry",
    "ProductionEditPlanPromoteDryRunTraceDocument",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductionEditPlanPromoteSectionReadiness(BaseModel):
    """Promote-Bereitschaft EINER Sektion — inkl. rein lesender
    Kollisionsprüfung gegen einen eventuell bereits existierenden
    Produktionsplan unter `_otio/edit_plan/{folder}.json`."""

    staging_section_id: str = ""
    production_section_id: str = ""
    folder_name: str = ""
    is_intro: bool = False
    staged_edit_plan_path: str = ""
    target_edit_plan_path: str = ""
    promote_action: str = ""  # WOULD_CREATE|WOULD_OVERWRITE|WOULD_SKIP_INTRO|BLOCKED
    target_exists: bool = False
    existing_file_hash: str = ""
    existing_confirmed: bool | None = None
    existing_candidate_status: str = ""
    existing_shot_count: int | None = None
    existing_timeline_item_count: int | None = None
    staged_edit_plan_hash: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanPromoteReadinessDocument(BaseModel):
    """Gesamt-Ergebnis EINES Promote-Dry-Run-Laufs — reine Diagnose, kein
    Promote, kein Schreiben nach `_otio/edit_plan/`."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_package_hash: str = ""
    source_validation_report_hash: str = ""
    status: str = PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED  # READY|NEEDS_REVIEW|BLOCKED
    sections: list[ProductionEditPlanPromoteSectionReadiness] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanPromoteDryRunTraceEntry(BaseModel):
    """Zeigt für EINE Sektion, was ein späterer Promote konkret getan hätte
    — abgeleitet 1:1 aus der bereits gebauten
    ProductionEditPlanPromoteSectionReadiness, keine eigene Neuberechnung."""

    trace_id: str = ""
    staging_section_id: str = ""
    production_section_id: str = ""
    folder_name: str = ""
    is_intro: bool = False
    staged_edit_plan_path: str = ""
    target_edit_plan_path: str = ""
    promote_action: str = ""
    reason: str = ""
    existing_file_hash: str = ""
    staged_edit_plan_hash: str = ""
    would_write: bool = False
    would_overwrite: bool = False
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanPromoteDryRunTraceDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_package_hash: str = ""
    entries: list[ProductionEditPlanPromoteDryRunTraceEntry] = Field(default_factory=list)
