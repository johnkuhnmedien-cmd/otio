"""Domain contracts for Discovery V2 Phase 12 visual edit."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VISUAL_EDIT_SCHEMA_VERSION = "visual-edit-v1"
VISUAL_EDIT_MODEL_IDENTIFIER = "fake-visual-edit-v1"

PROMPT_VERSION_VISUAL_EDIT_PLAN = "visual-edit-plan-v1"
PROMPT_VERSION_HUMANITY_REVIEW = "humanity-review-v1"
PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL = "editorial-repair-proposal-v1"
RESPONSE_SCHEMA_VISUAL_EDIT_PLAN = "visual-edit-plan-response-v1"
RESPONSE_SCHEMA_HUMANITY_REVIEW = "humanity-review-response-v1"
RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL = "editorial-repair-proposal-response-v1"

TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN = "visual_edit_plan"
TEXT_REQUEST_KIND_HUMANITY_REVIEW = "humanity_review"
TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL = "editorial_repair_proposal"

VISUAL_EDIT_RUN_SCOPE_PLAN = "visual_edit_plan_only"
VISUAL_EDIT_RUN_SCOPE_HUMANITY = "humanity_review_only"
VISUAL_EDIT_RUN_SCOPE_FEASIBILITY = "feasibility_check_only"
VISUAL_EDIT_RUN_SCOPE_REPAIR = "editorial_repair_only"

# E1-E24 implementation constants.
MAX_SHOTS_PER_MINUTE_WARNING = 12.0
MAX_SHOTS_PER_MINUTE_BLOCKING = 20.0
VIDEO_SHOT_MIN_SECONDS = 0.80
VIDEO_SHOT_MAX_SECONDS = 12.0
CLOSING_HOLD_MAX_SECONDS = 16.0
ASSET_REUSE_MAX = 3
SOURCE_RANGE_OVERLAP_RATIO_MAX = 0.90
SOURCE_RANGE_REUSE_MAX = 1
MIN_SOURCE_HANDLE_SECONDS = 0.10
TRANSITION_CUT_SECONDS = 0.0
TRANSITION_MIN_SECONDS = 0.10
TRANSITION_MAX_SECONDS = 0.80
PHOTO_SHOT_MIN_SECONDS = 1.20
PHOTO_SHOT_MAX_SECONDS = 6.0
SENTENCE_BOUNDARY_WARNING_RATIO = 0.65
SENTENCE_BOUNDARY_BLOCKING_RATIO = 0.85
SHOT_DURATION_VARIANCE_MIN_RATIO = 1.25
SHOT_DURATION_VARIANCE_MIN_SHOTS = 6
GENERIC_STOCK_WARNING_RATIO = 0.40
GENERIC_STOCK_BLOCKING_RATIO = 0.60
SIMILAR_MOTIF_WARNING_RUN = 3
SIMILAR_MOTIF_BLOCKING_RUN = 4
TIMELINE_DURATION_TOLERANCE_FRAMES = 1

VISUAL_EDIT_ERROR_SCRIPT_LOCK_MISSING = "script_lock_missing"
VISUAL_EDIT_ERROR_SCRIPT_LOCK_INVALIDATED = "script_lock_invalidated"
VISUAL_EDIT_ERROR_NARRATION_TIMELINE_MISSING = "narration_timeline_missing"
VISUAL_EDIT_ERROR_NARRATION_TIMELINE_STALE = "narration_timeline_stale"
VISUAL_EDIT_ERROR_INPUT_STALE = "visual_edit_input_stale"
VISUAL_EDIT_ERROR_GATEWAY_UNCONFIGURED = "visual_edit_gateway_unconfigured"
VISUAL_EDIT_ERROR_RESPONSE_INVALID = "visual_edit_response_invalid"
VISUAL_EDIT_ERROR_RESPONSE_SCHEMA_MISMATCH = "visual_edit_response_schema_mismatch"
VISUAL_EDIT_ERROR_INVALID_NARRATION_ENTRY_REFERENCE = "invalid_narration_entry_reference"
VISUAL_EDIT_ERROR_INVALID_SENTENCE_REFERENCE = "invalid_sentence_reference"
VISUAL_EDIT_ERROR_INVALID_VISUAL_BEAT_REFERENCE = "invalid_visual_beat_reference"
VISUAL_EDIT_ERROR_INVALID_VISUAL_INTENT_REFERENCE = "invalid_visual_intent_reference"
VISUAL_EDIT_ERROR_INVALID_ASSET_REFERENCE = "invalid_asset_reference"
VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE = "invalid_working_media_reference"
VISUAL_EDIT_ERROR_INVALID_OBSERVATION_REFERENCE = "invalid_observation_reference"
VISUAL_EDIT_ERROR_INVALID_TECHNICAL_SHOT_REFERENCE = "invalid_technical_shot_reference"
VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE = "invalid_shot_timeline"
VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE = "invalid_source_range"
VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS = "source_range_out_of_bounds"
VISUAL_EDIT_ERROR_PLANNED_GRAPHIC_NOT_EXPORTABLE = "planned_graphic_not_exportable"
VISUAL_EDIT_ERROR_HUMANITY_REVIEW_INVALID = "humanity_review_invalid"
VISUAL_EDIT_ERROR_HUMANITY_BLOCKING_FINDING = "humanity_blocking_finding"
VISUAL_EDIT_ERROR_FEASIBILITY_CHECK_FAILED = "feasibility_check_failed"
VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE = "feasibility_blocking_issue"
VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID = "repair_proposal_invalid"
VISUAL_EDIT_ERROR_REPAIR_CONFLICT = "repair_conflict"
VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED = "repair_validation_failed"
VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE = "visual_edit_run_already_active"
VISUAL_EDIT_ERROR_NARRATION_RUN_ALREADY_ACTIVE = "narration_run_already_active"
VISUAL_EDIT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE = "analysis_run_already_active"
VISUAL_EDIT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE = "editorial_run_already_active"
VISUAL_EDIT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE = "supplementation_run_already_active"
VISUAL_EDIT_ERROR_ARTIFACT_CONFLICT = "visual_edit_artifact_conflict"
VISUAL_EDIT_ERROR_REGISTRY_WRITE_FAILED = "visual_edit_registry_write_failed"
VISUAL_EDIT_ERROR_ARTIFACT_WRITE_FAILED = "visual_edit_artifact_write_failed"
VISUAL_EDIT_ERROR_WORKER_INTERRUPTED = "worker_interrupted"
VISUAL_EDIT_ERROR_REPORT_WRITE_FAILED = "report_write_failed"
VISUAL_EDIT_ERROR_NO_E3_COMPLIANT_ASSIGNMENT = "visual_edit_no_e3_compliant_assignment"
VISUAL_EDIT_ERROR_NO_E4_COMPLIANT_SOURCE_RANGE = "visual_edit_no_e4_compliant_source_range"

VISUAL_EDIT_ERROR_CODES = (
    VISUAL_EDIT_ERROR_SCRIPT_LOCK_MISSING,
    VISUAL_EDIT_ERROR_SCRIPT_LOCK_INVALIDATED,
    VISUAL_EDIT_ERROR_NARRATION_TIMELINE_MISSING,
    VISUAL_EDIT_ERROR_NARRATION_TIMELINE_STALE,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_GATEWAY_UNCONFIGURED,
    VISUAL_EDIT_ERROR_RESPONSE_INVALID,
    VISUAL_EDIT_ERROR_RESPONSE_SCHEMA_MISMATCH,
    VISUAL_EDIT_ERROR_INVALID_NARRATION_ENTRY_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_SENTENCE_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_VISUAL_INTENT_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_ASSET_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_OBSERVATION_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_TECHNICAL_SHOT_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE,
    VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE,
    VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS,
    VISUAL_EDIT_ERROR_PLANNED_GRAPHIC_NOT_EXPORTABLE,
    VISUAL_EDIT_ERROR_HUMANITY_REVIEW_INVALID,
    VISUAL_EDIT_ERROR_HUMANITY_BLOCKING_FINDING,
    VISUAL_EDIT_ERROR_FEASIBILITY_CHECK_FAILED,
    VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID,
    VISUAL_EDIT_ERROR_REPAIR_CONFLICT,
    VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED,
    VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_NARRATION_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_ARTIFACT_CONFLICT,
    VISUAL_EDIT_ERROR_REGISTRY_WRITE_FAILED,
    VISUAL_EDIT_ERROR_ARTIFACT_WRITE_FAILED,
    VISUAL_EDIT_ERROR_WORKER_INTERRUPTED,
    VISUAL_EDIT_ERROR_REPORT_WRITE_FAILED,
    VISUAL_EDIT_ERROR_NO_E3_COMPLIANT_ASSIGNMENT,
    VISUAL_EDIT_ERROR_NO_E4_COMPLIANT_SOURCE_RANGE,
)

VisualEditPlanStatusLiteral = Literal[
    "draft",
    "review_required",
    "repair_required",
    "ready_for_editorial_review",
    "superseded",
    "invalidated",
]
EditorialShotStatusLiteral = Literal[
    "planned",
    "assigned",
    "needs_repair",
    "blocked",
    "accepted_risk",
]
MediaStrategyLiteral = Literal[
    "local_video",
    "local_photo",
    "planned_graphic",
    "intentional_visual_only",
]
AssignmentStatusLiteral = Literal["proposed", "resolved", "invalid", "blocked"]
TransitionTechnicalTypeLiteral = Literal["cut", "dissolve", "fade", "hold"]
TransitionStatusLiteral = Literal["planned", "resolved", "blocked"]
HumanityReviewStatusLiteral = Literal["completed", "stale", "superseded", "invalid"]
HumanityJudgmentLiteral = Literal["pass_with_risks", "needs_repair", "blocked"]
HumanitySeverityLiteral = Literal["info", "warning", "blocking"]
HumanityFindingUserStatusLiteral = Literal[
    "open",
    "accepted_risk",
    "resolved_by_repair",
    "dismissed_invalid",
]
FeasibilityReportStatusLiteral = Literal["completed", "stale", "superseded", "failed"]
FeasibilityAssessmentLiteral = Literal["pass", "pass_with_warnings", "fail"]
FeasibilitySeverityLiteral = Literal["warning", "blocking"]
RepairProposalSourceLiteral = Literal["editorial_fake_llm", "deterministic_python", "user"]
RepairProposalStatusLiteral = Literal[
    "proposed",
    "selected",
    "applied",
    "rejected",
    "superseded",
]
RepairRunStatusLiteral = Literal["queued", "running", "completed", "failed", "interrupted"]
VisualEditRunScopeLiteral = Literal[
    "visual_edit_plan_only",
    "humanity_review_only",
    "feasibility_check_only",
    "editorial_repair_only",
]


class VisualEditRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


ACTIVE_VISUAL_EDIT_RUN_STATUSES = frozenset(
    {VisualEditRunStatus.QUEUED, VisualEditRunStatus.RUNNING}
)


class AcceptedRiskRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: str
    category: str
    rationale: str | None = None


class VisualEditProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    current_visual_edit_plan_id: str | None = None
    current_humanity_review_id: str | None = None
    current_feasibility_report_id: str | None = None
    current_repair_run_id: str | None = None
    current_script_lock_id: str | None = None
    current_narration_timeline_id: str | None = None
    updated_at: datetime


class VisualEditRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    scope: VisualEditRunScopeLiteral
    status: VisualEditRunStatus
    script_lock_id: str | None = None
    narration_timeline_id: str | None = None
    plan_id: str | None = None
    input_fingerprint: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    relative_report_path: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    schema_version: str = VISUAL_EDIT_SCHEMA_VERSION


class VisualEditPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    project_id: str
    script_lock_id: str
    narration_timeline_id: str
    input_fingerprint: str
    plan_version: int = Field(ge=1)
    gateway_version: str
    model_id: str
    prompt_version: str
    schema_version: str = VISUAL_EDIT_SCHEMA_VERSION
    status: VisualEditPlanStatusLiteral
    total_shot_count: int = Field(ge=0)
    expected_visual_duration_seconds: float = Field(ge=0.0)
    accepted_risks: list[AcceptedRiskRef] = Field(default_factory=list)
    created_at: datetime


class EditorialShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    plan_id: str
    ordinal: int = Field(ge=0)
    shot_function: str
    narration_entry_ids: list[str] = Field(default_factory=list)
    sentence_ids: list[str] = Field(default_factory=list)
    visual_beat_ids: list[str] = Field(default_factory=list)
    visual_intent_ids: list[str] = Field(default_factory=list)
    timeline_start_seconds: float = Field(ge=0.0)
    timeline_end_seconds: float = Field(gt=0.0)
    duration_seconds: float = Field(gt=0.0)
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    transition_intent: str | None = None
    continuity_intent: str | None = None
    rhythm_intent: str | None = None
    media_strategy: MediaStrategyLiteral
    priority: int = Field(ge=0)
    uncertainty_notes: list[str] = Field(default_factory=list)
    status: EditorialShotStatusLiteral

    @model_validator(mode="after")
    def _valid_range(self) -> "EditorialShot":
        if self.timeline_end_seconds <= self.timeline_start_seconds:
            raise ValueError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
        if self.timeline_end_frame <= self.timeline_start_frame:
            raise ValueError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
        if abs((self.timeline_end_seconds - self.timeline_start_seconds) - self.duration_seconds) > 1e-5:
            raise ValueError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
        return self


class SourceRangeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_bias: Literal["beginning", "middle", "end"] = "middle"
    desired_duration_seconds: float | None = Field(default=None, gt=0.0)
    action_hint: str | None = None
    continuity_hint: str | None = None


class ShotMediaAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    shot_id: str
    asset_id: str | None = None
    working_media_id: str | None = None
    technical_shot_id: str | None = None
    visual_observation_id: str | None = None
    assignment_priority: int = Field(ge=0)
    source_range_intent: SourceRangeIntent = Field(default_factory=SourceRangeIntent)
    technical_source_in_seconds: float | None = None
    technical_source_out_seconds: float | None = None
    technical_source_in_frame: int | None = None
    technical_source_out_frame: int | None = None
    duration_seconds: float = Field(gt=0.0)
    selection_rationale: str
    status: AssignmentStatusLiteral

    @model_validator(mode="after")
    def _range_pairing(self) -> "ShotMediaAssignment":
        seconds = (self.technical_source_in_seconds, self.technical_source_out_seconds)
        frames = (self.technical_source_in_frame, self.technical_source_out_frame)
        if any(item is None for item in seconds) != all(item is None for item in seconds):
            raise ValueError(VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE)
        if any(item is None for item in frames) != all(item is None for item in frames):
            raise ValueError(VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE)
        if self.technical_source_in_seconds is not None:
            if self.technical_source_out_seconds is None or self.technical_source_out_seconds <= self.technical_source_in_seconds:
                raise ValueError(VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE)
            if self.technical_source_in_frame is None or self.technical_source_out_frame is None:
                raise ValueError(VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE)
            if self.technical_source_out_frame <= self.technical_source_in_frame:
                raise ValueError(VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE)
        return self


class ShotTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    plan_id: str
    from_shot_id: str
    to_shot_id: str
    editorial_function: str
    technical_type: TransitionTechnicalTypeLiteral
    desired_duration_seconds: float = Field(ge=0.0)
    resolved_duration_seconds: float = Field(ge=0.0)
    status: TransitionStatusLiteral


class HumanityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    review_id: str
    shot_id: str | None = None
    plan_level: bool = False
    category: str
    severity: HumanitySeverityLiteral
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    recommended_action: str
    user_status: HumanityFindingUserStatusLiteral = "open"


class HumanityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    visual_edit_plan_id: str
    review_version: int = Field(ge=1)
    input_fingerprint: str
    status: HumanityReviewStatusLiteral
    overall_judgment: HumanityJudgmentLiteral
    deterministic_signals: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class FeasibilityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    report_id: str
    shot_id: str | None = None
    assignment_id: str | None = None
    error_code: str
    severity: FeasibilitySeverityLiteral
    technical_details: str
    deterministically_repairable: bool = False
    blocks_phase_13: bool = False


class FeasibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    plan_id: str
    input_fingerprint: str
    timebase: str
    status: FeasibilityReportStatusLiteral
    overall_technical_assessment: FeasibilityAssessmentLiteral
    metrics: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    plan_id: str
    humanity_review_id: str | None = None
    feasibility_report_id: str | None = None
    source: RepairProposalSourceLiteral
    repair_type: str
    affected_ids: list[str] = Field(default_factory=list)
    description: str
    expected_effect: str
    user_status: RepairProposalStatusLiteral = "proposed"
    version: int = Field(default=1, ge=1)


class RepairRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    input_plan_id: str
    selected_proposal_ids: list[str] = Field(default_factory=list)
    output_plan_id: str | None = None
    status: RepairRunStatusLiteral
    created_at: datetime


class RepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    run_id: str
    changes: list[dict[str, object]] = Field(default_factory=list)
    remaining_findings: list[str] = Field(default_factory=list)
    remaining_feasibility_issues: list[str] = Field(default_factory=list)
    created_at: datetime


class RankedCandidateRef(BaseModel):
    """Editorial candidate offered by Fake/LLM for technical Python selection."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    working_media_id: str
    observation_id: str
    technical_shot_id: str | None = None


class VisualEditPlanShotIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    ordinal: int = Field(ge=0)
    shot_function: str
    duration_weight: float = Field(gt=0.0)
    narration_entry_ids: list[str] = Field(default_factory=list)
    sentence_ids: list[str] = Field(default_factory=list)
    visual_beat_ids: list[str] = Field(default_factory=list)
    visual_intent_ids: list[str] = Field(default_factory=list)
    media_strategy: MediaStrategyLiteral
    candidate_asset_id: str | None = None
    candidate_working_media_id: str | None = None
    candidate_technical_shot_id: str | None = None
    candidate_observation_id: str | None = None
    ranked_candidates: list[RankedCandidateRef] = Field(default_factory=list)
    source_range_intent: SourceRangeIntent = Field(default_factory=SourceRangeIntent)
    transition_intent: str | None = None
    continuity_intent: str | None = None
    rhythm_intent: str | None = None
    selection_rationale: str
    uncertainty_notes: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0)


class VisualEditTransitionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    from_shot_id: str
    to_shot_id: str
    editorial_function: str
    technical_type: TransitionTechnicalTypeLiteral
    desired_duration_seconds: float = Field(ge=0.0)


class VisualEditPlanGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    project_id: str
    script_lock_id: str
    narration_timeline_id: str
    input_fingerprint: str
    plan_version: int = Field(ge=1)
    model_id: str = VISUAL_EDIT_MODEL_IDENTIFIER
    prompt_version: str = PROMPT_VERSION_VISUAL_EDIT_PLAN
    schema_version: str = RESPONSE_SCHEMA_VISUAL_EDIT_PLAN
    expected_visual_duration_seconds: float = Field(ge=0.0)
    accepted_risks: list[AcceptedRiskRef] = Field(default_factory=list)
    shots: list[VisualEditPlanShotIntent]
    transitions: list[VisualEditTransitionIntent] = Field(default_factory=list)
    created_at: datetime


class HumanityReviewGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    visual_edit_plan_id: str
    review_version: int = Field(ge=1)
    input_fingerprint: str
    overall_judgment: HumanityJudgmentLiteral
    deterministic_signals: dict[str, object] = Field(default_factory=dict)
    findings: list[HumanityFinding] = Field(default_factory=list)
    created_at: datetime


class EditorialRepairProposalGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    input_fingerprint: str
    proposals: list[RepairProposal] = Field(default_factory=list)


class VisualEditPlanBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: VisualEditPlan
    shots: list[EditorialShot] = Field(default_factory=list)
    assignments: list[ShotMediaAssignment] = Field(default_factory=list)
    transitions: list[ShotTransition] = Field(default_factory=list)


class HumanityReviewBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: HumanityReview
    findings: list[HumanityFinding] = Field(default_factory=list)


class FeasibilityReportBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: FeasibilityReport
    issues: list[FeasibilityIssue] = Field(default_factory=list)


class RepairRunBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RepairRun
    result: RepairResult | None = None


@dataclass(frozen=True)
class VisualEditInputGate:
    script_lock_id: str
    lock_fingerprint: str
    narration_timeline_id: str
    input_fingerprint: str
    total_duration_seconds: float
    total_frames: int


def compute_visual_edit_sha256(value: object) -> str:
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


def visual_edit_input_fingerprint(
    *,
    script_lock_id: str,
    lock_fingerprint: str,
    narration_timeline_id: str,
    narration_timeline_fingerprint: str,
    observations: list[object],
    working_media: list[object],
    technical_shots: list[object],
    sentences: list[object],
    visual_beats: list[object],
    visual_intents: list[object],
) -> str:
    def get(item: object, name: str, default: object = None) -> object:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    obs_rows = [
        {
            "observation_id": str(get(item, "observation_id")),
            "asset_id": str(get(item, "asset_id")),
            "analysis_identity_id": str(get(item, "analysis_identity_id")),
            "working_media_id": str(get(item, "working_media_id")),
            "observation_sha256": str(get(item, "observation_sha256")),
            "frame_set_fingerprint": str(get(item, "frame_set_fingerprint")),
        }
        for item in observations
    ]
    wm_rows = [
        {
            "working_media_id": str(get(item, "working_media_id")),
            "asset_id": str(get(item, "asset_id")),
            "output_sha256": str(get(item, "output_sha256")),
            "media_kind": str(get(item, "media_kind")),
            "status": str(get(item, "status")),
            "processing_profile_version": str(get(item, "processing_profile_version")),
        }
        for item in working_media
    ]
    shot_rows = [
        {
            "shot_id": str(get(item, "shot_id")),
            "analysis_identity_id": str(get(item, "analysis_identity_id")),
            "working_media_id": str(get(item, "working_media_id")),
            "start_seconds": float(get(item, "start_seconds", 0.0)),
            "end_seconds": float(get(item, "end_seconds", 0.0)),
            "detection_profile_version": str(get(item, "detection_profile_version")),
        }
        for item in technical_shots
    ]
    sentence_rows = [
        {"sentence_id": str(get(item, "sentence_id")), "ordinal": int(get(item, "ordinal", 0))}
        for item in sentences
    ]
    beat_rows = [
        {"visual_beat_id": str(get(item, "visual_beat_id")), "sentence_ids": list(get(item, "sentence_ids", []))}
        for item in visual_beats
    ]
    intent_rows = [
        {
            "visual_intent_id": str(get(item, "visual_intent_id")),
            "visual_beat_id": str(get(item, "visual_beat_id")),
        }
        for item in visual_intents
    ]
    return compute_visual_edit_sha256(
        {
            "script_lock_id": script_lock_id,
            "lock_fingerprint": lock_fingerprint,
            "narration_timeline_id": narration_timeline_id,
            "narration_timeline_fingerprint": narration_timeline_fingerprint,
            "observations": sorted(obs_rows, key=lambda row: row["observation_id"]),
            "working_media": sorted(wm_rows, key=lambda row: row["working_media_id"]),
            "technical_shots": sorted(shot_rows, key=lambda row: row["shot_id"]),
            "sentences": sorted(sentence_rows, key=lambda row: row["ordinal"]),
            "visual_beats": sorted(beat_rows, key=lambda row: row["visual_beat_id"]),
            "visual_intents": sorted(intent_rows, key=lambda row: row["visual_intent_id"]),
            "profile": VISUAL_EDIT_SCHEMA_VERSION,
        }
    )


def seconds_to_frame_nearest(seconds: float, fps: float) -> int:
    if seconds < 0:
        raise ValueError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
    return int(math.floor(seconds * fps + 0.5 + 1e-9))


__all__ = [name for name in globals() if name.startswith("VISUAL_EDIT_")] + [
    "ACTIVE_VISUAL_EDIT_RUN_STATUSES",
    "ASSET_REUSE_MAX",
    "AcceptedRiskRef",
    "CLOSING_HOLD_MAX_SECONDS",
    "EditorialRepairProposalGatewayPayload",
    "EditorialShot",
    "FeasibilityIssue",
    "FeasibilityReport",
    "FeasibilityReportBundle",
    "GENERIC_STOCK_BLOCKING_RATIO",
    "GENERIC_STOCK_WARNING_RATIO",
    "HumanityFinding",
    "HumanityReview",
    "HumanityReviewBundle",
    "HumanityReviewGatewayPayload",
    "MAX_SHOTS_PER_MINUTE_BLOCKING",
    "MAX_SHOTS_PER_MINUTE_WARNING",
    "MIN_SOURCE_HANDLE_SECONDS",
    "PHOTO_SHOT_MAX_SECONDS",
    "PHOTO_SHOT_MIN_SECONDS",
    "PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL",
    "PROMPT_VERSION_HUMANITY_REVIEW",
    "PROMPT_VERSION_VISUAL_EDIT_PLAN",
    "RankedCandidateRef",
    "RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL",
    "RESPONSE_SCHEMA_HUMANITY_REVIEW",
    "RESPONSE_SCHEMA_VISUAL_EDIT_PLAN",
    "RepairProposal",
    "RepairResult",
    "RepairRun",
    "RepairRunBundle",
    "SHOT_DURATION_VARIANCE_MIN_RATIO",
    "SHOT_DURATION_VARIANCE_MIN_SHOTS",
    "SENTENCE_BOUNDARY_BLOCKING_RATIO",
    "SENTENCE_BOUNDARY_WARNING_RATIO",
    "SIMILAR_MOTIF_BLOCKING_RUN",
    "SIMILAR_MOTIF_WARNING_RUN",
    "SOURCE_RANGE_OVERLAP_RATIO_MAX",
    "SOURCE_RANGE_REUSE_MAX",
    "TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL",
    "TEXT_REQUEST_KIND_HUMANITY_REVIEW",
    "TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN",
    "TIMELINE_DURATION_TOLERANCE_FRAMES",
    "TRANSITION_CUT_SECONDS",
    "TRANSITION_MAX_SECONDS",
    "TRANSITION_MIN_SECONDS",
    "ShotMediaAssignment",
    "ShotTransition",
    "SourceRangeIntent",
    "VisualEditInputGate",
    "VisualEditPlan",
    "VisualEditPlanBundle",
    "VisualEditPlanGatewayPayload",
    "VisualEditPlanShotIntent",
    "VisualEditProjectState",
    "VisualEditRun",
    "VisualEditRunScopeLiteral",
    "VisualEditRunStatus",
    "VisualEditTransitionIntent",
    "compute_visual_edit_sha256",
    "seconds_to_frame_nearest",
    "visual_edit_input_fingerprint",
]
