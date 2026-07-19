"""Application builders for Coverage Gap Semantic Identity (C3.2).

Pure builders only — no materialization integration, no persistence, no matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from otio_app.discovery_v2.application.editorial_service import get_editorial_view
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.domain.coverage_gap_identity import (
    COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID,
    COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    COVERAGE_GAP_SEMANTIC_KEY_COLLISION,
    COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
    CanonicalCoverageGapSemanticIdentity,
    CoverageGapIdentityError,
    build_canonical_gap_semantic_identity,
    compare_gap_semantic_identities,
    compute_canonical_payload_sha256,
    compute_gap_semantic_key,
    require_compatible_gap_semantic_identities,
)
from otio_app.discovery_v2.domain.editorial import VisualIntent
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGap,
    CoverageLevel,
    CoverageRiskFlag,
)
from otio_app.models import Project


@dataclass(frozen=True)
class GapSemanticIdentityResult:
    ok: bool
    schema_version: str | None = None
    semantic_key: str | None = None
    canonical_identity: CanonicalCoverageGapSemanticIdentity | None = None
    canonical_payload_sha256: str | None = None
    message: str = ""
    error_code: str | None = None


@dataclass(frozen=True)
class GapSemanticIdentityCompareResult:
    ok: bool
    identical: bool = False
    message: str = ""
    error_code: str | None = None


def _intent_semantics_from_mapping(intent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "desired_motif": intent.get("desired_motif"),
        "action": intent.get("action"),
        "setting": intent.get("setting"),
        "geographic_requirements": intent.get("geographic_requirements"),
        "authenticity_requirements": list(intent.get("authenticity_requirements") or ()),
        "allowed_media_kinds": list(intent.get("allowed_media_kinds") or ()),
    }


def _intent_semantics_from_visual_intent(intent: VisualIntent) -> dict[str, Any]:
    return {
        "desired_motif": intent.desired_motif,
        "action": intent.action,
        "setting": intent.setting,
        "geographic_requirements": intent.geographic_requirements,
        "authenticity_requirements": list(intent.authenticity_requirements or ()),
        "allowed_media_kinds": list(intent.allowed_media_kinds or ()),
    }


def _risk_code_values(
    risk_codes: Sequence[Any] | None,
) -> list[str]:
    out: list[str] = []
    for item in risk_codes or ():
        if isinstance(item, CoverageRiskFlag):
            out.append(item.value)
        else:
            out.append(str(item))
    return out


def build_gap_semantic_identity(
    *,
    project_id: str,
    visual_intent: VisualIntent | Mapping[str, Any],
    coverage_level: str | CoverageLevel,
    missing_properties: Sequence[Any] | None = None,
    risk_codes: Sequence[Any] | None = None,
) -> GapSemanticIdentityResult:
    """Build semantic identity from resolved Visual Intent + coverage problem parts."""

    if not project_id:
        return GapSemanticIdentityResult(
            ok=False,
            message="project_id fehlt.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )
    if visual_intent is None:
        return GapSemanticIdentityResult(
            ok=False,
            message="Visual Intent fehlt.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )
    if coverage_level is None or coverage_level == "":
        return GapSemanticIdentityResult(
            ok=False,
            message="Coverage Result / coverage_level fehlt.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )

    if isinstance(visual_intent, VisualIntent):
        intent_parts = _intent_semantics_from_visual_intent(visual_intent)
    elif isinstance(visual_intent, Mapping):
        intent_parts = _intent_semantics_from_mapping(visual_intent)
    else:
        return GapSemanticIdentityResult(
            ok=False,
            message="Visual Intent ungültig.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID,
        )

    try:
        identity = build_canonical_gap_semantic_identity(
            project_id=project_id,
            desired_motif=str(intent_parts["desired_motif"] or ""),
            action=str(intent_parts["action"] or ""),
            setting=str(intent_parts["setting"] or ""),
            geographic_requirements=intent_parts.get("geographic_requirements"),
            authenticity_requirements=intent_parts.get("authenticity_requirements"),
            allowed_media_kinds=intent_parts.get("allowed_media_kinds"),
            coverage_level=coverage_level,
            missing_properties=missing_properties,
            risk_codes=_risk_code_values(risk_codes),
        )
        digest = compute_canonical_payload_sha256(identity)
        key = compute_gap_semantic_key(identity)
    except (ValueError, TypeError) as exc:
        return GapSemanticIdentityResult(
            ok=False,
            message=str(exc) or "Semantic Identity ungültig.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID,
        )

    return GapSemanticIdentityResult(
        ok=True,
        schema_version=COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION,
        semantic_key=key,
        canonical_identity=identity,
        canonical_payload_sha256=digest,
        message="Semantic Gap Identity berechnet.",
    )


def build_gap_semantic_identity_for_gap(
    project: Project,
    gap: CoverageGap,
) -> GapSemanticIdentityResult:
    """Resolve Visual Intent for a gap and build its semantic identity (no I/O beyond registry reads)."""

    project = require_discovery_project(project)
    if gap is None:
        return GapSemanticIdentityResult(
            ok=False,
            message="Coverage Gap fehlt.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )

    view = get_editorial_view(project)
    bundle = view.script_bundle or {}
    intents = {
        str(item.get("visual_intent_id")): item
        for item in (bundle.get("visual_intents") or [])
        if isinstance(item, dict)
    }
    intent = intents.get(gap.visual_intent_id)
    if intent is None:
        return GapSemanticIdentityResult(
            ok=False,
            message="Visual Intent für Gap nicht verfügbar.",
            error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )

    return build_gap_semantic_identity(
        project_id=gap.project_id or project.id,
        visual_intent=intent,
        coverage_level=gap.coverage_level,
        missing_properties=gap.missing_properties,
        risk_codes=gap.risk_flags,
    )


def compare_gap_semantic_identity_results(
    left: GapSemanticIdentityResult,
    right: GapSemanticIdentityResult,
) -> GapSemanticIdentityCompareResult:
    """Exact semantic-identity compare including collision detection."""

    if not left.ok or left.canonical_identity is None:
        return GapSemanticIdentityCompareResult(
            ok=False,
            message=left.message or "left identity unavailable",
            error_code=left.error_code or COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )
    if not right.ok or right.canonical_identity is None:
        return GapSemanticIdentityCompareResult(
            ok=False,
            message=right.message or "right identity unavailable",
            error_code=right.error_code or COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
        )

    ok, error_code, message = compare_gap_semantic_identities(
        left.canonical_identity,
        right.canonical_identity,
        left_key=left.semantic_key,
        right_key=right.semantic_key,
    )
    if error_code == COVERAGE_GAP_SEMANTIC_KEY_COLLISION:
        return GapSemanticIdentityCompareResult(
            ok=False,
            identical=False,
            message=message,
            error_code=error_code,
        )
    if error_code:
        return GapSemanticIdentityCompareResult(
            ok=False,
            identical=False,
            message=message,
            error_code=error_code,
        )
    return GapSemanticIdentityCompareResult(
        ok=ok,
        identical=ok,
        message=message,
        error_code=None,
    )


def require_compatible_gap_semantic_identity_results(
    left: GapSemanticIdentityResult,
    right: GapSemanticIdentityResult,
) -> None:
    """Raise CoverageGapIdentityError on key/payload collision."""

    if (
        left.ok
        and right.ok
        and left.canonical_identity is not None
        and right.canonical_identity is not None
    ):
        require_compatible_gap_semantic_identities(
            left.canonical_identity,
            right.canonical_identity,
            left_key=left.semantic_key,
            right_key=right.semantic_key,
        )
        return
    raise CoverageGapIdentityError(
        "semantic identity unavailable for comparison",
        error_code=COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE,
    )


__all__ = [
    "GapSemanticIdentityCompareResult",
    "GapSemanticIdentityResult",
    "build_gap_semantic_identity",
    "build_gap_semantic_identity_for_gap",
    "compare_gap_semantic_identity_results",
    "require_compatible_gap_semantic_identity_results",
]
