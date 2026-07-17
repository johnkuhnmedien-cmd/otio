"""Domain contracts for Discovery V2 Phase 13 OTIO export."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EXPORT_SCHEMA_VERSION = "export-v1"
EDITORIAL_APPROVAL_SCHEMA_VERSION = "editorial-approval-v1"
EXPORT_PROFILE_VERSION = "discovery-otio-export-v1"
OTIO_LIBRARY_VERSION = "0.18.1"

EXPORT_RUN_SCOPE_VALIDATION = "export_validation_only"
EXPORT_RUN_SCOPE_OTIO = "otio_export_only"
EXPORT_RUN_SCOPE_REPARSE = "otio_reparse_only"

EXPORT_ERROR_EDITORIAL_APPROVAL_REQUIRED = "editorial_approval_required"
EXPORT_ERROR_EDITORIAL_APPROVAL_REJECTED = "editorial_approval_rejected"
EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED = "editorial_approval_invalidated"
EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED = "editorial_approval_confirmation_required"
EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH = "editorial_approval_fingerprint_mismatch"
EXPORT_ERROR_VALIDATION_REQUIRED = "export_validation_required"
EXPORT_ERROR_VALIDATION_FAILED = "export_validation_failed"
EXPORT_ERROR_BLOCKING_ISSUE = "export_blocking_issue"
EXPORT_ERROR_INVALID_MEDIA_REFERENCE = "invalid_export_media_reference"
EXPORT_ERROR_INVALID_AUDIO_REFERENCE = "invalid_export_audio_reference"
EXPORT_ERROR_INVALID_SOURCE_RANGE = "invalid_export_source_range"
EXPORT_ERROR_INVALID_TIMEBASE = "invalid_export_timebase"
EXPORT_ERROR_PLANNED_GRAPHIC = "planned_graphic_not_exportable"
EXPORT_ERROR_OTIO_EXPORT_FAILED = "otio_export_failed"
EXPORT_ERROR_ARTIFACT_CONFLICT = "otio_artifact_conflict"
EXPORT_ERROR_SERIALIZE_FAILED = "otio_serialize_failed"
EXPORT_ERROR_REPARSE_FAILED = "otio_reparse_failed"
EXPORT_ERROR_SEMANTIC_MISMATCH = "otio_semantic_mismatch"
EXPORT_ERROR_INPUT_STALE = "export_input_stale"
EXPORT_ERROR_RUN_ALREADY_ACTIVE = "export_run_already_active"
EXPORT_ERROR_VISUAL_EDIT_RUN_ALREADY_ACTIVE = "visual_edit_run_already_active"
EXPORT_ERROR_NARRATION_RUN_ALREADY_ACTIVE = "narration_run_already_active"
EXPORT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE = "analysis_run_already_active"
EXPORT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE = "editorial_run_already_active"
EXPORT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE = "supplementation_run_already_active"
EXPORT_ERROR_REGISTRY_WRITE_FAILED = "export_registry_write_failed"
EXPORT_ERROR_ARTIFACT_WRITE_FAILED = "export_artifact_write_failed"
EXPORT_ERROR_WORKER_INTERRUPTED = "worker_interrupted"
EXPORT_ERROR_REPORT_WRITE_FAILED = "report_write_failed"

EXPORT_ERROR_CODES = (
    EXPORT_ERROR_EDITORIAL_APPROVAL_REQUIRED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_REJECTED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH,
    EXPORT_ERROR_VALIDATION_REQUIRED,
    EXPORT_ERROR_VALIDATION_FAILED,
    EXPORT_ERROR_BLOCKING_ISSUE,
    EXPORT_ERROR_INVALID_MEDIA_REFERENCE,
    EXPORT_ERROR_INVALID_AUDIO_REFERENCE,
    EXPORT_ERROR_INVALID_SOURCE_RANGE,
    EXPORT_ERROR_INVALID_TIMEBASE,
    EXPORT_ERROR_PLANNED_GRAPHIC,
    EXPORT_ERROR_OTIO_EXPORT_FAILED,
    EXPORT_ERROR_ARTIFACT_CONFLICT,
    EXPORT_ERROR_SERIALIZE_FAILED,
    EXPORT_ERROR_REPARSE_FAILED,
    EXPORT_ERROR_SEMANTIC_MISMATCH,
    EXPORT_ERROR_INPUT_STALE,
    EXPORT_ERROR_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_VISUAL_EDIT_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_NARRATION_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_REGISTRY_WRITE_FAILED,
    EXPORT_ERROR_ARTIFACT_WRITE_FAILED,
    EXPORT_ERROR_WORKER_INTERRUPTED,
    EXPORT_ERROR_REPORT_WRITE_FAILED,
)


class EditorialApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class ExportValidationReportStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"
    SUPERSEDED = "superseded"


class ExportIssueSeverity(str, Enum):
    WARNING = "warning"
    BLOCKING = "blocking"


class OtioExportRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


ACTIVE_EXPORT_RUN_STATUSES = frozenset({OtioExportRunStatus.QUEUED, OtioExportRunStatus.RUNNING})


class OtioReparseReportStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class AcceptedExportRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: str
    category: str
    description: str
    source_ref: str


class EditorialApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    project_id: str
    visual_edit_plan_id: str
    humanity_review_id: str
    feasibility_report_id: str
    script_lock_id: str
    narration_timeline_id: str
    input_fingerprint: str
    user_decision: Literal["approved", "rejected"]
    user_comment: str = ""
    accepted_visible_risks: list[AcceptedExportRisk] = Field(default_factory=list)
    confirmation_checked: bool
    status: EditorialApprovalStatus
    revision: int = Field(ge=1)
    created_at: datetime
    schema_version: str = EXPORT_SCHEMA_VERSION


class ExportValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    report_id: str
    shot_id: str | None = None
    assignment_id: str | None = None
    error_code: str
    severity: ExportIssueSeverity
    technical_details: str
    blocks_export: bool


class ExportValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    approval_id: str
    visual_edit_plan_id: str
    input_fingerprint: str
    otio_profile_version: str = EXPORT_PROFILE_VERSION
    timebase: str
    status: ExportValidationReportStatus
    issues: list[ExportValidationIssue] = Field(default_factory=list)
    metrics: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    schema_version: str = EXPORT_SCHEMA_VERSION


class OtioExportRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    approval_id: str
    validation_report_id: str
    visual_edit_plan_id: str
    export_profile_version: str = EXPORT_PROFILE_VERSION
    input_fingerprint: str
    output_relative_path: str | None = None
    otio_sha256: str | None = None
    status: OtioExportRunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    relative_report_path: str | None = None
    schema_version: str = EXPORT_SCHEMA_VERSION


class OtioExportArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    relative_path: str
    byte_size: int = Field(ge=0)
    sha256: str
    otio_library_version: str = OTIO_LIBRARY_VERSION
    track_count: int = Field(ge=0)
    clip_count: int = Field(ge=0)
    total_duration_seconds: float = Field(ge=0.0)
    total_frames: int = Field(ge=0)
    timebase: str
    created_at: datetime
    schema_version: str = EXPORT_SCHEMA_VERSION


class OtioReparseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    export_run_id: str
    artifact_id: str | None = None
    parseable: bool
    semantically_equivalent: bool
    deviations: list[str] = Field(default_factory=list)
    track_count: int = Field(ge=0)
    clip_count: int = Field(ge=0)
    total_duration_seconds: float = Field(ge=0.0)
    total_frames: int = Field(ge=0)
    timebase: str
    status: OtioReparseReportStatus
    created_at: datetime
    schema_version: str = EXPORT_SCHEMA_VERSION


class ExportProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    current_editorial_approval_id: str | None = None
    current_export_validation_report_id: str | None = None
    current_otio_export_run_id: str | None = None
    current_otio_artifact_id: str | None = None
    current_reparse_report_id: str | None = None
    current_visual_edit_plan_id: str | None = None
    current_narration_timeline_id: str | None = None
    updated_at: datetime


class ExportMediaReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    asset_id: str | None = None
    working_media_id: str | None = None
    voice_segment_id: str | None = None
    relative_path: str
    absolute_target_url: str
    media_kind: Literal["video", "photo", "audio"]
    sha256: str | None = None


class ExportVideoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    ordinal: int = Field(ge=0)
    item_type: Literal["clip", "gap"]
    name: str
    duration_frames: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    media_strategy: str
    media_reference_id: str | None = None
    asset_id: str | None = None
    working_media_id: str | None = None
    assignment_id: str | None = None
    source_in_frame: int | None = None
    source_out_frame: int | None = None
    source_in_seconds: float | None = None
    source_out_seconds: float | None = None
    sentence_ids: list[str] = Field(default_factory=list)
    visual_beat_ids: list[str] = Field(default_factory=list)
    visual_intent_ids: list[str] = Field(default_factory=list)
    narration_entry_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ExportAudioItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ordinal: int = Field(ge=0)
    item_type: Literal["clip", "gap"]
    name: str
    duration_frames: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    media_reference_id: str | None = None
    sentence_id: str | None = None
    voice_segment_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ExportTransitionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    from_shot_id: str
    to_shot_id: str
    technical_type: Literal["dissolve"]
    duration_frames: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    metadata: dict[str, object] = Field(default_factory=dict)


class ExportContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    approval_id: str
    validation_report_id: str
    visual_edit_plan_id: str
    humanity_review_id: str
    feasibility_report_id: str
    script_lock_id: str
    narration_timeline_id: str
    timeline_name: str
    export_profile_version: str = EXPORT_PROFILE_VERSION
    input_fingerprint: str
    fps_numerator: int = Field(gt=0)
    fps_denominator: int = Field(gt=0)
    fps: float = Field(gt=0)
    total_frames: int = Field(ge=0)
    total_duration_seconds: float = Field(ge=0.0)
    media_references: list[ExportMediaReference] = Field(default_factory=list)
    video_items: list[ExportVideoItem] = Field(default_factory=list)
    audio_items: list[ExportAudioItem] = Field(default_factory=list)
    transitions: list[ExportTransitionItem] = Field(default_factory=list)
    metrics: dict[str, object] = Field(default_factory=dict)
    schema_version: str = EXPORT_SCHEMA_VERSION


class ExportValidationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: ExportValidationReport
    contract: ExportContract | None = None


def compute_export_sha256(value: object) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def timeline_name_for(project_id: str, plan_version: int) -> str:
    return f"discovery_v2_{project_id[:8]}_{plan_version}"


def canonical_fingerprint(payload: dict[str, object]) -> str:
    return compute_export_sha256(payload)


__all__ = [name for name in globals() if not name.startswith("_")]
