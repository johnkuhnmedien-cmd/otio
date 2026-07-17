"""Domainverträge für Discovery-V2-Assetanalyse (Phase 8A/8B)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


ANALYSIS_CONTRACT_PROFILE_VERSION = "analysis-contract-v1"
ANALYSIS_PREPARE_PROFILE_VERSION = "analysis-prepare-v1"
SHOT_DETECT_PROFILE_VERSION = "shot-detect-v1"
FRAME_SAMPLE_PROFILE_VERSION = "frame-sample-v1"
ANALYSIS_RUN_SCHEMA_VERSION = "1"
ANALYSIS_RUN_SCOPE_PREPARE = "prepare"  # Legacy-Vertragsalias
ANALYSIS_RUN_SCOPE_MODEL = "model"
ANALYSIS_RUN_SCOPE_PREPARE_ONLY = "analysis_prepare_only"

WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE = "worker_interrupted"


class AnalysisRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisPrepareAssetStatus(str, Enum):
    PENDING = "pending"
    DETECTING_SHOTS = "detecting_shots"
    EXTRACTING_FRAMES = "extracting_frames"
    PREPARED = "prepared"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NOT_APPLICABLE = "not_applicable"


# Explizit nicht in Phase 8A/8B verwendet (Dokumentation / Guardrails).
FORBIDDEN_PHASE8A_STATUSES = frozenset(
    {
        "analyzing",
        "model_completed",
        "editorially_approved",
        "ready_for_visual_beats",
    }
)

ACTIVE_ANALYSIS_RUN_STATUSES = frozenset(
    {
        AnalysisRunStatus.QUEUED,
        AnalysisRunStatus.RUNNING,
    }
)


class AnalysisInputIdentity(BaseModel):
    project_id: str
    asset_id: str
    working_media_id: str
    validation_id: str
    source_sha256: str
    output_sha256: str
    processing_profile_version: str
    media_kind: str
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION


class AnalysisEligibility(BaseModel):
    asset_id: str
    working_media_id: str | None = None
    eligible: bool
    reason_code: str | None = None
    expected_action: str | None = None
    expected_processing_profile_version: str | None = None
    actual_processing_profile_version: str | None = None
    media_kind: str
    source_group: str
    source_relative_path: str
    output_sha256: str | None = None
    validation_id: str | None = None
    display_name: str | None = None


class AnalysisRun(BaseModel):
    run_id: str
    project_id: str
    scope: str
    analysis_profile_version: str = ANALYSIS_PREPARE_PROFILE_VERSION
    status: AnalysisRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    prepared_assets: int = 0
    reused_assets: int = 0
    not_applicable_assets: int = 0
    failed_assets: int = 0
    interrupted_assets: int = 0
    error_summary: str | None = None


class AnalysisRunAsset(BaseModel):
    run_id: str
    asset_id: str
    working_media_id: str
    validation_id: str
    source_sha256: str
    output_sha256: str
    processing_profile_version: str
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION
    media_kind: str
    status: AnalysisPrepareAssetStatus
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    analysis_identity_id: str | None = None


class AnalysisIdentityRecord(BaseModel):
    analysis_identity_id: str
    project_id: str
    asset_id: str
    working_media_id: str
    output_sha256: str
    processing_profile_version: str
    analysis_profile_version: str
    created_at: datetime


class TechnicalShotContract(BaseModel):
    """Vertragsform — Phase 8A erzeugte keine Instanzen; Phase 8B persistiert."""

    shot_id: str
    working_media_id: str
    ordinal: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    detection_profile_version: str


class RepresentativeFrameContract(BaseModel):
    """Vertragsform — Phase 8A erzeugte keine Instanzen; Phase 8B persistiert."""

    frame_id: str
    working_media_id: str
    shot_id: str | None = None
    timestamp_seconds: float | None = None
    relative_path: str
    frame_sha256: str
    sampling_profile_version: str


class TechnicalShotRecord(BaseModel):
    shot_id: str
    analysis_identity_id: str
    project_id: str
    asset_id: str
    working_media_id: str
    ordinal: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    detection_profile_version: str = SHOT_DETECT_PROFILE_VERSION
    created_at: datetime

    @model_validator(mode="after")
    def _validate_bounds(self) -> TechnicalShotRecord:
        if self.ordinal < 0:
            raise ValueError("ordinal muss >= 0 sein")
        if self.start_seconds < 0:
            raise ValueError("start_seconds muss >= 0 sein")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds muss > start_seconds sein")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds muss > 0 sein")
        return self


class RepresentativeFrameRecord(BaseModel):
    frame_id: str
    analysis_identity_id: str
    project_id: str
    asset_id: str
    working_media_id: str
    shot_id: str | None = None
    ordinal: int
    timestamp_seconds: float | None = None
    relative_path: str
    frame_sha256: str
    pixel_sha256: str
    file_size_bytes: int
    width: int
    height: int
    sampling_profile_version: str = FRAME_SAMPLE_PROFILE_VERSION
    brightness_mean: float
    black_fraction: float
    sharpness_score: float
    is_black: bool
    created_at: datetime

    @model_validator(mode="after")
    def _validate_frame(self) -> RepresentativeFrameRecord:
        if self.ordinal < 0:
            raise ValueError("ordinal muss >= 0 sein")
        if self.file_size_bytes < 0:
            raise ValueError("file_size_bytes muss >= 0 sein")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height müssen > 0 sein")
        # Shot-Frame: Timestamp Pflicht. Standbild: beides null.
        # Overview-Frame: shot_id null, timestamp gesetzt.
        if self.shot_id is not None and self.timestamp_seconds is None:
            raise ValueError("Shot-Frame erfordert timestamp_seconds")
        if self.timestamp_seconds is None and self.shot_id is not None:
            raise ValueError("timestamp_seconds=null erfordert shot_id=null")
        return self


class AnalysisRunReportCounts(BaseModel):
    total_assets: int = 0
    prepared_assets: int = 0
    reused_assets: int = 0
    not_applicable_assets: int = 0
    failed_assets: int = 0
    interrupted_assets: int = 0
    shot_count: int = 0
    frame_count: int = 0


class AnalysisRunReportError(BaseModel):
    asset_id: str | None = None
    error_code: str
    error_message: str | None = None


class AnalysisRunReportAsset(BaseModel):
    analysis_identity_id: str | None = None
    asset_id: str
    working_media_id: str
    media_kind: str
    status: AnalysisPrepareAssetStatus
    shot_count: int = 0
    frame_count: int = 0
    relative_frame_paths: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class AnalysisRunReport(BaseModel):
    """Versionierter JSON-Vertrag für Analyse-Run-Artefakte."""

    schema_version: str = ANALYSIS_RUN_SCHEMA_VERSION
    run_id: str
    project_id: str
    scope: str = ANALYSIS_RUN_SCOPE_PREPARE_ONLY
    analysis_profile_version: str = ANALYSIS_PREPARE_PROFILE_VERSION
    status: AnalysisRunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    input_identities: list[AnalysisInputIdentity] = Field(default_factory=list)
    counts: AnalysisRunReportCounts = Field(default_factory=AnalysisRunReportCounts)
    assets: list[AnalysisRunReportAsset] = Field(default_factory=list)
    errors: list[AnalysisRunReportError] = Field(default_factory=list)
    total_assets: int = 0
    prepared_assets: int = 0
    reused_assets: int = 0
    not_applicable_assets: int = 0
    failed_assets: int = 0
    interrupted_assets: int = 0
    shot_count: int = 0
    frame_count: int = 0

    @field_validator("schema_version")
    @classmethod
    def _schema_must_be_supported(cls, value: str) -> str:
        if value != ANALYSIS_RUN_SCHEMA_VERSION:
            raise ValueError(
                f"Ungültige Analysis-Run-Schema-Version: {value} "
                f"(erwartet {ANALYSIS_RUN_SCHEMA_VERSION})"
            )
        return value


def prepared_is_not_model_analyzed(status: AnalysisPrepareAssetStatus) -> bool:
    """Guard: prepared bedeutet nicht modellanalysiert."""
    return status == AnalysisPrepareAssetStatus.PREPARED
