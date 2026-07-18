"""Canonical Coverage Input (coverage-input-v1) — fachliche Idempotenz ohne Run-IDs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_RUN_SCOPE_COVERAGE,
    CoverageAudit,
    HookVariant,
    NarrativePlan,
    ProjectBrief,
    ScriptDraft,
    compute_text_sha256,
)

COVERAGE_INPUT_SCHEMA_VERSION = "coverage-input-v1"

CoverageExecutionMode = Literal["normal", "retry_failed", "force_recompute"]

EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID = "coverage_canonical_input_invalid"
EDITORIAL_ERROR_COVERAGE_ACTIVE_RUN_INPUT_UNAVAILABLE = (
    "coverage_active_run_input_unavailable"
)
EDITORIAL_ERROR_COVERAGE_CURRENT_AUDIT_INPUT_UNAVAILABLE = (
    "coverage_current_audit_input_unavailable"
)
EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE = (
    "coverage_completed_audit_reuse_unsafe"
)
EDITORIAL_ERROR_COVERAGE_INPUT_FINGERPRINT_MISMATCH = (
    "coverage_input_fingerprint_mismatch"
)

REUSE_REASON_COMPLETED_CURRENT_AUDIT = "completed_equivalent_current_audit"
REUSE_REASON_ACTIVE_EQUIVALENT_RUN = "active_equivalent_run"


class CanonicalBriefRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_brief_id: str
    brief_version: int = Field(ge=1)
    content_sha256: str


class CanonicalNarrativeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative_plan_id: str
    content_sha256: str


class CanonicalHookRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_id: str
    content_sha256: str


class CanonicalScriptRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str
    script_version: int = Field(ge=1)
    content_sha256: str


class CanonicalStructureRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure_fingerprint: str


class CanonicalVisualIntentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_intent_id: str
    visual_beat_id: str
    desired_motif: str
    action: str
    setting: str
    geographic_requirements: str | None = None
    authenticity_requirements: list[str] = Field(default_factory=list)
    allowed_media_kinds: list[str] = Field(default_factory=list)
    priority: int = Field(ge=1)


class CanonicalModelRoutingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str


class CanonicalCoverageInput(BaseModel):
    """Versioniertes fachliches Coverage-Input-Modell (ohne technische Run-/Audit-IDs)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coverage-input-v1"] = COVERAGE_INPUT_SCHEMA_VERSION
    project_id: str
    brief: CanonicalBriefRef
    narrative: CanonicalNarrativeRef
    hook: CanonicalHookRef
    script: CanonicalScriptRef
    structure: CanonicalStructureRef
    visual_intents: list[CanonicalVisualIntentRef] = Field(default_factory=list)
    observation_fingerprint: str
    model_routing: CanonicalModelRoutingRef
    coverage_scope: Literal["editorial_coverage_only"] = EDITORIAL_RUN_SCOPE_COVERAGE


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_canonical_coverage_fingerprint(coverage_input: CanonicalCoverageInput) -> str:
    """SHA-256 over versioned, UTF-8, sort-stable JSON of the canonical input."""

    payload = coverage_input.model_dump(mode="json")
    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def build_coverage_run_dedup_key(
    *,
    project_id: str,
    canonical_coverage_input_fingerprint: str,
    coverage_scope: str = EDITORIAL_RUN_SCOPE_COVERAGE,
    execution_mode: CoverageExecutionMode = "normal",
) -> str:
    return "|".join(
        (
            project_id,
            canonical_coverage_input_fingerprint,
            coverage_scope,
            execution_mode,
        )
    )


def narrative_content_sha256(plan: NarrativePlan) -> str:
    return compute_text_sha256(
        {
            "central_question": plan.central_question,
            "editorial_thesis": plan.editorial_thesis,
            "hook_strategy": plan.hook_strategy,
            "narrative_roles": list(plan.narrative_roles),
            "arc": plan.arc,
            "transition_logic": plan.transition_logic,
            "ending_function": plan.ending_function,
            "uncertainties": sorted(plan.uncertainties),
        }
    )


def hook_content_sha256(hook: HookVariant) -> str:
    return compute_text_sha256(
        {
            "hook_text": hook.hook_text,
            "hook_type": hook.hook_type,
            "intended_effect": hook.intended_effect,
            "risks": sorted(hook.risks),
        }
    )


def structure_fingerprint_from_bundle(script_bundle: dict[str, Any]) -> str:
    from otio_app.discovery_v2.domain.supplementation import script_structure_fingerprint

    return script_structure_fingerprint(script_bundle)


def visual_intent_refs_from_bundle(
    script_bundle: dict[str, Any],
) -> list[CanonicalVisualIntentRef]:
    intents = script_bundle.get("visual_intents") or []
    refs: list[CanonicalVisualIntentRef] = []
    for item in intents:
        if not isinstance(item, dict):
            continue
        refs.append(
            CanonicalVisualIntentRef(
                visual_intent_id=str(item["visual_intent_id"]),
                visual_beat_id=str(item["visual_beat_id"]),
                desired_motif=str(item.get("desired_motif") or ""),
                action=str(item.get("action") or ""),
                setting=str(item.get("setting") or ""),
                geographic_requirements=(
                    None
                    if item.get("geographic_requirements") in (None, "")
                    else str(item.get("geographic_requirements"))
                ),
                authenticity_requirements=sorted(
                    str(value) for value in (item.get("authenticity_requirements") or [])
                ),
                allowed_media_kinds=sorted(
                    str(value) for value in (item.get("allowed_media_kinds") or [])
                ),
                priority=int(item.get("priority") or 1),
            )
        )
    refs.sort(key=lambda ref: (ref.visual_intent_id, ref.visual_beat_id, ref.priority))
    return refs


def build_canonical_coverage_input(
    *,
    project_id: str,
    brief: ProjectBrief,
    narrative: NarrativePlan,
    hook: HookVariant,
    script: ScriptDraft,
    script_bundle: dict[str, Any],
    observation_fingerprint: str,
    provider: str,
    model_identifier: str,
    gateway_version: str,
    prompt_version: str,
    response_schema_version: str,
) -> CanonicalCoverageInput:
    if not observation_fingerprint.strip():
        raise ValueError("observation_fingerprint missing")
    intents = visual_intent_refs_from_bundle(script_bundle)
    if not intents:
        raise ValueError("visual_intents missing")
    return CanonicalCoverageInput(
        project_id=project_id,
        brief=CanonicalBriefRef(
            project_brief_id=brief.project_brief_id,
            brief_version=brief.brief_version,
            content_sha256=brief.content_sha256,
        ),
        narrative=CanonicalNarrativeRef(
            narrative_plan_id=narrative.narrative_plan_id,
            content_sha256=narrative_content_sha256(narrative),
        ),
        hook=CanonicalHookRef(
            hook_id=hook.hook_id,
            content_sha256=hook_content_sha256(hook),
        ),
        script=CanonicalScriptRef(
            script_id=script.script_id,
            script_version=script.script_version,
            content_sha256=script.content_sha256,
        ),
        structure=CanonicalStructureRef(
            structure_fingerprint=structure_fingerprint_from_bundle(script_bundle),
        ),
        visual_intents=intents,
        observation_fingerprint=observation_fingerprint,
        model_routing=CanonicalModelRoutingRef(
            provider=provider,
            model_identifier=model_identifier,
            gateway_version=gateway_version,
            prompt_version=prompt_version,
            response_schema_version=response_schema_version,
        ),
    )


def audit_has_stored_canonical_fingerprint(audit: CoverageAudit) -> bool:
    value = getattr(audit, "canonical_coverage_input_fingerprint", None)
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "COVERAGE_INPUT_SCHEMA_VERSION",
    "CanonicalBriefRef",
    "CanonicalCoverageInput",
    "CanonicalHookRef",
    "CanonicalModelRoutingRef",
    "CanonicalNarrativeRef",
    "CanonicalScriptRef",
    "CanonicalStructureRef",
    "CanonicalVisualIntentRef",
    "CoverageExecutionMode",
    "EDITORIAL_ERROR_COVERAGE_ACTIVE_RUN_INPUT_UNAVAILABLE",
    "EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID",
    "EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE",
    "EDITORIAL_ERROR_COVERAGE_CURRENT_AUDIT_INPUT_UNAVAILABLE",
    "EDITORIAL_ERROR_COVERAGE_INPUT_FINGERPRINT_MISMATCH",
    "REUSE_REASON_ACTIVE_EQUIVALENT_RUN",
    "REUSE_REASON_COMPLETED_CURRENT_AUDIT",
    "audit_has_stored_canonical_fingerprint",
    "build_canonical_coverage_input",
    "build_coverage_run_dedup_key",
    "compute_canonical_coverage_fingerprint",
    "hook_content_sha256",
    "narrative_content_sha256",
    "structure_fingerprint_from_bundle",
    "visual_intent_refs_from_bundle",
]
