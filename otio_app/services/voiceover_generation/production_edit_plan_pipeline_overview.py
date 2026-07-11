"""Phase 10.9: Gesamt-Übersichts-Dashboard für den Production-EditPlan-
Workflow.

Rein lesende Aggregation — liest ausschließlich bereits vorhandene
Artefakte aller vorherigen Phasen (10.1-10.8) und berechnet daraus einen
Gesamt-Überblick. Kein neues Artefakt wird geschrieben, kein Seiteneffekt,
kein Aufruf irgendeiner Build-/Save-/Export-Funktion. Bleibt — wie alle
anderen Module dieser Pipeline — vollständig von der bestehenden
Produktionspipeline isoliert."""

from __future__ import annotations

from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_OTIO_EXPORT_READINESS,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE_READINESS,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_STAGING,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VALIDATION,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VOICE_FOLDER_MAPPING_MERGE,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_STATUS_NOT_STARTED,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED as PROMOTE_READINESS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED as PROMOTE_MANIFEST_BLOCKED,
    PRODUCTION_EDIT_PLAN_STATUS_BLOCKED as PACKAGE_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED as VALIDATION_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED as OTIO_READINESS_BLOCKED,
    VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED as MERGE_MANIFEST_BLOCKED,
)
from otio_app.models import Project
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness import (
    load_otio_export_readiness_report,
)
from otio_app.services.voiceover_generation.production_edit_plan_pipeline_overview_models import (
    PipelineStageOverview,
    ProductionEditPlanPipelineOverview,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
    is_production_edit_plan_promote_manifest_stale,
    load_production_edit_plan_promote_manifest,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_readiness import (
    is_production_edit_plan_promote_readiness_stale,
    load_production_edit_plan_promote_readiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    is_production_edit_plan_validation_report_stale,
    load_production_edit_plan_validation_report,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge import (
    is_voice_folder_mapping_merge_manifest_stale,
    load_voice_folder_mapping_merge_manifest,
)

__all__ = ["build_production_edit_plan_pipeline_overview"]

_BLOCKED_STATUSES = frozenset(
    {
        PACKAGE_STATUS_BLOCKED,
        VALIDATION_STATUS_BLOCKED,
        PROMOTE_READINESS_BLOCKED,
        PROMOTE_MANIFEST_BLOCKED,
        MERGE_MANIFEST_BLOCKED,
        OTIO_READINESS_BLOCKED,
    }
)


def _not_started_stage(stage_id: str, label: str) -> PipelineStageOverview:
    return PipelineStageOverview(
        stage_id=stage_id,
        label=label,
        exists=False,
        status=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_STATUS_NOT_STARTED,
        is_stale=False,
        detail="Noch nicht ausgeführt.",
    )


def _staging_stage(project: Project) -> PipelineStageOverview:
    package = load_production_edit_plan_staging_package(project)
    if package is None:
        return _not_started_stage(PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_STAGING, "Staging")
    stale = is_production_edit_plan_staging_stale(project, package)
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_STAGING,
        label="Staging",
        exists=True,
        status=package.status,
        is_stale=stale,
        detail=f"{len(package.sections)} Sektion(en), {len(package.blockers)} Blocker",
    )


def _validation_stage(project: Project) -> PipelineStageOverview:
    report = load_production_edit_plan_validation_report(project)
    if report is None:
        return _not_started_stage(PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VALIDATION, "Validation")
    stale = is_production_edit_plan_validation_report_stale(project, report)
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VALIDATION,
        label="Validation",
        exists=True,
        status=report.status,
        is_stale=stale,
        detail=f"{len(report.warnings)} Warning(s), {len(report.blockers)} Blocker",
    )


def _promote_readiness_stage(project: Project) -> PipelineStageOverview:
    readiness = load_production_edit_plan_promote_readiness(project)
    if readiness is None:
        return _not_started_stage(PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE_READINESS, "Promote Readiness")
    stale = is_production_edit_plan_promote_readiness_stale(project, readiness)
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE_READINESS,
        label="Promote Readiness",
        exists=True,
        status=readiness.status,
        is_stale=stale,
        detail=f"{len(readiness.sections)} Section(en) geprüft",
    )


def _promote_stage(project: Project) -> PipelineStageOverview:
    manifest = load_production_edit_plan_promote_manifest(project)
    if manifest is None:
        return _not_started_stage(PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE, "Promote")
    stale = is_production_edit_plan_promote_manifest_stale(project, manifest)
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE,
        label="Promote",
        exists=True,
        status=manifest.status,
        is_stale=stale,
        detail=(
            f"{manifest.created_count} erstellt, {manifest.overwritten_count} überschrieben, "
            f"{manifest.skipped_intro_count} Intro übersprungen"
        ),
    )


def _voice_folder_mapping_merge_stage(project: Project) -> PipelineStageOverview:
    manifest = load_voice_folder_mapping_merge_manifest(project)
    if manifest is None:
        return _not_started_stage(
            PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VOICE_FOLDER_MAPPING_MERGE, "Voice Folder Mapping Merge"
        )
    stale = is_voice_folder_mapping_merge_manifest_stale(project, manifest)
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VOICE_FOLDER_MAPPING_MERGE,
        label="Voice Folder Mapping Merge",
        exists=True,
        status=manifest.status,
        is_stale=stale,
        detail=f"{manifest.added_count} hinzugefügt, {manifest.updated_count} aktualisiert, {manifest.skipped_count} übersprungen",
    )


def _otio_export_readiness_stage(project: Project) -> PipelineStageOverview:
    report = load_otio_export_readiness_report(project)
    if report is None:
        return _not_started_stage(
            PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_OTIO_EXPORT_READINESS, "OTIO Export Readiness"
        )
    # Dieser Report wird nicht gegen den aktuellen Stand auf Staleness
    # geprüft (Phase 10.8 bietet dafür keine eigene Staleness-Funktion, da
    # der Check jederzeit gefahrlos neu ausgeführt werden kann) — hier wird
    # er defensiv anhand des zuletzt bekannten Promote-Manifest-Hashes mit
    # dem aktuellen verglichen.
    current_manifest = load_production_edit_plan_promote_manifest(project)
    stale = current_manifest is None or content_hash_of_model(current_manifest) != report.source_promote_manifest_hash
    return PipelineStageOverview(
        stage_id=PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_OTIO_EXPORT_READINESS,
        label="OTIO Export Readiness",
        exists=True,
        status=report.status,
        is_stale=stale,
        detail=f"{len(report.checked_folders)} Folder geprüft",
    )


def build_production_edit_plan_pipeline_overview(project: Project) -> ProductionEditPlanPipelineOverview:
    """Baut den Gesamt-Überblick live aus den bestehenden Artefakten —
    reine Funktion, kein Seitendeffekt, wird nicht persistiert."""
    stages = [
        _staging_stage(project),
        _validation_stage(project),
        _promote_readiness_stage(project),
        _promote_stage(project),
        _voice_folder_mapping_merge_stage(project),
        _otio_export_readiness_stage(project),
    ]

    if not stages[0].exists:
        overall_status = PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED
    elif any(stage.status in _BLOCKED_STATUSES for stage in stages if stage.exists):
        overall_status = PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED
    elif (
        stages[-1].exists
        and stages[-1].status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY
        and stages[3].exists
        and stages[3].status == PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED
        and not any(stage.is_stale for stage in stages if stage.exists)
    ):
        overall_status = PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE
    else:
        overall_status = PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS

    return ProductionEditPlanPipelineOverview(
        project_id=project.id,
        overall_status=overall_status,
        stages=stages,
    )
