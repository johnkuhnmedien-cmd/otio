"""Phase 10.8: OTIO Export Readiness Check für bereits promotete/gemappte
Folder.

Wie alle anderen Module unter `otio_app/services/voiceover_generation/`
bleibt dieses Modul STRIKT von der bestehenden "mit Voice-Over"-
Produktionspipeline isoliert — es importiert und ruft KEINE der bestehenden
Produktions-Export- oder -Build-Funktionen auf.

Stattdessen prüft es rein strukturell und vollständig eigenständig, ob die
grundlegenden Voraussetzungen erfüllt sind, die die bestehende Export-
Pipeline für einen Folder verlangt (bestätigte Zuordnung in
`voice_folder_mapping.json`, bestätigtes `EditPlanDocument`, nicht-leere
TimelineItems/Shots, vorhandener VoiceoverPlan) — für die zuletzt promoteten
(Phase 10.6) Folder. Dies ist eine bewusst VEREINFACHTE, konservative
Annäherung ("wären die offensichtlichen Voraussetzungen erfüllt?"), KEINE
vollständige Nachbildung der eigentlichen Timeline-Zusammenführung — für
eine verbindliche Aussage bleibt die bestehende Vorschau im Tab
„③ Schnittplan → 📤 OTIO Export“ die maßgebliche Quelle.

Kein Export, keine .otio-Datei, kein ffprobe, kein Render, kein Lock, keine
LLM-Planung, keine automatische Neuplanung."""

from __future__ import annotations

import json

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_edit_plan_path, get_otio_export_readiness_report_path
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness_models import (
    OtioExportReadinessFolderResult,
    OtioExportReadinessReport,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
    load_production_edit_plan_promote_manifest,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge import (
    load_voice_folder_mapping_merge_manifest,
)

__all__ = [
    "build_otio_export_readiness_report",
    "save_otio_export_readiness_report",
    "load_otio_export_readiness_report",
]


def _promoted_folder_names(promote_manifest) -> list[str]:
    return [
        section.folder_name
        for section in promote_manifest.sections
        if section.promote_action
        in {
            PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED,
            PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN,
        }
    ]


def _read_edit_plan_metadata(edit_plan_path) -> tuple[bool, bool, bool, int, int, list[str]]:
    """Rein lesend — gibt (exists, confirmed, has_voiceover, shot_count,
    timeline_item_count, warnings) zurück. Liest die Datei direkt, ohne den
    Cache oder Loader der bestehenden Produktionspipeline zu verwenden.
    Warnung statt Crash bei ungültigem Inhalt."""
    if not edit_plan_path.is_file():
        return False, False, False, 0, 0, []
    try:
        payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
        document = EditPlanDocument.model_validate(payload)
        return (
            True,
            document.confirmed,
            document.voiceover is not None,
            len(document.shots),
            len(document.timeline_items),
            [],
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return True, False, False, 0, 0, [f"EditPlanDocument nicht lesbar/ungültig: {exc}"]


def build_otio_export_readiness_report(project: Project) -> OtioExportReadinessReport:
    """Rein lesende, vollständig isolierte Diagnose — kein Export, keine
    .otio-Datei, kein ffprobe, kein Aufruf der Produktions-Export-Pipeline.
    Prüft ausschließlich Folder, die im letzten Promote-Manifest (Phase
    10.6) als CREATED/OVERWRITTEN dokumentiert sind."""
    promote_manifest = load_production_edit_plan_promote_manifest(project)
    if promote_manifest is None:
        return OtioExportReadinessReport(
            project_id=project.id,
            status=PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED,
            warnings=["Kein Promote Manifest vorhanden (production_edit_plan_promote_manifest.json fehlt)."],
        )

    promoted_folder_names = _promoted_folder_names(promote_manifest)
    if not promoted_folder_names:
        return OtioExportReadinessReport(
            project_id=project.id,
            source_promote_manifest_hash=content_hash_of_model(promote_manifest),
            status=PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED,
            warnings=[
                "Das letzte Promote Manifest enthält keine tatsächlich promoteten (CREATED/OVERWRITTEN) Folder."
            ],
        )

    merge_manifest = load_voice_folder_mapping_merge_manifest(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    mapping_confirmed = mapping is not None and mapping.confirmed
    mapping_confirmed_folders: set[str] = set()
    if mapping_confirmed:
        mapping_confirmed_folders = {entry.folder for entry in mapping.entries if entry.folder and entry.confirmed}

    report_warnings: list[str] = []
    if not mapping_confirmed:
        report_warnings.append(
            "voice_folder_mapping.json fehlt oder ist nicht bestätigt (Dokument-Level confirmed=false)."
        )

    folder_results: list[OtioExportReadinessFolderResult] = []
    total_shots = 0
    total_timeline_items = 0

    for folder_name in promoted_folder_names:
        edit_plan_path = get_folder_edit_plan_path(project.language_work_dir_path, folder_name)
        exists, confirmed, has_voiceover, shot_count, timeline_item_count, read_warnings = _read_edit_plan_metadata(
            edit_plan_path
        )
        in_confirmed_mapping = folder_name in mapping_confirmed_folders
        total_shots += shot_count
        total_timeline_items += timeline_item_count

        folder_warnings: list[str] = list(read_warnings)
        if not exists:
            folder_warnings.append(f"{edit_plan_path} existiert nicht.")
        if exists and not confirmed:
            folder_warnings.append("EditPlanDocument ist nicht confirmed.")
        if exists and not has_voiceover:
            folder_warnings.append("EditPlanDocument hat keinen VoiceoverPlan.")
        if exists and timeline_item_count == 0:
            folder_warnings.append("EditPlanDocument hat keine TimelineItems.")
        if exists and shot_count == 0:
            folder_warnings.append("EditPlanDocument hat keine Shots.")
        if not in_confirmed_mapping:
            folder_warnings.append(
                "Folder ist nicht Teil einer bestätigten voice_folder_mapping.json (Dokument- und/oder "
                "Eintrags-Level confirmed=false)."
            )

        is_ready = (
            exists
            and confirmed
            and has_voiceover
            and timeline_item_count > 0
            and shot_count > 0
            and in_confirmed_mapping
        )
        status = (
            PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY
            if is_ready
            else PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY
        )

        folder_results.append(
            OtioExportReadinessFolderResult(
                folder_name=folder_name,
                edit_plan_path=str(edit_plan_path),
                edit_plan_exists=exists,
                edit_plan_confirmed=confirmed,
                in_confirmed_mapping=in_confirmed_mapping,
                has_voiceover=has_voiceover,
                shot_count=shot_count,
                timeline_item_count=timeline_item_count,
                status=status,
                warnings=folder_warnings,
            )
        )

    all_folders_ready = all(
        result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY for result in folder_results
    )

    if not mapping_confirmed:
        overall_status = PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED
    elif all_folders_ready:
        overall_status = PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY
    else:
        overall_status = PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY

    return OtioExportReadinessReport(
        project_id=project.id,
        source_promote_manifest_hash=content_hash_of_model(promote_manifest),
        source_merge_manifest_hash=content_hash_of_model(merge_manifest) if merge_manifest is not None else "",
        checked_folders=promoted_folder_names,
        mapping_confirmed=mapping_confirmed,
        total_shots=total_shots,
        total_timeline_items=total_timeline_items,
        status=overall_status,
        folders=folder_results,
        warnings=report_warnings,
    )


def save_otio_export_readiness_report(project: Project, report: OtioExportReadinessReport) -> OtioExportReadinessReport:
    normalized = report.model_copy(update={"project_id": project.id})
    path = get_otio_export_readiness_report_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_otio_export_readiness_report(project: Project) -> OtioExportReadinessReport | None:
    path = get_otio_export_readiness_report_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return OtioExportReadinessReport.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
