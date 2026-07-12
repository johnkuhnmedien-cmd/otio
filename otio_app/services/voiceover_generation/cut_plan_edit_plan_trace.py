"""Phase 9.1/9.2: Trace für die isolierte EditPlan-Bridge.

Zeigt für JEDES TimelineItem im Bridge-Draft, aus welchem CutPlan-Element es
entstand, und macht alle DREI Stufen der Zeit-Transformation nachvollziehbar:

    CutPlan-Zeit (original)
    -> Frame-normalisierte Zeit (rounded, vor Boundary-Chaining)
    -> finale, boundary-gechainte Zeit (timeline_in/out_sec == TimelineItem)

Liest ausschließlich bereits vorhandene Cut-Plan-/Bridge-Draft-Daten — keine
eigene Übersetzung, keine Validierung, kein Netzwerkzugriff. Die 'rounded'-
Stufe wird mit denselben reinen Funktionen wie beim Bau des Bridge-Drafts
neu berechnet; die 'final' Stufe wird direkt aus dem bereits gebauten
edit_plan gelesen (Boundary-Chaining wird hier NICHT erneut durchgeführt),
damit keine Abweichung zwischen Trace und tatsächlichem Draft entstehen
kann."""

from __future__ import annotations

import json

from otio_app.analysis_models import EditPlanDocument, TimelineItem
from otio_app.defaults import AUDIO_SCOPE_INTRO, EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_edit_plan_bridge_trace_path
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    round_audio_times_to_frame,
    round_visual_times_to_frame,
    safe_timeline_item_component,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    EditPlanBridgeTraceDocument,
    EditPlanBridgeTraceEntry,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = ["build_edit_plan_bridge_trace", "save_edit_plan_bridge_trace", "load_edit_plan_bridge_trace"]

_EPSILON = 1e-6


def _audio_timeline_item_id(scope: str, folder_name: str) -> str:
    scope_label = scope or AUDIO_SCOPE_INTRO
    folder_component = safe_timeline_item_component(folder_name) if folder_name else "intro"
    return f"edit_audio_{scope_label}_{folder_component}"


def _final_values(
    final_item: TimelineItem | None,
    fallback_timeline_in: float,
    fallback_timeline_out: float,
    fallback_source_in: float,
    fallback_source_out: float,
) -> tuple[float, float, float, float]:
    if final_item is None:
        return fallback_timeline_in, fallback_timeline_out, fallback_source_in, fallback_source_out
    return (
        final_item.timeline_in_sec,
        final_item.timeline_out_sec,
        final_item.source_in_sec,
        final_item.source_out_sec,
    )


def build_edit_plan_bridge_trace(
    project: Project, cut_plan: CutPlanDocument, edit_plan: EditPlanDocument
) -> EditPlanBridgeTraceDocument:
    """Reine Funktion — baut EINEN EditPlanBridgeTraceEntry je Audio-Item UND
    je VisualSegment aus dem bereits vorhandenen Cut Plan. Die 'rounded'-
    Stufe wird mit denselben reinen Frame-Rundungsfunktionen wie beim Bau des
    Bridge-Drafts neu berechnet; die finalen (boundary-gechainten) Werte
    werden direkt aus edit_plan.timeline_items übernommen. Speichert nichts
    (siehe save_edit_plan_bridge_trace)."""
    fps = cut_plan.timeline_fps or 25
    entries: list[EditPlanBridgeTraceEntry] = []
    final_items_by_id = {item.timeline_item_id: item for item in edit_plan.timeline_items}

    for audio_item in cut_plan.audio_items:
        original_in, original_out = audio_item.timeline_start_sec, audio_item.timeline_end_sec
        rounded_in, rounded_out, rounded_source_in, rounded_source_out, frame_rounded, delta = (
            round_audio_times_to_frame(original_in, original_out, audio_item.duration_sec, fps)
        )
        timeline_item_id = _audio_timeline_item_id(audio_item.scope, audio_item.folder_name)
        final_item = final_items_by_id.get(timeline_item_id)
        final_in, final_out, final_source_in, final_source_out = _final_values(
            final_item, rounded_in, rounded_out, rounded_source_in, rounded_source_out
        )

        boundary_chained = (
            abs(final_in - rounded_in) > _EPSILON or abs(final_out - rounded_out) > _EPSILON
        )
        boundary_chain_delta = (final_out - final_in) - (rounded_out - rounded_in)
        source_duration_adjusted = (
            abs(final_source_in - rounded_source_in) > _EPSILON
            or abs(final_source_out - rounded_source_out) > _EPSILON
        )
        source_duration_delta = (final_source_out - final_source_in) - (rounded_source_out - rounded_source_in)

        entries.append(
            EditPlanBridgeTraceEntry(
                trace_id=f"trace_{timeline_item_id}",
                source_scope=audio_item.scope or AUDIO_SCOPE_INTRO,
                folder_name=audio_item.folder_name,
                timeline_item_id=timeline_item_id,
                timeline_item_type=EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
                track=audio_item.track or "A1",
                asset_path=audio_item.audio_path,
                timeline_in_sec=final_in,
                timeline_out_sec=final_out,
                source_in_sec=final_source_in,
                source_out_sec=final_source_out,
                frame_rounded=frame_rounded,
                frame_rounding_delta_sec=delta,
                reason="voiceover_audio",
                original_timeline_in_sec=original_in,
                original_timeline_out_sec=original_out,
                rounded_timeline_in_sec=rounded_in,
                rounded_timeline_out_sec=rounded_out,
                boundary_chained=boundary_chained,
                boundary_chain_delta_sec=boundary_chain_delta,
                source_duration_adjusted=source_duration_adjusted,
                source_duration_delta_sec=source_duration_delta,
            )
        )

    for item in cut_plan.items:
        source_sentence_id = ""
        source_hook_beat_id = ""
        for source_ref in item.source_refs:
            source_sentence_id = source_sentence_id or source_ref.source_sentence_id
            source_hook_beat_id = source_hook_beat_id or source_ref.source_hook_beat_id

        for segment in item.planned_visual_segments:
            original_in, original_out = segment.timeline_in_sec, segment.timeline_out_sec
            rounded_in, rounded_out, rounded_source_in, rounded_source_out, frame_rounded, delta = (
                round_visual_times_to_frame(
                    original_in, original_out, segment.source_in_sec, segment.source_out_sec, fps
                )
            )
            timeline_item_id = f"edit_{segment.segment_id}"
            timeline_item_type = "video_shot" if segment.asset_type == "video" else "image_shot"
            final_item = final_items_by_id.get(timeline_item_id)
            final_in, final_out, final_source_in, final_source_out = _final_values(
                final_item, rounded_in, rounded_out, rounded_source_in, rounded_source_out
            )

            boundary_chained = (
                abs(final_in - rounded_in) > _EPSILON or abs(final_out - rounded_out) > _EPSILON
            )
            boundary_chain_delta = (final_out - final_in) - (rounded_out - rounded_in)
            source_duration_adjusted = (
                abs(final_source_in - rounded_source_in) > _EPSILON
                or abs(final_source_out - rounded_source_out) > _EPSILON
            )
            source_duration_delta = (final_source_out - final_source_in) - (rounded_source_out - rounded_source_in)

            entries.append(
                EditPlanBridgeTraceEntry(
                    trace_id=f"trace_{timeline_item_id}",
                    cut_item_id=item.cut_item_id,
                    visual_segment_id=segment.segment_id,
                    source_scope=item.source_scope,
                    folder_name=item.folder_name,
                    source_sentence_id=source_sentence_id,
                    source_hook_beat_id=source_hook_beat_id,
                    timeline_item_id=timeline_item_id,
                    timeline_item_type=timeline_item_type,
                    track=segment.track or "V1",
                    asset_id=segment.asset_id,
                    asset_path=segment.asset_path,
                    timeline_in_sec=final_in,
                    timeline_out_sec=final_out,
                    source_in_sec=final_source_in,
                    source_out_sec=final_source_out,
                    frame_rounded=frame_rounded,
                    frame_rounding_delta_sec=delta,
                    reason=segment.reason,
                    warnings=list(item.warnings),
                    original_chosen_asset_id=item.chosen_asset_id,
                    duration_strategy=item.duration_strategy,
                    original_timeline_in_sec=original_in,
                    original_timeline_out_sec=original_out,
                    rounded_timeline_in_sec=rounded_in,
                    rounded_timeline_out_sec=rounded_out,
                    boundary_chained=boundary_chained,
                    boundary_chain_delta_sec=boundary_chain_delta,
                    source_duration_adjusted=source_duration_adjusted,
                    source_duration_delta_sec=source_duration_delta,
                )
            )

    return EditPlanBridgeTraceDocument(
        project_id=project.id,
        source_cut_plan_hash=content_hash_of_model(cut_plan),
        edit_plan_hash=content_hash_of_model(edit_plan),
        entries=entries,
    )


def save_edit_plan_bridge_trace(project: Project, trace: EditPlanBridgeTraceDocument) -> None:
    normalized = trace.model_copy(update={"project_id": project.id})
    path = get_cut_plan_edit_plan_bridge_trace_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")


def load_edit_plan_bridge_trace(project: Project) -> EditPlanBridgeTraceDocument | None:
    path = get_cut_plan_edit_plan_bridge_trace_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanBridgeTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
