"""Phase 10.2: Mapping-Trace für das Production-EditPlan-Staging.

Zeigt für jedes Produktions-TimelineItem UND jeden VoiceoverPlan, aus
welchem Bridge-/CutPlan-Element es entstand — macht CutPlan -> BridgeDraft
-> ProductionEditPlan vollständig nachvollziehbar. Liest ausschließlich
bereits vorhandene Daten (das fertige ProductionEditPlanPackage, die bereits
gebauten Sektions-EditPlanDocuments, den bestätigten Bridge-Trace und
-Audio-Plan) — keine eigene Übersetzung, keine Validierung, kein
Netzwerkzugriff."""

from __future__ import annotations

import json

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import AUDIO_SCOPE_INTRO
from otio_app.models import Project
from otio_app.project_layout import get_production_edit_plan_mapping_trace_path
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    EditPlanBridgeTraceDocument,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import (
    ProductionEditPlanMappingTraceDocument,
    ProductionEditPlanMappingTraceEntry,
    ProductionEditPlanPackage,
    ProductionEditPlanSection,
)

__all__ = [
    "build_production_edit_plan_mapping_trace",
    "save_production_edit_plan_mapping_trace",
    "load_production_edit_plan_mapping_trace",
]

_FIELDS_DEFAULTED_AUDIO = ["duration_source=bridge_audio_plan", "trim_policy=disabled"]
_FIELDS_DROPPED_AUDIO = ["voiceover_audio TimelineItem dropped from production timeline"]


def _matching_audio_plan_item(section: ProductionEditPlanSection, bridge_audio_plan: BridgeAudioPlanDocument):
    for candidate in bridge_audio_plan.items:
        candidate_is_intro = candidate.scope == AUDIO_SCOPE_INTRO
        if candidate_is_intro != section.is_intro:
            continue
        if not candidate_is_intro and candidate.folder_name != section.folder_name:
            continue
        return candidate
    return None


def build_production_edit_plan_mapping_trace(
    project: Project,
    package: ProductionEditPlanPackage,
    section_documents: dict[str, EditPlanDocument],
    bridge_trace: EditPlanBridgeTraceDocument,
    bridge_audio_plan: BridgeAudioPlanDocument,
) -> ProductionEditPlanMappingTraceDocument:
    """Reine Funktion — baut EINEN ProductionEditPlanMappingTraceEntry je
    Visual-TimelineItem UND je VoiceoverPlan aus den bereits gebauten
    Sektions-Dokumenten. Speichert nichts (siehe
    save_production_edit_plan_mapping_trace)."""
    bridge_trace_by_timeline_item_id = {entry.timeline_item_id: entry for entry in bridge_trace.entries}

    entries: list[ProductionEditPlanMappingTraceEntry] = []

    for section in package.sections:
        document = section_documents.get(section.staging_section_id)
        if document is None:
            continue

        for local_item in document.timeline_items:
            bridge_entry = bridge_trace_by_timeline_item_id.get(local_item.timeline_item_id)
            entries.append(
                ProductionEditPlanMappingTraceEntry(
                    trace_id=f"prod_trace_{local_item.timeline_item_id}",
                    source_bridge_timeline_item_id=local_item.timeline_item_id,
                    source_cut_item_id=bridge_entry.cut_item_id if bridge_entry else "",
                    source_visual_segment_id=bridge_entry.visual_segment_id if bridge_entry else "",
                    resulting_staging_section_id=section.staging_section_id,
                    resulting_production_section_id=section.production_section_id,
                    resulting_edit_plan_path=section.staged_edit_plan_path,
                    resulting_timeline_item_id=local_item.timeline_item_id,
                    folder_name=section.folder_name,
                    is_intro=section.is_intro,
                    original_timeline_in_sec=(
                        bridge_entry.timeline_in_sec if bridge_entry else local_item.timeline_in_sec
                    ),
                    original_timeline_out_sec=(
                        bridge_entry.timeline_out_sec if bridge_entry else local_item.timeline_out_sec
                    ),
                    local_timeline_in_sec=local_item.timeline_in_sec,
                    local_timeline_out_sec=local_item.timeline_out_sec,
                    asset_id=local_item.asset_id,
                    asset_path=local_item.resolved_media_path,
                    mapping_reason="bridge_visual_to_production_timeline_item",
                    warnings=list(local_item.warnings),
                )
            )

        if document.voiceover is not None:
            audio_plan_item = _matching_audio_plan_item(section, bridge_audio_plan)
            entries.append(
                ProductionEditPlanMappingTraceEntry(
                    trace_id=f"prod_trace_audio_{section.staging_section_id}",
                    source_bridge_audio_plan_index=(
                        audio_plan_item.source_cut_plan_audio_index if audio_plan_item else None
                    ),
                    resulting_staging_section_id=section.staging_section_id,
                    resulting_production_section_id=section.production_section_id,
                    resulting_edit_plan_path=section.staged_edit_plan_path,
                    folder_name=section.folder_name,
                    is_intro=section.is_intro,
                    original_timeline_in_sec=audio_plan_item.timeline_in_sec if audio_plan_item else 0.0,
                    original_timeline_out_sec=audio_plan_item.timeline_out_sec if audio_plan_item else 0.0,
                    local_timeline_in_sec=document.voiceover.timeline_start_sec,
                    local_timeline_out_sec=document.voiceover.timeline_end_sec,
                    asset_path=document.voiceover.path,
                    mapping_reason="bridge_audio_plan_to_voiceover_plan",
                    fields_defaulted=list(_FIELDS_DEFAULTED_AUDIO),
                    fields_dropped=list(_FIELDS_DROPPED_AUDIO),
                )
            )

    return ProductionEditPlanMappingTraceDocument(
        project_id=project.id,
        source_bridge_manifest_hash=package.source_bridge_manifest_hash,
        source_cut_plan_hash=package.source_cut_plan_hash,
        entries=entries,
    )


def save_production_edit_plan_mapping_trace(project: Project, trace: ProductionEditPlanMappingTraceDocument) -> None:
    normalized = trace.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_mapping_trace_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")


def load_production_edit_plan_mapping_trace(project: Project) -> ProductionEditPlanMappingTraceDocument | None:
    path = get_production_edit_plan_mapping_trace_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanMappingTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
