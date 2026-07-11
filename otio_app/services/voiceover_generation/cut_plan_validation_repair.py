"""Validation Repair (Nutzervorgabe, Juli 2026): eigenständiger, dem
regulären Supplement-Bereich NACHGESCHALTETER Reparatur-Schritt.

Hintergrund: nach der vollständigen Cut-Plan-Validierung
(validate_cut_plan_draft) bleiben oft nur noch WENIGE, aber hartnäckige
Rest-Blocker übrig — typischerweise BLACK_GAP_DURING_VOICEOVER (kurze
visuelle Lücken zwischen bereits gewählten VisualSegments) und
ASSET_REUSE_DISTANCE_TOO_SHORT (ein Segment nutzt ein zu früh erneut
verwendetes Asset). Der bestehende `build_supplement_requests_from_cut_
plan` (cut_plan_supplement_bridge.py) behandelt solche Items wie jedes
andere SUPPLEMENT_REQUIRED-Item — d. h. er würde für das GESAMTE Item ein
Ersatz-Asset suchen. Für BLACK_GAP ist das redaktionell falsch: die
Lücke ist oft deutlich kürzer als shot_min_sec, ein einfacher Ersatz
würde selbst wieder gegen die Mindest-Shot-Länge verstoßen oder visuell
flackern (siehe Nutzerdiskussion Juli 2026).

Dieses Modul erkennt NUR die nach der Validierung verbleibenden,
bereits einem CutPlanItem zugeordneten BLACK_GAP_DURING_VOICEOVER-/
ASSET_REUSE_DISTANCE_TOO_SHORT-Blocker und baut daraus eigenständige
`CutPlanValidationRepairRequest`-Einträge (siehe cut_plan_validation_
repair_models.py) — GETRENNT von `supplement_requests.from_cut_plan.
json`, mit eigener Datei `validation_repair_requests.json` und eigenem
UI-Bereich UNTERHALB der bestehenden Supplement Requests.

Baut in dieser Phase (3) ausschließlich die Requests inkl. des rohen
Lücken-Zeitfensters (gap_start_sec/gap_end_sec) — die eigentliche
Reparatur-Logik (Zeitfenster-Erweiterung mit Kürzung angrenzender
Segmente für BLACK_GAP, Asset-Ersatz für ASSET_REUSE_DISTANCE) folgt in
Phase 4/5/7. Schreibt NICHTS an cut_plan.draft.json, keine Downloads,
keine LLM-Aufrufe — reine Erkennungs-/Aufbau-Funktion plus I/O für das
eigene Requests-Dokument."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import (
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_validation_repair_requests_path
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanValidationError
from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
    CutPlanValidationRepairRequest,
    CutPlanValidationRepairRequestsDocument,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "REPAIRABLE_VALIDATION_ERROR_TYPES",
    "find_repairable_validation_blockers",
    "build_validation_repair_requests_from_cut_plan",
    "save_cut_plan_validation_repair_requests",
    "load_cut_plan_validation_repair_requests",
    "update_cut_plan_validation_repair_request",
]

# Nur diese beiden Fehlertypen werden vom Validation-Repair-Schritt
# behandelt — beide sind über cut_item_id einem konkreten CutPlanItem
# zugeordnet (Voraussetzung, siehe cut_plan_validator.py). Andere
# asset-bezogene Blocker (MISSING_ASSET_MAPPING, ASSET_TOO_SHORT, …)
# bleiben bewusst beim bestehenden Supplement-Request-Builder (Phase F) —
# dort ist "Ersatz für das ganze Item" tatsächlich die richtige Lösung.
REPAIRABLE_VALIDATION_ERROR_TYPES = frozenset(
    {
        CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
        CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    }
)

_REPAIR_TYPE_BY_ERROR_TYPE: dict[str, str] = {
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER: CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT: CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE,
}


def _safe_path_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned or "item"


def find_repairable_validation_blockers(cut_plan: CutPlanDocument) -> list[CutPlanValidationError]:
    """Liefert alle Blocker aus `cut_plan.blockers` (vom vollständigen
    Validierungslauf, siehe attach_validation_to_cut_plan), die BEIDE
    Bedingungen erfüllen: Typ ist reparierbar (siehe
    REPAIRABLE_VALIDATION_ERROR_TYPES) UND einem konkreten CutPlanItem
    zugeordnet (cut_item_id gesetzt) — ein nicht attribuierbarer
    BLACK_GAP-Blocker (siehe cut_plan_validator._items_overlapping_gap,
    Fall 'kein zugehöriges Cut-Plan-Item') kann nicht repariert werden,
    weil es kein Item gibt, dessen Nachbar-Segmente angepasst werden
    könnten."""
    return [
        error
        for error in cut_plan.blockers
        if error.type in REPAIRABLE_VALIDATION_ERROR_TYPES and error.cut_item_id
    ]


def build_validation_repair_requests_from_cut_plan(
    project: Project, cut_plan: CutPlanDocument
) -> CutPlanValidationRepairRequestsDocument:
    """Reine Funktion — gruppiert die reparierbaren Blocker (siehe
    find_repairable_validation_blockers) nach (repair_type, cut_item_id)
    und baut GENAU EINEN CutPlanValidationRepairRequest je Gruppe.

    Mehrere BLACK_GAP-Blocker für DASSELBE Item (z. B. zwei getrennte
    unbedeckte Teilbereiche innerhalb seines Audio-Zeitraums) werden zu
    einem einzigen Request mit dem UMFASSENDEN Zeitfenster
    [min(gap_start_sec), max(gap_end_sec)] zusammengeführt — Phase 4
    berechnet daraus ein einziges, konsistentes Reparatur-Segment statt
    mehrerer sich potenziell überlappender Mini-Segmente.

    Speichert nichts (siehe save_cut_plan_validation_repair_requests)."""
    repairable_blockers = find_repairable_validation_blockers(cut_plan)
    items_by_id = {item.cut_item_id: item for item in cut_plan.items}

    grouped: dict[tuple[str, str], list[CutPlanValidationError]] = {}
    order: list[tuple[str, str]] = []
    for error in repairable_blockers:
        repair_type = _REPAIR_TYPE_BY_ERROR_TYPE.get(error.type)
        if repair_type is None:
            continue
        key = (repair_type, error.cut_item_id)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(error)

    requests: list[CutPlanValidationRepairRequest] = []
    for repair_type, cut_item_id in order:
        item = items_by_id.get(cut_item_id)
        if item is None:
            continue  # Item existiert nicht mehr im aktuellen Draft -> veraltet
        errors_for_key = grouped[(repair_type, cut_item_id)]

        gap_start_sec = 0.0
        gap_end_sec = 0.0
        if repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP:
            gap_bounds = [
                (error.gap_start_sec, error.gap_end_sec)
                for error in errors_for_key
                if error.gap_end_sec > error.gap_start_sec
            ]
            if gap_bounds:
                gap_start_sec = min(bounds[0] for bounds in gap_bounds)
                gap_end_sec = max(bounds[1] for bounds in gap_bounds)

        needed_duration_sec = (
            max(0.0, gap_end_sec - gap_start_sec)
            if repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP
            else item.duration_sec
        )

        reason = (
            f"Cut-Plan-Validierung meldet: {errors_for_key[0].type}. "
            + (
                f"Visuelles Loch {gap_start_sec:.2f}s–{gap_end_sec:.2f}s."
                if repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP
                else "Asset wird zu früh wiederverwendet — Ersatz-Asset benötigt."
            )
        )

        requests.append(
            CutPlanValidationRepairRequest(
                repair_id=f"repair_{repair_type.lower()}_{_safe_path_component(cut_item_id)}",
                repair_type=repair_type,
                cut_item_id=cut_item_id,
                source_scope=item.source_scope,
                folder_name=item.folder_name,
                text=item.text,
                visual_intent=item.visual_intent,
                gap_start_sec=gap_start_sec,
                gap_end_sec=gap_end_sec,
                needed_duration_sec=needed_duration_sec,
                reason=reason,
                source_error_message=" | ".join(dict.fromkeys(error.message for error in errors_for_key)),
            )
        )

    return CutPlanValidationRepairRequestsDocument(
        project_id=project.id,
        source_cut_plan_hash=content_hash_of_model(cut_plan),
        requests=requests,
    )


def save_cut_plan_validation_repair_requests(
    project: Project, document: CutPlanValidationRepairRequestsDocument
) -> Path:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_cut_plan_validation_repair_requests_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_cut_plan_validation_repair_requests(project: Project) -> CutPlanValidationRepairRequestsDocument | None:
    path = get_cut_plan_validation_repair_requests_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanValidationRepairRequestsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def update_cut_plan_validation_repair_request(
    project: Project, repair_id: str, **updates: object
) -> CutPlanValidationRepairRequest | None:
    """Lädt/ändert/speichert GENAU EINEN Repair Request per repair_id, alle
    anderen Requests im Dokument bleiben unverändert — analog zu
    update_cut_plan_supplement_request in cut_plan_supplement_bridge.py."""
    document = load_cut_plan_validation_repair_requests(project)
    if document is None:
        return None
    updated_request: CutPlanValidationRepairRequest | None = None
    new_requests: list[CutPlanValidationRepairRequest] = []
    for request in document.requests:
        if request.repair_id == repair_id:
            updated_request = request.model_copy(update=updates)
            new_requests.append(updated_request)
        else:
            new_requests.append(request)
    save_cut_plan_validation_repair_requests(project, document.model_copy(update={"requests": new_requests}))
    return updated_request
