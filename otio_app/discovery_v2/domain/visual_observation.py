"""Domain contracts for Discovery-V2 fake vision model analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


GATEWAY_VERSION = "discovery-vision-gateway-v1"
PROMPT_VERSION = "vision-prompt-v1"
RESPONSE_SCHEMA_VERSION = "visual-observation-v1"
ANALYSIS_MODEL_PROFILE = "analysis-model-v1"

ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED = "analysis_consent_required"
ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED = "analysis_frame_limit_exceeded"
ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING = "analysis_frame_missing"
ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH = "analysis_frame_hash_mismatch"
ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED = "analysis_gateway_unconfigured"
ANALYSIS_ERROR_VISION_MODEL_UNAVAILABLE = "vision_model_unavailable"
ANALYSIS_ERROR_MODEL_RESPONSE_INVALID = "model_response_invalid"
ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH = "model_response_schema_mismatch"
ANALYSIS_ERROR_PROVIDER_TRANSIENT = "provider_transient_error"
ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED = "analysis_retry_exhausted"
ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED = "analysis_registry_write_failed"
ANALYSIS_ERROR_WORKER_INTERRUPTED = "worker_interrupted"

ANALYSIS_MODEL_ERROR_CODES = (
    ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING,
    ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH,
    ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED,
    ANALYSIS_ERROR_VISION_MODEL_UNAVAILABLE,
    ANALYSIS_ERROR_MODEL_RESPONSE_INVALID,
    ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH,
    ANALYSIS_ERROR_PROVIDER_TRANSIENT,
    ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED,
    ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED,
    ANALYSIS_ERROR_WORKER_INTERRUPTED,
)

IndoorOutdoor = Literal["indoor", "outdoor", "mixed", "unknown"]
DayNight = Literal["day", "night", "mixed", "unknown"]
CrowdLevel = Literal["none", "few", "many", "crowd", "unknown"]
CameraScale = Literal[
    "extreme_closeup",
    "closeup",
    "medium",
    "wide",
    "aerial",
    "unknown",
]
CameraMotionHint = Literal[
    "static",
    "pan",
    "tilt",
    "handheld",
    "tracking",
    "unknown",
]


class AnalysisModelAssetStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    REUSED = "reused"
    NOT_APPLICABLE = "not_applicable"


class VisualObservation(BaseModel):
    """Strict untrusted-model response schema for visual observations."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    visible_subjects: list[str]
    actions: list[str]
    setting: str | None
    indoor_outdoor: IndoorOutdoor
    day_night: DayNight
    people_present: bool | None
    crowd_level: CrowdLevel
    camera_scale: CameraScale
    camera_motion_hint: CameraMotionHint
    visual_quality_notes: list[str]
    readable_text_present: bool | None
    readable_text_summary: str | None
    possible_location_clues: list[str]
    geographic_confidence: float = Field(ge=0.0, le=1.0)
    landmark_candidates: list[str]
    weather_visible: str | None
    safety_or_sensitive_content: list[str]
    possible_synthetic_indicators: list[str]
    synthetic_confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_notes: list[str]
    evidence_frame_ids: list[str]
    editorial_signals: list[str]

    @field_validator("evidence_frame_ids")
    @classmethod
    def _evidence_ids_must_be_present(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("evidence_frame_ids must not be empty")
        if any(not str(item).strip() for item in value):
            raise ValueError("evidence_frame_ids must not contain blank values")
        return value


@dataclass(frozen=True)
class VisionConfig:
    provider: str
    enabled: bool
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    max_retries: int
    max_frames_per_video: int
    max_frames_per_run: int
    max_frame_bytes: int
    max_run_bytes: int
    timeout_seconds: int


class VisionFramePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str
    relative_path: str
    mime_type: str
    frame_sha256: str
    file_size_bytes: int = Field(ge=0)
    ordinal: int = Field(ge=0)


class VisionGatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str
    asset_id: str
    analysis_identity_id: str
    media_kind: str
    prompt: str
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    frames: list[VisionFramePart]

    @property
    def frame_ids(self) -> set[str]:
        return {frame.frame_id for frame in self.frames}


class VisionGatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: VisualObservation
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    attempt_count: int = Field(ge=1)


class ModelAnalysisAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    analysis_identity_id: str
    project_id: str
    asset_id: str
    run_id: str
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    status: Literal["queued", "running", "completed", "failed", "reused", "interrupted"]
    attempt_number: int = Field(ge=1)
    error_code: str | None = None
    error_message: str | None = None
    frame_count: int = Field(ge=0)
    frame_hash_fingerprint: str
    created_at: datetime
    completed_at: datetime | None = None


class AnalysisConsentEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_id: str
    project_id: str
    run_id: str
    created_at: datetime
    frame_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    acknowledged: bool
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str


class VisualObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    analysis_identity_id: str
    project_id: str
    asset_id: str
    attempt_id: str
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    frame_hash_fingerprint: str
    relative_json_path: str
    observation_json: str
    created_at: datetime


__all__ = [
    "ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED",
    "ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH",
    "ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED",
    "ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING",
    "ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED",
    "ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED",
    "ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED",
    "ANALYSIS_ERROR_MODEL_RESPONSE_INVALID",
    "ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH",
    "ANALYSIS_ERROR_PROVIDER_TRANSIENT",
    "ANALYSIS_ERROR_VISION_MODEL_UNAVAILABLE",
    "ANALYSIS_ERROR_WORKER_INTERRUPTED",
    "ANALYSIS_MODEL_ERROR_CODES",
    "ANALYSIS_MODEL_PROFILE",
    "GATEWAY_VERSION",
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA_VERSION",
    "AnalysisConsentEventRecord",
    "AnalysisModelAssetStatus",
    "ModelAnalysisAttemptRecord",
    "VisionConfig",
    "VisionFramePart",
    "VisionGatewayRequest",
    "VisionGatewayResponse",
    "VisualObservation",
    "VisualObservationRecord",
]
