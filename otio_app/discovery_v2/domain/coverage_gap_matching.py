"""Exact Coverage Gap Match Engine (coverage-gap-match-report-v1).

Pure, deterministic exact matching of verified C3.2 semantic identities.
No repository I/O, no carry-forward, no similarity scoring.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from otio_app.discovery_v2.domain.coverage_gap_identity import (
    COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_SEMANTIC_KEY_COLLISION,
    COVERAGE_GAP_SEMANTIC_KEY_PREFIX,
    COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
    CanonicalCoverageGapSemanticIdentity,
    compute_canonical_payload_sha256,
    format_gap_semantic_key,
)

COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION = "coverage-gap-match-report-v1"

COVERAGE_GAP_MATCH_INPUT_INVALID = "coverage_gap_match_input_invalid"
COVERAGE_GAP_MATCH_PROJECT_MISMATCH = "coverage_gap_match_project_mismatch"
COVERAGE_GAP_MATCH_AUDIT_MISMATCH = "coverage_gap_match_audit_mismatch"
COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE = "coverage_gap_match_duplicate_instance"
COVERAGE_GAP_MATCH_SCHEMA_MISMATCH = "coverage_gap_match_schema_mismatch"
COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE = "coverage_gap_match_identity_unavailable"

_KEY_RE = re.compile(
    rf"^{re.escape(COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION)}:[0-9a-f]{{64}}$"
)


class CoverageGapMatchClass(str, Enum):
    EXACT_ONE_TO_ONE = "exact_one_to_one"
    AMBIGUOUS_ONE_TO_MANY = "ambiguous_one_to_many"
    AMBIGUOUS_MANY_TO_ONE = "ambiguous_many_to_one"
    AMBIGUOUS_MANY_TO_MANY = "ambiguous_many_to_many"
    UNMATCHED_SOURCE = "unmatched_source"
    UNMATCHED_TARGET = "unmatched_target"
    BLOCKED_COLLISION = "blocked_collision"
    BLOCKED_SCHEMA_MISMATCH = "blocked_schema_mismatch"
    BLOCKED_IDENTITY_UNAVAILABLE = "blocked_identity_unavailable"


class VerifiedGapSemanticIdentity(BaseModel):
    """Verified C3.2 identity snapshot required for exact matching."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    semantic_key: str
    canonical_identity: CanonicalCoverageGapSemanticIdentity
    canonical_payload_sha256: str

    @field_validator("schema_version")
    @classmethod
    def _supported_schema(cls, value: str) -> str:
        if value != COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION:
            raise ValueError(f"unsupported semantic schema: {value}")
        return value

    @field_validator("semantic_key")
    @classmethod
    def _valid_key_format(cls, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(f"invalid semantic_key format: {value!r}")
        return value

    @model_validator(mode="after")
    def _payload_and_digest_consistent(self) -> VerifiedGapSemanticIdentity:
        digest = compute_canonical_payload_sha256(self.canonical_identity)
        if digest != self.canonical_payload_sha256.lower():
            raise ValueError("canonical_payload_sha256 inconsistent with payload")
        expected_key = format_gap_semantic_key(digest)
        if self.semantic_key != expected_key:
            raise ValueError("semantic_key inconsistent with canonical payload")
        if self.canonical_identity.schema_version != self.schema_version:
            raise ValueError("identity schema_version mismatch")
        return self


class CoverageGapMatchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    coverage_audit_id: str
    gap_id: str
    semantic_identity_result: VerifiedGapSemanticIdentity


class CoverageGapMatchGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_key: str
    match_class: CoverageGapMatchClass
    source_gap_ids: list[str] = Field(default_factory=list)
    target_gap_ids: list[str] = Field(default_factory=list)
    carry_forward_evaluation_allowed: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class CoverageGapMatchReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_count: int = Field(ge=0)
    exact_one_to_one_count: int = Field(ge=0)
    ambiguous_group_count: int = Field(ge=0)
    unmatched_source_count: int = Field(ge=0)
    unmatched_target_count: int = Field(ge=0)
    blocked_group_count: int = Field(ge=0)
    carry_forward_evaluation_allowed_count: int = Field(ge=0)


class CoverageGapMatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coverage-gap-match-report-v1"] = (
        COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION
    )
    project_id: str
    source_audit_id: str
    target_audit_id: str
    groups: list[CoverageGapMatchGroup] = Field(default_factory=list)
    unmatched_source_gap_ids: list[str] = Field(default_factory=list)
    unmatched_target_gap_ids: list[str] = Field(default_factory=list)
    report_fingerprint: str
    summary: CoverageGapMatchReportSummary


class CoverageGapMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    source_audit_id: str
    target_audit_id: str
    source_candidates: list[CoverageGapMatchCandidate] = Field(default_factory=list)
    target_candidates: list[CoverageGapMatchCandidate] = Field(default_factory=list)


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_match_report_fingerprint(report_payload: dict[str, Any]) -> str:
    """SHA-256 over versioned report payload without fingerprint field."""

    payload = dict(report_payload)
    payload.pop("report_fingerprint", None)
    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


class CoverageGapMatchError(ValueError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _validate_request(request: CoverageGapMatchRequest) -> None:
    if not request.project_id:
        raise CoverageGapMatchError(
            "project_id fehlt",
            error_code=COVERAGE_GAP_MATCH_INPUT_INVALID,
        )
    if not request.source_audit_id or not request.target_audit_id:
        raise CoverageGapMatchError(
            "source_audit_id und target_audit_id sind erforderlich",
            error_code=COVERAGE_GAP_MATCH_INPUT_INVALID,
        )
    if request.source_audit_id == request.target_audit_id:
        raise CoverageGapMatchError(
            "source_audit_id und target_audit_id müssen verschieden sein",
            error_code=COVERAGE_GAP_MATCH_INPUT_INVALID,
        )

    source_ids: set[str] = set()
    for candidate in request.source_candidates:
        if candidate.project_id != request.project_id:
            raise CoverageGapMatchError(
                f"source candidate project mismatch: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_PROJECT_MISMATCH,
            )
        if candidate.coverage_audit_id != request.source_audit_id:
            raise CoverageGapMatchError(
                f"source candidate audit mismatch: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_AUDIT_MISMATCH,
            )
        if candidate.gap_id in source_ids:
            raise CoverageGapMatchError(
                f"duplicate source gap_id: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE,
            )
        source_ids.add(candidate.gap_id)
        _assert_verified_identity(candidate)

    target_ids: set[str] = set()
    for candidate in request.target_candidates:
        if candidate.project_id != request.project_id:
            raise CoverageGapMatchError(
                f"target candidate project mismatch: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_PROJECT_MISMATCH,
            )
        if candidate.coverage_audit_id != request.target_audit_id:
            raise CoverageGapMatchError(
                f"target candidate audit mismatch: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_AUDIT_MISMATCH,
            )
        if candidate.gap_id in target_ids:
            raise CoverageGapMatchError(
                f"duplicate target gap_id: {candidate.gap_id}",
                error_code=COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE,
            )
        target_ids.add(candidate.gap_id)
        _assert_verified_identity(candidate)

    overlap = source_ids & target_ids
    if overlap:
        raise CoverageGapMatchError(
            f"gap_id darf nicht zugleich Source und Target sein: {sorted(overlap)[0]}",
            error_code=COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE,
        )


def _assert_verified_identity(candidate: CoverageGapMatchCandidate) -> None:
    identity = candidate.semantic_identity_result
    if identity.canonical_identity.project_id != candidate.project_id:
        raise CoverageGapMatchError(
            f"identity project_id mismatch for gap {candidate.gap_id}",
            error_code=COVERAGE_GAP_MATCH_PROJECT_MISMATCH,
        )
    if identity.schema_version != COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION:
        raise CoverageGapMatchError(
            f"unsupported identity schema for gap {candidate.gap_id}",
            error_code=COVERAGE_GAP_MATCH_SCHEMA_MISMATCH,
        )


def _payload_fingerprint(identity: CanonicalCoverageGapSemanticIdentity) -> str:
    return json.dumps(
        identity.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _classify_group(
    *,
    semantic_key: str,
    source_gap_ids: list[str],
    target_gap_ids: list[str],
    blocked_reason: str | None = None,
) -> CoverageGapMatchGroup:
    sources = sorted(source_gap_ids)
    targets = sorted(target_gap_ids)

    if blocked_reason == COVERAGE_GAP_SEMANTIC_KEY_COLLISION:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.BLOCKED_COLLISION,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=[COVERAGE_GAP_SEMANTIC_KEY_COLLISION],
        )
    if blocked_reason == COVERAGE_GAP_MATCH_SCHEMA_MISMATCH:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.BLOCKED_SCHEMA_MISMATCH,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=[COVERAGE_GAP_MATCH_SCHEMA_MISMATCH],
        )
    if blocked_reason in {
        COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE,
        COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    }:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.BLOCKED_IDENTITY_UNAVAILABLE,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=[COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE],
        )

    if sources and not targets:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.UNMATCHED_SOURCE,
            source_gap_ids=sources,
            target_gap_ids=[],
            carry_forward_evaluation_allowed=False,
            reason_codes=["unmatched_source"],
        )
    if targets and not sources:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.UNMATCHED_TARGET,
            source_gap_ids=[],
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=["unmatched_target"],
        )

    if len(sources) == 1 and len(targets) == 1:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.EXACT_ONE_TO_ONE,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=True,
            reason_codes=["exact_identity_match"],
        )
    if len(sources) == 1 and len(targets) > 1:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.AMBIGUOUS_ONE_TO_MANY,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=["ambiguous_one_to_many"],
        )
    if len(sources) > 1 and len(targets) == 1:
        return CoverageGapMatchGroup(
            semantic_key=semantic_key,
            match_class=CoverageGapMatchClass.AMBIGUOUS_MANY_TO_ONE,
            source_gap_ids=sources,
            target_gap_ids=targets,
            carry_forward_evaluation_allowed=False,
            reason_codes=["ambiguous_many_to_one"],
        )
    return CoverageGapMatchGroup(
        semantic_key=semantic_key,
        match_class=CoverageGapMatchClass.AMBIGUOUS_MANY_TO_MANY,
        source_gap_ids=sources,
        target_gap_ids=targets,
        carry_forward_evaluation_allowed=False,
        reason_codes=["ambiguous_many_to_many"],
    )


def match_coverage_gaps(request: CoverageGapMatchRequest) -> CoverageGapMatchReport:
    """Exact-match gaps by verified C3.2 identity (key + full payload)."""

    _validate_request(request)

    # Cluster by semantic_key, then verify payload equality within each cluster.
    clusters: dict[str, dict[str, Any]] = {}

    def _add(side: str, candidate: CoverageGapMatchCandidate) -> None:
        key = candidate.semantic_identity_result.semantic_key
        cluster = clusters.setdefault(
            key,
            {
                "sources": [],
                "targets": [],
                "payloads": {},
                "blocked_reason": None,
            },
        )
        gap_id = candidate.gap_id
        payload_fp = _payload_fingerprint(
            candidate.semantic_identity_result.canonical_identity
        )
        existing = cluster["payloads"].get(payload_fp)
        if existing is None:
            # First payload for this key in this cluster.
            if cluster["payloads"] and payload_fp not in cluster["payloads"]:
                cluster["blocked_reason"] = COVERAGE_GAP_SEMANTIC_KEY_COLLISION
            cluster["payloads"][payload_fp] = gap_id
        # If a different payload fingerprint already exists under same key → collision.
        if len(cluster["payloads"]) > 1:
            cluster["blocked_reason"] = COVERAGE_GAP_SEMANTIC_KEY_COLLISION

        if side == "source":
            cluster["sources"].append(gap_id)
        else:
            cluster["targets"].append(gap_id)

    for candidate in request.source_candidates:
        _add("source", candidate)
    for candidate in request.target_candidates:
        _add("target", candidate)

    groups: list[CoverageGapMatchGroup] = []
    unmatched_sources: list[str] = []
    unmatched_targets: list[str] = []

    for semantic_key in sorted(clusters.keys()):
        cluster = clusters[semantic_key]
        group = _classify_group(
            semantic_key=semantic_key,
            source_gap_ids=cluster["sources"],
            target_gap_ids=cluster["targets"],
            blocked_reason=cluster["blocked_reason"],
        )
        groups.append(group)
        if group.match_class == CoverageGapMatchClass.UNMATCHED_SOURCE:
            unmatched_sources.extend(group.source_gap_ids)
        elif group.match_class == CoverageGapMatchClass.UNMATCHED_TARGET:
            unmatched_targets.extend(group.target_gap_ids)

    groups.sort(key=lambda g: (g.semantic_key, g.match_class.value))
    unmatched_sources = sorted(set(unmatched_sources))
    unmatched_targets = sorted(set(unmatched_targets))

    summary = CoverageGapMatchReportSummary(
        group_count=len(groups),
        exact_one_to_one_count=sum(
            1
            for g in groups
            if g.match_class == CoverageGapMatchClass.EXACT_ONE_TO_ONE
        ),
        ambiguous_group_count=sum(
            1
            for g in groups
            if g.match_class
            in {
                CoverageGapMatchClass.AMBIGUOUS_ONE_TO_MANY,
                CoverageGapMatchClass.AMBIGUOUS_MANY_TO_ONE,
                CoverageGapMatchClass.AMBIGUOUS_MANY_TO_MANY,
            }
        ),
        unmatched_source_count=len(unmatched_sources),
        unmatched_target_count=len(unmatched_targets),
        blocked_group_count=sum(
            1
            for g in groups
            if g.match_class
            in {
                CoverageGapMatchClass.BLOCKED_COLLISION,
                CoverageGapMatchClass.BLOCKED_SCHEMA_MISMATCH,
                CoverageGapMatchClass.BLOCKED_IDENTITY_UNAVAILABLE,
            }
        ),
        carry_forward_evaluation_allowed_count=sum(
            1 for g in groups if g.carry_forward_evaluation_allowed
        ),
    )

    draft = {
        "schema_version": COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION,
        "project_id": request.project_id,
        "source_audit_id": request.source_audit_id,
        "target_audit_id": request.target_audit_id,
        "groups": [g.model_dump(mode="json") for g in groups],
        "unmatched_source_gap_ids": unmatched_sources,
        "unmatched_target_gap_ids": unmatched_targets,
        "summary": summary.model_dump(mode="json"),
    }
    fingerprint = compute_match_report_fingerprint(draft)

    return CoverageGapMatchReport(
        project_id=request.project_id,
        source_audit_id=request.source_audit_id,
        target_audit_id=request.target_audit_id,
        groups=groups,
        unmatched_source_gap_ids=unmatched_sources,
        unmatched_target_gap_ids=unmatched_targets,
        report_fingerprint=fingerprint,
        summary=summary,
    )


__all__ = [
    "COVERAGE_GAP_MATCH_AUDIT_MISMATCH",
    "COVERAGE_GAP_MATCH_DUPLICATE_INSTANCE",
    "COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE",
    "COVERAGE_GAP_MATCH_INPUT_INVALID",
    "COVERAGE_GAP_MATCH_PROJECT_MISMATCH",
    "COVERAGE_GAP_MATCH_REPORT_SCHEMA_VERSION",
    "COVERAGE_GAP_MATCH_SCHEMA_MISMATCH",
    "COVERAGE_GAP_SEMANTIC_KEY_COLLISION",
    "CoverageGapMatchCandidate",
    "CoverageGapMatchClass",
    "CoverageGapMatchError",
    "CoverageGapMatchGroup",
    "CoverageGapMatchReport",
    "CoverageGapMatchReportSummary",
    "CoverageGapMatchRequest",
    "VerifiedGapSemanticIdentity",
    "compute_match_report_fingerprint",
    "match_coverage_gaps",
]
