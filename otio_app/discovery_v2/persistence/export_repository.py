"""Persistence for Discovery V2 Phase 13 export artifacts and registry rows."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.export import (
    ACTIVE_EXPORT_RUN_STATUSES,
    AcceptedExportRisk,
    EditorialApproval,
    EditorialApprovalStatus,
    ExportProjectState,
    ExportValidationIssue,
    ExportValidationReport,
    ExportValidationReportStatus,
    OtioExportArtifact,
    OtioExportRun,
    OtioExportRunStatus,
    OtioReparseReport,
    OtioReparseReportStatus,
    compute_export_sha256,
)
from otio_app.discovery_v2.export_paths import (
    assert_export_relative_path,
    editorial_approval_json_relative_path,
    export_manifest_relative_path,
    export_report_relative_path,
    export_run_json_relative_path,
    export_temp_dir,
    export_validation_json_relative_path,
    latest_approval_relative_path,
    latest_otio_export_relative_path,
    latest_reparse_relative_path,
    latest_validation_relative_path,
    otio_export_relative_path,
    otio_reparse_json_relative_path,
    resolve_export_relative_path,
)
from otio_app.discovery_v2.persistence.asset_registry_database import get_registry_connection
from otio_app.discovery_v2.persistence.editorial_repository import bind_project_root_for_json_reads
from otio_app.discovery_v2.persistence.inventory_artifact_store import InventoryArtifactError

_EXPORT_JSON_ROOT: Path | None = None


def bind_project_root_for_export_json_reads(project_root: Path) -> None:
    global _EXPORT_JSON_ROOT
    _EXPORT_JSON_ROOT = Path(project_root).expanduser().resolve()


def open_export_registry(project_root: Path) -> sqlite3.Connection:
    bind_project_root_for_json_reads(project_root)
    bind_project_root_for_export_json_reads(project_root)
    return get_registry_connection(project_root)


def new_editorial_approval_id() -> str:
    return str(uuid4())


def new_export_validation_report_id() -> str:
    return str(uuid4())


def new_export_validation_issue_id() -> str:
    return str(uuid4())


def new_otio_export_run_id() -> str:
    return str(uuid4())


def new_otio_export_artifact_id() -> str:
    return str(uuid4())


def new_otio_reparse_report_id() -> str:
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
    relative = assert_export_relative_path(relative_path)
    target = resolve_export_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if target.exists():
        if target.read_bytes() != data:
            raise InventoryArtifactError(f"Export artifact conflict: {relative}")
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
    relative = assert_export_relative_path(relative_path)
    target = resolve_export_relative_path(project_root, relative)
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


def publish_otio_file(project_root: Path, *, temp_path: Path, relative_path: str) -> tuple[str, int, str]:
    relative = assert_export_relative_path(relative_path)
    target = resolve_export_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = Path(temp_path).read_bytes()
    digest = compute_export_sha256(data)
    if target.exists():
        existing = target.read_bytes()
        if existing != data:
            raise InventoryArtifactError(f"Export artifact conflict: {relative}")
        return relative, len(existing), compute_export_sha256(existing)
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        if compute_export_sha256(tmp.read_bytes()) != digest:
            raise InventoryArtifactError("OTIO hash mismatch after temp write.")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative, len(data), digest


def cleanup_export_temp(project_root: Path, *, run_id: str) -> None:
    temp = export_temp_dir(project_root, run_id)
    if temp.exists():
        shutil.rmtree(temp)


def save_editorial_approval_json(project_root: Path, approval: EditorialApproval) -> str:
    return save_json_artifact(
        project_root,
        editorial_approval_json_relative_path(approval.approval_id),
        approval.model_dump(mode="json"),
    )


def save_validation_report_json(project_root: Path, report: ExportValidationReport) -> str:
    return save_json_artifact(
        project_root,
        export_validation_json_relative_path(report.report_id),
        report.model_dump(mode="json"),
    )


def save_export_run_json(project_root: Path, run: OtioExportRun, payload: object | None = None) -> str:
    return save_json_artifact(
        project_root,
        export_run_json_relative_path(run.run_id),
        payload or run.model_dump(mode="json"),
    )


def save_export_manifest_json(project_root: Path, run_id: str, payload: object) -> str:
    return save_json_artifact(project_root, export_manifest_relative_path(run_id), payload)


def save_reparse_report_json(project_root: Path, report: OtioReparseReport) -> str:
    return save_json_artifact(
        project_root,
        otio_reparse_json_relative_path(report.report_id),
        report.model_dump(mode="json"),
    )


def save_run_report(project_root: Path, run_id: str, payload: object) -> str:
    return save_pointer_json(project_root, export_report_relative_path(run_id), payload)


def get_project_state(conn: sqlite3.Connection, *, project_id: str) -> ExportProjectState | None:
    row = conn.execute(
        "SELECT * FROM export_project_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_project_state(row)


def upsert_project_state(conn: sqlite3.Connection, state: ExportProjectState) -> None:
    conn.execute(
        """
        INSERT INTO export_project_state (
            project_id, current_editorial_approval_id, current_export_validation_report_id,
            current_otio_export_run_id, current_otio_artifact_id, current_reparse_report_id,
            current_visual_edit_plan_id, current_narration_timeline_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            current_editorial_approval_id = excluded.current_editorial_approval_id,
            current_export_validation_report_id = excluded.current_export_validation_report_id,
            current_otio_export_run_id = excluded.current_otio_export_run_id,
            current_otio_artifact_id = excluded.current_otio_artifact_id,
            current_reparse_report_id = excluded.current_reparse_report_id,
            current_visual_edit_plan_id = excluded.current_visual_edit_plan_id,
            current_narration_timeline_id = excluded.current_narration_timeline_id,
            updated_at = excluded.updated_at
        """,
        (
            state.project_id,
            state.current_editorial_approval_id,
            state.current_export_validation_report_id,
            state.current_otio_export_run_id,
            state.current_otio_artifact_id,
            state.current_reparse_report_id,
            state.current_visual_edit_plan_id,
            state.current_narration_timeline_id,
            state.updated_at.isoformat(),
        ),
    )


def next_approval_revision(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(revision) AS revision FROM editorial_approvals WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["revision"] or 0) + 1


def insert_editorial_approval(conn: sqlite3.Connection, approval: EditorialApproval, relative_json_path: str) -> None:
    assert_export_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO editorial_approvals (
            approval_id, project_id, visual_edit_plan_id, humanity_review_id,
            feasibility_report_id, script_lock_id, narration_timeline_id,
            input_fingerprint, user_decision, user_comment, confirmation_checked,
            status, revision, relative_json_path, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approval.approval_id,
            approval.project_id,
            approval.visual_edit_plan_id,
            approval.humanity_review_id,
            approval.feasibility_report_id,
            approval.script_lock_id,
            approval.narration_timeline_id,
            approval.input_fingerprint,
            approval.user_decision,
            approval.user_comment,
            1 if approval.confirmation_checked else 0,
            approval.status.value,
            approval.revision,
            relative_json_path,
            approval.created_at.isoformat(),
            approval.schema_version,
        ),
    )
    for ordinal, risk in enumerate(approval.accepted_visible_risks):
        insert_editorial_approval_risk(conn, approval_id=approval.approval_id, ordinal=ordinal, risk=risk)


def insert_editorial_approval_risk(
    conn: sqlite3.Connection,
    *,
    approval_id: str,
    ordinal: int,
    risk: AcceptedExportRisk,
) -> None:
    conn.execute(
        """
        INSERT INTO editorial_approval_risks (
            approval_id, ordinal, risk_id, category, description, source_ref
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (approval_id, ordinal, risk.risk_id, risk.category, risk.description, risk.source_ref),
    )


def update_editorial_approval_status(
    conn: sqlite3.Connection,
    *,
    approval_id: str,
    status: EditorialApprovalStatus,
) -> None:
    conn.execute(
        "UPDATE editorial_approvals SET status = ? WHERE approval_id = ?",
        (status.value, approval_id),
    )


def get_editorial_approval(conn: sqlite3.Connection, *, approval_id: str) -> EditorialApproval | None:
    row = conn.execute(
        "SELECT * FROM editorial_approvals WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_approval(row, list_approval_risks(conn, approval_id=approval_id))


def list_editorial_approvals(conn: sqlite3.Connection, *, project_id: str) -> list[EditorialApproval]:
    rows = conn.execute(
        """
        SELECT * FROM editorial_approvals
        WHERE project_id = ?
        ORDER BY revision DESC, created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [
        _row_to_approval(row, list_approval_risks(conn, approval_id=str(row["approval_id"])))
        for row in rows
    ]


def list_approval_risks(conn: sqlite3.Connection, *, approval_id: str) -> list[AcceptedExportRisk]:
    rows = conn.execute(
        """
        SELECT * FROM editorial_approval_risks
        WHERE approval_id = ?
        ORDER BY ordinal
        """,
        (approval_id,),
    ).fetchall()
    return [_row_to_risk(row) for row in rows]


def mark_current_approval(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    approval_id: str,
    visual_edit_plan_id: str,
    narration_timeline_id: str,
) -> None:
    state = get_project_state(conn, project_id=project_id) or ExportProjectState(project_id=project_id, updated_at=_now())
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_editorial_approval_id": approval_id,
                "current_export_validation_report_id": None,
                "current_otio_export_run_id": None,
                "current_otio_artifact_id": None,
                "current_reparse_report_id": None,
                "current_visual_edit_plan_id": visual_edit_plan_id,
                "current_narration_timeline_id": narration_timeline_id,
                "updated_at": _now(),
            }
        ),
    )


def insert_validation_report(conn: sqlite3.Connection, report: ExportValidationReport, relative_json_path: str) -> None:
    assert_export_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO export_validation_reports (
            report_id, approval_id, visual_edit_plan_id, input_fingerprint,
            otio_profile_version, timebase, status, issues_json, metrics_json,
            relative_json_path, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report.report_id,
            report.approval_id,
            report.visual_edit_plan_id,
            report.input_fingerprint,
            report.otio_profile_version,
            report.timebase,
            report.status.value,
            _json([issue.model_dump(mode="json") for issue in report.issues]),
            _json(report.metrics),
            relative_json_path,
            report.created_at.isoformat(),
            report.schema_version,
        ),
    )
    for issue in report.issues:
        insert_validation_issue(conn, issue)


def insert_validation_issue(conn: sqlite3.Connection, issue: ExportValidationIssue) -> None:
    conn.execute(
        """
        INSERT INTO export_validation_issues (
            issue_id, report_id, shot_id, assignment_id, error_code,
            severity, technical_details, blocks_export
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            issue.issue_id,
            issue.report_id,
            issue.shot_id,
            issue.assignment_id,
            issue.error_code,
            issue.severity.value,
            issue.technical_details,
            1 if issue.blocks_export else 0,
        ),
    )


def update_validation_report_status(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    status: ExportValidationReportStatus,
) -> None:
    conn.execute(
        "UPDATE export_validation_reports SET status = ? WHERE report_id = ?",
        (status.value, report_id),
    )


def get_validation_report(conn: sqlite3.Connection, *, report_id: str) -> ExportValidationReport | None:
    row = conn.execute(
        "SELECT * FROM export_validation_reports WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_validation_report(row, list_validation_issues(conn, report_id=report_id))


def list_validation_issues(conn: sqlite3.Connection, *, report_id: str) -> list[ExportValidationIssue]:
    rows = conn.execute(
        "SELECT * FROM export_validation_issues WHERE report_id = ? ORDER BY rowid",
        (report_id,),
    ).fetchall()
    return [_row_to_validation_issue(row) for row in rows]


def mark_current_validation_report(conn: sqlite3.Connection, *, project_id: str, report_id: str) -> None:
    state = get_project_state(conn, project_id=project_id) or ExportProjectState(project_id=project_id, updated_at=_now())
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_export_validation_report_id": report_id,
                "current_otio_export_run_id": None,
                "current_otio_artifact_id": None,
                "current_reparse_report_id": None,
                "updated_at": _now(),
            }
        ),
    )


def insert_otio_export_run(conn: sqlite3.Connection, run: OtioExportRun) -> None:
    conn.execute(
        """
        INSERT INTO otio_export_runs (
            run_id, project_id, approval_id, validation_report_id, visual_edit_plan_id,
            export_profile_version, input_fingerprint, output_relative_path, otio_sha256,
            status, error_code, error_message, relative_report_path,
            created_at, started_at, finished_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _run_values(run),
    )


def update_otio_export_run(conn: sqlite3.Connection, run: OtioExportRun) -> None:
    conn.execute(
        """
        UPDATE otio_export_runs SET
            approval_id = ?, validation_report_id = ?, visual_edit_plan_id = ?,
            export_profile_version = ?, input_fingerprint = ?, output_relative_path = ?,
            otio_sha256 = ?, status = ?, error_code = ?, error_message = ?,
            relative_report_path = ?, started_at = ?, finished_at = ?, schema_version = ?
        WHERE run_id = ?
        """,
        (
            run.approval_id,
            run.validation_report_id,
            run.visual_edit_plan_id,
            run.export_profile_version,
            run.input_fingerprint,
            run.output_relative_path,
            run.otio_sha256,
            run.status.value,
            run.error_code,
            run.error_message,
            run.relative_report_path,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.schema_version,
            run.run_id,
        ),
    )


def get_otio_export_run(conn: sqlite3.Connection, *, run_id: str) -> OtioExportRun | None:
    row = conn.execute("SELECT * FROM otio_export_runs WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _row_to_run(row)


def list_otio_export_runs(conn: sqlite3.Connection, *, project_id: str) -> list[OtioExportRun]:
    rows = conn.execute(
        """
        SELECT * FROM otio_export_runs
        WHERE project_id = ?
        ORDER BY created_at DESC, run_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def find_active_export_run(conn: sqlite3.Connection, *, project_id: str) -> OtioExportRun | None:
    row = conn.execute(
        """
        SELECT * FROM otio_export_runs
        WHERE project_id = ? AND status IN (?, ?)
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            project_id,
            OtioExportRunStatus.QUEUED.value,
            OtioExportRunStatus.RUNNING.value,
        ),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def insert_otio_export_artifact(conn: sqlite3.Connection, artifact: OtioExportArtifact) -> None:
    assert_export_relative_path(artifact.relative_path)
    conn.execute(
        """
        INSERT INTO otio_export_artifacts (
            artifact_id, run_id, relative_path, byte_size, sha256,
            otio_library_version, track_count, clip_count, total_duration_seconds,
            total_frames, timebase, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact.artifact_id,
            artifact.run_id,
            artifact.relative_path,
            artifact.byte_size,
            artifact.sha256,
            artifact.otio_library_version,
            artifact.track_count,
            artifact.clip_count,
            artifact.total_duration_seconds,
            artifact.total_frames,
            artifact.timebase,
            artifact.created_at.isoformat(),
            artifact.schema_version,
        ),
    )


def get_otio_export_artifact(conn: sqlite3.Connection, *, artifact_id: str) -> OtioExportArtifact | None:
    row = conn.execute(
        "SELECT * FROM otio_export_artifacts WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    return None if row is None else _row_to_artifact(row)


def get_artifact_for_run(conn: sqlite3.Connection, *, run_id: str) -> OtioExportArtifact | None:
    row = conn.execute(
        "SELECT * FROM otio_export_artifacts WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_artifact(row)


def mark_current_otio_export(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
    artifact_id: str,
    reparse_report_id: str | None = None,
) -> None:
    state = get_project_state(conn, project_id=project_id) or ExportProjectState(project_id=project_id, updated_at=_now())
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_otio_export_run_id": run_id,
                "current_otio_artifact_id": artifact_id,
                "current_reparse_report_id": reparse_report_id,
                "updated_at": _now(),
            }
        ),
    )


def insert_reparse_report(conn: sqlite3.Connection, report: OtioReparseReport, relative_json_path: str | None = None) -> None:
    if relative_json_path is not None:
        assert_export_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO otio_reparse_reports (
            report_id, export_run_id, artifact_id, parseable, semantically_equivalent,
            deviations_json, track_count, clip_count, total_duration_seconds,
            total_frames, timebase, status, relative_json_path, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report.report_id,
            report.export_run_id,
            report.artifact_id,
            1 if report.parseable else 0,
            1 if report.semantically_equivalent else 0,
            _json(report.deviations),
            report.track_count,
            report.clip_count,
            report.total_duration_seconds,
            report.total_frames,
            report.timebase,
            report.status.value,
            relative_json_path,
            report.created_at.isoformat(),
            report.schema_version,
        ),
    )


def get_reparse_report(conn: sqlite3.Connection, *, report_id: str) -> OtioReparseReport | None:
    row = conn.execute(
        "SELECT * FROM otio_reparse_reports WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    return None if row is None else _row_to_reparse_report(row)


def write_latest_approval_pointer(project_root: Path, approval: EditorialApproval) -> str:
    return save_pointer_json(
        project_root,
        latest_approval_relative_path(),
        {
            "approval_id": approval.approval_id,
            "status": approval.status.value,
            "visual_edit_plan_id": approval.visual_edit_plan_id,
            "revision": approval.revision,
        },
    )


def write_latest_validation_pointer(project_root: Path, report: ExportValidationReport) -> str:
    return save_pointer_json(
        project_root,
        latest_validation_relative_path(),
        {
            "report_id": report.report_id,
            "status": report.status.value,
            "approval_id": report.approval_id,
            "visual_edit_plan_id": report.visual_edit_plan_id,
        },
    )


def write_latest_otio_export_pointer(project_root: Path, run: OtioExportRun, artifact: OtioExportArtifact) -> str:
    return save_pointer_json(
        project_root,
        latest_otio_export_relative_path(),
        {
            "run_id": run.run_id,
            "status": run.status.value,
            "relative_path": artifact.relative_path,
            "sha256": artifact.sha256,
        },
    )


def write_latest_reparse_pointer(project_root: Path, report: OtioReparseReport) -> str:
    return save_pointer_json(
        project_root,
        latest_reparse_relative_path(),
        {
            "report_id": report.report_id,
            "status": report.status.value,
            "export_run_id": report.export_run_id,
            "semantically_equivalent": report.semantically_equivalent,
        },
    )


def _row_to_project_state(row: sqlite3.Row) -> ExportProjectState:
    return ExportProjectState(
        project_id=str(row["project_id"]),
        current_editorial_approval_id=row["current_editorial_approval_id"],
        current_export_validation_report_id=row["current_export_validation_report_id"],
        current_otio_export_run_id=row["current_otio_export_run_id"],
        current_otio_artifact_id=row["current_otio_artifact_id"],
        current_reparse_report_id=row["current_reparse_report_id"],
        current_visual_edit_plan_id=row["current_visual_edit_plan_id"],
        current_narration_timeline_id=row["current_narration_timeline_id"],
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _row_to_approval(row: sqlite3.Row, risks: list[AcceptedExportRisk]) -> EditorialApproval:
    return EditorialApproval(
        approval_id=str(row["approval_id"]),
        project_id=str(row["project_id"]),
        visual_edit_plan_id=str(row["visual_edit_plan_id"]),
        humanity_review_id=str(row["humanity_review_id"]),
        feasibility_report_id=str(row["feasibility_report_id"]),
        script_lock_id=str(row["script_lock_id"]),
        narration_timeline_id=str(row["narration_timeline_id"]),
        input_fingerprint=str(row["input_fingerprint"]),
        user_decision=str(row["user_decision"]),
        user_comment=str(row["user_comment"] or ""),
        accepted_visible_risks=risks,
        confirmation_checked=bool(row["confirmation_checked"]),
        status=EditorialApprovalStatus(str(row["status"])),
        revision=int(row["revision"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def _row_to_risk(row: sqlite3.Row) -> AcceptedExportRisk:
    return AcceptedExportRisk(
        risk_id=str(row["risk_id"]),
        category=str(row["category"]),
        description=str(row["description"]),
        source_ref=str(row["source_ref"]),
    )


def _row_to_validation_report(row: sqlite3.Row, issues: list[ExportValidationIssue]) -> ExportValidationReport:
    return ExportValidationReport(
        report_id=str(row["report_id"]),
        approval_id=str(row["approval_id"]),
        visual_edit_plan_id=str(row["visual_edit_plan_id"]),
        input_fingerprint=str(row["input_fingerprint"]),
        otio_profile_version=str(row["otio_profile_version"]),
        timebase=str(row["timebase"]),
        status=ExportValidationReportStatus(str(row["status"])),
        issues=issues,
        metrics=dict(_loads(row["metrics_json"], {})),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def _row_to_validation_issue(row: sqlite3.Row) -> ExportValidationIssue:
    return ExportValidationIssue(
        issue_id=str(row["issue_id"]),
        report_id=str(row["report_id"]),
        shot_id=row["shot_id"],
        assignment_id=row["assignment_id"],
        error_code=str(row["error_code"]),
        severity=str(row["severity"]),
        technical_details=str(row["technical_details"]),
        blocks_export=bool(row["blocks_export"]),
    )


def _run_values(run: OtioExportRun) -> tuple:
    return (
        run.run_id,
        run.project_id,
        run.approval_id,
        run.validation_report_id,
        run.visual_edit_plan_id,
        run.export_profile_version,
        run.input_fingerprint,
        run.output_relative_path,
        run.otio_sha256,
        run.status.value,
        run.error_code,
        run.error_message,
        run.relative_report_path,
        run.created_at.isoformat(),
        None if run.started_at is None else run.started_at.isoformat(),
        None if run.finished_at is None else run.finished_at.isoformat(),
        run.schema_version,
    )


def _row_to_run(row: sqlite3.Row) -> OtioExportRun:
    return OtioExportRun(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        approval_id=str(row["approval_id"]),
        validation_report_id=str(row["validation_report_id"]),
        visual_edit_plan_id=str(row["visual_edit_plan_id"]),
        export_profile_version=str(row["export_profile_version"]),
        input_fingerprint=str(row["input_fingerprint"]),
        output_relative_path=row["output_relative_path"],
        otio_sha256=row["otio_sha256"],
        status=OtioExportRunStatus(str(row["status"])),
        error_code=row["error_code"],
        error_message=row["error_message"],
        relative_report_path=row["relative_report_path"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        schema_version=str(row["schema_version"]),
    )


def _row_to_artifact(row: sqlite3.Row) -> OtioExportArtifact:
    return OtioExportArtifact(
        artifact_id=str(row["artifact_id"]),
        run_id=str(row["run_id"]),
        relative_path=str(row["relative_path"]),
        byte_size=int(row["byte_size"]),
        sha256=str(row["sha256"]),
        otio_library_version=str(row["otio_library_version"]),
        track_count=int(row["track_count"]),
        clip_count=int(row["clip_count"]),
        total_duration_seconds=float(row["total_duration_seconds"]),
        total_frames=int(row["total_frames"]),
        timebase=str(row["timebase"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def _row_to_reparse_report(row: sqlite3.Row) -> OtioReparseReport:
    return OtioReparseReport(
        report_id=str(row["report_id"]),
        export_run_id=str(row["export_run_id"]),
        artifact_id=row["artifact_id"],
        parseable=bool(row["parseable"]),
        semantically_equivalent=bool(row["semantically_equivalent"]),
        deviations=list(_loads(row["deviations_json"], [])),
        track_count=int(row["track_count"]),
        clip_count=int(row["clip_count"]),
        total_duration_seconds=float(row["total_duration_seconds"]),
        total_frames=int(row["total_frames"]),
        timebase=str(row["timebase"]),
        status=OtioReparseReportStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def default_otio_relative_path(run_id: str) -> str:
    return otio_export_relative_path(run_id)


def default_manifest_relative_path(run_id: str) -> str:
    return export_manifest_relative_path(run_id)


__all__ = [name for name in globals() if not name.startswith("_")]
