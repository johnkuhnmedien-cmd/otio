"""Domainvertrag für Discovery-V2 Media-Intake-Planung (Phase 7A, nur Planung)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


INTAKE_PLAN_SCHEMA_VERSION = "1"
INTAKE_PLANNER_VERSION = "2"
PROCESSING_PROFILE_VERSION = "1"


class IntakeAction(str, Enum):
    COPY = "copy"
    REMUX = "remux"
    TRANSCODE = "transcode"
    BLOCKED = "blocked"


class IntakePlanStatus(str, Enum):
    READY = "ready"
    READY_WITH_BLOCKED_ASSETS = "ready_with_blocked_assets"
    BLOCKED = "blocked"
    STALE = "stale"


class IntakePlanItemStatus(str, Enum):
    PLANNED = "planned"
    BLOCKED = "blocked"


class IntakePlanItem(BaseModel):
    asset_id: str
    validation_id: str
    source_relative_path: str
    source_group: str
    media_kind: str
    source_sha256: str | None = None
    extension: str
    container_format: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate_numerator: int | None = None
    frame_rate_denominator: int | None = None
    embedded_timecode: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    duplicate_group_id: str | None = None
    planned_action: IntakeAction
    status: IntakePlanItemStatus
    reason_code: str
    reason_detail: str
    proposed_target_extension: str | None = None
    processing_profile_version: str = PROCESSING_PROFILE_VERSION


class IntakePlan(BaseModel):
    schema_version: str = INTAKE_PLAN_SCHEMA_VERSION
    planner_version: str = INTAKE_PLANNER_VERSION
    plan_id: str
    project_id: str
    import_id: str
    selection_id: str
    scan_id: str
    validation_run_id: str
    created_at: datetime
    status: IntakePlanStatus
    total_assets: int = 0
    copy_count: int = 0
    remux_count: int = 0
    transcode_count: int = 0
    blocked_count: int = 0
    duplicate_warning_count: int = 0
    items: list[IntakePlanItem] = Field(default_factory=list)


class IntakePlanLatestPointer(BaseModel):
    schema_version: str = INTAKE_PLAN_SCHEMA_VERSION
    plan_id: str
    import_id: str
    selection_id: str
    scan_id: str
    validation_run_id: str
    created_at: datetime
    status: IntakePlanStatus
    plan_relative_path: str


class IntakePlanCreateResult(BaseModel):
    """Ergebnis einer bewussten Planerstellung für UI/Tests."""

    created: bool
    message: str
    plan: IntakePlan | None = None


# --- Phase 7B/7C: Intake-Runs / Working Media --------------------------------

INTAKE_RUN_SCHEMA_VERSION = "1"
COPY_INTAKE_WORKER_VERSION = "1"
REMUX_INTAKE_WORKER_VERSION = "1"
VIDEO_TRANSCODE_WORKER_VERSION = "1"
WORKER_INTERRUPTED_INTAKE_ERROR_CODE = "worker_interrupted"
# Kanonische Working-Media-Profile (Whitelist für Pfadsegmente).
COPY_WORKING_PROFILE_VERSION = "copy-v1"
REMUX_WORKING_PROFILE_VERSION = "remux-mp4-v1"
VIDEO_H264_PROFILE_VERSION = "video-h264-v1"
COPY_WORKING_ACTION = "copy"
REMUX_WORKING_ACTION = "remux"
VIDEO_TRANSCODE_ACTION = "transcode"
ALLOWED_WORKING_PROFILE_VERSIONS = frozenset(
    {
        COPY_WORKING_PROFILE_VERSION,
        REMUX_WORKING_PROFILE_VERSION,
        VIDEO_H264_PROFILE_VERSION,
    }
)
INTAKE_RUN_SCOPE_COPY_ONLY = "copy_only"
INTAKE_RUN_SCOPE_REMUX_ONLY = "remux_only"
INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY = "video_transcode_only"


class IntakeRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IntakeRunAssetStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REUSED = "reused"


class WorkingMediaStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    # Legacy-Alias aus früherem 7B-Stand.
    READY = "ready"


ACTIVE_INTAKE_RUN_STATUSES = frozenset(
    {
        IntakeRunStatus.QUEUED,
        IntakeRunStatus.RUNNING,
    }
)

TERMINAL_INTAKE_RUN_STATUSES = frozenset(
    {
        IntakeRunStatus.COMPLETED,
        IntakeRunStatus.COMPLETED_WITH_ERRORS,
        IntakeRunStatus.FAILED,
        IntakeRunStatus.CANCELLED,
    }
)


class IntakeRunRecord(BaseModel):
    run_id: str
    project_id: str
    plan_id: str
    import_id: str
    selection_id: str
    scan_id: str
    validation_run_id: str
    status: IntakeRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    processed_assets: int = 0
    succeeded_assets: int = 0
    failed_assets: int = 0
    skipped_assets: int = 0
    copied_assets: int = 0
    remuxed_assets: int = 0
    transcoded_assets: int = 0
    reused_assets: int = 0
    error_summary: str | None = None
    worker_version: str = COPY_INTAKE_WORKER_VERSION
    scope: str = INTAKE_RUN_SCOPE_COPY_ONLY


class IntakeRunAssetRecord(BaseModel):
    run_asset_id: str
    run_id: str
    plan_id: str
    asset_id: str
    source_relative_path: str
    source_group: str
    media_kind: str
    planned_action: IntakeAction = IntakeAction.COPY
    status: IntakeRunAssetStatus
    source_sha256: str | None = None
    output_sha256: str | None = None
    working_relative_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    processed_at: datetime | None = None


class WorkingMediaRecord(BaseModel):
    working_media_id: str
    project_id: str
    asset_id: str
    plan_id: str
    intake_run_id: str
    source_relative_path: str
    working_relative_path: str
    source_sha256: str
    output_sha256: str
    media_kind: str
    extension: str
    action: str = COPY_WORKING_ACTION
    processing_profile_version: str = COPY_WORKING_PROFILE_VERSION
    status: WorkingMediaStatus = WorkingMediaStatus.COMPLETED
    created_at: datetime
    updated_at: datetime


class IntakeRunReportAsset(BaseModel):
    asset_id: str
    source_relative_path: str
    status: str
    working_relative_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    audio_policy: str | None = None
    timecode_policy: str | None = None
    output_sha256: str | None = None


class IntakeRunReport(BaseModel):
    schema_version: str = INTAKE_RUN_SCHEMA_VERSION
    run_id: str
    project_id: str
    plan_id: str
    import_id: str
    selection_id: str
    scan_id: str
    validation_run_id: str
    status: IntakeRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    processed_assets: int = 0
    succeeded_assets: int = 0
    failed_assets: int = 0
    skipped_assets: int = 0
    copied_assets: int = 0
    remuxed_assets: int = 0
    transcoded_assets: int = 0
    # Kurzer JSON-Alias für Video-Transcode-Berichte (gleicher Wert).
    transcoded: int = 0
    reused_assets: int = 0
    # Kurzer JSON-Alias (gleicher Wert wie reused_assets).
    reused: int = 0
    # Kurzer JSON-Alias für fehlgeschlagene Assets.
    failed: int = 0
    error_summary: str | None = None
    worker_version: str = COPY_INTAKE_WORKER_VERSION
    scope: str = INTAKE_RUN_SCOPE_COPY_ONLY
    report_relative_path: str = ""
    registry_sqlite_relative_path: str = "registry/assets.sqlite3"
    assets: list[IntakeRunReportAsset] = Field(default_factory=list)


class IntakeRunLatestPointer(BaseModel):
    schema_version: str = INTAKE_RUN_SCHEMA_VERSION
    run_id: str
    plan_id: str
    import_id: str
    selection_id: str
    scan_id: str
    validation_run_id: str
    status: IntakeRunStatus
    completed_at: datetime | None = None
    report_relative_path: str
    scope: str = INTAKE_RUN_SCOPE_COPY_ONLY


class IntakeRunStartResult(BaseModel):
    started: bool
    message: str
    run: IntakeRunRecord | None = None
