"""Cut-Plan Workflow Dashboard (Nutzervorgabe, Juli 2026): "die Buttons sind
all over the place, ich weiß nicht, welchen Schritt ich als nächstes
auslösen will" — dieses Modul berechnet EINEN konsolidierten Workflow-
Status über die gesamte Cut-Plan-Pipeline (Draft, Asset-Auswahl,
Validierung, Supplement, Validation Repair, Final Check) und leitet daraus
GENAU EINEN empfohlenen nächsten Schritt ab.

Reine Lesefunktion — löst KEINE Aktion aus, schreibt NICHTS. Die UI
(cut_plan_tab.py) rendert das Ergebnis oben im Tab als Checklist + einen
primären "nächster Schritt"-Button, der intern denselben Code wie die
bestehenden Detail-Buttons weiter unten im Tab aufruft.

Bewusste Design-Entscheidung: "Validierung" ist EIN wiederkehrendes Gate
(dieselbe Artefakt-Datei `cut_plan.validation_report.json`), kein separater
Schritt pro Pipeline-Phase — der Status wechselt einfach zwischen DONE und
STALE, je nachdem ob sich der Draft seit der letzten Validierung geändert
hat. Das bildet die Realität ab (derselbe Button, immer wieder), statt
künstlich mehrere "Validierung nach X"-Schritte zu simulieren, die sich nur
durch ihre Position in der Pipeline unterscheiden würden.

Dieses Modul ist bewusst als Liste von Einzel-Berechnungsfunktionen
aufgebaut, damit neue Pipeline-Schritte (wie die Residual-Gap-Schritte,
siehe cut_plan_residual_gap_requests.py) ergänzt werden können, ohne
bestehende Schritte umzubauen."""

from __future__ import annotations

from pydantic import BaseModel, Field

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
)
from otio_app.models import Project
from otio_app.services.voiceover_generation.cut_plan_builder import (
    is_cut_plan_draft_stale,
    is_cut_plan_settings_stale,
    load_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    count_unapplied_accepted_residual_gap_requests,
    load_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_visual_gap_analysis import (
    GAP_KIND_RESIDUAL_ITEM_GAP,
    analyze_visual_gaps,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    build_supplement_requests_from_cut_plan,
    count_unapplied_accepted_supplement_requests,
    load_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    find_repairable_validation_blockers,
    load_cut_plan_validation_repair_requests,
)
from otio_app.services.voiceover_generation.cut_plan_validator import (
    content_hash_of_cut_plan_content,
    load_cut_plan_validation_report,
)
from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED",
    "CUT_PLAN_WORKFLOW_STATUS_READY",
    "CUT_PLAN_WORKFLOW_STATUS_DONE",
    "CUT_PLAN_WORKFLOW_STATUS_STALE",
    "CUT_PLAN_WORKFLOW_STATUS_BLOCKED",
    "CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED",
    "CUT_PLAN_WORKFLOW_ACTIONABLE_STATUSES",
    "CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT",
    "CUT_PLAN_WORKFLOW_ACTION_APPLY_ASSET_SELECTION",
    "CUT_PLAN_WORKFLOW_ACTION_VALIDATE",
    "CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS",
    "CUT_PLAN_WORKFLOW_ACTION_REAPPLY_SUPPLEMENT_ASSETS",
    "CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_SUPPLEMENTS",
    "CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS",
    "CUT_PLAN_WORKFLOW_ACTION_REAPPLY_RESIDUAL_GAP_ASSETS",
    "CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_RESIDUAL_GAPS",
    "CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS",
    "CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_VALIDATION_REPAIR",
    "CutPlanWorkflowStep",
    "CutPlanWorkflowState",
    "compute_cut_plan_workflow_state",
]

CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT = "build_draft"
CUT_PLAN_WORKFLOW_ACTION_APPLY_ASSET_SELECTION = "apply_asset_selection"
CUT_PLAN_WORKFLOW_ACTION_VALIDATE = "validate_cut_plan"
CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS = "build_supplement_requests"
CUT_PLAN_WORKFLOW_ACTION_REAPPLY_SUPPLEMENT_ASSETS = "reapply_supplement_assets"
CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_SUPPLEMENTS = "auto_resolve_supplements"
CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS = "build_residual_gap_requests"
CUT_PLAN_WORKFLOW_ACTION_REAPPLY_RESIDUAL_GAP_ASSETS = "reapply_residual_gap_assets"
CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_RESIDUAL_GAPS = "auto_resolve_residual_gaps"
CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS = "build_validation_repair_requests"
CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_VALIDATION_REPAIR = "auto_resolve_validation_repair"

CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED = "NOT_STARTED"
CUT_PLAN_WORKFLOW_STATUS_READY = "READY"
CUT_PLAN_WORKFLOW_STATUS_DONE = "DONE"
CUT_PLAN_WORKFLOW_STATUS_STALE = "STALE"
CUT_PLAN_WORKFLOW_STATUS_BLOCKED = "BLOCKED"
CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED = "NOT_NEEDED"

# Diese drei Stati bedeuten "hier gibt es etwas zu tun" — werden vom
# Prioritäts-Scan in compute_cut_plan_workflow_state genutzt, um den
# EINEN empfohlenen nächsten Schritt zu bestimmen.
CUT_PLAN_WORKFLOW_ACTIONABLE_STATUSES = frozenset(
    {
        CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
        CUT_PLAN_WORKFLOW_STATUS_READY,
        CUT_PLAN_WORKFLOW_STATUS_STALE,
    }
)


class CutPlanWorkflowStep(BaseModel):
    """EIN Schritt der Cut-Plan-Pipeline für die Dashboard-Anzeige.

    `next_action_key` ist ein maschinenlesbarer Bezeichner (siehe
    CUT_PLAN_WORKFLOW_ACTION_KEYS_* Konstanten), den die UI zum
    Dispatchen der tatsächlichen Aktion nutzt — bewusst GETRENNT von
    `next_action_label` (menschenlesbarer Button-Text), damit ein
    späteres Umformulieren des Labels niemals die Verdrahtung bricht."""

    step_id: str
    label: str
    status: str = CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED
    summary: str = ""
    next_action_label: str = ""
    next_action_key: str = ""
    reason: str = ""


class CutPlanWorkflowState(BaseModel):
    """Konsolidiertes Ergebnis für das Dashboard — `steps` in fester
    Pipeline-Reihenfolge, `next_step_id` leer bedeutet: keine weitere
    automatisierbare Aktion (entweder alles fertig oder Rest-Blocker, die
    nicht automatisch lösbar sind, siehe `all_done`/`has_unresolvable_
    blockers`)."""

    steps: list[CutPlanWorkflowStep] = Field(default_factory=list)
    next_step_id: str = ""
    next_action_label: str = ""
    next_action_key: str = ""
    next_reason: str = ""
    all_done: bool = False
    has_unresolvable_blockers: bool = False
    unresolvable_blocker_count: int = 0


def _step_draft(project: Project, draft: CutPlanDocument | None, has_source_plan: bool) -> CutPlanWorkflowStep:
    if not has_source_plan:
        return CutPlanWorkflowStep(
            step_id="draft",
            label="Cut Plan Draft",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
            summary="Kein bestätigter Voice-over-Projektplan vorhanden.",
        )
    if draft is None:
        return CutPlanWorkflowStep(
            step_id="draft",
            label="Cut Plan Draft",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
            summary="Noch kein Draft erzeugt.",
            next_action_label="Cut Plan Draft erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT,
            reason="Es existiert noch kein Cut Plan Draft für den bestätigten Voice-over-Projektplan.",
        )
    if is_cut_plan_draft_stale(project, draft) or is_cut_plan_settings_stale(project, draft):
        return CutPlanWorkflowStep(
            step_id="draft",
            label="Cut Plan Draft",
            status=CUT_PLAN_WORKFLOW_STATUS_STALE,
            summary="Voice-over-Projektplan oder Cut-Plan-Settings haben sich seit Draft-Erzeugung geändert.",
            next_action_label="Cut Plan Draft neu erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT,
            reason="Der Draft basiert auf einem veralteten Projektplan oder veralteten Settings.",
        )
    return CutPlanWorkflowStep(
        step_id="draft",
        label="Cut Plan Draft",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"{len(draft.items)} Cut-Plan-Item(s).",
    )


def _step_asset_selection(draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    if draft is None:
        return CutPlanWorkflowStep(step_id="asset_selection", label="Asset-Auswahl", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED)
    total_items = len(draft.items)
    unresolved = sum(1 for item in draft.items if item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED)
    if total_items == 0:
        return CutPlanWorkflowStep(
            step_id="asset_selection", label="Asset-Auswahl", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
            summary="Draft enthält keine Cut-Plan-Items.",
        )
    if unresolved > 0:
        return CutPlanWorkflowStep(
            step_id="asset_selection",
            label="Asset-Auswahl",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{unresolved} von {total_items} Item(s) noch UNRESOLVED.",
            next_action_label="Asset-Auswahl anwenden",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_APPLY_ASSET_SELECTION,
            reason=f"{unresolved} Item(s) haben noch keine Asset-Auswahl durchlaufen.",
        )
    return CutPlanWorkflowStep(
        step_id="asset_selection",
        label="Asset-Auswahl",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"Alle {total_items} Item(s) klassifiziert.",
    )


def _step_validate(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    if draft is None:
        return CutPlanWorkflowStep(step_id="validate", label="Validierung", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED)
    report = load_cut_plan_validation_report(project)
    if report is None:
        return CutPlanWorkflowStep(
            step_id="validate",
            label="Validierung",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary="Noch nie validiert.",
            next_action_label="Cut Plan validieren",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_VALIDATE,
            reason="Für den aktuellen Draft liegt noch kein Validation Report vor.",
        )
    current_hash = content_hash_of_cut_plan_content(draft)
    if report.cut_plan_hash != current_hash:
        return CutPlanWorkflowStep(
            step_id="validate",
            label="Validierung",
            status=CUT_PLAN_WORKFLOW_STATUS_STALE,
            summary="Draft hat sich seit der letzten Validierung geändert.",
            next_action_label="Cut Plan erneut validieren",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_VALIDATE,
            reason="Der Draft wurde seit der letzten Validierung verändert (Asset-Auswahl, Supplement, Repair, …).",
        )
    return CutPlanWorkflowStep(
        step_id="validate",
        label="Validierung",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"Aktuell — {len(report.blockers)} Blocker, {len(report.warnings)} Warnungen.",
    )


def _step_supplement_requests(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    if draft is None:
        return CutPlanWorkflowStep(
            step_id="supplement_requests", label="Supplement Requests", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED
        )
    fresh_document = build_supplement_requests_from_cut_plan(project, draft)
    needed_count = len(fresh_document.requests)
    if needed_count == 0:
        return CutPlanWorkflowStep(
            step_id="supplement_requests",
            label="Supplement Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
            summary="Kein Item benötigt aktuell ein Supplement-Asset.",
        )
    existing = load_cut_plan_supplement_requests(project)
    if existing is None:
        return CutPlanWorkflowStep(
            step_id="supplement_requests",
            label="Supplement Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{needed_count} Item(s) benötigen ein Supplement-Asset.",
            next_action_label="Supplement Requests erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS,
            reason=f"{needed_count} Item(s) benötigen ein Supplement-Asset, aber es gibt noch keine Requests.",
        )
    if existing.source_cut_plan_hash != content_hash_of_model(draft):
        return CutPlanWorkflowStep(
            step_id="supplement_requests",
            label="Supplement Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_STALE,
            summary="Requests stammen aus einer älteren Draft-Version.",
            next_action_label="Supplement Requests neu erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS,
            reason="Der Draft hat sich seit dem letzten Erzeugen der Supplement Requests geändert.",
        )
    return CutPlanWorkflowStep(
        step_id="supplement_requests",
        label="Supplement Requests",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"{len(existing.requests)} Request(s) aktuell.",
    )


def _step_supplement_resolve(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    existing = load_cut_plan_supplement_requests(project) if draft is not None else None
    if existing is None or not existing.requests:
        return CutPlanWorkflowStep(
            step_id="supplement_resolve", label="Supplement Assets", status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED
        )
    unapplied = count_unapplied_accepted_supplement_requests(draft, existing) if draft is not None else 0
    if unapplied > 0:
        return CutPlanWorkflowStep(
            step_id="supplement_resolve",
            label="Supplement Assets",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{unapplied} akzeptierte Asset(s) noch nicht im Draft übernommen.",
            next_action_label="Akzeptierte Supplement-Assets anwenden",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_REAPPLY_SUPPLEMENT_ASSETS,
            reason=f"{unapplied} Request(s) haben bereits ein akzeptiertes Asset, das im Draft noch fehlt.",
        )
    open_count = sum(
        1 for request in existing.requests if request.status != CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED
    )
    if open_count > 0:
        return CutPlanWorkflowStep(
            step_id="supplement_resolve",
            label="Supplement Assets",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{open_count} von {len(existing.requests)} Request(s) noch ohne Asset.",
            next_action_label="Alle fehlenden Supplement-Assets automatisch suchen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_SUPPLEMENTS,
            reason=f"{open_count} Request(s) haben noch kein Asset gefunden/akzeptiert.",
        )
    return CutPlanWorkflowStep(
        step_id="supplement_resolve",
        label="Supplement Assets",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"Alle {len(existing.requests)} Request(s) akzeptiert und angewendet.",
    )


def _step_residual_gap_requests(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    if draft is None:
        return CutPlanWorkflowStep(
            step_id="residual_gap_requests", label="Residual Gap Requests", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED
        )
    settings = CutPlanSettings(project_id=project.id, **draft.settings_snapshot)
    fresh_gaps = analyze_visual_gaps(draft, settings)
    residual_count = sum(1 for gap in fresh_gaps if gap.gap_kind == GAP_KIND_RESIDUAL_ITEM_GAP)
    if residual_count == 0:
        return CutPlanWorkflowStep(
            step_id="residual_gap_requests",
            label="Residual Gap Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
            summary="Keine Rest-Lücken bei bereits versorgten Items gefunden.",
        )
    existing = load_residual_gap_requests(project)
    if existing is None:
        return CutPlanWorkflowStep(
            step_id="residual_gap_requests",
            label="Residual Gap Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{residual_count} Rest-Lücke(n) bei bereits versorgten Items gefunden.",
            next_action_label="Residual Gap Requests erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS,
            reason=(
                f"{residual_count} Item(s) haben bereits ein Asset, aber die visuelle Abdeckung reicht "
                "nicht bis zum erwarteten Fenster-Ende und die Lücke ist zu groß für eine Mini-Reparatur."
            ),
        )
    if existing.source_cut_plan_hash != content_hash_of_model(draft):
        return CutPlanWorkflowStep(
            step_id="residual_gap_requests",
            label="Residual Gap Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_STALE,
            summary="Requests stammen aus einer älteren Draft-Version.",
            next_action_label="Residual Gap Requests neu erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_RESIDUAL_GAP_REQUESTS,
            reason="Der Draft hat sich seit dem letzten Erzeugen der Residual Gap Requests geändert.",
        )
    return CutPlanWorkflowStep(
        step_id="residual_gap_requests",
        label="Residual Gap Requests",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"{len(existing.requests)} Request(s) aktuell.",
    )


def _step_residual_gap_resolve(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    existing = load_residual_gap_requests(project) if draft is not None else None
    if existing is None or not existing.requests:
        return CutPlanWorkflowStep(
            step_id="residual_gap_resolve", label="Residual Gap Assets", status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED
        )
    unapplied = count_unapplied_accepted_residual_gap_requests(draft, existing) if draft is not None else 0
    if unapplied > 0:
        return CutPlanWorkflowStep(
            step_id="residual_gap_resolve",
            label="Residual Gap Assets",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{unapplied} akzeptierte Asset(s) noch nicht im Draft übernommen.",
            next_action_label="Akzeptierte Residual-Gap-Assets anwenden",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_REAPPLY_RESIDUAL_GAP_ASSETS,
            reason=f"{unapplied} Request(s) haben bereits ein akzeptiertes Asset, das im Draft noch fehlt.",
        )
    open_count = sum(1 for request in existing.requests if not request.accepted_asset_id)
    if open_count > 0:
        return CutPlanWorkflowStep(
            step_id="residual_gap_resolve",
            label="Residual Gap Assets",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{open_count} von {len(existing.requests)} Request(s) noch ohne Asset.",
            next_action_label="Alle offenen Residual Gap Requests automatisch suchen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_RESIDUAL_GAPS,
            reason=f"{open_count} Request(s) haben noch kein Asset gefunden/akzeptiert.",
        )
    return CutPlanWorkflowStep(
        step_id="residual_gap_resolve",
        label="Residual Gap Assets",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"Alle {len(existing.requests)} Request(s) akzeptiert und angewendet.",
    )


def _step_validation_repair_requests(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    if draft is None:
        return CutPlanWorkflowStep(
            step_id="validation_repair_requests", label="Validation Repair Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
        )
    repairable = find_repairable_validation_blockers(draft)
    if not repairable:
        return CutPlanWorkflowStep(
            step_id="validation_repair_requests",
            label="Validation Repair Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
            summary="Keine reparierbaren Rest-Blocker (BLACK_GAP mit Zeitfenster, ASSET_REUSE_DISTANCE) im Draft.",
        )
    existing = load_cut_plan_validation_repair_requests(project)
    if existing is None:
        return CutPlanWorkflowStep(
            step_id="validation_repair_requests",
            label="Validation Repair Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{len(repairable)} reparierbare Rest-Blocker gefunden.",
            next_action_label="Validation Repair Requests erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS,
            reason=f"{len(repairable)} reparierbare Rest-Blocker gefunden, aber noch keine Requests erzeugt.",
        )
    if existing.source_cut_plan_hash != content_hash_of_model(draft):
        return CutPlanWorkflowStep(
            step_id="validation_repair_requests",
            label="Validation Repair Requests",
            status=CUT_PLAN_WORKFLOW_STATUS_STALE,
            summary="Requests stammen aus einer älteren Draft-Version.",
            next_action_label="Validation Repair Requests neu erzeugen",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_BUILD_VALIDATION_REPAIR_REQUESTS,
            reason="Der Draft hat sich seit dem letzten Erzeugen der Validation Repair Requests geändert.",
        )
    return CutPlanWorkflowStep(
        step_id="validation_repair_requests",
        label="Validation Repair Requests",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"{len(existing.requests)} Request(s) aktuell.",
    )


def _step_validation_repair_apply(project: Project, draft: CutPlanDocument | None) -> CutPlanWorkflowStep:
    existing = load_cut_plan_validation_repair_requests(project) if draft is not None else None
    if existing is None or not existing.requests:
        return CutPlanWorkflowStep(
            step_id="validation_repair_apply", label="Validation Repair anwenden",
            status=CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
        )
    open_count = sum(
        1 for request in existing.requests if request.status != CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED
    )
    if open_count > 0:
        return CutPlanWorkflowStep(
            step_id="validation_repair_apply",
            label="Validation Repair anwenden",
            status=CUT_PLAN_WORKFLOW_STATUS_READY,
            summary=f"{open_count} von {len(existing.requests)} Request(s) noch offen.",
            next_action_label="Alle offenen Validation Repair Requests automatisch reparieren",
            next_action_key=CUT_PLAN_WORKFLOW_ACTION_AUTO_RESOLVE_VALIDATION_REPAIR,
            reason=f"{open_count} Validation Repair Request(s) sind noch nicht bearbeitet.",
        )
    return CutPlanWorkflowStep(
        step_id="validation_repair_apply",
        label="Validation Repair anwenden",
        status=CUT_PLAN_WORKFLOW_STATUS_DONE,
        summary=f"Alle {len(existing.requests)} Request(s) bearbeitet.",
    )


def _step_final_check(project: Project, draft: CutPlanDocument | None) -> tuple[CutPlanWorkflowStep, int]:
    if draft is None:
        return (
            CutPlanWorkflowStep(step_id="final_check", label="Final Check", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED),
            0,
        )
    report = load_cut_plan_validation_report(project)
    if report is None or report.cut_plan_hash != content_hash_of_cut_plan_content(draft):
        return (
            CutPlanWorkflowStep(
                step_id="final_check", label="Final Check", status=CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
                summary="Wartet auf eine aktuelle Validierung.",
            ),
            0,
        )
    if report.blockers:
        return (
            CutPlanWorkflowStep(
                step_id="final_check",
                label="Final Check",
                status=CUT_PLAN_WORKFLOW_STATUS_BLOCKED,
                summary=f"{len(report.blockers)} Blocker verbleiben nach der aktuellen Validierung.",
                reason="Es gibt weiterhin Blocker, die nicht durch einen der obigen Automatik-Schritte gelöst wurden.",
            ),
            len(report.blockers),
        )
    return (
        CutPlanWorkflowStep(
            step_id="final_check", label="Final Check", status=CUT_PLAN_WORKFLOW_STATUS_DONE,
            summary="Cut Plan validiert, 0 Blocker.",
        ),
        0,
    )


def compute_cut_plan_workflow_state(project: Project) -> CutPlanWorkflowState:
    """Berechnet den vollständigen Workflow-Status für das Dashboard.
    Reine Lesefunktion, keine Seiteneffekte. Reihenfolge der Schritte ist
    FEST und entspricht der empfohlenen Pipeline-Reihenfolge — der erste
    Schritt mit einem aktionierbaren Status (siehe CUT_PLAN_WORKFLOW_
    ACTIONABLE_STATUSES) wird als `next_step_id` zurückgegeben."""
    source_plan = load_confirmed_voiceover_project_plan(project)
    draft = load_cut_plan_draft(project)

    steps = [
        _step_draft(project, draft, has_source_plan=source_plan is not None),
        _step_asset_selection(draft),
        _step_validate(project, draft),
        _step_supplement_requests(project, draft),
        _step_supplement_resolve(project, draft),
        _step_residual_gap_requests(project, draft),
        _step_residual_gap_resolve(project, draft),
        _step_validation_repair_requests(project, draft),
        _step_validation_repair_apply(project, draft),
    ]
    final_check_step, unresolvable_blocker_count = _step_final_check(project, draft)
    steps.append(final_check_step)

    next_step_id = ""
    next_action_label = ""
    next_action_key = ""
    next_reason = ""
    for step in steps:
        if step.status in CUT_PLAN_WORKFLOW_ACTIONABLE_STATUSES and step.next_action_label:
            next_step_id = step.step_id
            next_action_label = step.next_action_label
            next_action_key = step.next_action_key
            next_reason = step.reason
            break

    all_done = not next_step_id and final_check_step.status == CUT_PLAN_WORKFLOW_STATUS_DONE
    has_unresolvable_blockers = not next_step_id and final_check_step.status == CUT_PLAN_WORKFLOW_STATUS_BLOCKED

    return CutPlanWorkflowState(
        steps=steps,
        next_step_id=next_step_id,
        next_action_label=next_action_label,
        next_action_key=next_action_key,
        next_reason=next_reason,
        all_done=all_done,
        has_unresolvable_blockers=has_unresolvable_blockers,
        unresolvable_blocker_count=unresolvable_blocker_count,
    )
