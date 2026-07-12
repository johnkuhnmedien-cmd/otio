"""Phase 10.7: Voice Folder Mapping Merge — explizit bestätigte, selektive
Übernahme des Vorbereitungs-Patches (Phase 10.6) in die echte
`voice_folder_mapping.json`.

Dies ist die EINZIGE Stelle im gesamten "Projekt ohne Voice-Over"-Workflow,
die `voice_folder_mapping.json` tatsächlich verändern darf —
ausschließlich über `merge_voice_folder_mapping()`. Vor jedem Schreiben wird
die bestehende Datei gesichert (Backup + Byte-Verifikation), danach folgt
ein atomarer Write. NEEDS_REVIEW-Konflikte aus dem Patch MÜSSEN vom Nutzer
explizit aufgelöst werden (APPLY = neuen Voice-over übernehmen, SKIP =
bestehenden Eintrag unverändert lassen) — ohne vollständige Auflösung wird
der gesamte Merge blockiert (kein partieller Merge).

Kein OTIO-Export, kein Render, kein Lock-Konzept, keine LLM-Planung, kein
Aufruf der Save- oder Build-Funktionen der bestehenden Produktions-
EditPlan-Pipeline, keine automatische Neuplanung, keine automatische
Supplement-Suche. Verändert NIEMALS `_otio/edit_plan/`, `_otio/exports/`
oder `_otio/supplement/`, keine Originalmedien, keine Audio-Dateien.

Konfirmations-Design (bewusst konservativ):
- Das Dokument-Level-Flag `confirmed` von `voice_folder_mapping.json` wird
  NICHT automatisch auf True gesetzt — ein bereits bestehender Wert bleibt
  erhalten, eine neu angelegte Datei startet mit `confirmed=False`. Die
  bewusste Vollbestätigung der gesamten Zuordnung bleibt Aufgabe des
  bestehenden „② Zuordnung“-Tabs.
- Pro Eintrag wird `confirmed` nur gesetzt, wenn der Aufrufer
  `mark_entries_confirmed=True` explizit übergibt (Default False)."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import VoiceFolderMappingDocument, VoiceFolderMappingEntry
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_ADDED,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_ALREADY_PRESENT,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_CONFLICT_UNRESOLVED,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_UPDATED,
    VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_voice_folder_mapping_merge_backup_run_dir,
    get_voice_folder_mapping_merge_manifest_path,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.services.voiceover_generation.llm_trace_service import content_hash, content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
    load_production_edit_plan_promote_manifest,
    load_voice_folder_mapping_patch,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute_models import (
    ProductionEditPlanVoiceFolderMappingPatch,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge_models import (
    VoiceFolderMappingMergeEntryResult,
    VoiceFolderMappingMergeManifest,
)

__all__ = [
    "can_merge_voice_folder_mapping",
    "merge_voice_folder_mapping",
    "save_voice_folder_mapping_merge_manifest",
    "load_voice_folder_mapping_merge_manifest",
    "is_voice_folder_mapping_merge_manifest_stale",
]


def _merge_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def _needs_review_entries(
    patch: ProductionEditPlanVoiceFolderMappingPatch,
) -> list:
    return [entry for entry in patch.entries if entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW]


def can_merge_voice_folder_mapping(
    project: Project, *, folder_resolutions: dict[str, str] | None = None
) -> tuple[bool, list[str]]:
    """Einzige Quelle der Wahrheit für alle harten Merge-Voraussetzungen.
    Reine Funktion, kein Seiteneffekt."""
    reasons: list[str] = []
    resolutions = folder_resolutions or {}

    patch = load_voice_folder_mapping_patch(project)
    if patch is None:
        reasons.append(
            "Kein Voice Folder Mapping Patch vorhanden (production_edit_plan_voice_folder_mapping_patch.json fehlt)."
        )

    promote_manifest = load_production_edit_plan_promote_manifest(project)
    if promote_manifest is None:
        reasons.append("Kein Promote Manifest vorhanden (production_edit_plan_promote_manifest.json fehlt).")

    if patch is None or promote_manifest is None:
        return False, reasons

    if patch.promote_run_id != promote_manifest.promote_run_id:
        reasons.append(
            "Der Mapping Patch stammt aus einem anderen Promote-Lauf als das aktuelle Manifest "
            f"(Patch: {patch.promote_run_id!r}, Manifest: {promote_manifest.promote_run_id!r}) — bitte Promote "
            "und Patch neu erzeugen."
        )

    if not patch.entries:
        reasons.append("Der Mapping Patch enthält keine Einträge — nichts zu übernehmen.")

    unresolved = [
        entry.folder_name
        for entry in _needs_review_entries(patch)
        if resolutions.get(entry.folder_name)
        not in (VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY, VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP)
    ]
    if unresolved:
        reasons.append(
            "Für folgende Folder muss ein Konflikt explizit aufgelöst werden (APPLY/SKIP): "
            + ", ".join(unresolved)
            + ". Kein partieller Merge — bitte alle Konflikte auflösen oder den Merge nicht ausführen."
        )

    return not reasons, reasons


def _atomic_write_text(path: Path, text: str) -> None:
    """Analog zum Atomic-Write-Helper aus Phase 10.6 (bewusst lokal
    dupliziert statt eines privaten Cross-Modul-Imports): temp file im
    selben Zielordner, flush + fsync, dann os.replace."""
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


def _backup_existing_voice_folder_mapping(project: Project, merge_run_id: str, raw_text: str) -> tuple[str, str]:
    """Sichert die EXISTIERENDE voice_folder_mapping.json VOR dem
    Überschreiben. Gibt (backup_path, backup_hash) zurück. Wirft
    OSError/ValueError, wenn das Backup nicht byte-identisch verifiziert
    werden kann — der Aufrufer MUSS in diesem Fall die Datei unverändert
    lassen (kein Write)."""
    backup_dir = get_voice_folder_mapping_merge_backup_run_dir(project.work_dir_path, merge_run_id)
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_hash = content_hash(raw_text)
    backup_json_path = backup_dir / "voice_folder_mapping.existing.json"
    backup_hash_path = backup_dir / "voice_folder_mapping.existing.hash.txt"

    backup_json_path.write_text(raw_text, encoding="utf-8")
    backup_hash_path.write_text(backup_hash, encoding="utf-8")

    if backup_json_path.read_text(encoding="utf-8") != raw_text:
        raise ValueError("Backup-Verifikation fehlgeschlagen für voice_folder_mapping.json — nicht byte-identisch.")

    return str(backup_json_path), backup_hash


def _append_mapping_entry(
    final_entries: list[VoiceFolderMappingEntry],
    entry: VoiceFolderMappingEntry,
) -> None:
    """Intro muss am Anfang der Mapping-Reihenfolge stehen (Timeline-Start)."""
    if entry.folder == "Intro":
        final_entries[:] = [e for e in final_entries if e.folder != "Intro"]
        final_entries.insert(0, entry)
    else:
        final_entries.append(entry)


def merge_voice_folder_mapping(
    project: Project,
    *,
    folder_resolutions: dict[str, str] | None = None,
    mark_entries_confirmed: bool = False,
) -> VoiceFolderMappingMergeManifest:
    """Führt den tatsächlichen Merge aus. Wirft ValueError, wenn
    can_merge_voice_folder_mapping False zurückgibt — in diesem Fall wird
    NICHTS geschrieben (weder Backup noch voice_folder_mapping.json)."""
    eligible, reasons = can_merge_voice_folder_mapping(project, folder_resolutions=folder_resolutions)
    if not eligible:
        raise ValueError("Voice Folder Mapping Merge blockiert: " + " ".join(reasons))

    resolutions = folder_resolutions or {}
    patch = load_voice_folder_mapping_patch(project)
    promote_manifest = load_production_edit_plan_promote_manifest(project)
    existing_mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    existing_entries = list(existing_mapping.entries) if existing_mapping is not None else []
    existing_document_confirmed = existing_mapping.confirmed if existing_mapping is not None else False

    merge_run_id = _merge_run_id()
    mapping_path = project.voice_folder_mapping_path

    backup_path = ""
    backup_hash = ""
    previous_mapping_hash = ""
    if mapping_path.is_file():
        raw_text = mapping_path.read_text(encoding="utf-8")
        previous_mapping_hash = content_hash(raw_text)
        try:
            backup_path, backup_hash = _backup_existing_voice_folder_mapping(project, merge_run_id, raw_text)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Backup vor Merge fehlgeschlagen — kein Write erfolgt (Merge vollständig abgebrochen): {exc}"
            ) from exc

    by_folder: dict[str, VoiceFolderMappingEntry] = {
        entry.folder: entry for entry in existing_entries if entry.folder
    }
    final_entries: list[VoiceFolderMappingEntry] = list(existing_entries)
    results: list[VoiceFolderMappingMergeEntryResult] = []
    added_count = 0
    updated_count = 0
    skipped_count = 0

    for patch_entry in patch.entries:
        folder = patch_entry.folder_name
        resolution = resolutions.get(folder)
        previous_entry = by_folder.get(folder)
        previous_voice_file = previous_entry.voice_file if previous_entry else ""

        if patch_entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD:
            if resolution == VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP:
                skipped_count += 1
                results.append(
                    VoiceFolderMappingMergeEntryResult(
                        folder_name=folder,
                        action=VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER,
                        new_voice_file=patch_entry.voiceover_path,
                        applied=False,
                        reason="Vom Nutzer übersprungen (resolution=SKIP).",
                    )
                )
                continue
            final_entries = [entry for entry in final_entries if entry.folder != folder]
            _append_mapping_entry(
                final_entries,
                VoiceFolderMappingEntry(
                    voice_file=patch_entry.voiceover_path,
                    folder=folder,
                    match_method="production_promote",
                    confirmed=mark_entries_confirmed,
                ),
            )
            added_count += 1
            results.append(
                VoiceFolderMappingMergeEntryResult(
                    folder_name=folder,
                    action=VOICE_FOLDER_MAPPING_MERGE_ACTION_ADDED,
                    new_voice_file=patch_entry.voiceover_path,
                    applied=True,
                    reason="Neu aus Production EditPlan Promote übernommen.",
                )
            )
        elif patch_entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT:
            skipped_count += 1
            results.append(
                VoiceFolderMappingMergeEntryResult(
                    folder_name=folder,
                    action=VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_ALREADY_PRESENT,
                    previous_voice_file=previous_voice_file,
                    new_voice_file=patch_entry.voiceover_path,
                    applied=False,
                    reason="Bereits identisch in voice_folder_mapping.json vorhanden.",
                )
            )
        elif patch_entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW:
            if resolution == VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY:
                final_entries = [entry for entry in final_entries if entry.folder != folder]
                _append_mapping_entry(
                    final_entries,
                    VoiceFolderMappingEntry(
                        voice_file=patch_entry.voiceover_path,
                        folder=folder,
                        match_method="production_promote",
                        confirmed=mark_entries_confirmed,
                    ),
                )
                updated_count += 1
                results.append(
                    VoiceFolderMappingMergeEntryResult(
                        folder_name=folder,
                        action=VOICE_FOLDER_MAPPING_MERGE_ACTION_UPDATED,
                        previous_voice_file=previous_voice_file,
                        new_voice_file=patch_entry.voiceover_path,
                        applied=True,
                        reason="Konflikt vom Nutzer aufgelöst (resolution=APPLY) — neuer Voice-over übernommen.",
                    )
                )
            elif resolution == VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP:
                skipped_count += 1
                results.append(
                    VoiceFolderMappingMergeEntryResult(
                        folder_name=folder,
                        action=VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER,
                        previous_voice_file=previous_voice_file,
                        new_voice_file=patch_entry.voiceover_path,
                        applied=False,
                        reason="Konflikt vom Nutzer aufgelöst (resolution=SKIP) — bestehender Eintrag bleibt.",
                    )
                )
            else:
                # Defensiv — can_merge_voice_folder_mapping garantiert bereits,
                # dass jeder NEEDS_REVIEW-Konflikt aufgelöst ist.
                skipped_count += 1
                results.append(
                    VoiceFolderMappingMergeEntryResult(
                        folder_name=folder,
                        action=VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_CONFLICT_UNRESOLVED,
                        previous_voice_file=previous_voice_file,
                        new_voice_file=patch_entry.voiceover_path,
                        applied=False,
                        reason="Konflikt nicht aufgelöst — bestehender Eintrag bleibt unverändert.",
                    )
                )

    document = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=existing_document_confirmed,
        entries=final_entries,
    )
    serialized = document.model_dump_json(indent=2)
    _atomic_write_text(mapping_path, serialized)
    new_mapping_hash = content_hash(serialized)

    return VoiceFolderMappingMergeManifest(
        project_id=project.id,
        merge_run_id=merge_run_id,
        source_patch_hash=content_hash_of_model(patch),
        source_promote_manifest_hash=content_hash_of_model(promote_manifest),
        previous_mapping_hash=previous_mapping_hash,
        new_mapping_hash=new_mapping_hash,
        backup_path=backup_path,
        backup_hash=backup_hash,
        status=VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED,
        entries=results,
        added_count=added_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
    )


def save_voice_folder_mapping_merge_manifest(
    project: Project, manifest: VoiceFolderMappingMergeManifest
) -> VoiceFolderMappingMergeManifest:
    normalized = manifest.model_copy(update={"project_id": project.id})
    path = get_voice_folder_mapping_merge_manifest_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_voice_folder_mapping_merge_manifest(project: Project) -> VoiceFolderMappingMergeManifest | None:
    path = get_voice_folder_mapping_merge_manifest_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceFolderMappingMergeManifest.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def is_voice_folder_mapping_merge_manifest_stale(project: Project, manifest: VoiceFolderMappingMergeManifest) -> bool:
    """True, wenn sich der Mapping Patch seit diesem Merge-Lauf geändert hat
    oder wenn `voice_folder_mapping.json` seither extern verändert wurde
    (z. B. über den bestehenden „② Zuordnung“-Tab). Reine Lesefunktion."""
    patch = load_voice_folder_mapping_patch(project)
    if patch is None or content_hash_of_model(patch) != manifest.source_patch_hash:
        return True

    mapping_path = project.voice_folder_mapping_path
    current_hash = content_hash(mapping_path.read_text(encoding="utf-8")) if mapping_path.is_file() else ""
    if current_hash != manifest.new_mapping_hash:
        return True

    return False
