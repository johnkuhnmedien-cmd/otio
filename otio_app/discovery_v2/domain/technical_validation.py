"""Domainvertrag für Discovery-V2 technische Medienprüfung."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


VALIDATION_REPORT_SCHEMA_VERSION = "1"


class ValidationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssetValidationStatus(str, Enum):
    PROBE_SUCCEEDED = "probe_succeeded"
    PROBE_FAILED = "probe_failed"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    UNSUPPORTED_MEDIA_KIND = "unsupported_media_kind"
    VALIDATION_ERROR = "validation_error"


ACTIVE_RUN_STATUSES = frozenset(
    {
        ValidationRunStatus.QUEUED,
        ValidationRunStatus.RUNNING,
    }
)

TERMINAL_RUN_STATUSES = frozenset(
    {
        ValidationRunStatus.COMPLETED,
        ValidationRunStatus.COMPLETED_WITH_ERRORS,
        ValidationRunStatus.FAILED,
        ValidationRunStatus.CANCELLED,
    }
)


class ValidationRunRecord(BaseModel):
    run_id: str
    project_id: str
    import_id: str
    selection_id: str
    scan_id: str
    status: ValidationRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    processed_assets: int = 0
    successful_assets: int = 0
    failed_assets: int = 0
    error_summary: str | None = None


class AssetValidationRecord(BaseModel):
    validation_id: str
    run_id: str
    asset_id: str
    source_relative_path: str
    status: AssetValidationStatus
    checked_size_bytes: int | None = None
    checked_mtime_ns: int | None = None
    sha256: str | None = None
    media_kind: str | None = None
    container_format: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    frame_rate_numerator: int | None = None
    frame_rate_denominator: int | None = None
    audio_stream_count: int | None = None
    embedded_timecode: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    validated_at: datetime
    duplicate_group_id: str | None = None
    duplicate_hint: str | None = None
    source_group: str | None = None


class DuplicateGroupRecord(BaseModel):
    duplicate_group_id: str
    project_id: str
    run_id: str
    sha256: str
    member_count: int
    created_at: datetime
    hint: str = "potential_content_duplicate"


class ValidationRunReport(BaseModel):
    """Versionierter JSON-Prüfbericht unter `_otio_v2/validation/runs/`."""

    schema_version: str = VALIDATION_REPORT_SCHEMA_VERSION
    run_id: str
    project_id: str
    import_id: str
    selection_id: str
    scan_id: str
    status: ValidationRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    processed_assets: int = 0
    successful_assets: int = 0
    failed_assets: int = 0
    source_missing_count: int = 0
    source_changed_count: int = 0
    potential_duplicate_count: int = 0
    error_summary: str | None = None
    registry_sqlite_relative_path: str = "registry/assets.sqlite3"
    report_relative_path: str = ""


class ValidationLatestPointer(BaseModel):
    schema_version: str = VALIDATION_REPORT_SCHEMA_VERSION
    run_id: str
    import_id: str
    selection_id: str
    scan_id: str
    status: ValidationRunStatus
    completed_at: datetime | None = None
    report_relative_path: str


class ValidationStartResult(BaseModel):
    """Ergebnis eines Startversuchs für UI/Tests."""

    started: bool
    message: str
    run: ValidationRunRecord | None = None
