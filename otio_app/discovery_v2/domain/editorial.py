"""Domain contracts for Discovery V2 Editorial Core (Phase 9)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from otio_app.discovery_v2.domain.narration import (
    PauseDirectionGatewayPayload,
    PROMPT_VERSION_PAUSE_DIRECTION,
    RESPONSE_SCHEMA_PAUSE_DIRECTION,
)
from otio_app.discovery_v2.domain.visual_edit import (
    EditorialRepairProposalGatewayPayload,
    HumanityReviewGatewayPayload,
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    PROMPT_VERSION_HUMANITY_REVIEW,
    PROMPT_VERSION_VISUAL_EDIT_PLAN,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_HUMANITY_REVIEW,
    RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    TEXT_REQUEST_KIND_HUMANITY_REVIEW,
    TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    VisualEditPlanGatewayPayload,
)

EDITORIAL_SCHEMA_VERSION = "editorial-v1"
GATEWAY_VERSION = "discovery-text-gateway-v1"
TEXT_MODEL_IDENTIFIER = "fake-editorial-v1"
TEXT_PROVIDER = "fake"

PROMPT_VERSION_NARRATIVE = "editorial-narrative-v1"
PROMPT_VERSION_SCRIPT = "editorial-script-v1"
PROMPT_VERSION_STRUCTURE = "editorial-structure-v1"
PROMPT_VERSION_COVERAGE = "editorial-coverage-v1"

RESPONSE_SCHEMA_NARRATIVE = "narrative-plan-v1"
RESPONSE_SCHEMA_SCRIPT = "script-draft-v1"
RESPONSE_SCHEMA_STRUCTURE = "script-structure-v1"
RESPONSE_SCHEMA_COVERAGE = "coverage-audit-v1"

EDITORIAL_RUN_SCOPE_NARRATIVE = "editorial_narrative_only"
EDITORIAL_RUN_SCOPE_SCRIPT = "editorial_script_only"
EDITORIAL_RUN_SCOPE_STRUCTURE = "editorial_structure_only"
EDITORIAL_RUN_SCOPE_COVERAGE = "editorial_coverage_only"

EDITORIAL_ERROR_PROJECT_BRIEF_MISSING = "project_brief_missing"
EDITORIAL_ERROR_PROJECT_BRIEF_INVALID = "project_brief_invalid"
EDITORIAL_ERROR_GATEWAY_UNCONFIGURED = "editorial_gateway_unconfigured"
EDITORIAL_ERROR_MODEL_UNAVAILABLE = "editorial_model_unavailable"
EDITORIAL_ERROR_CONSENT_REQUIRED = "editorial_consent_required"
EDITORIAL_ERROR_INPUT_STALE = "editorial_input_stale"
EDITORIAL_ERROR_RESPONSE_INVALID = "editorial_response_invalid"
EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH = "editorial_response_schema_mismatch"
EDITORIAL_ERROR_INVALID_OBSERVATION_REFERENCE = "invalid_observation_reference"
EDITORIAL_ERROR_INVALID_ASSET_REFERENCE = "invalid_asset_reference"
EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE = "invalid_sentence_reference"
EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE = "invalid_visual_beat_reference"
EDITORIAL_ERROR_INVALID_VISUAL_INTENT_REFERENCE = "invalid_visual_intent_reference"
EDITORIAL_ERROR_COVERAGE_AUDIT_INVALID = "coverage_audit_invalid"
EDITORIAL_ERROR_RETRY_EXHAUSTED = "editorial_retry_exhausted"
EDITORIAL_ERROR_ARTIFACT_CONFLICT = "editorial_artifact_conflict"
EDITORIAL_ERROR_REGISTRY_WRITE_FAILED = "editorial_registry_write_failed"
EDITORIAL_ERROR_ARTIFACT_WRITE_FAILED = "editorial_artifact_write_failed"
EDITORIAL_ERROR_WORKER_INTERRUPTED = "worker_interrupted"
EDITORIAL_ERROR_REPORT_WRITE_FAILED = "report_write_failed"
EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE = "analysis_run_already_active"
EDITORIAL_ERROR_RUN_ALREADY_ACTIVE = "editorial_run_already_active"
EDITORIAL_ERROR_COVERAGE_ARTIFACT_PUBLISH_FAILED = "coverage_artifact_publish_failed"
EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED = "coverage_audit_persist_failed"
EDITORIAL_ERROR_COVERAGE_CURRENT_STATE_UPDATE_FAILED = (
    "coverage_current_state_update_failed"
)
EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE = "script_structure_sentences_incomplete"
EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING = "script_structure_beats_missing"
EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING = (
    "script_structure_visual_intents_missing"
)
EDITORIAL_ERROR_STRUCTURE_INCOMPLETE = "script_structure_incomplete"
EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH = "editorial_script_identity_mismatch"
EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH = "registry_artifact_mismatch"
EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING = "active_script_pointer_missing"
EDITORIAL_ERROR_STRUCTURE_REPLACEMENT_CONFLICTS_WITH_COVERAGE = (
    "script_structure_replacement_conflicts_with_coverage"
)
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING = (
    "active_script_recovery_candidate_missing"
)
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS = "active_script_recovery_ambiguous"
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH = (
    "active_script_recovery_identity_mismatch"
)
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH = (
    "active_script_recovery_coverage_mismatch"
)
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED = (
    "active_script_recovery_confirmation_required"
)
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED = "active_script_recovery_failed"
EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED = "active_script_recovered"

EDITORIAL_ERROR_CODES = (
    EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
    EDITORIAL_ERROR_PROJECT_BRIEF_INVALID,
    EDITORIAL_ERROR_GATEWAY_UNCONFIGURED,
    EDITORIAL_ERROR_MODEL_UNAVAILABLE,
    EDITORIAL_ERROR_CONSENT_REQUIRED,
    EDITORIAL_ERROR_INPUT_STALE,
    EDITORIAL_ERROR_RESPONSE_INVALID,
    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
    EDITORIAL_ERROR_INVALID_OBSERVATION_REFERENCE,
    EDITORIAL_ERROR_INVALID_ASSET_REFERENCE,
    EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE,
    EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
    EDITORIAL_ERROR_INVALID_VISUAL_INTENT_REFERENCE,
    EDITORIAL_ERROR_COVERAGE_AUDIT_INVALID,
    EDITORIAL_ERROR_RETRY_EXHAUSTED,
    EDITORIAL_ERROR_ARTIFACT_CONFLICT,
    EDITORIAL_ERROR_REGISTRY_WRITE_FAILED,
    EDITORIAL_ERROR_ARTIFACT_WRITE_FAILED,
    EDITORIAL_ERROR_WORKER_INTERRUPTED,
    EDITORIAL_ERROR_REPORT_WRITE_FAILED,
    EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
    EDITORIAL_ERROR_COVERAGE_ARTIFACT_PUBLISH_FAILED,
    EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED,
    EDITORIAL_ERROR_COVERAGE_CURRENT_STATE_UPDATE_FAILED,
    EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE,
    EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING,
    EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING,
    EDITORIAL_ERROR_STRUCTURE_INCOMPLETE,
    EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH,
    EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING,
    EDITORIAL_ERROR_STRUCTURE_REPLACEMENT_CONFLICTS_WITH_COVERAGE,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED,
)

MediaKindLiteral = Literal["video", "image", "audio", "unknown"]


class ProjectBriefStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class NarrativePlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"


class HookUserStatus(str, Enum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    REJECTED = "rejected"


class ScriptDraftStatus(str, Enum):
    DRAFT = "draft"
    REVIEW_REQUESTED = "review_requested"
    USER_EDITED = "user_edited"
    STRUCTURE_PENDING = "structure_pending"
    SUPERSEDED = "superseded"


class ScriptSourceKind(str, Enum):
    LLM = "llm"
    USER_EDIT = "user_edit"


class ClaimStatus(str, Enum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    USER_CONFIRMATION_REQUIRED = "user_confirmation_required"
    UNSUPPORTED = "unsupported"


class CoverageAuditStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class CoverageStatus(str, Enum):
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    NOT_COVERED = "not_covered"
    GEOGRAPHICALLY_UNCERTAIN = "geographically_uncertain"
    TOO_GENERIC = "too_generic"
    REPETITION_RISK = "repetition_risk"
    POSSIBLE_SYNTHETIC_RISK = "possible_synthetic_risk"
    USER_DECISION_REQUIRED = "user_decision_required"


class EditorialRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


ACTIVE_EDITORIAL_RUN_STATUSES = frozenset(
    {EditorialRunStatus.QUEUED, EditorialRunStatus.RUNNING}
)


class EditorialAttemptStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REUSED = "reused"
    INTERRUPTED = "interrupted"


class EditorialProjectStateStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["observation", "asset", "user_note"]
    id: str


class ProjectBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = EDITORIAL_SCHEMA_VERSION
    project_brief_id: str
    project_id: str
    language: str
    topic: str
    target_audience: str
    desired_duration_seconds: int | None = Field(default=None, ge=1)
    tone: str
    geographic_frame: str | None = None
    must_include: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    user_notes: str | None = None
    brief_version: int = Field(ge=1)
    content_sha256: str
    status: ProjectBriefStatus
    created_at: datetime
    supersedes_brief_id: str | None = None


class NarrativePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RESPONSE_SCHEMA_NARRATIVE
    narrative_plan_id: str
    project_id: str
    project_brief_id: str
    brief_version: int = Field(ge=1)
    central_question: str
    editorial_thesis: str
    hook_strategy: str
    narrative_roles: list[str] = Field(default_factory=list)
    arc: str
    transition_logic: str
    ending_function: str
    uncertainties: list[str] = Field(default_factory=list)
    input_observation_ids: list[str] = Field(default_factory=list)
    input_observation_fingerprint: str
    prompt_version: str
    gateway_version: str
    model_identifier: str
    provider: str
    status: NarrativePlanStatus
    created_at: datetime


class HookVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RESPONSE_SCHEMA_NARRATIVE
    hook_id: str
    narrative_plan_id: str
    hook_text: str
    hook_type: str
    intended_effect: str
    risks: list[str] = Field(default_factory=list)
    local_evidence_refs: list[str] = Field(default_factory=list)
    user_status: HookUserStatus = HookUserStatus.PROPOSED
    created_at: datetime


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    script_id: str
    statement: str
    claim_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    user_note: str | None = None
    status: ClaimStatus


class Sentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    script_id: str
    ordinal: int = Field(ge=0)
    text: str
    narrative_function: str
    claim_ids: list[str] = Field(default_factory=list)
    visual_beat_ids: list[str] = Field(default_factory=list)


class VisualBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_beat_id: str
    script_id: str
    function: str
    description: str
    sentence_ids: list[str] = Field(default_factory=list)
    rhythm_function: str
    continuity_requirements: list[str] = Field(default_factory=list)
    intended_duration_hint_seconds: float | None = Field(default=None, gt=0)


class VisualIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_intent_id: str
    visual_beat_id: str
    desired_motif: str
    action: str
    setting: str
    geographic_requirements: str | None = None
    authenticity_requirements: list[str] = Field(default_factory=list)
    allowed_media_kinds: list[MediaKindLiteral] = Field(default_factory=list)
    priority: int = Field(ge=1)


class ScriptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RESPONSE_SCHEMA_SCRIPT
    script_id: str
    script_version: int = Field(ge=1)
    project_id: str
    language: str
    full_text: str
    sentence_order: list[str] = Field(default_factory=list)
    narrative_plan_id: str
    selected_hook_id: str | None = None
    project_brief_id: str
    brief_version: int = Field(ge=1)
    prompt_version: str
    gateway_version: str
    model_identifier: str
    provider: str
    source_kind: ScriptSourceKind
    supersedes_script_id: str | None = None
    content_sha256: str
    status: ScriptDraftStatus
    created_at: datetime

    @field_validator("status")
    @classmethod
    def _no_locked_status(cls, value: ScriptDraftStatus) -> ScriptDraftStatus:
        if str(value.value) == "locked":
            raise ValueError("ScriptDraft status 'locked' is not part of Phase 9")
        return value


class CoverageIntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_intent_id: str
    coverage_status: CoverageStatus
    candidate_asset_ids: list[str] = Field(default_factory=list, max_length=5)
    accepted_observation_ids: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    missing_properties: list[str] = Field(default_factory=list)
    recommended_next_action: str

    @field_validator("candidate_asset_ids")
    @classmethod
    def _candidate_ids_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate_asset_ids must be unique per intent")
        return value


class CoverageAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RESPONSE_SCHEMA_COVERAGE
    coverage_audit_id: str
    project_id: str
    script_id: str
    script_version: int = Field(ge=1)
    brief_version: int = Field(ge=1)
    narrative_plan_id: str
    input_observation_fingerprint: str
    status: CoverageAuditStatus
    created_at: datetime
    prompt_version: str
    gateway_version: str
    model_identifier: str
    provider: str
    results: list[CoverageIntentResult] = Field(default_factory=list)
    # Optional fachlicher Idempotenz-Fingerprint (coverage-input-v1). Legacy audits omit it.
    canonical_coverage_input_fingerprint: str | None = None


class EditorialRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = EDITORIAL_SCHEMA_VERSION
    run_id: str
    project_id: str
    scope: Literal[
        "editorial_narrative_only",
        "editorial_script_only",
        "editorial_structure_only",
        "editorial_coverage_only",
    ]
    status: EditorialRunStatus
    brief_id: str | None = None
    brief_version: int | None = None
    narrative_plan_id: str | None = None
    script_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    relative_report_path: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EditorialAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    run_id: str
    project_id: str
    request_kind: Literal["narrative", "script", "structure", "coverage", "pause_direction"]
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    input_fingerprint: str
    status: EditorialAttemptStatus
    relative_json_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class EditorialProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    active_brief_id: str | None = None
    active_narrative_plan_id: str | None = None
    selected_hook_id: str | None = None
    active_script_id: str | None = None
    active_coverage_audit_id: str | None = None
    current_script_lock_id: str | None = None
    observation_fingerprint: str | None = None
    status: EditorialProjectStateStatus = EditorialProjectStateStatus.ACTIVE
    updated_at: datetime


@dataclass(frozen=True)
class TextConfig:
    provider: str
    enabled: bool
    model_identifier: str
    gateway_version: str
    max_retries: int
    timeout_seconds: int
    prompts: dict[str, str]
    response_schemas: dict[str, str]


class EditorialReadyObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    asset_id: str
    analysis_identity_id: str
    working_media_id: str
    summary: str
    evidence_frame_ids: list[str] = Field(default_factory=list)
    geographic_confidence: float = Field(ge=0.0, le=1.0)
    synthetic_confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_notes: list[str] = Field(default_factory=list)
    observation_sha256: str
    frame_set_fingerprint: str


class TextGatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str
    request_kind: Literal[
        "narrative",
        "script",
        "structure",
        "coverage",
        "pause_direction",
        "visual_edit_plan",
        "humanity_review",
        "editorial_repair_proposal",
    ]
    prompt: str
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    project_brief: ProjectBrief | None = None
    narrative_plan: NarrativePlan | None = None
    hooks: list[HookVariant] = Field(default_factory=list)
    selected_hook_id: str | None = None
    script: ScriptDraft | None = None
    sentences: list[Sentence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    visual_intents: list[VisualIntent] = Field(default_factory=list)
    observations: list[EditorialReadyObservationInput] = Field(default_factory=list)
    candidate_asset_ids: list[str] = Field(default_factory=list)
    pause_voice_segments: list[dict[str, object]] = Field(default_factory=list)
    visual_edit_input: dict[str, object] = Field(default_factory=dict)
    input_fingerprint: str


class NarrativeGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative_plan: NarrativePlan
    hooks: list[HookVariant]

    @model_validator(mode="after")
    def _exactly_three_hooks(self) -> "NarrativeGatewayPayload":
        if len(self.hooks) != 3:
            raise ValueError("Narrative response must contain exactly 3 hooks")
        return self


class ScriptGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: ScriptDraft
    sentences: list[Sentence]
    claims: list[Claim]
    visual_beats: list[VisualBeat]
    visual_intents: list[VisualIntent]


class CoverageGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage_audit: CoverageAudit


class TextGatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: Literal[
        "narrative",
        "script",
        "structure",
        "coverage",
        "pause_direction",
        "visual_edit_plan",
        "humanity_review",
        "editorial_repair_proposal",
    ]
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    attempt_count: int = Field(ge=1)
    narrative: NarrativeGatewayPayload | None = None
    script: ScriptGatewayPayload | None = None
    coverage: CoverageGatewayPayload | None = None
    pause_direction: PauseDirectionGatewayPayload | None = None
    visual_edit_plan: VisualEditPlanGatewayPayload | None = None
    humanity_review: HumanityReviewGatewayPayload | None = None
    editorial_repair_proposal: EditorialRepairProposalGatewayPayload | None = None


def compute_text_sha256(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_observation_set_fingerprint(observations: list[object]) -> str:
    """Stable hash of editorial-ready observation identities and content hashes."""

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
    rows.sort(
        key=lambda row: (
            row["observation_id"],
            row["asset_id"],
            row["observation_sha256"],
            row["frame_set_fingerprint"],
        )
    )
    return compute_text_sha256(rows)


__all__ = [name for name in globals() if name.startswith("EDITORIAL_")] + [
    "ACTIVE_EDITORIAL_RUN_STATUSES",
    "Claim",
    "ClaimStatus",
    "CoverageAudit",
    "CoverageAuditStatus",
    "CoverageGatewayPayload",
    "CoverageIntentResult",
    "CoverageStatus",
    "EditorialAttempt",
    "EditorialAttemptStatus",
    "EditorialProjectState",
    "EditorialProjectStateStatus",
    "EditorialReadyObservationInput",
    "EditorialRun",
    "EditorialRunStatus",
    "EvidenceRef",
    "GATEWAY_VERSION",
    "HookUserStatus",
    "HookVariant",
    "NarrativeGatewayPayload",
    "NarrativePlan",
    "NarrativePlanStatus",
    "PROMPT_VERSION_COVERAGE",
    "PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL",
    "PROMPT_VERSION_HUMANITY_REVIEW",
    "PROMPT_VERSION_NARRATIVE",
    "PROMPT_VERSION_PAUSE_DIRECTION",
    "PROMPT_VERSION_SCRIPT",
    "PROMPT_VERSION_STRUCTURE",
    "PROMPT_VERSION_VISUAL_EDIT_PLAN",
    "ProjectBrief",
    "ProjectBriefStatus",
    "RESPONSE_SCHEMA_COVERAGE",
    "RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL",
    "RESPONSE_SCHEMA_HUMANITY_REVIEW",
    "RESPONSE_SCHEMA_NARRATIVE",
    "RESPONSE_SCHEMA_PAUSE_DIRECTION",
    "RESPONSE_SCHEMA_SCRIPT",
    "RESPONSE_SCHEMA_STRUCTURE",
    "RESPONSE_SCHEMA_VISUAL_EDIT_PLAN",
    "ScriptDraft",
    "ScriptDraftStatus",
    "ScriptGatewayPayload",
    "ScriptSourceKind",
    "Sentence",
    "TEXT_MODEL_IDENTIFIER",
    "TEXT_PROVIDER",
    "TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL",
    "TEXT_REQUEST_KIND_HUMANITY_REVIEW",
    "TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN",
    "TextConfig",
    "TextGatewayRequest",
    "TextGatewayResponse",
    "VisualBeat",
    "VisualIntent",
    "compute_observation_set_fingerprint",
    "compute_text_sha256",
]
