"""Worker: TIFF→PNG Image-Convert (image-png-v1) für Discovery V2."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.adapters.image_convert import (
    ImageConvertError,
    publish_image_png_v1,
    validate_existing_png_against_source,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    IMAGE_CONVERT_ACTION,
    IMAGE_PNG_PROFILE_VERSION,
    INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    IntakeAction,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.domain.technical_validation import AssetValidationRecord
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_report_from_intake_run,
    build_temp_relative_path,
    build_working_relative_path,
    get_intake_run,
    get_working_media,
    list_intake_run_assets,
    media_temp_dir,
    new_working_media_id,
    open_registry,
    save_intake_run_report,
    update_intake_run,
    update_intake_run_asset,
    upsert_working_media,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    list_asset_validations,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)

_TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})
Extras = dict[str, dict[str, str | None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extension_of(asset: IntakeRunAssetRecord) -> str:
    return PurePosixPath(asset.source_relative_path).suffix.lower()


def _resolve_source_path(
    project_root: Path, relative_path: str
) -> tuple[Path | None, str | None, str | None]:
    parts = Path(relative_path).parts
    if not parts:
        return None, "invalid_source_path", "leerer Pfad"
    if parts[0] == DEFAULT_WORK_SUBDIR or DEFAULT_WORK_SUBDIR in parts:
        return None, "invalid_source_path", f"Pfad liegt unter `{DEFAULT_WORK_SUBDIR}/`"
    if (
        parts[0] == DEFAULT_DISCOVERY_V2_WORK_SUBDIR
        or DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts
    ):
        return (
            None,
            "invalid_source_path",
            f"Pfad liegt unter `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/`",
        )

    root = project_root.expanduser().resolve()
    raw = root / relative_path
    if raw.is_symlink():
        return None, "invalid_source_path", f"Keine reguläre Datei: {relative_path}"
    if not raw.exists():
        return None, "source_missing", f"Quelldatei fehlt: {relative_path}"
    try:
        path = raw.resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError):
        return (
            None,
            "invalid_source_path",
            f"Pfad außerhalb des Projektroots: {relative_path}",
        )

    for banned, label in (
        (root / DEFAULT_WORK_SUBDIR, DEFAULT_WORK_SUBDIR),
        (root / DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_DISCOVERY_V2_WORK_SUBDIR),
    ):
        try:
            path.relative_to(banned)
        except ValueError:
            pass
        else:
            return None, "invalid_source_path", f"Pfad liegt unter `{label}/`"

    if not path.is_file() or path.is_symlink():
        return None, "invalid_source_path", f"Keine reguläre Datei: {relative_path}"
    return path, None, None


def _abs_under_v2(project_root: Path, relative: str) -> Path:
    return get_discovery_v2_root(project_root) / relative


def _is_completed_status(status: WorkingMediaStatus) -> bool:
    return status in {WorkingMediaStatus.COMPLETED, WorkingMediaStatus.READY}


def _meta_extras(meta) -> dict[str, str | None]:
    return {
        "source_image_format": meta.source_format,
        "source_image_mode": meta.source_mode,
        "source_width": str(meta.source_width),
        "source_height": str(meta.source_height),
        "output_image_format": meta.output_format,
        "output_image_mode": meta.output_mode,
        "output_width": str(meta.output_width),
        "output_height": str(meta.output_height),
        "orientation_result": (
            "applied" if meta.orientation_applied else "unchanged"
        ),
        "alpha_result": "preserved" if meta.output_has_alpha else "none",
        "pixel_digest": meta.pixel_digest,
    }


def _fail(
    conn,
    asset: IntakeRunAssetRecord,
    *,
    code: str,
    message: str,
    extras: Extras,
) -> IntakeRunAssetRecord:
    failed = asset.model_copy(
        update={
            "status": IntakeRunAssetStatus.FAILED,
            "error_code": code,
            "error_message": message,
            "processed_at": _now(),
        }
    )
    update_intake_run_asset(conn, failed)
    conn.commit()
    extras[asset.asset_id] = extras.get(asset.asset_id, {})
    return failed


def _reuse(
    conn,
    asset: IntakeRunAssetRecord,
    existing: WorkingMediaRecord,
    extras: Extras,
    meta_extra: dict[str, str | None] | None = None,
) -> IntakeRunAssetRecord:
    reused = asset.model_copy(
        update={
            "status": IntakeRunAssetStatus.REUSED,
            "source_sha256": existing.source_sha256,
            "output_sha256": existing.output_sha256,
            "working_relative_path": existing.working_relative_path,
            "error_code": "reused",
            "error_message": "Kanonische image-png-v1-Ausgabe wiederverwendet.",
            "processed_at": _now(),
        }
    )
    update_intake_run_asset(conn, reused)
    conn.commit()
    extras[asset.asset_id] = meta_extra or {}
    return reused


def _load_validation_map(
    conn, *, validation_run_id: str
) -> dict[str, AssetValidationRecord]:
    return {
        v.asset_id: v
        for v in list_asset_validations(conn, run_id=validation_run_id)
    }


def _process_one_asset(
    conn,
    project_root: Path,
    run: IntakeRunRecord,
    asset: IntakeRunAssetRecord,
    validations: dict[str, AssetValidationRecord],
    extras: Extras,
) -> IntakeRunAssetRecord:
    running = asset.model_copy(
        update={"status": IntakeRunAssetStatus.RUNNING, "processed_at": _now()}
    )
    update_intake_run_asset(conn, running)
    conn.commit()

    if asset.planned_action != IntakeAction.TRANSCODE:
        return _fail(
            conn,
            asset,
            code="unsupported_media_kind",
            message="Nur Transcode-Items werden verarbeitet.",
            extras=extras,
        )
    if (asset.media_kind or "").lower() != MediaKind.IMAGE.value:
        return _fail(
            conn,
            asset,
            code="unsupported_media_kind",
            message=f"Medientyp ist kein Image: {asset.media_kind}",
            extras=extras,
        )

    ext = _extension_of(asset)
    if ext not in _TIFF_EXTENSIONS:
        return _fail(
            conn,
            asset,
            code="image_format_unsupported",
            message=f"Nur TIFF-Dateien (.tif/.tiff), nicht {ext or '—'}.",
            extras=extras,
        )

    validation = validations.get(asset.asset_id)
    if validation is None:
        return _fail(
            conn,
            asset,
            code="stale_plan",
            message="Keine Validation für Asset im aktuellen Validation-Run.",
            extras=extras,
        )

    source_sha = (asset.source_sha256 or validation.sha256 or "").strip().lower()
    if not source_sha:
        return _fail(
            conn,
            asset,
            code="source_hash_mismatch",
            message="Plan-Item ohne source_sha256.",
            extras=extras,
        )

    try:
        rel_working = build_working_relative_path(
            asset_id=asset.asset_id,
            source_sha256=source_sha,
            extension=".png",
            profile_version=IMAGE_PNG_PROFILE_VERSION,
        )
        rel_temp = build_temp_relative_path(
            run_id=run.run_id,
            asset_id=asset.asset_id,
            extension=".png",
        )
    except ValueError as exc:
        return _fail(
            conn, asset, code="invalid_source_path", message=str(exc), extras=extras
        )

    working_abs = _abs_under_v2(project_root, rel_working)
    temp_abs = _abs_under_v2(project_root, rel_temp)

    existing = get_working_media(
        conn,
        project_id=run.project_id,
        asset_id=asset.asset_id,
        source_sha256=source_sha,
        action=IMAGE_CONVERT_ACTION,
        processing_profile_version=IMAGE_PNG_PROFILE_VERSION,
    )

    source_path, err_code, err_msg = _resolve_source_path(
        project_root, asset.source_relative_path
    )
    if source_path is None:
        return _fail(
            conn,
            asset,
            code=err_code or "source_missing",
            message=err_msg or "Quelle ungültig.",
            extras=extras,
        )

    try:
        st = source_path.stat()
    except OSError as exc:
        return _fail(conn, asset, code="source_missing", message=str(exc), extras=extras)
    if (
        validation.checked_size_bytes is not None
        and st.st_size != validation.checked_size_bytes
    ):
        return _fail(
            conn,
            asset,
            code="source_changed",
            message="Quellgröße weicht von der Validation ab.",
            extras=extras,
        )
    if (
        validation.checked_mtime_ns is not None
        and st.st_mtime_ns != validation.checked_mtime_ns
    ):
        return _fail(
            conn,
            asset,
            code="source_changed",
            message="Quell-mtime weicht von der Validation ab.",
            extras=extras,
        )
    try:
        live_sha = compute_sha256_hex(source_path)
    except OSError as exc:
        return _fail(
            conn, asset, code="source_hash_mismatch", message=str(exc), extras=extras
        )
    if live_sha != source_sha:
        return _fail(
            conn,
            asset,
            code="source_hash_mismatch",
            message="Aktueller SHA-256 weicht von der Validation ab.",
            extras=extras,
        )

    if existing is not None and _is_completed_status(existing.status):
        if existing.working_relative_path != rel_working:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message="Registry-Pfad stimmt nicht mit kanonischem PNG-Pfad überein.",
                extras=extras,
            )
        existing_abs = _abs_under_v2(project_root, existing.working_relative_path)
        try:
            meta = validate_existing_png_against_source(
                source_path=source_path,
                output_path=existing_abs,
                source_extension=ext,
                expected_output_sha256=existing.output_sha256,
            )
        except ImageConvertError as exc:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=f"Vorhandene PNG-Ausgabe widerspricht Policy ({exc.code}).",
                extras=extras,
            )
        return _reuse(conn, asset, existing, extras, _meta_extras(meta))

    if working_abs.exists():
        try:
            existing_sha = compute_sha256_hex(working_abs)
            meta = validate_existing_png_against_source(
                source_path=source_path,
                output_path=working_abs,
                source_extension=ext,
                expected_output_sha256=existing_sha,
            )
        except (OSError, ImageConvertError) as exc:
            code = getattr(exc, "code", "working_media_conflict")
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=(
                    "Widersprüchliche Final-Datei am kanonischen PNG-Pfad "
                    f"({code})."
                ),
                extras=extras,
            )
        repaired = WorkingMediaRecord(
            working_media_id=(
                existing.working_media_id if existing else new_working_media_id()
            ),
            project_id=run.project_id,
            asset_id=asset.asset_id,
            plan_id=run.plan_id,
            intake_run_id=run.run_id,
            source_relative_path=asset.source_relative_path,
            working_relative_path=rel_working,
            source_sha256=source_sha,
            output_sha256=existing_sha,
            media_kind=asset.media_kind,
            extension=".png",
            action=IMAGE_CONVERT_ACTION,
            processing_profile_version=IMAGE_PNG_PROFILE_VERSION,
            status=WorkingMediaStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        upsert_working_media(conn, repaired)
        conn.commit()
        return _reuse(conn, asset, repaired, extras, _meta_extras(meta))

    try:
        assert_path_is_under_discovery_v2(working_abs, project_root)
        assert_path_is_under_discovery_v2(temp_abs, project_root)
        result = publish_image_png_v1(
            project_root=project_root,
            source_path=source_path,
            temp_path=temp_abs,
            working_path=working_abs,
            expected_source_sha256=source_sha,
            source_extension=ext,
        )
    except ImageConvertError as exc:
        return _fail(
            conn,
            asset,
            code=exc.code,
            message=exc.message,
            extras=extras,
        )

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
        extension=".png",
        action=IMAGE_CONVERT_ACTION,
        processing_profile_version=IMAGE_PNG_PROFILE_VERSION,
        status=WorkingMediaStatus.COMPLETED,
        created_at=_now(),
        updated_at=_now(),
    )
    try:
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
        extras[asset.asset_id] = _meta_extras(result.meta)
        return ok
    except Exception as exc:  # noqa: BLE001
        return _fail(
            conn,
            asset,
            code="registry_write_failed",
            message=f"PNG veröffentlicht, Registry fehlgeschlagen: {exc}",
            extras=extras,
        )


def _persist_report(
    project_root: Path,
    run: IntakeRunRecord,
    assets: list[IntakeRunAssetRecord],
    extras: Extras,
) -> None:
    try:
        report = build_report_from_intake_run(
            run, assets=assets, asset_extras=extras
        )
        save_intake_run_report(project_root, report)
    except InventoryArtifactError:
        try:
            conn = open_registry(project_root)
            failed = run.model_copy(
                update={
                    "error_summary": (
                        (run.error_summary or "") + " report_write_failed"
                    ).strip()
                }
            )
            update_intake_run(conn, failed)
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001
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


def process_image_convert_run(project_root: Path, run_id: str) -> IntakeRunRecord:
    root = Path(project_root).expanduser().resolve()
    conn = open_registry(root)
    extras: Extras = {}
    try:
        run = get_intake_run(conn, run_id=run_id)
        if run is None:
            raise ValueError(f"Intake-Run nicht gefunden: {run_id}")
        if run.scope != INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY:
            raise ValueError(
                f"Intake-Run-Scope ist nicht image_convert_only: {run.scope}"
            )

        run = run.model_copy(
            update={
                "status": IntakeRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
            }
        )
        update_intake_run(conn, run)
        conn.commit()

        validations = _load_validation_map(
            conn, validation_run_id=run.validation_run_id
        )
        assets = list_intake_run_assets(conn, run_id=run_id)
        succeeded = 0
        failed = 0
        skipped = 0
        converted = 0
        reused = 0

        for asset in assets:
            if (
                asset.planned_action != IntakeAction.TRANSCODE
                or (asset.media_kind or "").lower() != MediaKind.IMAGE.value
                or _extension_of(asset) not in _TIFF_EXTENSIONS
            ):
                skipped_asset = asset.model_copy(
                    update={
                        "status": IntakeRunAssetStatus.SKIPPED,
                        "error_code": "skipped_non_tiff_image_convert",
                        "error_message": "Nur TIFF-Image-Convert-Items werden verarbeitet.",
                        "processed_at": _now(),
                    }
                )
                update_intake_run_asset(conn, skipped_asset)
                conn.commit()
                skipped += 1
                continue

            updated = _process_one_asset(
                conn, root, run, asset, validations, extras
            )
            if updated.status == IntakeRunAssetStatus.SUCCEEDED:
                succeeded += 1
                converted += 1
            elif updated.status == IntakeRunAssetStatus.REUSED:
                reused += 1
            elif updated.status == IntakeRunAssetStatus.SKIPPED:
                skipped += 1
            else:
                failed += 1
            processed = succeeded + failed + skipped + reused
            run = run.model_copy(
                update={
                    "processed_assets": processed,
                    "succeeded_assets": succeeded,
                    "failed_assets": failed,
                    "skipped_assets": skipped,
                    "converted_assets": converted,
                    "reused_assets": reused,
                    "copied_assets": 0,
                    "remuxed_assets": 0,
                    "transcoded_assets": 0,
                }
            )
            update_intake_run(conn, run)
            conn.commit()

        if failed == 0:
            final_status = IntakeRunStatus.COMPLETED
        elif succeeded + skipped + reused > 0:
            final_status = IntakeRunStatus.COMPLETED_WITH_ERRORS
        else:
            final_status = IntakeRunStatus.FAILED

        run = run.model_copy(
            update={
                "status": final_status,
                "completed_at": _now(),
                "processed_assets": succeeded + failed + skipped + reused,
                "succeeded_assets": succeeded,
                "failed_assets": failed,
                "skipped_assets": skipped,
                "converted_assets": converted,
                "reused_assets": reused,
                "copied_assets": 0,
                "remuxed_assets": 0,
                "transcoded_assets": 0,
                "error_summary": (
                    None
                    if failed == 0
                    else f"{failed} TIFF-Convert-Asset(s) fehlgeschlagen."
                ),
            }
        )
        update_intake_run(conn, run)
        conn.commit()
        final_assets = list_intake_run_assets(conn, run_id=run_id)
        _persist_report(root, run, final_assets, extras)
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
                assets = list_intake_run_assets(conn, run_id=run_id)
                _persist_report(root, failed_run, assets, extras)
                return failed_run
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()
        _cleanup_temp_dir(root, run_id)
