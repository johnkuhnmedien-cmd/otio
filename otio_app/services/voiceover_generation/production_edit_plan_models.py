"""Phase 10.1: Datenmodelle für das isolierte Production-EditPlan-Staging.

Bewusst GETRENNT von `cut_plan_models.py` und `cut_plan_edit_plan_models.py`
(eigener Namensraum für die Übersetzung eines bestätigten EditPlan-Bridge-
Snapshots in ein produktionskompatibles Staging-Paket). Diese Modelle werden
ausschließlich unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`
persistiert — NIEMALS unter `_otio/edit_plan/`."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_STATUS_STAGED,
    READINESS_SEVERITY_WARNING,
)

__all__ = [
    "ProductionEditPlanSection",
    "ProductionEditPlanPackage",
    "ProductionEditPlanMappingTraceEntry",
    "ProductionEditPlanMappingTraceDocument",
    "ProductionEditPlanValidationError",
    "ProductionEditPlanValidationReport",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductionEditPlanSection(BaseModel):
    """Eine Sektion (Intro oder ein Folder) im Staging-Paket."""

    staging_section_id: str = ""
    production_section_id: str = ""
    folder_name: str = ""
    is_intro: bool = False
    staged_edit_plan_path: str = ""
    shot_count: int = 0
    timeline_item_count: int = 0
    has_voiceover: bool = False
    # Phase 10.2 §7: additiv ergänzt — erlaubt Staleness-Erkennung auf
    # Datei-Ebene, ohne die gesamte staged edit_plan.json neu laden zu
    # müssen, nur um festzustellen, ob sie sich seit dem Staging geändert hat.
    staged_edit_plan_hash: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanPackage(BaseModel):
    """Gesamt-Manifest EINES Staging-Laufs — referenziert die einzelnen
    gestagten Sektions-EditPlanDocuments, schreibt sie aber nicht selbst."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_bridge_manifest_hash: str = ""
    source_cut_plan_hash: str = ""
    status: str = PRODUCTION_EDIT_PLAN_STATUS_STAGED  # STAGED|NEEDS_REVIEW|BLOCKED
    sections: list[ProductionEditPlanSection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanMappingTraceEntry(BaseModel):
    """Zeigt für EIN Produktions-TimelineItem (bzw. EINEN VoiceoverPlan-
    Eintrag), aus welchem Bridge-/CutPlan-Element es entstand — macht
    CutPlan -> BridgeDraft -> ProductionEditPlan vollständig nachvollziehbar."""

    trace_id: str = ""
    source_bridge_timeline_item_id: str = ""
    source_bridge_audio_plan_index: int | None = None
    source_cut_item_id: str = ""
    source_visual_segment_id: str = ""
    resulting_staging_section_id: str = ""
    resulting_production_section_id: str = ""
    resulting_edit_plan_path: str = ""
    resulting_timeline_item_id: str = ""
    folder_name: str = ""
    is_intro: bool = False
    original_timeline_in_sec: float = 0.0
    original_timeline_out_sec: float = 0.0
    local_timeline_in_sec: float = 0.0
    local_timeline_out_sec: float = 0.0
    asset_id: str = ""
    asset_path: str = ""
    mapping_reason: str = ""
    fields_defaulted: list[str] = Field(default_factory=list)
    fields_dropped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductionEditPlanMappingTraceDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_bridge_manifest_hash: str = ""
    source_cut_plan_hash: str = ""
    entries: list[ProductionEditPlanMappingTraceEntry] = Field(default_factory=list)


class ProductionEditPlanValidationError(BaseModel):
    type: str
    severity: str = READINESS_SEVERITY_WARNING  # WARNING|BLOCKER
    scope: str = "project"  # project|section|voiceover|timeline|asset|trace
    staging_section_id: str = ""
    production_section_id: str = ""
    timeline_item_id: str = ""
    message: str = ""
    fix_hint: str = ""


class ProductionEditPlanValidationReport(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_bridge_manifest_hash: str = ""
    package_hash: str = ""
    status: str = "PASS"  # PASS|WARNING|BLOCKED
    warnings: list[ProductionEditPlanValidationError] = Field(default_factory=list)
    blockers: list[ProductionEditPlanValidationError] = Field(default_factory=list)
