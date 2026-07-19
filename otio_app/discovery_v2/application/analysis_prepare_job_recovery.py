"""Recovery contract for Discovery-V2 analysis-prepare jobs."""

from __future__ import annotations

from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    get_analysis_job_launcher,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_RUN_SCOPE_MODEL,
    WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE,
    AnalysisInputIdentity,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunReport,
    AnalysisRunReportAsset,
    AnalysisRunReportCounts,
    AnalysisRunReportError,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.domain.visual_observation import AnalysisModelAssetStatus
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    cleanup_analysis_temp,
    find_active_analysis_run,
    list_analysis_run_assets,
    list_model_analysis_attempts,
    list_representative_frames,
    list_technical_shots,
    open_analysis_registry,
    save_analysis_run_report,
    update_analysis_run,
    update_analysis_run_asset,
    update_model_analysis_attempt,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.models import Project


_UNFINISHED_ASSET_STATUSES = {
    AnalysisPrepareAssetStatus.PENDING,
    AnalysisPrepareAssetStatus.DETECTING_SHOTS,
    AnalysisPrepareAssetStatus.EXTRACTING_FRAMES,
}

_UNFINISHED_MODEL_ASSET_STATUSES = {
    AnalysisModelAssetStatus.PENDING,
    AnalysisModelAssetStatus.ANALYZING,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_orphaned_analysis_run(project: Project) -> AnalysisRun | None:
    """Mark orphaned queued/running analysis runs as failed."""
    launcher = get_analysis_job_launcher()
    if launcher.is_active(project.id):
        return None

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError:
        return None

    try:
        active = find_active_analysis_run(conn, project_id=project.id)
        if active is None:
            return None
        if launcher.is_active(project.id):
            return None

        if active.scope == ANALYSIS_RUN_SCOPE_MODEL:
            return _reconcile_model_run(conn, project, active)

        interrupted_at = _now()
        for asset in list_analysis_run_assets(conn, run_id=active.run_id):
            if asset.status in _UNFINISHED_ASSET_STATUSES:
                update_analysis_run_asset(
                    conn,
                    asset.model_copy(
                        update={
                            "status": AnalysisPrepareAssetStatus.INTERRUPTED,
                            "error_code": WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE,
                            "error_message": (
                                "Analysis-Prepare-Worker ist nicht mehr aktiv "
                                "(Prozessabbruch oder Neustart)."
                            ),
                            "completed_at": interrupted_at,
                        }
                    ),
                )

        assets = list_analysis_run_assets(conn, run_id=active.run_id)
        counts = _counts_from_assets(assets)
        updated = active.model_copy(
            update={
                "status": AnalysisRunStatus.FAILED,
                "completed_at": interrupted_at,
                "prepared_assets": counts[AnalysisPrepareAssetStatus.PREPARED],
                "reused_assets": _reused_count(assets),
                "not_applicable_assets": counts[
                    AnalysisPrepareAssetStatus.NOT_APPLICABLE
                ],
                "failed_assets": counts[AnalysisPrepareAssetStatus.FAILED],
                "interrupted_assets": counts[AnalysisPrepareAssetStatus.INTERRUPTED],
                "error_summary": (
                    f"{WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE}: "
                    "Analysis-Prepare-Worker ist nicht mehr aktiv "
                    "(Prozessabbruch oder Neustart)."
                ),
            }
        )
        update_analysis_run(conn, updated)
        conn.commit()

        cleanup_analysis_temp(project.project_root_path, run_id=updated.run_id)
        try:
            save_analysis_run_report(
                project.project_root_path,
                build_report_from_analysis_run(conn, updated, assets=assets),
            )
        except (InventoryArtifactError, OSError, ValueError):
            pass
        return updated
    finally:
        conn.close()


def build_report_from_analysis_run(
    conn,
    run: AnalysisRun,
    *,
    assets: list[AnalysisRunAsset] | None = None,
) -> AnalysisRunReport:
    """Build an analysis run report from SQLite rows only."""
    run_assets = assets if assets is not None else list_analysis_run_assets(
        conn, run_id=run.run_id
    )
    report_assets: list[AnalysisRunReportAsset] = []
    errors: list[AnalysisRunReportError] = []
    shot_total = 0
    frame_total = 0
    for asset in run_assets:
        shots = []
        frames = []
        if asset.analysis_identity_id:
            shots = list_technical_shots(
                conn, analysis_identity_id=asset.analysis_identity_id
            )
            frames = list_representative_frames(
                conn, analysis_identity_id=asset.analysis_identity_id
            )
        shot_total += len(shots)
        frame_total += len(frames)
        if asset.error_code:
            errors.append(
                AnalysisRunReportError(
                    asset_id=asset.asset_id,
                    error_code=asset.error_code,
                    error_message=asset.error_message,
                )
            )
        report_assets.append(
            AnalysisRunReportAsset(
                analysis_identity_id=asset.analysis_identity_id,
                asset_id=asset.asset_id,
                working_media_id=asset.working_media_id,
                media_kind=asset.media_kind,
                status=asset.status,
                shot_count=len(shots),
                frame_count=len(frames),
                relative_frame_paths=[frame.relative_path for frame in frames],
                error_code=asset.error_code,
                error_message=asset.error_message,
            )
        )

    counts = AnalysisRunReportCounts(
        total_assets=run.total_assets,
        prepared_assets=run.prepared_assets,
        reused_assets=run.reused_assets,
        not_applicable_assets=run.not_applicable_assets,
        failed_assets=run.failed_assets,
        interrupted_assets=run.interrupted_assets,
        shot_count=shot_total,
        frame_count=frame_total,
    )
    return AnalysisRunReport(
        run_id=run.run_id,
        project_id=run.project_id,
        scope=run.scope,
        analysis_profile_version=run.analysis_profile_version,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        input_identities=[
            AnalysisInputIdentity(
                project_id=run.project_id,
                asset_id=asset.asset_id,
                working_media_id=asset.working_media_id,
                validation_id=asset.validation_id,
                source_sha256=asset.source_sha256,
                output_sha256=asset.output_sha256,
                processing_profile_version=asset.processing_profile_version,
                media_kind=asset.media_kind,
                analysis_profile_version=asset.analysis_profile_version,
            )
            for asset in run_assets
        ],
        counts=counts,
        assets=report_assets,
        errors=errors,
        total_assets=counts.total_assets,
        prepared_assets=counts.prepared_assets,
        reused_assets=counts.reused_assets,
        not_applicable_assets=counts.not_applicable_assets,
        failed_assets=counts.failed_assets,
        interrupted_assets=counts.interrupted_assets,
        shot_count=counts.shot_count,
        frame_count=counts.frame_count,
    )


def _reconcile_model_run(conn, project: Project, active: AnalysisRun) -> AnalysisRun:
    interrupted_at = _now()
    for asset in list_analysis_run_assets(conn, run_id=active.run_id):
        if asset.status in _UNFINISHED_MODEL_ASSET_STATUSES:
            update_analysis_run_asset(
                conn,
                asset.model_copy(
                    update={
                        "status": AnalysisModelAssetStatus.INTERRUPTED,
                        "error_code": WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE,
                        "error_message": (
                            "Modellanalyse-Worker ist nicht mehr aktiv "
                            "(Prozessabbruch oder Neustart)."
                        ),
                        "completed_at": interrupted_at,
                    }
                ),
            )

    for attempt in list_model_analysis_attempts(conn, run_id=active.run_id):
        if attempt.status in {"queued", "running"}:
            update_model_analysis_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": "interrupted",
                        "error_code": WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE,
                        "error_message": (
                            "Modellanalyse-Worker ist nicht mehr aktiv "
                            "(Prozessabbruch oder Neustart)."
                        ),
                        "completed_at": interrupted_at,
                    }
                ),
            )

    assets = list_analysis_run_assets(conn, run_id=active.run_id)
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
    updated = active.model_copy(
        update={
            "status": AnalysisRunStatus.FAILED,
            "completed_at": interrupted_at,
            "prepared_assets": completed,
            "reused_assets": reused,
            "not_applicable_assets": not_applicable,
            "failed_assets": failed,
            "interrupted_assets": interrupted,
            "error_summary": (
                f"{WORKER_INTERRUPTED_ANALYSIS_ERROR_CODE}: "
                "Modellanalyse-Worker ist nicht mehr aktiv "
                "(Prozessabbruch oder Neustart)."
            ),
        }
    )
    update_analysis_run(conn, updated)
    conn.commit()
    try:
        save_analysis_run_report(
            project.project_root_path,
            build_report_from_analysis_run(conn, updated, assets=assets),
        )
    except (InventoryArtifactError, OSError, ValueError):
        pass
    return updated


def _counts_from_assets(
    assets: list[AnalysisRunAsset],
) -> dict[AnalysisPrepareAssetStatus, int]:
    return {
        status: sum(1 for asset in assets if asset.status == status)
        for status in AnalysisPrepareAssetStatus
    }


def _reused_count(assets: list[AnalysisRunAsset]) -> int:
    return sum(
        1
        for asset in assets
        if asset.status == AnalysisPrepareAssetStatus.PREPARED
        and asset.error_code == "reused"
    )


__all__ = [
    "build_report_from_analysis_run",
    "reconcile_orphaned_analysis_run",
]
