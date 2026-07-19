"""Atomic Script Lock / Narration current-state invalidation (L4).

Clears Editorial + Narration current pointers in one registry transaction.
Historical lock / voice / pause / timeline rows are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.narration import (
    ACTIVE_NARRATION_RUN_STATUSES,
    NARRATION_ERROR_WORKER_INTERRUPTED,
    NarrationProjectState,
    NarrationRunStatus,
)
from otio_app.discovery_v2.domain.script_lock_current_state import (
    SCRIPT_LOCK_CONTEXT_ALREADY_INVALID,
    SCRIPT_LOCK_CONTEXT_INVALIDATED,
    SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED,
)
from otio_app.discovery_v2.domain.supplementation import ScriptLockStatus
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import narration_repository as narration_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
    get_registry_connection,
)
from otio_app.models import Project


class ScriptLockCurrentStateMutationError(InventoryServiceError):
    """Infrastructure / transaction error for current-state mutation."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class ScriptLockContextInvalidationResult:
    ok: bool
    reason_code: str
    project_id: str
    invalidated_lock_id: str | None = None
    already_invalid: bool = False
    interrupted_run_ids: tuple[str, ...] = ()
    cleared_editorial_pointer: bool = False
    cleared_narration_pointer: bool = False
    cleared_voice_run_pointer: bool = False
    cleared_pause_plan_pointer: bool = False
    cleared_timeline_pointer: bool = False
    diagnostics: list[str] = field(default_factory=list)
    source_operation_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def invalidate_current_script_lock_context(
    project: Project,
    *,
    reason_code: str,
    source_operation_id: str | None = None,
) -> ScriptLockContextInvalidationResult:
    """Atomically invalidate the current Script Lock context for ``project``.

    Architecture: callers pass a Discovery ``Project`` (same pattern as other
    application services). The result includes ``project_id``.
    """

    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        raise ScriptLockCurrentStateMutationError(
            SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED, str(exc)
        ) from exc

    try:
        conn = get_registry_connection(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise ScriptLockCurrentStateMutationError(
            SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED, str(exc)
        ) from exc

    try:
        conn.execute("BEGIN IMMEDIATE")
        result = apply_script_lock_context_invalidation(
            conn,
            project_id=project.id,
            reason_code=reason_code,
            source_operation_id=source_operation_id,
        )
        conn.commit()
        return result
    except ScriptLockCurrentStateMutationError:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise ScriptLockCurrentStateMutationError(
            SCRIPT_LOCK_CONTEXT_INVALIDATION_FAILED, str(exc)
        ) from exc
    finally:
        conn.close()


def apply_script_lock_context_invalidation(
    conn,
    *,
    project_id: str,
    reason_code: str,
    source_operation_id: str | None = None,
) -> ScriptLockContextInvalidationResult:
    """Apply invalidation on an open connection (caller owns the transaction)."""

    editorial_state = editorial_repo.get_project_state(conn, project_id=project_id)
    narration_state = narration_repo.get_project_state(conn, project_id=project_id)
    editorial_pointer = (
        None if editorial_state is None else editorial_state.current_script_lock_id
    )
    lock = (
        None
        if not editorial_pointer
        else supp_repo.get_script_lock(conn, lock_id=editorial_pointer)
    )

    narr_pointer = None if narration_state is None else narration_state.current_script_lock_id
    voice_ptr = None if narration_state is None else narration_state.current_voice_run_id
    pause_ptr = None if narration_state is None else narration_state.current_pause_plan_id
    timeline_ptr = None if narration_state is None else narration_state.current_timeline_id

    already_clear = (
        editorial_pointer is None
        and narr_pointer is None
        and voice_ptr is None
        and pause_ptr is None
        and timeline_ptr is None
        and (lock is None or lock.status != ScriptLockStatus.LOCKED)
    )
    if already_clear:
        return ScriptLockContextInvalidationResult(
            ok=True,
            reason_code=SCRIPT_LOCK_CONTEXT_ALREADY_INVALID,
            project_id=project_id,
            invalidated_lock_id=None if lock is None else lock.lock_id,
            already_invalid=True,
            diagnostics=[reason_code, "already_invalid"],
            source_operation_id=source_operation_id,
        )

    interrupted: list[str] = []
    active = narration_repo.find_active_narration_run(conn, project_id=project_id)
    if active is not None and active.status in ACTIVE_NARRATION_RUN_STATUSES:
        # Supported historical transition: queued/running → interrupted.
        updated = active.model_copy(
            update={
                "status": NarrationRunStatus.INTERRUPTED,
                "error_code": NARRATION_ERROR_WORKER_INTERRUPTED,
                "error_message": f"Interrupted by script lock context invalidation ({reason_code})",
                "finished_at": _now(),
            }
        )
        narration_repo.update_voice_run(conn, updated)
        interrupted.append(active.run_id)

    invalidated_lock_id = None
    if lock is not None and lock.status == ScriptLockStatus.LOCKED:
        supp_repo.update_script_lock_status(
            conn,
            lock_id=lock.lock_id,
            status=ScriptLockStatus.INVALIDATED,
        )
        invalidated_lock_id = lock.lock_id
    elif lock is not None:
        invalidated_lock_id = lock.lock_id

    cleared_editorial = False
    if editorial_state is not None and editorial_state.current_script_lock_id is not None:
        editorial_repo.upsert_project_state(
            conn,
            editorial_state.model_copy(
                update={
                    "current_script_lock_id": None,
                    "updated_at": _now(),
                }
            ),
        )
        cleared_editorial = True
    elif editorial_state is None and editorial_pointer:
        # Defensive: pointer without state row should not happen.
        pass

    cleared_narration = False
    cleared_voice = False
    cleared_pause = False
    cleared_timeline = False
    if narration_state is None:
        if narr_pointer or voice_ptr or pause_ptr or timeline_ptr:
            narration_repo.upsert_project_state(
                conn,
                NarrationProjectState(
                    project_id=project_id,
                    current_voice_profile_id=None,
                    current_voice_run_id=None,
                    current_pause_plan_id=None,
                    current_timeline_id=None,
                    current_script_lock_id=None,
                    updated_at=_now(),
                ),
            )
            cleared_narration = bool(narr_pointer)
            cleared_voice = bool(voice_ptr)
            cleared_pause = bool(pause_ptr)
            cleared_timeline = bool(timeline_ptr)
    else:
        cleared_narration = narration_state.current_script_lock_id is not None
        cleared_voice = narration_state.current_voice_run_id is not None
        cleared_pause = narration_state.current_pause_plan_id is not None
        cleared_timeline = narration_state.current_timeline_id is not None
        if cleared_narration or cleared_voice or cleared_pause or cleared_timeline:
            narration_repo.upsert_project_state(
                conn,
                narration_state.model_copy(
                    update={
                        "current_script_lock_id": None,
                        "current_voice_run_id": None,
                        "current_pause_plan_id": None,
                        "current_timeline_id": None,
                        # Keep voice profile — not a lock-bound current artifact.
                        "updated_at": _now(),
                    }
                ),
            )

    return ScriptLockContextInvalidationResult(
        ok=True,
        reason_code=SCRIPT_LOCK_CONTEXT_INVALIDATED,
        project_id=project_id,
        invalidated_lock_id=invalidated_lock_id,
        already_invalid=False,
        interrupted_run_ids=tuple(interrupted),
        cleared_editorial_pointer=cleared_editorial,
        cleared_narration_pointer=cleared_narration,
        cleared_voice_run_pointer=cleared_voice,
        cleared_pause_plan_pointer=cleared_pause,
        cleared_timeline_pointer=cleared_timeline,
        diagnostics=[reason_code],
        source_operation_id=source_operation_id,
    )


def clear_narration_current_artifacts_on_conn(
    conn,
    *,
    project_id: str,
    keep_voice_profile: bool = True,
) -> None:
    """Clear Narration current lock/artifact pointers (caller owns transaction)."""

    state = narration_repo.get_project_state(conn, project_id=project_id)
    if state is None:
        narration_repo.upsert_project_state(
            conn,
            NarrationProjectState(project_id=project_id, updated_at=_now()),
        )
        return
    narration_repo.upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_script_lock_id": None,
                "current_voice_run_id": None,
                "current_pause_plan_id": None,
                "current_timeline_id": None,
                "current_voice_profile_id": (
                    state.current_voice_profile_id if keep_voice_profile else None
                ),
                "updated_at": _now(),
            }
        ),
    )


def bind_narration_voice_start_on_conn(
    conn,
    *,
    project_id: str,
    script_lock_id: str,
    voice_profile_id: str,
    voice_run_id: str,
) -> None:
    """Atomically bind Narration current state to a new Voice run (start)."""

    state = narration_repo.get_project_state(conn, project_id=project_id) or NarrationProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    narration_repo.upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_script_lock_id": script_lock_id,
                "current_voice_profile_id": voice_profile_id,
                "current_voice_run_id": voice_run_id,
                "current_pause_plan_id": None,
                "current_timeline_id": None,
                "updated_at": _now(),
            }
        ),
    )


__all__ = [
    "ScriptLockContextInvalidationResult",
    "ScriptLockCurrentStateMutationError",
    "apply_script_lock_context_invalidation",
    "bind_narration_voice_start_on_conn",
    "clear_narration_current_artifacts_on_conn",
    "invalidate_current_script_lock_context",
]
