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


# --- Phase 7B: Copy-Intake-Runs / Working Media --------------------------------

INTAKE_RUN_SCHEMA_VERSION = "1"
COPY_INTAKE_WORKER_VERSION = "1"
WORKER_INTERRUPTED_INTAKE_ERROR_CODE = "worker_interrupted"


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


class WorkingMediaStatus(str, Enum):
    READY = "ready"
    FAILED = "failed"


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
    error_summary: str | None = None
    worker_version: str = COPY_INTAKE_WORKER_VERSION


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
    status: WorkingMediaStatus = WorkingMediaStatus.READY
    created_at: datetime
    updated_at: datetime


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
    error_summary: str | None = None
    worker_version: str = COPY_INTAKE_WORKER_VERSION
    report_relative_path: str = ""


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


class IntakeRunStartResult(BaseModel):
    started: bool
    message: str
    run: IntakeRunRecord | None = None
