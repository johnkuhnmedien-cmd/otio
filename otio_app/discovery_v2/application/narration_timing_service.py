"""Deterministic narration timing resolver for Discovery V2 Phase 11."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from otio_app.discovery_v2.adapters.narration_job_launcher import get_narration_job_launcher
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.voice_generation_service import (
    NarrationServiceError,
    NarrationStartResult,
    _active_blocker,
    require_effective_lock_for_narration,
)
from otio_app.discovery_v2.domain.narration import (
    MAX_PAUSE_RATIO,
    NARRATION_ERROR_INPUT_STALE,
    NARRATION_ERROR_INVALID_TIMELINE,
    NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT,
    NARRATION_ERROR_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_TIMING_RESOLUTION_FAILED,
    NARRATION_ERROR_VOICE_SEGMENT_MISSING,
    NARRATION_RUN_SCOPE_TIMING,
    TIMING_PROFILE_VERSION,
    NarrationRunStatus,
    NarrationTimelineEntry,
    NarrationTimelineEntryType,
    NarrationTimelineStatus,
    PauseDirection,
    PauseFunction,
    PauseHardness,
    PausePositionKind,
    ResolvedNarrationTimeline,
    VoiceGenerationRun,
    VoiceSegment,
    clamp_pause_duration,
    pause_max_for_function,
    seconds_to_frame_floor,
    timebase_from_fps,
    timing_input_fingerprint,
)
from otio_app.discovery_v2.persistence import narration_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class TimingResolveResult:
    ok: bool
    message: str
    run: VoiceGenerationRun | None = None
    timeline: ResolvedNarrationTimeline | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_narration_timing_run(project: Project, *, sync: bool = True) -> NarrationStartResult:
    project = require_discovery_project(project)
    try:
        lock_input = require_effective_lock_for_narration(project)
    except NarrationServiceError as exc:
        return NarrationStartResult(False, str(exc), error_code=exc.code)
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        blocked = _active_blocker(conn, project_id=project.id)
        if blocked is not None:
            code, message = blocked
            return NarrationStartResult(False, message, error_code=code)
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_voice_run_id is None or state.current_pause_plan_id is None:
            return NarrationStartResult(
                False,
                "Voice-Run oder Pause-Plan fehlt.",
                error_code=NARRATION_ERROR_INPUT_STALE,
            )
        voice_run = repo.get_voice_run(conn, run_id=state.current_voice_run_id)
        if voice_run is None or voice_run.status != NarrationRunStatus.COMPLETED:
            return NarrationStartResult(
                False,
                "Completed Voice-Run fehlt.",
                error_code=NARRATION_ERROR_INPUT_STALE,
            )
        pause_plan = repo.get_pause_plan(conn, pause_plan_id=state.current_pause_plan_id)
        if pause_plan is None:
            return NarrationStartResult(
                False,
                "Pause-Plan fehlt.",
                error_code=NARRATION_ERROR_INPUT_STALE,
            )
        # L3: Voice and Pause must both bind to the effective current lock.
        if voice_run.script_lock_id != lock_input.lock.lock_id:
            from otio_app.discovery_v2.domain.script_lock_current_state import (
                NARRATION_VOICE_NOT_CURRENT,
            )

            return NarrationStartResult(
                False,
                "Voice-Run gehoert nicht zum wirksamen Script Lock.",
                error_code=NARRATION_VOICE_NOT_CURRENT,
            )
        if pause_plan.script_lock_id != lock_input.lock.lock_id:
            from otio_app.discovery_v2.domain.script_lock_current_state import (
                NARRATION_PAUSE_PLAN_NOT_CURRENT,
            )

            return NarrationStartResult(
                False,
                "Pause-Plan gehoert nicht zum wirksamen Script Lock.",
                error_code=NARRATION_PAUSE_PLAN_NOT_CURRENT,
            )
        timebase = timebase_from_fps(float(project.fps or 25.0))
        fingerprint = timing_input_fingerprint(
            script_lock_id=lock_input.lock.lock_id,
            voice_run_id=state.current_voice_run_id,
            pause_plan_id=state.current_pause_plan_id,
            timebase=timebase,
        )
        run = VoiceGenerationRun(
            run_id=repo.new_voice_run_id(),
            project_id=project.id,
            script_lock_id=lock_input.lock.lock_id,
            script_id=lock_input.script.script_id,
            voice_profile_id=voice_run.voice_profile_id,
            input_fingerprint=fingerprint,
            provider="fake",
            adapter_version=TIMING_PROFILE_VERSION,
            scope=NARRATION_RUN_SCOPE_TIMING,
            status=NarrationRunStatus.QUEUED,
            sentence_count=len(lock_input.sentences),
            created_at=_now(),
        )
        repo.insert_voice_run(conn, run)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise NarrationServiceError(str(exc)) from exc
    finally:
        conn.close()
    if sync:
        result = resolve_narration_timing(project, run_id=run.run_id)
        return NarrationStartResult(
            started=result.ok,
            message=result.message,
            run=result.run,
            error_code=result.error_code,
        )
    launched = get_narration_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="narration_timing",
        sync=False,
    )
    if not launched:
        return NarrationStartResult(
            False,
            "Narration-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=NARRATION_ERROR_RUN_ALREADY_ACTIVE,
        )
    return NarrationStartResult(True, "Narration Timing gestartet.", run=run)


def resolve_narration_timing(project: Project, *, run_id: str | None = None) -> TimingResolveResult:
    project = require_discovery_project(project)
    conn = repo.open_narration_registry(project.project_root_path)
    run = None
    try:
        if run_id is not None:
            run = repo.get_voice_run(conn, run_id=run_id)
            if run is not None:
                run = run.model_copy(
                    update={"status": NarrationRunStatus.RUNNING, "started_at": run.started_at or _now()}
                )
                repo.update_voice_run(conn, run)
                conn.commit()
        lock_input = require_effective_lock_for_narration(project)
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_voice_run_id is None or state.current_pause_plan_id is None:
            raise NarrationServiceError(NARRATION_ERROR_INPUT_STALE)
        voice_run = repo.get_voice_run(conn, run_id=state.current_voice_run_id)
        pause_plan = repo.get_pause_plan(conn, pause_plan_id=state.current_pause_plan_id)
        if voice_run is None or pause_plan is None:
            raise NarrationServiceError(NARRATION_ERROR_INPUT_STALE)
        segments = repo.list_voice_segments_for_run(conn, run_id=voice_run.run_id)
        if len(segments) < len(lock_input.sentences):
            raise NarrationServiceError(NARRATION_ERROR_VOICE_SEGMENT_MISSING)
        directions = repo.list_pause_directions(conn, pause_plan_id=pause_plan.pause_plan_id)
        timebase = timebase_from_fps(float(project.fps or 25.0))
        timeline = _resolve_timeline(
            project_id=project.id,
            script_lock_id=lock_input.lock.lock_id,
            voice_run_id=voice_run.run_id,
            pause_plan_id=pause_plan.pause_plan_id,
            segments=segments,
            directions=directions,
            timebase=timebase,
            created_at=pause_plan.created_at,
        )
        relative = repo.save_timeline_json(project.project_root_path, timeline)
        conn.execute("BEGIN IMMEDIATE")
        repo.insert_timeline(conn, timeline, relative)
        repo.mark_current_timeline(
            conn,
            project_id=project.id,
            script_lock_id=lock_input.lock.lock_id,
            timeline_id=timeline.timeline_id,
        )
        repo.write_latest_timeline_pointer(project.project_root_path, timeline)
        if run is not None:
            run = run.model_copy(
                update={"status": NarrationRunStatus.COMPLETED, "finished_at": _now()}
            )
            repo.update_voice_run(conn, run)
        conn.commit()
        return TimingResolveResult(True, "Narration Timing abgeschlossen.", run=run, timeline=timeline)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        code = getattr(exc, "code", str(exc) or NARRATION_ERROR_TIMING_RESOLUTION_FAILED)
        if run is not None:
            run = run.model_copy(
                update={
                    "status": NarrationRunStatus.FAILED,
                    "error_code": code,
                    "error_message": "Timing resolution failed.",
                    "finished_at": _now(),
                }
            )
            repo.update_voice_run(conn, run)
            conn.commit()
        return TimingResolveResult(False, "Narration Timing fehlgeschlagen.", run=run, error_code=code)
    finally:
        conn.close()


def process_narration_timing_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    run = None
    conn = repo.open_narration_registry(root)
    try:
        run = repo.get_voice_run(conn, run_id=run_id)
    finally:
        conn.close()
    if run is None:
        return
    project = _project_stub(root, run.project_id)
    resolve_narration_timing(project, run_id=run_id)


def _resolve_timeline(
    *,
    project_id: str,
    script_lock_id: str,
    voice_run_id: str,
    pause_plan_id: str,
    segments: list[VoiceSegment],
    directions: list[PauseDirection],
    timebase,
    created_at: datetime,
) -> ResolvedNarrationTimeline:
    ordered_segments = sorted(segments, key=lambda item: (item.sentence_ordinal, item.segment_id))
    selected_directions = _select_non_conflicting_directions(directions)
    input_fingerprint = timing_input_fingerprint(
        script_lock_id=script_lock_id,
        voice_run_id=voice_run_id,
        pause_plan_id=pause_plan_id,
        timebase=timebase,
    )
    timeline_id = str(
        uuid5(
            NAMESPACE_URL,
            f"otio-discovery-v2-narration-timeline:{input_fingerprint}",
        )
    )
    speech_duration = sum(segment.duration_seconds for segment in ordered_segments)
    max_pause_total = speech_duration * MAX_PAUSE_RATIO
    pause_duration_total = sum(
        clamp_pause_duration(direction)
        for direction in selected_directions
        if direction.function != PauseFunction.NO_PAUSE
    )
    if pause_duration_total > max_pause_total + 1e-9:
        raise NarrationServiceError(NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT)

    before: dict[int, list[PauseDirection]] = {}
    after: dict[int, list[PauseDirection]] = {}
    timeline_start: list[PauseDirection] = []
    timeline_end: list[PauseDirection] = []
    for direction in selected_directions:
        if direction.position_kind == PausePositionKind.TIMELINE_START:
            timeline_start.append(direction)
        elif direction.position_kind == PausePositionKind.TIMELINE_END:
            timeline_end.append(direction)
        elif direction.position_kind == PausePositionKind.BEFORE_SENTENCE:
            before.setdefault(int(direction.anchor_ordinal or 0), []).append(direction)
        elif direction.position_kind == PausePositionKind.BETWEEN_SENTENCES:
            before.setdefault(int(direction.anchor_ordinal or 0), []).append(direction)
        elif direction.position_kind == PausePositionKind.AFTER_SENTENCE:
            after.setdefault(int(direction.anchor_ordinal or 0), []).append(direction)

    entries: list[NarrationTimelineEntry] = []
    cursor = 0.0
    previous_end_frame = 0

    def add_entry(
        entry_type: NarrationTimelineEntryType,
        duration: float,
        function: str,
        *,
        sentence_id: str | None = None,
        segment_id: str | None = None,
        direction_id: str | None = None,
        notes: list[str] | None = None,
    ) -> None:
        nonlocal cursor, previous_end_frame
        if duration <= 0:
            return
        start = cursor
        end = cursor + duration
        start_frame = previous_end_frame
        end_frame = max(start_frame + 1, seconds_to_frame_floor(end, timebase))
        ordinal = len(entries)
        entries.append(
            NarrationTimelineEntry(
                entry_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        ":".join(
                            [
                                "otio-discovery-v2-narration-timeline-entry",
                                timeline_id,
                                str(ordinal),
                                entry_type.value,
                                sentence_id or "",
                                segment_id or "",
                                direction_id or "",
                                function,
                            ]
                        ),
                    )
                ),
                ordinal=ordinal,
                entry_type=entry_type,
                sentence_id=sentence_id,
                voice_segment_id=segment_id,
                pause_direction_id=direction_id,
                start_seconds=start,
                end_seconds=end,
                duration_seconds=duration,
                start_frame=start_frame,
                end_frame=end_frame,
                function=function,
                technical_notes=list(notes or []),
            )
        )
        cursor = end
        previous_end_frame = end_frame

    for direction in timeline_start:
        _add_pause_entry(add_entry, direction)
    for segment in ordered_segments:
        for direction in before.get(segment.sentence_ordinal, []):
            _add_pause_entry(add_entry, direction)
        add_entry(
            NarrationTimelineEntryType.VOICE,
            segment.duration_seconds,
            "speech",
            sentence_id=segment.sentence_id,
            segment_id=segment.segment_id,
            notes=["voice_segment_duration_from_wav"],
        )
        for direction in after.get(segment.sentence_ordinal, []):
            _add_pause_entry(add_entry, direction)
    for direction in timeline_end:
        _add_pause_entry(add_entry, direction)

    if not entries:
        raise NarrationServiceError(NARRATION_ERROR_INVALID_TIMELINE)
    return ResolvedNarrationTimeline(
        timeline_id=timeline_id,
        project_id=project_id,
        script_lock_id=script_lock_id,
        voice_run_id=voice_run_id,
        pause_plan_id=pause_plan_id,
        timing_profile_version=TIMING_PROFILE_VERSION,
        timebase=timebase,
        total_duration_seconds=entries[-1].end_seconds,
        total_frames=entries[-1].end_frame,
        entries=entries,
        input_fingerprint=input_fingerprint,
        status=NarrationTimelineStatus.COMPLETED,
        created_at=created_at,
    )


def _add_pause_entry(add_entry, direction: PauseDirection) -> None:
    if direction.function == PauseFunction.NO_PAUSE:
        return
    maximum = pause_max_for_function(direction.function)
    if direction.hardness == PauseHardness.HARD and direction.max_duration_intent_s > maximum:
        raise NarrationServiceError(NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT)
    duration = clamp_pause_duration(direction)
    entry_type = (
        NarrationTimelineEntryType.VISUAL_ONLY
        if direction.function == PauseFunction.VISUAL_BREATH
        else NarrationTimelineEntryType.PAUSE
    )
    add_entry(
        entry_type,
        duration,
        direction.function.value,
        sentence_id=direction.sentence_id,
        segment_id=None,
        direction_id=direction.direction_id,
        notes=[f"pause_hardness={direction.hardness.value}"],
    )


def _select_non_conflicting_directions(directions: list[PauseDirection]) -> list[PauseDirection]:
    by_position: dict[tuple[object, ...], PauseDirection] = {}
    for direction in directions:
        key = (
            direction.position_kind.value,
            direction.sentence_id,
            direction.segment_id,
            direction.anchor_ordinal,
        )
        existing = by_position.get(key)
        if existing is None:
            by_position[key] = direction
            continue
        if existing.hardness == PauseHardness.HARD and direction.hardness == PauseHardness.HARD:
            raise NarrationServiceError(NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT)
        if existing.function != direction.function and (
            existing.hardness == PauseHardness.HARD or direction.hardness == PauseHardness.HARD
        ):
            by_position[key] = existing if existing.hardness == PauseHardness.HARD else direction
            continue
        if existing.function != direction.function:
            raise NarrationServiceError(NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT)
        if direction.hardness == PauseHardness.HARD:
            by_position[key] = direction
    return list(by_position.values())


def _project_stub(root: Path, project_id: str) -> Project:
    from otio_app.models import ProjectMode, ProjectStatus

    return Project(
        id=project_id,
        name="Narration timing worker project",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.DISCOVERY_V2,
        language="de",
        fps=25.0,
        status=ProjectStatus.DRAFT,
        asset_subdir_names=[],
        selected_asset_subdirs=[],
    )


__all__ = [
    "TimingResolveResult",
    "process_narration_timing_run",
    "resolve_narration_timing",
    "start_narration_timing_run",
]
