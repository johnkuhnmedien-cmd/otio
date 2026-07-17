"""Application service for Discovery-V2 fake model analysis runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    get_analysis_job_launcher,
)
from otio_app.discovery_v2.adapters.vision_config import load_vision_config
from otio_app.discovery_v2.application.analysis_prepare_job_recovery import (
    reconcile_orphaned_analysis_run,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_MODEL_PROFILE,
    ANALYSIS_RUN_SCOPE_MODEL,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunStatus,
    RepresentativeFrameRecord,
)
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED,
    ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
    AnalysisConsentEventRecord,
    AnalysisModelAssetStatus,
    VisualObservationRecord,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    find_active_analysis_run,
    get_latest_analysis_run,
    insert_analysis_consent_event,
    insert_analysis_run,
    insert_analysis_run_asset,
    list_analysis_run_assets,
    list_analysis_runs,
    list_representative_frames,
    list_visual_observations_for_project,
    new_analysis_consent_id,
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


class ModelAnalysisServiceError(InventoryServiceError):
    """Domain error for fake model analysis service operations."""


@dataclass(frozen=True)
class PreparedModelAnalysisAssetView:
    asset_id: str
    analysis_identity_id: str
    working_media_id: str
    media_kind: str
    frame_count: int
    total_bytes: int
    display_name: str | None = None


@dataclass(frozen=True)
class ModelAnalysisSelectionPreview:
    asset_count: int = 0
    frame_count: int = 0
    total_bytes: int = 0
    error_code: str | None = None
    message: str | None = None
    assets: list[PreparedModelAnalysisAssetView] = field(default_factory=list)


@dataclass(frozen=True)
class ModelAnalysisStartResult:
    started: bool
    message: str
    run: AnalysisRun | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ModelAnalysisStatusView:
    ok: bool
    message: str | None
    config_provider: str | None = None
    config_model_label: str | None = None
    active_run: AnalysisRun | None = None
    latest_run: AnalysisRun | None = None
    prepared_assets: list[PreparedModelAnalysisAssetView] = field(default_factory=list)
    observations: list[VisualObservationRecord] = field(default_factory=list)
    can_start: bool = False
    chain_ok: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_model_analysis(
    project: Project,
    *,
    asset_ids: list[str] | None = None,
    consent_acknowledged: bool,
    sync: bool = False,
) -> ModelAnalysisStartResult:
    project = require_discovery_project(project)
    reconcile_orphaned_analysis_run(project)
    config = load_vision_config()
    if not consent_acknowledged:
        return ModelAnalysisStartResult(
            started=False,
            message="Für die Modellanalyse ist eine Zustimmung erforderlich.",
            error_code=ANALYSIS_ERROR_ANALYSIS_CONSENT_REQUIRED,
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise ModelAnalysisServiceError(str(exc)) from exc

    try:
        from otio_app.discovery_v2.persistence.export_repository import (
            find_active_export_run,
        )

        if find_active_export_run(conn, project_id=project.id) is not None:
            return ModelAnalysisStartResult(
                started=False,
                message="Es läuft bereits ein Export-Run.",
                error_code="export_run_already_active",
            )
        active = find_active_analysis_run(conn, project_id=project.id)
        if active is not None:
            return ModelAnalysisStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Analysis-Run "
                    f"({active.scope}/{active.status.value})."
                ),
                run=active,
            )
        active_editorial = find_active_editorial_run(conn, project_id=project.id)
        if active_editorial is not None:
            return ModelAnalysisStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Editorial-Run "
                    f"({active_editorial.scope}/{active_editorial.status.value})."
                ),
                error_code=EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
            )
        active_supplementation = find_active_supplementation_run(conn, project_id=project.id)
        if active_supplementation is not None:
            return ModelAnalysisStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Supplementation-Run "
                    f"({active_supplementation.scope}/{active_supplementation.status.value})."
                ),
                error_code="supplementation_run_already_active",
            )
        active_narration = find_active_narration_run(conn, project_id=project.id)
        if active_narration is not None:
            return ModelAnalysisStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Narration-Run "
                    f"({active_narration.scope}/{active_narration.status.value})."
                ),
                error_code="narration_run_already_active",
            )
        from otio_app.discovery_v2.persistence.visual_edit_repository import (
            find_active_visual_edit_run,
        )

        active_visual_edit = find_active_visual_edit_run(conn, project_id=project.id)
        if active_visual_edit is not None:
            return ModelAnalysisStartResult(
                started=False,
                message=(
                    f"Es läuft bereits ein Visual-Edit-Run "
                    f"({active_visual_edit.scope}/{active_visual_edit.status.value})."
                ),
                error_code="visual_edit_run_already_active",
            )

        selected = _selected_prepared_assets(
            conn,
            project_id=project.id,
            asset_ids=asset_ids,
        )
        preview = _preview_from_assets(selected, config=config)
        if preview.error_code:
            return ModelAnalysisStartResult(
                started=False,
                message=preview.message or "Modellanalyse-Auswahl ist ungültig.",
                error_code=preview.error_code,
            )
        if not selected:
            return ModelAnalysisStartResult(
                started=False,
                message="Keine vorbereiteten Analyseframes vorhanden.",
            )

        run = AnalysisRun(
            run_id=new_analysis_run_id(),
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_MODEL,
            analysis_profile_version=ANALYSIS_MODEL_PROFILE,
            status=AnalysisRunStatus.QUEUED,
            created_at=_now(),
            total_assets=len(selected),
        )
        insert_analysis_run(conn, run)
        insert_analysis_consent_event(
            conn,
            AnalysisConsentEventRecord(
                consent_id=new_analysis_consent_id(),
                project_id=project.id,
                run_id=run.run_id,
                created_at=_now(),
                frame_count=preview.frame_count,
                total_bytes=preview.total_bytes,
                acknowledged=True,
                provider=config.provider,
                model_identifier=config.model_identifier,
                gateway_version=config.gateway_version,
                prompt_version=config.prompt_version,
                response_schema_version=config.response_schema_version,
            ),
        )
        for prepared in selected:
            source_asset = prepared.source_asset
            insert_analysis_run_asset(
                conn,
                source_asset.model_copy(
                    update={
                        "run_id": run.run_id,
                        "analysis_profile_version": ANALYSIS_MODEL_PROFILE,
                        "status": AnalysisModelAssetStatus.PENDING,
                        "error_code": None,
                        "error_message": None,
                        "created_at": _now(),
                        "completed_at": None,
                    }
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        if isinstance(exc, ModelAnalysisServiceError):
            raise
        raise ModelAnalysisServiceError(str(exc)) from exc
    else:
        conn.close()

    launcher = get_analysis_job_launcher()
    launched = launcher.launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="model_analysis",
        sync=sync,
    )
    if not launched and not sync:
        return ModelAnalysisStartResult(
            started=False,
            message="Modellanalyse-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
        )
    if sync:
        try:
            conn = open_analysis_registry(project.project_root_path)
            final = get_latest_analysis_run(
                conn,
                project_id=project.id,
                scope=ANALYSIS_RUN_SCOPE_MODEL,
            )
            conn.close()
        except RegistryDatabaseError:
            final = run
        return ModelAnalysisStartResult(
            started=True,
            message="Modellanalyse abgeschlossen.",
            run=final or run,
        )
    return ModelAnalysisStartResult(
        started=True,
        message="Modellanalyse gestartet.",
        run=run,
    )


def get_model_analysis_view(project: Project) -> ModelAnalysisStatusView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return ModelAnalysisStatusView(ok=False, message=str(exc), chain_ok=False)

    reconcile_orphaned_analysis_run(project)
    config = load_vision_config()
    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return ModelAnalysisStatusView(
            ok=False,
            message=str(exc),
            config_provider=config.provider,
            config_model_label=config.model_identifier,
            chain_ok=False,
        )
    try:
        active = find_active_analysis_run(conn, project_id=project.id)
        latest = get_latest_analysis_run(
            conn,
            project_id=project.id,
            scope=ANALYSIS_RUN_SCOPE_MODEL,
        )
        prepared = [
            item.view
            for item in _selected_prepared_assets(
                conn,
                project_id=project.id,
                asset_ids=None,
            )
        ]
        observations = list_visual_observations_for_project(
            conn,
            project_id=project.id,
        )
    finally:
        conn.close()
    launcher_active = get_analysis_job_launcher().is_active(project.id)
    return ModelAnalysisStatusView(
        ok=True,
        message=None,
        config_provider=config.provider,
        config_model_label=config.model_identifier,
        active_run=active,
        latest_run=latest,
        prepared_assets=prepared,
        observations=observations,
        can_start=bool(prepared) and active is None and not launcher_active,
        chain_ok=True,
    )


def preview_model_analysis_selection(
    project: Project,
    asset_ids: list[str] | None,
) -> ModelAnalysisSelectionPreview:
    project = require_discovery_project(project)
    config = load_vision_config()
    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise ModelAnalysisServiceError(str(exc)) from exc
    try:
        selected = _selected_prepared_assets(
            conn,
            project_id=project.id,
            asset_ids=asset_ids,
        )
        return _preview_from_assets(selected, config=config)
    finally:
        conn.close()


@dataclass(frozen=True)
class _PreparedAssetCandidate:
    source_asset: AnalysisRunAsset
    frames: list[RepresentativeFrameRecord]

    @property
    def view(self) -> PreparedModelAnalysisAssetView:
        return PreparedModelAnalysisAssetView(
            asset_id=self.source_asset.asset_id,
            analysis_identity_id=self.source_asset.analysis_identity_id or "",
            working_media_id=self.source_asset.working_media_id,
            media_kind=self.source_asset.media_kind,
            frame_count=len(self.frames),
            total_bytes=sum(frame.file_size_bytes for frame in self.frames),
        )


def _selected_prepared_assets(
    conn,
    *,
    project_id: str,
    asset_ids: list[str] | None,
) -> list[_PreparedAssetCandidate]:
    requested = set(asset_ids or [])
    by_asset: dict[str, _PreparedAssetCandidate] = {}
    for run in list_analysis_runs(conn, project_id=project_id):
        if run.scope != ANALYSIS_RUN_SCOPE_PREPARE_ONLY:
            continue
        for asset in list_analysis_run_assets(conn, run_id=run.run_id):
            if requested and asset.asset_id not in requested:
                continue
            if asset.status != AnalysisPrepareAssetStatus.PREPARED:
                continue
            if not asset.analysis_identity_id:
                continue
            if asset.asset_id in by_asset:
                continue
            frames = list_representative_frames(
                conn,
                analysis_identity_id=asset.analysis_identity_id,
            )
            if not frames:
                continue
            by_asset[asset.asset_id] = _PreparedAssetCandidate(
                source_asset=asset,
                frames=frames,
            )
    ordered = sorted(
        by_asset.values(),
        key=lambda item: (item.source_asset.asset_id, item.source_asset.working_media_id),
    )
    return ordered


def _preview_from_assets(
    assets: list[_PreparedAssetCandidate],
    *,
    config,
) -> ModelAnalysisSelectionPreview:
    frame_count = sum(len(asset.frames) for asset in assets)
    total_bytes = sum(frame.file_size_bytes for asset in assets for frame in asset.frames)
    for asset in assets:
        if (
            asset.source_asset.media_kind == "video"
            and len(asset.frames) > config.max_frames_per_video
        ):
            return ModelAnalysisSelectionPreview(
                asset_count=len(assets),
                frame_count=frame_count,
                total_bytes=total_bytes,
                error_code=ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
                message="Zu viele Frames fuer ein Video-Asset.",
                assets=[asset.view for asset in assets],
            )
    if frame_count > config.max_frames_per_run or total_bytes > config.max_run_bytes:
        return ModelAnalysisSelectionPreview(
            asset_count=len(assets),
            frame_count=frame_count,
            total_bytes=total_bytes,
            error_code=ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
            message="Die Modellanalyse-Auswahl ueberschreitet die Run-Limits.",
            assets=[asset.view for asset in assets],
        )
    if any(frame.file_size_bytes > config.max_frame_bytes for asset in assets for frame in asset.frames):
        return ModelAnalysisSelectionPreview(
            asset_count=len(assets),
            frame_count=frame_count,
            total_bytes=total_bytes,
            error_code=ANALYSIS_ERROR_ANALYSIS_FRAME_LIMIT_EXCEEDED,
            message="Mindestens ein Analyseframe ueberschreitet das Groessenlimit.",
            assets=[asset.view for asset in assets],
        )
    return ModelAnalysisSelectionPreview(
        asset_count=len(assets),
        frame_count=frame_count,
        total_bytes=total_bytes,
        assets=[asset.view for asset in assets],
    )


__all__ = [
    "ModelAnalysisSelectionPreview",
    "ModelAnalysisServiceError",
    "ModelAnalysisStartResult",
    "ModelAnalysisStatusView",
    "PreparedModelAnalysisAssetView",
    "get_model_analysis_view",
    "preview_model_analysis_selection",
    "start_model_analysis",
]
