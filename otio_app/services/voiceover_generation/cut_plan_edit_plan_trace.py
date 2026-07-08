"""Phase 9.1: Trace für die isolierte EditPlan-Bridge.

Zeigt für JEDES TimelineItem im Bridge-Draft, aus welchem CutPlan-Element es
entstand: CutPlan-Segment A -> TimelineItem B -> Asset C -> Zeitbereich D ->
Rundung/Anpassung E. Liest ausschließlich bereits vorhandene Cut-Plan-/
Bridge-Draft-Daten — keine eigene Übersetzung, keine Validierung, kein
Netzwerkzugriff."""

from __future__ import annotations

import json

from otio_app.analysis_models import EditPlanDocument
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


def _audio_timeline_item_id(scope: str, folder_name: str) -> str:
    scope_label = scope or AUDIO_SCOPE_INTRO
    folder_component = safe_timeline_item_component(folder_name) if folder_name else "intro"
    return f"edit_audio_{scope_label}_{folder_component}"


def build_edit_plan_bridge_trace(
    project: Project, cut_plan: CutPlanDocument, edit_plan: EditPlanDocument
) -> EditPlanBridgeTraceDocument:
    """Reine Funktion — baut EINEN EditPlanBridgeTraceEntry je Audio-Item UND
    je VisualSegment aus dem bereits vorhandenen Cut Plan. Nutzt dieselben
    reinen Frame-Rundungsfunktionen wie build_edit_plan_draft_from_confirmed_
    cut_plan, damit die getraceten Zeiten exakt den im edit_plan
    geschriebenen Werten entsprechen. Speichert nichts (siehe
    save_edit_plan_bridge_trace)."""
    fps = cut_plan.timeline_fps or 25
    entries: list[EditPlanBridgeTraceEntry] = []

    for audio_item in cut_plan.audio_items:
        rounded_in, rounded_out, source_in, source_out, frame_rounded, delta = round_audio_times_to_frame(
            audio_item.timeline_start_sec, audio_item.timeline_end_sec, audio_item.duration_sec, fps
        )
        timeline_item_id = _audio_timeline_item_id(audio_item.scope, audio_item.folder_name)
        entries.append(
            EditPlanBridgeTraceEntry(
                trace_id=f"trace_{timeline_item_id}",
                source_scope=audio_item.scope or AUDIO_SCOPE_INTRO,
                folder_name=audio_item.folder_name,
                timeline_item_id=timeline_item_id,
                timeline_item_type=EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
                track=audio_item.track or "A1",
                asset_path=audio_item.audio_path,
                timeline_in_sec=rounded_in,
                timeline_out_sec=rounded_out,
                source_in_sec=source_in,
                source_out_sec=source_out,
                frame_rounded=frame_rounded,
                frame_rounding_delta_sec=delta,
                reason="voiceover_audio",
            )
        )

    for item in cut_plan.items:
        source_sentence_id = ""
        source_hook_beat_id = ""
        for source_ref in item.source_refs:
            source_sentence_id = source_sentence_id or source_ref.source_sentence_id
            source_hook_beat_id = source_hook_beat_id or source_ref.source_hook_beat_id

        for segment in item.planned_visual_segments:
            rounded_in, rounded_out, source_in, source_out, frame_rounded, delta = round_visual_times_to_frame(
                segment.timeline_in_sec, segment.timeline_out_sec, segment.source_in_sec, segment.source_out_sec, fps
            )
            timeline_item_id = f"edit_{segment.segment_id}"
            timeline_item_type = "video_shot" if segment.asset_type == "video" else "image_shot"
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
                    timeline_in_sec=rounded_in,
                    timeline_out_sec=rounded_out,
                    source_in_sec=source_in,
                    source_out_sec=source_out,
                    frame_rounded=frame_rounded,
                    frame_rounding_delta_sec=delta,
                    reason=segment.reason,
                    warnings=list(item.warnings),
                    original_chosen_asset_id=item.chosen_asset_id,
                    duration_strategy=item.duration_strategy,
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
    path = get_cut_plan_edit_plan_bridge_trace_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")


def load_edit_plan_bridge_trace(project: Project) -> EditPlanBridgeTraceDocument | None:
    path = get_cut_plan_edit_plan_bridge_trace_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanBridgeTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
