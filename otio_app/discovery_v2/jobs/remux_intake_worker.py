"""Worker: Remux-Intake geplanter Discovery-V2-Assets (Stream Copy)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.adapters.ffmpeg_runner import ffmpeg_available
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.media_remux import (
    MediaRemuxError,
    evaluate_remux_gate,
    publish_remux_mp4,
    validate_remux_output_policy,
    evaluate_remux_audio_policy,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    REMUX_WORKING_ACTION,
    REMUX_WORKING_PROFILE_VERSION,
    IntakeAction,
    IntakeRunAssetRecord,
    IntakeRunAssetStatus,
    IntakeRunRecord,
    IntakeRunStatus,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    build_temp_relative_path,
    build_working_relative_path,
    build_report_from_intake_run,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _fail(
    conn,
    asset: IntakeRunAssetRecord,
    *,
    code: str,
    message: str,
    extras: dict[str, dict[str, str | None]],
    audio_policy: str | None = None,
    timecode_policy: str | None = None,
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
    extras[asset.asset_id] = {
        "audio_policy": audio_policy,
        "timecode_policy": timecode_policy,
    }
    return failed


def _reuse(
    conn,
    asset: IntakeRunAssetRecord,
    existing: WorkingMediaRecord,
    extras: dict[str, dict[str, str | None]],
) -> IntakeRunAssetRecord:
    reused = asset.model_copy(
        update={
            "status": IntakeRunAssetStatus.REUSED,
            "source_sha256": existing.source_sha256,
            "output_sha256": existing.output_sha256,
            "working_relative_path": existing.working_relative_path,
            "error_code": "reused",
            "error_message": "Kanonische Remux-Working-Media-Ausgabe wiederverwendet.",
            "processed_at": _now(),
        }
    )
    update_intake_run_asset(conn, reused)
    conn.commit()
    extras[asset.asset_id] = {
        "audio_policy": "reused",
        "timecode_policy": "reused",
    }
    return reused


def _load_validation_map(
    conn, *, validation_run_id: str
) -> dict[str, AssetValidationRecord]:
    return {
        v.asset_id: v
        for v in list_asset_validations(conn, run_id=validation_run_id)
    }


def _validate_existing_remux_output(
    *,
    project_root: Path,
    working_abs: Path,
    expected_sha: str,
    source_probe_for_policy: object | None = None,
) -> tuple[bool, str | None]:
    if not working_abs.is_file() or working_abs.is_symlink():
        return False, "missing"
    try:
        digest = compute_sha256_hex(working_abs)
    except OSError:
        return False, "hash_failed"
    if digest != expected_sha.lower():
        return False, "hash_mismatch"
    try:
        out_probe = probe_source_media(working_abs, media_kind=MediaKind.VIDEO)
    except MediaProbeAdapterError:
        return False, "probe_failed"
    except Exception:  # noqa: BLE001
        return False, "probe_failed"
    if source_probe_for_policy is not None:
        try:
            audio = evaluate_remux_audio_policy(source_probe_for_policy)  # type: ignore[arg-type]
            validate_remux_output_policy(
                source=source_probe_for_policy,  # type: ignore[arg-type]
                output=out_probe,
                expected_audio=audio,
            )
        except MediaRemuxError:
            return False, "policy_failed"
    return True, None


def _process_one_asset(
    conn,
    project_root: Path,
    run: IntakeRunRecord,
    asset: IntakeRunAssetRecord,
    validations: dict[str, AssetValidationRecord],
    extras: dict[str, dict[str, str | None]],
) -> IntakeRunAssetRecord:
    now = _now()
    running = asset.model_copy(
        update={"status": IntakeRunAssetStatus.RUNNING, "processed_at": now}
    )
    update_intake_run_asset(conn, running)
    conn.commit()

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
            message="Plan-Item ohne source_sha256 — Remux nicht möglich.",
            extras=extras,
        )

    gate = evaluate_remux_gate(
        planned_action=asset.planned_action,
        media_kind=asset.media_kind or validation.media_kind,
        video_codec=validation.video_codec,
        pixel_format=validation.pixel_format,
        bit_depth=validation.bit_depth,
        extension=Path(asset.source_relative_path).suffix,
        container_format=validation.container_format,
        source_relative_path=asset.source_relative_path,
        validation_status=validation.status.value,
    )
    if not gate.ok:
        return _fail(
            conn,
            asset,
            code=gate.error_code or "unsupported_codec",
            message=gate.error_message or "Remux-Gate fehlgeschlagen.",
            extras=extras,
        )

    try:
        rel_working = build_working_relative_path(
            asset_id=asset.asset_id,
            source_sha256=source_sha,
            extension=".mp4",
            profile_version=REMUX_WORKING_PROFILE_VERSION,
        )
        rel_temp = build_temp_relative_path(
            run_id=run.run_id,
            asset_id=asset.asset_id,
            extension=".mp4",
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
        action=REMUX_WORKING_ACTION,
        processing_profile_version=REMUX_WORKING_PROFILE_VERSION,
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

    # Größe / mtime / Hash gegen Validation
    try:
        st = source_path.stat()
    except OSError as exc:
        return _fail(
            conn,
            asset,
            code="source_missing",
            message=str(exc),
            extras=extras,
        )
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
            conn,
            asset,
            code="source_hash_mismatch",
            message=str(exc),
            extras=extras,
        )
    if live_sha != source_sha:
        return _fail(
            conn,
            asset,
            code="source_hash_mismatch",
            message="Aktueller SHA-256 weicht von der Validation ab.",
            extras=extras,
        )

    try:
        source_probe = probe_source_media(source_path, media_kind=MediaKind.VIDEO)
    except MediaProbeAdapterError as exc:
        return _fail(conn, asset, code=exc.code, message=exc.message, extras=extras)

    # Audio-/Remux-Gate erneut mit Live-Probe
    gate2 = evaluate_remux_gate(
        planned_action=IntakeAction.REMUX,
        media_kind=MediaKind.VIDEO.value,
        video_codec=source_probe.video_codec,
        pixel_format=source_probe.pixel_format,
        bit_depth=source_probe.bit_depth,
        extension=Path(asset.source_relative_path).suffix,
        container_format=source_probe.container_format,
        source_relative_path=asset.source_relative_path,
        validation_status=AssetValidationStatus.PROBE_SUCCEEDED.value,
        probe=source_probe,
    )
    if not gate2.ok:
        return _fail(
            conn,
            asset,
            code=gate2.error_code or "unsupported_codec",
            message=gate2.error_message or "Live-Remux-Gate fehlgeschlagen.",
            extras=extras,
            audio_policy=gate2.audio.policy_result if gate2.audio else None,
        )

    if existing is not None and _is_completed_status(existing.status):
        if existing.working_relative_path != rel_working:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=(
                    "Registry-Pfad stimmt nicht mit kanonischem Remux-Pfad überein."
                ),
                extras=extras,
            )
        existing_abs = _abs_under_v2(project_root, existing.working_relative_path)
        ok, reason = _validate_existing_remux_output(
            project_root=project_root,
            working_abs=existing_abs,
            expected_sha=existing.output_sha256,
            source_probe_for_policy=source_probe,
        )
        if ok:
            return _reuse(conn, asset, existing, extras)
        return _fail(
            conn,
            asset,
            code="working_media_conflict",
            message=(
                f"Vorhandene Remux-Ausgabe widerspricht Registry/Policy ({reason})."
            ),
            extras=extras,
        )

    # Crash-Fenster: Datei vorhanden, Registry fehlt → reparieren wenn passend.
    if working_abs.exists():
        try:
            existing_sha = compute_sha256_hex(working_abs)
            ok_repair, reason_repair = _validate_existing_remux_output(
                project_root=project_root,
                working_abs=working_abs,
                expected_sha=existing_sha,
                source_probe_for_policy=source_probe,
            )
        except OSError:
            ok_repair, reason_repair = False, "hash_failed"
        if not ok_repair:
            return _fail(
                conn,
                asset,
                code="working_media_conflict",
                message=(
                    "Widersprüchliche Final-Datei am kanonischen Remux-Pfad "
                    f"({reason_repair}) — keine Überschreibung."
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
            extension=".mp4",
            action=REMUX_WORKING_ACTION,
            processing_profile_version=REMUX_WORKING_PROFILE_VERSION,
            status=WorkingMediaStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        upsert_working_media(conn, repaired)
        conn.commit()
        return _reuse(conn, asset, repaired, extras)

    try:
        assert_path_is_under_discovery_v2(working_abs, project_root)
        assert_path_is_under_discovery_v2(temp_abs, project_root)
        result = publish_remux_mp4(
            project_root=project_root,
            source_path=source_path,
            temp_path=temp_abs,
            working_path=working_abs,
            expected_source_sha256=source_sha,
            source_probe=source_probe,
        )
    except MediaRemuxError as exc:
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
        extension=".mp4",
        action=REMUX_WORKING_ACTION,
        processing_profile_version=REMUX_WORKING_PROFILE_VERSION,
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
        extras[asset.asset_id] = {
            "audio_policy": result.audio_policy,
            "timecode_policy": result.timecode_policy,
        }
        return ok
    except Exception as exc:  # noqa: BLE001
        return _fail(
            conn,
            asset,
            code="registry_write_failed",
            message=(
                f"Remux-Datei veröffentlicht, Registry fehlgeschlagen: {exc}"
            ),
            extras=extras,
            audio_policy=result.audio_policy,
            timecode_policy=result.timecode_policy,
        )


def _persist_report(
    project_root: Path,
    run: IntakeRunRecord,
    assets: list[IntakeRunAssetRecord],
    extras: dict[str, dict[str, str | None]],
) -> None:
    try:
        save_intake_run_report(
            project_root,
            build_report_from_intake_run(run, assets=assets, asset_extras=extras),
        )
    except InventoryArtifactError:
        # Run bleibt gültig; Fehlercode im Run-Summary wenn nötig.
        try:
            conn = open_registry(project_root)
            failed = run.model_copy(
                update={
                    "error_summary": (
                        (run.error_summary or "")
                        + " report_write_failed"
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


def process_remux_intake_run(project_root: Path, run_id: str) -> IntakeRunRecord:
    """Verarbeitet einen Remux-Intake-Run außerhalb von Streamlit-Reruns."""
    root = Path(project_root).expanduser().resolve()
    conn = open_registry(root)
    extras: dict[str, dict[str, str | None]] = {}
    try:
        run = get_intake_run(conn, run_id=run_id)
        if run is None:
            raise ValueError(f"Intake-Run nicht gefunden: {run_id}")
        if run.scope != INTAKE_RUN_SCOPE_REMUX_ONLY:
            raise ValueError(f"Intake-Run-Scope ist nicht remux_only: {run.scope}")

        if not ffmpeg_available():
            assets = list_intake_run_assets(conn, run_id=run_id)
            for asset in assets:
                if asset.status in {
                    IntakeRunAssetStatus.PENDING,
                    IntakeRunAssetStatus.RUNNING,
                }:
                    _fail(
                        conn,
                        asset,
                        code="ffmpeg_not_found",
                        message="ffmpeg ist nicht vorhanden.",
                        extras=extras,
                    )
            run = run.model_copy(
                update={
                    "status": IntakeRunStatus.FAILED,
                    "started_at": run.started_at or _now(),
                    "completed_at": _now(),
                    "failed_assets": len(assets),
                    "processed_assets": len(assets),
                    "error_summary": "ffmpeg_not_found",
                }
            )
            update_intake_run(conn, run)
            conn.commit()
            assets = list_intake_run_assets(conn, run_id=run_id)
            _persist_report(root, run, assets, extras)
            return run

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

        for asset in assets:
            if asset.planned_action != IntakeAction.REMUX:
                skipped_asset = asset.model_copy(
                    update={
                        "status": IntakeRunAssetStatus.SKIPPED,
                        "error_code": "skipped_non_remux",
                        "error_message": "Nur Remux-Items werden verarbeitet.",
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
                    else f"{failed} Remux-Asset(s) fehlgeschlagen."
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
