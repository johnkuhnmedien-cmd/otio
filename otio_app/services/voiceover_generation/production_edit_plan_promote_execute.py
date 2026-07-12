"""Phase 10.6: Actual Production EditPlan Promote — Backup, Manifest,
Kollisionsschutz.

Schreibt ein validiertes, nicht-stale Production-EditPlan-Staging-Paket
kontrolliert nach `_otio/edit_plan/{safe_folder_slug(folder_name)}.json`.
Dies ist die EINZIGE Stelle im gesamten "Projekt ohne Voice-Over"-Workflow,
die nach `_otio/edit_plan/` schreiben darf — ausschließlich über
`promote_production_edit_plans()`.

Nur Nicht-Intro-Folder werden geschrieben. Intro bleibt bewusst SKIPPED,
weil die bestehende Produktionspipeline kein natives Intro-Folder-Konzept
hat (siehe `skipped_reason` in der Intro-Section des Manifests).

Kein OTIO-Export, kein Render, kein Lock-Konzept, keine LLM-Planung, kein
Aufruf der Save- oder Build-Funktionen der bestehenden Produktions-EditPlan-
Pipeline, keine automatische Neuplanung, keine automatische Supplement-
Suche. `voice_folder_mapping.json` wird NICHT verändert — stattdessen wird
ein reiner Vorbereitungs-Patch geschrieben (siehe
`build_voice_folder_mapping_patch`).

Sicherheitsdesign — kein partieller Promote:
1. `can_promote_production_edit_plans` ist die EINZIGE Quelle der Wahrheit
   für alle harten Voraussetzungen (§1) — vertraut dabei bewusst dem bereits
   vollständig geprüften, NICHT-stale Promote-Readiness-Dokument (Phase
   10.5) statt dieselben Prüfungen ein zweites Mal durchzuführen. Staleness
   garantiert, dass sich die gestagten Dateien seit der Readiness-Prüfung
   nicht verändert haben.
2. Fehlt für auch nur EINE WOULD_OVERWRITE-Section die explizite Freigabe
   (`allow_overwrite_section_ids`), wird der GESAMTE Promote blockiert
   (kein partieller Promote).
3. Backups werden für ALLE Overwrite-Sections ZUERST erzeugt (vor jedem
   Schreiben einer Zieldatei) — schlägt auch nur ein Backup fehl, wird
   NICHTS geschrieben."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_SKIPPED_INTRO,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_folder_edit_plan_path,
    get_production_edit_plan_promote_backup_run_dir,
    get_production_edit_plan_promote_manifest_path,
    get_production_edit_plan_voice_folder_mapping_patch_path,
    safe_folder_slug,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import _scan_for_leaked_secrets
from otio_app.services.voiceover_generation.llm_trace_service import content_hash, content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanSection
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute_models import (
    ProductionEditPlanPromoteManifest,
    ProductionEditPlanPromoteSectionResult,
    ProductionEditPlanVoiceFolderMappingPatch,
    ProductionEditPlanVoiceFolderMappingPatchEntry,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_readiness import (
    is_production_edit_plan_promote_readiness_stale,
    load_production_edit_plan_promote_dry_run_trace,
    load_production_edit_plan_promote_readiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    load_production_edit_plan_validation_report,
)

__all__ = [
    "can_promote_production_edit_plans",
    "promote_production_edit_plans",
    "save_production_edit_plan_promote_manifest",
    "load_production_edit_plan_promote_manifest",
    "build_voice_folder_mapping_patch",
    "save_voice_folder_mapping_patch",
    "load_voice_folder_mapping_patch",
    "is_production_edit_plan_promote_manifest_stale",
]

_INTRO_SKIPPED_REASON = "Intro is synthetic and requires a later export/mapping strategy."


def _promote_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def _target_edit_plan_path_for_section(project: Project, section: ProductionEditPlanSection) -> Path:
    return get_folder_edit_plan_path(project.work_dir_path, section.folder_name)


def can_promote_production_edit_plans(
    project: Project, *, allow_overwrite_section_ids: list[str] | None = None
) -> tuple[bool, list[str]]:
    """Einzige Quelle der Wahrheit für alle harten Promote-Voraussetzungen
    (§1). Reine Funktion, kein Seiteneffekt. Vertraut bewusst einem NICHT-
    stale Promote-Readiness-Dokument (Phase 10.5) statt dessen Section-
    Prüfungen (Voiceover/TimelineItems/Shots/Leaks/Hash) ein zweites Mal
    durchzuführen — Staleness-Freiheit garantiert, dass sich die gestagten
    Dateien seit der letzten Dry-Run-Prüfung nicht verändert haben."""
    reasons: list[str] = []

    readiness = load_production_edit_plan_promote_readiness(project)
    if readiness is None:
        reasons.append(
            "Kein Promote Readiness Dry Run vorhanden (production_edit_plan_promote_readiness.json fehlt)."
        )
    dry_run_trace = load_production_edit_plan_promote_dry_run_trace(project)
    if dry_run_trace is None:
        reasons.append(
            "Kein Promote Dry Run Trace vorhanden (production_edit_plan_promote_dry_run_trace.json fehlt)."
        )
    if readiness is None or dry_run_trace is None:
        return False, reasons

    if is_production_edit_plan_promote_readiness_stale(project, readiness):
        reasons.append("Der Promote Dry Run ist veraltet. Bitte erneut ausführen.")

    if readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED:
        reasons.append("Readiness-Status ist BLOCKED.")

    blocked_sections = [
        section for section in readiness.sections if section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    ]
    if blocked_sections:
        reasons.append(
            f"{len(blocked_sections)} Section(en) mit promote_action=BLOCKED: "
            + ", ".join(section.staging_section_id for section in blocked_sections)
        )

    report = load_production_edit_plan_validation_report(project)
    if report is None:
        reasons.append("Kein Validation Report vorhanden (production_edit_plan_validation_report.json fehlt).")
    elif report.status != PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS or report.warnings or report.blockers:
        reasons.append(
            "Validation Report ist nicht PASS ohne Warnings/Blocker "
            f"(Status={report.status}, {len(report.warnings)} Warning(s), {len(report.blockers)} Blocker)."
        )

    package = load_production_edit_plan_staging_package(project)
    if package is None:
        reasons.append("Kein Production EditPlan Staging Package vorhanden.")
    elif is_production_edit_plan_staging_stale(project, package):
        reasons.append("Das Staging-Paket ist veraltet.")

    allow_ids = set(allow_overwrite_section_ids or [])
    would_overwrite_sections = [
        section
        for section in readiness.sections
        if section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
    ]
    missing_permission = [
        section for section in would_overwrite_sections if section.staging_section_id not in allow_ids
    ]
    if missing_permission:
        reasons.append(
            "Folgende Section(en) würden bestehende Produktionspläne überschreiben, sind aber nicht in "
            "allow_overwrite_section_ids freigegeben: "
            + ", ".join(section.staging_section_id for section in missing_permission)
            + ". Kein partieller Promote — bitte entweder alle betroffenen Overwrites freigeben oder den "
            "Promote nicht ausführen."
        )

    return not reasons, reasons


def _atomic_write_text(path: Path, text: str) -> None:
    """Schreibt `text` möglichst atomar: temp file im selben Zielordner,
    flush + fsync, dann os.replace. Bei jedem Fehler wird die temp-Datei
    entfernt und die Exception weitergereicht — die Zieldatei bleibt in
    diesem Fall unverändert."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path_str, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _atomic_write_edit_plan_document(target_path: Path, serialized: str) -> None:
    """Dünner, benannter Wrapper um `_atomic_write_text` speziell für
    EditPlanDocument-Ziele — verwendet bewusst NICHT die Save-Funktion der
    bestehenden Produktions-EditPlan-Pipeline (bleibt für diese isolierte
    Pipeline verboten)."""
    _atomic_write_text(target_path, serialized)


def _backup_existing_edit_plan(
    project: Project, promote_run_id: str, folder_name: str, target_path: Path
) -> tuple[str, str]:
    """Sichert eine EXISTIERENDE Zieldatei VOR dem Überschreiben. Gibt
    (backup_path, backup_hash) zurück. Wirft OSError/ValueError, wenn das
    Backup nicht byte-identisch verifiziert werden kann — der Aufrufer MUSS
    in diesem Fall die Zieldatei unverändert lassen (kein Write)."""
    backup_dir = get_production_edit_plan_promote_backup_run_dir(project.work_dir_path, promote_run_id)
    backup_dir.mkdir(parents=True, exist_ok=True)

    raw_text = target_path.read_text(encoding="utf-8")
    backup_hash = content_hash(raw_text)

    safe_name = safe_folder_slug(folder_name)
    backup_json_path = backup_dir / f"{safe_name}.existing.json"
    backup_hash_path = backup_dir / f"{safe_name}.existing.hash.txt"

    backup_json_path.write_text(raw_text, encoding="utf-8")
    backup_hash_path.write_text(backup_hash, encoding="utf-8")

    # Verifikation: das Backup MUSS byte-identisch zur Originaldatei sein.
    if backup_json_path.read_text(encoding="utf-8") != raw_text:
        raise ValueError(f"Backup-Verifikation fehlgeschlagen für {target_path} — Backup ist nicht byte-identisch.")

    return str(backup_json_path), backup_hash


def _set_promoted_document_confirmed_true(
    project: Project, document: EditPlanDocument, folder_name: str
) -> EditPlanDocument:
    """Analog zur Normalisierung der bestehenden Produktions-Save-Funktion
    (project_id/folder_name), zusätzlich confirmed=true — der Promote selbst
    ist der explizite Nutzerakt, der ein bereits PASS-validiertes
    Staging-Paket bestätigt.

    Visual-TimelineItems aus dem Cut-Plan-Staging haben oft leeres
    voice_file; für den späteren OTIO-Merge wird der VoiceoverPlan-Pfad
    auf die Items gestempelt."""
    voice_path = (document.voiceover.path if document.voiceover is not None else "").strip()
    timeline_items = list(document.timeline_items)
    if voice_path:
        timeline_items = [
            item
            if item.voice_file.strip()
            else item.model_copy(update={"voice_file": voice_path})
            for item in timeline_items
        ]
    return document.model_copy(
        update={
            "project_id": project.id,
            "folder_name": folder_name,
            "confirmed": True,
            "timeline_items": timeline_items,
        }
    )


def _scan_promoted_document_for_leaks(document: EditPlanDocument) -> list[str]:
    return _scan_for_leaked_secrets(document)


def promote_production_edit_plans(
    project: Project, *, allow_overwrite_section_ids: list[str] | None = None
) -> ProductionEditPlanPromoteManifest:
    """Führt den tatsächlichen Promote aus. Wirft ValueError, wenn
    can_promote_production_edit_plans False zurückgibt — in diesem Fall wird
    NICHTS geschrieben (weder Backup noch Zieldatei)."""
    eligible, reasons = can_promote_production_edit_plans(
        project, allow_overwrite_section_ids=allow_overwrite_section_ids
    )
    if not eligible:
        raise ValueError("Production EditPlan Promote blockiert: " + " ".join(reasons))

    allow_ids = set(allow_overwrite_section_ids or [])
    readiness = load_production_edit_plan_promote_readiness(project)
    package = load_production_edit_plan_staging_package(project)
    report = load_production_edit_plan_validation_report(project)
    promote_run_id = _promote_run_id()

    prepared: list[dict] = []
    sections_results: list[ProductionEditPlanPromoteSectionResult] = []

    for section in readiness.sections:
        if section.is_intro:
            sections_results.append(
                ProductionEditPlanPromoteSectionResult(
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    folder_name=section.folder_name,
                    is_intro=True,
                    source_staged_edit_plan_path=section.staged_edit_plan_path,
                    promote_action=PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_SKIPPED_INTRO,
                    source_hash=section.staged_edit_plan_hash,
                    warnings=[_INTRO_SKIPPED_REASON],
                )
            )
            continue

        document = load_staged_edit_plan(project, section.staging_section_id)
        target_path = _target_edit_plan_path_for_section(project, section)
        will_overwrite = (
            section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
            and section.staging_section_id in allow_ids
        )
        prepared.append(
            {
                "section": section,
                "document": document,
                "target_path": target_path,
                "will_overwrite": will_overwrite,
            }
        )

    # --- Phase 1: Backups für ALLE Overwrite-Sections ZUERST. Schlägt auch
    # nur eines fehl, wird NICHTS geschrieben (kein partieller Promote). ---
    backups: dict[str, tuple[str, str]] = {}
    backup_dir_path = get_production_edit_plan_promote_backup_run_dir(project.work_dir_path, promote_run_id)
    try:
        for item in prepared:
            if item["will_overwrite"]:
                backup_path, backup_hash = _backup_existing_edit_plan(
                    project, promote_run_id, item["section"].folder_name, item["target_path"]
                )
                backups[item["section"].staging_section_id] = (backup_path, backup_hash)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Backup vor Promote fehlgeschlagen — kein Write erfolgt (Promote vollständig abgebrochen): {exc}"
        ) from exc

    # --- Phase 2: atomare Writes. ---
    created_count = 0
    overwritten_count = 0
    for item in prepared:
        section = item["section"]
        document = item["document"]
        target_path = item["target_path"]
        will_overwrite = item["will_overwrite"]

        source_hash = content_hash_of_model(document)
        promoted_document = _set_promoted_document_confirmed_true(project, document, section.folder_name)
        serialized = promoted_document.model_dump_json(indent=2)
        _atomic_write_edit_plan_document(target_path, serialized)
        target_hash_after = content_hash(serialized)

        try:
            from otio_app.services.edit_plan_cache import invalidate_edit_plan_cache

            invalidate_edit_plan_cache(project.id, section.folder_name)
        except Exception:  # noqa: BLE001 — Cache-Invalidierung ist best-effort.
            pass

        backup_path, backup_hash = backups.get(section.staging_section_id, ("", ""))
        if will_overwrite:
            overwritten_count += 1
            action = PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN
        else:
            created_count += 1
            action = PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED

        sections_results.append(
            ProductionEditPlanPromoteSectionResult(
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                folder_name=section.folder_name,
                is_intro=False,
                source_staged_edit_plan_path=section.staged_edit_plan_path,
                target_edit_plan_path=str(target_path),
                promote_action=action,
                source_hash=source_hash,
                target_hash_after=target_hash_after,
                backup_path=backup_path,
                backup_hash=backup_hash,
                confirmed_set_to_true=True,
            )
        )

    skipped_intro_count = sum(1 for section in readiness.sections if section.is_intro)

    return ProductionEditPlanPromoteManifest(
        project_id=project.id,
        promote_run_id=promote_run_id,
        source_readiness_hash=content_hash_of_model(readiness),
        source_package_hash=content_hash_of_model(package),
        source_validation_report_hash=content_hash_of_model(report) if report is not None else "",
        status=PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
        sections=sections_results,
        created_count=created_count,
        overwritten_count=overwritten_count,
        skipped_intro_count=skipped_intro_count,
        blocked_count=0,
        backup_dir=str(backup_dir_path) if backups else "",
    )


def save_production_edit_plan_promote_manifest(
    project: Project, manifest: ProductionEditPlanPromoteManifest
) -> ProductionEditPlanPromoteManifest:
    normalized = manifest.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_promote_manifest_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_production_edit_plan_promote_manifest(project: Project) -> ProductionEditPlanPromoteManifest | None:
    path = get_production_edit_plan_promote_manifest_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanPromoteManifest.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def build_voice_folder_mapping_patch(
    project: Project, manifest: ProductionEditPlanPromoteManifest
) -> ProductionEditPlanVoiceFolderMappingPatch:
    """Reine Funktion — liest ausschließlich bereits vorhandene Daten
    (Promote-Manifest, die soeben promoteten EditPlanDocuments,
    `voice_folder_mapping.json`). Verändert `voice_folder_mapping.json`
    NICHT. Intro wird NICHT als normaler Folder-Eintrag aufgenommen."""
    existing_mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    existing_by_folder: dict[str, str] = {}
    if existing_mapping is not None:
        for entry in existing_mapping.entries:
            if entry.folder:
                existing_by_folder[entry.folder] = entry.voice_file

    entries: list[ProductionEditPlanVoiceFolderMappingPatchEntry] = []
    warnings: list[str] = []

    for section_result in manifest.sections:
        if section_result.is_intro:
            continue
        if section_result.promote_action not in {
            PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED,
            PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN,
        }:
            continue

        voiceover_path = ""
        voiceover_duration_sec = 0.0
        target_path = Path(section_result.target_edit_plan_path) if section_result.target_edit_plan_path else None
        if target_path is not None and target_path.is_file():
            try:
                promoted_doc = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
                if promoted_doc.voiceover is not None:
                    voiceover_path = promoted_doc.voiceover.path
                    voiceover_duration_sec = promoted_doc.voiceover.duration_sec
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(f"{section_result.folder_name}: promotete Datei nicht lesbar ({exc}).")

        existing_voice_file = existing_by_folder.get(section_result.folder_name)
        if existing_voice_file is None:
            action = PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD
            reason = "Folder ist noch nicht in voice_folder_mapping.json — würde neu hinzugefügt."
        elif existing_voice_file == voiceover_path:
            action = PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT
            reason = "Folder ist bereits mit demselben Voice-over in voice_folder_mapping.json vorhanden."
        else:
            action = PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW
            reason = (
                "Folder ist bereits in voice_folder_mapping.json vorhanden, aber mit einem anderen "
                f"Voice-over ({existing_voice_file!r} statt {voiceover_path!r}) — manuelle Prüfung nötig."
            )

        entries.append(
            ProductionEditPlanVoiceFolderMappingPatchEntry(
                folder_name=section_result.folder_name,
                edit_plan_path=section_result.target_edit_plan_path,
                voiceover_path=voiceover_path,
                voiceover_duration_sec=voiceover_duration_sec,
                action=action,
                reason=reason,
            )
        )

    return ProductionEditPlanVoiceFolderMappingPatch(
        project_id=project.id,
        promote_run_id=manifest.promote_run_id,
        entries=entries,
        warnings=warnings,
    )


def save_voice_folder_mapping_patch(
    project: Project, patch: ProductionEditPlanVoiceFolderMappingPatch
) -> ProductionEditPlanVoiceFolderMappingPatch:
    normalized = patch.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_voice_folder_mapping_patch_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_voice_folder_mapping_patch(project: Project) -> ProductionEditPlanVoiceFolderMappingPatch | None:
    path = get_production_edit_plan_voice_folder_mapping_patch_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanVoiceFolderMappingPatch.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def is_production_edit_plan_promote_manifest_stale(
    project: Project, manifest: ProductionEditPlanPromoteManifest
) -> bool:
    """True, wenn sich Readiness, Staging-Paket oder Validation Report seit
    diesem Promote-Lauf geändert haben (oder fehlen). Reine Lesefunktion."""
    readiness = load_production_edit_plan_promote_readiness(project)
    if readiness is None or content_hash_of_model(readiness) != manifest.source_readiness_hash:
        return True

    package = load_production_edit_plan_staging_package(project)
    if package is None or content_hash_of_model(package) != manifest.source_package_hash:
        return True

    if manifest.source_validation_report_hash:
        report = load_production_edit_plan_validation_report(project)
        if report is None or content_hash_of_model(report) != manifest.source_validation_report_hash:
            return True

    return False
