"""Domain contracts for Discovery-V2 visual observation reviews."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ObservationReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REANALYZE_REQUESTED = "reanalyze_requested"
    REJECTED = "rejected"


ObservationReviewDecisionLiteral = Literal[
    "accepted",
    "reanalyze_requested",
    "rejected",
]
ObservationReviewEffectiveStatus = Literal[
    "unreviewed",
    "accepted",
    "reanalyze_requested",
    "rejected",
]

OBSERVATION_REVIEW_STATUS_UNREVIEWED = "unreviewed"

OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING = "visual_observation_missing"
OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE = "visual_observation_stale"
OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH = (
    "visual_observation_hash_mismatch"
)
OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_INVALID = "visual_observation_invalid"
OBSERVATION_REVIEW_ERROR_REASON_REQUIRED = "observation_review_reason_required"
OBSERVATION_REVIEW_ERROR_CONFLICT = "observation_review_conflict"
OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED = "analysis_registry_write_failed"

PHASE8_ASSET_STATUS_NOT_ELIGIBLE = "not_eligible"
PHASE8_ASSET_STATUS_ELIGIBLE_NOT_PREPARED = "eligible_not_prepared"
PHASE8_ASSET_STATUS_PREPARED = "prepared"
PHASE8_ASSET_STATUS_MODEL_NOT_STARTED = "model_not_started"
PHASE8_ASSET_STATUS_MODEL_FAILED = "model_failed"
PHASE8_ASSET_STATUS_OBSERVATION_UNREVIEWED = "observation_unreviewed"
PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED = "observation_accepted"
PHASE8_ASSET_STATUS_REANALYSIS_REQUESTED = "reanalysis_requested"
PHASE8_ASSET_STATUS_OBSERVATION_REJECTED = "observation_rejected"
PHASE8_ASSET_STATUS_STALE = "stale"
PHASE8_ASSET_STATUS_BLOCKED = "blocked"


class ObservationReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    observation_id: str
    analysis_identity_id: str
    project_id: str
    asset_id: str
    working_media_id: str
    observation_sha256: str
    frame_set_fingerprint: str
    review_revision: int = Field(ge=1)
    decision: ObservationReviewDecisionLiteral
    reason_code: str | None = None
    review_note: str | None = None
    created_at: datetime
    supersedes_review_id: str | None = None


def compute_observation_sha256(observation_json: str) -> str:
    """Hash exactly the stored JSON text bytes."""

    return hashlib.sha256(observation_json.encode("utf-8")).hexdigest()


__all__ = [
    "OBSERVATION_REVIEW_ERROR_CONFLICT",
    "OBSERVATION_REVIEW_ERROR_REASON_REQUIRED",
    "OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED",
    "OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH",
    "OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_INVALID",
    "OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING",
    "OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE",
    "OBSERVATION_REVIEW_STATUS_UNREVIEWED",
    "PHASE8_ASSET_STATUS_BLOCKED",
    "PHASE8_ASSET_STATUS_ELIGIBLE_NOT_PREPARED",
    "PHASE8_ASSET_STATUS_MODEL_FAILED",
    "PHASE8_ASSET_STATUS_MODEL_NOT_STARTED",
    "PHASE8_ASSET_STATUS_NOT_ELIGIBLE",
    "PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED",
    "PHASE8_ASSET_STATUS_OBSERVATION_REJECTED",
    "PHASE8_ASSET_STATUS_OBSERVATION_UNREVIEWED",
    "PHASE8_ASSET_STATUS_PREPARED",
    "PHASE8_ASSET_STATUS_REANALYSIS_REQUESTED",
    "PHASE8_ASSET_STATUS_STALE",
    "ObservationReviewDecision",
    "ObservationReviewDecisionLiteral",
    "ObservationReviewEffectiveStatus",
    "ObservationReviewRecord",
    "compute_observation_sha256",
]
