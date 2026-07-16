"""Worker: technische Prüfung der Registry-Assets außerhalb von Streamlit-Reruns."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.asset_registry import RegistryAssetRecord
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    DuplicateGroupRecord,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    build_report_from_run,
    get_run,
    insert_asset_validation,
    insert_duplicate_group,
    list_asset_validations,
    list_assets_for_import,
    new_duplicate_group_id,
    new_validation_id,
    open_registry,
    save_validation_report,
    set_duplicate_on_validation,
    update_run,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path_under_reserved(relative_path: str) -> str | None:
    parts = Path(relative_path).parts
    if not parts:
        return "leerer Pfad"
    if parts[0] == DEFAULT_WORK_SUBDIR or DEFAULT_WORK_SUBDIR in parts:
        return f"Pfad liegt unter `{DEFAULT_WORK_SUBDIR}/`"
    if (
        parts[0] == DEFAULT_DISCOVERY_V2_WORK_SUBDIR
        or DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts
    ):
        return f"Pfad liegt unter `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/`"
    return None


def _resolve_source_path(
    project_root: Path, relative_path: str
) -> tuple[Path | None, str | None, str | None]:
    """Returns (path, error_code, error_message)."""
    reserved = _path_under_reserved(relative_path)
    if reserved:
        code = (
            "path_under_otio_v2"
            if DEFAULT_DISCOVERY_V2_WORK_SUBDIR in Path(relative_path).parts
            or f"`{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/`" in reserved
            else "path_under_otio"
        )
        return None, code, reserved

    root = project_root.expanduser().resolve()
    raw = root / relative_path
    if raw.is_symlink():
        return None, "not_regular_file", f"Keine reguläre Datei: {relative_path}"
    if not raw.exists():
        return None, "source_missing", f"Quelldatei fehlt: {relative_path}"

    try:
        path = raw.resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError):
        return None, "path_outside_root", f"Pfad außerhalb des Projektroots: {relative_path}"

    classic_root = root / DEFAULT_WORK_SUBDIR
    v2_root = root / DEFAULT_DISCOVERY_V2_WORK_SUBDIR
    for banned, code, label in (
        (classic_root, "path_under_otio", DEFAULT_WORK_SUBDIR),
        (v2_root, "path_under_otio_v2", DEFAULT_DISCOVERY_V2_WORK_SUBDIR),
    ):
        try:
            path.relative_to(banned)
        except ValueError:
            pass
        else:
            return None, code, f"Pfad liegt unter `{label}/`"

    if not path.is_file() or path.is_symlink():
        return None, "not_regular_file", f"Keine reguläre Datei: {relative_path}"
    return path, None, None


def _check_source_unchanged(
    path: Path, asset: RegistryAssetRecord
) -> tuple[int, int] | AssetValidationRecord:
    """Prüft Größe/mtime gegen Registry. Bei Abweichung: Record-Template-Felder via Exception-Ersatz."""
    try:
        stat = path.stat()
    except OSError as exc:
        raise _AssetOutcome(
            AssetValidationStatus.VALIDATION_ERROR,
            error_code="read_error",
            error_message=f"Metadaten nicht lesbar: {exc}",
        ) from exc

    size = int(stat.st_size)
    mtime_ns = int(stat.st_mtime_ns)
    if size != int(asset.size_bytes):
        raise _AssetOutcome(
            AssetValidationStatus.SOURCE_CHANGED,
            error_code="source_changed_size",
            error_message=(
                "Dateigröße weicht von der Registry ab. "
                "Bitte Bestandsaufnahme und Auswahl erneut bestätigen."
            ),
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
        )
    if mtime_ns != int(asset.mtime_ns):
        raise _AssetOutcome(
            AssetValidationStatus.SOURCE_CHANGED,
            error_code="source_changed_mtime",
            error_message=(
                "Änderungszeit weicht von der Registry ab. "
                "Bitte Bestandsaufnahme und Auswahl erneut bestätigen."
            ),
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
        )
    return size, mtime_ns


class _AssetOutcome(Exception):
    """Kontrollierter Asset-Ausgang (kein Infrastrukturfehler)."""

    def __init__(
        self,
        status: AssetValidationStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        checked_size_bytes: int | None = None,
        checked_mtime_ns: int | None = None,
        sha256: str | None = None,
        **probe_fields: object,
    ) -> None:
        super().__init__(error_message or status.value)
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.checked_size_bytes = checked_size_bytes
        self.checked_mtime_ns = checked_mtime_ns
        self.sha256 = sha256
        self.probe_fields = probe_fields


def _validate_one_asset(
    project_root: Path,
    asset: RegistryAssetRecord,
    *,
    run_id: str,
) -> AssetValidationRecord:
    now = _now()
    path, err_code, err_msg = _resolve_source_path(
        project_root, asset.source_relative_path
    )
    if path is None:
        status = (
            AssetValidationStatus.SOURCE_MISSING
            if err_code == "source_missing"
            else AssetValidationStatus.VALIDATION_ERROR
        )
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=status,
            media_kind=asset.media_kind.value,
            error_code=err_code,
            error_message=err_msg,
            validated_at=now,
            source_group=asset.source_group,
        )

    try:
        size, mtime_ns = _check_source_unchanged(path, asset)
    except _AssetOutcome as outcome:
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=outcome.status,
            checked_size_bytes=outcome.checked_size_bytes,
            checked_mtime_ns=outcome.checked_mtime_ns,
            media_kind=asset.media_kind.value,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            validated_at=now,
            source_group=asset.source_group,
        )

    if asset.media_kind == MediaKind.OTHER:
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=AssetValidationStatus.UNSUPPORTED_MEDIA_KIND,
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
            media_kind=asset.media_kind.value,
            error_code="unsupported_media_kind",
            error_message="Medientyp wird nicht technisch geprüft.",
            validated_at=now,
            source_group=asset.source_group,
        )

    try:
        digest = compute_sha256_hex(path)
    except OSError as exc:
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=AssetValidationStatus.VALIDATION_ERROR,
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
            media_kind=asset.media_kind.value,
            error_code="hash_error",
            error_message=f"SHA-256 konnte nicht berechnet werden: {exc}",
            validated_at=now,
            source_group=asset.source_group,
        )

    try:
        probe = probe_source_media(path, media_kind=asset.media_kind)
    except MediaProbeAdapterError as exc:
        status = (
            AssetValidationStatus.UNSUPPORTED_MEDIA_KIND
            if exc.code == "unsupported_media_kind"
            else AssetValidationStatus.PROBE_FAILED
        )
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=status,
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
            sha256=digest,
            media_kind=asset.media_kind.value,
            error_code=exc.code,
            error_message=exc.message,
            validated_at=now,
            source_group=asset.source_group,
        )
    except Exception as exc:  # noqa: BLE001 — pro Asset isolieren
        return AssetValidationRecord(
            validation_id=new_validation_id(),
            run_id=run_id,
            asset_id=asset.asset_id,
            source_relative_path=asset.source_relative_path,
            status=AssetValidationStatus.VALIDATION_ERROR,
            checked_size_bytes=size,
            checked_mtime_ns=mtime_ns,
            sha256=digest,
            media_kind=asset.media_kind.value,
            error_code="validation_error",
            error_message=str(exc),
            validated_at=now,
            source_group=asset.source_group,
        )

    return AssetValidationRecord(
        validation_id=new_validation_id(),
        run_id=run_id,
        asset_id=asset.asset_id,
        source_relative_path=asset.source_relative_path,
        status=AssetValidationStatus.PROBE_SUCCEEDED,
        checked_size_bytes=size,
        checked_mtime_ns=mtime_ns,
        sha256=digest,
        media_kind=probe.media_kind,
        container_format=probe.container_format,
        video_codec=probe.video_codec,
        audio_codec=probe.audio_codec,
        width=probe.width,
        height=probe.height,
        duration_seconds=probe.duration_seconds,
        frame_rate_numerator=probe.frame_rate_numerator,
        frame_rate_denominator=probe.frame_rate_denominator,
        audio_stream_count=probe.audio_stream_count,
        embedded_timecode=probe.embedded_timecode,
        validated_at=now,
        source_group=asset.source_group,
    )


def _mark_duplicates(conn: sqlite3.Connection, run: ValidationRunRecord) -> None:
    validations = list_asset_validations(conn, run_id=run.run_id)
    by_hash: dict[str, list[AssetValidationRecord]] = defaultdict(list)
    for record in validations:
        if record.sha256 and record.status == AssetValidationStatus.PROBE_SUCCEEDED:
            by_hash[record.sha256].append(record)

    now = _now()
    for digest, members in by_hash.items():
        if len(members) < 2:
            continue
        group_id = new_duplicate_group_id()
        insert_duplicate_group(
            conn,
            DuplicateGroupRecord(
                duplicate_group_id=group_id,
                project_id=run.project_id,
                run_id=run.run_id,
                sha256=digest,
                member_count=len(members),
                created_at=now,
                hint="potential_content_duplicate",
            ),
        )
        for member in members:
            set_duplicate_on_validation(
                conn,
                validation_id=member.validation_id,
                duplicate_group_id=group_id,
                hint="potential_content_duplicate",
            )


def _persist_report(project_root: Path, conn: sqlite3.Connection, run: ValidationRunRecord) -> None:
    report = build_report_from_run(conn, run=run)
    save_validation_report(project_root, report)


def process_validation_run(project_root: Path, run_id: str) -> ValidationRunRecord:
    """Verarbeitet einen queued Run vollständig. Assetfehler blockieren andere Assets nicht."""
    root = project_root.expanduser().resolve()
    try:
        conn = open_registry(root)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Registry nicht öffnenbar: {exc}") from exc

    try:
        run = get_run(conn, run_id=run_id)
        if run is None:
            raise RuntimeError(f"Validation-Run nicht gefunden: {run_id}")

        run = run.model_copy(
            update={
                "status": ValidationRunStatus.RUNNING,
                "started_at": _now(),
            }
        )
        update_run(conn, run)
        conn.commit()

        try:
            assets = list_assets_for_import(conn, import_id=run.import_id)
            run = run.model_copy(update={"total_assets": len(assets)})
            update_run(conn, run)
            conn.commit()

            for asset in assets:
                record = _validate_one_asset(root, asset, run_id=run.run_id)
                insert_asset_validation(conn, record)
                processed = run.processed_assets + 1
                successful = run.successful_assets + (
                    1 if record.status == AssetValidationStatus.PROBE_SUCCEEDED else 0
                )
                failed = run.failed_assets + (
                    0 if record.status == AssetValidationStatus.PROBE_SUCCEEDED else 1
                )
                run = run.model_copy(
                    update={
                        "processed_assets": processed,
                        "successful_assets": successful,
                        "failed_assets": failed,
                    }
                )
                update_run(conn, run)
                conn.commit()

            _mark_duplicates(conn, run)
            conn.commit()

            final_status = (
                ValidationRunStatus.COMPLETED
                if run.failed_assets == 0
                else ValidationRunStatus.COMPLETED_WITH_ERRORS
            )
            run = run.model_copy(
                update={
                    "status": final_status,
                    "completed_at": _now(),
                }
            )
            update_run(conn, run)
            conn.commit()
            try:
                _persist_report(root, conn, run)
            except InventoryArtifactError as exc:
                run = run.model_copy(
                    update={
                        "status": ValidationRunStatus.FAILED,
                        "error_summary": f"Prüfbericht konnte nicht geschrieben werden: {exc}",
                        "completed_at": _now(),
                    }
                )
                update_run(conn, run)
                conn.commit()
            return run

        except sqlite3.Error as exc:
            run = run.model_copy(
                update={
                    "status": ValidationRunStatus.FAILED,
                    "error_summary": f"SQLite-Fehler: {exc}",
                    "completed_at": _now(),
                }
            )
            try:
                update_run(conn, run)
                conn.commit()
                _persist_report(root, conn, run)
            except Exception:  # noqa: BLE001
                pass
            return run
        except Exception as exc:  # noqa: BLE001 — globaler Infrastrukturfehler
            run = run.model_copy(
                update={
                    "status": ValidationRunStatus.FAILED,
                    "error_summary": str(exc),
                    "completed_at": _now(),
                }
            )
            try:
                update_run(conn, run)
                conn.commit()
                _persist_report(root, conn, run)
            except Exception:  # noqa: BLE001
                pass
            return run
    finally:
        conn.close()
