"""Worker: bytegenaue Copy-Übernahme geplanter Discovery-V2-Assets."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.adapters.byte_copy import ByteCopyError, publish_byte_exact_copy
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_temp_relative_path,
    build_working_relative_path,
    build_report_from_intake_run,
    get_intake_run,
    get_working_media,
    is_legacy_working_relative_path,
    list_intake_run_assets,
    media_temp_dir,
    media_working_dir,
    new_working_media_id,
    normalize_extension,
    open_registry,
    save_intake_run_report,
    update_intake_run,
    update_intake_run_asset,
    upsert_working_media,
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


def _abs_under_v2(project_root: Path, relative: str) -> Path:
    return get_discovery_v2_root(project_root) / relative


def _is_completed_status(status: WorkingMediaStatus) -> bool:
    return status in {WorkingMediaStatus.COMPLETED, WorkingMediaStatus.READY}


def _validate_existing_output(
    *,
    project_root: Path,
    working_abs: Path,
    expected_sha: str,
    media_kind: MediaKind,
    expected_size: int | None = None,
) -> tuple[bool, str | None]:
    if not working_abs.is_file() or working_abs.is_symlink():
        return False, "missing"
    try:
        size = working_abs.stat().st_size
    except OSError:
        return False, "stat_failed"
    if expected_size is not None and size != expected_size:
        return False, "size_mismatch"
    try:
        digest = compute_sha256_hex(working_abs)
    except OSError:
        return False, "hash_failed"
    if digest != expected_sha.lower():
        return False, "hash_mismatch"
    try:
        probe_source_media(working_abs, media_kind=media_kind)
    except MediaProbeAdapterError:
        return False, "probe_failed"
    except Exception:  # noqa: BLE001
        return False, "probe_failed"
    return True, None


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
            elif updated.status in {
                IntakeRunAssetStatus.SKIPPED,
                IntakeRunAssetStatus.REUSED,
            }:
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


def _fail(
    conn,
    asset: IntakeRunAssetRecord,
    *,
    code: str,
    message: str,
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
    return failed


def _reuse(
    conn,
    asset: IntakeRunAssetRecord,
    existing: WorkingMediaRecord,
) -> IntakeRunAssetRecord:
    reused = asset.model_copy(
        update={
            "status": IntakeRunAssetStatus.REUSED,
            "source_sha256": existing.source_sha256,
            "output_sha256": existing.output_sha256,
            "working_relative_path": existing.working_relative_path,
            "error_code": "reused",
            "error_message": "Kanonische Working-Media-Ausgabe wiederverwendet.",
            "processed_at": _now(),
        }
    )
    update_intake_run_asset(conn, reused)
    conn.commit()
    return reused


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

    source_sha = (asset.source_sha256 or "").strip().lower()
    if not source_sha:
        return _fail(
            conn,
            asset,
            code="missing_source_sha256",
            message="Plan-Item ohne source_sha256 — Copy nicht möglich.",
        )

    try:
        extension = normalize_extension(
            Path(asset.source_relative_path).suffix,
            source_relative_path=asset.source_relative_path,
        )
        rel_working = build_working_relative_path(
            asset_id=asset.asset_id,
            source_sha256=source_sha,
            extension=extension,
        )
        rel_temp = build_temp_relative_path(
            run_id=run.run_id,
            asset_id=asset.asset_id,
            extension=extension,
        )
    except ValueError as exc:
        return _fail(conn, asset, code="invalid_path", message=str(exc))

    working_abs = _abs_under_v2(project_root, rel_working)
    temp_abs = _abs_under_v2(project_root, rel_temp)
    media_kind = _parse_media_kind(asset.media_kind)

    # Legacy-Pfad (source_relative) nur erkennen — nie überschreiben/als kanonisch nutzen.
    legacy_candidate = (
        get_discovery_v2_root(project_root)
        / "media"
        / "working"
        / asset.source_relative_path
    )
    if legacy_candidate.exists() and is_legacy_working_relative_path(
        f"media/working/{asset.source_relative_path}"
    ):
        # Nur Hinweis im Fehlerpfad, falls später Konflikt; sonst ignorieren.
        pass

    existing = get_working_media(
        conn,
        project_id=run.project_id,
        asset_id=asset.asset_id,
        source_sha256=source_sha,
        action=COPY_WORKING_ACTION,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
    )

    if existing is not None and _is_completed_status(existing.status):
        if existing.working_relative_path != rel_working:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=(
                    "Registry-Pfad stimmt nicht mit kanonischem Working-Media-Pfad überein."
                ),
            )
        existing_abs = _abs_under_v2(project_root, existing.working_relative_path)
        ok, reason = _validate_existing_output(
            project_root=project_root,
            working_abs=existing_abs,
            expected_sha=existing.output_sha256,
            media_kind=media_kind,
        )
        if ok and existing.output_sha256.lower() == source_sha:
            return _reuse(conn, asset, existing)
        return _fail(
            conn,
            asset,
            code="working_media_conflict",
            message=(
                f"Vorhandene Working-Media-Ausgabe widerspricht Registry/Hash "
                f"({reason})."
            ),
        )

    # Crash-Fenster: Datei vorhanden, Registry fehlt → reparieren wenn passend.
    if working_abs.exists():
        ok, reason = _validate_existing_output(
            project_root=project_root,
            working_abs=working_abs,
            expected_sha=source_sha,
            media_kind=media_kind,
        )
        if not ok:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=(
                    "Widersprüchliche Final-Datei am kanonischen Pfad "
                    f"({reason}) — keine Überschreibung."
                ),
            )
        # Registry-Zustand reparieren, Datei behalten.
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
            output_sha256=source_sha,
            media_kind=asset.media_kind,
            extension=extension,
            action=COPY_WORKING_ACTION,
            processing_profile_version=COPY_WORKING_PROFILE_VERSION,
            status=WorkingMediaStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        upsert_working_media(conn, repaired)
        conn.commit()
        return _reuse(conn, asset, repaired)

    source_path, err_code, err_msg = _resolve_source_path(
        project_root, asset.source_relative_path
    )
    if source_path is None:
        return _fail(conn, asset, code=err_code or "source_missing", message=err_msg or "")

    try:
        assert_path_is_under_discovery_v2(working_abs, project_root)
        assert_path_is_under_discovery_v2(temp_abs, project_root)
        result = publish_byte_exact_copy(
            project_root=project_root,
            source_path=source_path,
            temp_path=temp_abs,
            working_path=working_abs,
            media_kind=media_kind,
            expected_source_sha256=source_sha,
        )
    except ByteCopyError as exc:
        return _fail(conn, asset, code=exc.code, message=exc.message)

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
        action=COPY_WORKING_ACTION,
        processing_profile_version=COPY_WORKING_PROFILE_VERSION,
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
        return ok
    except Exception as exc:  # noqa: BLE001
        # Datei bleibt liegen — Wiederholung repariert via Existenzpfad.
        return _fail(
            conn,
            asset,
            code="registry_persist_failed",
            message=(
                f"Working-Media-Datei veröffentlicht, Registry fehlgeschlagen: {exc}"
            ),
        )


def _persist_report(project_root: Path, run: IntakeRunRecord) -> None:
    try:
        save_intake_run_report(project_root, build_report_from_intake_run(run))
    except InventoryArtifactError:
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
    _ = media_working_dir
