"""Worker: bytegenaue Copy-Übernahme geplanter Discovery-V2-Assets."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.adapters.byte_copy import ByteCopyError, publish_byte_exact_copy
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_report_from_intake_run,
    get_intake_run,
    get_working_media,
    list_intake_run_assets,
    media_temp_dir,
    media_working_dir,
    new_working_media_id,
    open_registry,
    save_intake_run_report,
    update_intake_run,
    update_intake_run_asset,
    upsert_working_media,
    working_relative_path_for,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_source_path(
    project_root: Path, relative_path: str
) -> tuple[Path | None, str | None, str | None]:
    parts = Path(relative_path).parts
    if not parts:
        return None, "leerer_pfad", "leerer Pfad"
    if parts[0] == DEFAULT_WORK_SUBDIR or DEFAULT_WORK_SUBDIR in parts:
        return None, "path_under_otio", f"Pfad liegt unter `{DEFAULT_WORK_SUBDIR}/`"
    if (
        parts[0] == DEFAULT_DISCOVERY_V2_WORK_SUBDIR
        or DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts
    ):
        return (
            None,
            "path_under_otio_v2",
            f"Pfad liegt unter `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/`",
        )

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

    for banned, code, label in (
        (root / DEFAULT_WORK_SUBDIR, "path_under_otio", DEFAULT_WORK_SUBDIR),
        (
            root / DEFAULT_DISCOVERY_V2_WORK_SUBDIR,
            "path_under_otio_v2",
            DEFAULT_DISCOVERY_V2_WORK_SUBDIR,
        ),
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


def _parse_media_kind(value: str) -> MediaKind:
    try:
        return MediaKind(value)
    except ValueError:
        return MediaKind.OTHER


def process_copy_intake_run(project_root: Path, run_id: str) -> IntakeRunRecord:
    """Verarbeitet einen Copy-Intake-Run außerhalb von Streamlit-Reruns."""
    root = Path(project_root).expanduser().resolve()
    conn = open_registry(root)
    try:
        run = get_intake_run(conn, run_id=run_id)
        if run is None:
            raise ValueError(f"Intake-Run nicht gefunden: {run_id}")

        run = run.model_copy(
            update={
                "status": IntakeRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
            }
        )
        update_intake_run(conn, run)
        conn.commit()

        assets = list_intake_run_assets(conn, run_id=run_id)
        succeeded = 0
        failed = 0
        skipped = 0

        for asset in assets:
            updated = _process_one_asset(conn, root, run, asset)
            if updated.status == IntakeRunAssetStatus.SUCCEEDED:
                succeeded += 1
            elif updated.status == IntakeRunAssetStatus.SKIPPED:
                skipped += 1
            else:
                failed += 1
            processed = succeeded + failed + skipped
            run = run.model_copy(
                update={
                    "processed_assets": processed,
                    "succeeded_assets": succeeded,
                    "failed_assets": failed,
                    "skipped_assets": skipped,
                }
            )
            update_intake_run(conn, run)
            conn.commit()

        if failed == 0:
            final_status = IntakeRunStatus.COMPLETED
        elif succeeded + skipped > 0:
            final_status = IntakeRunStatus.COMPLETED_WITH_ERRORS
        else:
            final_status = IntakeRunStatus.FAILED

        run = run.model_copy(
            update={
                "status": final_status,
                "completed_at": _now(),
                "processed_assets": succeeded + failed + skipped,
                "succeeded_assets": succeeded,
                "failed_assets": failed,
                "skipped_assets": skipped,
                "error_summary": (
                    None
                    if failed == 0
                    else f"{failed} Copy-Asset(s) fehlgeschlagen."
                ),
            }
        )
        update_intake_run(conn, run)
        conn.commit()
        _persist_report(root, run)
        return run
    except Exception as exc:  # noqa: BLE001
        try:
            run = get_intake_run(conn, run_id=run_id)
            if run is not None:
                failed_run = run.model_copy(
                    update={
                        "status": IntakeRunStatus.FAILED,
                        "completed_at": _now(),
                        "error_summary": str(exc),
                    }
                )
                update_intake_run(conn, failed_run)
                conn.commit()
                _persist_report(root, failed_run)
                return failed_run
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()
        _cleanup_temp_dir(root, run_id)


def _process_one_asset(
    conn,
    project_root: Path,
    run: IntakeRunRecord,
    asset: IntakeRunAssetRecord,
) -> IntakeRunAssetRecord:
    now = _now()
    running = asset.model_copy(
        update={"status": IntakeRunAssetStatus.RUNNING, "processed_at": now}
    )
    update_intake_run_asset(conn, running)
    conn.commit()

    # Idempotenz: vorhandenes Working Media mit gleichem Source-Hash.
    existing = get_working_media(
        conn, project_id=run.project_id, asset_id=asset.asset_id
    )
    if existing is not None and existing.status == WorkingMediaStatus.READY:
        working_abs = get_discovery_v2_root(project_root) / existing.working_relative_path
        expected = (asset.source_sha256 or existing.source_sha256 or "").lower()
        if working_abs.is_file() and expected:
            try:
                current = compute_sha256_hex(working_abs)
            except OSError:
                current = ""
            if current == expected and current == existing.output_sha256.lower():
                skipped = asset.model_copy(
                    update={
                        "status": IntakeRunAssetStatus.SKIPPED,
                        "source_sha256": existing.source_sha256,
                        "output_sha256": existing.output_sha256,
                        "working_relative_path": existing.working_relative_path,
                        "error_code": "already_present",
                        "error_message": "Working Media bereits byteidentisch vorhanden.",
                        "processed_at": _now(),
                    }
                )
                update_intake_run_asset(conn, skipped)
                conn.commit()
                return skipped

    source_path, err_code, err_msg = _resolve_source_path(
        project_root, asset.source_relative_path
    )
    if source_path is None:
        failed = asset.model_copy(
            update={
                "status": IntakeRunAssetStatus.FAILED,
                "error_code": err_code,
                "error_message": err_msg,
                "processed_at": _now(),
            }
        )
        update_intake_run_asset(conn, failed)
        conn.commit()
        return failed

    rel_working = working_relative_path_for(asset.source_relative_path)
    working_abs = get_discovery_v2_root(project_root) / rel_working
    temp_dir = media_temp_dir(project_root, run.run_id)
    suffix = Path(asset.source_relative_path).suffix
    temp_abs = temp_dir / f"{asset.asset_id}{suffix}"

    try:
        assert_path_is_under_discovery_v2(working_abs, project_root)
        assert_path_is_under_discovery_v2(temp_abs, project_root)
        result = publish_byte_exact_copy(
            project_root=project_root,
            source_path=source_path,
            temp_path=temp_abs,
            working_path=working_abs,
            media_kind=_parse_media_kind(asset.media_kind),
            expected_source_sha256=asset.source_sha256,
        )
    except ByteCopyError as exc:
        failed = asset.model_copy(
            update={
                "status": IntakeRunAssetStatus.FAILED,
                "error_code": exc.code,
                "error_message": exc.message,
                "processed_at": _now(),
            }
        )
        update_intake_run_asset(conn, failed)
        conn.commit()
        return failed

    extension = Path(asset.source_relative_path).suffix.lower()
    wm = WorkingMediaRecord(
        working_media_id=new_working_media_id(),
        project_id=run.project_id,
        asset_id=asset.asset_id,
        plan_id=run.plan_id,
        intake_run_id=run.run_id,
        source_relative_path=asset.source_relative_path,
        working_relative_path=rel_working,
        source_sha256=result.source_sha256,
        output_sha256=result.output_sha256,
        media_kind=asset.media_kind,
        extension=extension,
        status=WorkingMediaStatus.READY,
        created_at=_now(),
        updated_at=_now(),
    )
    upsert_working_media(conn, wm)

    ok = asset.model_copy(
        update={
            "status": IntakeRunAssetStatus.SUCCEEDED,
            "source_sha256": result.source_sha256,
            "output_sha256": result.output_sha256,
            "working_relative_path": rel_working,
            "error_code": None,
            "error_message": None,
            "processed_at": _now(),
        }
    )
    update_intake_run_asset(conn, ok)
    conn.commit()
    return ok


def _persist_report(project_root: Path, run: IntakeRunRecord) -> None:
    try:
        save_intake_run_report(project_root, build_report_from_intake_run(run))
    except InventoryArtifactError:
        # Bericht optional — Run-Status in SQLite bleibt Wahrheit.
        pass


def _cleanup_temp_dir(project_root: Path, run_id: str) -> None:
    temp = media_temp_dir(project_root, run_id)
    try:
        if not temp.exists():
            return
        for child in temp.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        temp.rmdir()
    except OSError:
        pass
    # leeres media/temp belassen; media/working bleibt
    _ = media_working_dir
