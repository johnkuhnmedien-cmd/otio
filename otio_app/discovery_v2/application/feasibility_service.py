"""Deterministic technical feasibility service for Discovery V2 Phase 12."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.visual_edit_job_launcher import get_visual_edit_job_launcher
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    VisualEditServiceError,
    _active_blocker,
    build_visual_edit_input_context,
)
from otio_app.discovery_v2.domain.visual_edit import (
    ASSET_REUSE_MAX,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    TIMELINE_DURATION_TOLERANCE_FRAMES,
    TRANSITION_MAX_SECONDS,
    TRANSITION_MIN_SECONDS,
    VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE,
    VISUAL_EDIT_ERROR_FEASIBILITY_CHECK_FAILED,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_INVALID_ASSET_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_OBSERVATION_REFERENCE,
    VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE,
    VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE,
    VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE,
    VISUAL_EDIT_ERROR_PLANNED_GRAPHIC_NOT_EXPORTABLE,
    VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS,
    VISUAL_EDIT_RUN_SCOPE_FEASIBILITY,
    FeasibilityIssue,
    FeasibilityReport,
    FeasibilityReportBundle,
    RepairProposal,
    VisualEditPlan,
    VisualEditPlanBundle,
    VisualEditRun,
    VisualEditRunStatus,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class FeasibilityResult:
    ok: bool
    message: str
    run: VisualEditRun | None = None
    report: FeasibilityReport | None = None
    ready: bool = False
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_feasibility_check_run(project: Project, *, sync: bool = True) -> FeasibilityResult:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    run = None
    try:
        blocker = _active_blocker(conn, project_id=project.id)
        if blocker is not None:
            code, message = blocker
            return FeasibilityResult(False, message, error_code=code)
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return FeasibilityResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        plan = repo.get_plan(conn, plan_id=state.current_visual_edit_plan_id)
        if plan is None:
            return FeasibilityResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=plan)
        run = VisualEditRun(
            run_id=repo.new_visual_edit_run_id(),
            project_id=project.id,
            scope=VISUAL_EDIT_RUN_SCOPE_FEASIBILITY,
            status=VisualEditRunStatus.QUEUED,
            script_lock_id=plan.script_lock_id,
            narration_timeline_id=plan.narration_timeline_id,
            plan_id=plan.plan_id,
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
        return process_feasibility_check(project, run_id=run.run_id)
    launched = get_visual_edit_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="feasibility_check",
        sync=False,
    )
    if not launched:
        return FeasibilityResult(
            False,
            "Visual-Edit-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
        )
    return FeasibilityResult(True, "Feasibility gestartet.", run=run)


def process_feasibility_check(project: Project, *, run_id: str | None = None) -> FeasibilityResult:
    project = require_discovery_project(project)
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
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        if run is not None and run.input_fingerprint != context.fingerprint:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        report_bundle = evaluate_feasibility(bundle, context.package)
        relative = repo.save_feasibility_report_json(project.project_root_path, report_bundle)
        conn.execute("BEGIN IMMEDIATE")
        if state.current_feasibility_report_id:
            repo.update_feasibility_report_status(
                conn,
                report_id=state.current_feasibility_report_id,
                status="superseded",
            )
        repo.insert_feasibility_report_bundle(conn, report_bundle, relative)
        repo.mark_current_feasibility_report(
            conn,
            project_id=project.id,
            report_id=report_bundle.report.report_id,
        )
        repo.write_latest_feasibility_pointer(project.project_root_path, report_bundle.report)
        _insert_deterministic_repair_proposals(conn, bundle.plan, report_bundle)
        if run is not None:
            run = run.model_copy(
                update={"status": VisualEditRunStatus.COMPLETED, "finished_at": _now()}
            )
            repo.update_visual_edit_run(conn, run)
        ready = evaluate_ready_for_editorial_review(conn, project_id=project.id)
        conn.commit()
        return FeasibilityResult(
            ok=report_bundle.report.overall_technical_assessment != "fail",
            message="Feasibility abgeschlossen.",
            run=run,
            report=report_bundle.report,
            ready=ready,
            error_code=(
                VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE
                if report_bundle.report.overall_technical_assessment == "fail"
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        code = getattr(exc, "code", VISUAL_EDIT_ERROR_FEASIBILITY_CHECK_FAILED)
        run = _fail_run(conn, run, code)
        return FeasibilityResult(False, "Feasibility fehlgeschlagen.", run=run, error_code=code)
    finally:
        conn.close()


def process_feasibility_check_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_visual_edit_registry(root)
    try:
        run = repo.get_visual_edit_run(conn, run_id=run_id)
    finally:
        conn.close()
    if run is None:
        return
    from otio_app.discovery_v2.application.visual_edit_plan_service import _project_stub

    process_feasibility_check(_project_stub(root, run.project_id), run_id=run_id)


def evaluate_feasibility(bundle: VisualEditPlanBundle, package: dict[str, object]) -> FeasibilityReportBundle:
    report_id = repo.new_feasibility_report_id()
    issues: list[FeasibilityIssue] = []
    shot_by_id = {shot.shot_id: shot for shot in bundle.shots}
    assignment_by_shot = {assignment.shot_id: assignment for assignment in bundle.assignments}
    timeline = package.get("narration_timeline", {})
    total_frames = int(timeline.get("total_frames", 0)) if isinstance(timeline, dict) else 0
    fps = float(timeline.get("timebase", {}).get("fps", 25.0)) if isinstance(timeline, dict) else 25.0
    if not bundle.shots or bundle.shots[0].timeline_start_frame != 0:
        issues.append(_issue(report_id, None, None, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Shot timeline does not start at frame 0."))
    previous_end = 0
    for shot in bundle.shots:
        if shot.duration_seconds <= 0 or shot.timeline_end_frame <= shot.timeline_start_frame:
            issues.append(_issue(report_id, shot.shot_id, None, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Shot has invalid duration."))
        if shot.timeline_start_frame != previous_end:
            issues.append(_issue(report_id, shot.shot_id, None, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Shot timeline has a gap or overlap."))
        previous_end = shot.timeline_end_frame
        if shot.media_strategy == "planned_graphic":
            issues.append(_issue(report_id, shot.shot_id, None, VISUAL_EDIT_ERROR_PLANNED_GRAPHIC_NOT_EXPORTABLE, "blocking", "Planned graphic has no working media."))
        if shot.media_strategy in {"local_video", "local_photo"} and shot.shot_id not in assignment_by_shot:
            issues.append(_issue(report_id, shot.shot_id, None, VISUAL_EDIT_ERROR_INVALID_ASSET_REFERENCE, "blocking", "Local shot is missing a media assignment."))
    if abs(previous_end - total_frames) > TIMELINE_DURATION_TOLERANCE_FRAMES:
        issues.append(_issue(report_id, None, None, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Shot timeline does not match narration duration."))
    allowed_working = {
        str(item.get("working_media_id"))
        for item in package.get("candidates", [])
        if isinstance(item, dict) and item.get("working_media_id")
    }
    allowed_assets = {
        str(item.get("asset_id"))
        for item in package.get("candidates", [])
        if isinstance(item, dict) and item.get("asset_id")
    }
    allowed_observations = {
        str(item.get("observation_id"))
        for item in package.get("candidates", [])
        if isinstance(item, dict) and item.get("observation_id")
    }
    for assignment in bundle.assignments:
        shot = shot_by_id.get(assignment.shot_id)
        if shot is None:
            issues.append(_issue(report_id, None, assignment.assignment_id, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Assignment references unknown shot."))
            continue
        if assignment.working_media_id not in allowed_working:
            issues.append(
                _issue(
                    report_id,
                    shot.shot_id,
                    assignment.assignment_id,
                    VISUAL_EDIT_ERROR_INVALID_WORKING_MEDIA_REFERENCE,
                    "blocking",
                    "Assignment working media is not current completed working media.",
                )
            )
        if assignment.asset_id not in allowed_assets:
            issues.append(
                _issue(
                    report_id,
                    shot.shot_id,
                    assignment.assignment_id,
                    VISUAL_EDIT_ERROR_INVALID_ASSET_REFERENCE,
                    "blocking",
                    "Assignment asset is not a current project candidate.",
                )
            )
        if assignment.visual_observation_id not in allowed_observations:
            issues.append(
                _issue(
                    report_id,
                    shot.shot_id,
                    assignment.assignment_id,
                    VISUAL_EDIT_ERROR_INVALID_OBSERVATION_REFERENCE,
                    "blocking",
                    "Assignment observation is not currently accepted.",
                )
            )
        if shot.media_strategy == "local_video":
            if assignment.technical_source_in_seconds is None or assignment.technical_source_out_seconds is None:
                issues.append(_issue(report_id, shot.shot_id, assignment.assignment_id, VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE, "blocking", "Video assignment lacks source range."))
            elif assignment.technical_source_out_seconds <= assignment.technical_source_in_seconds:
                issues.append(_issue(report_id, shot.shot_id, assignment.assignment_id, VISUAL_EDIT_ERROR_SOURCE_RANGE_OUT_OF_BOUNDS, "blocking", "Video assignment has invalid source range."))
        if shot.media_strategy == "local_photo" and assignment.technical_source_in_seconds is not None:
            issues.append(_issue(report_id, shot.shot_id, assignment.assignment_id, VISUAL_EDIT_ERROR_INVALID_SOURCE_RANGE, "blocking", "Photo assignment must not have a video source range."))
    asset_counts: dict[str, int] = {}
    for assignment in bundle.assignments:
        if assignment.asset_id:
            asset_counts[assignment.asset_id] = asset_counts.get(assignment.asset_id, 0) + 1
    for asset_id, count in asset_counts.items():
        if count > ASSET_REUSE_MAX:
            issues.append(_issue(report_id, None, None, VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE, "blocking", f"Asset reuse exceeds E3 for {asset_id}."))
    for left, right in _video_range_pairs(bundle.assignments):
        overlap = _overlap_ratio(left, right)
        if overlap >= SOURCE_RANGE_OVERLAP_RATIO_MAX:
            issues.append(
                _issue(
                    report_id,
                    right.shot_id,
                    right.assignment_id,
                    VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE,
                    "blocking",
                    f"Video source range reuse exceeds E4 for asset {right.asset_id}.",
                )
            )
    for transition in bundle.transitions:
        left = shot_by_id.get(transition.from_shot_id)
        right = shot_by_id.get(transition.to_shot_id)
        if left is None or right is None:
            issues.append(_issue(report_id, None, None, VISUAL_EDIT_ERROR_INVALID_SHOT_TIMELINE, "blocking", "Transition references unknown shot."))
            continue
        if transition.technical_type == "cut" and transition.resolved_duration_seconds != 0:
            issues.append(_issue(report_id, left.shot_id, None, VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE, "blocking", "Cut transition must be 0 seconds.", repairable=True))
        if transition.technical_type in {"dissolve", "fade"} and not (
            TRANSITION_MIN_SECONDS <= transition.resolved_duration_seconds <= min(TRANSITION_MAX_SECONDS, left.duration_seconds, right.duration_seconds)
        ):
            issues.append(_issue(report_id, left.shot_id, None, VISUAL_EDIT_ERROR_FEASIBILITY_BLOCKING_ISSUE, "blocking", "Transition duration is outside E6.", repairable=True))
    blocking = [issue for issue in issues if issue.severity == "blocking"]
    assessment = "fail" if blocking else ("pass_with_warnings" if issues else "pass")
    report = FeasibilityReport(
        report_id=report_id,
        plan_id=bundle.plan.plan_id,
        input_fingerprint=bundle.plan.input_fingerprint,
        timebase=f"{fps:g}",
        status="completed",
        overall_technical_assessment=assessment,
        metrics={
            "shot_count": len(bundle.shots),
            "assignment_count": len(bundle.assignments),
            "asset_reuse_counts": asset_counts,
            "issue_count": len(issues),
        },
        created_at=_now(),
    )
    return FeasibilityReportBundle(report=report, issues=issues)


def evaluate_ready_for_editorial_review(conn, *, project_id: str) -> bool:
    if repo.find_active_visual_edit_run(conn, project_id=project_id) is not None:
        return False
    state = repo.get_project_state(conn, project_id=project_id)
    if state is None or not state.current_visual_edit_plan_id:
        return False
    bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
    if bundle is None:
        return False
    if any(shot.media_strategy == "planned_graphic" for shot in bundle.shots):
        repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="repair_required")
        return False
    if not state.current_humanity_review_id or not state.current_feasibility_report_id:
        return False
    humanity = repo.get_humanity_review_bundle(conn, review_id=state.current_humanity_review_id)
    feasibility = repo.get_feasibility_report_bundle(conn, report_id=state.current_feasibility_report_id)
    if humanity is None or feasibility is None:
        return False
    if humanity.review.visual_edit_plan_id != bundle.plan.plan_id or humanity.review.status != "completed":
        return False
    if feasibility.report.plan_id != bundle.plan.plan_id or feasibility.report.status != "completed":
        return False
    open_blocking = [
        finding
        for finding in humanity.findings
        if finding.severity == "blocking" and finding.user_status == "open"
    ]
    if open_blocking:
        repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="repair_required")
        return False
    blocking_issues = [issue for issue in feasibility.issues if issue.severity == "blocking"]
    if feasibility.report.overall_technical_assessment == "fail" or blocking_issues:
        repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="repair_required")
        return False
    repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="ready_for_editorial_review")
    return True


def _insert_deterministic_repair_proposals(
    conn,
    plan: VisualEditPlan,
    report_bundle: FeasibilityReportBundle,
) -> None:
    for issue in report_bundle.issues:
        if not issue.deterministically_repairable:
            continue
        proposal = RepairProposal(
            proposal_id=repo.new_repair_proposal_id(),
            plan_id=plan.plan_id,
            feasibility_report_id=report_bundle.report.report_id,
            source="deterministic_python",
            repair_type="clamp_transition_or_range",
            affected_ids=[item for item in [issue.shot_id, issue.assignment_id] if item],
            description="Deterministic feasibility repair can clamp a technical value.",
            expected_effect="Removes the technical blocker without editorial substitution.",
            user_status="proposed",
            version=1,
        )
        repo.insert_repair_proposal(conn, proposal)


def _issue(
    report_id: str,
    shot_id: str | None,
    assignment_id: str | None,
    code: str,
    severity: str,
    details: str,
    *,
    repairable: bool = False,
) -> FeasibilityIssue:
    return FeasibilityIssue(
        issue_id=repo.new_feasibility_issue_id(),
        report_id=report_id,
        shot_id=shot_id,
        assignment_id=assignment_id,
        error_code=code,
        severity=severity,
        technical_details=details,
        deterministically_repairable=repairable,
        blocks_phase_13=severity == "blocking",
    )


def _video_range_pairs(assignments):
    ranges = [
        item
        for item in assignments
        if item.technical_source_in_seconds is not None and item.technical_source_out_seconds is not None
    ]
    for idx, left in enumerate(ranges):
        for right in ranges[idx + 1 :]:
            if left.asset_id == right.asset_id and left.working_media_id == right.working_media_id:
                yield left, right


def _overlap_ratio(left, right) -> float:
    left_start = float(left.technical_source_in_seconds)
    left_end = float(left.technical_source_out_seconds)
    right_start = float(right.technical_source_in_seconds)
    right_end = float(right.technical_source_out_seconds)
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    shortest = min(left_end - left_start, right_end - right_start)
    return 0.0 if shortest <= 0 else overlap / shortest


def _fail_run(conn, run: VisualEditRun | None, code: str) -> VisualEditRun | None:
    if run is None:
        return None
    failed = run.model_copy(
        update={
            "status": VisualEditRunStatus.FAILED,
            "error_code": code,
            "error_message": "Feasibility worker failed.",
            "finished_at": _now(),
        }
    )
    repo.update_visual_edit_run(conn, failed)
    conn.commit()
    return failed


__all__ = [
    "FeasibilityResult",
    "evaluate_feasibility",
    "evaluate_ready_for_editorial_review",
    "process_feasibility_check",
    "process_feasibility_check_run",
    "start_feasibility_check_run",
]
