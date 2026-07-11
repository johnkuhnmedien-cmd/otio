"""Phase 8.7: Cut Plan Confirm.

Bestätigt einen bereits vollständig validierten Cut-Plan-Entwurf als
`cut_plan.confirmed.json` — reiner Snapshot des vorhandenen, validierten
Drafts. Führt KEINEN Rebuild, KEINE erneute Asset-Auswahl und KEINE erneute
Validierung aus. Kein EditPlanDocument, kein OTIO-Export, kein locked
EditPlan, kein LLM-Konfliktlöser."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_STATUS_CONFIRMED,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_confirmed_path
from otio_app.services.voiceover_generation.cut_plan_builder import (
    is_cut_plan_draft_stale,
    is_cut_plan_settings_stale,
    load_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanValidationReport
from otio_app.services.voiceover_generation.cut_plan_settings_service import load_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    is_open_cut_plan_supplement_request,
    load_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_trace_service import build_cut_plan_trace, save_cut_plan_trace
from otio_app.services.voiceover_generation.cut_plan_validator import (
    content_hash_of_cut_plan_content,
    load_cut_plan_validation_report,
)
from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "can_confirm_cut_plan",
    "confirm_cut_plan",
    "load_confirmed_cut_plan",
    "unconfirm_cut_plan",
    "is_confirmed_cut_plan_stale",
]

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def can_confirm_cut_plan(
    project: Project,
    cut_plan: CutPlanDocument | None,
    validation_report: CutPlanValidationReport | None,
) -> tuple[bool, list[str]]:
    """Prüft alle Confirm-Bedingungen (§1) und gibt (eligible, reasons)
    zurück — reasons ist leer, wenn eligible True ist. Reine Funktion, kein
    Seiteneffekt."""
    reasons: list[str] = []

    if cut_plan is None:
        reasons.append("Kein Cut Plan Draft vorhanden.")
        return False, reasons
    if validation_report is None:
        reasons.append("Kein Validation Report vorhanden — bitte zuerst „Cut Plan validieren“ ausführen.")
        return False, reasons

    if validation_report.status == CUT_PLAN_VALIDATION_STATUS_BLOCKED:
        reasons.append("Validation Report ist BLOCKED.")
    if validation_report.blockers:
        reasons.append(f"Validation Report enthält {len(validation_report.blockers)} Blocker.")
    if cut_plan.status != CUT_PLAN_STATUS_VALIDATED:
        reasons.append(f"Cut Plan Status ist '{cut_plan.status}', erwartet '{CUT_PLAN_STATUS_VALIDATED}'.")

    current_hash = content_hash_of_cut_plan_content(cut_plan)
    if validation_report.cut_plan_hash != current_hash:
        reasons.append(
            "Validation Report ist veraltet — der Cut Plan hat sich seit der letzten Validierung geändert."
        )

    if is_cut_plan_draft_stale(project, cut_plan):
        reasons.append("Der bestätigte Voice-over-Projektplan hat sich seit Draft-Erzeugung geändert.")
    if is_cut_plan_settings_stale(project, cut_plan):
        reasons.append("Die Cut-Plan-Settings wurden seit Draft-Erzeugung geändert.")

    unresolved_item_ids = [
        item.cut_item_id for item in cut_plan.items
        if item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED
    ]
    if unresolved_item_ids:
        reasons.append(
            f"{len(unresolved_item_ids)} CutPlanItem(s) mit Status UNRESOLVED: "
            f"{', '.join(unresolved_item_ids)}."
        )

    supplement_required_item_ids = [
        item.cut_item_id for item in cut_plan.items
        if item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED
    ]
    if supplement_required_item_ids:
        reasons.append(
            f"{len(supplement_required_item_ids)} CutPlanItem(s) mit Status SUPPLEMENT_REQUIRED: "
            f"{', '.join(supplement_required_item_ids)}."
        )

    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is not None:
        current_item_ids = {item.cut_item_id for item in cut_plan.items}
        open_request_ids = sorted(
            {
                request.request_id
                for request in requests_document.requests
                if is_open_cut_plan_supplement_request(request)
                and request.cut_item_id in current_item_ids
            }
        )
        if open_request_ids:
            reasons.append(
                "Es gibt noch offene Supplement Requests ohne akzeptierten Kandidaten: "
                f"{', '.join(open_request_ids)}."
            )

    return not reasons, reasons


def confirm_cut_plan(project: Project) -> CutPlanDocument:
    """Lädt den bestehenden validierten Draft und den Validation Report, prüft
    can_confirm_cut_plan, schreibt bei Erfolg cut_plan.confirmed.json (status
    CONFIRMED, confirmed_at gesetzt, generated_at/source_plan_hash/
    settings_snapshot unverändert übernommen) sowie cut_plan.trace.json.

    Führt KEINEN Rebuild, KEINE Asset-Auswahl, KEINE Validierung aus — nur
    das bereits vorhandene, validierte Draft-Ergebnis wird übernommen.

    Wirft ValueError mit den konkreten Gründen, wenn can_confirm_cut_plan
    False zurückgibt."""
    draft = load_cut_plan_draft(project)
    report = load_cut_plan_validation_report(project)

    eligible, reasons = can_confirm_cut_plan(project, draft, report)
    if not eligible:
        raise ValueError("Cut Plan kann nicht bestätigt werden: " + " ".join(reasons))

    confirmed = draft.model_copy(  # type: ignore[union-attr]
        update={"status": CUT_PLAN_STATUS_CONFIRMED, "confirmed_at": _utcnow()}
    )
    path = get_cut_plan_confirmed_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(confirmed.model_dump_json(indent=2), encoding="utf-8")

    trace = build_cut_plan_trace(project, confirmed)
    save_cut_plan_trace(project, trace)

    return confirmed


def load_confirmed_cut_plan(project: Project) -> CutPlanDocument | None:
    path = get_cut_plan_confirmed_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def unconfirm_cut_plan(project: Project) -> None:
    """Nimmt eine Bestätigung zurück, indem cut_plan.confirmed.json entfernt
    wird. Der Draft (cut_plan.draft.json) bleibt unverändert — sein Status
    kann weiterhin VALIDATED sein. Minimal & testbar (Phase 8.7 §5): kein
    Archivieren, nur Löschen."""
    path = get_cut_plan_confirmed_path(project.work_dir_path)
    if path.is_file():
        path.unlink()


def is_confirmed_cut_plan_stale(project: Project, confirmed_plan: CutPlanDocument) -> bool:
    """True, wenn sich der bestätigte Voice-over-Projektplan oder die
    Cut-Plan-Settings seit der Bestätigung geändert haben. Kein Blocker —
    der bestätigte Cut Plan bleibt ein bewusster Snapshot und wird NICHT
    automatisch überschrieben (§7)."""
    current_source_plan = load_confirmed_voiceover_project_plan(project)
    current_source_hash = content_hash_of_model(current_source_plan)
    if confirmed_plan.source_plan_hash != current_source_hash:
        return True

    current_settings = load_cut_plan_settings(project)
    current_snapshot = current_settings.model_dump(mode="json", exclude={"project_id", "generated_at"})
    return confirmed_plan.settings_snapshot != current_snapshot
