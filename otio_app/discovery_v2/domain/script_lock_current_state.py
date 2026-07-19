"""Domain contracts for Discovery V2 effective Script Lock current-state (L2).

Read-only resolution codes and result shape. No persistence, no mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from otio_app.discovery_v2.domain.supplementation import ScriptLock

SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION = "script-lock-current-state-v1"

SCRIPT_LOCK_EFFECTIVE = "script_lock_effective"
SCRIPT_LOCK_CURRENT_POINTER_MISSING = "script_lock_current_pointer_missing"
SCRIPT_LOCK_CURRENT_POINTER_STALE = "script_lock_current_pointer_stale"
SCRIPT_LOCK_STATUS_NOT_EFFECTIVE = "script_lock_status_not_effective"
SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH = "script_lock_editorial_state_mismatch"
SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE = "script_lock_fingerprint_unavailable"
SCRIPT_LOCK_FINGERPRINT_MISMATCH = "script_lock_fingerprint_mismatch"
SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH = "script_lock_risk_confirmation_mismatch"
NARRATION_SCRIPT_LOCK_STALE = "narration_script_lock_stale"
NARRATION_SCRIPT_LOCK_MISSING = "narration_script_lock_missing"
NARRATION_VOICE_NOT_CURRENT = "narration_voice_not_current"
NARRATION_PAUSE_PLAN_NOT_CURRENT = "narration_pause_plan_not_current"
NARRATION_TIMELINE_NOT_CURRENT = "narration_timeline_not_current"
NARRATION_ARTIFACT_LOCK_MISMATCH = "narration_artifact_lock_mismatch"
NARRATION_CURRENT_STATE_UPDATE_FAILED = "narration_current_state_update_failed"

SCRIPT_LOCK_CONTEXT_INVALIDATED = "script_lock_context_invalidated"
SCRIPT_LOCK_CONTEXT_ALREADY_INVALID = "script_lock_context_already_invalid"
SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED = "script_lock_context_invalidation_failed"
SCRIPT_LOCK_CONTEXT_ACTIVE_RUN_UNSUPPORTED = "script_lock_context_active_run_unsupported"

NARRATION_POINTER_MISSING = "missing"
NARRATION_POINTER_MATCHING = "matching"
NARRATION_POINTER_STALE = "stale"


@dataclass(frozen=True)
class EffectiveScriptLockResolution:
    """Deterministic result of resolve_effective_current_script_lock."""

    schema_version: str
    project_id: str
    current_script_lock_id: str | None
    is_effective: bool
    effective_lock: ScriptLock | None
    reason_code: str
    diagnostics: list[str] = field(default_factory=list)
    current_fingerprint: str | None = None
    stored_lock_fingerprint: str | None = None
    mismatched_fields: tuple[str, ...] = ()
    narration_current_script_lock_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "EffectiveScriptLockResolution",
    "NARRATION_ARTIFACT_LOCK_MISMATCH",
    "NARRATION_CURRENT_STATE_UPDATE_FAILED",
    "NARRATION_PAUSE_PLAN_NOT_CURRENT",
    "NARRATION_POINTER_MATCHING",
    "NARRATION_POINTER_MISSING",
    "NARRATION_POINTER_STALE",
    "NARRATION_SCRIPT_LOCK_MISSING",
    "NARRATION_SCRIPT_LOCK_STALE",
    "NARRATION_TIMELINE_NOT_CURRENT",
    "NARRATION_VOICE_NOT_CURRENT",
    "SCRIPT_LOCK_CONTEXT_ACTIVE_RUN_UNSUPPORTED",
    "SCRIPT_LOCK_CONTEXT_ALREADY_INVALID",
    "SCRIPT_LOCK_CONTEXT_INVALIDATED",
    "SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED",
    "SCRIPT_LOCK_CURRENT_POINTER_MISSING",
    "SCRIPT_LOCK_CURRENT_POINTER_STALE",
    "SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION",
    "SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH",
    "SCRIPT_LOCK_EFFECTIVE",
    "SCRIPT_LOCK_FINGERPRINT_MISMATCH",
    "SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE",
    "SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH",
    "SCRIPT_LOCK_STATUS_NOT_EFFECTIVE",
]
