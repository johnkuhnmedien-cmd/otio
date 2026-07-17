"""Persistence für Discovery-V2-Assetanalyse (Phase 8A — Contracts only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    AnalysisIdentityRecord,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunReport,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)


def open_analysis_registry(project_root: Path) -> sqlite3.Connection:
    return get_registry_connection(project_root)


def new_analysis_identity_id() -> str:
    return str(uuid4())


def new_analysis_run_id() -> str:
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


def find_analysis_identity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    asset_id: str,
    working_media_id: str,
    output_sha256: str,
    processing_profile_version: str,
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION,
) -> AnalysisIdentityRecord | None:
    row = conn.execute(
        """
        SELECT * FROM analysis_identities
        WHERE project_id = ?
          AND asset_id = ?
          AND working_media_id = ?
          AND output_sha256 = ?
          AND processing_profile_version = ?
          AND analysis_profile_version = ?
        """,
        (
            project_id,
            asset_id,
            working_media_id,
            output_sha256.lower(),
            processing_profile_version,
            analysis_profile_version,
        ),
    ).fetchone()
    return None if row is None else _row_to_identity(row)


def find_or_create_analysis_identity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    asset_id: str,
    working_media_id: str,
    output_sha256: str,
    processing_profile_version: str,
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION,
) -> AnalysisIdentityRecord:
    existing = find_analysis_identity(
        conn,
        project_id=project_id,
        asset_id=asset_id,
        working_media_id=working_media_id,
        output_sha256=output_sha256,
        processing_profile_version=processing_profile_version,
        analysis_profile_version=analysis_profile_version,
    )
    if existing is not None:
        return existing
    record = AnalysisIdentityRecord(
        analysis_identity_id=new_analysis_identity_id(),
        project_id=project_id,
        asset_id=asset_id,
        working_media_id=working_media_id,
        output_sha256=output_sha256.lower(),
        processing_profile_version=processing_profile_version,
        analysis_profile_version=analysis_profile_version,
        created_at=_now(),
    )
    conn.execute(
        """
        INSERT INTO analysis_identities (
            analysis_identity_id, project_id, asset_id, working_media_id,
            output_sha256, processing_profile_version, analysis_profile_version,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.analysis_identity_id,
            record.project_id,
            record.asset_id,
            record.working_media_id,
            record.output_sha256,
            record.processing_profile_version,
            record.analysis_profile_version,
            record.created_at.isoformat(),
        ),
    )
    return record


def list_analysis_identities(
    conn: sqlite3.Connection, *, project_id: str
) -> list[AnalysisIdentityRecord]:
    rows = conn.execute(
        """
        SELECT * FROM analysis_identities
        WHERE project_id = ?
        ORDER BY created_at, analysis_identity_id
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_identity(row) for row in rows]


def insert_analysis_run(conn: sqlite3.Connection, run: AnalysisRun) -> None:
    conn.execute(
        """
        INSERT INTO analysis_runs (
            run_id, project_id, scope, analysis_profile_version, status,
            created_at, started_at, completed_at, total_assets,
            prepared_assets, not_applicable_assets, failed_assets,
            interrupted_assets, error_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.project_id,
            run.scope,
            run.analysis_profile_version,
            run.status.value,
            run.created_at.isoformat(),
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.completed_at is None else run.completed_at.isoformat(),
            run.total_assets,
            run.prepared_assets,
            run.not_applicable_assets,
            run.failed_assets,
            run.interrupted_assets,
            run.error_summary,
        ),
    )


def get_analysis_run(
    conn: sqlite3.Connection, *, run_id: str
) -> AnalysisRun | None:
    row = conn.execute(
        "SELECT * FROM analysis_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def list_analysis_runs(
    conn: sqlite3.Connection, *, project_id: str
) -> list[AnalysisRun]:
    rows = conn.execute(
        """
        SELECT * FROM analysis_runs
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def insert_analysis_run_asset(
    conn: sqlite3.Connection, asset: AnalysisRunAsset
) -> None:
    conn.execute(
        """
        INSERT INTO analysis_run_assets (
            run_id, asset_id, working_media_id, validation_id, source_sha256,
            output_sha256, processing_profile_version, analysis_profile_version,
            media_kind, status, error_code, error_message, created_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset.run_id,
            asset.asset_id,
            asset.working_media_id,
            asset.validation_id,
            asset.source_sha256.lower(),
            asset.output_sha256.lower(),
            asset.processing_profile_version,
            asset.analysis_profile_version,
            asset.media_kind,
            asset.status.value,
            asset.error_code,
            asset.error_message,
            None if asset.created_at is None else asset.created_at.isoformat(),
            None if asset.completed_at is None else asset.completed_at.isoformat(),
        ),
    )


def list_analysis_run_assets(
    conn: sqlite3.Connection, *, run_id: str
) -> list[AnalysisRunAsset]:
    rows = conn.execute(
        """
        SELECT * FROM analysis_run_assets
        WHERE run_id = ?
        ORDER BY asset_id
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_run_asset(row) for row in rows]


def analysis_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE 'analysis%'
        """
    ).fetchall()
    return {str(r[0]) for r in rows}


def serialize_analysis_run_report(report: AnalysisRunReport) -> str:
    payload = report.model_dump(mode="json")
    _assert_no_absolute_paths(payload)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_analysis_run_report(raw: str | bytes | dict) -> AnalysisRunReport:
    if isinstance(raw, dict):
        data = raw
    else:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
    _assert_no_absolute_paths(data)
    return AnalysisRunReport.model_validate(data)


def _assert_no_absolute_paths(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (
                value.startswith("/")
                or (len(value) > 2 and value[1] == ":" and value[2] in "\\/")
            ):
                if "path" in str(key).lower() or value.startswith("/"):
                    # Nur Pfad-ähnliche Felder / klare Absolute blockieren.
                    if "path" in str(key).lower() or value.startswith(("/", "\\")):
                        raise ValueError(
                            f"Absolute Pfade in Analysis-JSON verboten: {key}={value}"
                        )
            _assert_no_absolute_paths(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_absolute_paths(item)


def _row_to_identity(row: sqlite3.Row) -> AnalysisIdentityRecord:
    return AnalysisIdentityRecord(
        analysis_identity_id=str(row["analysis_identity_id"]),
        project_id=str(row["project_id"]),
        asset_id=str(row["asset_id"]),
        working_media_id=str(row["working_media_id"]),
        output_sha256=str(row["output_sha256"]),
        processing_profile_version=str(row["processing_profile_version"]),
        analysis_profile_version=str(row["analysis_profile_version"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_run(row: sqlite3.Row) -> AnalysisRun:
    return AnalysisRun(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        analysis_profile_version=str(row["analysis_profile_version"]),
        status=AnalysisRunStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        completed_at=_parse_dt(row["completed_at"]),
        total_assets=int(row["total_assets"] or 0),
        prepared_assets=int(row["prepared_assets"] or 0),
        not_applicable_assets=int(row["not_applicable_assets"] or 0),
        failed_assets=int(row["failed_assets"] or 0),
        interrupted_assets=int(row["interrupted_assets"] or 0),
        error_summary=row["error_summary"],
    )


def _row_to_run_asset(row: sqlite3.Row) -> AnalysisRunAsset:
    return AnalysisRunAsset(
        run_id=str(row["run_id"]),
        asset_id=str(row["asset_id"]),
        working_media_id=str(row["working_media_id"]),
        validation_id=str(row["validation_id"]),
        source_sha256=str(row["source_sha256"]),
        output_sha256=str(row["output_sha256"]),
        processing_profile_version=str(row["processing_profile_version"]),
        analysis_profile_version=str(row["analysis_profile_version"]),
        media_kind=str(row["media_kind"]),
        status=AnalysisPrepareAssetStatus(str(row["status"])),
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_parse_dt(row["created_at"]),
        completed_at=_parse_dt(row["completed_at"]),
    )
