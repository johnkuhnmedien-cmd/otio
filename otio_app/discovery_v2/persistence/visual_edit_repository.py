"""Persistence for Discovery V2 Phase 12 visual edit artifacts and registry rows."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.visual_edit import (
    ACTIVE_VISUAL_EDIT_RUN_STATUSES,
    AcceptedRiskRef,
    EditorialShot,
    FeasibilityIssue,
    FeasibilityReport,
    FeasibilityReportBundle,
    HumanityFinding,
    HumanityReview,
    HumanityReviewBundle,
    RepairProposal,
    RepairResult,
    RepairRun,
    ShotMediaAssignment,
    ShotTransition,
    SourceRangeIntent,
    VisualEditPlan,
    VisualEditPlanBundle,
    VisualEditProjectState,
    VisualEditRun,
    VisualEditRunStatus,
)
from otio_app.discovery_v2.editing_paths import (
    assert_editing_relative_path,
    feasibility_report_json_relative_path,
    humanity_review_json_relative_path,
    latest_feasibility_report_relative_path,
    latest_humanity_review_relative_path,
    latest_repair_run_relative_path,
    latest_visual_edit_plan_relative_path,
    repair_run_json_relative_path,
    resolve_editing_relative_path,
    visual_edit_plan_json_relative_path,
    visual_edit_report_relative_path,
    visual_edit_temp_dir,
)
from otio_app.discovery_v2.persistence.asset_registry_database import get_registry_connection
from otio_app.discovery_v2.persistence.editorial_repository import bind_project_root_for_json_reads
from otio_app.discovery_v2.persistence.inventory_artifact_store import InventoryArtifactError

_VISUAL_EDIT_JSON_ROOT: Path | None = None


def bind_project_root_for_visual_edit_json_reads(project_root: Path) -> None:
    global _VISUAL_EDIT_JSON_ROOT
    _VISUAL_EDIT_JSON_ROOT = Path(project_root).expanduser().resolve()


def open_visual_edit_registry(project_root: Path) -> sqlite3.Connection:
    bind_project_root_for_json_reads(project_root)
    bind_project_root_for_visual_edit_json_reads(project_root)
    return get_registry_connection(project_root)


def new_visual_edit_run_id() -> str:
    return str(uuid4())


def new_visual_edit_plan_id() -> str:
    return str(uuid4())


def new_editorial_shot_id() -> str:
    return str(uuid4())


def new_assignment_id() -> str:
    return str(uuid4())


def new_transition_id() -> str:
    return str(uuid4())


def new_humanity_review_id() -> str:
    return str(uuid4())


def new_humanity_finding_id() -> str:
    return str(uuid4())


def new_feasibility_report_id() -> str:
    return str(uuid4())


def new_feasibility_issue_id() -> str:
    return str(uuid4())


def new_repair_proposal_id() -> str:
    return str(uuid4())


def new_repair_run_id() -> str:
    return str(uuid4())


def new_repair_result_id() -> str:
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: object, default: object) -> object:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def save_json_artifact(project_root: Path, relative_path: str, payload: object) -> str:
    relative = assert_editing_relative_path(relative_path)
    target = resolve_editing_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if target.exists():
        if target.read_bytes() != data:
            raise InventoryArtifactError(f"Visual edit artifact conflict: {relative}")
        return relative
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative


def save_pointer_json(project_root: Path, relative_path: str, payload: object) -> str:
    relative = assert_editing_relative_path(relative_path)
    target = resolve_editing_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative


def cleanup_visual_edit_temp(project_root: Path, *, run_id: str) -> None:
    temp = visual_edit_temp_dir(project_root, run_id)
    if temp.exists():
        shutil.rmtree(temp)


def save_plan_json(project_root: Path, bundle: VisualEditPlanBundle) -> str:
    return save_json_artifact(
        project_root,
        visual_edit_plan_json_relative_path(bundle.plan.plan_id),
        bundle.model_dump(mode="json"),
    )


def save_humanity_review_json(project_root: Path, bundle: HumanityReviewBundle) -> str:
    return save_json_artifact(
        project_root,
        humanity_review_json_relative_path(bundle.review.review_id),
        bundle.model_dump(mode="json"),
    )


def save_feasibility_report_json(project_root: Path, bundle: FeasibilityReportBundle) -> str:
    return save_json_artifact(
        project_root,
        feasibility_report_json_relative_path(bundle.report.report_id),
        bundle.model_dump(mode="json"),
    )


def save_repair_run_json(project_root: Path, run: RepairRun, result: RepairResult | None) -> str:
    return save_json_artifact(
        project_root,
        repair_run_json_relative_path(run.run_id),
        {"run": run.model_dump(mode="json"), "result": None if result is None else result.model_dump(mode="json")},
    )


def save_run_report(project_root: Path, run_id: str, payload: object) -> str:
    return save_pointer_json(project_root, visual_edit_report_relative_path(run_id), payload)


def get_project_state(conn: sqlite3.Connection, *, project_id: str) -> VisualEditProjectState | None:
    row = conn.execute(
        "SELECT * FROM visual_edit_project_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_project_state(row)


def upsert_project_state(conn: sqlite3.Connection, state: VisualEditProjectState) -> None:
    conn.execute(
        """
        INSERT INTO visual_edit_project_state (
            project_id, current_visual_edit_plan_id, current_humanity_review_id,
            current_feasibility_report_id, current_repair_run_id,
            current_script_lock_id, current_narration_timeline_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            current_visual_edit_plan_id = excluded.current_visual_edit_plan_id,
            current_humanity_review_id = excluded.current_humanity_review_id,
            current_feasibility_report_id = excluded.current_feasibility_report_id,
            current_repair_run_id = excluded.current_repair_run_id,
            current_script_lock_id = excluded.current_script_lock_id,
            current_narration_timeline_id = excluded.current_narration_timeline_id,
            updated_at = excluded.updated_at
        """,
        (
            state.project_id,
            state.current_visual_edit_plan_id,
            state.current_humanity_review_id,
            state.current_feasibility_report_id,
            state.current_repair_run_id,
            state.current_script_lock_id,
            state.current_narration_timeline_id,
            state.updated_at.isoformat(),
        ),
    )


def insert_visual_edit_run(conn: sqlite3.Connection, run: VisualEditRun) -> None:
    conn.execute(
        """
        INSERT INTO visual_edit_runs (
            run_id, project_id, scope, status, script_lock_id, narration_timeline_id,
            plan_id, input_fingerprint, error_code, error_message, relative_report_path,
            created_at, started_at, finished_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _run_values(run),
    )


def update_visual_edit_run(conn: sqlite3.Connection, run: VisualEditRun) -> None:
    conn.execute(
        """
        UPDATE visual_edit_runs SET
            scope = ?, status = ?, script_lock_id = ?, narration_timeline_id = ?,
            plan_id = ?, input_fingerprint = ?, error_code = ?, error_message = ?,
            relative_report_path = ?, started_at = ?, finished_at = ?, schema_version = ?
        WHERE run_id = ?
        """,
        (
            run.scope,
            run.status.value,
            run.script_lock_id,
            run.narration_timeline_id,
            run.plan_id,
            run.input_fingerprint,
            run.error_code,
            run.error_message,
            run.relative_report_path,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.schema_version,
            run.run_id,
        ),
    )


def get_visual_edit_run(conn: sqlite3.Connection, *, run_id: str) -> VisualEditRun | None:
    row = conn.execute("SELECT * FROM visual_edit_runs WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _row_to_run(row)


def list_visual_edit_runs(conn: sqlite3.Connection, *, project_id: str) -> list[VisualEditRun]:
    rows = conn.execute(
        """
        SELECT * FROM visual_edit_runs
        WHERE project_id = ?
        ORDER BY created_at DESC, run_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def find_active_visual_edit_run(conn: sqlite3.Connection, *, project_id: str) -> VisualEditRun | None:
    row = conn.execute(
        """
        SELECT * FROM visual_edit_runs
        WHERE project_id = ? AND status IN (?, ?)
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            project_id,
            VisualEditRunStatus.QUEUED.value,
            VisualEditRunStatus.RUNNING.value,
        ),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def next_plan_version(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(plan_version) AS version FROM visual_edit_plans WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["version"] or 0) + 1


def insert_plan_bundle(conn: sqlite3.Connection, bundle: VisualEditPlanBundle, artifact_relpath: str) -> None:
    assert_editing_relative_path(artifact_relpath)
    plan = bundle.plan
    conn.execute(
        """
        INSERT INTO visual_edit_plans (
            plan_id, project_id, script_lock_id, narration_timeline_id,
            input_fingerprint, plan_version, gateway_version, model_id,
            prompt_version, schema_version, status, total_shot_count,
            expected_visual_duration_seconds, accepted_risks_json, created_at,
            artifact_relpath
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.plan_id,
            plan.project_id,
            plan.script_lock_id,
            plan.narration_timeline_id,
            plan.input_fingerprint,
            plan.plan_version,
            plan.gateway_version,
            plan.model_id,
            plan.prompt_version,
            plan.schema_version,
            plan.status,
            plan.total_shot_count,
            plan.expected_visual_duration_seconds,
            _json([risk.model_dump(mode="json") for risk in plan.accepted_risks]),
            plan.created_at.isoformat(),
            artifact_relpath,
        ),
    )
    for shot in bundle.shots:
        insert_editorial_shot(conn, shot)
    for assignment in bundle.assignments:
        insert_shot_media_assignment(conn, assignment)
    for transition in bundle.transitions:
        insert_shot_transition(conn, transition)


def update_plan_status(conn: sqlite3.Connection, *, plan_id: str, status: str) -> None:
    conn.execute("UPDATE visual_edit_plans SET status = ? WHERE plan_id = ?", (status, plan_id))


def insert_editorial_shot(conn: sqlite3.Connection, shot: EditorialShot) -> None:
    conn.execute(
        """
        INSERT INTO editorial_shots (
            shot_id, plan_id, ordinal, shot_function, timeline_start_seconds,
            timeline_end_seconds, duration_seconds, timeline_start_frame,
            timeline_end_frame, transition_intent, continuity_intent,
            rhythm_intent, media_strategy, priority, uncertainty_notes_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shot.shot_id,
            shot.plan_id,
            shot.ordinal,
            shot.shot_function,
            shot.timeline_start_seconds,
            shot.timeline_end_seconds,
            shot.duration_seconds,
            shot.timeline_start_frame,
            shot.timeline_end_frame,
            shot.transition_intent,
            shot.continuity_intent,
            shot.rhythm_intent,
            shot.media_strategy,
            shot.priority,
            _json(shot.uncertainty_notes),
            shot.status,
        ),
    )
    _insert_join_rows(conn, "editorial_shot_narration_entries", "narration_entry_id", shot.shot_id, shot.narration_entry_ids)
    _insert_join_rows(conn, "editorial_shot_sentences", "sentence_id", shot.shot_id, shot.sentence_ids)
    _insert_join_rows(conn, "editorial_shot_visual_beats", "visual_beat_id", shot.shot_id, shot.visual_beat_ids)
    _insert_join_rows(conn, "editorial_shot_visual_intents", "visual_intent_id", shot.shot_id, shot.visual_intent_ids)


def _insert_join_rows(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    shot_id: str,
    values: list[str],
) -> None:
    for value in values:
        conn.execute(
            f"INSERT OR IGNORE INTO {table} (shot_id, {column}) VALUES (?, ?)",
            (shot_id, value),
        )


def insert_shot_media_assignment(conn: sqlite3.Connection, assignment: ShotMediaAssignment) -> None:
    conn.execute(
        """
        INSERT INTO shot_media_assignments (
            assignment_id, shot_id, asset_id, working_media_id, technical_shot_id,
            visual_observation_id, assignment_priority, source_range_intent_json,
            technical_source_in_seconds, technical_source_out_seconds,
            technical_source_in_frame, technical_source_out_frame, duration_seconds,
            selection_rationale, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment.assignment_id,
            assignment.shot_id,
            assignment.asset_id,
            assignment.working_media_id,
            assignment.technical_shot_id,
            assignment.visual_observation_id,
            assignment.assignment_priority,
            _json(assignment.source_range_intent.model_dump(mode="json")),
            assignment.technical_source_in_seconds,
            assignment.technical_source_out_seconds,
            assignment.technical_source_in_frame,
            assignment.technical_source_out_frame,
            assignment.duration_seconds,
            assignment.selection_rationale,
            assignment.status,
        ),
    )


def update_shot_media_assignment(conn: sqlite3.Connection, assignment: ShotMediaAssignment) -> None:
    conn.execute(
        """
        UPDATE shot_media_assignments SET
            asset_id = ?, working_media_id = ?, technical_shot_id = ?,
            visual_observation_id = ?, source_range_intent_json = ?,
            technical_source_in_seconds = ?, technical_source_out_seconds = ?,
            technical_source_in_frame = ?, technical_source_out_frame = ?,
            duration_seconds = ?, selection_rationale = ?, status = ?
        WHERE assignment_id = ?
        """,
        (
            assignment.asset_id,
            assignment.working_media_id,
            assignment.technical_shot_id,
            assignment.visual_observation_id,
            _json(assignment.source_range_intent.model_dump(mode="json")),
            assignment.technical_source_in_seconds,
            assignment.technical_source_out_seconds,
            assignment.technical_source_in_frame,
            assignment.technical_source_out_frame,
            assignment.duration_seconds,
            assignment.selection_rationale,
            assignment.status,
            assignment.assignment_id,
        ),
    )


def insert_shot_transition(conn: sqlite3.Connection, transition: ShotTransition) -> None:
    conn.execute(
        """
        INSERT INTO shot_transitions (
            transition_id, plan_id, from_shot_id, to_shot_id, editorial_function,
            technical_type, desired_duration_seconds, resolved_duration_seconds, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition.transition_id,
            transition.plan_id,
            transition.from_shot_id,
            transition.to_shot_id,
            transition.editorial_function,
            transition.technical_type,
            transition.desired_duration_seconds,
            transition.resolved_duration_seconds,
            transition.status,
        ),
    )


def get_plan(conn: sqlite3.Connection, *, plan_id: str) -> VisualEditPlan | None:
    row = conn.execute("SELECT * FROM visual_edit_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return None if row is None else _row_to_plan(row)


def get_plan_bundle(conn: sqlite3.Connection, *, plan_id: str) -> VisualEditPlanBundle | None:
    plan = get_plan(conn, plan_id=plan_id)
    if plan is None:
        return None
    return VisualEditPlanBundle(
        plan=plan,
        shots=list_editorial_shots(conn, plan_id=plan_id),
        assignments=list_assignments_for_plan(conn, plan_id=plan_id),
        transitions=list_transitions(conn, plan_id=plan_id),
    )


def list_plans(conn: sqlite3.Connection, *, project_id: str) -> list[VisualEditPlan]:
    rows = conn.execute(
        """
        SELECT * FROM visual_edit_plans
        WHERE project_id = ?
        ORDER BY plan_version DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_plan(row) for row in rows]


def list_editorial_shots(conn: sqlite3.Connection, *, plan_id: str) -> list[EditorialShot]:
    rows = conn.execute(
        "SELECT * FROM editorial_shots WHERE plan_id = ? ORDER BY ordinal",
        (plan_id,),
    ).fetchall()
    return [_row_to_shot(conn, row) for row in rows]


def list_assignments_for_plan(conn: sqlite3.Connection, *, plan_id: str) -> list[ShotMediaAssignment]:
    rows = conn.execute(
        """
        SELECT a.*
        FROM shot_media_assignments a
        JOIN editorial_shots s ON s.shot_id = a.shot_id
        WHERE s.plan_id = ?
        ORDER BY s.ordinal, a.assignment_priority
        """,
        (plan_id,),
    ).fetchall()
    return [_row_to_assignment(row) for row in rows]


def list_transitions(conn: sqlite3.Connection, *, plan_id: str) -> list[ShotTransition]:
    rows = conn.execute(
        "SELECT * FROM shot_transitions WHERE plan_id = ? ORDER BY rowid",
        (plan_id,),
    ).fetchall()
    return [_row_to_transition(row) for row in rows]


def mark_current_plan(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_lock_id: str,
    narration_timeline_id: str,
    plan_id: str,
) -> None:
    state = get_project_state(conn, project_id=project_id) or VisualEditProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_visual_edit_plan_id": plan_id,
                "current_humanity_review_id": None,
                "current_feasibility_report_id": None,
                "current_script_lock_id": script_lock_id,
                "current_narration_timeline_id": narration_timeline_id,
                "updated_at": _now(),
            }
        ),
    )


def mark_current_humanity_review(conn: sqlite3.Connection, *, project_id: str, review_id: str) -> None:
    state = get_project_state(conn, project_id=project_id) or VisualEditProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(update={"current_humanity_review_id": review_id, "updated_at": _now()}),
    )


def mark_current_feasibility_report(conn: sqlite3.Connection, *, project_id: str, report_id: str) -> None:
    state = get_project_state(conn, project_id=project_id) or VisualEditProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(update={"current_feasibility_report_id": report_id, "updated_at": _now()}),
    )


def mark_current_repair_run(conn: sqlite3.Connection, *, project_id: str, run_id: str) -> None:
    state = get_project_state(conn, project_id=project_id) or VisualEditProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(update={"current_repair_run_id": run_id, "updated_at": _now()}),
    )


def write_latest_plan_pointer(project_root: Path, plan: VisualEditPlan) -> str:
    return save_pointer_json(
        project_root,
        latest_visual_edit_plan_relative_path(),
        {"plan_id": plan.plan_id, "status": plan.status, "plan_version": plan.plan_version},
    )


def write_latest_humanity_pointer(project_root: Path, review: HumanityReview) -> str:
    return save_pointer_json(
        project_root,
        latest_humanity_review_relative_path(),
        {"review_id": review.review_id, "status": review.status, "plan_id": review.visual_edit_plan_id},
    )


def write_latest_feasibility_pointer(project_root: Path, report: FeasibilityReport) -> str:
    return save_pointer_json(
        project_root,
        latest_feasibility_report_relative_path(),
        {
            "report_id": report.report_id,
            "status": report.status,
            "plan_id": report.plan_id,
            "assessment": report.overall_technical_assessment,
        },
    )


def write_latest_repair_pointer(project_root: Path, run: RepairRun) -> str:
    return save_pointer_json(
        project_root,
        latest_repair_run_relative_path(),
        {"run_id": run.run_id, "status": run.status, "input_plan_id": run.input_plan_id},
    )


def insert_humanity_review_bundle(
    conn: sqlite3.Connection,
    bundle: HumanityReviewBundle,
    artifact_relpath: str,
) -> None:
    assert_editing_relative_path(artifact_relpath)
    review = bundle.review
    conn.execute(
        """
        INSERT INTO humanity_reviews (
            review_id, visual_edit_plan_id, review_version, input_fingerprint,
            status, overall_judgment, deterministic_signals_json, created_at,
            artifact_relpath
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review.review_id,
            review.visual_edit_plan_id,
            review.review_version,
            review.input_fingerprint,
            review.status,
            review.overall_judgment,
            _json(review.deterministic_signals),
            review.created_at.isoformat(),
            artifact_relpath,
        ),
    )
    for finding in bundle.findings:
        insert_humanity_finding(conn, finding)


def insert_humanity_finding(conn: sqlite3.Connection, finding: HumanityFinding) -> None:
    conn.execute(
        """
        INSERT INTO humanity_findings (
            finding_id, review_id, shot_id, plan_level, category, severity,
            rationale, evidence_refs_json, recommended_action, user_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding.finding_id,
            finding.review_id,
            finding.shot_id,
            1 if finding.plan_level else 0,
            finding.category,
            finding.severity,
            finding.rationale,
            _json(finding.evidence_refs),
            finding.recommended_action,
            finding.user_status,
        ),
    )


def update_humanity_finding_status(
    conn: sqlite3.Connection,
    *,
    finding_id: str,
    user_status: str,
) -> None:
    conn.execute(
        "UPDATE humanity_findings SET user_status = ? WHERE finding_id = ?",
        (user_status, finding_id),
    )


def update_humanity_review_status(conn: sqlite3.Connection, *, review_id: str, status: str) -> None:
    conn.execute("UPDATE humanity_reviews SET status = ? WHERE review_id = ?", (status, review_id))


def get_humanity_review(conn: sqlite3.Connection, *, review_id: str) -> HumanityReview | None:
    row = conn.execute("SELECT * FROM humanity_reviews WHERE review_id = ?", (review_id,)).fetchone()
    return None if row is None else _row_to_humanity_review(row)


def get_humanity_review_bundle(
    conn: sqlite3.Connection, *, review_id: str
) -> HumanityReviewBundle | None:
    review = get_humanity_review(conn, review_id=review_id)
    if review is None:
        return None
    return HumanityReviewBundle(
        review=review,
        findings=list_humanity_findings(conn, review_id=review_id),
    )


def list_humanity_findings(conn: sqlite3.Connection, *, review_id: str) -> list[HumanityFinding]:
    rows = conn.execute(
        "SELECT * FROM humanity_findings WHERE review_id = ? ORDER BY rowid",
        (review_id,),
    ).fetchall()
    return [_row_to_humanity_finding(row) for row in rows]


def insert_feasibility_report_bundle(
    conn: sqlite3.Connection,
    bundle: FeasibilityReportBundle,
    artifact_relpath: str,
) -> None:
    assert_editing_relative_path(artifact_relpath)
    report = bundle.report
    conn.execute(
        """
        INSERT INTO feasibility_reports (
            report_id, plan_id, input_fingerprint, timebase, status,
            overall_technical_assessment, metrics_json, created_at, artifact_relpath
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report.report_id,
            report.plan_id,
            report.input_fingerprint,
            report.timebase,
            report.status,
            report.overall_technical_assessment,
            _json(report.metrics),
            report.created_at.isoformat(),
            artifact_relpath,
        ),
    )
    for issue in bundle.issues:
        insert_feasibility_issue(conn, issue)


def insert_feasibility_issue(conn: sqlite3.Connection, issue: FeasibilityIssue) -> None:
    conn.execute(
        """
        INSERT INTO feasibility_issues (
            issue_id, report_id, shot_id, assignment_id, error_code, severity,
            technical_details, deterministically_repairable, blocks_phase_13
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            issue.issue_id,
            issue.report_id,
            issue.shot_id,
            issue.assignment_id,
            issue.error_code,
            issue.severity,
            issue.technical_details,
            1 if issue.deterministically_repairable else 0,
            1 if issue.blocks_phase_13 else 0,
        ),
    )


def update_feasibility_report_status(conn: sqlite3.Connection, *, report_id: str, status: str) -> None:
    conn.execute("UPDATE feasibility_reports SET status = ? WHERE report_id = ?", (status, report_id))


def get_feasibility_report(conn: sqlite3.Connection, *, report_id: str) -> FeasibilityReport | None:
    row = conn.execute("SELECT * FROM feasibility_reports WHERE report_id = ?", (report_id,)).fetchone()
    return None if row is None else _row_to_feasibility_report(row)


def get_feasibility_report_bundle(
    conn: sqlite3.Connection, *, report_id: str
) -> FeasibilityReportBundle | None:
    report = get_feasibility_report(conn, report_id=report_id)
    if report is None:
        return None
    return FeasibilityReportBundle(
        report=report,
        issues=list_feasibility_issues(conn, report_id=report_id),
    )


def list_feasibility_issues(conn: sqlite3.Connection, *, report_id: str) -> list[FeasibilityIssue]:
    rows = conn.execute(
        "SELECT * FROM feasibility_issues WHERE report_id = ? ORDER BY rowid",
        (report_id,),
    ).fetchall()
    return [_row_to_feasibility_issue(row) for row in rows]


def insert_repair_proposal(conn: sqlite3.Connection, proposal: RepairProposal) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO repair_proposals (
            proposal_id, plan_id, humanity_review_id, feasibility_report_id, source,
            repair_type, affected_ids_json, description, expected_effect,
            user_status, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal.proposal_id,
            proposal.plan_id,
            proposal.humanity_review_id,
            proposal.feasibility_report_id,
            proposal.source,
            proposal.repair_type,
            _json(proposal.affected_ids),
            proposal.description,
            proposal.expected_effect,
            proposal.user_status,
            proposal.version,
        ),
    )


def update_repair_proposal_status(conn: sqlite3.Connection, *, proposal_id: str, status: str) -> None:
    conn.execute(
        "UPDATE repair_proposals SET user_status = ? WHERE proposal_id = ?",
        (status, proposal_id),
    )


def get_repair_proposal(conn: sqlite3.Connection, *, proposal_id: str) -> RepairProposal | None:
    row = conn.execute("SELECT * FROM repair_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return None if row is None else _row_to_repair_proposal(row)


def list_repair_proposals(conn: sqlite3.Connection, *, plan_id: str) -> list[RepairProposal]:
    rows = conn.execute(
        "SELECT * FROM repair_proposals WHERE plan_id = ? ORDER BY version, proposal_id",
        (plan_id,),
    ).fetchall()
    return [_row_to_repair_proposal(row) for row in rows]


def insert_repair_run(conn: sqlite3.Connection, run: RepairRun) -> None:
    conn.execute(
        """
        INSERT INTO repair_runs (
            run_id, input_plan_id, selected_proposal_ids_json, output_plan_id,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.input_plan_id,
            _json(run.selected_proposal_ids),
            run.output_plan_id,
            run.status,
            run.created_at.isoformat(),
        ),
    )


def update_repair_run(conn: sqlite3.Connection, run: RepairRun) -> None:
    conn.execute(
        """
        UPDATE repair_runs SET
            selected_proposal_ids_json = ?, output_plan_id = ?, status = ?
        WHERE run_id = ?
        """,
        (_json(run.selected_proposal_ids), run.output_plan_id, run.status, run.run_id),
    )


def get_repair_run(conn: sqlite3.Connection, *, run_id: str) -> RepairRun | None:
    row = conn.execute("SELECT * FROM repair_runs WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _row_to_repair_run(row)


def insert_repair_result(conn: sqlite3.Connection, result: RepairResult) -> None:
    conn.execute(
        """
        INSERT INTO repair_results (
            result_id, run_id, changes_json, remaining_findings_json,
            remaining_feasibility_issues_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            result.result_id,
            result.run_id,
            _json(result.changes),
            _json(result.remaining_findings),
            _json(result.remaining_feasibility_issues),
            result.created_at.isoformat(),
        ),
    )


def get_repair_result(conn: sqlite3.Connection, *, run_id: str) -> RepairResult | None:
    row = conn.execute("SELECT * FROM repair_results WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _row_to_repair_result(row)


def _row_to_project_state(row: sqlite3.Row) -> VisualEditProjectState:
    return VisualEditProjectState(
        project_id=str(row["project_id"]),
        current_visual_edit_plan_id=row["current_visual_edit_plan_id"],
        current_humanity_review_id=row["current_humanity_review_id"],
        current_feasibility_report_id=row["current_feasibility_report_id"],
        current_repair_run_id=row["current_repair_run_id"],
        current_script_lock_id=row["current_script_lock_id"],
        current_narration_timeline_id=row["current_narration_timeline_id"],
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _run_values(run: VisualEditRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.project_id,
        run.scope,
        run.status.value,
        run.script_lock_id,
        run.narration_timeline_id,
        run.plan_id,
        run.input_fingerprint,
        run.error_code,
        run.error_message,
        run.relative_report_path,
        run.created_at.isoformat(),
        None if run.started_at is None else run.started_at.isoformat(),
        None if run.finished_at is None else run.finished_at.isoformat(),
        run.schema_version,
    )


def _row_to_run(row: sqlite3.Row) -> VisualEditRun:
    return VisualEditRun(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        status=VisualEditRunStatus(str(row["status"])),
        script_lock_id=row["script_lock_id"],
        narration_timeline_id=row["narration_timeline_id"],
        plan_id=row["plan_id"],
        input_fingerprint=row["input_fingerprint"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        relative_report_path=row["relative_report_path"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        schema_version=str(row["schema_version"]),
    )


def _row_to_plan(row: sqlite3.Row) -> VisualEditPlan:
    return VisualEditPlan(
        plan_id=str(row["plan_id"]),
        project_id=str(row["project_id"]),
        script_lock_id=str(row["script_lock_id"]),
        narration_timeline_id=str(row["narration_timeline_id"]),
        input_fingerprint=str(row["input_fingerprint"]),
        plan_version=int(row["plan_version"]),
        gateway_version=str(row["gateway_version"]),
        model_id=str(row["model_id"]),
        prompt_version=str(row["prompt_version"]),
        schema_version=str(row["schema_version"]),
        status=str(row["status"]),
        total_shot_count=int(row["total_shot_count"]),
        expected_visual_duration_seconds=float(row["expected_visual_duration_seconds"]),
        accepted_risks=[
            AcceptedRiskRef.model_validate(item)
            for item in _loads(row["accepted_risks_json"], [])
        ],
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _join_values(conn: sqlite3.Connection, table: str, column: str, shot_id: str) -> list[str]:
    rows = conn.execute(
        f"SELECT {column} AS value FROM {table} WHERE shot_id = ? ORDER BY rowid",
        (shot_id,),
    ).fetchall()
    return [str(row["value"]) for row in rows]


def _row_to_shot(conn: sqlite3.Connection, row: sqlite3.Row) -> EditorialShot:
    shot_id = str(row["shot_id"])
    return EditorialShot(
        shot_id=shot_id,
        plan_id=str(row["plan_id"]),
        ordinal=int(row["ordinal"]),
        shot_function=str(row["shot_function"]),
        narration_entry_ids=_join_values(conn, "editorial_shot_narration_entries", "narration_entry_id", shot_id),
        sentence_ids=_join_values(conn, "editorial_shot_sentences", "sentence_id", shot_id),
        visual_beat_ids=_join_values(conn, "editorial_shot_visual_beats", "visual_beat_id", shot_id),
        visual_intent_ids=_join_values(conn, "editorial_shot_visual_intents", "visual_intent_id", shot_id),
        timeline_start_seconds=float(row["timeline_start_seconds"]),
        timeline_end_seconds=float(row["timeline_end_seconds"]),
        duration_seconds=float(row["duration_seconds"]),
        timeline_start_frame=int(row["timeline_start_frame"]),
        timeline_end_frame=int(row["timeline_end_frame"]),
        transition_intent=row["transition_intent"],
        continuity_intent=row["continuity_intent"],
        rhythm_intent=row["rhythm_intent"],
        media_strategy=str(row["media_strategy"]),
        priority=int(row["priority"]),
        uncertainty_notes=list(_loads(row["uncertainty_notes_json"], [])),
        status=str(row["status"]),
    )


def _row_to_assignment(row: sqlite3.Row) -> ShotMediaAssignment:
    return ShotMediaAssignment(
        assignment_id=str(row["assignment_id"]),
        shot_id=str(row["shot_id"]),
        asset_id=row["asset_id"],
        working_media_id=row["working_media_id"],
        technical_shot_id=row["technical_shot_id"],
        visual_observation_id=row["visual_observation_id"],
        assignment_priority=int(row["assignment_priority"]),
        source_range_intent=SourceRangeIntent.model_validate(_loads(row["source_range_intent_json"], {})),
        technical_source_in_seconds=(
            None if row["technical_source_in_seconds"] is None else float(row["technical_source_in_seconds"])
        ),
        technical_source_out_seconds=(
            None if row["technical_source_out_seconds"] is None else float(row["technical_source_out_seconds"])
        ),
        technical_source_in_frame=(
            None if row["technical_source_in_frame"] is None else int(row["technical_source_in_frame"])
        ),
        technical_source_out_frame=(
            None if row["technical_source_out_frame"] is None else int(row["technical_source_out_frame"])
        ),
        duration_seconds=float(row["duration_seconds"]),
        selection_rationale=str(row["selection_rationale"]),
        status=str(row["status"]),
    )


def _row_to_transition(row: sqlite3.Row) -> ShotTransition:
    return ShotTransition(
        transition_id=str(row["transition_id"]),
        plan_id=str(row["plan_id"]),
        from_shot_id=str(row["from_shot_id"]),
        to_shot_id=str(row["to_shot_id"]),
        editorial_function=str(row["editorial_function"]),
        technical_type=str(row["technical_type"]),
        desired_duration_seconds=float(row["desired_duration_seconds"]),
        resolved_duration_seconds=float(row["resolved_duration_seconds"]),
        status=str(row["status"]),
    )


def _row_to_humanity_review(row: sqlite3.Row) -> HumanityReview:
    return HumanityReview(
        review_id=str(row["review_id"]),
        visual_edit_plan_id=str(row["visual_edit_plan_id"]),
        review_version=int(row["review_version"]),
        input_fingerprint=str(row["input_fingerprint"]),
        status=str(row["status"]),
        overall_judgment=str(row["overall_judgment"]),
        deterministic_signals=dict(_loads(row["deterministic_signals_json"], {})),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_humanity_finding(row: sqlite3.Row) -> HumanityFinding:
    return HumanityFinding(
        finding_id=str(row["finding_id"]),
        review_id=str(row["review_id"]),
        shot_id=row["shot_id"],
        plan_level=bool(int(row["plan_level"])),
        category=str(row["category"]),
        severity=str(row["severity"]),
        rationale=str(row["rationale"]),
        evidence_refs=list(_loads(row["evidence_refs_json"], [])),
        recommended_action=str(row["recommended_action"]),
        user_status=str(row["user_status"]),
    )


def _row_to_feasibility_report(row: sqlite3.Row) -> FeasibilityReport:
    return FeasibilityReport(
        report_id=str(row["report_id"]),
        plan_id=str(row["plan_id"]),
        input_fingerprint=str(row["input_fingerprint"]),
        timebase=str(row["timebase"]),
        status=str(row["status"]),
        overall_technical_assessment=str(row["overall_technical_assessment"]),
        metrics=dict(_loads(row["metrics_json"], {})),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_feasibility_issue(row: sqlite3.Row) -> FeasibilityIssue:
    return FeasibilityIssue(
        issue_id=str(row["issue_id"]),
        report_id=str(row["report_id"]),
        shot_id=row["shot_id"],
        assignment_id=row["assignment_id"],
        error_code=str(row["error_code"]),
        severity=str(row["severity"]),
        technical_details=str(row["technical_details"]),
        deterministically_repairable=bool(int(row["deterministically_repairable"])),
        blocks_phase_13=bool(int(row["blocks_phase_13"])),
    )


def _row_to_repair_proposal(row: sqlite3.Row) -> RepairProposal:
    return RepairProposal(
        proposal_id=str(row["proposal_id"]),
        plan_id=str(row["plan_id"]),
        humanity_review_id=row["humanity_review_id"],
        feasibility_report_id=row["feasibility_report_id"],
        source=str(row["source"]),
        repair_type=str(row["repair_type"]),
        affected_ids=list(_loads(row["affected_ids_json"], [])),
        description=str(row["description"]),
        expected_effect=str(row["expected_effect"]),
        user_status=str(row["user_status"]),
        version=int(row["version"]),
    )


def _row_to_repair_run(row: sqlite3.Row) -> RepairRun:
    return RepairRun(
        run_id=str(row["run_id"]),
        input_plan_id=str(row["input_plan_id"]),
        selected_proposal_ids=list(_loads(row["selected_proposal_ids_json"], [])),
        output_plan_id=row["output_plan_id"],
        status=str(row["status"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_repair_result(row: sqlite3.Row) -> RepairResult:
    return RepairResult(
        result_id=str(row["result_id"]),
        run_id=str(row["run_id"]),
        changes=list(_loads(row["changes_json"], [])),
        remaining_findings=list(_loads(row["remaining_findings_json"], [])),
        remaining_feasibility_issues=list(_loads(row["remaining_feasibility_issues_json"], [])),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


__all__ = [name for name in globals() if not name.startswith("_")]
