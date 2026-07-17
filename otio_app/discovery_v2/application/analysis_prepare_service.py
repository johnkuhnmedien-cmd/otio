"""Application service for Discovery-V2 analysis-prepare runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    get_analysis_job_launcher,
)
from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    AnalysisEligibilityView,
    get_analysis_eligibility_view,
)
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    reconcile_orphaned_analysis_run,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_PREPARE_PROFILE_VERSION,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisEligibility,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunStatus,
    RepresentativeFrameRecord,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    find_active_analysis_run,
    find_analysis_identity,
    get_analysis_run,
    get_latest_analysis_run,
    insert_analysis_run,
    insert_analysis_run_asset,
    list_analysis_run_assets,
    list_representative_frames_for_project,
    list_technical_shots_for_project,
    new_analysis_run_id,
    open_analysis_registry,
)
from otio_app.discovery_v2.persistence.editorial_repository import (
    find_active_editorial_run,
)
from otio_app.discovery_v2.persistence.supplementation_repository import (
    find_active_supplementation_run,
)
from otio_app.discovery_v2.persistence.narration_repository import (
    find_active_narration_run,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.models import Project


class AnalysisPrepareServiceError(InventoryServiceError):
    """Domain error for analysis-prepare service operations."""


@dataclass(frozen=True)
class AnalysisPrepareStartResult:
    started: bool
    message: str
    run: AnalysisRun | None = None


@dataclass(frozen=True)
class AnalysisPrepareEligibilityItemView:
    eligibility: AnalysisEligibility
    prepare_status: AnalysisPrepareAssetStatus | None = None
    shot_count: int = 0
    frame_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    analysis_identity_id: str | None = None

    @property
    def error(self) -> str | None:
        if self.error_code and self.error_message:
            return f"{self.error_code}: {self.error_message}"
        return self.error_code or self.error_message


@dataclass(frozen=True)
class AnalysisPrepareStatusView:
    ok: bool
    message: str | None
    chain_error_code: str | None = None
    plan_id: str | None = None
    items: list[AnalysisPrepareEligibilityItemView] = field(default_factory=list)
    active_run: AnalysisRun | None = None
    latest_run: AnalysisRun | None = None
    chain_ok: bool = False
    can_start: bool = False


@dataclass(frozen=True)
class AnalysisPrepareArtifactReviewView:
    ok: bool
    message: str | None = None
    shots: list[TechnicalShotRecord] = field(default_factory=list)
    frames: list[RepresentativeFrameRecord] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_analysis_prepare(
    project: Project,
    *,
    sync: bool = False,
) -> AnalysisPrepareStartResult:
    """Create and explicitly launch an analysis-prepare run."""
    project = require_discovery_project(project)
    reconcile_orphaned_analysis_run(project)
    eligibility = get_analysis_eligibility_view(project)
    if not eligibility.ok:
        return AnalysisPrepareStartResult(
            started=False,
            message=eligibility.message
            or "Analysis-Prepare kann nicht gestartet werden.",
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise AnalysisPrepareServiceError(str(exc)) from exc

    try:
        active = find_active_analysis_run(conn, project_id=project.id)
        if active is not None:
            return AnalysisPrepareStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Analysis-Run "
                    f"({active.scope}/{active.status.value})."
                ),
                run=active,
            )
        active_editorial = find_active_editorial_run(conn, project_id=project.id)
        if active_editorial is not None:
            return AnalysisPrepareStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Editorial-Run "
                    f"({active_editorial.scope}/{active_editorial.status.value})."
                ),
            )
        active_supplementation = find_active_supplementation_run(conn, project_id=project.id)
        if active_supplementation is not None:
            return AnalysisPrepareStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Supplementation-Run "
                    f"({active_supplementation.scope}/{active_supplementation.status.value})."
                ),
            )
        active_narration = find_active_narration_run(conn, project_id=project.id)
        if active_narration is not None:
            return AnalysisPrepareStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Narration-Run "
                    f"({active_narration.scope}/{active_narration.status.value})."
                ),
            )

        run_assets = _build_run_assets_from_eligibility(
            conn,
            project_id=project.id,
            run_id="__pending__",
            eligibility=eligibility,
        )
        if not run_assets:
            return AnalysisPrepareStartResult(
                started=False,
                message="Keine analysefähigen oder not_applicable Assets vorhanden.",
            )

        run = AnalysisRun(
            run_id=new_analysis_run_id(),
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
            analysis_profile_version=ANALYSIS_PREPARE_PROFILE_VERSION,
            status=AnalysisRunStatus.QUEUED,
            created_at=_now(),
            total_assets=len(run_assets),
        )
        insert_analysis_run(conn, run)
        for asset in run_assets:
            insert_analysis_run_asset(
                conn,
                asset.model_copy(update={"run_id": run.run_id}),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        if isinstance(exc, AnalysisPrepareServiceError):
            raise
        raise AnalysisPrepareServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_analysis_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="analysis_prepare",
        sync=sync,
    )
    if not launched and not sync:
        return AnalysisPrepareStartResult(
            started=False,
            message="Analysis-Prepare-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
        )

    if sync:
        try:
            conn = open_analysis_registry(project.project_root_path)
            final = get_analysis_run(conn, run_id=run.run_id) or run
            conn.close()
        except RegistryDatabaseError:
            final = run
        return AnalysisPrepareStartResult(
            started=True,
            message="Analysis-Prepare abgeschlossen.",
            run=final,
        )

    return AnalysisPrepareStartResult(
        started=True,
        message="Analysis-Prepare gestartet.",
        run=run,
    )


def get_analysis_prepare_view(project: Project) -> AnalysisPrepareStatusView:
    """Return analysis-prepare UI state using persisted SQLite rows only."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return AnalysisPrepareStatusView(
            ok=False,
            message=str(exc),
            chain_error_code="wrong_project_mode",
        )

    reconcile_orphaned_analysis_run(project)
    eligibility = get_analysis_eligibility_view(project)
    if not eligibility.ok:
        return AnalysisPrepareStatusView(
            ok=False,
            message=eligibility.message,
            chain_error_code=eligibility.chain_error_code,
            plan_id=eligibility.plan_id,
            chain_ok=False,
            can_start=False,
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return AnalysisPrepareStatusView(
            ok=False,
            message=str(exc),
            chain_error_code="registry_unavailable",
            plan_id=eligibility.plan_id,
            chain_ok=False,
            can_start=False,
        )

    try:
        active = find_active_analysis_run(conn, project_id=project.id)
        latest = get_latest_analysis_run(
            conn,
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
        )
        status_assets = _status_assets_for_view(conn, active=active, latest=latest)
        items = [
            _enrich_eligibility_item(
                conn,
                project_id=project.id,
                eligibility=item,
                status_assets=status_assets,
            )
            for item in eligibility.items
        ]
    finally:
        conn.close()

    has_eligible = any(item.eligible for item in eligibility.items)
    launcher_active = get_analysis_job_launcher().is_active(project.id)
    can_start = (
        eligibility.ok
        and active is None
        and not launcher_active
        and has_eligible
    )
    return AnalysisPrepareStatusView(
        ok=True,
        message=eligibility.message,
        chain_error_code=eligibility.chain_error_code,
        plan_id=eligibility.plan_id,
        items=items,
        active_run=active,
        latest_run=latest,
        chain_ok=True,
        can_start=can_start,
    )


def count_prepare_artifacts_for_identity(
    conn,
    *,
    analysis_identity_id: str,
) -> tuple[int, int]:
    """Count technical shots and representative frames from SQLite only."""
    shot_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM technical_shots WHERE analysis_identity_id = ?",
            (analysis_identity_id,),
        ).fetchone()[0]
        or 0
    )
    frame_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM representative_frames WHERE analysis_identity_id = ?",
            (analysis_identity_id,),
        ).fetchone()[0]
        or 0
    )
    return shot_count, frame_count


def get_analysis_prepare_artifact_review(
    project: Project,
) -> AnalysisPrepareArtifactReviewView:
    """Return persisted shot/frame rows for UI review without media I/O."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return AnalysisPrepareArtifactReviewView(ok=False, message=str(exc))
    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return AnalysisPrepareArtifactReviewView(ok=False, message=str(exc))
    try:
        shots = list_technical_shots_for_project(conn, project_id=project.id)
        frames = list_representative_frames_for_project(conn, project_id=project.id)
    finally:
        conn.close()
    return AnalysisPrepareArtifactReviewView(ok=True, shots=shots, frames=frames)


def _build_run_assets_from_eligibility(
    conn,
    *,
    project_id: str,
    run_id: str,
    eligibility: AnalysisEligibilityView,
) -> list[AnalysisRunAsset]:
    assets: list[AnalysisRunAsset] = []
    for item in eligibility.items:
        if not _is_launchable_eligibility_item(item):
            continue
        if not item.working_media_id or not item.output_sha256 or not item.validation_id:
            continue
        working = _get_working_media_raw(
            conn,
            project_id=project_id,
            asset_id=item.asset_id,
            working_media_id=item.working_media_id,
        )
        if working is None:
            continue
        kind = (item.media_kind or "").strip().lower()
        status = (
            AnalysisPrepareAssetStatus.NOT_APPLICABLE
            if kind == MediaKind.AUDIO.value
            else AnalysisPrepareAssetStatus.PENDING
        )
        assets.append(
            AnalysisRunAsset(
                run_id=run_id,
                asset_id=item.asset_id,
                working_media_id=item.working_media_id,
                validation_id=item.validation_id,
                source_sha256=str(working["source_sha256"]).lower(),
                output_sha256=str(item.output_sha256).lower(),
                processing_profile_version=str(
                    item.actual_processing_profile_version
                    or working["processing_profile_version"]
                ),
                analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
                media_kind=kind,
                status=status,
                error_code=(
                    "not_applicable"
                    if status == AnalysisPrepareAssetStatus.NOT_APPLICABLE
                    else None
                ),
                error_message=(
                    "Audio besitzt keine visuellen Prepare-Artefakte."
                    if status == AnalysisPrepareAssetStatus.NOT_APPLICABLE
                    else None
                ),
                created_at=_now(),
                completed_at=(
                    _now()
                    if status == AnalysisPrepareAssetStatus.NOT_APPLICABLE
                    else None
                ),
            )
        )
    return assets


def _is_launchable_eligibility_item(item: AnalysisEligibility) -> bool:
    kind = (item.media_kind or "").strip().lower()
    if item.eligible and kind in {MediaKind.VIDEO.value, MediaKind.IMAGE.value}:
        return bool(item.working_media_id and item.output_sha256)
    if (
        kind == MediaKind.AUDIO.value
        and item.reason_code == "not_applicable"
        and item.working_media_id
        and item.output_sha256
    ):
        return True
    return False


def _status_assets_for_view(
    conn,
    *,
    active: AnalysisRun | None,
    latest: AnalysisRun | None,
) -> dict[tuple[str, str], AnalysisRunAsset]:
    run = active or latest
    if run is None:
        return {}
    return {
        (asset.asset_id, asset.working_media_id): asset
        for asset in list_analysis_run_assets(conn, run_id=run.run_id)
    }


def _enrich_eligibility_item(
    conn,
    *,
    project_id: str,
    eligibility: AnalysisEligibility,
    status_assets: dict[tuple[str, str], AnalysisRunAsset],
) -> AnalysisPrepareEligibilityItemView:
    identity_id = None
    shot_count = 0
    frame_count = 0
    if (
        eligibility.working_media_id
        and eligibility.output_sha256
        and eligibility.actual_processing_profile_version
    ):
        identity = find_analysis_identity(
            conn,
            project_id=project_id,
            asset_id=eligibility.asset_id,
            working_media_id=eligibility.working_media_id,
            output_sha256=eligibility.output_sha256,
            processing_profile_version=eligibility.actual_processing_profile_version,
            analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        )
        if identity is not None:
            identity_id = identity.analysis_identity_id
            shot_count, frame_count = count_prepare_artifacts_for_identity(
                conn,
                analysis_identity_id=identity.analysis_identity_id,
            )

    status_asset = None
    if eligibility.working_media_id:
        status_asset = status_assets.get(
            (eligibility.asset_id, eligibility.working_media_id)
        )
    prepare_status = status_asset.status if status_asset is not None else None
    error_code = status_asset.error_code if status_asset is not None else None
    error_message = status_asset.error_message if status_asset is not None else None
    if prepare_status is None and frame_count > 0:
        prepare_status = AnalysisPrepareAssetStatus.PREPARED
    if (
        prepare_status is None
        and (eligibility.media_kind or "").strip().lower() == MediaKind.AUDIO.value
        and eligibility.reason_code == "not_applicable"
    ):
        prepare_status = AnalysisPrepareAssetStatus.NOT_APPLICABLE
    return AnalysisPrepareEligibilityItemView(
        eligibility=eligibility,
        prepare_status=prepare_status,
        shot_count=shot_count,
        frame_count=frame_count,
        error_code=error_code,
        error_message=error_message,
        analysis_identity_id=identity_id,
    )


def _get_working_media_raw(
    conn,
    *,
    project_id: str,
    asset_id: str,
    working_media_id: str,
) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT *
        FROM working_media
        WHERE project_id = ?
          AND asset_id = ?
          AND working_media_id = ?
        """,
        (project_id, asset_id, working_media_id),
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


__all__ = [
    "AnalysisPrepareEligibilityItemView",
    "AnalysisPrepareArtifactReviewView",
    "AnalysisPrepareServiceError",
    "AnalysisPrepareStartResult",
    "AnalysisPrepareStatusView",
    "count_prepare_artifacts_for_identity",
    "get_analysis_prepare_artifact_review",
    "get_analysis_prepare_view",
    "start_analysis_prepare",
]
