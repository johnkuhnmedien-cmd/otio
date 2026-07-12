"""Phase 10.5: Production EditPlan Promote Readiness / Dry Run.

Prüft AUSSCHLIESSLICH rein lesend, was ein SPÄTERER Promote eines validierten
Staging-Pakets nach `_otio/edit_plan/{folder}.json` tun würde — kein
tatsächliches Kopieren, kein Lock, kein OTIO-Export, kein Render, kein Aufruf
der Save- oder Build-Funktionen der bestehenden Produktions-EditPlan-
Pipeline. Schreibt ausschließlich unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`
(`production_edit_plan_promote_readiness.json` und
`production_edit_plan_promote_dry_run_trace.json`). Verändert niemals eine
existierende `_otio/edit_plan/{folder}.json`, `voice_folder_mapping.json`,
Inventory-Dateien, Originalmedien oder Audio-Dateien.

Promote ist (in einer künftigen Phase) nur zulässig, wenn:
- production_edit_plan_package.json existiert und nicht stale ist
- production_edit_plan_validation_report.json existiert, Status PASS ist und
  weder Warnings noch Blocker enthält, und nicht stale ist
- alle staged_edit_plans/{id}/edit_plan.json existieren und ihr Hash zum
  Package passt
- production_edit_plan_mapping_trace.json existiert
- keine Section technische Blocker hat (fehlendes Voiceover/TimelineItems/
  Shots, voiceover_audio-Leak, Secret-Leak, Mapping-Sanity-Blocker aus dem
  Package selbst)

Jede Section (inkl. Intro als Ordner „Intro“) wird zusätzlich rein lesend
gegen einen eventuell bereits existierenden Produktionsplan unter
`_otio/edit_plan/{folder}.json` geprüft (WOULD_CREATE/WOULD_OVERWRITE).
Intro wird wie ein normaler Folder nach `_otio/edit_plan/Intro.json`
promotet, damit es in Merge/OTIO-Export eingeht."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED,
    PRODUCTION_EDIT_PLAN_ERROR_SECTION_HAS_BLOCKERS,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_folder_edit_plan_path,
    get_production_edit_plan_promote_dry_run_trace_path,
    get_production_edit_plan_promote_readiness_path,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import _scan_for_leaked_secrets
from otio_app.services.voiceover_generation.llm_trace_service import content_hash, content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanSection
from otio_app.services.voiceover_generation.production_edit_plan_promote_models import (
    ProductionEditPlanPromoteDryRunTraceDocument,
    ProductionEditPlanPromoteDryRunTraceEntry,
    ProductionEditPlanPromoteReadinessDocument,
    ProductionEditPlanPromoteSectionReadiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import load_production_edit_plan_mapping_trace
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    is_production_edit_plan_validation_report_stale,
    load_production_edit_plan_validation_report,
)

__all__ = [
    "build_production_edit_plan_promote_readiness",
    "save_production_edit_plan_promote_readiness",
    "load_production_edit_plan_promote_readiness",
    "build_production_edit_plan_promote_dry_run_trace",
    "save_production_edit_plan_promote_dry_run_trace",
    "load_production_edit_plan_promote_dry_run_trace",
    "is_production_edit_plan_promote_readiness_stale",
]

def _read_existing_production_plan(
    target_path: Path,
) -> tuple[str, bool | None, str, int | None, int | None, list[str]]:
    """Rein lesende Kollisionsprüfung EINES existierenden Produktionsplans.
    Gibt (existing_file_hash, existing_confirmed, existing_candidate_status,
    existing_shot_count, existing_timeline_item_count, warnings) zurück —
    Warnings statt Crash, wenn die Datei nicht lesbar/kein gültiges
    EditPlanDocument ist. Liest ausschließlich, schreibt/verändert nichts."""
    warnings: list[str] = []
    try:
        raw_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE}: "
            f"{target_path} konnte nicht gelesen werden ({exc})."
        )
        return "", None, "", None, None, warnings

    existing_file_hash = content_hash(raw_text)
    try:
        payload = json.loads(raw_text)
        existing_doc = EditPlanDocument.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        warnings.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE}: "
            f"{target_path} ist kein gültiges EditPlanDocument ({exc})."
        )
        return existing_file_hash, None, "", None, None, warnings

    return (
        existing_file_hash,
        existing_doc.confirmed,
        existing_doc.candidate_status,
        len(existing_doc.shots),
        len(existing_doc.timeline_items),
        warnings,
    )


def _build_section_readiness(
    project: Project, section: ProductionEditPlanSection
) -> ProductionEditPlanPromoteSectionReadiness:
    section_warnings: list[str] = []
    section_blockers: list[str] = []

    document = load_staged_edit_plan(project, section.staging_section_id)
    if document is None:
        section_blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING}: "
            f"staged_edit_plans/{section.staging_section_id}/edit_plan.json existiert nicht."
        )
        return ProductionEditPlanPromoteSectionReadiness(
            staging_section_id=section.staging_section_id,
            production_section_id=section.production_section_id,
            folder_name=section.folder_name,
            is_intro=section.is_intro,
            staged_edit_plan_path=section.staged_edit_plan_path,
            promote_action=PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED,
            staged_edit_plan_hash=section.staged_edit_plan_hash,
            blockers=section_blockers,
        )

    current_hash = content_hash_of_model(document)
    if section.staged_edit_plan_hash and current_hash != section.staged_edit_plan_hash:
        section_blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH}: "
            "staged_edit_plan_hash im Package passt nicht zur aktuellen edit_plan.json."
        )
    if section.blockers:
        section_blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_SECTION_HAS_BLOCKERS}: "
            f"Package meldet bereits Section-Blocker: {', '.join(section.blockers)}."
        )
    if document.voiceover is None:
        section_blockers.append(f"{PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING}: voiceover fehlt.")
    if not document.timeline_items:
        section_blockers.append(f"{PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE}: timeline_items ist leer.")
    if not document.shots:
        section_blockers.append(f"{PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS}: shots ist leer.")
    if any(item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO for item in document.timeline_items):
        section_blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED}: "
            "'voiceover_audio'-TimelineItem im gestagten EditPlanDocument."
        )
    for leak in _scan_for_leaked_secrets(document):
        section_blockers.append(f"{PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED}: {leak}")

    target_path = get_folder_edit_plan_path(project.work_dir_path, section.folder_name)
    target_exists = target_path.is_file()
    existing_file_hash = ""
    existing_confirmed: bool | None = None
    existing_candidate_status = ""
    existing_shot_count: int | None = None
    existing_timeline_item_count: int | None = None
    if target_exists:
        (
            existing_file_hash,
            existing_confirmed,
            existing_candidate_status,
            existing_shot_count,
            existing_timeline_item_count,
            read_warnings,
        ) = _read_existing_production_plan(target_path)
        section_warnings.extend(read_warnings)

    if section_blockers:
        promote_action = PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    elif target_exists:
        promote_action = PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
    else:
        promote_action = PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE

    return ProductionEditPlanPromoteSectionReadiness(
        staging_section_id=section.staging_section_id,
        production_section_id=section.production_section_id,
        folder_name=section.folder_name,
        is_intro=section.is_intro,
        staged_edit_plan_path=section.staged_edit_plan_path,
        target_edit_plan_path=str(target_path),
        promote_action=promote_action,
        target_exists=target_exists,
        existing_file_hash=existing_file_hash,
        existing_confirmed=existing_confirmed,
        existing_candidate_status=existing_candidate_status,
        existing_shot_count=existing_shot_count,
        existing_timeline_item_count=existing_timeline_item_count,
        staged_edit_plan_hash=current_hash,
        warnings=section_warnings,
        blockers=section_blockers,
    )


def build_production_edit_plan_promote_readiness(project: Project) -> ProductionEditPlanPromoteReadinessDocument:
    """Baut den vollständigen Promote-Readiness-Dry-Run — rein lesend, kein
    Schreiben nach `_otio/edit_plan/`, kein Promote, kein Lock, kein
    OTIO-Export. Speichert nichts (siehe
    save_production_edit_plan_promote_readiness)."""
    warnings: list[str] = []
    blockers: list[str] = []

    package = load_production_edit_plan_staging_package(project)
    if package is None:
        blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING}: "
            "production_edit_plan_package.json existiert nicht."
        )
        return ProductionEditPlanPromoteReadinessDocument(
            project_id=project.id,
            status=PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED,
            blockers=blockers,
        )

    report = load_production_edit_plan_validation_report(project)
    if report is None:
        blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_MISSING}: "
            "production_edit_plan_validation_report.json existiert nicht."
        )
    else:
        if report.status != PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS:
            blockers.append(
                f"{PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS}: "
                f"Validation Report Status ist {report.status!r}, erwartet PASS."
            )
        if report.warnings or report.blockers:
            blockers.append(
                f"{PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS}: "
                f"Validation Report hat {len(report.warnings)} Warning(s) und "
                f"{len(report.blockers)} Blocker — für Promote wird ein vollständig sauberer Report benötigt."
            )
        if is_production_edit_plan_validation_report_stale(project, report):
            blockers.append(
                f"{PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_STALE}: "
                "Der Validation Report ist veraltet."
            )

    if is_production_edit_plan_staging_stale(project, package):
        blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE}: Das Staging-Paket ist veraltet."
        )

    trace = load_production_edit_plan_mapping_trace(project)
    if trace is None:
        blockers.append(
            f"{PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING}: "
            "production_edit_plan_mapping_trace.json existiert nicht."
        )

    sections = [_build_section_readiness(project, section) for section in package.sections]

    for section_readiness in sections:
        warnings.extend(f"{section_readiness.staging_section_id}: {w}" for w in section_readiness.warnings)
        blockers.extend(f"{section_readiness.staging_section_id}: {b}" for b in section_readiness.blockers)

    any_section_blocked = any(
        section_readiness.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
        for section_readiness in sections
    )
    any_would_overwrite = any(
        section_readiness.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
        for section_readiness in sections
    )

    if blockers or any_section_blocked:
        status = PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    elif any_would_overwrite or warnings:
        status = PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW
    else:
        status = PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY

    return ProductionEditPlanPromoteReadinessDocument(
        project_id=project.id,
        source_package_hash=content_hash_of_model(package),
        source_validation_report_hash=content_hash_of_model(report) if report is not None else "",
        status=status,
        sections=sections,
        warnings=warnings,
        blockers=blockers,
    )


def save_production_edit_plan_promote_readiness(
    project: Project, document: ProductionEditPlanPromoteReadinessDocument
) -> ProductionEditPlanPromoteReadinessDocument:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_promote_readiness_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_production_edit_plan_promote_readiness(project: Project) -> ProductionEditPlanPromoteReadinessDocument | None:
    path = get_production_edit_plan_promote_readiness_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanPromoteReadinessDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def _reason_for_promote_action(promote_action: str, blockers: list[str]) -> str:
    if promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE:
        return "Zielpfad existiert nicht — ein späterer Promote würde eine neue Produktions-EditPlan-Datei anlegen."
    if promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE:
        return (
            "Zielpfad existiert bereits — ein späterer Promote würde die bestehende Produktions-EditPlan-Datei "
            "überschreiben und müsste dafür ausdrücklich bestätigt werden."
        )
    return "Section kann aufgrund technischer Blocker nicht promotet werden: " + "; ".join(blockers)


def build_production_edit_plan_promote_dry_run_trace(
    project: Project, readiness: ProductionEditPlanPromoteReadinessDocument
) -> ProductionEditPlanPromoteDryRunTraceDocument:
    """Reine Funktion — leitet den Dry-Run-Trace 1:1 aus der bereits gebauten
    ProductionEditPlanPromoteReadinessDocument ab, keine eigene
    Neuberechnung/kein erneutes Lesen. Speichert nichts (siehe
    save_production_edit_plan_promote_dry_run_trace)."""
    entries: list[ProductionEditPlanPromoteDryRunTraceEntry] = []
    for section_readiness in readiness.sections:
        would_write = section_readiness.promote_action in {
            PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE,
            PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE,
        }
        would_overwrite = section_readiness.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
        entries.append(
            ProductionEditPlanPromoteDryRunTraceEntry(
                trace_id=f"promote_dry_run_{section_readiness.staging_section_id}",
                staging_section_id=section_readiness.staging_section_id,
                production_section_id=section_readiness.production_section_id,
                folder_name=section_readiness.folder_name,
                is_intro=section_readiness.is_intro,
                staged_edit_plan_path=section_readiness.staged_edit_plan_path,
                target_edit_plan_path=section_readiness.target_edit_plan_path,
                promote_action=section_readiness.promote_action,
                reason=_reason_for_promote_action(section_readiness.promote_action, section_readiness.blockers),
                existing_file_hash=section_readiness.existing_file_hash,
                staged_edit_plan_hash=section_readiness.staged_edit_plan_hash,
                would_write=would_write,
                would_overwrite=would_overwrite,
                warnings=list(section_readiness.warnings),
                blockers=list(section_readiness.blockers),
            )
        )

    return ProductionEditPlanPromoteDryRunTraceDocument(
        project_id=project.id,
        source_package_hash=readiness.source_package_hash,
        entries=entries,
    )


def save_production_edit_plan_promote_dry_run_trace(
    project: Project, trace: ProductionEditPlanPromoteDryRunTraceDocument
) -> ProductionEditPlanPromoteDryRunTraceDocument:
    normalized = trace.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_promote_dry_run_trace_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_production_edit_plan_promote_dry_run_trace(
    project: Project,
) -> ProductionEditPlanPromoteDryRunTraceDocument | None:
    path = get_production_edit_plan_promote_dry_run_trace_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanPromoteDryRunTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def is_production_edit_plan_promote_readiness_stale(
    project: Project, document: ProductionEditPlanPromoteReadinessDocument
) -> bool:
    """True, wenn sich das Staging-Paket oder der Validation Report seit
    diesem Dry-Run-Lauf geändert haben (oder fehlen). Reine Lesefunktion."""
    package = load_production_edit_plan_staging_package(project)
    if package is None:
        return True
    if content_hash_of_model(package) != document.source_package_hash:
        return True
    if is_production_edit_plan_staging_stale(project, package):
        return True

    report = load_production_edit_plan_validation_report(project)
    if report is None:
        return True
    if content_hash_of_model(report) != document.source_validation_report_hash:
        return True
    if is_production_edit_plan_validation_report_stale(project, report):
        return True

    return False
