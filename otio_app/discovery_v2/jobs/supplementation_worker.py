"""Worker for Phase 10 local supplementation jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.application.supplementation_service import (
    perform_search_for_gap,
    validate_candidates_for_gap,
)
from otio_app.discovery_v2.domain.supplementation import (
    SUPPLEMENTATION_ERROR_REGISTRY_WRITE_FAILED,
    SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION,
    SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW,
    SUPPLEMENTATION_RUN_SCOPE_SEARCH,
    SupplementationAttempt,
    SupplementationAttemptStatus,
    SupplementationRun,
    SupplementationRunStatus,
)
from otio_app.discovery_v2.persistence import supplementation_repository as repo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_supplementation_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_supplementation_registry(root)
    try:
        run = repo.get_supplementation_run(conn, run_id=run_id)
        if run is None:
            return
        run = run.model_copy(
            update={
                "status": SupplementationRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
            }
        )
        repo.update_supplementation_run(conn, run)
        conn.commit()
        try:
            if run.scope == SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW:
                run = _process_local_review(conn, root, run)
            elif run.scope == SUPPLEMENTATION_RUN_SCOPE_SEARCH:
                run = _process_search(conn, root, run)
            elif run.scope == SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION:
                run = _process_candidate_validation(conn, root, run)
            else:
                raise ValueError(f"Unsupported supplementation scope: {run.scope}")
        except Exception as exc:  # noqa: BLE001
            run = _fail_run(conn, run, getattr(exc, "code", SUPPLEMENTATION_ERROR_REGISTRY_WRITE_FAILED), str(exc))
        _write_report(conn, root, run)
    finally:
        conn.close()
        repo.cleanup_supplementation_temp(root, run_id=run_id)


def _process_local_review(conn, root: Path, run: SupplementationRun) -> SupplementationRun:
    for gap_id in run.selected_gap_ids:
        attempt = _start_attempt(conn, run, gap_id=gap_id)
        _complete_attempt(
            conn,
            root,
            attempt,
            {"gap_id": gap_id, "local_review": "manual_review_assigned"},
        )
    return _complete_run(conn, run)


def _process_search(conn, root: Path, run: SupplementationRun) -> SupplementationRun:
    for gap_id in run.selected_gap_ids:
        attempt = _start_attempt(conn, run, gap_id=gap_id)
        perform_search_for_gap(
            root,
            project_id=run.project_id,
            run_id=run.run_id,
            gap_id=gap_id,
        )
        _complete_attempt(conn, root, attempt, {"gap_id": gap_id, "search": "completed"})
    return _complete_run(conn, run)


def _process_candidate_validation(conn, root: Path, run: SupplementationRun) -> SupplementationRun:
    for gap_id in run.selected_gap_ids:
        attempt = _start_attempt(conn, run, gap_id=gap_id)
        validate_candidates_for_gap(root, project_id=run.project_id, gap_id=gap_id)
        _complete_attempt(conn, root, attempt, {"gap_id": gap_id, "validation": "completed"})
    return _complete_run(conn, run)


def _start_attempt(conn, run: SupplementationRun, *, gap_id: str) -> SupplementationAttempt:
    attempt = SupplementationAttempt(
        attempt_id=repo.new_supplementation_attempt_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        scope=run.scope,
        gap_id=gap_id,
        status=SupplementationAttemptStatus.RUNNING,
        created_at=_now(),
    )
    repo.insert_supplementation_attempt(conn, attempt)
    conn.commit()
    return attempt


def _complete_attempt(conn, root: Path, attempt: SupplementationAttempt, payload: dict) -> None:
    relative = repo.save_supplementation_attempt_json(root, attempt, payload)
    repo.update_supplementation_attempt(
        conn,
        attempt.model_copy(
            update={
                "status": SupplementationAttemptStatus.COMPLETED,
                "relative_json_path": relative,
                "completed_at": _now(),
            }
        ),
    )
    conn.commit()


def _complete_run(conn, run: SupplementationRun) -> SupplementationRun:
    final = run.model_copy(
        update={"status": SupplementationRunStatus.COMPLETED, "finished_at": _now()}
    )
    repo.update_supplementation_run(conn, final)
    conn.commit()
    return final


def _fail_run(conn, run: SupplementationRun, code: str, message: str) -> SupplementationRun:
    for attempt in repo.list_supplementation_attempts(conn, run_id=run.run_id):
        if attempt.status == SupplementationAttemptStatus.RUNNING:
            repo.update_supplementation_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": SupplementationAttemptStatus.FAILED,
                        "error_code": code,
                        "error_message": message,
                        "completed_at": _now(),
                    }
                ),
            )
    failed = run.model_copy(
        update={
            "status": SupplementationRunStatus.FAILED,
            "error_code": code,
            "error_message": message,
            "finished_at": _now(),
        }
    )
    repo.update_supplementation_run(conn, failed)
    conn.commit()
    return failed


def _write_report(conn, root: Path, run: SupplementationRun) -> None:
    payload = {
        "run": run.model_dump(mode="json"),
        "attempts": [
            attempt.model_dump(mode="json")
            for attempt in repo.list_supplementation_attempts(conn, run_id=run.run_id)
        ],
    }
    relative = repo.save_supplementation_run_report(root, run, payload)
    repo.update_supplementation_run(conn, run.model_copy(update={"relative_report_path": relative}))
    conn.commit()


__all__ = ["process_supplementation_run"]
