"""Domain contracts for Discovery V2 Phase 10 supplementation and script lock."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from otio_app.discovery_v2.domain.editorial import compute_text_sha256

SUPPLEMENTATION_SCHEMA_VERSION = "supplementation-v1"
STOCK_GATEWAY_VERSION = "discovery-stock-gateway-v1"
FAKE_STOCK_ADAPTER_VERSION = "fake-stock-v1"
STOCK_PROVIDER_FAKE = "fake"

SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW = "supplementation_local_review_only"
SUPPLEMENTATION_RUN_SCOPE_SEARCH = "supplementation_search_only"
SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION = (
    "supplementation_candidate_validation_only"
)

MAX_STOCK_CANDIDATES_PER_ATTEMPT = 10
MAX_SEARCH_ATTEMPTS_PER_GAP_VERSION = 5

SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING = "coverage_gap_missing"
SUPPLEMENTATION_ERROR_COVERAGE_GAP_STALE = "coverage_gap_stale"
SUPPLEMENTATION_ERROR_REQUEST_INVALID = "supplementation_request_invalid"
SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED = "supplementation_gateway_unconfigured"
SUPPLEMENTATION_ERROR_PROVIDER_UNAVAILABLE = "supplementation_provider_unavailable"
SUPPLEMENTATION_ERROR_RESPONSE_INVALID = "supplementation_response_invalid"
SUPPLEMENTATION_ERROR_INVALID_STOCK_CANDIDATE = "invalid_stock_candidate"
SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_DUPLICATE = "stock_candidate_duplicate"
SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_PREVIEW_MISSING = (
    "stock_candidate_preview_missing"
)
SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_NOT_ACCEPTED = "stock_candidate_not_accepted"
SUPPLEMENTATION_ERROR_STOCK_LICENSE_UNKNOWN = "stock_license_unknown"
SUPPLEMENTATION_ERROR_STOCK_OAUTH_UNKNOWN = "stock_oauth_unknown"
SUPPLEMENTATION_ERROR_RETRY_EXHAUSTED = "supplementation_retry_exhausted"
SUPPLEMENTATION_ERROR_CLAIM_DECISION_REQUIRED = "claim_decision_required"
SUPPLEMENTATION_ERROR_CLAIM_DECISION_STALE = "claim_decision_stale"
SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET = (
    "script_lock_requirements_not_met"
)
SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFIRMATION_REQUIRED = (
    "script_lock_confirmation_required"
)
SUPPLEMENTATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH = (
    "script_lock_fingerprint_mismatch"
)
SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFLICT = "script_lock_conflict"
SUPPLEMENTATION_ERROR_SCRIPT_LOCK_INVALIDATED = "script_lock_invalidated"
SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE = "supplementation_run_already_active"
SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE = "analysis_run_already_active"
SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE = "editorial_run_already_active"
SUPPLEMENTATION_ERROR_ARTIFACT_CONFLICT = "supplementation_artifact_conflict"
SUPPLEMENTATION_ERROR_REGISTRY_WRITE_FAILED = "supplementation_registry_write_failed"
SUPPLEMENTATION_ERROR_ARTIFACT_WRITE_FAILED = "supplementation_artifact_write_failed"
SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED = "worker_interrupted"
SUPPLEMENTATION_ERROR_REPORT_WRITE_FAILED = "report_write_failed"

SUPPLEMENTATION_ERROR_CODES = (
    SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
    SUPPLEMENTATION_ERROR_COVERAGE_GAP_STALE,
    SUPPLEMENTATION_ERROR_REQUEST_INVALID,
    SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED,
    SUPPLEMENTATION_ERROR_PROVIDER_UNAVAILABLE,
    SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
    SUPPLEMENTATION_ERROR_INVALID_STOCK_CANDIDATE,
    SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_DUPLICATE,
    SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_PREVIEW_MISSING,
    SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_NOT_ACCEPTED,
    SUPPLEMENTATION_ERROR_STOCK_LICENSE_UNKNOWN,
    SUPPLEMENTATION_ERROR_STOCK_OAUTH_UNKNOWN,
    SUPPLEMENTATION_ERROR_RETRY_EXHAUSTED,
    SUPPLEMENTATION_ERROR_CLAIM_DECISION_REQUIRED,
    SUPPLEMENTATION_ERROR_CLAIM_DECISION_STALE,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFIRMATION_REQUIRED,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFLICT,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_ARTIFACT_CONFLICT,
    SUPPLEMENTATION_ERROR_REGISTRY_WRITE_FAILED,
    SUPPLEMENTATION_ERROR_ARTIFACT_WRITE_FAILED,
    SUPPLEMENTATION_ERROR_WORKER_INTERRUPTED,
    SUPPLEMENTATION_ERROR_REPORT_WRITE_FAILED,
)

SupplementationRunScopeLiteral = Literal[
    "supplementation_local_review_only",
    "supplementation_search_only",
    "supplementation_candidate_validation_only",
]
MediaKindLiteral = Literal["video", "image", "audio", "unknown"]


class CoverageLevel(str, Enum):
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    NOT_COVERED = "not_covered"


class CoverageRiskFlag(str, Enum):
    GEOGRAPHICALLY_UNCERTAIN = "geographically_uncertain"
    TOO_GENERIC = "too_generic"
    REPETITION_RISK = "repetition_risk"
    POSSIBLE_SYNTHETIC_RISK = "possible_synthetic_risk"
    USER_DECISION_REQUIRED = "user_decision_required"
    COVERAGE_EXACT_MATCH_NOT_VERIFIED = "coverage_exact_match_not_verified"


# Explicit allow-list only. Unknown missing_properties are never auto-acceptable.
ACCEPTABLE_MISSING_PROPERTY_RISK_MAP: dict[str, CoverageRiskFlag] = {
    "exact_match_not_verified": CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED,
}


def derive_acceptable_risks_from_missing_properties(
    missing_properties: list[str] | tuple[str, ...] | None,
) -> list[CoverageRiskFlag]:
    """Map known missing_properties onto visible acceptable CoverageRiskFlags."""
    derived: list[CoverageRiskFlag] = []
    seen: set[CoverageRiskFlag] = set()
    for item in missing_properties or ():
        key = str(item).strip()
        mapped = ACCEPTABLE_MISSING_PROPERTY_RISK_MAP.get(key)
        if mapped is None or mapped in seen:
            continue
        derived.append(mapped)
        seen.add(mapped)
    return derived


def merge_gap_risk_flags(
    existing: list[CoverageRiskFlag] | tuple[CoverageRiskFlag, ...] | None,
    missing_properties: list[str] | tuple[str, ...] | None,
) -> list[CoverageRiskFlag]:
    """Union existing risk_flags with explicitly derived acceptable risks."""
    merged: list[CoverageRiskFlag] = []
    seen: set[CoverageRiskFlag] = set()
    for risk in list(existing or ()) + derive_acceptable_risks_from_missing_properties(
        missing_properties
    ):
        if risk in seen:
            continue
        merged.append(risk)
        seen.add(risk)
    return merged


class CoverageGapStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED_WITH_LOCAL_ASSET = "resolved_with_local_asset"
    RESOLVED_WITH_SUPPLEMENT = "resolved_with_supplement"
    RESOLVED_BY_SCRIPT_REVISION = "resolved_by_script_revision"
    RESOLVED_BY_GRAPHIC_PLAN = "resolved_by_graphic_plan"
    ACCEPTED_UNRESOLVED = "accepted_unresolved"
    USER_DECISION_REQUIRED = "user_decision_required"
    SUPERSEDED = "superseded"


TERMINAL_GAP_STATUSES = frozenset(
    {
        CoverageGapStatus.RESOLVED_WITH_LOCAL_ASSET,
        CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT,
        CoverageGapStatus.RESOLVED_BY_SCRIPT_REVISION,
        CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
        CoverageGapStatus.ACCEPTED_UNRESOLVED,
        CoverageGapStatus.SUPERSEDED,
    }
)


class EscalationStep(str, Enum):
    LOCAL_DEEPER_REVIEW = "local_deeper_review"
    PHOTO = "photo"
    BETTER_SEARCH = "better_search"
    TARGETED_SCRIPT_REVISION = "targeted_script_revision"
    SEARCH_AGAIN = "search_again"
    MAP_OR_GRAPHIC = "map_or_graphic"
    USER_DECISION = "user_decision"


ESCALATION_SEQUENCE: tuple[EscalationStep, ...] = (
    EscalationStep.LOCAL_DEEPER_REVIEW,
    EscalationStep.PHOTO,
    EscalationStep.BETTER_SEARCH,
    EscalationStep.TARGETED_SCRIPT_REVISION,
    EscalationStep.SEARCH_AGAIN,
    EscalationStep.MAP_OR_GRAPHIC,
    EscalationStep.USER_DECISION,
)


class GapEventType(str, Enum):
    MATERIALIZED = "materialized"
    ESCALATED = "escalated"
    LOCAL_REVIEW_ASSIGNED = "local_review_assigned"
    CANDIDATE_LINKED = "candidate_linked"
    GRAPHIC_PLAN_CREATED = "graphic_plan_created"
    USER_DECISION_RECORDED = "user_decision_recorded"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class SupplementationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


ACTIVE_SUPPLEMENTATION_RUN_STATUSES = frozenset(
    {SupplementationRunStatus.QUEUED, SupplementationRunStatus.RUNNING}
)


class SupplementationAttemptStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REUSED = "reused"
    INTERRUPTED = "interrupted"


class SupplementationRequestStatus(str, Enum):
    DRAFT = "draft"
    SEARCHING = "searching"
    AWAITING_DECISION = "awaiting_decision"
    IMPORT_PENDING = "import_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STALE = "stale"


class StockSearchAttemptStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StockLicenseStatus(str, Enum):
    UNKNOWN = "unknown"


class StockDuplicateStatus(str, Enum):
    UNKNOWN = "unknown"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    NOT_DUPLICATE = "not_duplicate"


class StockCandidateUserStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED_FOR_IMPORT = "accepted_for_import"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class CandidateDecisionValue(str, Enum):
    ACCEPTED_FOR_IMPORT = "accepted_for_import"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ClaimDecisionValue(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ACCEPTED_AS_UNCERTAIN = "accepted_as_uncertain"
    REVISION_REQUIRED = "revision_required"


LOCK_COMPATIBLE_CLAIM_DECISIONS = frozenset(
    {
        ClaimDecisionValue.CONFIRMED,
        ClaimDecisionValue.REJECTED,
        ClaimDecisionValue.ACCEPTED_AS_UNCERTAIN,
    }
)


class GraphicPlanUserStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ScriptLockStatus(str, Enum):
    LOCKED = "locked"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class CoverageGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str
    project_id: str
    script_id: str
    script_version: int = Field(ge=1)
    coverage_audit_id: str
    visual_intent_id: str
    coverage_level: CoverageLevel
    risk_flags: list[CoverageRiskFlag] = Field(default_factory=list)
    missing_properties: list[str] = Field(default_factory=list)
    current_escalation_step: EscalationStep = EscalationStep.LOCAL_DEEPER_REVIEW
    prior_attempt_summaries: list[str] = Field(default_factory=list)
    user_decision: str | None = None
    outcome: str | None = None
    status: CoverageGapStatus
    gap_version: int = Field(ge=1)
    accepted_unresolved_risks: list[CoverageRiskFlag] = Field(default_factory=list)
    resolved_asset_id: str | None = None
    created_at: datetime
    updated_at: datetime


class GapEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    gap_id: str
    project_id: str
    event_type: GapEventType
    from_step: EscalationStep | None = None
    to_step: EscalationStep | None = None
    message: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class SupplementationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SUPPLEMENTATION_SCHEMA_VERSION
    run_id: str
    project_id: str
    scope: SupplementationRunScopeLiteral
    status: SupplementationRunStatus
    selected_gap_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    relative_report_path: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SupplementationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    run_id: str
    project_id: str
    scope: SupplementationRunScopeLiteral
    gap_id: str | None = None
    request_id: str | None = None
    cache_key: str | None = None
    status: SupplementationAttemptStatus
    relative_json_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class SupplementationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    project_id: str
    gap_id: str
    script_id: str
    visual_intent_id: str
    motif: str
    action: str
    setting: str
    geographic_requirements: str | None = None
    authenticity_requirements: list[str] = Field(default_factory=list)
    allowed_media_kinds: list[MediaKindLiteral] = Field(default_factory=list)
    query_text: str
    search_version: int = Field(ge=1)
    status: SupplementationRequestStatus
    created_at: datetime
    updated_at: datetime


class StockSearchRequest(BaseModel):
    """Provider-neutral adapter request. Adapter payloads forbid unknown fields."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    request_id: str
    gap_id: str
    query_text: str
    search_strategy: str
    provider: Literal["fake"] = STOCK_PROVIDER_FAKE
    max_results: int = Field(default=MAX_STOCK_CANDIDATES_PER_ATTEMPT, ge=1, le=MAX_STOCK_CANDIDATES_PER_ATTEMPT)


class StockCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    project_id: str
    request_id: str
    gap_id: str
    attempt_id: str
    provider: Literal["fake"] = STOCK_PROVIDER_FAKE
    provider_candidate_id: str
    preview_ref: str | None = None
    description: str
    media_kind: MediaKindLiteral
    visible_metadata: dict[str, object] = Field(default_factory=dict)
    geographic_hint: str | None = None
    license_status: StockLicenseStatus = StockLicenseStatus.UNKNOWN
    duplicate_status: StockDuplicateStatus = StockDuplicateStatus.UNKNOWN
    user_status: StockCandidateUserStatus = StockCandidateUserStatus.PROPOSED
    metadata_fingerprint: str | None = None
    preview_sha256: str | None = None
    created_at: datetime

    @field_validator("preview_ref")
    @classmethod
    def _preview_is_opaque_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("/") or ".." in value.split("/") or "_otio/" in value:
            raise ValueError("preview_ref must be an opaque relative editorial ref")
        if not value.startswith("editorial/supplementation/previews/"):
            raise ValueError("preview_ref must live under editorial/supplementation/previews/")
        return value


class StockSearchAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    project_id: str
    request_id: str
    gap_id: str
    query_text: str
    search_strategy: str
    provider: Literal["fake"] = STOCK_PROVIDER_FAKE
    adapter_version: str
    attempt_number: int = Field(ge=1)
    result_count: int = Field(ge=0, le=MAX_STOCK_CANDIDATES_PER_ATTEMPT)
    status: StockSearchAttemptStatus
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class StockSearchResponse(BaseModel):
    """Provider-neutral adapter response. Adapter payloads forbid unknown fields."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    provider: Literal["fake"] = STOCK_PROVIDER_FAKE
    adapter_version: str = FAKE_STOCK_ADAPTER_VERSION
    candidates: list[StockCandidate] = Field(
        default_factory=list, max_length=MAX_STOCK_CANDIDATES_PER_ATTEMPT
    )


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    project_id: str
    gap_id: str
    candidate_id: str
    revision: int = Field(ge=1)
    decision: CandidateDecisionValue
    reason: str
    user_note: str | None = None
    created_at: datetime


class ClaimDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    project_id: str
    script_id: str
    claim_id: str
    claim_content_sha256: str
    revision: int = Field(ge=1)
    decision: ClaimDecisionValue
    reason: str | None = None
    user_note: str | None = None
    created_at: datetime


class GraphicPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graphic_plan_id: str
    project_id: str
    visual_intent_id: str
    gap_id: str
    description: str
    required_data: list[str] = Field(default_factory=list)
    geographic_scope: str | None = None
    user_status: GraphicPlanUserStatus = GraphicPlanUserStatus.PROPOSED
    created_at: datetime
    updated_at: datetime


class ScriptLockRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_id: str
    risk_key: str
    confirmed_at: datetime
    confirmation_fingerprint: str


class ScriptLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_id: str
    project_id: str
    script_id: str
    script_version: int = Field(ge=1)
    project_brief_id: str
    narrative_plan_id: str
    selected_hook_id: str
    coverage_audit_id: str
    observation_set_fingerprint: str
    script_hash: str
    structure_fingerprint: str
    coverage_fingerprint: str
    accepted_open_risks: list[str] = Field(default_factory=list)
    claim_decision_snapshot: list[dict[str, object]] = Field(default_factory=list)
    user_confirmed: bool
    user_confirmed_at: datetime | None = None
    confirmation_fingerprint: str
    lock_fingerprint: str
    lock_version: int = Field(ge=1)
    status: ScriptLockStatus
    created_at: datetime

    @model_validator(mode="after")
    def _confirmation_required_for_locked(self) -> "ScriptLock":
        if self.status == ScriptLockStatus.LOCKED and not self.user_confirmed:
            raise ValueError("locked ScriptLock requires explicit user confirmation")
        return self


@dataclass(frozen=True)
class StockConfig:
    provider: str
    enabled: bool
    adapter_version: str
    gateway_version: str
    max_retries: int
    timeout_seconds: int


def metadata_fingerprint(candidate: StockCandidate | dict[str, object]) -> str:
    if isinstance(candidate, StockCandidate):
        payload = {
            "provider": candidate.provider,
            "provider_candidate_id": candidate.provider_candidate_id,
            "description": candidate.description,
            "media_kind": candidate.media_kind,
            "visible_metadata": candidate.visible_metadata,
            "geographic_hint": candidate.geographic_hint,
            "preview_sha256": candidate.preview_sha256,
        }
    else:
        payload = {
            key: candidate.get(key)
            for key in (
                "provider",
                "provider_candidate_id",
                "description",
                "media_kind",
                "visible_metadata",
                "geographic_hint",
                "preview_sha256",
            )
        }
    return compute_text_sha256(payload)


def observation_identity_fingerprint(observations: list[object]) -> str:
    rows = []
    for item in observations:
        rows.append(
            {
                "observation_id": str(getattr(item, "observation_id")),
                "asset_id": str(getattr(item, "asset_id")),
                "observation_sha256": str(getattr(item, "observation_sha256")),
                "frame_set_fingerprint": str(getattr(item, "frame_set_fingerprint")),
            }
        )
    rows.sort(key=lambda row: tuple(row.values()))
    return compute_text_sha256(rows)


def script_structure_fingerprint(script_bundle: dict[str, object]) -> str:
    payload = {
        "sentences": script_bundle.get("sentences", []),
        "claims": script_bundle.get("claims", []),
        "visual_beats": script_bundle.get("visual_beats", []),
        "visual_intents": script_bundle.get("visual_intents", []),
    }
    return compute_text_sha256(payload)


def coverage_gap_fingerprint(
    *,
    coverage_audit_id: str,
    gaps: list[CoverageGap],
    claim_decisions: list[ClaimDecision],
) -> str:
    return compute_text_sha256(
        {
            "coverage_audit_id": coverage_audit_id,
            "gaps": [
                {
                    "gap_id": gap.gap_id,
                    "visual_intent_id": gap.visual_intent_id,
                    "gap_version": gap.gap_version,
                    "status": gap.status.value,
                    "risk_flags": [risk.value for risk in gap.risk_flags],
                    "accepted_unresolved_risks": [
                        risk.value for risk in gap.accepted_unresolved_risks
                    ],
                    "resolved_asset_id": gap.resolved_asset_id,
                    "outcome": gap.outcome,
                }
                for gap in sorted(gaps, key=lambda item: item.gap_id)
            ],
            "claim_decisions": [
                decision.model_dump(mode="json")
                for decision in sorted(
                    claim_decisions,
                    key=lambda item: (item.script_id, item.claim_id, item.revision),
                )
            ],
        }
    )


def script_lock_fingerprint(
    *,
    project_id: str,
    script_id: str,
    script_version: int,
    project_brief_id: str,
    narrative_plan_id: str,
    selected_hook_id: str,
    coverage_audit_id: str,
    observation_set_fingerprint: str,
    script_hash: str,
    structure_fingerprint: str,
    coverage_fingerprint: str,
    accepted_open_risks: list[str],
    claim_decision_snapshot: list[dict[str, object]],
) -> str:
    return compute_text_sha256(
        {
            "project_id": project_id,
            "script_id": script_id,
            "script_version": script_version,
            "project_brief_id": project_brief_id,
            "narrative_plan_id": narrative_plan_id,
            "selected_hook_id": selected_hook_id,
            "coverage_audit_id": coverage_audit_id,
            "observation_set_fingerprint": observation_set_fingerprint,
            "script_hash": script_hash,
            "structure_fingerprint": structure_fingerprint,
            "coverage_fingerprint": coverage_fingerprint,
            "accepted_open_risks": sorted(accepted_open_risks),
            "claim_decision_snapshot": claim_decision_snapshot,
        }
    )


def preview_hash_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [name for name in globals() if name.startswith("SUPPLEMENTATION_")] + [
    "ACTIVE_SUPPLEMENTATION_RUN_STATUSES",
    "CandidateDecision",
    "CandidateDecisionValue",
    "ClaimDecision",
    "ClaimDecisionValue",
    "CoverageGap",
    "CoverageGapStatus",
    "CoverageLevel",
    "ACCEPTABLE_MISSING_PROPERTY_RISK_MAP",
    "CoverageRiskFlag",
    "ESCALATION_SEQUENCE",
    "EscalationStep",
    "FAKE_STOCK_ADAPTER_VERSION",
    "GapEvent",
    "GapEventType",
    "GraphicPlan",
    "GraphicPlanUserStatus",
    "LOCK_COMPATIBLE_CLAIM_DECISIONS",
    "MAX_SEARCH_ATTEMPTS_PER_GAP_VERSION",
    "MAX_STOCK_CANDIDATES_PER_ATTEMPT",
    "STOCK_GATEWAY_VERSION",
    "STOCK_PROVIDER_FAKE",
    "ScriptLock",
    "ScriptLockRisk",
    "ScriptLockStatus",
    "StockCandidate",
    "StockCandidateUserStatus",
    "StockConfig",
    "StockDuplicateStatus",
    "StockLicenseStatus",
    "StockSearchAttempt",
    "StockSearchAttemptStatus",
    "StockSearchRequest",
    "StockSearchResponse",
    "SupplementationAttempt",
    "SupplementationAttemptStatus",
    "SupplementationRequest",
    "SupplementationRequestStatus",
    "SupplementationRun",
    "SupplementationRunScopeLiteral",
    "SupplementationRunStatus",
    "TERMINAL_GAP_STATUSES",
    "coverage_gap_fingerprint",
    "derive_acceptable_risks_from_missing_properties",
    "merge_gap_risk_flags",
    "metadata_fingerprint",
    "observation_identity_fingerprint",
    "preview_hash_from_bytes",
    "script_lock_fingerprint",
    "script_structure_fingerprint",
]
