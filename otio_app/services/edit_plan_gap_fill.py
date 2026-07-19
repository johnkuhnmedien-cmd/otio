"""Fallback-Zuordnung für Shots ohne Asset beim manuellen Bestätigen.

Bisher blockierte ein fehlendes Asset (kein `resolved_media_path`) IMMER die
Bestätigung eines Schnittplans — auch wenn der Nutzer bewusst mit einer
Lücke leben wollte, weil kein passendes Supplement-Asset gefunden wurde.
Dieses Modul füllt solche Lücken beim manuellen Bestätigen automatisch mit
dem inhaltlich nächstbesten verfügbaren Asset aus demselben Ordner — statt
den Nutzer komplett zu blockieren.
"""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRulesDocument, TimelineItem
from otio_app.services.asset_usage import max_asset_usage_limit, usage_count_by_asset_id_from_timeline
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_coverage import score_asset_match

GAP_FILLABLE_TYPES = frozenset(
    {"video_shot", "image_shot", "generic_narration_visual", "generic_outro_visual", "image_with_background"}
)


def _best_candidate(
    item: TimelineItem,
    candidates: list[dict[str, str]],
) -> dict[str, str]:
    query_text = item.passage_text or item.motif or ""
    scored = sorted(
        candidates,
        key=lambda asset: (
            -score_asset_match(
                passage_text=query_text,
                visual_requirement=item.motif or query_text,
                description=asset.get("description") or Path(asset["path"]).stem,
            ),
            asset.get("path", ""),
        ),
    )
    return scored[0]


def fill_missing_timeline_assets(
    items: list[TimelineItem],
    *,
    folder_assets: dict[str, list[dict[str, str]]],
    rules_doc: EditPlanRulesDocument,
) -> tuple[list[TimelineItem], list[str]]:
    """Weist Shots ohne Asset das inhaltlich beste verfügbare Asset zu.

    Wird beim manuellen Bestätigen aufgerufen: Statt die Bestätigung wegen
    eines fehlenden Assets hart zu blockieren, wird — sofern der Ordner
    überhaupt Assets enthält — automatisch das nächstbeste (inhaltlich am
    besten passende) Asset gewählt. `max_asset_usage` wird dabei nach
    Möglichkeit respektiert; nur wenn wirklich kein anderer Kandidat mehr
    übrig ist, wird es trotzdem (mit deutlicher Warnung) verwendet.
    """
    notes: list[str] = []
    max_count = max_asset_usage_limit(rules_doc)
    usage = usage_count_by_asset_id_from_timeline(items)
    filled: list[TimelineItem] = []

    for item in items:
        needs_fill = (
            item.type in GAP_FILLABLE_TYPES
            and not item.resolved_media_path
            and not item.allow_black
        )
        if not needs_fill:
            filled.append(item)
            continue

        candidates = [
            asset for asset in folder_assets.get(item.folder_name, []) if asset.get("path")
        ]
        if not candidates:
            filled.append(item)
            continue

        ranked = sorted(
            candidates,
            key=lambda asset: (
                -score_asset_match(
                    passage_text=item.passage_text or item.motif or "",
                    visual_requirement=item.motif or item.passage_text or "",
                    description=asset.get("description") or Path(asset["path"]).stem,
                ),
                asset.get("path", ""),
            ),
        )

        chosen: dict[str, str] | None = None
        within_limit = True
        for asset in ranked:
            asset_id = asset.get("asset_id") or asset_id_for_path(asset["path"])
            if max_count is None or usage.get(asset_id, 0) < max_count:
                chosen = asset
                break
        if chosen is None:
            chosen = ranked[0]
            within_limit = False

        asset_id = chosen.get("asset_id") or asset_id_for_path(chosen["path"])
        usage[asset_id] = usage.get(asset_id, 0) + 1

        source_in = item.source_in_sec
        source_out = source_in + max(item.duration_sec, 0.0)
        media_duration = probe_duration_seconds(Path(chosen["path"]))
        if media_duration is not None:
            source_out = min(source_out, media_duration)

        warning = f"Kein Asset gefunden — nächstbestes Asset automatisch zugewiesen: `{Path(chosen['path']).name}`"
        if not within_limit:
            warning += " (überschreitet max_asset_usage — bitte manuell prüfen)"

        updated = item.model_copy(
            update={
                "resolved_media_path": chosen["path"],
                "original_asset_path": chosen["path"],
                "asset_id": asset_id,
                "asset_origin": chosen.get("asset_origin") or "local_original",
                "source_in_sec": source_in,
                "source_out_sec": round(source_out, 4),
                "selection_reason": "Fallback: nächstbestes verfügbares Asset (manuell bestätigt trotz Lücke).",
                "media_source_type": "local",
                "warnings": [*item.warnings, warning],
            }
        )
        filled.append(updated)
        notes.append(f"{item.timeline_item_id}: {warning}")

    return filled, notes
