"""Residual Gap Apply (Nutzervorgabe, Juli 2026): übernimmt ein bereits
heruntergeladenes/akzeptiertes Asset für GENAU EINEN Residual Gap Request
in den Cut-Plan-Draft. Löst KEINE Suche/Download aus (siehe
cut_plan_residual_gap_requests.py für die Request-Erkennung und eine
spätere Phase für die Auto-Resolve-Suche) — reine Übernahme-Logik plus
`reapply_accepted_residual_gap_assets` für den 'ohne erneute Suche
anwenden'-Button (analog zu reapply_accepted_supplements_to_cut_plan).

Zwei Reparatur-Modi (siehe CutPlanResidualGapRequest.repair_mode):

- PATCH_GAP_ONLY: das bestehende Segment/die bestehenden Segmente des
  Items bleiben UNVERÄNDERT — ein zusätzliches Patch-Segment füllt NUR
  [gap_start_sec, gap_end_sec]. Für den häufigen Fall 'Pause nach dem Satz
  ins Visual Window verlängert, aber das Segment reicht nicht so weit'.
- REPLACE_ITEM_VISUAL: ALLE bestehenden Segmente des Items werden durch
  EIN neues Segment ersetzt, das das gesamte erwartete Fenster
  [item.timeline_start_sec, visual_window_end_sec] abdeckt. Für den
  selteneren Fall 'die Lücke liegt mitten im Satz' — ein Patch mitten im
  Satz wäre redaktionell riskanter (Sprung zurück zum alten Asset direkt
  nach dem Patch) als ein vollständiger Ersatz."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL,
    CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
)
from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_asset_selector import (
    aggregate_item_level_errors,
    compute_visual_window_end_sec,
    settings_from_snapshot,
    update_asset_usage_summary,
)
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, VisualSegment
from otio_app.services.voiceover_generation.cut_plan_residual_gap_models import CutPlanResidualGapRequest
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    load_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementAsset
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import apply_visual_coverage_extensions

__all__ = [
    "apply_residual_gap_asset",
    "reapply_accepted_residual_gap_assets",
]

_DURATION_EPSILON = 0.05
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".m4v"})


def _asset_from_accepted_request(request: CutPlanResidualGapRequest) -> CutPlanSupplementAsset | None:
    if not request.accepted_asset_id or not request.accepted_asset_path:
        return None
    asset_path = Path(request.accepted_asset_path)
    if not asset_path.is_file():
        return None
    asset_type = "video" if asset_path.suffix.lower() in _VIDEO_SUFFIXES else "image"
    duration_sec = probe_duration_seconds(asset_path) or request.needed_duration_sec
    return CutPlanSupplementAsset(
        asset_id=request.accepted_asset_id,
        request_id=request.request_id,
        candidate_id=request.accepted_candidate_id or f"reapplied_{request.request_id}",
        provider="",
        asset_path=str(asset_path),
        asset_type=asset_type,
        duration_sec=duration_sec,
    )


def _overlaps_existing_segment(cut_plan: CutPlanDocument, gap_start_sec: float, gap_end_sec: float) -> bool:
    for item in cut_plan.items:
        for segment in item.planned_visual_segments:
            if segment.timeline_in_sec >= gap_end_sec - _DURATION_EPSILON:
                continue
            if segment.timeline_out_sec <= gap_start_sec + _DURATION_EPSILON:
                continue
            return True
    return False


def apply_residual_gap_asset(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanResidualGapRequest,
    accepted_asset: CutPlanSupplementAsset,
) -> CutPlanDocument:
    """Reine Funktion: aktualisiert GENAU das CutPlanItem, das zu
    request.cut_item_id gehört. Wirft ValueError, wenn das Asset zu kurz
    ist oder (bei PATCH_GAP_ONLY) das Gap-Fenster bereits durch ein
    anderes Segment belegt ist (z. B. weil der Draft sich seit der
    Request-Erzeugung geändert hat) — kein stilles Teil-Update."""
    settings = settings_from_snapshot(project, cut_plan)

    target_item = next((item for item in cut_plan.items if item.cut_item_id == request.cut_item_id), None)
    if target_item is None:
        raise ValueError(f"CutPlanItem '{request.cut_item_id}' nicht im Cut Plan gefunden.")

    if request.repair_mode == CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL:
        item_index = next(
            (index for index, item in enumerate(cut_plan.items) if item.cut_item_id == target_item.cut_item_id), None
        )
        next_item = (
            cut_plan.items[item_index + 1] if item_index is not None and item_index + 1 < len(cut_plan.items) else None
        )
        window_end_sec = compute_visual_window_end_sec(target_item, next_item, settings)
        segment_duration_sec = max(0.0, window_end_sec - target_item.timeline_start_sec)

        if accepted_asset.asset_type == "video":
            source_in_sec = settings.video_head_trim_sec
            usable_duration_sec = max(0.0, accepted_asset.duration_sec - settings.video_head_trim_sec)
            if target_item.duration_sec > usable_duration_sec + _DURATION_EPSILON:
                raise ValueError(
                    f"Ersatz-Asset zu kurz für Item '{target_item.cut_item_id}': benötigt mindestens "
                    f"{target_item.duration_sec:.2f}s (Satzdauer), verfügbar {usable_duration_sec:.2f}s "
                    f"nach video_head_trim_sec ({settings.video_head_trim_sec:.2f}s)."
                )
            segment_duration_sec = min(segment_duration_sec, usable_duration_sec)
            window_end_sec = target_item.timeline_start_sec + segment_duration_sec
            source_out_sec = source_in_sec + segment_duration_sec
        else:
            source_in_sec = 0.0
            source_out_sec = segment_duration_sec

        new_segment = VisualSegment(
            segment_id=f"{target_item.cut_item_id}_residual_replace",
            timeline_in_sec=target_item.timeline_start_sec,
            timeline_out_sec=window_end_sec,
            duration_sec=segment_duration_sec,
            asset_id=accepted_asset.asset_id,
            asset_path=accepted_asset.asset_path,
            asset_type=accepted_asset.asset_type,
            source_in_sec=source_in_sec,
            source_out_sec=source_out_sec,
            track="V1",
            reason="residual_gap_replace",
        )
        updated_item = target_item.model_copy(
            update={
                "chosen_asset_id": accepted_asset.asset_id,
                "asset_selection_status": CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
                "asset_selection_reason": (
                    f"Residual Gap Repair: vollständiger Ersatz durch '{accepted_asset.asset_id}' "
                    f"(Fenster reichte vorher nur bis {request.gap_start_sec:.2f}s)."
                ),
                "planned_visual_segments": [new_segment],
            }
        )
    else:  # PATCH_GAP_ONLY
        gap_start_sec, gap_end_sec = request.gap_start_sec, request.gap_end_sec
        segment_duration_sec = max(0.0, gap_end_sec - gap_start_sec)
        if _overlaps_existing_segment(cut_plan, gap_start_sec, gap_end_sec):
            raise ValueError(
                f"Reparatur-Fenster {gap_start_sec:.2f}s–{gap_end_sec:.2f}s für Item "
                f"'{target_item.cut_item_id}' ist bereits durch ein VisualSegment belegt — bitte Draft "
                "erneut validieren und diesen Request neu erzeugen."
            )
        if accepted_asset.asset_type == "video":
            source_in_sec = settings.video_head_trim_sec
            usable_duration_sec = max(0.0, accepted_asset.duration_sec - settings.video_head_trim_sec)
            if segment_duration_sec > usable_duration_sec + _DURATION_EPSILON:
                raise ValueError(
                    f"Patch-Asset zu kurz für Rest-Lücke von Item '{target_item.cut_item_id}': benötigt "
                    f"{segment_duration_sec:.2f}s, verfügbar {usable_duration_sec:.2f}s nach "
                    f"video_head_trim_sec ({settings.video_head_trim_sec:.2f}s)."
                )
            source_out_sec = source_in_sec + segment_duration_sec
        else:
            source_in_sec = 0.0
            source_out_sec = segment_duration_sec

        patch_segment = VisualSegment(
            segment_id=f"{target_item.cut_item_id}_residual_patch",
            timeline_in_sec=gap_start_sec,
            timeline_out_sec=gap_end_sec,
            duration_sec=segment_duration_sec,
            asset_id=accepted_asset.asset_id,
            asset_path=accepted_asset.asset_path,
            asset_type=accepted_asset.asset_type,
            source_in_sec=source_in_sec,
            source_out_sec=source_out_sec,
            track="V1",
            reason="residual_gap_patch",
        )
        merged_segments = sorted(
            [*target_item.planned_visual_segments, patch_segment], key=lambda segment: segment.timeline_in_sec
        )
        updated_item = target_item.model_copy(update={"planned_visual_segments": merged_segments})

    remaining_blockers = [
        blocker for blocker in updated_item.blockers if blocker not in CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES
    ]
    remaining_warnings = [
        warning for warning in updated_item.warnings if warning not in CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES
    ]
    updated_item = updated_item.model_copy(update={"blockers": remaining_blockers, "warnings": remaining_warnings})

    updated_items = [
        updated_item if item.cut_item_id == target_item.cut_item_id else item for item in cut_plan.items
    ]
    updated_cut_plan = cut_plan.model_copy(update={"items": updated_items})
    updated_cut_plan = apply_visual_coverage_extensions(updated_cut_plan, settings)

    asset_usage_summary = update_asset_usage_summary(updated_cut_plan)
    warnings, blockers = aggregate_item_level_errors(updated_cut_plan.items)

    return updated_cut_plan.model_copy(
        update={
            "asset_usage_summary": asset_usage_summary,
            "warnings": warnings,
            "blockers": blockers,
            "status": CUT_PLAN_STATUS_NEEDS_REVIEW,
        }
    )


def reapply_accepted_residual_gap_assets(project: Project) -> tuple[CutPlanDocument, list[str], list[str]]:
    """Übernimmt alle bereits akzeptierten Residual-Gap-Assets aus
    `residual_gap_requests.json` erneut in den aktuellen Cut-Plan-Draft —
    OHNE externe Suche/Download (analog zu reapply_accepted_supplements_
    to_cut_plan). Gibt `(updated_cut_plan, applied_cut_item_ids,
    skipped_cut_item_ids)` zurück und speichert den Draft, wenn mindestens
    ein Request angewendet wurde."""
    requests_document = load_residual_gap_requests(project)
    if requests_document is None:
        raise ValueError("Keine Residual Gap Requests vorhanden.")
    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")

    applied: list[str] = []
    skipped: list[str] = []
    updated_cut_plan = draft
    for request in requests_document.requests:
        accepted_asset = _asset_from_accepted_request(request)
        if accepted_asset is None:
            continue
        item = next((entry for entry in updated_cut_plan.items if entry.cut_item_id == request.cut_item_id), None)
        if item is None:
            skipped.append(request.cut_item_id)
            continue
        already_applied = any(
            segment.asset_id == accepted_asset.asset_id for segment in item.planned_visual_segments
        )
        if already_applied:
            continue
        try:
            updated_cut_plan = apply_residual_gap_asset(project, updated_cut_plan, request, accepted_asset)
            applied.append(request.cut_item_id)
        except ValueError:
            skipped.append(request.cut_item_id)

    if applied:
        save_cut_plan_draft(project, updated_cut_plan)
    return updated_cut_plan, applied, skipped
