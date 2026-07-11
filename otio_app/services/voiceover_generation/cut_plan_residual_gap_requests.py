"""Residual Gap Requests (Nutzervorgabe, Juli 2026): Erkennung + Aufbau +
Cache-Verwaltung für den dritten, eigenständigen Reparaturpfad zwischen
Supplement (Item hat noch KEIN Asset) und Validation Repair (kleine Lücke,
per Nachbar-Kürzung reparierbar).

Baut GENAU EINEN Request je Item mit mindestens einer GAP_KIND_RESIDUAL_
ITEM_GAP-Lücke (siehe cut_plan_visual_gap_analysis.py) — mehrere Rest-
Lücken desselben Items werden zu einem Request mit dem umfassenden
[gap_start_sec, gap_end_sec]-Fenster zusammengeführt, analog zu
cut_plan_validation_repair.build_validation_repair_requests_from_cut_plan.

Nutzervorgabe (Pflicht, Juli 2026, "wie bei der normalen Supplement-
Pipeline will ich, dass ein einmal gefundenes Asset nicht erneut gesucht
werden muss"): `merge_prior_residual_gap_request_state` behält ein bereits
akzeptiertes Asset für DENSELBEN `cut_item_id` beim Neu-Erzeugen, wenn die
Datei noch existiert UND (a) exakt dieselbe Gap-Signatur vorliegt ODER
(b) das Asset auch für die AKTUELLE (leicht verschobene) Lücke technisch
noch ausreicht. Reicht es nicht mehr, bleibt der Request offen — mit einer
Warnung statt einer stillen Neu-Suche. Keine externe Suche, kein Download,
keine LLM-Aufrufe in diesem Modul."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import (
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_residual_gap_requests_path
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument
from otio_app.services.voiceover_generation.cut_plan_residual_gap_models import (
    CutPlanResidualGapRequest,
    CutPlanResidualGapRequestsDocument,
)
from otio_app.services.voiceover_generation.cut_plan_visual_gap_analysis import (
    GAP_KIND_RESIDUAL_ITEM_GAP,
    CutPlanVisualGap,
    analyze_visual_gaps,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "build_residual_gap_requests_from_cut_plan",
    "cache_signature_for_residual_gap_request",
    "merge_prior_residual_gap_request_state",
    "count_unapplied_accepted_residual_gap_requests",
    "save_residual_gap_requests",
    "load_residual_gap_requests",
    "update_residual_gap_request",
]

_EPSILON = 0.05
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".m4v"})


def _safe_path_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned or "item"


def cache_signature_for_residual_gap_request(
    cut_item_id: str, gap_start_sec: float, gap_end_sec: float, repair_mode: str
) -> str:
    """Starker Cache-Key (siehe Modul-Docstring, Fall a) — auf 0.1s
    gerundet, damit winzige Fließkomma-Abweichungen zwischen zwei Läufen
    (z. B. durch erneutes Runden bei der Draft-Serialisierung) nicht
    versehentlich einen exakten Treffer verhindern."""
    return f"{cut_item_id}|{round(gap_start_sec, 1)}|{round(gap_end_sec, 1)}|{repair_mode}"


def _repair_mode_for_gap(item_timeline_end_sec: float, gap_start_sec: float) -> str:
    """PATCH_GAP_ONLY, wenn die Lücke erst BEI/NACH dem Ende der eigenen
    Audio-Spanne (`item.timeline_end_sec`) beginnt — reine Pause/Visual-
    Window-Überhang, das bestehende Segment bleibt unverändert, ein
    zusätzliches Patch-Segment füllt nur den Rest. REPLACE_ITEM_VISUAL,
    wenn die Lücke bereits INNERHALB der eigenen Audio-Spanne liegt (das
    vorhandene Asset deckt den Satz selbst nicht vollständig ab — ein
    Patch mitten im Satz wäre redaktionell riskanter als ein
    vollständiger Ersatz)."""
    if gap_start_sec >= item_timeline_end_sec - _EPSILON:
        return CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY
    return CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL


def build_residual_gap_requests_from_cut_plan(
    project: Project, cut_plan: CutPlanDocument
) -> CutPlanResidualGapRequestsDocument:
    """Reine Funktion — berechnet Rest-Lücken direkt aus Draft-Daten
    (siehe analyze_visual_gaps), filtert auf GAP_KIND_RESIDUAL_ITEM_GAP und
    gruppiert nach cut_item_id. Speichert nichts (siehe
    save_residual_gap_requests)."""
    settings = settings_from_snapshot(project, cut_plan)
    all_gaps = analyze_visual_gaps(cut_plan, settings)
    residual_gaps = [gap for gap in all_gaps if gap.gap_kind == GAP_KIND_RESIDUAL_ITEM_GAP and gap.cut_item_id]

    grouped: dict[str, list[CutPlanVisualGap]] = {}
    order: list[str] = []
    for gap in residual_gaps:
        if gap.cut_item_id not in grouped:
            grouped[gap.cut_item_id] = []
            order.append(gap.cut_item_id)
        grouped[gap.cut_item_id].append(gap)

    items_by_id = {item.cut_item_id: item for item in cut_plan.items}
    requests: list[CutPlanResidualGapRequest] = []
    for cut_item_id in order:
        item = items_by_id.get(cut_item_id)
        if item is None:
            continue
        gaps_for_item = grouped[cut_item_id]
        gap_start_sec = min(gap.gap_start_sec for gap in gaps_for_item)
        gap_end_sec = max(gap.gap_end_sec for gap in gaps_for_item)
        needed_duration_sec = max(0.0, gap_end_sec - gap_start_sec)
        primary_gap = gaps_for_item[0]
        repair_mode = _repair_mode_for_gap(item.timeline_end_sec, gap_start_sec)

        requests.append(
            CutPlanResidualGapRequest(
                request_id=f"residual_{_safe_path_component(cut_item_id)}",
                cut_item_id=cut_item_id,
                source_scope=item.source_scope,
                folder_name=item.folder_name,
                text=item.text,
                visual_intent=item.visual_intent,
                gap_start_sec=gap_start_sec,
                gap_end_sec=gap_end_sec,
                needed_duration_sec=needed_duration_sec,
                expected_start_sec=primary_gap.expected_start_sec,
                expected_end_sec=primary_gap.expected_end_sec,
                existing_asset_id=item.chosen_asset_id,
                existing_asset_status=item.asset_selection_status,
                repair_mode=repair_mode,
                reason=(
                    f"Item ist bereits versorgt ({item.asset_selection_status}), aber die visuelle "
                    f"Abdeckung reicht nicht bis {gap_end_sec:.2f}s (Rest-Lücke {needed_duration_sec:.2f}s) "
                    "und die Nachbar-Segmente haben nicht genug Kürzungs-Spielraum für eine Mini-Reparatur."
                ),
            )
        )

    return CutPlanResidualGapRequestsDocument(
        project_id=project.id,
        source_cut_plan_hash=content_hash_of_model(cut_plan),
        requests=requests,
    )


def _cached_asset_still_fits(accepted_asset_path: str, needed_duration_sec: float) -> bool:
    path = Path(accepted_asset_path)
    if not path.is_file():
        return False
    if path.suffix.lower() not in _VIDEO_SUFFIXES:
        return True  # Bilder gelten als beliebig haltbar (§3, wie bei Supplement).
    duration = probe_duration_seconds(path)
    if duration is None:
        return True  # optimistisch — die eigentliche Prüfung übernimmt der Apply-Schritt (Phase 4).
    return duration >= needed_duration_sec - _EPSILON


def merge_prior_residual_gap_request_state(
    new_document: CutPlanResidualGapRequestsDocument,
    prior_document: CutPlanResidualGapRequestsDocument | None,
) -> CutPlanResidualGapRequestsDocument:
    """Pflicht-Verhalten (Nutzervorgabe Juli 2026): beim Neu-Erzeugen NICHT
    einfach alle Requests durch frische, leere Requests ersetzen — ein
    bereits akzeptiertes Asset pro `cut_item_id` bleibt erhalten, wenn:

    a) die Datei noch existiert UND
    b) entweder die Gap-Signatur exakt übereinstimmt (starker Cache-Key)
       ODER das Asset für die AKTUELLE Lücke technisch noch ausreicht
       (schwacher Fallback — z. B. weil sich Timeline-Zeiten minimal
       verschoben haben, siehe Modul-Docstring).

    Reicht die Datei nicht mehr / fehlt sie, bleibt der Request bewusst
    OFFEN (mit einer Warnung statt einer stillen automatischen Neu-Suche —
    die eigentliche Suche bleibt ein expliziter Nutzerklick, siehe
    Auto-Resolve-Service einer späteren Phase)."""
    if prior_document is None:
        return new_document

    prior_by_cut_item_id = {
        request.cut_item_id: request
        for request in prior_document.requests
        if request.accepted_asset_id and request.accepted_asset_path
    }
    if not prior_by_cut_item_id:
        return new_document

    merged_requests: list[CutPlanResidualGapRequest] = []
    for request in new_document.requests:
        prior = prior_by_cut_item_id.get(request.cut_item_id)
        if prior is None:
            merged_requests.append(request)
            continue

        if not Path(prior.accepted_asset_path).is_file():
            merged_requests.append(
                request.model_copy(update={"warnings": ["CACHED_ASSET_MISSING"]})
            )
            continue

        current_signature = cache_signature_for_residual_gap_request(
            request.cut_item_id, request.gap_start_sec, request.gap_end_sec, request.repair_mode
        )
        exact_match = prior.accepted_for_cache_signature == current_signature
        still_fits = exact_match or _cached_asset_still_fits(prior.accepted_asset_path, request.needed_duration_sec)

        if not still_fits:
            merged_requests.append(
                request.model_copy(update={"warnings": ["CACHED_ASSET_TOO_SHORT"]})
            )
            continue

        merged_requests.append(
            request.model_copy(
                update={
                    "status": prior.status,
                    "accepted_candidate_id": prior.accepted_candidate_id,
                    "accepted_asset_id": prior.accepted_asset_id,
                    "accepted_asset_path": prior.accepted_asset_path,
                    "accepted_for_cache_signature": prior.accepted_for_cache_signature or current_signature,
                    "llm_queries": prior.llm_queries,
                    "llm_query_status": prior.llm_query_status,
                    "llm_query_run_id": prior.llm_query_run_id,
                    "llm_query_error": prior.llm_query_error,
                    "auto_resolve_status": prior.auto_resolve_status,
                    "auto_resolve_attempts": prior.auto_resolve_attempts,
                }
            )
        )
    return new_document.model_copy(update={"requests": merged_requests})


def count_unapplied_accepted_residual_gap_requests(
    cut_plan: CutPlanDocument, requests_document: CutPlanResidualGapRequestsDocument
) -> int:
    """Zählt Requests, die zwar ein akzeptiertes Asset tragen, das aber im
    aktuellen Draft noch nicht als VisualSegment übernommen wurde (Apply-
    Logik folgt in einer späteren Phase — diese Zählung ist bereits jetzt
    nützlich, um in der UI/im Workflow-Dashboard sichtbar zu machen, dass
    hier noch eine Übernahme fehlt)."""
    items_by_id = {item.cut_item_id: item for item in cut_plan.items}
    count = 0
    for request in requests_document.requests:
        if not request.accepted_asset_id or not request.accepted_asset_path:
            continue
        if not Path(request.accepted_asset_path).is_file():
            continue
        item = items_by_id.get(request.cut_item_id)
        if item is None:
            continue
        already_applied = any(
            segment.asset_id == request.accepted_asset_id for segment in item.planned_visual_segments
        )
        if not already_applied:
            count += 1
    return count


def save_residual_gap_requests(project: Project, document: CutPlanResidualGapRequestsDocument) -> Path:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_cut_plan_residual_gap_requests_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_residual_gap_requests(project: Project) -> CutPlanResidualGapRequestsDocument | None:
    path = get_cut_plan_residual_gap_requests_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanResidualGapRequestsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def update_residual_gap_request(
    project: Project, request_id: str, **updates: object
) -> CutPlanResidualGapRequest | None:
    """Lädt/ändert/speichert GENAU EINEN Request per request_id — analog zu
    update_cut_plan_supplement_request/update_cut_plan_validation_repair_
    request."""
    document = load_residual_gap_requests(project)
    if document is None:
        return None
    updated_request: CutPlanResidualGapRequest | None = None
    new_requests: list[CutPlanResidualGapRequest] = []
    for request in document.requests:
        if request.request_id == request_id:
            updated_request = request.model_copy(update=updates)
            new_requests.append(updated_request)
        else:
            new_requests.append(request)
    save_residual_gap_requests(project, document.model_copy(update={"requests": new_requests}))
    return updated_request
