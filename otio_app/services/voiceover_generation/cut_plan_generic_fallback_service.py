"""Phase 11.4: generischer Ordner-Fallback für den Cut-Plan-Auto-Resolver.

Wenn Phase 11.3 (Auto-Resolver) für einen Supplement Request keinen Stock-
Kandidaten findet, der die Gemini-Prüfung besteht, wählt dieses Modul ein
bereits vorhandenes, NEUTRALES Asset aus demselben Ordner-Inventory
(dieselbe Auswahl-Logik, die die Produktions-Pipeline für generische Outro-
/Filler-Shots nutzt, siehe `generic_outro_selector.py`) und weist es dem
betroffenen CutPlanItem zu — KEIN Download, KEIN externer Provider-Aufruf,
KEINE Gemini-Prüfung nötig (das Material ist bereits im eigenen Inventory
und damit implizit redaktionell freigegeben).

Läuft ausschließlich bei explizitem Aufruf (aus dem Auto-Resolver, wenn
kein Stock-Kandidat PASS erreicht, ODER über einen eigenen manuellen
Button) — niemals automatisch beim Draft-Bau oder bei der Validierung.
Schreibt ausschließlich unter `_otio/voiceover_generation/cut_plan/` —
niemals in reguläre Folder-Inventories oder `_otio/supplement/`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_GENERIC_FALLBACK_USED,
    CUT_PLAN_ASSET_SELECTION_MANUAL_USED,
    CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
)
from otio_app.models import Project
from otio_app.services.generic_outro_selector import (
    GenericAssetCandidate,
    asset_id_for_path,
    select_generic_outro_assets,
)
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_asset_selector import (
    aggregate_item_level_errors,
    settings_from_snapshot,
    update_asset_usage_summary,
)
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanItem, VisualSegment
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    capture_pre_accept_item_snapshot_if_missing,
    load_cut_plan_supplement_requests,
    update_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementRequest
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import apply_visual_coverage_extensions

__all__ = [
    "GENERIC_FALLBACK_CANDIDATE_POOL_SIZE",
    "select_generic_fallback_candidate",
    "apply_generic_fallback_to_cut_plan_item",
    "apply_generic_fallback_for_cut_plan_request",
    "list_manual_asset_options_for_request",
    "list_manual_asset_options_for_cut_item",
    "apply_manual_asset_to_cut_plan_item",
    "apply_manual_asset_for_cut_plan_request",
    "apply_manual_asset_for_cut_item",
]

_DURATION_EPSILON = 0.05

# Mehrere Kandidaten anfordern statt nur 1 — select_generic_outro_assets
# rankt zwar bereits nach Dauer/Eignung, aber nur mit einem Score-Malus
# (kein hartes Ausschlusskriterium). Der harte Dauer-Check unten wählt aus
# diesem Pool den ersten, der die benötigte Dauer TATSÄCHLICH erfüllt.
GENERIC_FALLBACK_CANDIDATE_POOL_SIZE = 5


def select_generic_fallback_candidate(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanSupplementRequest,
    *,
    needed_duration_sec: float,
) -> GenericAssetCandidate | None:
    """Reine Funktion (kein I/O außer lesendem Inventory-Zugriff + ffprobe):
    wählt aus dem Ordner-Inventory von request.folder_name einen neutralen
    Kandidaten, der (a) noch nicht in diesem Cut Plan verwendet wird bzw.
    die max_asset_usage-Grenze nicht überschreitet, und (b) lang genug für
    needed_duration_sec ist (harter Dauer-Check — Bilder gelten als
    beliebig lang haltbar, wie überall sonst in dieser Pipeline).
    Liefert None, wenn der Ordner kein passendes Asset hat."""
    if not request.folder_name:
        return None

    settings = settings_from_snapshot(project, cut_plan)
    inventory = load_folder_inventory(project, request.folder_name)
    folder_assets = [
        {"path": str(project.project_root_path / asset.path), "description": asset.description}
        for asset in inventory.assets
        if asset.path
    ]
    if not folder_assets:
        return None

    used_paths = {
        segment.asset_path
        for item in cut_plan.items
        for segment in item.planned_visual_segments
        if segment.asset_path
    }

    candidates = select_generic_outro_assets(
        folder_assets,
        used_paths=used_paths,
        last_asset_path=None,
        count=GENERIC_FALLBACK_CANDIDATE_POOL_SIZE,
        min_duration_sec=needed_duration_sec,
        usage_by_asset_id=dict(cut_plan.asset_usage_summary),
        max_asset_usage=settings.max_asset_usage,
    )

    for candidate in candidates:
        local_path = Path(candidate.path)
        if not local_path.is_file():
            continue
        if is_image_media(local_path):
            return candidate  # Bilder gelten als beliebig lang haltbar (wie in cut_plan_asset_selector.py)
        duration = probe_duration_seconds(local_path)
        usable_duration_sec = max(0.0, (duration or 0.0) - settings.video_head_trim_sec)
        if needed_duration_sec <= usable_duration_sec + _DURATION_EPSILON:
            return candidate
    return None


def _build_local_asset_segment_and_updated_item(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanSupplementRequest,
    *,
    asset_id: str,
    asset_path: str,
    asset_selection_status: str,
    asset_selection_reason: str,
    fallback_reason: str,
    segment_reason: str,
) -> CutPlanItem:
    """Gemeinsamer Kern für generischen Fallback UND manuelle Zuweisung:
    baut ein VisualSegment aus einem bereits lokal vorhandenen Asset (kein
    Download) und liefert das aktualisierte CutPlanItem. Der harte Dauer-
    Check (Video zu kurz -> ValueError) gilt für beide Wege identisch."""
    settings = settings_from_snapshot(project, cut_plan)

    target_item = next((item for item in cut_plan.items if item.cut_item_id == request.cut_item_id), None)
    if target_item is None:
        raise ValueError(f"CutPlanItem '{request.cut_item_id}' nicht im Cut Plan gefunden.")

    local_path = Path(asset_path)
    is_image = is_image_media(local_path)
    if is_image:
        source_in_sec = 0.0
        source_out_sec = target_item.duration_sec
        asset_type = "image"
    else:
        source_in_sec = settings.video_head_trim_sec
        usable_duration_sec = max(0.0, (probe_duration_seconds(local_path) or 0.0) - settings.video_head_trim_sec)
        if target_item.duration_sec > usable_duration_sec + _DURATION_EPSILON:
            raise ValueError(
                f"Asset zu kurz für Item '{target_item.cut_item_id}': benötigt "
                f"{target_item.duration_sec:.2f}s, verfügbar {usable_duration_sec:.2f}s nach "
                f"video_head_trim_sec ({settings.video_head_trim_sec:.2f}s)."
            )
        source_out_sec = source_in_sec + target_item.duration_sec
        asset_type = "video"

    segment = VisualSegment(
        segment_id=f"{target_item.cut_item_id}_seg_01",
        timeline_in_sec=target_item.timeline_start_sec,
        timeline_out_sec=target_item.timeline_end_sec,
        duration_sec=target_item.duration_sec,
        asset_id=asset_id,
        asset_path=asset_path,
        asset_type=asset_type,
        source_in_sec=source_in_sec,
        source_out_sec=source_out_sec,
        track="V1",
        reason=segment_reason,
    )

    # Veraltete Validierungs-Blocker/Warnings (BLACK_GAP, ASSET_TOO_SHORT, …)
    # müssen weg — sonst meldet „Cut Plan validieren“ sie wieder als
    # „aus Asset-Auswahl übernommen“, obwohl das Asset gerade korrekt gesetzt wurde.
    remaining_blockers = [
        blocker for blocker in target_item.blockers if blocker not in CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES
    ]
    remaining_warnings = [
        warning for warning in target_item.warnings if warning not in CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES
    ]

    return target_item.model_copy(
        update={
            "chosen_asset_id": asset_id,
            "asset_selection_status": asset_selection_status,
            "asset_selection_reason": asset_selection_reason,
            "fallback_reason": fallback_reason,
            "supplement_request_id": request.request_id,
            "needs_supplement_asset": False,
            "planned_visual_segments": [segment],
            "blockers": remaining_blockers,
            "warnings": remaining_warnings,
        }
    )


def _finalize_local_asset_cut_plan_update(
    project: Project, cut_plan: CutPlanDocument, updated_item: CutPlanItem
) -> CutPlanDocument:
    settings = settings_from_snapshot(project, cut_plan)
    updated_items = [
        updated_item if item.cut_item_id == updated_item.cut_item_id else item for item in cut_plan.items
    ]
    updated_cut_plan = cut_plan.model_copy(update={"items": updated_items})

    # Visual Coverage (Phase 8.5) erneut anwenden — analog zum Supplement-
    # Accept-Pfad, damit dasselbe Vorlauf-/Pausen-Verhalten gilt.
    updated_cut_plan = apply_visual_coverage_extensions(updated_cut_plan, settings)

    asset_usage_summary = update_asset_usage_summary(updated_cut_plan)
    warnings, blockers = aggregate_item_level_errors(updated_cut_plan.items)
    status = CUT_PLAN_STATUS_NEEDS_REVIEW if blockers else CUT_PLAN_STATUS_DRAFT

    return updated_cut_plan.model_copy(
        update={
            "asset_usage_summary": asset_usage_summary,
            "warnings": warnings,
            "blockers": blockers,
            "status": status,
        }
    )


def apply_generic_fallback_to_cut_plan_item(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanSupplementRequest,
    candidate: GenericAssetCandidate,
) -> CutPlanDocument:
    """Reine Funktion: aktualisiert GENAU das CutPlanItem, das zu
    request.cut_item_id gehört, mit dem übergebenen generischen Ordner-
    Asset. Analog zu apply_accepted_supplement_to_cut_plan_item (Phase
    8.6), aber ohne CutPlanSupplementAsset (kein Download nötig — das
    Material liegt bereits lokal vor)."""
    updated_item = _build_local_asset_segment_and_updated_item(
        project,
        cut_plan,
        request,
        asset_id=candidate.asset_id,
        asset_path=candidate.path,
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_GENERIC_FALLBACK_USED,
        asset_selection_reason=(
            f"Kein Stock-Kandidat hat die automatische Prüfung bestanden — generisches "
            f"Ordner-Asset '{candidate.asset_id}' verwendet ({candidate.selection_reason})."
        ),
        fallback_reason="Generic fallback asset used because no supplement candidate passed validation.",
        segment_reason="generic_fallback_asset",
    )
    return _finalize_local_asset_cut_plan_update(project, cut_plan, updated_item)


def apply_generic_fallback_for_cut_plan_request(
    project: Project, request_id: str, force_replace: bool = False
) -> tuple[CutPlanDocument | None, GenericAssetCandidate | None]:
    """I/O-Orchestrator für GENAU EINEN Request: lädt Request/Draft, wählt
    (rein lesend) einen generischen Ordner-Kandidaten und übernimmt ihn bei
    Erfolg in den Cut Plan. Liefert (None, None), wenn kein passendes Asset
    gefunden wurde — wirft KEINE Exception, damit ein Auto-Resolver-Batch
    (spätere Phase) nicht an einem einzigen Request abbricht.

    Phase 11.6: wie bei accept_cut_plan_supplement_candidate wird VOR jeder
    Übernahme ein Snapshot des Vorzustands gesichert (nur beim ersten Mal)
    und ein bereits versorgter Request (accepted_asset_id gesetzt) wird
    ohne force_replace=True mit ValueError abgelehnt, statt still
    überschrieben zu werden.

    Aktualisiert den Request-Status auf ACCEPTED und setzt
    ``auto_resolve_status`` auf GENERIC_FALLBACK_USED, damit in der UI
    klar unterscheidbar bleibt, ob ein Stock-Kandidat oder ein vorhandenes
    Ordner-Asset verwendet wurde."""
    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is None:
        raise ValueError("Keine Supplement Requests vorhanden.")
    request = next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")
    if request.accepted_asset_id and not force_replace:
        raise ValueError("Supplement request already has an accepted asset. Use replace explicitly.")

    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")

    target_item = next((item for item in draft.items if item.cut_item_id == request.cut_item_id), None)
    needed_duration_sec = target_item.duration_sec if target_item is not None else request.needed_duration_sec

    candidate = select_generic_fallback_candidate(
        project, draft, request, needed_duration_sec=needed_duration_sec
    )
    if candidate is None:
        return None, None

    capture_pre_accept_item_snapshot_if_missing(project, request_id, target_item)

    updated_cut_plan = apply_generic_fallback_to_cut_plan_item(project, draft, request, candidate)
    save_cut_plan_draft(project, updated_cut_plan)
    update_cut_plan_supplement_request(
        project,
        request_id,
        status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
        accepted_asset_id=candidate.asset_id,
        accepted_asset_path=candidate.path,
    )
    return updated_cut_plan, candidate


# --- Manuelle Zuweisung (Phase 11.6) ---


@dataclass(frozen=True)
class ManualAssetOption:
    """Ein Eintrag für die manuelle Asset-Auswahl in der UI — rein
    informativ, kein redaktionelles Feld."""

    asset_id: str
    path: str
    description: str
    media_type: str  # "video" | "image" | ""
    duration_sec: float
    likely_usable: bool


def list_manual_asset_options_for_request(
    project: Project, request: CutPlanSupplementRequest, *, needed_duration_sec: float
) -> list[ManualAssetOption]:
    """Reine Funktion: listet alle Assets aus dem Ordner-Inventory von
    request.folder_name auf, für die manuelle Auswahl in der UI.
    likely_usable ist nur ein Hinweis (Bilder immer True, Videos anhand der
    rohen Dauer ohne video_head_trim_sec) — die tatsächliche, verbindliche
    Prüfung passiert erst in apply_manual_asset_to_cut_plan_item."""
    if not request.folder_name:
        return []
    inventory = load_folder_inventory(project, request.folder_name)
    options: list[ManualAssetOption] = []
    for asset in inventory.assets:
        if not asset.path:
            continue
        full_path = project.project_root_path / asset.path
        asset_id = asset.asset_id or asset_id_for_path(asset.path)
        is_image = is_image_media(full_path)
        duration_sec = 0.0 if is_image else (probe_duration_seconds(full_path) or 0.0)
        options.append(
            ManualAssetOption(
                asset_id=asset_id,
                path=str(full_path),
                description=asset.description,
                media_type="image" if is_image else "video",
                duration_sec=duration_sec,
                likely_usable=is_image or duration_sec >= needed_duration_sec,
            )
        )
    return options


def apply_manual_asset_to_cut_plan_item(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanSupplementRequest,
    *,
    asset_id: str,
    asset_path: str,
) -> CutPlanDocument:
    """Reine Funktion: weist GENAU dem CutPlanItem von request.cut_item_id
    ein vom Nutzer bewusst ausgewähltes, bereits vorhandenes Asset zu.
    Derselbe harte Dauer-Check wie beim generischen Fallback (kein
    stillschweigend zu kurzes Segment)."""
    updated_item = _build_local_asset_segment_and_updated_item(
        project,
        cut_plan,
        request,
        asset_id=asset_id,
        asset_path=asset_path,
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_MANUAL_USED,
        asset_selection_reason=f"Nutzer hat manuell das vorhandene Asset '{asset_id}' zugewiesen.",
        fallback_reason="Manually assigned asset from folder inventory.",
        segment_reason="manual_asset",
    )
    return _finalize_local_asset_cut_plan_update(project, cut_plan, updated_item)


def apply_manual_asset_for_cut_plan_request(
    project: Project,
    request_id: str,
    *,
    asset_id: str,
    asset_path: str,
    force_replace: bool = False,
) -> CutPlanDocument:
    """I/O-Orchestrator für die manuelle Zuweisung — lädt Request/Draft,
    sichert (nur beim ersten Mal) einen Vorzustand-Snapshot, wendet die
    Zuweisung an und speichert Draft + Request. Lehnt (ohne
    force_replace=True) ab, wenn der Request bereits ein übernommenes
    Asset hat — identische Absicherung wie accept_cut_plan_supplement_
    candidate / apply_generic_fallback_for_cut_plan_request."""
    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is None:
        raise ValueError("Keine Supplement Requests vorhanden.")
    request = next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")
    if request.accepted_asset_id and not force_replace:
        raise ValueError("Supplement request already has an accepted asset. Use replace explicitly.")

    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")

    target_item = next((item for item in draft.items if item.cut_item_id == request.cut_item_id), None)
    capture_pre_accept_item_snapshot_if_missing(project, request_id, target_item)

    updated_cut_plan = apply_manual_asset_to_cut_plan_item(
        project, draft, request, asset_id=asset_id, asset_path=asset_path
    )
    save_cut_plan_draft(project, updated_cut_plan)
    update_cut_plan_supplement_request(
        project,
        request_id,
        status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
        accepted_asset_id=asset_id,
        accepted_asset_path=asset_path,
    )
    return updated_cut_plan


def _transient_request_for_cut_item(
    cut_plan: CutPlanDocument, cut_item_id: str, *, needed_duration_sec: float | None = None
) -> tuple[CutPlanItem, CutPlanSupplementRequest]:
    target_item = next((item for item in cut_plan.items if item.cut_item_id == cut_item_id), None)
    if target_item is None:
        raise ValueError(f"CutPlanItem '{cut_item_id}' nicht im Cut Plan gefunden.")
    duration = (
        float(needed_duration_sec)
        if needed_duration_sec is not None
        else float(target_item.duration_sec)
    )
    request = CutPlanSupplementRequest(
        request_id=f"manual_replace_{cut_item_id}",
        cut_item_id=cut_item_id,
        source_scope=target_item.source_scope,
        folder_name=target_item.folder_name,
        text=target_item.text,
        visual_intent=target_item.visual_intent,
        needed_duration_sec=duration,
        reason="Manueller Asset-Tausch für Black-Gap-/Closing-Repair.",
        supplement_search_hint=target_item.supplement_search_hint,
    )
    return target_item, request


def list_manual_asset_options_for_cut_item(
    project: Project,
    cut_plan: CutPlanDocument,
    cut_item_id: str,
    *,
    needed_duration_sec: float | None = None,
) -> list[ManualAssetOption]:
    """Manuelle Asset-Auswahl für ein Cut-Plan-Item — ohne bestehenden
    Supplement Request (z. B. Closing-Shot bei Sektionspausen-Black-Gap)."""
    _item, request = _transient_request_for_cut_item(
        cut_plan, cut_item_id, needed_duration_sec=needed_duration_sec
    )
    return list_manual_asset_options_for_request(
        project, request, needed_duration_sec=request.needed_duration_sec
    )


def apply_manual_asset_for_cut_item(
    project: Project,
    cut_item_id: str,
    *,
    asset_id: str,
    asset_path: str,
    needed_duration_sec: float | None = None,
) -> CutPlanDocument:
    """Weist einem Cut-Plan-Item direkt ein Ordner-Asset zu, speichert den
    Draft und wendet Visual-Coverage erneut an — für Black-Gap-Repair an
    Closing Shots / Sätzen ohne offenen Supplement Request."""
    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")
    _item, request = _transient_request_for_cut_item(
        draft, cut_item_id, needed_duration_sec=needed_duration_sec
    )
    updated = apply_manual_asset_to_cut_plan_item(
        project, draft, request, asset_id=asset_id, asset_path=asset_path
    )
    save_cut_plan_draft(project, updated)
    return updated
