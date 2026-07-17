"""Domainvertrag für die Discovery-V2-Asset-Registry (Metadaten only)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from otio_app.discovery_v2.domain.inventory import MediaKind


REGISTRY_SCHEMA_VERSION = "6"
IMPORT_REPORT_SCHEMA_VERSION = "1"


class RegistryImportStatus(str, Enum):
    IMPORTED = "imported"
    ALREADY_IMPORTED = "already_imported"
    FAILED = "failed"
    STALE_SELECTION = "stale_selection"


class RegistryAssetRecord(BaseModel):
    asset_id: str
    project_id: str
    source_relative_path: str
    source_group: str
    file_name: str
    extension: str
    media_kind: MediaKind
    size_bytes: int
    mtime_ns: int
    created_at: datetime
    updated_at: datetime


class RegistryImportRecord(BaseModel):
    import_id: str
    project_id: str
    selection_id: str
    scan_id: str
    source_selection_relative_path: str
    imported_at: datetime
    status: RegistryImportStatus
    selected_asset_count: int


class RegistryImportReport(BaseModel):
    """Versionierter JSON-Importbericht unter `_otio_v2/registry/imports/`."""

    schema_version: str = IMPORT_REPORT_SCHEMA_VERSION
    import_id: str
    project_id: str
    selection_id: str
    scan_id: str
    imported_at: datetime
    status: RegistryImportStatus
    asset_count: int
    new_asset_count: int = 0
    reused_asset_count: int = 0
    source_groups: list[str] = Field(default_factory=list)
    media_kind_counts: dict[str, int] = Field(default_factory=dict)
    registry_sqlite_relative_path: str = "registry/assets.sqlite3"
    source_selection_relative_path: str = ""


class RegistryImportLatestPointer(BaseModel):
    schema_version: str = IMPORT_REPORT_SCHEMA_VERSION
    import_id: str
    selection_id: str
    scan_id: str
    imported_at: datetime
    status: RegistryImportStatus
    report_relative_path: str


class RegistryImportResult(BaseModel):
    """Ergebnis eines Importversuchs für UI/Tests."""

    status: RegistryImportStatus
    message: str
    import_id: str | None = None
    selection_id: str | None = None
    scan_id: str | None = None
    asset_count: int = 0
    new_asset_count: int = 0
    reused_asset_count: int = 0
    report: RegistryImportReport | None = None
    registry_sqlite_relative_path: str = "registry/assets.sqlite3"
