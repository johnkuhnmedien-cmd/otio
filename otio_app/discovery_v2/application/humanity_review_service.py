"""Humanity and authenticity review service for Discovery V2 Phase 12."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway, TextGatewayError
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import get_visual_edit_job_launcher
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    VisualEditServiceError,
    _active_blocker,
    build_visual_edit_input_context,
)
from otio_app.discovery_v2.domain.editorial import TextGatewayRequest
from otio_app.discovery_v2.domain.visual_edit import (
    GENERIC_STOCK_BLOCKING_RATIO,
    GENERIC_STOCK_WARNING_RATIO,
    PROMPT_VERSION_HUMANITY_REVIEW,
    RESPONSE_SCHEMA_HUMANITY_REVIEW,
    SENTENCE_BOUNDARY_BLOCKING_RATIO,
    SENTENCE_BOUNDARY_WARNING_RATIO,
    SHOT_DURATION_VARIANCE_MIN_RATIO,
    SHOT_DURATION_VARIANCE_MIN_SHOTS,
    SIMILAR_MOTIF_BLOCKING_RUN,
    SIMILAR_MOTIF_WARNING_RUN,
    TEXT_REQUEST_KIND_HUMANITY_REVIEW,
    VISUAL_EDIT_ERROR_HUMANITY_REVIEW_INVALID,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_RESPONSE_INVALID,
    VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
    VISUAL_EDIT_RUN_SCOPE_HUMANITY,
    HumanityReview,
    HumanityReviewBundle,
    VisualEditRun,
    VisualEditRunStatus,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class HumanityReviewResult:
    ok: bool
    message: str
    run: VisualEditRun | None = None
    review: HumanityReview | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_humanity_review_run(project: Project, *, sync: bool = True) -> HumanityReviewResult:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    run = None
    try:
        blocker = _active_blocker(conn, project_id=project.id)
        if blocker is not None:
            code, message = blocker
            return HumanityReviewResult(False, message, error_code=code)
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return HumanityReviewResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        plan = repo.get_plan(conn, plan_id=state.current_visual_edit_plan_id)
        if plan is None:
            return HumanityReviewResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=plan)
        run = VisualEditRun(
            run_id=repo.new_visual_edit_run_id(),
            project_id=project.id,
            scope=VISUAL_EDIT_RUN_SCOPE_HUMANITY,
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
        return process_humanity_review(project, run_id=run.run_id)
    launched = get_visual_edit_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="humanity_review",
        sync=False,
    )
    if not launched:
        return HumanityReviewResult(
            False,
            "Visual-Edit-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=VISUAL_EDIT_ERROR_RUN_ALREADY_ACTIVE,
        )
    return HumanityReviewResult(True, "Humanity Review gestartet.", run=run)


def process_humanity_review(project: Project, *, run_id: str | None = None) -> HumanityReviewResult:
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
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        if run is not None and run.input_fingerprint != context.fingerprint:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_INPUT_STALE)
        signals = deterministic_humanity_signals(bundle, context.package)
        request_input = {
            "plan": bundle.plan.model_dump(mode="json"),
            "shots": [shot.model_dump(mode="json") for shot in bundle.shots],
            "assignments": [assignment.model_dump(mode="json") for assignment in bundle.assignments],
            "deterministic_signals": signals,
            "next_review_version": 1,
        }
        request = TextGatewayRequest(
            project_id=project.id,
            run_id=run.run_id if run else repo.new_visual_edit_run_id(),
            request_kind=TEXT_REQUEST_KIND_HUMANITY_REVIEW,
            prompt="humanity_review",
            provider=config.provider,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            prompt_version=PROMPT_VERSION_HUMANITY_REVIEW,
            response_schema_version=RESPONSE_SCHEMA_HUMANITY_REVIEW,
            input_fingerprint=context.fingerprint,
            visual_edit_input=request_input,
        )
        response = DiscoveryTextGateway(config=config).generate(request)
        if response.humanity_review is None:
            raise VisualEditServiceError(VISUAL_EDIT_ERROR_RESPONSE_INVALID)
        payload = response.humanity_review
        review = HumanityReview(
            review_id=payload.review_id,
            visual_edit_plan_id=payload.visual_edit_plan_id,
            review_version=payload.review_version,
            input_fingerprint=payload.input_fingerprint,
            status="completed",
            overall_judgment=payload.overall_judgment,
            deterministic_signals=signals,
            created_at=payload.created_at,
        )
        review_bundle = HumanityReviewBundle(review=review, findings=payload.findings)
        relative = repo.save_humanity_review_json(project.project_root_path, review_bundle)
        conn.execute("BEGIN IMMEDIATE")
        if state.current_humanity_review_id:
            repo.update_humanity_review_status(conn, review_id=state.current_humanity_review_id, status="superseded")
        repo.insert_humanity_review_bundle(conn, review_bundle, relative)
        repo.mark_current_humanity_review(conn, project_id=project.id, review_id=review.review_id)
        repo.write_latest_humanity_pointer(project.project_root_path, review)
        if any(finding.severity == "blocking" and finding.user_status == "open" for finding in payload.findings):
            repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="repair_required")
        if run is not None:
            run = run.model_copy(
                update={"status": VisualEditRunStatus.COMPLETED, "finished_at": _now()}
            )
            repo.update_visual_edit_run(conn, run)
        conn.commit()
        return HumanityReviewResult(True, "Humanity Review abgeschlossen.", run=run, review=review)
    except TextGatewayError as exc:
        conn.rollback()
        run = _fail_run(conn, run, exc.code)
        return HumanityReviewResult(False, "Humanity Review fehlgeschlagen.", run=run, error_code=exc.code)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        code = getattr(exc, "code", VISUAL_EDIT_ERROR_HUMANITY_REVIEW_INVALID)
        run = _fail_run(conn, run, code)
        return HumanityReviewResult(False, "Humanity Review fehlgeschlagen.", run=run, error_code=code)
    finally:
        conn.close()


def process_humanity_review_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_visual_edit_registry(root)
    try:
        run = repo.get_visual_edit_run(conn, run_id=run_id)
    finally:
        conn.close()
    if run is None:
        return
    from otio_app.discovery_v2.application.visual_edit_plan_service import _project_stub

    process_humanity_review(_project_stub(root, run.project_id), run_id=run_id)


def deterministic_humanity_signals(bundle, package: dict[str, object]) -> dict[str, object]:
    assignments = bundle.assignments
    shots = bundle.shots
    asset_counts: dict[str, int] = {}
    for assignment in assignments:
        if assignment.asset_id:
            asset_counts[assignment.asset_id] = asset_counts.get(assignment.asset_id, 0) + 1
    durations = [shot.duration_seconds for shot in shots]
    duration_ratio = max(durations) / min(durations) if durations and min(durations) > 0 else 0.0
    boundary_ratio = _sentence_boundary_cut_ratio(shots, package)
    candidate_by_observation = {
        str(candidate.get("observation_id")): candidate
        for candidate in package.get("candidates", [])
        if isinstance(candidate, dict)
    }
    generic_count = 0
    motif_run = 0
    max_motif_run = 0
    previous_motif = None
    for assignment in assignments:
        candidate = candidate_by_observation.get(str(assignment.visual_observation_id))
        motif = None if candidate is None else candidate.get("motif_hash")
        if candidate is not None and candidate.get("generic_stock_like"):
            generic_count += 1
        if motif is not None and motif == previous_motif:
            motif_run += 1
        else:
            motif_run = 1
        previous_motif = motif
        max_motif_run = max(max_motif_run, motif_run)
    generic_ratio = generic_count / len(assignments) if assignments else 0.0
    return {
        "asset_reuse_counts": asset_counts,
        "sentence_boundary_cut_ratio": boundary_ratio,
        "sentence_boundary_warning": boundary_ratio > SENTENCE_BOUNDARY_WARNING_RATIO,
        "sentence_boundary_blocking": boundary_ratio > SENTENCE_BOUNDARY_BLOCKING_RATIO,
        "duration_ratio": duration_ratio,
        "duration_variance_warning": len(shots) >= SHOT_DURATION_VARIANCE_MIN_SHOTS
        and duration_ratio < SHOT_DURATION_VARIANCE_MIN_RATIO,
        "generic_stock_ratio": generic_ratio,
        "generic_stock_warning": generic_ratio >= GENERIC_STOCK_WARNING_RATIO,
        "generic_stock_blocking": generic_ratio >= GENERIC_STOCK_BLOCKING_RATIO,
        "max_similar_motif_run": max_motif_run,
        "similar_motif_warning": max_motif_run >= SIMILAR_MOTIF_WARNING_RUN,
        "similar_motif_blocking": max_motif_run >= SIMILAR_MOTIF_BLOCKING_RUN,
    }


def _sentence_boundary_cut_ratio(shots, package: dict[str, object]) -> float:
    timeline = package.get("narration_timeline", {})
    entries = timeline.get("entries", []) if isinstance(timeline, dict) else []
    sentence_end_frames = {
        int(entry["end_frame"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("entry_type") == "voice" and entry.get("sentence_id")
    }
    if len(shots) < 2:
        return 0.0
    total_cuts = len(shots) - 1
    boundary_cuts = 0
    for left, right in zip(shots, shots[1:]):
        if left.timeline_end_frame == right.timeline_start_frame and left.timeline_end_frame in sentence_end_frames:
            boundary_cuts += 1
    return boundary_cuts / total_cuts if total_cuts else 0.0


def _fail_run(conn, run: VisualEditRun | None, code: str) -> VisualEditRun | None:
    if run is None:
        return None
    failed = run.model_copy(
        update={
            "status": VisualEditRunStatus.FAILED,
            "error_code": code,
            "error_message": "Humanity review worker failed.",
            "finished_at": _now(),
        }
    )
    repo.update_visual_edit_run(conn, failed)
    conn.commit()
    return failed


__all__ = [
    "HumanityReviewResult",
    "deterministic_humanity_signals",
    "process_humanity_review",
    "process_humanity_review_run",
    "start_humanity_review_run",
]
