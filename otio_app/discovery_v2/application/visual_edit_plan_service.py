"""Visual edit plan generation service for Discovery V2 Phase 12."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway, TextGatewayError
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import get_visual_edit_job_launcher
from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.application.observation_review_service import list_editorial_ready_observations
from otio_app.discovery_v2.application.script_lock_service import get_effective_script_lock
from otio_app.discovery_v2.application.voice_generation_service import require_effective_lock_for_narration
from otio_app.discovery_v2.domain.editorial import (
    EditorialReadyObservationInput,
    TextGatewayRequest,
)
from otio_app.discovery_v2.domain.media_intake import WorkingMediaStatus
from otio_app.discovery_v2.domain.narration import NarrationTimelineStatus
from otio_app.discovery_v2.domain.visual_edit import (
    CLOSING_HOLD_MAX_SECONDS,
    MIN_SOURCE_HANDLE_SECONDS,
    PHOTO_SHOT_MAX_SECONDS,
    PHOTO_SHOT_MIN_SECONDS,
    PROMPT_VERSION_VISUAL_EDIT_PLAN,
    RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
    TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    TRANSITION_CUT_SECONDS,
    TRANSITION_MAX_SECONDS,
    TRANSITION_MIN_SECONDS,
    VIDEO_SHOT_MAX_SECONDS,
    VIDEO_SHOT_MIN_SECONDS,
    VISUAL_EDIT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_GATEWAY_UNCONFIGURED,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE,
    VISUAL_EDIT_ERROR_NARRATION_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_NARRATION_TIMELINE_MISSING,
    VISUAL_EDIT_ERROR_NARRATION_TIMELINE_STALE,
    VISUAL_EDIT_ERROR_RESPONSE_INVALID,
    VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_SCRIPT_LOCK_INVALIDATED,
    VISUAL_EDIT_ERROR_SCRIPT_LOCK_MISSING,
    VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS,
    VISUAL_EDIT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_MODEL_IDENTIFIER,
    VISUAL_EDIT_RUN_SCOPE_PLAN,
    EditorialShot,
    ShotMediaAssignment,
    ShotTransition,
    SourceRangeIntent,
    VisualEditInputGate,
    VisualEditPlan,
    VisualEditPlanBundle,
    VisualEditPlanGatewayPayload,
    VisualEditRun,
    VisualEditRunStatus,
    seconds_to_frame_nearest,
    visual_edit_input_fingerprint,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.persistence import editorial_repository
from otio_app.discovery_v2.persistence import narration_repository
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.discovery_v2.persistence.asset_analysis_repository import find_active_analysis_run
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence.editorial_repository import find_active_editorial_run
from otio_app.discovery_v2.persistence.supplementation_repository import find_active_supplementation_run
from otio_app.models import Project


class VisualEditServiceError(InventoryServiceError):
    """Domain error for visual edit operations."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class VisualEditStartResult:
    started: bool
    message: str
    run: VisualEditRun | None = None
    plan: VisualEditPlan | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class VisualEditView:
    ok: bool
    message: str | None = None
    state: object | None = None
    input_gate: VisualEditInputGate | None = None
    active_run: VisualEditRun | None = None
    runs: list[VisualEditRun] = field(default_factory=list)
    plans: list[VisualEditPlan] = field(default_factory=list)
    current_bundle: VisualEditPlanBundle | None = None
    humanity_review: object | None = None
    humanity_findings: list[object] = field(default_factory=list)
    feasibility_report: object | None = None
    feasibility_issues: list[object] = field(default_factory=list)
    repair_proposals: list[object] = field(default_factory=list)
    can_start_plan: bool = False
    can_start_humanity: bool = False
    can_start_feasibility: bool = False
    can_apply_repair: bool = False


@dataclass(frozen=True)
class VisualEditInputContext:
    lock_input: object
    timeline: object
    script_bundle: dict[str, object]
    observations: list[EditorialReadyObservationInput]
    working_media: list[object]
    technical_shots: list[object]
    fingerprint: str
    package: dict[str, object]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_visual_edit_plan_run(project: Project, *, sync: bool = True) -> VisualEditStartResult:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    run = None
    try:
        blocker = _active_blocker(conn, project_id=project.id)
        if blocker is not None:
            code, message = blocker
            return VisualEditStartResult(False, message, error_code=code)
        try:
            context = build_visual_edit_input_context(project, conn=conn)
        except VisualEditServiceError as exc:
            return VisualEditStartResult(False, str(exc), error_code=exc.code)
        run = VisualEditRun(
            run_id=repo.new_visual_edit_run_id(),
            project_id=project.id,
            scope=VISUAL_EDIT_RUN_SCOPE_PLAN,
            status=VisualEditRunStatus.QUEUED,
            script_lock_id=context.lock_input.lock.lock_id,
            narration_timeline_id=context.timeline.timeline_id,
            input_fingerprint=context.fingerprint,
            created_at=_now(),
        )
        repo.insert_visual_edit_run(conn, run)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise VisualEditServiceError(str(exc)) from exc
    finally:
        conn.close()
    if sync:
        result = process_visual_edit_plan(project, run_id=run.run_id)
        return VisualEditStartResult(
            started=result.started,
            message=result.message,
            run=result.run,
            plan=result.plan,
            error_code=result.error_code,
        )
    launched = get_visual_edit_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="visual_edit_plan",
        sync=False,
    )
    if not launched:
        return VisualEditStartResult(
            False,
            "Visual-Edit-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
        )
    return VisualEditStartResult(True, "Visual Edit Plan gestartet.", run=run)


def process_visual_edit_plan(project: Project, *, run_id: str | None = None) -> VisualEditStartResult:
    project = require_discovery_project(project)
    config = load_text_config()
    conn = repo.open_visual_edit_registry(project.project_root_path)
    run = None
    try:
        if run_id is not None:
            run = repo.get_visual_edit_run(conn, run_id=run_id)
            if run is not None:
                run = run.model_copy(
                    update={"status": VisualEditRunStatus.RUNNING, "started_at": run.started_at or _now()}
                )
                repo.update_visual_edit_run(conn, run)
                conn.commit()
        context = build_visual_edit_input_context(project, conn=conn)
        if run is not None and run.input_fingerprint != context.fingerprint:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        request = TextGatewayRequest(
            project_id=project.id,
            run_id=run.run_id if run else repo.new_visual_edit_run_id(),
            request_kind=TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
            prompt="visual_edit_plan",
            provider=config.provider,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            prompt_version=PROMPT_VERSION_VISUAL_EDIT_PLAN,
            response_schema_version=RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
            sentences=_sentence_models(context.script_bundle),
            visual_beats=_beat_models(context.script_bundle),
            visual_intents=_intent_models(context.script_bundle),
            observations=context.observations,
            candidate_asset_ids=[item.asset_id for item in context.observations],
            input_fingerprint=context.fingerprint,
            visual_edit_input=context.package,
        )
        response = DiscoveryTextGateway(config=config).generate(request)
        if response.visual_edit_plan is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_RESPONSE_INVALID)
        bundle = _build_bundle_from_gateway(
            project=project,
            context=context,
            payload=response.visual_edit_plan,
            gateway_version=response.gateway_version,
        )
        relative = repo.save_plan_json(project.project_root_path, bundle)
        conn.execute("BEGIN IMMEDIATE")
        _supersede_current(conn, project_id=project.id)
        repo.insert_plan_bundle(conn, bundle, relative)
        repo.mark_current_plan(
            conn,
            project_id=project.id,
            script_lock_id=bundle.plan.script_lock_id,
            narration_timeline_id=bundle.plan.narration_timeline_id,
            plan_id=bundle.plan.plan_id,
        )
        repo.write_latest_plan_pointer(project.project_root_path, bundle.plan)
        if run is not None:
            run = run.model_copy(
                update={
                    "status": VisualEditRunStatus.COMPLETED,
                    "finished_at": _now(),
                    "plan_id": bundle.plan.plan_id,
                }
            )
            repo.update_visual_edit_run(conn, run)
        conn.commit()
        return VisualEditStartResult(True, "Visual Edit Plan erzeugt.", run=run, plan=bundle.plan)
    except TextGatewayError as exc:
        conn.rollback()
        code = exc.code or VISUAL_EDIT_ERROR_GATEWAY_UNCONFIGURED
        run = _fail_run(conn, run, code=code)
        return VisualEditStartResult(False, "Visual Edit Plan fehlgeschlagen.", run=run, error_code=code)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        code = getattr(exc, "code", VISUAL_EDIT_ERROR_RESPONSE_INVALID)
        run = _fail_run(conn, run, code=code)
        return VisualEditStartResult(False, "Visual Edit Plan fehlgeschlagen.", run=run, error_code=code)
    finally:
        conn.close()


def process_visual_edit_plan_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_visual_edit_registry(root)
    try:
        run = repo.get_visual_edit_run(conn, run_id=run_id)
    finally:
        conn.close()
    if run is None:
        return
    project = _project_stub(root, run.project_id)
    process_visual_edit_plan(project, run_id=run_id)


def build_visual_edit_input_context(
    project: Project,
    *,
    conn,
    existing_plan: VisualEditPlan | None = None,
) -> VisualEditInputContext:
    lock_input = _require_effective_visual_lock(project)
    narration_state = narration_repository.get_project_state(conn, project_id=project.id)
    if narration_state is None or not narration_state.current_timeline_id:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_NARRATION_TIMELINE_MISSING)
    timeline = narration_repository.get_timeline(conn, timeline_id=narration_state.current_timeline_id)
    if timeline is None or timeline.status != NarrationTimelineStatus.COMPLETED:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_NARRATION_TIMELINE_MISSING)
    if timeline.script_lock_id != lock_input.lock.lock_id:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_NARRATION_TIMELINE_STALE)
    script_bundle = editorial_repository.get_script_bundle(conn, script_id=lock_input.script.script_id)
    if script_bundle is None:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
    observations = [
        EditorialReadyObservationInput(
            observation_id=item.observation_id,
            asset_id=item.asset_id,
            analysis_identity_id=item.analysis_identity_id,
            working_media_id=item.working_media_id,
            summary=item.summary,
            evidence_frame_ids=list(item.evidence_frame_ids),
            geographic_confidence=item.geographic_confidence,
            synthetic_confidence=item.synthetic_confidence,
            uncertainty_notes=list(item.uncertainty_notes),
            observation_sha256=item.observation_sha256,
            frame_set_fingerprint=item.frame_set_fingerprint,
        )
        for item in list_editorial_ready_observations(project)
    ]
    working = [
        item
        for item in copy_repo.list_working_media(conn, project_id=project.id)
        if item.status == WorkingMediaStatus.COMPLETED
    ]
    technical = analysis_repo.list_technical_shots_for_project(conn, project_id=project.id)
    fingerprint = visual_edit_input_fingerprint(
        script_lock_id=lock_input.lock.lock_id,
        lock_fingerprint=lock_input.lock.lock_fingerprint,
        narration_timeline_id=timeline.timeline_id,
        narration_timeline_fingerprint=timeline.input_fingerprint,
        observations=observations,
        working_media=working,
        technical_shots=technical,
        sentences=list(script_bundle.get("sentences", [])),
        visual_beats=list(script_bundle.get("visual_beats", [])),
        visual_intents=list(script_bundle.get("visual_intents", [])),
    )
    if existing_plan is not None and existing_plan.input_fingerprint != fingerprint:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
    package = _candidate_package(
        project_id=project.id,
        lock_input=lock_input,
        timeline=timeline,
        script_bundle=script_bundle,
        observations=observations,
        working_media=working,
        technical_shots=technical,
        fingerprint=fingerprint,
        next_plan_version=repo.next_plan_version(conn, project_id=project.id),
    )
    return VisualEditInputContext(
        lock_input=lock_input,
        timeline=timeline,
        script_bundle=script_bundle,
        observations=observations,
        working_media=working,
        technical_shots=technical,
        fingerprint=fingerprint,
        package=package,
    )


def get_visual_edit_view(project: Project) -> VisualEditView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return VisualEditView(ok=False, message=str(exc))
    try:
        conn = repo.open_visual_edit_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return VisualEditView(ok=False, message=str(exc))
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        active = repo.find_active_visual_edit_run(conn, project_id=project.id)
        runs = repo.list_visual_edit_runs(conn, project_id=project.id)
        plans = repo.list_plans(conn, project_id=project.id)
        current_bundle = (
            None
            if state is None or state.current_visual_edit_plan_id is None
            else repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        )
        humanity_bundle = (
            None
            if state is None or state.current_humanity_review_id is None
            else repo.get_humanity_review_bundle(conn, review_id=state.current_humanity_review_id)
        )
        feasibility_bundle = (
            None
            if state is None or state.current_feasibility_report_id is None
            else repo.get_feasibility_report_bundle(conn, report_id=state.current_feasibility_report_id)
        )
        proposals = (
            []
            if current_bundle is None
            else repo.list_repair_proposals(conn, plan_id=current_bundle.plan.plan_id)
        )
        gate = None
        try:
            context = build_visual_edit_input_context(project, conn=conn)
            gate = VisualEditInputGate(
                script_lock_id=context.lock_input.lock.lock_id,
                lock_fingerprint=context.lock_input.lock.lock_fingerprint,
                narration_timeline_id=context.timeline.timeline_id,
                input_fingerprint=context.fingerprint,
                total_duration_seconds=context.timeline.total_duration_seconds,
                total_frames=context.timeline.total_frames,
            )
        except VisualEditServiceError:
            gate = None
    finally:
        conn.close()
    plan = None if current_bundle is None else current_bundle.plan
    humanity_review = None if humanity_bundle is None else humanity_bundle.review
    humanity_findings = [] if humanity_bundle is None else humanity_bundle.findings
    feasibility_report = None if feasibility_bundle is None else feasibility_bundle.report
    feasibility_issues = [] if feasibility_bundle is None else feasibility_bundle.issues
    return VisualEditView(
        ok=True,
        state=state,
        input_gate=gate,
        active_run=active,
        runs=runs,
        plans=plans,
        current_bundle=current_bundle,
        humanity_review=humanity_review,
        humanity_findings=humanity_findings,
        feasibility_report=feasibility_report,
        feasibility_issues=feasibility_issues,
        repair_proposals=proposals,
        can_start_plan=gate is not None and active is None,
        can_start_humanity=plan is not None and active is None and plan.status in {"review_required", "repair_required"},
        can_start_feasibility=plan is not None and active is None and humanity_review is not None,
        can_apply_repair=bool(proposals) and active is None,
    )


def _require_effective_visual_lock(project: Project):
    result = get_effective_script_lock(project)
    if not result.ok or result.lock is None:
        code = result.error_code or VISUAL_EDIT_ERROR_SCRIPT_LOCK_MISSING
        if code == "script_lock_invalidated":
            code = VISUAL_EDIT_ERROR_SCRIPT_LOCK_INVALIDATED
        raise VisualEditServiceError(code)
    try:
        return require_effective_lock_for_narration(project)
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", VISUAL_EDIT_ERROR_INPUT_STALE)
        if code == "script_lock_missing":
            code = VISUAL_EDIT_ERROR_SCRIPT_LOCK_MISSING
        if code == "script_lock_invalidated":
            code = VISUAL_EDIT_ERROR_SCRIPT_LOCK_INVALIDATED
        raise VisualEditServiceError(code) from exc


def _active_blocker(conn, *, project_id: str) -> tuple[str, str] | None:
    if find_active_analysis_run(conn, project_id=project_id) is not None:
        return VISUAL_EDIT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE, "Analysis-Run ist aktiv."
    if find_active_editorial_run(conn, project_id=project_id) is not None:
        return VISUAL_EDIT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE, "Editorial-Run ist aktiv."
    if find_active_supplementation_run(conn, project_id=project_id) is not None:
        return VISUAL_EDIT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE, "Supplementation-Run ist aktiv."
    if narration_repository.find_active_narration_run(conn, project_id=project_id) is not None:
        return VISUAL_EDIT_ERROR_NARRATION_RUN_ALREADY_ACTIVE, "Narration-Run ist aktiv."
    if repo.find_active_visual_edit_run(conn, project_id=project_id) is not None:
        return VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE, "Visual-Edit-Run ist aktiv."
    return None


def _candidate_package(
    *,
    project_id: str,
    lock_input,
    timeline,
    script_bundle: dict[str, object],
    observations: list[EditorialReadyObservationInput],
    working_media: list[object],
    technical_shots: list[object],
    fingerprint: str,
    next_plan_version: int,
) -> dict[str, object]:
    working_by_id = {item.working_media_id: item for item in working_media}
    tech_by_working: dict[str, list[object]] = {}
    for shot in technical_shots:
        tech_by_working.setdefault(shot.working_media_id, []).append(shot)
    candidates = []
    for obs in observations:
        wm = working_by_id.get(obs.working_media_id)
        if wm is None:
            continue
        technical = sorted(tech_by_working.get(obs.working_media_id, []), key=lambda item: item.ordinal)
        duration = max((shot.end_seconds for shot in technical), default=0.0)
        candidates.append(
            {
                "asset_id": obs.asset_id,
                "working_media_id": obs.working_media_id,
                "analysis_identity_id": obs.analysis_identity_id,
                "observation_id": obs.observation_id,
                "media_kind": "video" if wm.media_kind == "video" else "image",
                "duration_seconds": duration,
                "technical_shots": [
                    {
                        "technical_shot_id": shot.shot_id,
                        "start_seconds": shot.start_seconds,
                        "end_seconds": shot.end_seconds,
                        "duration_seconds": shot.duration_seconds,
                    }
                    for shot in technical
                ],
                "observation_summary": obs.summary,
                "geographic_confidence": obs.geographic_confidence,
                "synthetic_confidence": obs.synthetic_confidence,
                "generic_stock_like": _generic_stock_like(obs),
                "motif_hash": _motif_hash(obs),
                "uncertainty_notes": list(obs.uncertainty_notes),
            }
        )
    return {
        "project_id": project_id,
        "script_lock_id": lock_input.lock.lock_id,
        "lock_fingerprint": lock_input.lock.lock_fingerprint,
        "narration_timeline_id": timeline.timeline_id,
        "narration_timeline": {
            "timeline_id": timeline.timeline_id,
            "timebase": timeline.timebase.model_dump(mode="json"),
            "total_duration_seconds": timeline.total_duration_seconds,
            "total_frames": timeline.total_frames,
            "entries": [entry.model_dump(mode="json") for entry in timeline.entries],
        },
        "sentences": list(script_bundle.get("sentences", [])),
        "visual_beats": list(script_bundle.get("visual_beats", [])),
        "visual_intents": list(script_bundle.get("visual_intents", [])),
        "candidates": candidates,
        "accepted_open_risks": list(getattr(lock_input.lock, "accepted_open_risks", [])),
        "input_fingerprint": fingerprint,
        "next_plan_version": next_plan_version,
        "limits": {
            "max_shots_per_minute_warning": 12,
            "max_shots_per_minute_blocking": 20,
            "video_shot_seconds": [0.80, 12.0],
            "photo_shot_seconds": [1.20, 6.0],
            "asset_reuse_max": 3,
        },
    }


def _build_bundle_from_gateway(
    *,
    project: Project,
    context: VisualEditInputContext,
    payload: VisualEditPlanGatewayPayload,
    gateway_version: str,
) -> VisualEditPlanBundle:
    fps = context.timeline.timebase.fps
    total = context.timeline.total_duration_seconds
    weights = [shot.duration_weight for shot in payload.shots]
    if not weights:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
    raw_durations = [total * weight / sum(weights) for weight in weights]
    durations = [_clamp_duration(value, payload.shots[idx].shot_function, payload.shots[idx].media_strategy) for idx, value in enumerate(raw_durations)]
    if sum(durations) <= 0:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
    scale = total / sum(durations)
    durations = [max(0.1, duration * scale) for duration in durations]
    plan = VisualEditPlan(
        plan_id=payload.plan_id,
        project_id=project.id,
        script_lock_id=context.lock_input.lock.lock_id,
        narration_timeline_id=context.timeline.timeline_id,
        input_fingerprint=context.fingerprint,
        plan_version=payload.plan_version,
        gateway_version=gateway_version,
        model_id=payload.model_id,
        prompt_version=payload.prompt_version,
        status="review_required",
        total_shot_count=len(payload.shots),
        expected_visual_duration_seconds=total,
        accepted_risks=payload.accepted_risks,
        created_at=payload.created_at,
    )
    candidates = _candidates_by_key(context.package)
    shots: list[EditorialShot] = []
    assignments: list[ShotMediaAssignment] = []
    cursor = 0.0
    previous_frame = 0
    for idx, intent in enumerate(payload.shots):
        start = cursor
        end = total if idx == len(payload.shots) - 1 else min(total, cursor + durations[idx])
        start_frame = previous_frame
        end_frame = context.timeline.total_frames if idx == len(payload.shots) - 1 else max(start_frame + 1, seconds_to_frame_nearest(end, fps))
        end = end_frame / fps
        duration = end - start
        if duration <= 0:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE)
        shot = EditorialShot(
            shot_id=intent.shot_id,
            plan_id=plan.plan_id,
            ordinal=idx,
            shot_function=intent.shot_function,
            narration_entry_ids=list(intent.narration_entry_ids),
            sentence_ids=list(intent.sentence_ids),
            visual_beat_ids=list(intent.visual_beat_ids),
            visual_intent_ids=list(intent.visual_intent_ids),
            timeline_start_seconds=start,
            timeline_end_seconds=end,
            duration_seconds=duration,
            timeline_start_frame=start_frame,
            timeline_end_frame=end_frame,
            transition_intent=intent.transition_intent,
            continuity_intent=intent.continuity_intent,
            rhythm_intent=intent.rhythm_intent,
            media_strategy=intent.media_strategy,
            priority=intent.priority,
            uncertainty_notes=list(intent.uncertainty_notes),
            status="assigned" if intent.media_strategy in {"local_video", "local_photo"} else "planned",
        )
        shots.append(shot)
        if intent.media_strategy in {"local_video", "local_photo"}:
            assignments.append(_resolve_assignment(intent, shot, candidates, fps))
        cursor = end
        previous_frame = end_frame
    transitions = [
        _resolve_transition(item, shots_by_id={shot.shot_id: shot for shot in shots})
        for item in payload.transitions
    ]
    return VisualEditPlanBundle(plan=plan, shots=shots, assignments=assignments, transitions=transitions)


def _resolve_assignment(intent, shot: EditorialShot, candidates: dict[str, dict[str, object]], fps: float) -> ShotMediaAssignment:
    key = f"{intent.candidate_asset_id}:{intent.candidate_working_media_id}:{intent.candidate_observation_id}"
    candidate = candidates.get(key)
    if candidate is None:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
    source_intent = intent.source_range_intent
    if shot.media_strategy == "local_photo":
        duration = min(PHOTO_SHOT_MAX_SECONDS, max(PHOTO_SHOT_MIN_SECONDS, shot.duration_seconds))
        return ShotMediaAssignment(
            assignment_id=str(uuid5(NAMESPACE_URL, f"visual-edit-assignment:{shot.shot_id}:0")),
            shot_id=shot.shot_id,
            asset_id=str(candidate["asset_id"]),
            working_media_id=str(candidate["working_media_id"]),
            technical_shot_id=None,
            visual_observation_id=str(candidate["observation_id"]),
            assignment_priority=0,
            source_range_intent=source_intent,
            duration_seconds=duration,
            selection_rationale=intent.selection_rationale,
            status="resolved",
        )
    tech = _select_technical_shot(intent, candidate)
    desired = min(VIDEO_SHOT_MAX_SECONDS, max(VIDEO_SHOT_MIN_SECONDS, shot.duration_seconds))
    start, end, notes = _resolve_video_range(tech, desired, source_intent.start_bias)
    in_frame = seconds_to_frame_nearest(start, fps)
    out_frame = max(in_frame + 1, seconds_to_frame_nearest(end, fps))
    if notes and "technical_short_handles_zero" not in shot.uncertainty_notes:
        shot.uncertainty_notes.extend(notes)
    return ShotMediaAssignment(
        assignment_id=str(uuid5(NAMESPACE_URL, f"visual-edit-assignment:{shot.shot_id}:0")),
        shot_id=shot.shot_id,
        asset_id=str(candidate["asset_id"]),
        working_media_id=str(candidate["working_media_id"]),
        technical_shot_id=str(tech["technical_shot_id"]),
        visual_observation_id=str(candidate["observation_id"]),
        assignment_priority=0,
        source_range_intent=source_intent,
        technical_source_in_seconds=start,
        technical_source_out_seconds=end,
        technical_source_in_frame=in_frame,
        technical_source_out_frame=out_frame,
        duration_seconds=(out_frame - in_frame) / fps,
        selection_rationale=intent.selection_rationale,
        status="resolved",
    )


def _resolve_video_range(tech: dict[str, object], desired: float, bias: str) -> tuple[float, float, list[str]]:
    start = float(tech["start_seconds"])
    end = float(tech["end_seconds"])
    available = end - start
    notes: list[str] = []
    if available <= 0:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS)
    handle = 0.0 if available < desired + (2 * MIN_SOURCE_HANDLE_SECONDS) else MIN_SOURCE_HANDLE_SECONDS
    if handle == 0.0:
        notes.append("technical_short_handles_zero")
    inner_start = start + handle
    inner_end = end - handle
    duration = min(desired, max(0.05, inner_end - inner_start))
    if bias == "beginning":
        source_in = inner_start
    elif bias == "end":
        source_in = inner_end - duration
    else:
        source_in = inner_start + max(0.0, (inner_end - inner_start - duration) / 2.0)
    source_out = min(end, source_in + duration)
    source_in = max(start, min(source_in, source_out - 0.05))
    if source_out > end + 1e-6 or source_in < start - 1e-6 or source_out <= source_in:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS)
    return round(source_in, 6), round(source_out, 6), notes


def _select_technical_shot(intent, candidate: dict[str, object]) -> dict[str, object]:
    shots = candidate.get("technical_shots", [])
    shots = shots if isinstance(shots, list) else []
    if not shots:
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS)
    wanted = intent.candidate_technical_shot_id
    for shot in shots:
        if isinstance(shot, dict) and shot.get("technical_shot_id") == wanted:
            return shot
    first = shots[0]
    if not isinstance(first, dict):
        raise VisualEditServiceError(VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS)
    return first


def _resolve_transition(intent, *, shots_by_id: dict[str, EditorialShot]) -> ShotTransition:
    left = shots_by_id[intent.from_shot_id]
    right = shots_by_id[intent.to_shot_id]
    if intent.technical_type == "cut":
        resolved = TRANSITION_CUT_SECONDS
    elif intent.technical_type in {"dissolve", "fade"}:
        resolved = min(
            max(TRANSITION_MIN_SECONDS, intent.desired_duration_seconds),
            TRANSITION_MAX_SECONDS,
            left.duration_seconds,
            right.duration_seconds,
        )
    else:
        resolved = 0.0
    return ShotTransition(
        transition_id=intent.transition_id,
        plan_id=left.plan_id,
        from_shot_id=left.shot_id,
        to_shot_id=right.shot_id,
        editorial_function=intent.editorial_function,
        technical_type=intent.technical_type,
        desired_duration_seconds=intent.desired_duration_seconds,
        resolved_duration_seconds=resolved,
        status="resolved",
    )


def _clamp_duration(value: float, shot_function: str, strategy: str) -> float:
    if strategy == "local_photo":
        return min(PHOTO_SHOT_MAX_SECONDS, max(PHOTO_SHOT_MIN_SECONDS, value))
    maximum = CLOSING_HOLD_MAX_SECONDS if shot_function in {"closing", "hold"} else VIDEO_SHOT_MAX_SECONDS
    return min(maximum, max(VIDEO_SHOT_MIN_SECONDS, value))


def _candidates_by_key(package: dict[str, object]) -> dict[str, dict[str, object]]:
    result = {}
    for candidate in package.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        key = f"{candidate.get('asset_id')}:{candidate.get('working_media_id')}:{candidate.get('observation_id')}"
        result[key] = candidate
    return result


def _generic_stock_like(obs: EditorialReadyObservationInput) -> bool:
    text = " ".join([obs.summary, *obs.uncertainty_notes]).lower()
    return "generic" in text or "low_local_detail" in text or "stock_like" in text


def _motif_hash(obs: EditorialReadyObservationInput) -> str:
    return str(uuid5(NAMESPACE_URL, f"visual-edit-motif:{obs.summary[:80].lower()}"))


def _sentence_models(bundle: dict[str, object]):
    from otio_app.discovery_v2.domain.editorial import Sentence

    return [Sentence.model_validate(item) for item in bundle.get("sentences", [])]


def _beat_models(bundle: dict[str, object]):
    from otio_app.discovery_v2.domain.editorial import VisualBeat

    return [VisualBeat.model_validate(item) for item in bundle.get("visual_beats", [])]


def _intent_models(bundle: dict[str, object]):
    from otio_app.discovery_v2.domain.editorial import VisualIntent

    return [VisualIntent.model_validate(item) for item in bundle.get("visual_intents", [])]


def _supersede_current(conn, *, project_id: str) -> None:
    state = repo.get_project_state(conn, project_id=project_id)
    if state is not None and state.current_visual_edit_plan_id:
        repo.update_plan_status(conn, plan_id=state.current_visual_edit_plan_id, status="superseded")


def _fail_run(conn, run: VisualEditRun | None, *, code: str) -> VisualEditRun | None:
    if run is None:
        return None
    failed = run.model_copy(
        update={
            "status": VisualEditRunStatus.FAILED,
            "error_code": code,
            "error_message": "Visual edit worker failed.",
            "finished_at": _now(),
        }
    )
    repo.update_visual_edit_run(conn, failed)
    conn.commit()
    return failed


def _project_stub(root: Path, project_id: str) -> Project:
    from otio_app.models import ProjectMode, ProjectStatus

    return Project(
        id=project_id,
        name="Visual edit worker project",
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
    "VisualEditInputContext",
    "VisualEditServiceError",
    "VisualEditStartResult",
    "VisualEditView",
    "build_visual_edit_input_context",
    "get_visual_edit_view",
    "process_visual_edit_plan",
    "process_visual_edit_plan_run",
    "start_visual_edit_plan_run",
    "_active_blocker",
]
