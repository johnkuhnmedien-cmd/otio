"""Phase 10.7: Datenmodelle für den Voice-Folder-Mapping-Merge.

Bewusst GETRENNT von `production_edit_plan_promote_execute_models.py`
(Phase 10.6 erzeugt nur einen reinen Vorbereitungs-Patch, verändert
`voice_folder_mapping.json` NICHT). Diese Modelle dokumentieren einen
TATSÄCHLICHEN, explizit bestätigten Merge-Lauf, inklusive Backup-Nachweis
der vorherigen `voice_folder_mapping.json`. Das Manifest wird unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`
persistiert (Backups unter .../voice_folder_mapping_merge_backups/
{merge_run_id}/) — die eigentliche `voice_folder_mapping.json` liegt jedoch
im Projekt-Root, wird aber ausschließlich über
`production_edit_plan_voice_folder_mapping_merge.py` geschrieben."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED

__all__ = [
    "VoiceFolderMappingMergeEntryResult",
    "VoiceFolderMappingMergeManifest",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VoiceFolderMappingMergeEntryResult(BaseModel):
    """Ergebnis EINES Patch-Eintrags aus EINEM tatsächlichen Merge-Lauf."""

    folder_name: str = ""
    action: str = ""  # ADDED|UPDATED|SKIPPED_ALREADY_PRESENT|SKIPPED_BY_USER|SKIPPED_CONFLICT_UNRESOLVED
    previous_voice_file: str = ""
    new_voice_file: str = ""
    applied: bool = False
    reason: str = ""


class VoiceFolderMappingMergeManifest(BaseModel):
    """Gesamt-Ergebnis EINES tatsächlichen Voice-Folder-Mapping-Merge-Laufs —
    dauerhafter Nachweis, was in `voice_folder_mapping.json` wann/wie
    verändert wurde."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    merge_run_id: str = ""
    source_patch_hash: str = ""
    source_promote_manifest_hash: str = ""
    previous_mapping_hash: str = ""
    new_mapping_hash: str = ""
    backup_path: str = ""
    backup_hash: str = ""
    status: str = VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED  # MERGED|BLOCKED
    entries: list[VoiceFolderMappingMergeEntryResult] = Field(default_factory=list)
    added_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
