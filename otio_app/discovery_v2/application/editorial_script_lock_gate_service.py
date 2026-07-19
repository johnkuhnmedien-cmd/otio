"""Read-only Editorial Script Lock gate model (L3).

UI renders this model. No pointer mutation, no lock creation, no artifact writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.script_lock_current_state_service import (
    resolve_effective_current_script_lock,
)
from otio_app.discovery_v2.application.script_lock_service import (
    ScriptLockPreview,
    build_current_script_lock_preview,
)
from otio_app.discovery_v2.domain.script_lock_current_state import (
    SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    SCRIPT_LOCK_EFFECTIVE,
    SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
    EffectiveScriptLockResolution,
)
from otio_app.discovery_v2.domain.supplementation import ScriptLock
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


class EditorialScriptLockGateServiceError(InventoryServiceError):
    """Infrastructure error for editorial script-lock gate resolution."""


@dataclass(frozen=True)
class EditorialScriptLockGateState:
    """Application gate model for Editorial Script Lock UI."""

    resolution: EffectiveScriptLockResolution
    has_effective_current_lock: bool
    effective_lock: ScriptLock | None
    historical_locks: tuple[ScriptLock, ...]
    current_preview: ScriptLockPreview
    current_fingerprint: str | None
    required_risk_keys: tuple[str, ...]
    confirmed_risk_keys: tuple[str, ...]
    confirmations_complete: bool
    can_create_lock: bool
    blocking_reason_codes: tuple[str, ...]
    diagnostics: list[str] = field(default_factory=list)


def resolve_editorial_script_lock_gate(
    project: Project,
    *,
    user_confirmed: bool = False,
    risk_confirmations: dict[str, bool] | None = None,
) -> EditorialScriptLockGateState:
    """Resolve Editorial Current-vs-History gate state (read-only)."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        raise EditorialScriptLockGateServiceError(str(exc)) from exc

    try:
        resolution = resolve_effective_current_script_lock(project)
        preview = build_current_script_lock_preview(project)
        locks = _list_script_locks(project)
    except RegistryDatabaseError as exc:
        raise EditorialScriptLockGateServiceError(str(exc)) from exc

    effective = resolution.effective_lock if resolution.is_effective else None
    effective_id = None if effective is None else effective.lock_id
    historical = tuple(
        lock for lock in locks if effective_id is None or lock.lock_id != effective_id
    )

    required = tuple(preview.accepted_open_risks or ())
    confirmations = risk_confirmations or {}
    confirmed = tuple(
        key for key in required if bool(confirmations.get(key, False))
    )
    risks_ok = (not required) or all(bool(confirmations.get(key, False)) for key in required)
    confirmations_complete = bool(user_confirmed and risks_ok)

    current_fingerprint = preview.lock_fingerprint
    fachlich_ready = bool(preview.ok and current_fingerprint)
    blockers: list[str] = []
    if resolution.is_effective:
        blockers.append(SCRIPT_LOCK_EFFECTIVE)
    if not fachlich_ready:
        blockers.append(SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE)
    if not user_confirmed:
        blockers.append("script_lock_confirmation_required")
    if required and not risks_ok:
        blockers.append("script_lock_risk_confirmation_mismatch")
    if preview.blockers:
        blockers.extend(f"preview_blocker:{code}" for code in preview.blockers)

    can_create_lock = bool(
        (not resolution.is_effective)
        and fachlich_ready
        and confirmations_complete
        and not preview.blockers
    )

    diagnostics = list(resolution.diagnostics)
    if not resolution.is_effective and resolution.reason_code:
        diagnostics.append(resolution.reason_code)
    if historical and resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING:
        diagnostics.append("historical_locks_present_not_current")

    return EditorialScriptLockGateState(
        resolution=resolution,
        has_effective_current_lock=bool(resolution.is_effective and effective is not None),
        effective_lock=effective,
        historical_locks=historical,
        current_preview=preview,
        current_fingerprint=current_fingerprint,
        required_risk_keys=required,
        confirmed_risk_keys=confirmed,
        confirmations_complete=confirmations_complete,
        can_create_lock=can_create_lock,
        blocking_reason_codes=tuple(dict.fromkeys(blockers)),
        diagnostics=diagnostics,
    )


def _list_script_locks(project: Project) -> list[ScriptLock]:
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        return list(supp_repo.list_script_locks(conn, project_id=project.id))
    finally:
        conn.close()


__all__ = [
    "EditorialScriptLockGateServiceError",
    "EditorialScriptLockGateState",
    "resolve_editorial_script_lock_gate",
]
