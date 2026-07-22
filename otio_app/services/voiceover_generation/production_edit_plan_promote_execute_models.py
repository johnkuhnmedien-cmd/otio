"""Phase 10.6: Datenmodelle für den tatsächlichen Production-EditPlan-Promote.

Bewusst GETRENNT von `production_edit_plan_promote_models.py` (Phase 10.5
Dry-Run: "was WÜRDE passieren") — diese Modelle dokumentieren "was IST
passiert", inklusive Backup-Nachweis vor jedem Overwrite. Persistiert unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`
(Manifest, Mapping-Patch) bzw. .../promote_backups/{promote_run_id}/
(Backups) — die eigentlichen Promote-Ziele liegen unter `_otio/edit_plan/`,
werden aber ausschließlich über `production_edit_plan_promote_execute.py`
geschrieben."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED

__all__ = [
    "ProductionEditPlanPromoteSectionResult",
    "ProductionEditPlanPromoteManifest",
    "ProductionEditPlanVoiceFolderMappingPatchEntry",
    "ProductionEditPlanVoiceFolderMappingPatch",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductionEditPlanPromoteSectionResult(BaseModel):
    """Ergebnis EINER Section aus EINEM tatsächlichen Promote-Lauf."""

    staging_section_id: str = ""
    production_section_id: str = ""
    folder_name: str = ""
    is_intro: bool = False
    source_staged_edit_plan_path: str = ""
    target_edit_plan_path: str = ""
    promote_action: str = ""  # CREATED|OVERWRITTEN|SKIPPED_INTRO|BLOCKED
    source_hash: str = ""
    target_hash_after: str = ""
    backup_path: str = ""
    backup_hash: str = ""
    confirmed_set_to_true: bool = False
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanPromoteManifest(BaseModel):
    """Gesamt-Ergebnis EINES tatsächlichen Promote-Laufs — dauerhafter
    Nachweis, welche Produktionspläne wann/wie geschrieben wurden."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    promote_run_id: str = ""
    source_readiness_hash: str = ""
    source_package_hash: str = ""
    source_validation_report_hash: str = ""
    status: str = PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED  # PROMOTED|NEEDS_REVIEW|BLOCKED
    sections: list[ProductionEditPlanPromoteSectionResult] = Field(default_factory=list)
    created_count: int = 0
    overwritten_count: int = 0
    skipped_intro_count: int = 0
    blocked_count: int = 0
    backup_dir: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProductionEditPlanVoiceFolderMappingPatchEntry(BaseModel):
    folder_name: str = ""
    edit_plan_path: str = ""
    voiceover_path: str = ""
    voiceover_duration_sec: float = 0.0
    action: str = ""  # WOULD_ADD|ALREADY_PRESENT|NEEDS_REVIEW
    reason: str = ""


class ProductionEditPlanVoiceFolderMappingPatch(BaseModel):
    """Reine Vorbereitungs-/Diagnose-Datei — verändert `voice_folder_mapping
    .json` selbst NICHT. Das bleibt einer späteren, eigenen Phase
    vorbehalten, weil es die Export-Reihenfolge und die bestehende
    Produktionspipeline beeinflusst."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    promote_run_id: str = ""
    entries: list[ProductionEditPlanVoiceFolderMappingPatchEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
