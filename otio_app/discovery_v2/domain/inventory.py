"""Domainvertrag für Discovery-V2-Bestandsaufnahmen (read-only)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


INVENTORY_SCHEMA_VERSION = "1"

# Intern stabil; UI zeigt ROOT_SOURCE_GROUP_LABEL.
ROOT_SOURCE_GROUP = "__root__"
ROOT_SOURCE_GROUP_LABEL = "Unsortiert"


class MediaKind(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    OTHER = "other"


class ScanStatus(str, Enum):
    FOUND = "found"
    ERROR = "error"


class InventoryFileEntry(BaseModel):
    """Eine gefundene Datei relativ zum Projektroot."""

    relative_path: str
    filename: str
    extension: str
    source_group: str
    source_group_label: str
    media_kind: MediaKind
    size_bytes: int
    mtime_iso: str
    scan_status: ScanStatus = ScanStatus.FOUND


class SourceGroupSummary(BaseModel):
    """Aggregierte Zähler je Quellgruppe (kein Kapitel)."""

    source_group: str
    label: str
    video_count: int = 0
    image_count: int = 0
    audio_count: int = 0
    other_count: int = 0
    file_count: int = 0


class ExcludedEntry(BaseModel):
    relative_path: str
    reason: str


class InventorySnapshot(BaseModel):
    """Vollständiger Bestandsaufnahme-Snapshot."""

    schema_version: str = INVENTORY_SCHEMA_VERSION
    scan_id: str
    project_id: str
    project_root: str
    created_at: datetime
    source_group_count: int = 0
    file_count: int = 0
    video_count: int = 0
    image_count: int = 0
    audio_count: int = 0
    other_count: int = 0
    excluded_count: int = 0
    source_groups: list[SourceGroupSummary] = Field(default_factory=list)
    files: list[InventoryFileEntry] = Field(default_factory=list)
    excluded: list[ExcludedEntry] = Field(default_factory=list)


class InventoryLatestPointer(BaseModel):
    """Kleines Pointer-Artefakt auf den letzten erfolgreichen Snapshot."""

    schema_version: str = INVENTORY_SCHEMA_VERSION
    scan_id: str
    created_at: datetime
    snapshot_relative_path: str
