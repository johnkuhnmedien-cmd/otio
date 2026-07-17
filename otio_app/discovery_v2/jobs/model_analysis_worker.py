"""Worker: Discovery-V2 fake model analysis via central vision gateway."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.adapters.vision_config import load_vision_config
from otio_app.discovery_v2.adapters.vision_gateway import (
    DiscoveryVisionGateway,
    VisionGatewayError,
)
from otio_app.discovery_v2.analysis_paths import (
    resolve_analysis_relative_path,
)
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    build_report_from_analysis_run,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_MODEL_PROFILE,
    ANALYSIS_RUN_SCOPE_MODEL,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH,
    ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING,
    ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED,
    AnalysisModelAssetStatus,
    ModelAnalysisAttemptRecord,
    VisionFramePart,
    VisionGatewayRequest,
    VisualObservationRecord,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    find_completed_model_analysis_attempt,
    get_analysis_identity,
    get_analysis_run,
    get_visual_observation_for_versions,
    insert_model_analysis_attempt,
    insert_visual_observation,
    list_analysis_run_assets,
    list_representative_frames,
    new_model_analysis_attempt_id,
    new_visual_observation_id,
    next_model_analysis_attempt_number,
    open_analysis_registry,
    save_analysis_run_report,
    save_visual_observation_json,
    update_analysis_run,
    update_analysis_run_asset,
    update_model_analysis_attempt,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)


class ModelAnalysisWorkerError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_model_analysis_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    config = load_vision_config()
    conn = open_analysis_registry(root)
    try:
        run = get_analysis_run(conn, run_id=run_id)
        if run is None:
            return
        run = run.model_copy(
            update={
                "status": AnalysisRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
                "scope": ANALYSIS_RUN_SCOPE_MODEL,
                "analysis_profile_version": ANALYSIS_MODEL_PROFILE,
            }
        )
        update_analysis_run(conn, run)
        conn.commit()

        for asset in list_analysis_run_assets(conn, run_id=run.run_id):
            if asset.status in {
                AnalysisModelAssetStatus.COMPLETED,
                AnalysisModelAssetStatus.REUSED,
                AnalysisModelAssetStatus.NOT_APPLICABLE,
            }:
                continue
            try:
                _process_one_model_asset(conn, root, run, asset, config=config)
            except ModelAnalysisWorkerError as exc:
                _fail_asset(conn, asset, code=exc.code, message=exc.message)
            except VisionGatewayError as exc:
                _fail_asset(conn, asset, code=exc.code, message=exc.message)
            except Exception as exc:  # noqa: BLE001
                _fail_asset(
                    conn,
                    asset,
                    code=ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED,
                    message=str(exc),
                )

        final_run = _finalize_run(conn, run.run_id)
        try:
            save_analysis_run_report(
                root,
                build_report_from_analysis_run(conn, final_run),
            )
        except (InventoryArtifactError, OSError, ValueError) as exc:
            final_run = final_run.model_copy(
                update={
                    "status": (
                        AnalysisRunStatus.COMPLETED_WITH_ERRORS
                        if final_run.status == AnalysisRunStatus.COMPLETED
                        else final_run.status
                    ),
                    "error_summary": _append_error_summary(
                        final_run.error_summary,
                        f"report_write_failed: {exc}",
                    ),
                }
            )
            update_analysis_run(conn, final_run)
            conn.commit()
    finally:
        conn.close()


def _process_one_model_asset(
    conn: sqlite3.Connection,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
    *,
    config,
) -> None:
    if not asset.analysis_identity_id:
        raise ModelAnalysisWorkerError("analysis_identity_missing")
    identity = get_analysis_identity(
        conn,
        analysis_identity_id=asset.analysis_identity_id,
    )
    if identity is None:
        raise ModelAnalysisWorkerError("analysis_identity_missing")
    frames = list_representative_frames(
        conn,
        analysis_identity_id=asset.analysis_identity_id,
    )
    if not frames:
        raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING)
    if (
        asset.media_kind == "video"
        and len(frames) > config.max_frames_per_video
    ) or len(frames) > config.max_frames_per_run:
        raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED)

    frame_parts = _verify_frame_parts(
        project_root,
        frames,
        max_frame_bytes=config.max_frame_bytes,
    )
    total_bytes = sum(frame.file_size_bytes for frame in frame_parts)
    if total_bytes > config.max_run_bytes:
        raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED)
    fingerprint = frame_hash_fingerprint([frame.frame_sha256 for frame in frame_parts])

    cached_attempt = find_completed_model_analysis_attempt(
        conn,
        analysis_identity_id=asset.analysis_identity_id,
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        frame_hash_fingerprint=fingerprint,
    )
    cached_observation = get_visual_observation_for_versions(
        conn,
        analysis_identity_id=asset.analysis_identity_id,
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        frame_hash_fingerprint=fingerprint,
    )
    attempt_number = next_model_analysis_attempt_number(
        conn,
        analysis_identity_id=asset.analysis_identity_id,
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        frame_hash_fingerprint=fingerprint,
    )
    if cached_attempt is not None and cached_observation is not None:
        attempt = ModelAnalysisAttemptRecord(
            attempt_id=new_model_analysis_attempt_id(),
            analysis_identity_id=asset.analysis_identity_id,
            project_id=run.project_id,
            asset_id=asset.asset_id,
            run_id=run.run_id,
            provider=config.provider,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            prompt_version=config.prompt_version,
            response_schema_version=config.response_schema_version,
            status="reused",
            attempt_number=attempt_number,
            error_code="reused",
            error_message="Vorhandene Modellanalyse wiederverwendet.",
            frame_count=len(frame_parts),
            frame_hash_fingerprint=fingerprint,
            created_at=_now(),
            completed_at=_now(),
        )
        insert_model_analysis_attempt(conn, attempt)
        _mark_asset(
            conn,
            asset,
            status=AnalysisModelAssetStatus.REUSED,
            error_code="reused",
            error_message="Vorhandene Modellanalyse wiederverwendet.",
        )
        conn.commit()
        return

    analyzing = asset.model_copy(update={"status": AnalysisModelAssetStatus.ANALYZING})
    update_analysis_run_asset(conn, analyzing)
    attempt = ModelAnalysisAttemptRecord(
        attempt_id=new_model_analysis_attempt_id(),
        analysis_identity_id=asset.analysis_identity_id,
        project_id=run.project_id,
        asset_id=asset.asset_id,
        run_id=run.run_id,
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        status="running",
        attempt_number=attempt_number,
        frame_count=len(frame_parts),
        frame_hash_fingerprint=fingerprint,
        created_at=_now(),
    )
    insert_model_analysis_attempt(conn, attempt)
    conn.commit()

    request = VisionGatewayRequest(
        project_id=run.project_id,
        run_id=run.run_id,
        asset_id=asset.asset_id,
        analysis_identity_id=asset.analysis_identity_id,
        media_kind=asset.media_kind,
        prompt=_prompt_for_request(),
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompt_version,
        response_schema_version=config.response_schema_version,
        frames=frame_parts,
    )
    try:
        response = DiscoveryVisionGateway(config=config).analyze(request)
        observation_id = new_visual_observation_id()
        relative_json_path, observation_json = save_visual_observation_json(
            project_root,
            analysis_identity_id=asset.analysis_identity_id,
            observation_id=observation_id,
            observation=response.observation,
            provider=response.provider,
            model_identifier=response.model_identifier,
            gateway_version=response.gateway_version,
            prompt_version=response.prompt_version,
            response_schema_version=response.response_schema_version,
        )
        insert_visual_observation(
            conn,
            VisualObservationRecord(
                observation_id=observation_id,
                analysis_identity_id=asset.analysis_identity_id,
                project_id=run.project_id,
                asset_id=asset.asset_id,
                attempt_id=attempt.attempt_id,
                provider=response.provider,
                model_identifier=response.model_identifier,
                gateway_version=response.gateway_version,
                prompt_version=response.prompt_version,
                response_schema_version=response.response_schema_version,
                frame_hash_fingerprint=fingerprint,
                relative_json_path=relative_json_path,
                observation_json=observation_json,
                created_at=_now(),
            ),
        )
        update_model_analysis_attempt(
            conn,
            attempt.model_copy(
                update={
                    "status": "completed",
                    "completed_at": _now(),
                }
            ),
        )
        _mark_asset(conn, asset, status=AnalysisModelAssetStatus.COMPLETED)
        conn.commit()
    except VisionGatewayError as exc:
        update_model_analysis_attempt(
            conn,
            attempt.model_copy(
                update={
                    "status": "failed",
                    "error_code": exc.code,
                    "error_message": exc.message,
                    "completed_at": _now(),
                }
            ),
        )
        conn.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        update_model_analysis_attempt(
            conn,
            attempt.model_copy(
                update={
                    "status": "failed",
                    "error_code": ANALYSIS_ERROR_ANALYSIS_REGISTRY_WRITE_FAILED,
                    "error_message": str(exc),
                    "completed_at": _now(),
                }
            ),
        )
        conn.commit()
        raise


def _verify_frame_parts(
    project_root: Path,
    frames,
    *,
    max_frame_bytes: int,
) -> list[VisionFramePart]:
    parts: list[VisionFramePart] = []
    for frame in sorted(frames, key=lambda item: item.ordinal):
        if not frame.relative_path.startswith("analysis/frames/"):
            raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING)
        path = resolve_analysis_relative_path(project_root, frame.relative_path)
        if path.is_symlink() or not path.is_file():
            raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING)
        try:
            stat = path.stat()
        except OSError as exc:
            raise ModelAnalysisWorkerError(
                ANALYSIS_ERROR_ANALYSIS_FRAME_MISSING,
                str(exc),
            ) from exc
        if stat.st_size > max_frame_bytes:
            raise ModelAnalysisWorkerError(ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED)
        if stat.st_size != frame.file_size_bytes:
            raise ModelAnalysisWorkerError(
                ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH
            )
        digest = compute_sha256_hex(path)
        if digest.lower() != frame.frame_sha256.lower():
            raise ModelAnalysisWorkerError(
                ANALYSIS_ERROR_ANALYSIS_FRAME_HASH_MISMATCH
            )
        parts.append(
            VisionFramePart(
                frame_id=frame.frame_id,
                relative_path=frame.relative_path,
                mime_type=_mime_type(frame.relative_path),
                frame_sha256=frame.frame_sha256.lower(),
                file_size_bytes=frame.file_size_bytes,
                ordinal=frame.ordinal,
            )
        )
    return parts


def frame_hash_fingerprint(frame_hashes: list[str]) -> str:
    normalized = "\n".join(sorted(item.lower() for item in frame_hashes))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _mime_type(relative_path: str) -> str:
    lower = relative_path.lower()
    if lower.endswith(".png"):
        return "image/png"
    return "image/jpeg"


def _prompt_for_request() -> str:
    return (
        "Beschreibe die persistierten Discovery-V2 Representative Frames "
        "als strukturierte VisualObservation. Unbekanntes bleibt unknown/null."
    )


def _mark_asset(
    conn: sqlite3.Connection,
    asset: AnalysisRunAsset,
    *,
    status: AnalysisModelAssetStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    update_analysis_run_asset(
        conn,
        asset.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": _now(),
            }
        ),
    )


def _fail_asset(
    conn: sqlite3.Connection,
    asset: AnalysisRunAsset,
    *,
    code: str,
    message: str,
) -> None:
    _mark_asset(
        conn,
        asset,
        status=AnalysisModelAssetStatus.FAILED,
        error_code=code,
        error_message=message,
    )
    conn.commit()


def _finalize_run(conn: sqlite3.Connection, run_id: str) -> AnalysisRun:
    run = get_analysis_run(conn, run_id=run_id)
    if run is None:
        raise ModelAnalysisWorkerError("analysis_run_missing", f"Run fehlt: {run_id}")
    assets = list_analysis_run_assets(conn, run_id=run_id)
    total = len(assets)
    completed = sum(
        1 for asset in assets if asset.status == AnalysisModelAssetStatus.COMPLETED
    )
    reused = sum(1 for asset in assets if asset.status == AnalysisModelAssetStatus.REUSED)
    not_applicable = sum(
        1 for asset in assets if asset.status == AnalysisModelAssetStatus.NOT_APPLICABLE
    )
    failed = sum(1 for asset in assets if asset.status == AnalysisModelAssetStatus.FAILED)
    interrupted = sum(
        1 for asset in assets if asset.status == AnalysisModelAssetStatus.INTERRUPTED
    )
    error_count = failed + interrupted
    if total > 0 and error_count >= total:
        status = AnalysisRunStatus.FAILED
    elif error_count > 0:
        status = AnalysisRunStatus.COMPLETED_WITH_ERRORS
    else:
        status = AnalysisRunStatus.COMPLETED
    summary = None
    if error_count:
        first_error = next(
            (asset.error_code for asset in assets if asset.error_code),
            "model_analysis_failed",
        )
        summary = f"{error_count} Asset(s) fehlgeschlagen ({first_error})."
    final = run.model_copy(
        update={
            "status": status,
            "completed_at": _now(),
            "total_assets": total,
            "prepared_assets": completed,
            "reused_assets": reused,
            "not_applicable_assets": not_applicable,
            "failed_assets": failed,
            "interrupted_assets": interrupted,
            "error_summary": summary,
        }
    )
    update_analysis_run(conn, final)
    conn.commit()
    return final


def _append_error_summary(current: str | None, addition: str) -> str:
    if current:
        return f"{current}; {addition}"
    return addition


__all__ = ["frame_hash_fingerprint", "process_model_analysis_run"]
