"""Persistence für Discovery-V2-Assetanalyse (Phase 8A/8B)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.analysis_paths import (
    analysis_latest_prepare_run_relative_path,
    analysis_manifest_json_relative_path,
    analysis_run_json_relative_path,
    assert_analysis_relative_path,
    resolve_analysis_relative_path,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ACTIVE_ANALYSIS_RUN_STATUSES,
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_PREPARE_PROFILE_VERSION,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisIdentityRecord,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunReport,
    AnalysisRunStatus,
    FRAME_SAMPLE_PROFILE_VERSION,
    RepresentativeFrameRecord,
    SHOT_DETECT_PROFILE_VERSION,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root


def open_analysis_registry(project_root: Path) -> sqlite3.Connection:
    return get_registry_connection(project_root)


def new_analysis_identity_id() -> str:
    return str(uuid4())


def new_analysis_run_id() -> str:
    return str(uuid4())


def new_shot_id() -> str:
    return str(uuid4())


def new_frame_id() -> str:
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


def get_analysis_identity(
    conn: sqlite3.Connection, *, analysis_identity_id: str
) -> AnalysisIdentityRecord | None:
    row = conn.execute(
        """
        SELECT * FROM analysis_identities
        WHERE analysis_identity_id = ?
        """,
        (analysis_identity_id,),
    ).fetchone()
    return None if row is None else _row_to_identity(row)


def insert_analysis_run(conn: sqlite3.Connection, run: AnalysisRun) -> None:
    conn.execute(
        """
        INSERT INTO analysis_runs (
            run_id, project_id, scope, analysis_profile_version, status,
            created_at, started_at, completed_at, total_assets,
            prepared_assets, reused_assets, not_applicable_assets, failed_assets,
            interrupted_assets, error_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            run.reused_assets,
            run.not_applicable_assets,
            run.failed_assets,
            run.interrupted_assets,
            run.error_summary,
        ),
    )


def update_analysis_run(conn: sqlite3.Connection, run: AnalysisRun) -> None:
    conn.execute(
        """
        UPDATE analysis_runs SET
            scope = ?,
            analysis_profile_version = ?,
            status = ?,
            started_at = ?,
            completed_at = ?,
            total_assets = ?,
            prepared_assets = ?,
            reused_assets = ?,
            not_applicable_assets = ?,
            failed_assets = ?,
            interrupted_assets = ?,
            error_summary = ?
        WHERE run_id = ?
        """,
        (
            run.scope,
            run.analysis_profile_version,
            run.status.value,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.completed_at is None else run.completed_at.isoformat(),
            run.total_assets,
            run.prepared_assets,
            run.reused_assets,
            run.not_applicable_assets,
            run.failed_assets,
            run.interrupted_assets,
            run.error_summary,
            run.run_id,
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


def find_active_analysis_run(
    conn: sqlite3.Connection, *, project_id: str
) -> AnalysisRun | None:
    rows = conn.execute(
        """
        SELECT * FROM analysis_runs
        WHERE project_id = ?
          AND status IN (?, ?)
        ORDER BY created_at DESC
        """,
        (
            project_id,
            AnalysisRunStatus.QUEUED.value,
            AnalysisRunStatus.RUNNING.value,
        ),
    ).fetchall()
    if not rows:
        return None
    return _row_to_run(rows[0])


def get_latest_analysis_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    scope: str | None = ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
) -> AnalysisRun | None:
    if scope is None:
        rows = conn.execute(
            """
            SELECT * FROM analysis_runs
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM analysis_runs
            WHERE project_id = ?
              AND scope = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id, scope),
        ).fetchall()
    if not rows:
        return None
    return _row_to_run(rows[0])


def insert_analysis_run_asset(
    conn: sqlite3.Connection, asset: AnalysisRunAsset
) -> None:
    conn.execute(
        """
        INSERT INTO analysis_run_assets (
            run_id, asset_id, working_media_id, validation_id, source_sha256,
            output_sha256, processing_profile_version, analysis_profile_version,
            media_kind, status, error_code, error_message, created_at, completed_at,
            analysis_identity_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            asset.analysis_identity_id,
        ),
    )


def update_analysis_run_asset(
    conn: sqlite3.Connection, asset: AnalysisRunAsset
) -> None:
    conn.execute(
        """
        UPDATE analysis_run_assets SET
            status = ?,
            error_code = ?,
            error_message = ?,
            completed_at = ?,
            analysis_identity_id = ?,
            output_sha256 = ?,
            source_sha256 = ?,
            processing_profile_version = ?,
            analysis_profile_version = ?,
            media_kind = ?,
            validation_id = ?
        WHERE run_id = ? AND asset_id = ? AND working_media_id = ?
        """,
        (
            asset.status.value,
            asset.error_code,
            asset.error_message,
            None if asset.completed_at is None else asset.completed_at.isoformat(),
            asset.analysis_identity_id,
            asset.output_sha256.lower(),
            asset.source_sha256.lower(),
            asset.processing_profile_version,
            asset.analysis_profile_version,
            asset.media_kind,
            asset.validation_id,
            asset.run_id,
            asset.asset_id,
            asset.working_media_id,
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


def insert_technical_shot(
    conn: sqlite3.Connection, shot: TechnicalShotRecord
) -> None:
    conn.execute(
        """
        INSERT INTO technical_shots (
            shot_id, analysis_identity_id, project_id, asset_id, working_media_id,
            ordinal, start_seconds, end_seconds, duration_seconds,
            detection_profile_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shot.shot_id,
            shot.analysis_identity_id,
            shot.project_id,
            shot.asset_id,
            shot.working_media_id,
            shot.ordinal,
            shot.start_seconds,
            shot.end_seconds,
            shot.duration_seconds,
            shot.detection_profile_version,
            shot.created_at.isoformat(),
        ),
    )


def list_technical_shots(
    conn: sqlite3.Connection,
    *,
    analysis_identity_id: str,
    detection_profile_version: str = SHOT_DETECT_PROFILE_VERSION,
) -> list[TechnicalShotRecord]:
    rows = conn.execute(
        """
        SELECT * FROM technical_shots
        WHERE analysis_identity_id = ?
          AND detection_profile_version = ?
        ORDER BY ordinal
        """,
        (analysis_identity_id, detection_profile_version),
    ).fetchall()
    return [_row_to_shot(row) for row in rows]


def list_technical_shots_for_project(
    conn: sqlite3.Connection, *, project_id: str
) -> list[TechnicalShotRecord]:
    rows = conn.execute(
        """
        SELECT * FROM technical_shots
        WHERE project_id = ?
        ORDER BY asset_id, ordinal
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_shot(row) for row in rows]


def insert_representative_frame(
    conn: sqlite3.Connection, frame: RepresentativeFrameRecord
) -> None:
    assert_analysis_relative_path(frame.relative_path)
    conn.execute(
        """
        INSERT INTO representative_frames (
            frame_id, analysis_identity_id, project_id, asset_id, working_media_id,
            shot_id, ordinal, timestamp_seconds, relative_path, frame_sha256,
            pixel_sha256, file_size_bytes, width, height, sampling_profile_version,
            brightness_mean, black_fraction, sharpness_score, is_black, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            frame.frame_id,
            frame.analysis_identity_id,
            frame.project_id,
            frame.asset_id,
            frame.working_media_id,
            frame.shot_id,
            frame.ordinal,
            frame.timestamp_seconds,
            frame.relative_path,
            frame.frame_sha256.lower(),
            frame.pixel_sha256.lower(),
            frame.file_size_bytes,
            frame.width,
            frame.height,
            frame.sampling_profile_version,
            frame.brightness_mean,
            frame.black_fraction,
            frame.sharpness_score,
            1 if frame.is_black else 0,
            frame.created_at.isoformat(),
        ),
    )


def list_representative_frames(
    conn: sqlite3.Connection,
    *,
    analysis_identity_id: str,
    sampling_profile_version: str = FRAME_SAMPLE_PROFILE_VERSION,
) -> list[RepresentativeFrameRecord]:
    rows = conn.execute(
        """
        SELECT * FROM representative_frames
        WHERE analysis_identity_id = ?
          AND sampling_profile_version = ?
        ORDER BY ordinal
        """,
        (analysis_identity_id, sampling_profile_version),
    ).fetchall()
    return [_row_to_frame(row) for row in rows]


def list_representative_frames_for_project(
    conn: sqlite3.Connection, *, project_id: str
) -> list[RepresentativeFrameRecord]:
    rows = conn.execute(
        """
        SELECT * FROM representative_frames
        WHERE project_id = ?
        ORDER BY asset_id, ordinal
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_frame(row) for row in rows]


def replace_prepare_artifacts(
    conn: sqlite3.Connection,
    *,
    analysis_identity_id: str,
    shots: list[TechnicalShotRecord],
    frames: list[RepresentativeFrameRecord],
    detection_profile_version: str = SHOT_DETECT_PROFILE_VERSION,
    sampling_profile_version: str = FRAME_SAMPLE_PROFILE_VERSION,
) -> None:
    """Ersetzt Shot-/Frame-Zeilen einer Identity atomar (innerhalb einer TX)."""
    conn.execute(
        """
        DELETE FROM representative_frames
        WHERE analysis_identity_id = ?
          AND sampling_profile_version = ?
        """,
        (analysis_identity_id, sampling_profile_version),
    )
    conn.execute(
        """
        DELETE FROM technical_shots
        WHERE analysis_identity_id = ?
          AND detection_profile_version = ?
        """,
        (analysis_identity_id, detection_profile_version),
    )
    for shot in shots:
        insert_technical_shot(conn, shot)
    for frame in frames:
        insert_representative_frame(conn, frame)


def analysis_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE 'analysis%'
        """
    ).fetchall()
    names = {str(r[0]) for r in rows}
    # technical_shots / representative_frames sind Analysis-Domain, aber ohne Prefix.
    extra = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name IN (
            'technical_shots', 'representative_frames',
            'visual_observations', 'model_analysis_attempts', 'consent_events'
        )
        """
    ).fetchall()
    names.update(str(r[0]) for r in extra)
    return names


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


def save_analysis_run_report(
    project_root: Path, report: AnalysisRunReport
) -> Path:
    relative = analysis_run_json_relative_path(report.run_id)
    path = resolve_analysis_relative_path(project_root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = serialize_analysis_run_report(report)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Analysis-Runbericht konnte nicht geschrieben werden: {exc}"
        ) from exc

    latest_rel = analysis_latest_prepare_run_relative_path()
    latest = resolve_analysis_relative_path(project_root, latest_rel)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest_tmp = latest.with_suffix(latest.suffix + ".tmp")
    try:
        latest_tmp.write_text(text, encoding="utf-8")
        latest_tmp.replace(latest)
    except OSError as exc:
        raise InventoryArtifactError(
            f"latest_prepare_run.json konnte nicht geschrieben werden: {exc}"
        ) from exc
    return path


def save_prepare_manifest(
    project_root: Path,
    *,
    analysis_identity_id: str,
    payload: dict,
) -> Path:
    relative = analysis_manifest_json_relative_path(analysis_identity_id)
    path = resolve_analysis_relative_path(project_root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_absolute_paths(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Prepare-Manifest konnte nicht geschrieben werden: {exc}"
        ) from exc
    return path


def cleanup_analysis_temp(project_root: Path, *, run_id: str) -> None:
    from otio_app.discovery_v2.analysis_paths import analysis_temp_dir

    temp_dir = analysis_temp_dir(project_root, run_id)
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


def _assert_no_absolute_paths(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (
                value.startswith("/")
                or (len(value) > 2 and value[1] == ":" and value[2] in "\\/")
            ):
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
    keys = set(row.keys())
    reused = int(row["reused_assets"] or 0) if "reused_assets" in keys else 0
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
        reused_assets=reused,
        not_applicable_assets=int(row["not_applicable_assets"] or 0),
        failed_assets=int(row["failed_assets"] or 0),
        interrupted_assets=int(row["interrupted_assets"] or 0),
        error_summary=row["error_summary"],
    )


def _row_to_run_asset(row: sqlite3.Row) -> AnalysisRunAsset:
    keys = set(row.keys())
    identity = None
    if "analysis_identity_id" in keys and row["analysis_identity_id"] is not None:
        identity = str(row["analysis_identity_id"])
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
        analysis_identity_id=identity,
    )


def _row_to_shot(row: sqlite3.Row) -> TechnicalShotRecord:
    return TechnicalShotRecord(
        shot_id=str(row["shot_id"]),
        analysis_identity_id=str(row["analysis_identity_id"]),
        project_id=str(row["project_id"]),
        asset_id=str(row["asset_id"]),
        working_media_id=str(row["working_media_id"]),
        ordinal=int(row["ordinal"]),
        start_seconds=float(row["start_seconds"]),
        end_seconds=float(row["end_seconds"]),
        duration_seconds=float(row["duration_seconds"]),
        detection_profile_version=str(row["detection_profile_version"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_frame(row: sqlite3.Row) -> RepresentativeFrameRecord:
    shot_id = row["shot_id"]
    return RepresentativeFrameRecord(
        frame_id=str(row["frame_id"]),
        analysis_identity_id=str(row["analysis_identity_id"]),
        project_id=str(row["project_id"]),
        asset_id=str(row["asset_id"]),
        working_media_id=str(row["working_media_id"]),
        shot_id=None if shot_id is None else str(shot_id),
        ordinal=int(row["ordinal"]),
        timestamp_seconds=(
            None
            if row["timestamp_seconds"] is None
            else float(row["timestamp_seconds"])
        ),
        relative_path=str(row["relative_path"]),
        frame_sha256=str(row["frame_sha256"]),
        pixel_sha256=str(row["pixel_sha256"]),
        file_size_bytes=int(row["file_size_bytes"]),
        width=int(row["width"]),
        height=int(row["height"]),
        sampling_profile_version=str(row["sampling_profile_version"]),
        brightness_mean=float(row["brightness_mean"]),
        black_fraction=float(row["black_fraction"]),
        sharpness_score=float(row["sharpness_score"]),
        is_black=bool(int(row["is_black"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


# Re-export helper for callers that need v2 root.
def discovery_v2_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root)


__all__ = [
    "ACTIVE_ANALYSIS_RUN_STATUSES",
    "ANALYSIS_CONTRACT_PROFILE_VERSION",
    "ANALYSIS_PREPARE_PROFILE_VERSION",
    "analysis_table_names",
    "cleanup_analysis_temp",
    "find_active_analysis_run",
    "find_analysis_identity",
    "find_or_create_analysis_identity",
    "get_analysis_identity",
    "get_analysis_run",
    "get_latest_analysis_run",
    "insert_analysis_run",
    "insert_analysis_run_asset",
    "insert_representative_frame",
    "insert_technical_shot",
    "list_analysis_identities",
    "list_analysis_run_assets",
    "list_analysis_runs",
    "list_representative_frames",
    "list_representative_frames_for_project",
    "list_technical_shots",
    "list_technical_shots_for_project",
    "new_analysis_identity_id",
    "new_analysis_run_id",
    "new_frame_id",
    "new_shot_id",
    "open_analysis_registry",
    "parse_analysis_run_report",
    "replace_prepare_artifacts",
    "save_analysis_run_report",
    "save_prepare_manifest",
    "serialize_analysis_run_report",
    "update_analysis_run",
    "update_analysis_run_asset",
]
