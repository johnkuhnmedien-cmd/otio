"""Domainvertrag für die bestätigte Discovery-V2-Medienauswahl."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


SELECTION_SCHEMA_VERSION = "1"

EXCLUSION_REASON_USER = "user_excluded"


class SelectionStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    STALE = "stale"


class InventorySelection(BaseModel):
    """Versionierte, an genau eine scan_id gebundene Medienauswahl."""

    schema_version: str = SELECTION_SCHEMA_VERSION
    selection_id: str
    project_id: str
    scan_id: str
    source_snapshot_relative_path: str
    created_at: datetime
    confirmed_at: datetime | None = None
    status: SelectionStatus = SelectionStatus.DRAFT
    selected_source_groups: list[str] = Field(default_factory=list)
    selected_relative_paths: list[str] = Field(default_factory=list)
    excluded_relative_paths: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    selected_video_count: int = 0
    selected_image_count: int = 0
    selected_audio_count: int = 0
    selected_media_count: int = 0
    other_file_count: int = 0


class SelectionLatestPointer(BaseModel):
    """Pointer auf die letzte bestätigte Auswahl."""

    schema_version: str = SELECTION_SCHEMA_VERSION
    selection_id: str
    scan_id: str
    created_at: datetime
    confirmed_at: datetime | None = None
    selection_relative_path: str


class SelectionDraft(BaseModel):
    """Unbestätigte Session-Auswahl (nicht persistent)."""

    scan_id: str
    # Quellgruppen-IDs, die übernommen werden sollen.
    selected_source_groups: list[str] = Field(default_factory=list)
    # Unterstützte Mediendateien, die der Nutzer innerhalb aktiver Gruppen ausschließt.
    excluded_relative_paths: list[str] = Field(default_factory=list)
