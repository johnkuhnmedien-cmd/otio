"""Read-only Narration gate model backed by the L2 Effective Lock resolver (L3).

Voice / Pause / Timing gates share one resolution. No pointer or artifact mutation.
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
from otio_app.discovery_v2.domain.narration import (
    NarrationProjectState,
    NarrationRunStatus,
    NarrationTimelineStatus,
    PauseDirectionPlan,
    PauseDirectionPlanStatus,
    ResolvedNarrationTimeline,
    VoiceGenerationRun,
)
from otio_app.discovery_v2.domain.script_lock_current_state import (
    NARRATION_PAUSE_PLAN_NOT_CURRENT,
    NARRATION_POINTER_MATCHING,
    NARRATION_POINTER_MISSING,
    NARRATION_POINTER_STALE,
    NARRATION_SCRIPT_LOCK_MISSING,
    NARRATION_SCRIPT_LOCK_STALE,
    NARRATION_TIMELINE_NOT_CURRENT,
    NARRATION_VOICE_NOT_CURRENT,
    EffectiveScriptLockResolution,
)
from otio_app.discovery_v2.persistence import narration_repository as repo
from otio_app.discovery_v2.persistence.asset_analysis_repository import find_active_analysis_run
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence.editorial_repository import find_active_editorial_run
from otio_app.discovery_v2.persistence.supplementation_repository import (
    find_active_supplementation_run,
)
from otio_app.models import Project


class NarrationGateServiceError(InventoryServiceError):
    """Infrastructure error for narration gate resolution."""


@dataclass(frozen=True)
class NarrationGateState:
    """Central Narration gate model for Voice / Pause / Timing UI and starts."""

    effective_lock_resolution: EffectiveScriptLockResolution
    effective_script_lock_id: str | None
    narration_current_script_lock_id: str | None
    narration_pointer_state: str
    current_voice_run: VoiceGenerationRun | None
    current_pause_plan: PauseDirectionPlan | None
    current_narration_timeline: ResolvedNarrationTimeline | None
    can_start_voice: bool
    can_start_pause_direction: bool
    can_resolve_timing: bool
    blocking_reason_codes: tuple[str, ...]
    diagnostics: list[str] = field(default_factory=list)
    state: NarrationProjectState | None = None
    active_run: VoiceGenerationRun | None = None


def resolve_narration_gate_state(project: Project) -> NarrationGateState:
    """Resolve Narration gates from the L2 Effective Lock (read-only)."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        raise NarrationGateServiceError(str(exc)) from exc

    try:
        resolution = resolve_effective_current_script_lock(project)
        return _resolve(project, resolution)
    except RegistryDatabaseError as exc:
        raise NarrationGateServiceError(str(exc)) from exc


def classify_narration_pointer_state(
    *,
    narration_pointer: str | None,
    effective_script_lock_id: str | None,
    is_effective: bool,
) -> str:
    if not narration_pointer:
        return NARRATION_POINTER_MISSING
    if is_effective and effective_script_lock_id and narration_pointer == effective_script_lock_id:
        return NARRATION_POINTER_MATCHING
    return NARRATION_POINTER_STALE


def _resolve(
    project: Project,
    resolution: EffectiveScriptLockResolution,
) -> NarrationGateState:
    effective_id = (
        resolution.effective_lock.lock_id
        if resolution.is_effective and resolution.effective_lock is not None
        else None
    )
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        active = repo.find_active_narration_run(conn, project_id=project.id)
        narration_pointer = None if state is None else state.current_script_lock_id
        pointer_state = classify_narration_pointer_state(
            narration_pointer=narration_pointer,
            effective_script_lock_id=effective_id,
            is_effective=resolution.is_effective,
        )

        voice_run = None
        pause_plan = None
        timeline = None
        blockers: list[str] = []
        diagnostics = list(resolution.diagnostics)

        if not resolution.is_effective or effective_id is None:
            blockers.append(NARRATION_SCRIPT_LOCK_MISSING)
            if pointer_state == NARRATION_POINTER_STALE:
                blockers.append(NARRATION_SCRIPT_LOCK_STALE)
            # Historical pointers / artifacts are never treated as current.
            if state is not None and state.current_voice_run_id:
                diagnostics.append(NARRATION_VOICE_NOT_CURRENT)
            if state is not None and state.current_pause_plan_id:
                diagnostics.append(NARRATION_PAUSE_PLAN_NOT_CURRENT)
            if state is not None and state.current_timeline_id:
                diagnostics.append(NARRATION_TIMELINE_NOT_CURRENT)
        else:
            if pointer_state == NARRATION_POINTER_STALE:
                blockers.append(NARRATION_SCRIPT_LOCK_STALE)
            voice_run = _current_voice_for_lock(
                conn,
                state=state,
                effective_script_lock_id=effective_id,
            )
            if voice_run is None and state is not None and state.current_voice_run_id:
                diagnostics.append(NARRATION_VOICE_NOT_CURRENT)
            pause_plan = _current_pause_for_lock(
                conn,
                state=state,
                effective_script_lock_id=effective_id,
            )
            if pause_plan is None and state is not None and state.current_pause_plan_id:
                diagnostics.append(NARRATION_PAUSE_PLAN_NOT_CURRENT)
            timeline = _current_timeline_for_lock(
                conn,
                state=state,
                effective_script_lock_id=effective_id,
            )
            if timeline is None and state is not None and state.current_timeline_id:
                diagnostics.append(NARRATION_TIMELINE_NOT_CURRENT)

        other_active = _other_active_blocker(conn, project_id=project.id)
        if active is not None:
            blockers.append("narration_run_already_active")
        if other_active is not None:
            blockers.append(other_active)

        can_voice = bool(
            resolution.is_effective
            and effective_id is not None
            and pointer_state in {NARRATION_POINTER_MISSING, NARRATION_POINTER_MATCHING}
            and active is None
            and other_active is None
        )
        can_pause = bool(
            resolution.is_effective
            and effective_id is not None
            and voice_run is not None
            and voice_run.status == NarrationRunStatus.COMPLETED
            and active is None
            and other_active is None
        )
        if resolution.is_effective and voice_run is None:
            blockers.append(NARRATION_VOICE_NOT_CURRENT)
        can_timing = bool(
            resolution.is_effective
            and effective_id is not None
            and voice_run is not None
            and voice_run.status == NarrationRunStatus.COMPLETED
            and pause_plan is not None
            and pause_plan.status == PauseDirectionPlanStatus.COMPLETED
            and active is None
            and other_active is None
        )
        if resolution.is_effective and pause_plan is None:
            blockers.append(NARRATION_PAUSE_PLAN_NOT_CURRENT)

        # Deduplicate while preserving order.
        blocking = tuple(dict.fromkeys(blockers))

        return NarrationGateState(
            effective_lock_resolution=resolution,
            effective_script_lock_id=effective_id,
            narration_current_script_lock_id=narration_pointer,
            narration_pointer_state=pointer_state,
            current_voice_run=voice_run,
            current_pause_plan=pause_plan,
            current_narration_timeline=timeline,
            can_start_voice=can_voice,
            can_start_pause_direction=can_pause,
            can_resolve_timing=can_timing,
            blocking_reason_codes=blocking,
            diagnostics=diagnostics,
            state=state,
            active_run=active,
        )
    finally:
        conn.close()


def _current_voice_for_lock(conn, *, state, effective_script_lock_id: str):
    if state is None or not state.current_voice_run_id:
        return None
    run = repo.get_voice_run(conn, run_id=state.current_voice_run_id)
    if run is None:
        return None
    if run.script_lock_id != effective_script_lock_id:
        return None
    return run


def _current_pause_for_lock(conn, *, state, effective_script_lock_id: str):
    if state is None or not state.current_pause_plan_id:
        return None
    plan = repo.get_pause_plan(conn, pause_plan_id=state.current_pause_plan_id)
    if plan is None:
        return None
    if plan.script_lock_id != effective_script_lock_id:
        return None
    return plan


def _current_timeline_for_lock(conn, *, state, effective_script_lock_id: str):
    if state is None or not state.current_timeline_id:
        return None
    timeline = repo.get_timeline(conn, timeline_id=state.current_timeline_id)
    if timeline is None:
        return None
    if timeline.script_lock_id != effective_script_lock_id:
        return None
    if timeline.status != NarrationTimelineStatus.COMPLETED:
        # Still expose as current-for-lock when bound; gate uses pause/voice status.
        pass
    return timeline


def _other_active_blocker(conn, *, project_id: str) -> str | None:
    if find_active_analysis_run(conn, project_id=project_id) is not None:
        return "analysis_run_already_active"
    if find_active_editorial_run(conn, project_id=project_id) is not None:
        return "editorial_run_already_active"
    if find_active_supplementation_run(conn, project_id=project_id) is not None:
        return "supplementation_run_already_active"
    return None


__all__ = [
    "NarrationGateServiceError",
    "NarrationGateState",
    "classify_narration_pointer_state",
    "resolve_narration_gate_state",
]
