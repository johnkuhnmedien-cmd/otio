"""Phase 8.7: Cut-Plan-Trace — Nachvollziehbarkeit pro CutPlanItem.

Beantwortet später (z. B. für Review oder Debugging): "confirmed_voiceover_
project_plan wollte Asset A, Cut Plan verwendet Asset B, Grund: Backup/
Supplement/Usage/Split/Merge." Liest ausschließlich den bereits vorhandenen
Cut Plan (Draft oder Confirmed) — keine eigene Asset-Auswahl, keine
Validierung, kein Netzwerkzugriff, kein EditPlanDocument, kein OTIO-Export."""

from __future__ import annotations

import json

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_trace_path
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanTraceDocument,
    CutPlanTraceEntry,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = ["build_cut_plan_trace", "save_cut_plan_trace", "load_cut_plan_trace"]


def build_cut_plan_trace(project: Project, cut_plan: CutPlanDocument) -> CutPlanTraceDocument:
    """Reine Funktion — baut EINEN CutPlanTraceEntry je CutPlanItem aus dem
    bereits vorhandenen Cut Plan. Speichert nichts (siehe save_cut_plan_trace)."""
    entries: list[CutPlanTraceEntry] = []

    for item in cut_plan.items:
        reason_markers: set[str] = set()
        for segment in item.planned_visual_segments:
            reason_markers.update(part for part in segment.reason.split("+") if part)

        entries.append(
            CutPlanTraceEntry(
                trace_id=f"trace_{item.cut_item_id}",
                cut_item_id=item.cut_item_id,
                source_refs=list(item.source_refs),
                original_primary_asset_id=item.primary_asset_id,
                original_backup_asset_ids=list(item.backup_asset_ids),
                chosen_asset_id=item.chosen_asset_id,
                choice_reason=item.asset_selection_reason or item.fallback_reason,
                fallback_used=item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
                supplement_request_id=item.supplement_request_id,
                duration_strategy=item.duration_strategy,
                split_or_merge_decision=item.duration_strategy,
                timeline_start_sec=item.timeline_start_sec,
                timeline_end_sec=item.timeline_end_sec,
                validation_warnings=list(item.warnings),
                validation_blockers=list(item.blockers),
                asset_selection_status=item.asset_selection_status,
                visual_segment_ids=[segment.segment_id for segment in item.planned_visual_segments],
                visual_segment_count=len(item.planned_visual_segments),
                used_supplement_asset=item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
                fallback_reason=item.fallback_reason,
                visual_segment_reason_markers=sorted(reason_markers),
            )
        )

    return CutPlanTraceDocument(
        project_id=project.id,
        source_plan_hash=cut_plan.source_plan_hash,
        cut_plan_hash=content_hash_of_model(cut_plan),
        entries=entries,
    )


def save_cut_plan_trace(project: Project, trace: CutPlanTraceDocument) -> None:
    normalized = trace.model_copy(update={"project_id": project.id})
    path = get_cut_plan_trace_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")


def load_cut_plan_trace(project: Project) -> CutPlanTraceDocument | None:
    path = get_cut_plan_trace_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
