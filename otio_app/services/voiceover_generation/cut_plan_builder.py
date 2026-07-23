"""Phase 8.2: Orchestriert den Cut-Plan-Entwurf.

Liest `confirmed_voiceover_project_plan.json` (Phase 7, weiterhin die
redaktionelle Quelle der Wahrheit) und `cut_plan_settings.json`, ruft die
reine Zeit-/Mapping-Logik aus `cut_plan_timeline_service.py` auf und
speichert das Ergebnis als `cut_plan.draft.json`.

Noch KEINE Asset-Auswahl, keine Split-/Merge-Heuristik, keine Supplement
Requests, keine vollständige Validierung, kein Confirm/Lock. Kein
EditPlanDocument, kein OTIO-Export."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from otio_app.defaults import CUT_PLAN_STATUS_DRAFT, CUT_PLAN_STATUS_NEEDS_REVIEW
from otio_app.models import Project
from otio_app.project_layout import get_confirmed_voiceover_project_plan_path, get_cut_plan_draft_path
from otio_app.services.voiceover_generation.cut_plan_asset_selector import apply_asset_selection_to_cut_plan
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument
from otio_app.services.voiceover_generation.cut_plan_settings_service import load_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_timeline_service import build_cut_plan_timeline_skeleton
from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

if TYPE_CHECKING:  # nur für Typannotationen, verhindert Zirkelimport zur Laufzeit
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanValidationReport

__all__ = [
    "build_cut_plan_draft",
    "load_cut_plan_draft",
    "save_cut_plan_draft",
    "is_cut_plan_draft_stale",
    "is_cut_plan_settings_stale",
    "apply_asset_selection_to_draft",
    "validate_cut_plan_draft",
]


def build_cut_plan_draft(project: Project) -> CutPlanDocument:
    """Baut einen neuen Cut-Plan-Entwurf. Speichert NICHTS (siehe
    save_cut_plan_draft) — reine Funktion.

    Wirft ValueError, wenn kein bestätigter Voice-over-Projektplan
    vorhanden ist (analog generate_folder_voiceover in Phase 4)."""
    source_plan = load_confirmed_voiceover_project_plan(project)
    if source_plan is None:
        raise ValueError(
            "Kein bestätigter Voice-over-Projektplan (confirmed_voiceover_project_plan.json) vorhanden."
        )

    settings = load_cut_plan_settings(project)
    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, source_plan, settings)

    status = CUT_PLAN_STATUS_NEEDS_REVIEW if blockers else CUT_PLAN_STATUS_DRAFT

    return CutPlanDocument(
        project_id=project.id,
        project_title=source_plan.project_title,
        language=source_plan.language,
        source_plan_path=str(get_confirmed_voiceover_project_plan_path(project.language_work_dir_path)),
        source_plan_hash=content_hash_of_model(source_plan),
        status=status,
        timeline_fps=settings.timeline_fps,
        initial_audio_offset_sec=settings.initial_audio_offset_sec,
        pause_between_sections_sec=settings.pause_between_sections_sec,
        section_visual_preroll_sec=settings.section_visual_preroll_sec,
        timeline_width=settings.timeline_width,
        timeline_height=settings.timeline_height,
        settings_snapshot=settings.model_dump(mode="json", exclude={"project_id", "generated_at"}),
        items=items,
        audio_items=audio_items,
        supplement_requests=[],
        asset_usage_summary={},
        warnings=warnings,
        blockers=blockers,
    )


def load_cut_plan_draft(project: Project) -> CutPlanDocument | None:
    path = get_cut_plan_draft_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_cut_plan_draft(project: Project, document: CutPlanDocument) -> CutPlanDocument:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_cut_plan_draft_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def is_cut_plan_draft_stale(project: Project, cut_plan: CutPlanDocument) -> bool:
    """True, wenn sich der bestätigte Voice-over-Projektplan seit der
    Cut-Plan-Erzeugung geändert hat (Vergleich über source_plan_hash, analog
    is_project_plan_stale in Phase 7). Kein Auto-Update, kein Auto-Overwrite
    — die UI zeigt nur eine Warnung an."""
    current_source_plan = load_confirmed_voiceover_project_plan(project)
    current_hash = content_hash_of_model(current_source_plan)
    return cut_plan.source_plan_hash != current_hash


def is_cut_plan_settings_stale(project: Project, cut_plan: CutPlanDocument) -> bool:
    """True, wenn die aktuelle cut_plan_settings.json von dem im Draft
    eingefrorenen settings_snapshot abweicht (Vorab-Hardening vor Phase 8.4).

    cut_plan.settings_snapshot ist die Quelle für alle Berechnungen an diesem
    Draft (Asset-Auswahl, Validierung) — nicht die jeweils aktuelle Datei.
    Kein Auto-Update, kein Auto-Overwrite."""
    current_settings = load_cut_plan_settings(project)
    current_snapshot = current_settings.model_dump(mode="json", exclude={"project_id", "generated_at"})
    return cut_plan.settings_snapshot != current_snapshot


def apply_asset_selection_to_draft(project: Project) -> CutPlanDocument:
    """Lädt den bestehenden cut_plan.draft.json, wendet Asset-Auswahl/
    Fallback/Dauer-/Split-/Merge-Strategie an (Phase 8.3) und speichert das
    Ergebnis wieder als cut_plan.draft.json. Schreibt ausdrücklich NICHT
    cut_plan.confirmed.json, keinen Validation Report und kein Trace-File —
    diese Schritte folgen erst in späteren Sub-Phasen.

    Verwendet IMMER cut_plan.settings_snapshot (nicht die ggf. inzwischen
    geänderte cut_plan_settings.json) — wirft ValueError, wenn die aktuellen
    Settings vom Snapshot abweichen (siehe is_cut_plan_settings_stale). Der
    Nutzer muss den Draft dann explizit neu erzeugen, statt dass die
    Asset-Auswahl mit inkonsistenten Settings weiterläuft.

    Wirft ValueError, wenn noch kein Draft existiert."""
    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden — bitte zuerst einen Draft erzeugen.")
    if is_cut_plan_settings_stale(project, draft):
        raise ValueError(
            "Die Cut-Plan-Settings wurden seit Draft-Erzeugung geändert. Bitte Draft neu erzeugen."
        )

    updated_draft = apply_asset_selection_to_cut_plan(project, draft)
    return save_cut_plan_draft(project, updated_draft)


def validate_cut_plan_draft(project: Project) -> tuple[CutPlanDocument, CutPlanValidationReport]:
    """Lädt den bestehenden cut_plan.draft.json, validiert ihn vollständig
    (Phase 8.4), speichert den aktualisierten Draft UND den Validation
    Report. Schreibt ausdrücklich NICHT cut_plan.confirmed.json, kein
    cut_plan.trace.json, kein EditPlanDocument, kein OTIO.

    Vor der Validierung werden Visual-Coverage-Extensions erneut angewendet
    (u. a. Teil-Hold bei knappen Closing-Videos), damit Settings wie
    section_pause_hold_tolerance_sec und aktuelle Hold-Logik greifen.

    Wirft ValueError, wenn noch kein Draft existiert."""
    from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
    from otio_app.services.voiceover_generation.cut_plan_validator import (
        attach_validation_to_cut_plan,
        save_cut_plan_validation_report,
        validate_cut_plan,
    )
    from otio_app.services.voiceover_generation.cut_plan_visual_coverage import (
        apply_visual_coverage_extensions,
    )

    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden — bitte zuerst einen Draft erzeugen.")

    settings = settings_from_snapshot(project, draft)
    draft = apply_visual_coverage_extensions(draft, settings)

    report = validate_cut_plan(project, draft)
    updated_draft = attach_validation_to_cut_plan(draft, report)
    save_cut_plan_draft(project, updated_draft)
    save_cut_plan_validation_report(project, report)
    return updated_draft, report
