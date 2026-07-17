"""Persistence for Discovery V2 Phase 10 supplementation and script locks."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.supplementation import (
    ACTIVE_SUPPLEMENTATION_RUN_STATUSES,
    CandidateDecision,
    CandidateDecisionValue,
    ClaimDecision,
    ClaimDecisionValue,
    CoverageGap,
    CoverageGapStatus,
    CoverageLevel,
    CoverageRiskFlag,
    EscalationStep,
    GapEvent,
    GapEventType,
    GraphicPlan,
    GraphicPlanUserStatus,
    ScriptLock,
    ScriptLockRisk,
    ScriptLockStatus,
    StockCandidate,
    StockCandidateUserStatus,
    StockDuplicateStatus,
    StockLicenseStatus,
    StockSearchAttempt,
    StockSearchAttemptStatus,
    SupplementationAttempt,
    SupplementationAttemptStatus,
    SupplementationRequest,
    SupplementationRequestStatus,
    SupplementationRun,
    SupplementationRunStatus,
    metadata_fingerprint,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.editorial_repository import (
    bind_project_root_for_json_reads,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.supplementation_paths import (
    assert_supplementation_relative_path,
    resolve_supplementation_relative_path,
    supplementation_attempt_json_relative_path,
    supplementation_candidate_json_relative_path,
    supplementation_claim_decision_json_relative_path,
    supplementation_gap_json_relative_path,
    supplementation_graphic_plan_json_relative_path,
    supplementation_latest_script_lock_relative_path,
    supplementation_request_json_relative_path,
    supplementation_run_json_relative_path,
    supplementation_script_lock_json_relative_path,
    supplementation_search_json_relative_path,
    supplementation_temp_dir,
)


def open_supplementation_registry(project_root: Path) -> sqlite3.Connection:
    bind_project_root_for_json_reads(project_root)
    bind_project_root_for_supplementation_json_reads(project_root)
    return get_registry_connection(project_root)


def new_gap_id() -> str:
    return str(uuid4())


def new_gap_event_id() -> str:
    return str(uuid4())


def new_supplementation_run_id() -> str:
    return str(uuid4())


def new_supplementation_attempt_id() -> str:
    return str(uuid4())


def new_supplementation_request_id() -> str:
    return str(uuid4())


def new_stock_search_attempt_id() -> str:
    return str(uuid4())


def new_stock_candidate_id() -> str:
    return str(uuid4())


def new_candidate_decision_id() -> str:
    return str(uuid4())


def new_claim_decision_id() -> str:
    return str(uuid4())


def new_graphic_plan_id() -> str:
    return str(uuid4())


def new_script_lock_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: object | None) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def insert_coverage_gap(
    conn: sqlite3.Connection,
    gap: CoverageGap,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO coverage_gaps (
            gap_id, project_id, script_id, script_version, coverage_audit_id,
            visual_intent_id, coverage_level, risk_flags_json,
            missing_properties_json, current_escalation_step,
            prior_attempt_summaries_json, user_decision, outcome, status,
            gap_version, accepted_unresolved_risks_json, resolved_asset_id,
            relative_json_path, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _gap_values(gap, relative_json_path),
    )


def update_coverage_gap(
    conn: sqlite3.Connection,
    gap: CoverageGap,
    relative_json_path: str | None = None,
) -> None:
    if relative_json_path is not None:
        assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        UPDATE coverage_gaps SET
            coverage_level = ?, risk_flags_json = ?, missing_properties_json = ?,
            current_escalation_step = ?, prior_attempt_summaries_json = ?,
            user_decision = ?, outcome = ?, status = ?, gap_version = ?,
            accepted_unresolved_risks_json = ?, resolved_asset_id = ?,
            relative_json_path = COALESCE(?, relative_json_path),
            updated_at = ?
        WHERE gap_id = ?
        """,
        (
            gap.coverage_level.value,
            _json([risk.value for risk in gap.risk_flags]),
            _json(gap.missing_properties),
            gap.current_escalation_step.value,
            _json(gap.prior_attempt_summaries),
            gap.user_decision,
            gap.outcome,
            gap.status.value,
            gap.gap_version,
            _json([risk.value for risk in gap.accepted_unresolved_risks]),
            gap.resolved_asset_id,
            relative_json_path,
            gap.updated_at.isoformat(),
            gap.gap_id,
        ),
    )


def get_coverage_gap(conn: sqlite3.Connection, *, gap_id: str) -> CoverageGap | None:
    row = conn.execute("SELECT * FROM coverage_gaps WHERE gap_id = ?", (gap_id,)).fetchone()
    return None if row is None else _row_to_gap(row)


def list_coverage_gaps(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    include_superseded: bool = False,
) -> list[CoverageGap]:
    sql = "SELECT * FROM coverage_gaps WHERE project_id = ?"
    params: list[object] = [project_id]
    if not include_superseded:
        sql += " AND status != 'superseded'"
    sql += " ORDER BY created_at, gap_id"
    return [_row_to_gap(row) for row in conn.execute(sql, params).fetchall()]


def list_gaps_for_audit(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    coverage_audit_id: str,
) -> list[CoverageGap]:
    rows = conn.execute(
        """
        SELECT * FROM coverage_gaps
        WHERE project_id = ? AND coverage_audit_id = ?
        ORDER BY created_at, gap_id
        """,
        (project_id, coverage_audit_id),
    ).fetchall()
    return [_row_to_gap(row) for row in rows]


def next_gap_version(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    visual_intent_id: str,
) -> int:
    row = conn.execute(
        """
        SELECT MAX(gap_version) AS max_version
        FROM coverage_gaps
        WHERE project_id = ? AND visual_intent_id = ?
        """,
        (project_id, visual_intent_id),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def supersede_gaps_not_in_audit(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    coverage_audit_id: str,
) -> list[CoverageGap]:
    stale = [
        gap
        for gap in list_coverage_gaps(conn, project_id=project_id)
        if gap.coverage_audit_id != coverage_audit_id
        and gap.status != CoverageGapStatus.SUPERSEDED
    ]
    for gap in stale:
        update_coverage_gap(
            conn,
            gap.model_copy(
                update={"status": CoverageGapStatus.SUPERSEDED, "updated_at": _now()}
            ),
        )
    return stale


def append_gap_event(conn: sqlite3.Connection, event: GapEvent) -> None:
    conn.execute(
        """
        INSERT INTO coverage_gap_events (
            event_id, gap_id, project_id, event_type, from_step, to_step,
            message, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.gap_id,
            event.project_id,
            event.event_type.value,
            None if event.from_step is None else event.from_step.value,
            None if event.to_step is None else event.to_step.value,
            event.message,
            _json(event.payload),
            event.created_at.isoformat(),
        ),
    )


def list_gap_events(conn: sqlite3.Connection, *, gap_id: str) -> list[GapEvent]:
    rows = conn.execute(
        """
        SELECT * FROM coverage_gap_events
        WHERE gap_id = ?
        ORDER BY created_at, event_id
        """,
        (gap_id,),
    ).fetchall()
    return [_row_to_gap_event(row) for row in rows]


def insert_supplementation_run(conn: sqlite3.Connection, run: SupplementationRun) -> None:
    conn.execute(
        """
        INSERT INTO supplementation_runs (
            run_id, project_id, scope, status, selected_gap_ids_json,
            error_code, error_message, relative_report_path, created_at,
            started_at, finished_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _run_values(run),
    )


def update_supplementation_run(conn: sqlite3.Connection, run: SupplementationRun) -> None:
    conn.execute(
        """
        UPDATE supplementation_runs SET
            scope = ?, status = ?, selected_gap_ids_json = ?, error_code = ?,
            error_message = ?, relative_report_path = ?, started_at = ?,
            finished_at = ?, schema_version = ?
        WHERE run_id = ?
        """,
        (
            run.scope,
            run.status.value,
            _json(run.selected_gap_ids),
            run.error_code,
            run.error_message,
            run.relative_report_path,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.schema_version,
            run.run_id,
        ),
    )


def get_supplementation_run(
    conn: sqlite3.Connection, *, run_id: str
) -> SupplementationRun | None:
    row = conn.execute(
        "SELECT * FROM supplementation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def list_supplementation_runs(
    conn: sqlite3.Connection, *, project_id: str
) -> list[SupplementationRun]:
    rows = conn.execute(
        """
        SELECT * FROM supplementation_runs
        WHERE project_id = ?
        ORDER BY created_at DESC, run_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def find_active_supplementation_run(
    conn: sqlite3.Connection, *, project_id: str
) -> SupplementationRun | None:
    rows = conn.execute(
        """
        SELECT * FROM supplementation_runs
        WHERE project_id = ? AND status IN (?, ?)
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            project_id,
            SupplementationRunStatus.QUEUED.value,
            SupplementationRunStatus.RUNNING.value,
        ),
    ).fetchall()
    return None if not rows else _row_to_run(rows[0])


def insert_supplementation_attempt(
    conn: sqlite3.Connection,
    attempt: SupplementationAttempt,
) -> None:
    conn.execute(
        """
        INSERT INTO supplementation_attempts (
            attempt_id, run_id, project_id, scope, gap_id, request_id, cache_key,
            status, relative_json_path, error_code, error_message, created_at,
            completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _attempt_values(attempt),
    )


def update_supplementation_attempt(
    conn: sqlite3.Connection,
    attempt: SupplementationAttempt,
) -> None:
    conn.execute(
        """
        UPDATE supplementation_attempts SET
            status = ?, relative_json_path = ?, error_code = ?, error_message = ?,
            completed_at = ?
        WHERE attempt_id = ?
        """,
        (
            attempt.status.value,
            attempt.relative_json_path,
            attempt.error_code,
            attempt.error_message,
            None if attempt.completed_at is None else attempt.completed_at.isoformat(),
            attempt.attempt_id,
        ),
    )


def list_supplementation_attempts(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    project_id: str | None = None,
) -> list[SupplementationAttempt]:
    sql = "SELECT * FROM supplementation_attempts"
    params: list[object] = []
    if run_id is not None:
        sql += " WHERE run_id = ?"
        params.append(run_id)
    elif project_id is not None:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    sql += " ORDER BY created_at, attempt_id"
    return [_row_to_attempt(row) for row in conn.execute(sql, params).fetchall()]


def insert_supplementation_request(
    conn: sqlite3.Connection,
    request: SupplementationRequest,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO supplementation_requests (
            request_id, project_id, gap_id, script_id, visual_intent_id, motif,
            action, setting, geographic_requirements,
            authenticity_requirements_json, allowed_media_kinds_json, query_text,
            search_version, status, relative_json_path, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.request_id,
            request.project_id,
            request.gap_id,
            request.script_id,
            request.visual_intent_id,
            request.motif,
            request.action,
            request.setting,
            request.geographic_requirements,
            _json(request.authenticity_requirements),
            _json(request.allowed_media_kinds),
            request.query_text,
            request.search_version,
            request.status.value,
            relative_json_path,
            request.created_at.isoformat(),
            request.updated_at.isoformat(),
        ),
    )


def update_supplementation_request(
    conn: sqlite3.Connection,
    request: SupplementationRequest,
    relative_json_path: str | None = None,
) -> None:
    if relative_json_path is not None:
        assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        UPDATE supplementation_requests SET
            query_text = ?, search_version = ?, status = ?,
            relative_json_path = COALESCE(?, relative_json_path),
            updated_at = ?
        WHERE request_id = ?
        """,
        (
            request.query_text,
            request.search_version,
            request.status.value,
            relative_json_path,
            request.updated_at.isoformat(),
            request.request_id,
        ),
    )


def get_supplementation_request(
    conn: sqlite3.Connection, *, request_id: str
) -> SupplementationRequest | None:
    row = conn.execute(
        "SELECT * FROM supplementation_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return None if row is None else _row_to_request(row)


def get_latest_request_for_gap(
    conn: sqlite3.Connection, *, gap_id: str
) -> SupplementationRequest | None:
    row = conn.execute(
        """
        SELECT * FROM supplementation_requests
        WHERE gap_id = ?
        ORDER BY search_version DESC, created_at DESC
        LIMIT 1
        """,
        (gap_id,),
    ).fetchone()
    return None if row is None else _row_to_request(row)


def next_request_search_version(conn: sqlite3.Connection, *, gap_id: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(search_version) AS max_version
        FROM supplementation_requests
        WHERE gap_id = ?
        """,
        (gap_id,),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def insert_stock_search_attempt(
    conn: sqlite3.Connection,
    attempt: StockSearchAttempt,
    relative_json_path: str | None = None,
) -> None:
    if relative_json_path is not None:
        assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO stock_search_attempts (
            attempt_id, project_id, request_id, gap_id, query_text, search_strategy,
            provider, adapter_version, attempt_number, result_count, status,
            error_code, error_message, relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt.attempt_id,
            attempt.project_id,
            attempt.request_id,
            attempt.gap_id,
            attempt.query_text,
            attempt.search_strategy,
            attempt.provider,
            attempt.adapter_version,
            attempt.attempt_number,
            attempt.result_count,
            attempt.status.value,
            attempt.error_code,
            attempt.error_message,
            relative_json_path,
            attempt.created_at.isoformat(),
        ),
    )


def update_stock_search_attempt_path(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        "UPDATE stock_search_attempts SET relative_json_path = ? WHERE attempt_id = ?",
        (relative_json_path, attempt_id),
    )


def count_search_attempts_for_gap_version(
    conn: sqlite3.Connection,
    *,
    gap_id: str,
    gap_version: int,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM stock_search_attempts s
        JOIN coverage_gaps g ON g.gap_id = s.gap_id
        WHERE s.gap_id = ? AND g.gap_version = ?
        """,
        (gap_id, gap_version),
    ).fetchone()
    return int(row["count"] or 0)


def next_stock_attempt_number(conn: sqlite3.Connection, *, request_id: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(attempt_number) AS max_number
        FROM stock_search_attempts
        WHERE request_id = ?
        """,
        (request_id,),
    ).fetchone()
    return int(row["max_number"] or 0) + 1


def list_stock_search_attempts_for_gap(
    conn: sqlite3.Connection, *, gap_id: str
) -> list[StockSearchAttempt]:
    rows = conn.execute(
        """
        SELECT * FROM stock_search_attempts
        WHERE gap_id = ?
        ORDER BY attempt_number, created_at
        """,
        (gap_id,),
    ).fetchall()
    return [_row_to_stock_attempt(row) for row in rows]


def insert_stock_candidate(
    conn: sqlite3.Connection,
    candidate: StockCandidate,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    fingerprint = candidate.metadata_fingerprint or metadata_fingerprint(candidate)
    conn.execute(
        """
        INSERT INTO stock_candidates (
            candidate_id, project_id, request_id, gap_id, attempt_id, provider,
            provider_candidate_id, preview_ref, description, media_kind,
            visible_metadata_json, geographic_hint, license_status, duplicate_status,
            user_status, metadata_fingerprint, preview_sha256, relative_json_path,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.candidate_id,
            candidate.project_id,
            candidate.request_id,
            candidate.gap_id,
            candidate.attempt_id,
            candidate.provider,
            candidate.provider_candidate_id,
            candidate.preview_ref,
            candidate.description,
            candidate.media_kind,
            _json(candidate.visible_metadata),
            candidate.geographic_hint,
            candidate.license_status.value,
            candidate.duplicate_status.value,
            candidate.user_status.value,
            fingerprint,
            candidate.preview_sha256,
            relative_json_path,
            candidate.created_at.isoformat(),
        ),
    )


def update_stock_candidate(
    conn: sqlite3.Connection,
    candidate: StockCandidate,
    relative_json_path: str | None = None,
) -> None:
    if relative_json_path is not None:
        assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        UPDATE stock_candidates SET
            duplicate_status = ?, user_status = ?, metadata_fingerprint = ?,
            preview_sha256 = ?, relative_json_path = COALESCE(?, relative_json_path)
        WHERE candidate_id = ?
        """,
        (
            candidate.duplicate_status.value,
            candidate.user_status.value,
            candidate.metadata_fingerprint or metadata_fingerprint(candidate),
            candidate.preview_sha256,
            relative_json_path,
            candidate.candidate_id,
        ),
    )


def get_stock_candidate(
    conn: sqlite3.Connection, *, candidate_id: str
) -> StockCandidate | None:
    row = conn.execute(
        "SELECT * FROM stock_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return None if row is None else _row_to_candidate(row)


def list_stock_candidates_for_gap(
    conn: sqlite3.Connection, *, gap_id: str
) -> list[StockCandidate]:
    rows = conn.execute(
        """
        SELECT * FROM stock_candidates
        WHERE gap_id = ?
        ORDER BY created_at, candidate_id
        """,
        (gap_id,),
    ).fetchall()
    return [_row_to_candidate(row) for row in rows]


def list_stock_candidates_for_attempt(
    conn: sqlite3.Connection, *, attempt_id: str
) -> list[StockCandidate]:
    rows = conn.execute(
        """
        SELECT * FROM stock_candidates
        WHERE attempt_id = ?
        ORDER BY created_at, candidate_id
        """,
        (attempt_id,),
    ).fetchall()
    return [_row_to_candidate(row) for row in rows]


def next_candidate_decision_revision(conn: sqlite3.Connection, *, candidate_id: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(revision) AS max_revision
        FROM stock_candidate_decisions
        WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    return int(row["max_revision"] or 0) + 1


def append_candidate_decision(conn: sqlite3.Connection, decision: CandidateDecision) -> None:
    conn.execute(
        """
        INSERT INTO stock_candidate_decisions (
            decision_id, project_id, gap_id, candidate_id, revision, decision,
            reason, user_note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision.decision_id,
            decision.project_id,
            decision.gap_id,
            decision.candidate_id,
            decision.revision,
            decision.decision.value,
            decision.reason,
            decision.user_note,
            decision.created_at.isoformat(),
        ),
    )


def list_candidate_decisions(
    conn: sqlite3.Connection, *, candidate_id: str | None = None, gap_id: str | None = None
) -> list[CandidateDecision]:
    sql = "SELECT * FROM stock_candidate_decisions"
    params: list[object] = []
    if candidate_id is not None:
        sql += " WHERE candidate_id = ?"
        params.append(candidate_id)
    elif gap_id is not None:
        sql += " WHERE gap_id = ?"
        params.append(gap_id)
    sql += " ORDER BY created_at, revision"
    return [_row_to_candidate_decision(row) for row in conn.execute(sql, params).fetchall()]


def next_claim_decision_revision(
    conn: sqlite3.Connection, *, script_id: str, claim_id: str
) -> int:
    row = conn.execute(
        """
        SELECT MAX(revision) AS max_revision
        FROM claim_decisions
        WHERE script_id = ? AND claim_id = ?
        """,
        (script_id, claim_id),
    ).fetchone()
    return int(row["max_revision"] or 0) + 1


def append_claim_decision(
    conn: sqlite3.Connection,
    decision: ClaimDecision,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO claim_decisions (
            decision_id, project_id, script_id, claim_id, claim_content_sha256,
            revision, decision, reason, user_note, relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision.decision_id,
            decision.project_id,
            decision.script_id,
            decision.claim_id,
            decision.claim_content_sha256,
            decision.revision,
            decision.decision.value,
            decision.reason,
            decision.user_note,
            relative_json_path,
            decision.created_at.isoformat(),
        ),
    )


def list_claim_decisions(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_id: str | None = None,
) -> list[ClaimDecision]:
    sql = "SELECT * FROM claim_decisions WHERE project_id = ?"
    params: list[object] = [project_id]
    if script_id is not None:
        sql += " AND script_id = ?"
        params.append(script_id)
    sql += " ORDER BY script_id, claim_id, revision"
    return [_row_to_claim_decision(row) for row in conn.execute(sql, params).fetchall()]


def latest_claim_decisions_for_script(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_id: str,
) -> dict[str, ClaimDecision]:
    result: dict[str, ClaimDecision] = {}
    for decision in list_claim_decisions(conn, project_id=project_id, script_id=script_id):
        current = result.get(decision.claim_id)
        if current is None or decision.revision > current.revision:
            result[decision.claim_id] = decision
    return result


def insert_graphic_plan(
    conn: sqlite3.Connection,
    plan: GraphicPlan,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO graphic_plans (
            graphic_plan_id, project_id, visual_intent_id, gap_id, description,
            required_data_json, geographic_scope, user_status, relative_json_path,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.graphic_plan_id,
            plan.project_id,
            plan.visual_intent_id,
            plan.gap_id,
            plan.description,
            _json(plan.required_data),
            plan.geographic_scope,
            plan.user_status.value,
            relative_json_path,
            plan.created_at.isoformat(),
            plan.updated_at.isoformat(),
        ),
    )


def list_graphic_plans_for_gap(
    conn: sqlite3.Connection, *, gap_id: str
) -> list[GraphicPlan]:
    rows = conn.execute(
        """
        SELECT * FROM graphic_plans
        WHERE gap_id = ?
        ORDER BY created_at, graphic_plan_id
        """,
        (gap_id,),
    ).fetchall()
    return [_row_to_graphic_plan(row) for row in rows]


def insert_script_lock(
    conn: sqlite3.Connection,
    lock: ScriptLock,
    relative_json_path: str,
) -> None:
    assert_supplementation_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO script_locks (
            lock_id, project_id, script_id, script_version, project_brief_id,
            narrative_plan_id, selected_hook_id, coverage_audit_id,
            observation_set_fingerprint, script_hash, structure_fingerprint,
            coverage_fingerprint, accepted_open_risks_json,
            claim_decision_snapshot_json, user_confirmed, user_confirmed_at,
            confirmation_fingerprint, lock_fingerprint, lock_version, status,
            relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lock.lock_id,
            lock.project_id,
            lock.script_id,
            lock.script_version,
            lock.project_brief_id,
            lock.narrative_plan_id,
            lock.selected_hook_id,
            lock.coverage_audit_id,
            lock.observation_set_fingerprint,
            lock.script_hash,
            lock.structure_fingerprint,
            lock.coverage_fingerprint,
            _json(lock.accepted_open_risks),
            _json(lock.claim_decision_snapshot),
            1 if lock.user_confirmed else 0,
            None if lock.user_confirmed_at is None else lock.user_confirmed_at.isoformat(),
            lock.confirmation_fingerprint,
            lock.lock_fingerprint,
            lock.lock_version,
            lock.status.value,
            relative_json_path,
            lock.created_at.isoformat(),
        ),
    )


def update_script_lock_status(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    status: ScriptLockStatus,
) -> None:
    conn.execute(
        "UPDATE script_locks SET status = ? WHERE lock_id = ?",
        (status.value, lock_id),
    )


def insert_script_lock_risk(conn: sqlite3.Connection, risk: ScriptLockRisk) -> None:
    conn.execute(
        """
        INSERT INTO script_lock_risks (
            lock_id, risk_key, confirmed_at, confirmation_fingerprint
        ) VALUES (?, ?, ?, ?)
        """,
        (
            risk.lock_id,
            risk.risk_key,
            risk.confirmed_at.isoformat(),
            risk.confirmation_fingerprint,
        ),
    )


def get_script_lock(conn: sqlite3.Connection, *, lock_id: str) -> ScriptLock | None:
    row = conn.execute("SELECT * FROM script_locks WHERE lock_id = ?", (lock_id,)).fetchone()
    return None if row is None else _row_to_script_lock(row)


def get_current_script_lock(
    conn: sqlite3.Connection, *, project_id: str
) -> ScriptLock | None:
    row = conn.execute(
        """
        SELECT * FROM script_locks
        WHERE project_id = ? AND status = 'locked'
        ORDER BY lock_version DESC, created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_script_lock(row)


def list_script_locks(
    conn: sqlite3.Connection, *, project_id: str
) -> list[ScriptLock]:
    rows = conn.execute(
        """
        SELECT * FROM script_locks
        WHERE project_id = ?
        ORDER BY lock_version DESC, created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_script_lock(row) for row in rows]


def next_script_lock_version(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(lock_version) AS max_version
        FROM script_locks
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def supplementation_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name IN (
            'coverage_gaps', 'coverage_gap_events', 'supplementation_runs',
            'supplementation_attempts', 'supplementation_requests',
            'stock_search_attempts', 'stock_candidates',
            'stock_candidate_decisions', 'claim_decisions', 'graphic_plans',
            'script_locks', 'script_lock_risks'
        )
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def save_coverage_gap_json(project_root: Path, gap: CoverageGap) -> str:
    return _save_json(
        project_root,
        supplementation_gap_json_relative_path(gap.gap_id),
        gap.model_dump(mode="json"),
    )


def save_supplementation_request_json(
    project_root: Path, request: SupplementationRequest
) -> str:
    return _save_json(
        project_root,
        supplementation_request_json_relative_path(request.request_id),
        request.model_dump(mode="json"),
    )


def save_stock_search_attempt_json(
    project_root: Path,
    attempt: StockSearchAttempt,
    *,
    candidates: list[StockCandidate],
) -> str:
    return _save_json(
        project_root,
        supplementation_search_json_relative_path(attempt.attempt_id),
        {
            "attempt": attempt.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        },
    )


def save_stock_candidate_json(project_root: Path, candidate: StockCandidate) -> str:
    return _save_json(
        project_root,
        supplementation_candidate_json_relative_path(candidate.candidate_id),
        candidate.model_dump(mode="json"),
    )


def save_candidate_decision_json(
    project_root: Path, decision: CandidateDecision
) -> str:
    return _save_json(
        project_root,
        f"editorial/supplementation/candidate_decisions/{decision.decision_id}.json",
        decision.model_dump(mode="json"),
    )


def save_claim_decision_json(project_root: Path, decision: ClaimDecision) -> str:
    return _save_json(
        project_root,
        supplementation_claim_decision_json_relative_path(decision.decision_id),
        decision.model_dump(mode="json"),
    )


def save_graphic_plan_json(project_root: Path, plan: GraphicPlan) -> str:
    return _save_json(
        project_root,
        supplementation_graphic_plan_json_relative_path(plan.graphic_plan_id),
        plan.model_dump(mode="json"),
    )


def save_script_lock_json(project_root: Path, lock: ScriptLock) -> str:
    return _save_json(
        project_root,
        supplementation_script_lock_json_relative_path(lock.lock_id),
        lock.model_dump(mode="json"),
        latest_relative=supplementation_latest_script_lock_relative_path(),
    )


def save_supplementation_run_report(
    project_root: Path,
    run: SupplementationRun,
    payload: dict,
) -> str:
    return _save_json(
        project_root,
        supplementation_run_json_relative_path(run.run_id),
        payload,
    )


def save_supplementation_attempt_json(
    project_root: Path,
    attempt: SupplementationAttempt,
    payload: dict,
) -> str:
    return _save_json(
        project_root,
        supplementation_attempt_json_relative_path(attempt.attempt_id),
        payload,
    )


def cleanup_supplementation_temp(project_root: Path, *, run_id: str) -> None:
    temp_dir = supplementation_temp_dir(project_root, run_id)
    if not temp_dir.exists():
        return
    for child in sorted(temp_dir.rglob("*"), reverse=True):
        try:
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        except OSError:
            pass
    try:
        temp_dir.rmdir()
    except OSError:
        pass


def bind_project_root_for_supplementation_json_reads(project_root: Path) -> None:
    global _CURRENT_PROJECT_ROOT
    _CURRENT_PROJECT_ROOT = Path(project_root).expanduser().resolve()


def _save_json(
    project_root: Path,
    relative_path: str,
    payload: dict,
    *,
    latest_relative: str | None = None,
) -> str:
    assert_supplementation_relative_path(relative_path)
    _assert_no_absolute_paths(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path = resolve_supplementation_relative_path(project_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        if latest_relative is not None:
            latest = resolve_supplementation_relative_path(project_root, latest_relative)
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest_tmp = latest.with_suffix(latest.suffix + ".tmp")
            latest_tmp.write_text(text, encoding="utf-8")
            latest_tmp.replace(latest)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Supplementation JSON could not be written: {exc}"
        ) from exc
    return relative_path


def _read_json_from_relative(relative_path: str) -> dict:
    root = _CURRENT_PROJECT_ROOT
    if root is None:
        raise InventoryArtifactError("Supplementation project root is not bound.")
    path = resolve_supplementation_relative_path(root, relative_path)
    return json.loads(path.read_text(encoding="utf-8"))


_CURRENT_PROJECT_ROOT: Path | None = None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: object | None, default):
    if value is None:
        return default
    return json.loads(str(value))


def _assert_no_absolute_paths(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (
                value.startswith("/")
                or (len(value) > 2 and value[1] == ":" and value[2] in "\\/")
            ):
                if "path" in str(key).lower() or value.startswith(("/", "\\")):
                    raise ValueError(
                        f"Absolute paths in Supplementation JSON are forbidden: {key}={value}"
                    )
            _assert_no_absolute_paths(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_absolute_paths(item)


def _gap_values(gap: CoverageGap, relative_json_path: str) -> tuple[object, ...]:
    return (
        gap.gap_id,
        gap.project_id,
        gap.script_id,
        gap.script_version,
        gap.coverage_audit_id,
        gap.visual_intent_id,
        gap.coverage_level.value,
        _json([risk.value for risk in gap.risk_flags]),
        _json(gap.missing_properties),
        gap.current_escalation_step.value,
        _json(gap.prior_attempt_summaries),
        gap.user_decision,
        gap.outcome,
        gap.status.value,
        gap.gap_version,
        _json([risk.value for risk in gap.accepted_unresolved_risks]),
        gap.resolved_asset_id,
        relative_json_path,
        gap.created_at.isoformat(),
        gap.updated_at.isoformat(),
    )


def _run_values(run: SupplementationRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.project_id,
        run.scope,
        run.status.value,
        _json(run.selected_gap_ids),
        run.error_code,
        run.error_message,
        run.relative_report_path,
        run.created_at.isoformat(),
        None if run.started_at is None else run.started_at.isoformat(),
        None if run.finished_at is None else run.finished_at.isoformat(),
        run.schema_version,
    )


def _attempt_values(attempt: SupplementationAttempt) -> tuple[object, ...]:
    return (
        attempt.attempt_id,
        attempt.run_id,
        attempt.project_id,
        attempt.scope,
        attempt.gap_id,
        attempt.request_id,
        attempt.cache_key,
        attempt.status.value,
        attempt.relative_json_path,
        attempt.error_code,
        attempt.error_message,
        attempt.created_at.isoformat(),
        None if attempt.completed_at is None else attempt.completed_at.isoformat(),
    )


def _row_to_gap(row: sqlite3.Row) -> CoverageGap:
    return CoverageGap(
        gap_id=str(row["gap_id"]),
        project_id=str(row["project_id"]),
        script_id=str(row["script_id"]),
        script_version=int(row["script_version"]),
        coverage_audit_id=str(row["coverage_audit_id"]),
        visual_intent_id=str(row["visual_intent_id"]),
        coverage_level=CoverageLevel(str(row["coverage_level"])),
        risk_flags=[CoverageRiskFlag(item) for item in _loads(row["risk_flags_json"], [])],
        missing_properties=list(_loads(row["missing_properties_json"], [])),
        current_escalation_step=EscalationStep(str(row["current_escalation_step"])),
        prior_attempt_summaries=list(_loads(row["prior_attempt_summaries_json"], [])),
        user_decision=row["user_decision"],
        outcome=row["outcome"],
        status=CoverageGapStatus(str(row["status"])),
        gap_version=int(row["gap_version"]),
        accepted_unresolved_risks=[
            CoverageRiskFlag(item)
            for item in _loads(row["accepted_unresolved_risks_json"], [])
        ],
        resolved_asset_id=row["resolved_asset_id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _row_to_gap_event(row: sqlite3.Row) -> GapEvent:
    return GapEvent(
        event_id=str(row["event_id"]),
        gap_id=str(row["gap_id"]),
        project_id=str(row["project_id"]),
        event_type=GapEventType(str(row["event_type"])),
        from_step=None if row["from_step"] is None else EscalationStep(str(row["from_step"])),
        to_step=None if row["to_step"] is None else EscalationStep(str(row["to_step"])),
        message=row["message"],
        payload=dict(_loads(row["payload_json"], {})),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_run(row: sqlite3.Row) -> SupplementationRun:
    return SupplementationRun(
        schema_version=str(row["schema_version"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        status=SupplementationRunStatus(str(row["status"])),
        selected_gap_ids=list(_loads(row["selected_gap_ids_json"], [])),
        error_code=row["error_code"],
        error_message=row["error_message"],
        relative_report_path=row["relative_report_path"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
    )


def _row_to_attempt(row: sqlite3.Row) -> SupplementationAttempt:
    return SupplementationAttempt(
        attempt_id=str(row["attempt_id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        gap_id=row["gap_id"],
        request_id=row["request_id"],
        cache_key=row["cache_key"],
        status=SupplementationAttemptStatus(str(row["status"])),
        relative_json_path=row["relative_json_path"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        completed_at=_parse_dt(row["completed_at"]),
    )


def _row_to_request(row: sqlite3.Row) -> SupplementationRequest:
    return SupplementationRequest(
        request_id=str(row["request_id"]),
        project_id=str(row["project_id"]),
        gap_id=str(row["gap_id"]),
        script_id=str(row["script_id"]),
        visual_intent_id=str(row["visual_intent_id"]),
        motif=str(row["motif"]),
        action=str(row["action"]),
        setting=str(row["setting"]),
        geographic_requirements=row["geographic_requirements"],
        authenticity_requirements=list(_loads(row["authenticity_requirements_json"], [])),
        allowed_media_kinds=list(_loads(row["allowed_media_kinds_json"], [])),
        query_text=str(row["query_text"]),
        search_version=int(row["search_version"]),
        status=SupplementationRequestStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _row_to_stock_attempt(row: sqlite3.Row) -> StockSearchAttempt:
    return StockSearchAttempt(
        attempt_id=str(row["attempt_id"]),
        project_id=str(row["project_id"]),
        request_id=str(row["request_id"]),
        gap_id=str(row["gap_id"]),
        query_text=str(row["query_text"]),
        search_strategy=str(row["search_strategy"]),
        provider=str(row["provider"]),
        adapter_version=str(row["adapter_version"]),
        attempt_number=int(row["attempt_number"]),
        result_count=int(row["result_count"]),
        status=StockSearchAttemptStatus(str(row["status"])),
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_candidate(row: sqlite3.Row) -> StockCandidate:
    return StockCandidate(
        candidate_id=str(row["candidate_id"]),
        project_id=str(row["project_id"]),
        request_id=str(row["request_id"]),
        gap_id=str(row["gap_id"]),
        attempt_id=str(row["attempt_id"]),
        provider=str(row["provider"]),
        provider_candidate_id=str(row["provider_candidate_id"]),
        preview_ref=row["preview_ref"],
        description=str(row["description"]),
        media_kind=str(row["media_kind"]),
        visible_metadata=dict(_loads(row["visible_metadata_json"], {})),
        geographic_hint=row["geographic_hint"],
        license_status=StockLicenseStatus(str(row["license_status"])),
        duplicate_status=StockDuplicateStatus(str(row["duplicate_status"])),
        user_status=StockCandidateUserStatus(str(row["user_status"])),
        metadata_fingerprint=row["metadata_fingerprint"],
        preview_sha256=row["preview_sha256"],
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_candidate_decision(row: sqlite3.Row) -> CandidateDecision:
    return CandidateDecision(
        decision_id=str(row["decision_id"]),
        project_id=str(row["project_id"]),
        gap_id=str(row["gap_id"]),
        candidate_id=str(row["candidate_id"]),
        revision=int(row["revision"]),
        decision=CandidateDecisionValue(str(row["decision"])),
        reason=str(row["reason"]),
        user_note=row["user_note"],
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_claim_decision(row: sqlite3.Row) -> ClaimDecision:
    return ClaimDecision(
        decision_id=str(row["decision_id"]),
        project_id=str(row["project_id"]),
        script_id=str(row["script_id"]),
        claim_id=str(row["claim_id"]),
        claim_content_sha256=str(row["claim_content_sha256"]),
        revision=int(row["revision"]),
        decision=ClaimDecisionValue(str(row["decision"])),
        reason=row["reason"],
        user_note=row["user_note"],
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_graphic_plan(row: sqlite3.Row) -> GraphicPlan:
    return GraphicPlan(
        graphic_plan_id=str(row["graphic_plan_id"]),
        project_id=str(row["project_id"]),
        visual_intent_id=str(row["visual_intent_id"]),
        gap_id=str(row["gap_id"]),
        description=str(row["description"]),
        required_data=list(_loads(row["required_data_json"], [])),
        geographic_scope=row["geographic_scope"],
        user_status=GraphicPlanUserStatus(str(row["user_status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _row_to_script_lock(row: sqlite3.Row) -> ScriptLock:
    return ScriptLock(
        lock_id=str(row["lock_id"]),
        project_id=str(row["project_id"]),
        script_id=str(row["script_id"]),
        script_version=int(row["script_version"]),
        project_brief_id=str(row["project_brief_id"]),
        narrative_plan_id=str(row["narrative_plan_id"]),
        selected_hook_id=str(row["selected_hook_id"]),
        coverage_audit_id=str(row["coverage_audit_id"]),
        observation_set_fingerprint=str(row["observation_set_fingerprint"]),
        script_hash=str(row["script_hash"]),
        structure_fingerprint=str(row["structure_fingerprint"]),
        coverage_fingerprint=str(row["coverage_fingerprint"]),
        accepted_open_risks=list(_loads(row["accepted_open_risks_json"], [])),
        claim_decision_snapshot=list(_loads(row["claim_decision_snapshot_json"], [])),
        user_confirmed=bool(row["user_confirmed"]),
        user_confirmed_at=_parse_dt(row["user_confirmed_at"]),
        confirmation_fingerprint=str(row["confirmation_fingerprint"]),
        lock_fingerprint=str(row["lock_fingerprint"]),
        lock_version=int(row["lock_version"]),
        status=ScriptLockStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


__all__ = [
    name
    for name in globals()
    if not name.startswith("_") and name not in {"json", "sqlite3", "Path"}
]
