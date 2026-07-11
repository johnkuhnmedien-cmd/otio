"""Phase 10.8: Datenmodelle für den OTIO-Export-Readiness-Check.

Bewusst GETRENNT von allen übrigen production_edit_plan_*-Modellen — diese
Modelle beschreiben eine rein lesende, vollständig isolierte Diagnose, ob
die grundlegenden Voraussetzungen der bestehenden Produktions-Export-
Pipeline für bereits promotete (Phase 10.6) UND gemappte (Phase 10.7)
Folder erfüllt wären. Kein Export, keine .otio-Datei, kein ffprobe."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED

__all__ = [
    "OtioExportReadinessFolderResult",
    "OtioExportReadinessReport",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OtioExportReadinessFolderResult(BaseModel):
    """Readiness-Ergebnis EINES promoteten Folders — rein strukturell
    abgeleitet (kein Aufruf der Produktions-Export-Pipeline)."""

    folder_name: str = ""
    edit_plan_path: str = ""
    edit_plan_exists: bool = False
    edit_plan_confirmed: bool = False
    in_confirmed_mapping: bool = False
    has_voiceover: bool = False
    shot_count: int = 0
    timeline_item_count: int = 0
    status: str = ""  # READY|NOT_READY
    warnings: list[str] = Field(default_factory=list)


class OtioExportReadinessReport(BaseModel):
    """Gesamt-Ergebnis EINES OTIO-Export-Readiness-Checks für die zuletzt
    promoteten Folder — reine, vollständig isolierte Struktur-Diagnose. Prüft
    dieselben grundsätzlichen Voraussetzungen, die die bestehende
    Produktions-Export-Pipeline verlangt (bestätigte Zuordnung, bestätigter
    EditPlan, nicht-leere Timeline/Shots), OHNE diese Pipeline selbst
    aufzurufen. Kein Export, keine .otio-Datei."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_promote_manifest_hash: str = ""
    source_merge_manifest_hash: str = ""
    checked_folders: list[str] = Field(default_factory=list)
    mapping_confirmed: bool = False
    total_shots: int = 0
    total_timeline_items: int = 0
    status: str = PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED  # READY|NOT_READY|BLOCKED
    folders: list[OtioExportReadinessFolderResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
