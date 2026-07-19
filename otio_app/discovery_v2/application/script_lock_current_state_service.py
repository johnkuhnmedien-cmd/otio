"""Read-only Effective Script Lock resolver (L2).

Answers: does this project have a currently effective Script Lock?

Does not create locks, mutate pointers, invalidate locks, or write artifacts.
"""

from __future__ import annotations

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.application.script_lock_service import (
    build_current_script_lock_preview,
)
from otio_app.discovery_v2.domain.editorial import compute_observation_set_fingerprint
from otio_app.discovery_v2.domain.script_lock_current_state import (
    NARRATION_SCRIPT_LOCK_STALE,
    SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    SCRIPT_LOCK_CURRENT_POINTER_STALE,
    SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
    SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
    SCRIPT_LOCK_EFFECTIVE,
    SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
    SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH,
    SCRIPT_LOCK_STATUS_NOT_EFFECTIVE,
    EffectiveScriptLockResolution,
)
from otio_app.discovery_v2.domain.supplementation import ScriptLockStatus
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import narration_repository as narration_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project


class ScriptLockCurrentStateServiceError(InventoryServiceError):
    """Infrastructure / corrupt-data error for current-state resolution."""


def resolve_effective_current_script_lock(
    project: Project,
) -> EffectiveScriptLockResolution:
    """Resolve whether the project has a currently effective Script Lock.

    Architecture note: callers pass a Discovery ``Project`` (same pattern as
    other application services). The result always includes ``project_id``.
    """

    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        raise ScriptLockCurrentStateServiceError(str(exc)) from exc

    try:
        return _resolve(project)
    except RegistryDatabaseError as exc:
        raise ScriptLockCurrentStateServiceError(str(exc)) from exc


def _resolve(project: Project) -> EffectiveScriptLockResolution:
    project_id = project.id
    editorial_pointer: str | None = None
    narration_pointer: str | None = None

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project_id)
        editorial_pointer = None if state is None else state.current_script_lock_id
    finally:
        conn.close()

    narr_conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        narr_state = narration_repo.get_project_state(narr_conn, project_id=project_id)
        narration_pointer = (
            None if narr_state is None else narr_state.current_script_lock_id
        )
    finally:
        narr_conn.close()

    if not editorial_pointer:
        diagnostics = _narration_diagnostics(
            editorial_pointer=None,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=None,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_CURRENT_POINTER_MISSING,
            diagnostics=diagnostics,
            narration_current_script_lock_id=narration_pointer,
        )

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        lock = supp_repo.get_script_lock(conn, lock_id=editorial_pointer)
        script = editorial_repo.get_active_script(conn, project_id=project_id)
        state = editorial_repo.get_project_state(conn, project_id=project_id)
        coverage = None
        if state is not None and state.active_coverage_audit_id:
            coverage = editorial_repo.get_coverage_audit(
                conn,
                coverage_audit_id=state.active_coverage_audit_id,
            )
    finally:
        conn.close()

    if lock is None:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_CURRENT_POINTER_STALE,
            diagnostics=diagnostics + ["pointed_lock_missing"],
            stored_lock_fingerprint=None,
            narration_current_script_lock_id=narration_pointer,
        )

    stored_fp = lock.lock_fingerprint or None

    if lock.project_id != project_id:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
            diagnostics=diagnostics + ["project_id"],
            mismatched_fields=("project_id",),
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
        )

    if lock.status != ScriptLockStatus.LOCKED:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_STATUS_NOT_EFFECTIVE,
            diagnostics=diagnostics + [f"status={lock.status.value}"],
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
        )

    current_obs_fp = _current_observation_fingerprint(project)
    mismatched = _identity_mismatches(
        lock=lock,
        project_id=project_id,
        script=script,
        state=state,
        coverage=coverage,
        observation_fingerprint=current_obs_fp,
    )
    if mismatched:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
            diagnostics=diagnostics + [f"mismatch:{name}" for name in mismatched],
            mismatched_fields=mismatched,
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
        )

    # Canonical fingerprint via existing preview builder (no gap materialization).
    preview = build_current_script_lock_preview(project)
    current_fp = preview.lock_fingerprint
    if not current_fp:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
            diagnostics=diagnostics
            + [f"preview_blocker:{item}" for item in (preview.blockers or [])[:12]],
            current_fingerprint=None,
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
        )

    if current_fp != lock.lock_fingerprint:
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_FINGERPRINT_MISMATCH,
            diagnostics=diagnostics + ["lock_fingerprint"],
            current_fingerprint=current_fp,
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
        )

    current_risks = tuple(sorted(preview.accepted_open_risks or ()))
    lock_risks = tuple(sorted(lock.accepted_open_risks or ()))
    confirmation_fp = lock.confirmation_fingerprint or ""
    if current_risks != lock_risks or (
        confirmation_fp and confirmation_fp != current_fp
    ):
        diagnostics = _narration_diagnostics(
            editorial_pointer=editorial_pointer,
            narration_pointer=narration_pointer,
            effective_lock_id=None,
            is_effective=False,
        )
        detail = []
        if current_risks != lock_risks:
            detail.append("accepted_open_risks")
        if confirmation_fp and confirmation_fp != current_fp:
            detail.append("confirmation_fingerprint")
        return EffectiveScriptLockResolution(
            schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
            project_id=project_id,
            current_script_lock_id=editorial_pointer,
            is_effective=False,
            effective_lock=None,
            reason_code=SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH,
            diagnostics=diagnostics + detail,
            current_fingerprint=current_fp,
            stored_lock_fingerprint=stored_fp,
            narration_current_script_lock_id=narration_pointer,
            extra={
                "current_risk_key_count": len(current_risks),
                "lock_risk_key_count": len(lock_risks),
            },
        )

    diagnostics = _narration_diagnostics(
        editorial_pointer=editorial_pointer,
        narration_pointer=narration_pointer,
        effective_lock_id=lock.lock_id,
        is_effective=True,
    )
    return EffectiveScriptLockResolution(
        schema_version=SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
        project_id=project_id,
        current_script_lock_id=editorial_pointer,
        is_effective=True,
        effective_lock=lock,
        reason_code=SCRIPT_LOCK_EFFECTIVE,
        diagnostics=diagnostics,
        current_fingerprint=current_fp,
        stored_lock_fingerprint=stored_fp,
        narration_current_script_lock_id=narration_pointer,
    )


def _current_observation_fingerprint(project: Project) -> str:
    observations = list_editorial_ready_observations(project)
    return compute_observation_set_fingerprint(
        [
            type(
                "Obs",
                (),
                {
                    "observation_id": item.observation_id,
                    "asset_id": item.asset_id,
                    "observation_sha256": item.observation_sha256,
                    "frame_set_fingerprint": item.frame_set_fingerprint,
                },
            )()
            for item in observations
        ]
    )


def _identity_mismatches(
    *,
    lock,
    project_id: str,
    script,
    state,
    coverage,
    observation_fingerprint: str,
) -> tuple[str, ...]:
    mismatched: list[str] = []
    if lock.project_id != project_id:
        mismatched.append("project_id")
    if script is None:
        mismatched.extend(
            [
                "script_id",
                "script_version",
                "narrative_plan_id",
                "selected_hook_id",
            ]
        )
    else:
        if lock.script_id != script.script_id:
            mismatched.append("script_id")
        if lock.script_version != script.script_version:
            mismatched.append("script_version")
        if lock.narrative_plan_id != script.narrative_plan_id:
            mismatched.append("narrative_plan_id")
        current_hook = script.selected_hook_id or (
            state.selected_hook_id if state is not None else None
        )
        if not current_hook or lock.selected_hook_id != current_hook:
            mismatched.append("selected_hook_id")
    current_audit = None if coverage is None else coverage.coverage_audit_id
    if not current_audit or lock.coverage_audit_id != current_audit:
        mismatched.append("coverage_audit_id")
    if lock.observation_set_fingerprint != observation_fingerprint:
        mismatched.append("observation_fingerprint")
    # Stable order, unique.
    order = (
        "project_id",
        "script_id",
        "script_version",
        "narrative_plan_id",
        "selected_hook_id",
        "coverage_audit_id",
        "observation_fingerprint",
    )
    return tuple(name for name in order if name in set(mismatched))


def _narration_diagnostics(
    *,
    editorial_pointer: str | None,
    narration_pointer: str | None,
    effective_lock_id: str | None,
    is_effective: bool,
) -> list[str]:
    """Diagnose narration pointer without letting it replace editorial current."""

    out: list[str] = []
    if narration_pointer and not editorial_pointer:
        out.append(NARRATION_SCRIPT_LOCK_STALE)
        return out
    if is_effective and effective_lock_id is not None:
        if narration_pointer is None or narration_pointer != effective_lock_id:
            out.append(NARRATION_SCRIPT_LOCK_STALE)
    elif narration_pointer and editorial_pointer and narration_pointer != editorial_pointer:
        out.append(NARRATION_SCRIPT_LOCK_STALE)
    return out


__all__ = [
    "ScriptLockCurrentStateServiceError",
    "resolve_effective_current_script_lock",
]
