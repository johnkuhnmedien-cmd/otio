"""Application entry for exact Coverage Gap matching (C3.3).

Pure in-memory matching — no registry reads/writes, no gateway, no media I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from otio_app.discovery_v2.application.coverage_gap_identity_service import (
    GapSemanticIdentityResult,
)
from otio_app.discovery_v2.domain.coverage_gap_identity import (
    COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
)
from otio_app.discovery_v2.domain.coverage_gap_matching import (
    COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_MATCH_INPUT_INVALID,
    COVERAGE_GAP_MATCH_SCHEMA_MISMATCH,
    CoverageGapMatchCandidate,
    CoverageGapMatchError,
    CoverageGapMatchReport,
    CoverageGapMatchRequest,
    VerifiedGapSemanticIdentity,
    match_coverage_gaps,
)


@dataclass(frozen=True)
class GapMatchEngineResult:
    ok: bool
    report: CoverageGapMatchReport | None = None
    message: str = ""
    error_code: str | None = None


def verified_identity_from_result(
    result: GapSemanticIdentityResult,
) -> VerifiedGapSemanticIdentity:
    """Convert a successful C3.2 result into a verified match identity."""

    if (
        not result.ok
        or result.canonical_identity is None
        or not result.semantic_key
        or not result.canonical_payload_sha256
        or not result.schema_version
    ):
        raise CoverageGapMatchError(
            result.message or "semantic identity unavailable",
            error_code=COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE,
        )
    if result.schema_version != COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION:
        raise CoverageGapMatchError(
            f"unsupported semantic schema: {result.schema_version}",
            error_code=COVERAGE_GAP_MATCH_SCHEMA_MISMATCH,
        )
    try:
        return VerifiedGapSemanticIdentity(
            schema_version=result.schema_version,
            semantic_key=result.semantic_key,
            canonical_identity=result.canonical_identity,
            canonical_payload_sha256=result.canonical_payload_sha256,
        )
    except ValueError as exc:
        raise CoverageGapMatchError(
            str(exc),
            error_code=COVERAGE_GAP_MATCH_IDENTITY_UNAVAILABLE,
        ) from exc


def build_match_candidate(
    *,
    project_id: str,
    coverage_audit_id: str,
    gap_id: str,
    semantic_identity_result: GapSemanticIdentityResult | VerifiedGapSemanticIdentity,
) -> CoverageGapMatchCandidate:
    if isinstance(semantic_identity_result, VerifiedGapSemanticIdentity):
        verified = semantic_identity_result
    else:
        verified = verified_identity_from_result(semantic_identity_result)
    return CoverageGapMatchCandidate(
        project_id=project_id,
        coverage_audit_id=coverage_audit_id,
        gap_id=gap_id,
        semantic_identity_result=verified,
    )


def run_exact_gap_match(
    *,
    project_id: str,
    source_audit_id: str,
    target_audit_id: str,
    source_candidates: Sequence[CoverageGapMatchCandidate],
    target_candidates: Sequence[CoverageGapMatchCandidate],
) -> GapMatchEngineResult:
    """Run the exact match engine and return a typed result."""

    try:
        request = CoverageGapMatchRequest(
            project_id=project_id,
            source_audit_id=source_audit_id,
            target_audit_id=target_audit_id,
            source_candidates=list(source_candidates),
            target_candidates=list(target_candidates),
        )
        report = match_coverage_gaps(request)
    except CoverageGapMatchError as exc:
        return GapMatchEngineResult(
            ok=False,
            message=str(exc),
            error_code=exc.error_code,
        )
    except ValueError as exc:
        return GapMatchEngineResult(
            ok=False,
            message=str(exc),
            error_code=COVERAGE_GAP_MATCH_INPUT_INVALID,
        )

    return GapMatchEngineResult(
        ok=True,
        report=report,
        message="exact gap match report computed",
    )


__all__ = [
    "GapMatchEngineResult",
    "build_match_candidate",
    "run_exact_gap_match",
    "verified_identity_from_result",
]
