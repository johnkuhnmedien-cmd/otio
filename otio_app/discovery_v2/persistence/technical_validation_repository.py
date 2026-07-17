"""Persistenz für Discovery-V2 technische Prüfungen (SQLite + JSON-Berichte)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.asset_registry import RegistryAssetRecord
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.technical_validation import (
    ACTIVE_RUN_STATUSES,
    AssetValidationRecord,
    AssetValidationStatus,
    DuplicateGroupRecord,
    ValidationLatestPointer,
    ValidationRunRecord,
    ValidationRunReport,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
    ensure_registry_dir,
    get_registry_connection,
    registry_dir,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    _atomic_write_text,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


def validation_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "validation"


def validation_runs_dir(project_root: Path) -> Path:
    return validation_dir(project_root) / "runs"


def validation_report_path(project_root: Path, run_id: str) -> Path:
    return validation_runs_dir(project_root) / f"{run_id}.json"


def latest_validation_pointer_path(project_root: Path) -> Path:
    return validation_dir(project_root) / "latest_run.json"


def ensure_validation_dirs(project_root: Path) -> None:
    ensure_registry_dir(project_root)
    try:
        validation_dir(project_root).mkdir(parents=True, exist_ok=True)
        validation_runs_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Validation-Verzeichnis nicht beschreibbar: {exc}"
        ) from exc
    assert_path_is_under_discovery_v2(validation_dir(project_root), project_root)
    assert_path_is_under_discovery_v2(validation_runs_dir(project_root), project_root)


def open_registry(project_root: Path) -> sqlite3.Connection:
    try:
        return get_registry_connection(project_root)
    except RegistryDatabaseError:
        raise


def find_latest_import(
    conn: sqlite3.Connection, *, project_id: str
) -> tuple[str, str, str, int] | None:
    """Returns (import_id, selection_id, scan_id, selected_asset_count) or None."""
    row = conn.execute(
        """
        SELECT import_id, selection_id, scan_id, selected_asset_count
        FROM selection_imports
        WHERE project_id = ?
        ORDER BY imported_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return (
        str(row["import_id"]),
        str(row["selection_id"]),
        str(row["scan_id"]),
        int(row["selected_asset_count"]),
    )


def find_import(
    conn: sqlite3.Connection, *, import_id: str
) -> tuple[str, str, str, str, int] | None:
    row = conn.execute(
        """
        SELECT import_id, project_id, selection_id, scan_id, selected_asset_count
        FROM selection_imports
        WHERE import_id = ?
        """,
        (import_id,),
    ).fetchone()
    if row is None:
        return None
    return (
        str(row["import_id"]),
        str(row["project_id"]),
        str(row["selection_id"]),
        str(row["scan_id"]),
        int(row["selected_asset_count"]),
    )


def list_assets_for_import(
    conn: sqlite3.Connection, *, import_id: str
) -> list[RegistryAssetRecord]:
    rows = conn.execute(
        """
        SELECT a.*
        FROM assets a
        JOIN selection_import_assets m ON m.asset_id = a.asset_id
        WHERE m.import_id = ?
        ORDER BY a.source_relative_path
        """,
        (import_id,),
    ).fetchall()
    return [_row_to_asset(row) for row in rows]


def find_active_run(
    conn: sqlite3.Connection, *, project_id: str
) -> ValidationRunRecord | None:
    placeholders = ", ".join("?" for _ in ACTIVE_RUN_STATUSES)
    rows = conn.execute(
        f"""
        SELECT * FROM validation_runs
        WHERE project_id = ?
          AND status IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id, *[s.value for s in ACTIVE_RUN_STATUSES]),
    ).fetchone()
    return None if rows is None else _row_to_run(rows)


def get_run(conn: sqlite3.Connection, *, run_id: str) -> ValidationRunRecord | None:
    row = conn.execute(
        "SELECT * FROM validation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def get_latest_run(
    conn: sqlite3.Connection, *, project_id: str
) -> ValidationRunRecord | None:
    row = conn.execute(
        """
        SELECT * FROM validation_runs
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def insert_run(conn: sqlite3.Connection, run: ValidationRunRecord) -> None:
    conn.execute(
        """
        INSERT INTO validation_runs (
            run_id, project_id, import_id, selection_id, scan_id, status,
            created_at, started_at, completed_at,
            total_assets, processed_assets, successful_assets, failed_assets,
            error_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.project_id,
            run.import_id,
            run.selection_id,
            run.scan_id,
            run.status.value,
            run.created_at.isoformat(),
            run.started_at.isoformat() if run.started_at else None,
            run.completed_at.isoformat() if run.completed_at else None,
            run.total_assets,
            run.processed_assets,
            run.successful_assets,
            run.failed_assets,
            run.error_summary,
        ),
    )


def update_run(conn: sqlite3.Connection, run: ValidationRunRecord) -> None:
    conn.execute(
        """
        UPDATE validation_runs
        SET status = ?,
            started_at = ?,
            completed_at = ?,
            total_assets = ?,
            processed_assets = ?,
            successful_assets = ?,
            failed_assets = ?,
            error_summary = ?
        WHERE run_id = ?
        """,
        (
            run.status.value,
            run.started_at.isoformat() if run.started_at else None,
            run.completed_at.isoformat() if run.completed_at else None,
            run.total_assets,
            run.processed_assets,
            run.successful_assets,
            run.failed_assets,
            run.error_summary,
            run.run_id,
        ),
    )


def insert_asset_validation(
    conn: sqlite3.Connection, record: AssetValidationRecord
) -> None:
    conn.execute(
        """
        INSERT INTO asset_validations (
            validation_id, run_id, asset_id, source_relative_path, status,
            checked_size_bytes, checked_mtime_ns, sha256, media_kind,
            container_format, video_codec, audio_codec, width, height,
            duration_seconds, frame_rate_numerator, frame_rate_denominator,
            audio_stream_count, audio_channel_count, embedded_timecode,
            pixel_format, bit_depth, rotation_degrees,
            image_format, image_mode, image_frame_count, has_alpha,
            has_icc_profile, exif_orientation, image_bit_depth, image_is_bigtiff,
            error_code, error_message, validated_at, duplicate_group_id,
            duplicate_hint
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            record.validation_id,
            record.run_id,
            record.asset_id,
            record.source_relative_path,
            record.status.value,
            record.checked_size_bytes,
            record.checked_mtime_ns,
            record.sha256,
            record.media_kind,
            record.container_format,
            record.video_codec,
            record.audio_codec,
            record.width,
            record.height,
            record.duration_seconds,
            record.frame_rate_numerator,
            record.frame_rate_denominator,
            record.audio_stream_count,
            record.audio_channel_count,
            record.embedded_timecode,
            record.pixel_format,
            record.bit_depth,
            record.rotation_degrees,
            record.image_format,
            record.image_mode,
            record.image_frame_count,
            (
                None
                if record.has_alpha is None
                else (1 if record.has_alpha else 0)
            ),
            (
                None
                if record.has_icc_profile is None
                else (1 if record.has_icc_profile else 0)
            ),
            record.exif_orientation,
            record.image_bit_depth,
            (
                None
                if record.image_is_bigtiff is None
                else (1 if record.image_is_bigtiff else 0)
            ),
            record.error_code,
            record.error_message,
            record.validated_at.isoformat(),
            record.duplicate_group_id,
            record.duplicate_hint,
        ),
    )


def list_asset_validations(
    conn: sqlite3.Connection, *, run_id: str
) -> list[AssetValidationRecord]:
    rows = conn.execute(
        """
        SELECT v.*, a.source_group
        FROM asset_validations v
        LEFT JOIN assets a ON a.asset_id = v.asset_id
        WHERE v.run_id = ?
        ORDER BY v.source_relative_path
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_validation(row) for row in rows]


def set_duplicate_on_validation(
    conn: sqlite3.Connection,
    *,
    validation_id: str,
    duplicate_group_id: str,
    hint: str = "potential_content_duplicate",
) -> None:
    conn.execute(
        """
        UPDATE asset_validations
        SET duplicate_group_id = ?, duplicate_hint = ?
        WHERE validation_id = ?
        """,
        (duplicate_group_id, hint, validation_id),
    )


def insert_duplicate_group(
    conn: sqlite3.Connection, group: DuplicateGroupRecord
) -> None:
    conn.execute(
        """
        INSERT INTO duplicate_groups (
            duplicate_group_id, project_id, run_id, sha256,
            member_count, created_at, hint
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group.duplicate_group_id,
            group.project_id,
            group.run_id,
            group.sha256,
            group.member_count,
            group.created_at.isoformat(),
            group.hint,
        ),
    )


def list_duplicate_groups(
    conn: sqlite3.Connection, *, run_id: str
) -> list[DuplicateGroupRecord]:
    rows = conn.execute(
        """
        SELECT * FROM duplicate_groups
        WHERE run_id = ?
        ORDER BY sha256
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_duplicate(row) for row in rows]


def count_duplicate_groups_for_run(conn: sqlite3.Connection, *, run_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM duplicate_groups WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def save_validation_report(
    project_root: Path, report: ValidationRunReport
) -> Path:
    ensure_validation_dirs(project_root)
    target = validation_report_path(project_root, report.run_id)
    assert_path_is_under_discovery_v2(target, project_root)
    # Historische Berichte bleiben erhalten — gleiche run_id darf aktualisiert werden.
    try:
        payload = report.model_dump_json(indent=2)
        _atomic_write_text(target, payload)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Prüfbericht konnte nicht atomar geschrieben werden: {exc}"
        ) from exc

    if report.status in {
        ValidationRunStatus.COMPLETED,
        ValidationRunStatus.COMPLETED_WITH_ERRORS,
        ValidationRunStatus.FAILED,
        ValidationRunStatus.CANCELLED,
    }:
        pointer = ValidationLatestPointer(
            run_id=report.run_id,
            import_id=report.import_id,
            selection_id=report.selection_id,
            scan_id=report.scan_id,
            status=report.status,
            completed_at=report.completed_at,
            report_relative_path=f"validation/runs/{report.run_id}.json",
        )
        latest = latest_validation_pointer_path(project_root)
        assert_path_is_under_discovery_v2(latest, project_root)
        try:
            _atomic_write_text(latest, pointer.model_dump_json(indent=2))
        except OSError as exc:
            raise InventoryArtifactError(
                f"latest_run.json konnte nicht atomar geschrieben werden: {exc}"
            ) from exc
    return target


def load_validation_report(path: Path) -> ValidationRunReport:
    try:
        return ValidationRunReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise InventoryArtifactError(f"Prüfbericht nicht gefunden: {path}") from exc
    except Exception as exc:
        raise InventoryArtifactError(
            f"Prüfbericht ungültig oder beschädigt: {path}"
        ) from exc


def load_latest_validation_report(
    project_root: Path,
) -> tuple[ValidationRunReport | None, str | None]:
    latest = latest_validation_pointer_path(project_root)
    if not latest.exists():
        return None, None
    try:
        pointer = ValidationLatestPointer.model_validate_json(
            latest.read_text(encoding="utf-8")
        )
    except Exception:
        return None, (
            "Die Datei `latest_run.json` ist beschädigt oder ungültig."
        )
    path = get_discovery_v2_root(project_root) / pointer.report_relative_path
    if not path.exists():
        path = validation_report_path(project_root, pointer.run_id)
    if not path.exists():
        return None, f"Prüfbericht `{pointer.run_id}` wurde nicht gefunden."
    try:
        return load_validation_report(path), None
    except InventoryArtifactError as exc:
        return None, str(exc)


def build_report_from_run(
    conn: sqlite3.Connection,
    *,
    run: ValidationRunRecord,
) -> ValidationRunReport:
    validations = list_asset_validations(conn, run_id=run.run_id)
    source_missing = sum(
        1 for v in validations if v.status == AssetValidationStatus.SOURCE_MISSING
    )
    source_changed = sum(
        1 for v in validations if v.status == AssetValidationStatus.SOURCE_CHANGED
    )
    dup_count = count_duplicate_groups_for_run(conn, run_id=run.run_id)
    return ValidationRunReport(
        run_id=run.run_id,
        project_id=run.project_id,
        import_id=run.import_id,
        selection_id=run.selection_id,
        scan_id=run.scan_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_assets=run.total_assets,
        processed_assets=run.processed_assets,
        successful_assets=run.successful_assets,
        failed_assets=run.failed_assets,
        source_missing_count=source_missing,
        source_changed_count=source_changed,
        potential_duplicate_count=dup_count,
        error_summary=run.error_summary,
        registry_sqlite_relative_path="registry/assets.sqlite3",
        report_relative_path=f"validation/runs/{run.run_id}.json",
    )


def new_validation_id() -> str:
    return str(uuid4())


def new_duplicate_group_id() -> str:
    return str(uuid4())


def _row_to_asset(row: sqlite3.Row) -> RegistryAssetRecord:
    return RegistryAssetRecord(
        asset_id=str(row["asset_id"]),
        project_id=str(row["project_id"]),
        source_relative_path=str(row["source_relative_path"]),
        source_group=str(row["source_group"]),
        file_name=str(row["file_name"]),
        extension=str(row["extension"]),
        media_kind=MediaKind(str(row["media_kind"])),
        size_bytes=int(row["size_bytes"]),
        mtime_ns=int(row["mtime_ns"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _row_to_run(row: sqlite3.Row) -> ValidationRunRecord:
    started = row["started_at"]
    completed = row["completed_at"]
    return ValidationRunRecord(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        import_id=str(row["import_id"]),
        selection_id=str(row["selection_id"]),
        scan_id=str(row["scan_id"]),
        status=ValidationRunStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        started_at=datetime.fromisoformat(str(started)) if started else None,
        completed_at=datetime.fromisoformat(str(completed)) if completed else None,
        total_assets=int(row["total_assets"]),
        processed_assets=int(row["processed_assets"]),
        successful_assets=int(row["successful_assets"]),
        failed_assets=int(row["failed_assets"]),
        error_summary=row["error_summary"],
    )


def _row_to_validation(row: sqlite3.Row) -> AssetValidationRecord:
    keys = row.keys()
    source_group = str(row["source_group"]) if "source_group" in keys and row["source_group"] is not None else None
    return AssetValidationRecord(
        validation_id=str(row["validation_id"]),
        run_id=str(row["run_id"]),
        asset_id=str(row["asset_id"]),
        source_relative_path=str(row["source_relative_path"]),
        status=AssetValidationStatus(str(row["status"])),
        checked_size_bytes=row["checked_size_bytes"],
        checked_mtime_ns=row["checked_mtime_ns"],
        sha256=row["sha256"],
        media_kind=row["media_kind"],
        container_format=row["container_format"],
        video_codec=row["video_codec"],
        audio_codec=row["audio_codec"],
        width=row["width"],
        height=row["height"],
        duration_seconds=row["duration_seconds"],
        frame_rate_numerator=row["frame_rate_numerator"],
        frame_rate_denominator=row["frame_rate_denominator"],
        audio_stream_count=row["audio_stream_count"],
        audio_channel_count=(
            row["audio_channel_count"] if "audio_channel_count" in keys else None
        ),
        embedded_timecode=row["embedded_timecode"],
        pixel_format=row["pixel_format"] if "pixel_format" in keys else None,
        bit_depth=row["bit_depth"] if "bit_depth" in keys else None,
        rotation_degrees=(
            float(row["rotation_degrees"])
            if "rotation_degrees" in keys and row["rotation_degrees"] is not None
            else None
        ),
        image_format=row["image_format"] if "image_format" in keys else None,
        image_mode=row["image_mode"] if "image_mode" in keys else None,
        image_frame_count=(
            int(row["image_frame_count"])
            if "image_frame_count" in keys and row["image_frame_count"] is not None
            else None
        ),
        has_alpha=(
            None
            if "has_alpha" not in keys or row["has_alpha"] is None
            else bool(int(row["has_alpha"]))
        ),
        has_icc_profile=(
            None
            if "has_icc_profile" not in keys or row["has_icc_profile"] is None
            else bool(int(row["has_icc_profile"]))
        ),
        exif_orientation=(
            int(row["exif_orientation"])
            if "exif_orientation" in keys and row["exif_orientation"] is not None
            else None
        ),
        image_bit_depth=(
            int(row["image_bit_depth"])
            if "image_bit_depth" in keys and row["image_bit_depth"] is not None
            else None
        ),
        image_is_bigtiff=(
            None
            if "image_is_bigtiff" not in keys or row["image_is_bigtiff"] is None
            else bool(int(row["image_is_bigtiff"]))
        ),
        error_code=row["error_code"],
        error_message=row["error_message"],
        validated_at=datetime.fromisoformat(str(row["validated_at"])),
        duplicate_group_id=row["duplicate_group_id"],
        duplicate_hint=row["duplicate_hint"],
        source_group=source_group,
    )


def _row_to_duplicate(row: sqlite3.Row) -> DuplicateGroupRecord:
    return DuplicateGroupRecord(
        duplicate_group_id=str(row["duplicate_group_id"]),
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]),
        sha256=str(row["sha256"]),
        member_count=int(row["member_count"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        hint=str(row["hint"]),
    )


# Suppress unused import warning for registry_dir used by callers via re-export intent
_ = registry_dir
