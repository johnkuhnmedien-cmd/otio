"""Domainverträge für Discovery-V2-Assetanalyse (Phase 8A — nur Contracts)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


ANALYSIS_CONTRACT_PROFILE_VERSION = "analysis-contract-v1"
ANALYSIS_RUN_SCHEMA_VERSION = "1"
ANALYSIS_RUN_SCOPE_PREPARE = "prepare"
ANALYSIS_RUN_SCOPE_MODEL = "model"


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


# Explizit nicht in Phase 8A verwendet (Dokumentation / Guardrails).
FORBIDDEN_PHASE8A_STATUSES = frozenset(
    {
        "analyzing",
        "model_completed",
        "editorially_approved",
        "ready_for_visual_beats",
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
    """Vertragsobjekt — Phase 8A startet keinen ausführenden Run."""

    run_id: str
    project_id: str
    scope: str
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION
    status: AnalysisRunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_assets: int = 0
    prepared_assets: int = 0
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
    """Vertragsform — Phase 8A erzeugt keine Instanzen im Produktablauf."""

    shot_id: str
    working_media_id: str
    ordinal: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    detection_profile_version: str


class RepresentativeFrameContract(BaseModel):
    """Vertragsform — Phase 8A erzeugt keine Instanzen im Produktablauf."""

    frame_id: str
    working_media_id: str
    shot_id: str | None = None
    timestamp_seconds: float | None = None
    relative_path: str
    frame_sha256: str
    sampling_profile_version: str


class AnalysisRunReportCounts(BaseModel):
    total_assets: int = 0
    prepared_assets: int = 0
    not_applicable_assets: int = 0
    failed_assets: int = 0
    interrupted_assets: int = 0


class AnalysisRunReportError(BaseModel):
    asset_id: str | None = None
    error_code: str
    error_message: str | None = None


class AnalysisRunReport(BaseModel):
    """Versionierter JSON-Vertrag für zukünftige Analyse-Run-Artefakte."""

    schema_version: str = ANALYSIS_RUN_SCHEMA_VERSION
    run_id: str
    project_id: str
    scope: str
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION
    status: AnalysisRunStatus
    input_identities: list[AnalysisInputIdentity] = Field(default_factory=list)
    counts: AnalysisRunReportCounts = Field(default_factory=AnalysisRunReportCounts)
    errors: list[AnalysisRunReportError] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None

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
