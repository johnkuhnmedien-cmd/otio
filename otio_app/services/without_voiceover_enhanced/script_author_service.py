"""LLM-Lauf 1: freiere Skripterzeugung für without_voiceover_enhanced."""

from __future__ import annotations

import json
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.gemini_client import _extract_json
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageNeed,
    EnhancedScriptDocument,
    FactCheckHint,
    ScriptSegment,
    VisualBeat,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import script_draft_path
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    detect_forbidden_phrases,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_script_prompt,
)


def _brief_text(project: Project) -> str:
    brief = load_project_brief(project)
    if brief is None:
        return "(kein Project Brief)"
    return brief.model_dump_json(indent=2)


def _dramaturgy_text(project: Project) -> str:
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return "(keine bestätigte Dramaturgie)"
    return plan.model_dump_json(indent=2)


def _style_text(project: Project) -> str:
    profile = load_style_profile(project)
    if profile is None:
        return "(kein Style Profile)"
    return profile.model_dump_json(indent=2)


def _verified_facts_text(project: Project) -> str:
    brief = load_project_brief(project)
    parts: list[str] = []
    if brief is not None:
        for field_name in (
            "video_title",
            "language",
            "tone_tags",
            "negative_rules_freetext",
            "forbidden_phrases",
            "global_extra_prompt",
        ):
            value = getattr(brief, field_name, None)
            if value:
                parts.append(f"{field_name}: {value}")
    return "\n".join(parts) if parts else "(keine verifizierten Fakten hinterlegt)"


def _asset_inventory_summary(project: Project) -> str:
    lines: list[str] = []
    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        asset_count = len(getattr(inventory, "assets", []) or [])
        lines.append(f"- {folder}: {asset_count} assets (visual resource only)")
    return "\n".join(lines) if lines else "(keine lokalen Assets inventarisiert)"


def parse_enhanced_script_response(raw: str | dict[str, Any]) -> EnhancedScriptDocument:
    if isinstance(raw, str):
        payload = _extract_json(raw)
    else:
        payload = raw
    if not isinstance(payload, dict):
        raise ValueError("Skript-Antwort ist kein JSON-Objekt.")

    segments_raw = payload.get("segments") or []
    segments: list[ScriptSegment] = []
    for index, item in enumerate(segments_raw, start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        segment_id = str(item.get("segment_id") or f"segment_{index:03d}")
        if " " in text and any(ch.isalnum() for ch in text):
            # Guard: no mid-word split markers expected from schema; keep text as-is.
            pass
        segments.append(
            ScriptSegment(
                segment_id=segment_id,
                text=text,
                sequence_index=int(item.get("sequence_index") or index),
                semantic_function=str(item.get("semantic_function") or "narration"),
                visual_intent_ids=[
                    str(x) for x in (item.get("visual_intent_ids") or []) if x
                ],
                fact_check_required=bool(item.get("fact_check_required", False)),
            )
        )

    intents = [
        VisualIntent(
            intent_id=str(item.get("intent_id") or f"intent_{i:03d}"),
            description=str(item.get("description") or ""),
            subject=str(item.get("subject") or ""),
            location=str(item.get("location") or ""),
            preferred_media_type=str(item.get("preferred_media_type") or "video"),
        )
        for i, item in enumerate(payload.get("visual_intents") or [], start=1)
        if isinstance(item, dict)
    ]
    beats = [
        VisualBeat(
            beat_id=str(item.get("beat_id") or f"beat_{i:03d}"),
            description=str(item.get("description") or ""),
            related_segment_ids=[
                str(x) for x in (item.get("related_segment_ids") or []) if x
            ],
            visual_intent_ids=[
                str(x) for x in (item.get("visual_intent_ids") or []) if x
            ],
        )
        for i, item in enumerate(payload.get("visual_beats") or [], start=1)
        if isinstance(item, dict)
    ]
    needs = [
        CoverageNeed(
            need_id=str(item.get("need_id") or f"need_{i:03d}"),
            visual_intent_id=str(item.get("visual_intent_id") or ""),
            subject=str(item.get("subject") or ""),
            reason=str(item.get("reason") or ""),
            search_queries=[str(x) for x in (item.get("search_queries") or []) if x],
        )
        for i, item in enumerate(payload.get("coverage_needs") or [], start=1)
        if isinstance(item, dict)
    ]
    hints = [
        FactCheckHint(
            hint_id=str(item.get("hint_id") or f"fact_{i:03d}"),
            related_segment_id=str(item.get("related_segment_id") or ""),
            claim=str(item.get("claim") or ""),
            status=str(item.get("status") or "fact_check_required"),
            note=str(item.get("note") or ""),
        )
        for i, item in enumerate(payload.get("fact_check_hints") or [], start=1)
        if isinstance(item, dict)
    ]

    narration = str(payload.get("narration_full") or "").strip()
    if not narration:
        narration = " ".join(s.text for s in segments)

    # Unbelegte Fakten: fact_check_required Segmente bleiben markiert, werden
    # nicht als gesichert übernommen (Status bleibt im Dokument sichtbar).
    for segment in segments:
        if segment.fact_check_required and segment.segment_id not in {
            h.related_segment_id for h in hints
        }:
            hints.append(
                FactCheckHint(
                    hint_id=f"fact_auto_{segment.segment_id}",
                    related_segment_id=segment.segment_id,
                    claim=segment.text,
                    status="fact_check_required",
                    note="Segment vom Modell als unbelegt markiert.",
                )
            )

    doc = EnhancedScriptDocument(
        narration_full=narration,
        segments=segments,
        visual_beats=beats,
        visual_intents=intents,
        coverage_needs=needs,
        fact_check_hints=hints,
        forbidden_phrases_found=detect_forbidden_phrases(narration),
        script_status="draft",
    )
    return doc


def generate_enhanced_script(
    project: Project,
    *,
    llm_callable: Callable[..., Any] | None = None,
    model: str = "openai:gpt-5.4-mini",
) -> EnhancedScriptDocument:
    prompt = build_enhanced_script_prompt(
        project_brief_text=_brief_text(project),
        dramaturgy_text=_dramaturgy_text(project),
        style_profile_text=_style_text(project),
        verified_facts_text=_verified_facts_text(project),
        asset_inventory_summary=_asset_inventory_summary(project),
        language=project.language,
    )
    if llm_callable is not None:
        raw = llm_callable(prompt=prompt, model=model)
        raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
    else:
        raw_text = generate_plan_text_with_metadata(prompt=prompt, model=model).raw_text
    document = parse_enhanced_script_response(raw_text)
    save_script_draft(project, document)
    write_json(script_draft_path(project), document)
    return document


def looks_like_asset_inventory_script(document: EnhancedScriptDocument) -> bool:
    """Heuristik: Skript wirkt wie Asset-Inventarliste."""
    if document.forbidden_phrases_found:
        return True
    if not document.segments:
        return True
    # If every segment has exactly one visual intent and intents == segments, suspicious.
    if document.visual_intents and len(document.visual_intents) == len(document.segments):
        single = all(len(s.visual_intent_ids) <= 1 for s in document.segments)
        if single and all(
            phrase in document.narration_full.lower()
            for phrase in ("asset", "bild zeigt")
        ):
            return True
    return False
