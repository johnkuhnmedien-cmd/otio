"""SQLite + JSON Persistenz für Discovery-V2 Copy-Intake-Runs."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from otio_app.discovery_v2.domain.media_intake import (
    ACTIVE_INTAKE_RUN_STATUSES,
    ALLOWED_WORKING_PROFILE_VERSIONS,
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    INTAKE_RUN_SCOPE_COPY_ONLY,
    IntakeAction,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunLatestPointer,
    IntakeRunRecord,
    IntakeRunReport,
    IntakeRunReportAsset,
    IntakeRunStatus,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    _atomic_write_text,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


def open_registry(project_root: Path) -> sqlite3.Connection:
    return get_registry_connection(project_root)


def new_intake_run_id() -> str:
    return str(uuid4())


def new_run_asset_id() -> str:
    return str(uuid4())


def new_working_media_id() -> str:
    return str(uuid4())


def intake_runs_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "intake" / "runs"


def intake_run_report_path(project_root: Path, run_id: str) -> Path:
    return intake_runs_dir(project_root) / f"{run_id}.json"


def latest_intake_run_pointer_path(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "intake" / "latest_run.json"


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def media_working_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "media" / "working"


def media_temp_dir(project_root: Path, run_id: str) -> Path:
    return get_discovery_v2_root(project_root) / "media" / "temp" / run_id


def normalize_extension(extension: str | None, *, source_relative_path: str = "") -> str:
    raw = (extension or "").strip().lower()
    if not raw:
        raw = PurePosixPath(source_relative_path.replace("\\", "/")).suffix.lower()
    if raw and not raw.startswith("."):
        raw = f".{raw}"
    if not raw or "/" in raw or ".." in raw:
        raise ValueError(f"Ungültige Dateiendung: {extension!r}")
    return raw


def _assert_safe_token(value: str, *, label: str) -> str:
    text = (value or "").strip()
    if not text or ".." in text or "/" in text or "\\" in text:
        raise ValueError(f"Ungültiges {label}: {value!r}")
    if not _SAFE_TOKEN_RE.match(text):
        raise ValueError(f"Ungültiges {label}: {value!r}")
    return text


def build_working_relative_path(
    *,
    asset_id: str,
    source_sha256: str,
    extension: str,
    profile_version: str = COPY_WORKING_PROFILE_VERSION,
) -> str:
    """Kanonischer relativer Pfad unter ``_otio_v2``:

    ``media/working/<asset_id>/<source_sha256>/<profile>/<asset_id>.<ext>``
    """
    asset = _assert_safe_token(asset_id, label="asset_id")
    digest = (source_sha256 or "").strip().lower()
    if not _SHA256_RE.match(digest):
        raise ValueError(f"Ungültiger source_sha256: {source_sha256!r}")
    profile = _assert_safe_token(profile_version, label="processing_profile_version")
    if profile not in ALLOWED_WORKING_PROFILE_VERSIONS:
        raise ValueError(f"Unsupported Working-Media-Profil: {profile}")
    ext = normalize_extension(extension)
    filename = f"{asset}{ext}"
    return f"media/working/{asset}/{digest}/{profile}/{filename}"


def build_temp_relative_path(
    *, run_id: str, asset_id: str, extension: str
) -> str:
    run = _assert_safe_token(run_id, label="run_id")
    asset = _assert_safe_token(asset_id, label="asset_id")
    ext = normalize_extension(extension)
    # z. B. media/temp/<run_id>/<asset_id>.tmp.mp4
    return f"media/temp/{run}/{asset}.tmp{ext}"


def is_canonical_working_relative_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    return (
        len(parts) == 5
        and parts[0] == "media"
        and parts[1] == "working"
        and parts[3] in ALLOWED_WORKING_PROFILE_VERSIONS
    )


def is_legacy_working_relative_path(relative_path: str) -> bool:
    """Altes Muster ``media/working/<source_relative_path>`` ohne copy-v1."""
    text = relative_path.replace("\\", "/").lstrip("/")
    if not text.startswith("media/working/"):
        return False
    return not is_canonical_working_relative_path(text)


def ensure_intake_run_dirs(project_root: Path) -> None:
    try:
        intake_runs_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Intake-Run-Verzeichnis nicht beschreibbar: {exc}"
        ) from exc
    assert_path_is_under_discovery_v2(intake_runs_dir(project_root), project_root)


def insert_intake_run(conn: sqlite3.Connection, run: IntakeRunRecord) -> None:
    conn.execute(
        """
        INSERT INTO intake_runs (
            run_id, project_id, plan_id, import_id, selection_id, scan_id,
            validation_run_id, status, created_at, started_at, completed_at,
            total_assets, processed_assets, succeeded_assets, failed_assets,
            skipped_assets, error_summary, worker_version, scope
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.project_id,
            run.plan_id,
            run.import_id,
            run.selection_id,
            run.scan_id,
            run.validation_run_id,
            run.status.value,
            run.created_at.isoformat(),
            run.started_at.isoformat() if run.started_at else None,
            run.completed_at.isoformat() if run.completed_at else None,
            run.total_assets,
            run.processed_assets,
            run.succeeded_assets,
            run.failed_assets,
            run.skipped_assets,
            run.error_summary,
            run.worker_version,
            run.scope or INTAKE_RUN_SCOPE_COPY_ONLY,
        ),
    )


def insert_intake_run_asset(
    conn: sqlite3.Connection, record: IntakeRunAssetRecord
) -> None:
    conn.execute(
        """
        INSERT INTO intake_run_assets (
            run_asset_id, run_id, plan_id, asset_id, source_relative_path,
            source_group, media_kind, planned_action, status, source_sha256,
            output_sha256, working_relative_path, error_code, error_message,
            processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.run_asset_id,
            record.run_id,
            record.plan_id,
            record.asset_id,
            record.source_relative_path,
            record.source_group,
            record.media_kind,
            record.planned_action.value,
            record.status.value,
            record.source_sha256,
            record.output_sha256,
            record.working_relative_path,
            record.error_code,
            record.error_message,
            record.processed_at.isoformat() if record.processed_at else None,
        ),
    )


def update_intake_run(conn: sqlite3.Connection, run: IntakeRunRecord) -> None:
    conn.execute(
        """
        UPDATE intake_runs
        SET status = ?,
            started_at = ?,
            completed_at = ?,
            total_assets = ?,
            processed_assets = ?,
            succeeded_assets = ?,
            failed_assets = ?,
            skipped_assets = ?,
            error_summary = ?,
            worker_version = ?,
            scope = ?
        WHERE run_id = ?
        """,
        (
            run.status.value,
            run.started_at.isoformat() if run.started_at else None,
            run.completed_at.isoformat() if run.completed_at else None,
            run.total_assets,
            run.processed_assets,
            run.succeeded_assets,
            run.failed_assets,
            run.skipped_assets,
            run.error_summary,
            run.worker_version,
            run.scope or INTAKE_RUN_SCOPE_COPY_ONLY,
            run.run_id,
        ),
    )


def update_intake_run_asset(
    conn: sqlite3.Connection, record: IntakeRunAssetRecord
) -> None:
    conn.execute(
        """
        UPDATE intake_run_assets
        SET status = ?,
            source_sha256 = ?,
            output_sha256 = ?,
            working_relative_path = ?,
            error_code = ?,
            error_message = ?,
            processed_at = ?
        WHERE run_asset_id = ?
        """,
        (
            record.status.value,
            record.source_sha256,
            record.output_sha256,
            record.working_relative_path,
            record.error_code,
            record.error_message,
            record.processed_at.isoformat() if record.processed_at else None,
            record.run_asset_id,
        ),
    )


def insert_working_media(
    conn: sqlite3.Connection, record: WorkingMediaRecord
) -> None:
    """Fügt eine Working-Media-Version ein (kein Überschreiben anderer Hashes)."""
    conn.execute(
        """
        INSERT INTO working_media (
            working_media_id, project_id, asset_id, plan_id, intake_run_id,
            source_relative_path, working_relative_path, source_sha256,
            output_sha256, media_kind, extension, action,
            processing_profile_version, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.working_media_id,
            record.project_id,
            record.asset_id,
            record.plan_id,
            record.intake_run_id,
            record.source_relative_path,
            record.working_relative_path,
            record.source_sha256,
            record.output_sha256,
            record.media_kind,
            record.extension,
            record.action,
            record.processing_profile_version,
            record.status.value,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        ),
    )


def upsert_working_media(
    conn: sqlite3.Connection, record: WorkingMediaRecord
) -> None:
    """Idempotent für dieselbe Hash/Action/Profil-Version."""
    conn.execute(
        """
        INSERT INTO working_media (
            working_media_id, project_id, asset_id, plan_id, intake_run_id,
            source_relative_path, working_relative_path, source_sha256,
            output_sha256, media_kind, extension, action,
            processing_profile_version, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            project_id, asset_id, source_sha256, action, processing_profile_version
        ) DO UPDATE SET
            plan_id = excluded.plan_id,
            intake_run_id = excluded.intake_run_id,
            source_relative_path = excluded.source_relative_path,
            working_relative_path = excluded.working_relative_path,
            output_sha256 = excluded.output_sha256,
            media_kind = excluded.media_kind,
            extension = excluded.extension,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (
            record.working_media_id,
            record.project_id,
            record.asset_id,
            record.plan_id,
            record.intake_run_id,
            record.source_relative_path,
            record.working_relative_path,
            record.source_sha256,
            record.output_sha256,
            record.media_kind,
            record.extension,
            record.action,
            record.processing_profile_version,
            record.status.value,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        ),
    )


def get_intake_run(
    conn: sqlite3.Connection, *, run_id: str
) -> IntakeRunRecord | None:
    row = conn.execute(
        "SELECT * FROM intake_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def get_latest_intake_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    scope: str | None = None,
) -> IntakeRunRecord | None:
    if scope is None:
        row = conn.execute(
            """
            SELECT * FROM intake_runs
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM intake_runs
            WHERE project_id = ?
              AND scope = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id, scope),
        ).fetchone()
    return None if row is None else _row_to_run(row)


def find_active_intake_run(
    conn: sqlite3.Connection, *, project_id: str
) -> IntakeRunRecord | None:
    placeholders = ", ".join("?" for _ in ACTIVE_INTAKE_RUN_STATUSES)
    row = conn.execute(
        f"""
        SELECT * FROM intake_runs
        WHERE project_id = ?
          AND status IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id, *[s.value for s in ACTIVE_INTAKE_RUN_STATUSES]),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def list_intake_run_assets(
    conn: sqlite3.Connection, *, run_id: str
) -> list[IntakeRunAssetRecord]:
    rows = conn.execute(
        """
        SELECT * FROM intake_run_assets
        WHERE run_id = ?
        ORDER BY source_relative_path
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_run_asset(row) for row in rows]


def get_working_media(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    asset_id: str,
    source_sha256: str,
    action: str = COPY_WORKING_ACTION,
    processing_profile_version: str = COPY_WORKING_PROFILE_VERSION,
) -> WorkingMediaRecord | None:
    row = conn.execute(
        """
        SELECT * FROM working_media
        WHERE project_id = ?
          AND asset_id = ?
          AND source_sha256 = ?
          AND action = ?
          AND processing_profile_version = ?
        """,
        (
            project_id,
            asset_id,
            source_sha256.lower(),
            action,
            processing_profile_version,
        ),
    ).fetchone()
    return None if row is None else _row_to_working(row)


def list_working_media(
    conn: sqlite3.Connection, *, project_id: str
) -> list[WorkingMediaRecord]:
    rows = conn.execute(
        """
        SELECT * FROM working_media
        WHERE project_id = ?
        ORDER BY asset_id, source_sha256, created_at
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_working(row) for row in rows]


def list_working_media_for_asset(
    conn: sqlite3.Connection, *, project_id: str, asset_id: str
) -> list[WorkingMediaRecord]:
    rows = conn.execute(
        """
        SELECT * FROM working_media
        WHERE project_id = ? AND asset_id = ?
        ORDER BY created_at
        """,
        (project_id, asset_id),
    ).fetchall()
    return [_row_to_working(row) for row in rows]


def build_report_from_intake_run(
    run: IntakeRunRecord,
    *,
    assets: list[IntakeRunAssetRecord] | None = None,
    asset_extras: dict[str, dict[str, str | None]] | None = None,
) -> IntakeRunReport:
    extras = asset_extras or {}
    report_assets: list[IntakeRunReportAsset] = []
    remuxed = 0
    reused = 0
    if assets:
        for asset in assets:
            if asset.status == IntakeRunAssetStatus.SUCCEEDED:
                remuxed += 1
            elif asset.status == IntakeRunAssetStatus.REUSED:
                reused += 1
            detail = extras.get(asset.asset_id, {})
            report_assets.append(
                IntakeRunReportAsset(
                    asset_id=asset.asset_id,
                    source_relative_path=asset.source_relative_path,
                    status=asset.status.value,
                    working_relative_path=asset.working_relative_path,
                    error_code=asset.error_code,
                    error_message=asset.error_message,
                    audio_policy=detail.get("audio_policy"),
                    timecode_policy=detail.get("timecode_policy"),
                    output_sha256=asset.output_sha256,
                )
            )
    return IntakeRunReport(
        run_id=run.run_id,
        project_id=run.project_id,
        plan_id=run.plan_id,
        import_id=run.import_id,
        selection_id=run.selection_id,
        scan_id=run.scan_id,
        validation_run_id=run.validation_run_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_assets=run.total_assets,
        processed_assets=run.processed_assets,
        succeeded_assets=run.succeeded_assets,
        failed_assets=run.failed_assets,
        skipped_assets=run.skipped_assets,
        remuxed_assets=remuxed,
        reused_assets=reused,
        error_summary=run.error_summary,
        worker_version=run.worker_version,
        scope=run.scope or INTAKE_RUN_SCOPE_COPY_ONLY,
        report_relative_path=f"intake/runs/{run.run_id}.json",
        registry_sqlite_relative_path="registry/assets.sqlite3",
        assets=report_assets,
    )


def save_intake_run_report(project_root: Path, report: IntakeRunReport) -> Path:
    ensure_intake_run_dirs(project_root)
    target = intake_run_report_path(project_root, report.run_id)
    assert_path_is_under_discovery_v2(target, project_root)
    try:
        _atomic_write_text(target, report.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"Intake-Run-Bericht konnte nicht geschrieben werden: {exc}"
        ) from exc

    if report.status in {
        IntakeRunStatus.COMPLETED,
        IntakeRunStatus.COMPLETED_WITH_ERRORS,
        IntakeRunStatus.FAILED,
        IntakeRunStatus.CANCELLED,
    }:
        pointer = IntakeRunLatestPointer(
            run_id=report.run_id,
            plan_id=report.plan_id,
            import_id=report.import_id,
            selection_id=report.selection_id,
            scan_id=report.scan_id,
            validation_run_id=report.validation_run_id,
            status=report.status,
            completed_at=report.completed_at,
            report_relative_path=report.report_relative_path
            or f"intake/runs/{report.run_id}.json",
            scope=report.scope or INTAKE_RUN_SCOPE_COPY_ONLY,
        )
        latest = latest_intake_run_pointer_path(project_root)
        assert_path_is_under_discovery_v2(latest, project_root)
        try:
            _atomic_write_text(latest, pointer.model_dump_json(indent=2))
        except OSError as exc:
            raise InventoryArtifactError(
                f"latest_run.json (intake) konnte nicht geschrieben werden: {exc}"
            ) from exc
    return target


def _parse_optional_dt(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


def _row_to_run(row: sqlite3.Row) -> IntakeRunRecord:
    keys = set(row.keys())
    scope = (
        str(row["scope"])
        if "scope" in keys and row["scope"]
        else INTAKE_RUN_SCOPE_COPY_ONLY
    )
    return IntakeRunRecord(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        plan_id=str(row["plan_id"]),
        import_id=str(row["import_id"]),
        selection_id=str(row["selection_id"]),
        scan_id=str(row["scan_id"]),
        validation_run_id=str(row["validation_run_id"]),
        status=IntakeRunStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        started_at=_parse_optional_dt(row["started_at"]),
        completed_at=_parse_optional_dt(row["completed_at"]),
        total_assets=int(row["total_assets"]),
        processed_assets=int(row["processed_assets"]),
        succeeded_assets=int(row["succeeded_assets"]),
        failed_assets=int(row["failed_assets"]),
        skipped_assets=int(row["skipped_assets"]),
        error_summary=row["error_summary"],
        worker_version=str(row["worker_version"]),
        scope=scope,
    )


def _row_to_run_asset(row: sqlite3.Row) -> IntakeRunAssetRecord:
    return IntakeRunAssetRecord(
        run_asset_id=str(row["run_asset_id"]),
        run_id=str(row["run_id"]),
        plan_id=str(row["plan_id"]),
        asset_id=str(row["asset_id"]),
        source_relative_path=str(row["source_relative_path"]),
        source_group=str(row["source_group"]),
        media_kind=str(row["media_kind"]),
        planned_action=IntakeAction(str(row["planned_action"])),
        status=IntakeRunAssetStatus(str(row["status"])),
        source_sha256=row["source_sha256"],
        output_sha256=row["output_sha256"],
        working_relative_path=row["working_relative_path"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        processed_at=_parse_optional_dt(row["processed_at"]),
    )


def _row_to_working(row: sqlite3.Row) -> WorkingMediaRecord:
    keys = set(row.keys())
    status_raw = str(row["status"])
    if status_raw == WorkingMediaStatus.READY.value:
        status_raw = WorkingMediaStatus.COMPLETED.value
    return WorkingMediaRecord(
        working_media_id=str(row["working_media_id"]),
        project_id=str(row["project_id"]),
        asset_id=str(row["asset_id"]),
        plan_id=str(row["plan_id"]),
        intake_run_id=str(row["intake_run_id"]),
        source_relative_path=str(row["source_relative_path"]),
        working_relative_path=str(row["working_relative_path"]),
        source_sha256=str(row["source_sha256"]),
        output_sha256=str(row["output_sha256"]),
        media_kind=str(row["media_kind"]),
        extension=str(row["extension"]),
        action=str(row["action"]) if "action" in keys and row["action"] else COPY_WORKING_ACTION,
        processing_profile_version=(
            str(row["processing_profile_version"])
            if "processing_profile_version" in keys and row["processing_profile_version"]
            else COPY_WORKING_PROFILE_VERSION
        ),
        status=WorkingMediaStatus(status_raw),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
