"""LLM-Lauf 1: Skripterzeugung für without_voiceover_enhanced.

Standard: ein Call pro Dramaturgie-Kapitel (wie klassische Folder-VOs),
Ergebnisse werden in ein EnhancedScriptDocument gemerged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import (
    PlanLlmCancelledError,
    PlanLlmNotConfiguredError,
    generate_plan_text_with_metadata,
    reraise_if_llm_cancelled,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverSetting,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_reference_service import (
    compute_style_context_hash,
    is_raw_style_mode,
    load_style_references,
    style_context_text_for_prompts,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageNeed,
    EnhancedScriptDocument,
    FactCheckHint,
    ScriptSegment,
    VisualBeat,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import script_locked_path
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    detect_forbidden_phrases,
    load_script_draft,
    save_script_draft,
    update_folder_chapter_narration,
)
from otio_app.services.without_voiceover_enhanced.script_neighbor_context import (
    build_chapter_order_block,
    build_editorial_neighbor_craft_block,
    build_film_wide_editorial_links_block,
    build_recent_neighbor_excerpts_block,
    recent_prior_chapter_excerpts,
)
from otio_app.services.without_voiceover_enhanced.raw_chapter_style_structure import (
    analyze_raw_chapter_style_structure,
    detect_raw_chapter_style_violations,
    prepare_raw_chapter_reference,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    ChapterDisplayTextError,
    canonicalize_script_document_to_pause_blocks,
    chapter_display_text,
    flatten_folder_segments_to_pause_blocks,
    join_spoken_segment_texts,
    normalize_author_pause_seconds,
    parse_chapter_display_text,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_link_guard import (
    detect_chapter_link_violations,
)
from otio_app.services.without_voiceover_enhanced.script_series_bridge import (
    SERIES_BRIDGE_CTA_REPAIR_INSTRUCTION,
    SERIES_BRIDGE_LINK_REPAIR_INSTRUCTION,
    build_series_bridge_prompt_block,
    detect_series_bridge_cta_violations,
    series_bridge_from_brief,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
    build_enhanced_script_revision_prompt,
)
from otio_app.services.without_voiceover_enhanced.script_opening_inventory import (
    build_opening_inventory_prompt_block,
    clear_opening_inventory,
    load_opening_inventory,
    merge_opening_for_folder,
    remove_opening_for_folder,
    save_opening_inventory,
    validate_opening_against_inventory,
)
from otio_app.services.without_voiceover_enhanced.script_rhetoric import (
    build_rhetoric_ledger_prompt_block,
    clear_rhetoric_ledger,
    load_rhetoric_ledger,
    merge_rhetoric_claims_for_folder,
    parse_rhetoric_usage,
    remove_rhetoric_claims_for_folder,
    save_rhetoric_ledger,
    validate_rhetoric_usage_against_ledger,
)

DEFAULT_ENHANCED_SCRIPT_MODEL = "openai:gpt-5.4-mini"
DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS = 100_000


@dataclass
class FolderScriptBuildResult:
    folder_name: str
    status: str  # PASS | FAIL
    document: EnhancedScriptDocument | None = None
    error: str | None = None
    segment_count: int = 0


_BRIEF_PROMPT_EXCLUDE = frozenset(
    {
        "project_id",
        "generated_at",
        # Title-LLM inspiration only — not needed for chapter narration.
        "title_references",
        # Last-chapter series bridge is injected as its own prompt block.
        "series_bridge_enabled",
        "series_bridge_destination",
        "series_bridge_hook_facts",
        "series_bridge_angle",
    }
)


def _brief_text(project: Project) -> str:
    brief = load_project_brief(project)
    if brief is None:
        return "(kein Project Brief)"
    payload = brief.model_dump(mode="json", exclude=_BRIEF_PROMPT_EXCLUDE)
    compact = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    if not compact:
        return "(kein Project Brief)"
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _style_text(project: Project, *, for_chapter: bool = True) -> str:
    return style_context_text_for_prompts(
        project, detailed=True, for_chapter=for_chapter
    )


def _style_is_raw_chapter(project: Project) -> bool:
    return is_raw_style_mode(load_style_references(project))


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


def list_enabled_dramaturgy_folders(project: Project) -> list[DramaturgyFolderEntry]:
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return []
    return sorted(
        (entry for entry in plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )


def _film_context_text(plan: DramaturgyPlan) -> str:
    return (
        f"project_title: {plan.project_title or '-'}\n"
        f"core_promise: {plan.core_promise or '-'}\n"
        "narrative_arc (SILENT EDITORIAL METADATA — DO NOT VERBALIZE): "
        f"{plan.narrative_arc or '-'}"
    )


def _chapter_dramaturgy_text(entry: DramaturgyFolderEntry) -> str:
    return (
        "SILENT EDITORIAL METADATA — DO NOT VERBALIZE\n"
        f"folder_name: {entry.folder_name}\n"
        f"order_index: {entry.order_index}\n"
        f"dramaturgy_role: {entry.dramaturgy_role}\n"
        f"reason: {entry.reason or '-'}\n"
        f"recommended_word_count: {entry.recommended_word_count}\n"
        f"recommended_min_words: {entry.recommended_min_words}\n"
        f"recommended_max_words: {entry.recommended_max_words}"
    )


def _word_targets_for_folder(
    project: Project, entry: DramaturgyFolderEntry
) -> tuple[int, int, int]:
    settings_doc = load_folder_voiceover_settings(project)
    if settings_doc is None:
        settings_doc = build_default_folder_voiceover_settings(project)
    for setting in settings_doc.settings:
        if setting.folder_name == entry.folder_name and setting.enabled:
            return setting.target_words, setting.min_words, setting.max_words
    target = entry.recommended_word_count or VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    min_words = entry.recommended_min_words or VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
    max_words = entry.recommended_max_words or VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS
    return target, min_words, max_words


def _previous_and_next_folder(
    entries: list[DramaturgyFolderEntry], folder_name: str
) -> tuple[str | None, str | None]:
    names = [entry.folder_name for entry in entries]
    if folder_name not in names:
        return None, None
    index = names.index(folder_name)
    previous_name = names[index - 1] if index > 0 else None
    next_name = names[index + 1] if index + 1 < len(names) else None
    return previous_name, next_name


def _folder_voiceover_setting_for(
    project: Project, folder_name: str
) -> FolderVoiceoverSetting | None:
    settings_doc = load_folder_voiceover_settings(project)
    if settings_doc is None:
        settings_doc = build_default_folder_voiceover_settings(project)
    for setting in settings_doc.settings:
        if setting.folder_name == folder_name:
            return setting
    return None


def _build_script_neighbor_context(
    *,
    project: Project,
    entries: list[DramaturgyFolderEntry],
    entry: DramaturgyFolderEntry,
    previous_name: str | None,
    next_name: str | None,
    existing_draft: EnhancedScriptDocument | None,
) -> tuple[str, str, str, str, str, str]:
    """Kapitelliste, Film-Links, Opening-Inventar, Rhetoric, Nachbar-Sätze, Craft."""
    folder_order = [item.folder_name for item in entries]
    chapter_order_text = build_chapter_order_block(
        entries,
        current_folder_name=entry.folder_name,
    )
    setting = _folder_voiceover_setting_for(project, entry.folder_name)
    allow_callback = bool(setting and setting.callback_to_previous)
    film_wide_editorial_links_text = build_film_wide_editorial_links_block(
        allow_callback=allow_callback,
        allow_forward_glance=False,
    )

    opening_inventory = remove_opening_for_folder(
        load_opening_inventory(project), entry.folder_name
    )
    opening_inventory_text = build_opening_inventory_prompt_block(
        opening_inventory,
        exclude_folder=entry.folder_name,
    )

    # Claims dieses Kapitels freigeben, damit Re-Generate denselben Slot neu setzen kann.
    ledger = remove_rhetoric_claims_for_folder(
        load_rhetoric_ledger(project), entry.folder_name
    )
    rhetoric_ledger_text = build_rhetoric_ledger_prompt_block(ledger)

    chapter_index = folder_order.index(entry.folder_name)
    prior_folder_names: list[str] = []
    if chapter_index >= 2:
        prior_folder_names = folder_order[chapter_index - 2 : chapter_index]

    narration_by_folder = {
        name: chapter_narration_text(existing_draft, name)
        for name in prior_folder_names
    }
    excerpts = recent_prior_chapter_excerpts(
        prior_folder_names=prior_folder_names,
        narration_for_folder=narration_by_folder,
    )
    recent_neighbor_excerpts_text = build_recent_neighbor_excerpts_block(excerpts)

    editorial_neighbor_craft_text = build_editorial_neighbor_craft_block(
        entry=entry,
        setting=setting,
        previous_folder_name=previous_name,
        next_folder_name=next_name,
    )
    return (
        chapter_order_text,
        film_wide_editorial_links_text,
        opening_inventory_text,
        rhetoric_ledger_text,
        recent_neighbor_excerpts_text,
        editorial_neighbor_craft_text,
    )


def parse_enhanced_script_response(
    raw: str | dict[str, Any],
    *,
    folder_name: str = "",
    folder_order_index: int = 0,
) -> EnhancedScriptDocument:
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
                folder_name=folder_name or str(item.get("folder_name") or ""),
                folder_order_index=folder_order_index
                or int(item.get("folder_order_index") or 0),
                paragraph_break_after=bool(item.get("paragraph_break_after", False)),
                author_pause_after_seconds=normalize_author_pause_seconds(
                    item.get("author_pause_after_seconds", 0.0)
                ),
            )
        )

    intents = [
        VisualIntent(
            intent_id=str(item.get("intent_id") or f"intent_{i:03d}"),
            description=str(item.get("description") or ""),
            subject=str(item.get("subject") or ""),
            location=str(item.get("location") or folder_name or ""),
            preferred_media_type=str(item.get("preferred_media_type") or "video"),
            folder_name=folder_name or str(item.get("folder_name") or ""),
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
        narration = join_spoken_segment_texts(segments)

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

    document = EnhancedScriptDocument(
        narration_full=narration,
        segments=segments,
        visual_beats=beats,
        visual_intents=intents,
        coverage_needs=needs,
        fact_check_hints=hints,
        forbidden_phrases_found=detect_forbidden_phrases(narration),
        script_status="draft",
    )
    # Kapitel kanonisch als Text + Autorenpausen speichern (nicht Satz-Segmente).
    if folder_name:
        flat, id_map = flatten_folder_segments_to_pause_blocks(
            document.segments,
            folder_name=folder_name,
            segment_id_prefix=f"{safe_folder_slug(folder_name)}_segment",
        )
        if flat:
            for beat in document.visual_beats:
                remapped: list[str] = []
                for segment_id in beat.related_segment_ids:
                    mapped = id_map.get(segment_id, segment_id)
                    if mapped and mapped not in remapped:
                        remapped.append(mapped)
                beat.related_segment_ids = remapped
            for hint in document.fact_check_hints:
                if hint.related_segment_id in id_map:
                    hint.related_segment_id = id_map[hint.related_segment_id]
            document.segments = flat
            document.narration_full = join_spoken_segment_texts(document.segments)
            document.forbidden_phrases_found = detect_forbidden_phrases(
                document.narration_full
            )
    else:
        canonicalize_script_document_to_pause_blocks(document)
        document.forbidden_phrases_found = detect_forbidden_phrases(
            document.narration_full
        )
    return document


def _invalidate_script_lock(project: Project) -> None:
    locked = script_locked_path(project)
    if locked.is_file():
        locked.unlink()


def _resequence_document(
    document: EnhancedScriptDocument,
    folder_order: list[str],
) -> EnhancedScriptDocument:
    order_rank = {name: index for index, name in enumerate(folder_order)}
    segments = sorted(
        document.segments,
        key=lambda seg: (
            order_rank.get(seg.folder_name, 10_000),
            seg.folder_order_index,
            seg.sequence_index,
        ),
    )
    for index, segment in enumerate(segments, start=1):
        segment.sequence_index = index
    document.segments = segments
    document.narration_full = join_spoken_segment_texts(segments)
    document.forbidden_phrases_found = detect_forbidden_phrases(document.narration_full)
    document.script_status = "draft"
    return document


def merge_folder_script_into_document(
    existing: EnhancedScriptDocument | None,
    folder_partial: EnhancedScriptDocument,
    *,
    folder_name: str,
    folder_order_index: int,
    folder_order: list[str],
) -> EnhancedScriptDocument:
    base = existing or EnhancedScriptDocument(script_status="draft")

    keep_segments = [s for s in base.segments if s.folder_name != folder_name]
    removed_ids = {s.segment_id for s in base.segments if s.folder_name == folder_name}
    keep_intent_ids = {
        intent_id for seg in keep_segments for intent_id in seg.visual_intent_ids
    }
    keep_intents = [
        intent
        for intent in base.visual_intents
        if intent.folder_name != folder_name
        and (intent.folder_name != "" or intent.intent_id in keep_intent_ids)
    ]
    keep_beats = [
        beat
        for beat in base.visual_beats
        if not any(seg_id in removed_ids for seg_id in beat.related_segment_ids)
    ]
    kept_intent_id_set = {intent.intent_id for intent in keep_intents}
    keep_needs = [
        need
        for need in base.coverage_needs
        if not need.visual_intent_id or need.visual_intent_id in kept_intent_id_set
    ]
    keep_hints = [
        hint
        for hint in base.fact_check_hints
        if hint.related_segment_id not in removed_ids
    ]

    stamped_segments = [
        seg.model_copy(
            update={
                "folder_name": folder_name,
                "folder_order_index": folder_order_index,
            }
        )
        for seg in folder_partial.segments
    ]
    stamped_intents = [
        intent.model_copy(
            update={
                "folder_name": folder_name,
                "location": intent.location or folder_name,
            }
        )
        for intent in folder_partial.visual_intents
    ]

    merged = EnhancedScriptDocument(
        schema_version=base.schema_version,
        script_version=base.script_version,
        script_status="draft",
        segments=keep_segments + stamped_segments,
        visual_beats=keep_beats + list(folder_partial.visual_beats),
        visual_intents=keep_intents + stamped_intents,
        coverage_needs=keep_needs + list(folder_partial.coverage_needs),
        fact_check_hints=keep_hints + list(folder_partial.fact_check_hints),
        source_brief_hash=base.source_brief_hash,
        source_style_context_hash=(
            folder_partial.source_style_context_hash
            or base.source_style_context_hash
        ),
    )
    canonicalize_script_document_to_pause_blocks(merged)
    return _resequence_document(merged, folder_order)


def folders_present_in_script(document: EnhancedScriptDocument | None) -> set[str]:
    if document is None:
        return set()
    return {seg.folder_name for seg in document.segments if seg.folder_name}


def chapter_narration_text(
    document: EnhancedScriptDocument | None,
    folder_name: str,
) -> str:
    """Nur gesprochener Kapiteltext (ohne sichtbare Pausemarker)."""
    if document is None:
        return ""
    return join_spoken_segment_texts(
        [
            seg
            for seg in document.segments
            if seg.folder_name == folder_name and seg.text.strip()
        ]
    )


def chapter_display_text_for_folder(
    document: EnhancedScriptDocument | None,
    folder_name: str,
) -> str:
    """Sichtbare Kapitelansicht inkl. [pause X seconds]."""
    if document is None:
        return ""
    return chapter_display_text(
        [
            seg
            for seg in document.segments
            if seg.folder_name == folder_name and seg.text.strip()
        ]
    )


CHAPTER_LINK_REPAIR_INSTRUCTION = """\
REPAIR REQUIRED

The previous answer used a forbidden spoken connection between chapters.

Rewrite the complete chapter as a self-contained mini-documentary.

Start directly with the location, a concrete defining feature, its historical importance, or a verified fact.

Do not describe leaving another place, travelling here, continuing the journey, the road to the next location, or what comes next.

Preserve the factual content and target length.
Return the complete required JSON again.
"""


# Wie Unified-Cut: ein automatischer Zweitversuch nach Fehler hinter dem LLM-Call.
SCRIPT_LLM_ATTEMPTS = 2


def _generic_script_retry_instruction(error: str) -> str:
    """Repair-Hinweis, wenn die erste Antwort nach dem LLM-Call unbrauchbar war."""
    detail = (error or "").strip()
    if len(detail) > 500:
        detail = detail[:500] + "…"
    return (
        "RETRY REQUIRED\n"
        "The previous model response could not be used after the LLM call.\n"
        f"Error: {detail}\n"
        "Return a complete valid JSON object that satisfies the schema.\n"
        "Do not repeat the previous mistake."
    )


def _notify_llm_retry(on_retry: Callable[[str], None] | None, error: str) -> None:
    if on_retry is None:
        return
    on_retry((error or "").strip() or "unbekannter Fehler")


def _compose_script_repair_instruction(
    *,
    link_errors: list[str],
    style_errors: list[str],
    cta_errors: list[str],
    series_bridge_active: bool,
    style_repair: str,
) -> str:
    parts: list[str] = []
    if cta_errors:
        parts.append(SERIES_BRIDGE_CTA_REPAIR_INSTRUCTION)
    if link_errors:
        parts.append(
            SERIES_BRIDGE_LINK_REPAIR_INSTRUCTION
            if series_bridge_active
            else CHAPTER_LINK_REPAIR_INSTRUCTION
        )
    if style_errors and style_repair:
        parts.append(style_repair)
    return "\n\n".join(part.strip() for part in parts if part.strip())


RAW_STYLE_REPAIR_INSTRUCTION = """\
RAW STYLE REPAIR REQUIRED

The previous answer did not follow the binding prose architecture of the Raw Chapter Reference.

Rewrite the complete chapter.

Match the reference's:
- directness
- sentence-length pattern
- factual density
- one-main-idea-per-beat rhythm
- restrained use of atmosphere
- concrete treatment of places and landmarks

Do not copy facts, place names, wording or sentences from the reference.

Do not include pause labels or production directions.

Keep the chapter-specific verified content and target length.

Return the complete required JSON again.
"""

RAW_STYLE_PAUSE_REPAIR_INSTRUCTION = """\
RAW STYLE REPAIR REQUIRED

The Raw Chapter Reference uses explicit timed pauses between factual beats.

The previous response did not reproduce that pause rhythm.

Set author_pause_after_seconds on appropriate segment boundaries, using the observed duration range from the reference.

Do not write pause labels inside spoken text.
Return the complete JSON again.
"""


def _validate_chapter_link_usage_audit(
    payload: dict[str, Any] | None,
    *,
    narration_full: str,
    allow_from_previous: bool,
    allow_to_next: bool,
    allow_callback: bool,
) -> list[str]:
    if not isinstance(payload, dict):
        return []
    usage = payload.get("chapter_link_usage")
    if usage is None:
        return []
    if not isinstance(usage, dict):
        return ["chapter_link_usage muss ein Objekt sein."]
    errors: list[str] = []
    narration = narration_full or ""
    for key, allowed in (
        ("from_previous", allow_from_previous),
        ("to_next", allow_to_next),
        ("callback", allow_callback),
    ):
        if bool(usage.get(key)) and not allowed:
            errors.append(
                f"chapter_link_usage.{key}=true ohne Erlaubnis."
            )
    for quote in usage.get("evidence_quotes") or []:
        text = str(quote or "").strip()
        if text and text not in narration:
            errors.append(
                f"chapter_link_usage evidence_quote fehlt in narration_full: «{text[:80]}»"
            )
    return errors


def segments_for_folder(
    document: EnhancedScriptDocument | None,
    folder_name: str,
) -> list[ScriptSegment]:
    if document is None:
        return []
    return [seg for seg in document.segments if seg.folder_name == folder_name]


def group_segments_by_folder(
    document: EnhancedScriptDocument | None,
    *,
    folder_order: list[str] | None = None,
) -> list[tuple[str, list[ScriptSegment]]]:
    """Kapitel-Gruppen in Dramaturgie-Reihenfolge; Intro immer zuerst."""
    if document is None or not document.segments:
        return []
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        is_intro_folder_name,
    )

    buckets: dict[str, list[ScriptSegment]] = {}
    for segment in document.segments:
        key = segment.folder_name or ""
        buckets.setdefault(key, []).append(segment)

    ordered: list[tuple[str, list[ScriptSegment]]] = []
    seen: set[str] = set()
    for name in folder_order or []:
        if name in buckets:
            ordered.append((name, buckets[name]))
            seen.add(name)
    for name, segs in buckets.items():
        if name not in seen:
            ordered.append((name, segs))

    intro_groups = [(name, segs) for name, segs in ordered if is_intro_folder_name(name)]
    other_groups = [
        (name, segs) for name, segs in ordered if not is_intro_folder_name(name)
    ]
    return intro_groups + other_groups


def _strip_plain_narration_response(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def revise_enhanced_script_for_folder(
    project: Project,
    folder_name: str,
    *,
    editor_instructions: str,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    llm_callable: Callable[..., Any] | None = None,
    on_retry: Callable[[str], None] | None = None,
) -> FolderScriptBuildResult:
    """Revidiert ein bestehendes Kapitel-Skript — nur Freitext + aktuelles Skript ans LLM."""
    instructions = (editor_instructions or "").strip()
    if not instructions:
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="FAIL",
            error="Freitext-Anweisung ist leer.",
        )

    draft = load_script_draft(project)
    # Display text includes [pause N seconds] so the LLM can preserve timing 1:1.
    current = chapter_display_text_for_folder(draft, folder_name).strip()
    if not current:
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="FAIL",
            error=f"Kein bestehendes Skript für „{folder_name}“ — zuerst erzeugen.",
        )

    prompt = build_enhanced_script_revision_prompt(
        editor_instructions=instructions,
        current_script=current,
        folder_name=folder_name,
        language=project.language,
    )
    model_id = resolve_llm_model_id(provider, model)
    last_error = "Freitext-Nachbearbeitung fehlgeschlagen."
    for attempt in range(SCRIPT_LLM_ATTEMPTS):
        attempt_prompt = prompt
        if attempt > 0:
            attempt_prompt = (
                f"{prompt}\n\n{_generic_script_retry_instruction(last_error)}"
            )
        try:
            if llm_callable is not None:
                raw = llm_callable(
                    prompt=attempt_prompt,
                    model=model_id,
                    max_output_tokens=max_output_tokens,
                )
                raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
            else:
                raw_text = generate_plan_text_with_metadata(
                    prompt=attempt_prompt,
                    model=model_id,
                    max_output_tokens=max_output_tokens,
                ).raw_text
            revised = _strip_plain_narration_response(raw_text)
            if not revised:
                last_error = "LLM-Antwort war leer."
                if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                    return FolderScriptBuildResult(
                        folder_name=folder_name,
                        status="FAIL",
                        error=last_error,
                    )
                _notify_llm_retry(on_retry, last_error)
                continue
            updated = update_folder_chapter_narration(project, folder_name, revised)
            return FolderScriptBuildResult(
                folder_name=folder_name,
                status="PASS",
                document=updated,
                segment_count=sum(
                    1 for seg in updated.segments if seg.folder_name == folder_name
                ),
            )
        except PlanLlmCancelledError:
            raise
        except PlanLlmNotConfiguredError as exc:
            return FolderScriptBuildResult(
                folder_name=folder_name,
                status="FAIL",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_llm_cancelled(exc)
            last_error = str(exc)
            if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                return FolderScriptBuildResult(
                    folder_name=folder_name,
                    status="FAIL",
                    error=last_error,
                )
            _notify_llm_retry(on_retry, last_error)
    return FolderScriptBuildResult(
        folder_name=folder_name,
        status="FAIL",
        error=last_error,
    )


def revise_all_enhanced_scripts(
    project: Project,
    *,
    editor_instructions: str,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[FolderScriptBuildResult]:
    """Wendet denselben Freitext sequenziell auf alle vorhandenen Kapitel-Skripte an."""
    draft = load_script_draft(project)
    present = sorted(
        folders_present_in_script(draft),
        key=lambda name: next(
            (
                entry.order_index
                for entry in list_enabled_dramaturgy_folders(project)
                if entry.folder_name == name
            ),
            10_000,
        ),
    )
    results: list[FolderScriptBuildResult] = []
    total = len(present)
    for index, folder_name in enumerate(present, start=1):
        if progress_callback is not None:
            progress_callback(folder_name, index, total)
        results.append(
            revise_enhanced_script_for_folder(
                project,
                folder_name,
                editor_instructions=editor_instructions,
                provider=provider,
                model=model,
                max_output_tokens=max_output_tokens,
                llm_callable=llm_callable,
            )
        )
    return results


def generate_enhanced_script_for_folder(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    llm_callable: Callable[..., Any] | None = None,
    on_retry: Callable[[str], None] | None = None,
) -> FolderScriptBuildResult:
    entries = list_enabled_dramaturgy_folders(project)
    if not entries:
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="FAIL",
            error="Keine bestätigte Dramaturgie mit aktiven Ordnern.",
        )
    entry = next((item for item in entries if item.folder_name == folder_name), None)
    if entry is None:
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="FAIL",
            error=f"Ordner „{folder_name}“ ist in der bestätigten Dramaturgie nicht aktiv.",
        )

    plan = load_confirmed_dramaturgy(project)
    assert plan is not None
    previous_name, next_name = _previous_and_next_folder(entries, folder_name)
    target, min_words, max_words = _word_targets_for_folder(project, entry)
    folder_slug = safe_folder_slug(folder_name)
    existing_draft = load_script_draft(project)
    setting = _folder_voiceover_setting_for(project, folder_name)
    allow_from = bool(setting and setting.transition_from_previous)
    allow_to = bool(setting and setting.transition_to_next)
    allow_callback = bool(setting and setting.callback_to_previous)
    allow_contrast = bool(setting and setting.use_contrast_with_previous)
    allow_commonality = bool(setting and setting.use_commonality_with_previous)
    style_is_raw = _style_is_raw_chapter(project)
    style_hash = compute_style_context_hash(project)
    raw_structure = None
    if style_is_raw:
        refs = load_style_references(project)
        raw_structure = analyze_raw_chapter_style_structure(
            prepare_raw_chapter_reference(refs.raw_reference_text or "")
        )

    brief = load_project_brief(project)
    series_bridge = series_bridge_from_brief(brief)
    is_last_chapter = bool(entries) and entries[-1].folder_name == folder_name
    series_bridge_text = build_series_bridge_prompt_block(
        series_bridge,
        this_place=project.video_place or "",
        is_last_chapter=is_last_chapter,
    )
    series_bridge_active = bool(series_bridge_text)

    (
        chapter_order_text,
        film_wide_editorial_links_text,
        opening_inventory_text,
        rhetoric_ledger_text,
        recent_neighbor_excerpts_text,
        editorial_neighbor_craft_text,
    ) = _build_script_neighbor_context(
        project=project,
        entries=entries,
        entry=entry,
        previous_name=previous_name,
        next_name=next_name,
        existing_draft=existing_draft,
    )

    model_id = resolve_llm_model_id(provider, model)
    repair_instruction = ""
    last_error = "Skripterzeugung fehlgeschlagen."
    for attempt in range(SCRIPT_LLM_ATTEMPTS):
        try:
            prompt = build_enhanced_folder_script_prompt(
                project_brief_text=_brief_text(project),
                film_context_text=_film_context_text(plan),
                chapter_dramaturgy_text=_chapter_dramaturgy_text(entry),
                style_profile_text=_style_text(project, for_chapter=True),
                verified_facts_text=_verified_facts_text(project),
                folder_name=folder_name,
                folder_slug=folder_slug,
                dramaturgy_role=entry.dramaturgy_role,
                target_words=target,
                min_words=min_words,
                max_words=max_words,
                previous_folder_name=previous_name,
                next_folder_name=next_name,
                chapter_order_text=chapter_order_text,
                film_wide_editorial_links_text=film_wide_editorial_links_text,
                recent_neighbor_excerpts_text=recent_neighbor_excerpts_text,
                editorial_neighbor_craft_text=editorial_neighbor_craft_text,
                rhetoric_ledger_text=rhetoric_ledger_text,
                opening_inventory_text=opening_inventory_text,
                series_bridge_text=series_bridge_text,
                language=project.language,
                transition_from_previous=allow_from,
                transition_to_next=allow_to,
                callback_to_previous=allow_callback,
                use_contrast_with_previous=allow_contrast,
                use_commonality_with_previous=allow_commonality,
                style_is_raw_chapter=style_is_raw,
                repair_instruction=repair_instruction,
            )

            if llm_callable is not None:
                raw = llm_callable(
                    prompt=prompt,
                    model=model_id,
                    max_output_tokens=max_output_tokens,
                )
                raw_text = (
                    raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
                )
            else:
                raw_text = generate_plan_text_with_metadata(
                    prompt=prompt,
                    model=model_id,
                    max_output_tokens=max_output_tokens,
                ).raw_text
            partial = parse_enhanced_script_response(
                raw_text,
                folder_name=folder_name,
                folder_order_index=entry.order_index,
            )
            if not partial.segments:
                last_error = "LLM-Antwort enthielt keine Segmente."
                if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                    return FolderScriptBuildResult(
                        folder_name=folder_name,
                        status="FAIL",
                        error=last_error,
                    )
                repair_instruction = _generic_script_retry_instruction(last_error)
                _notify_llm_retry(on_retry, last_error)
                continue

            payload = _extract_json(raw_text) if isinstance(raw_text, str) else raw_text
            payload_dict = payload if isinstance(payload, dict) else None
            usage = parse_rhetoric_usage(payload_dict)
            ledger_for_validation = remove_rhetoric_claims_for_folder(
                load_rhetoric_ledger(project), folder_name
            )
            rhetoric_errors = validate_rhetoric_usage_against_ledger(
                usage=usage,
                ledger=ledger_for_validation,
                folder_name=folder_name,
                narration_full=partial.narration_full,
            )
            if rhetoric_errors:
                last_error = "Rhetoric-Ledger: " + " ".join(rhetoric_errors)
                if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                    return FolderScriptBuildResult(
                        folder_name=folder_name,
                        status="FAIL",
                        error=last_error,
                    )
                repair_instruction = _generic_script_retry_instruction(last_error)
                _notify_llm_retry(on_retry, last_error)
                continue

            opening_for_validation = remove_opening_for_folder(
                load_opening_inventory(project), folder_name
            )
            opening_errors = validate_opening_against_inventory(
                narration_full=partial.narration_full,
                inventory=opening_for_validation,
                folder_name=folder_name,
            )
            if opening_errors:
                last_error = "Satzanfang-Inventar: " + " ".join(opening_errors)
                if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                    return FolderScriptBuildResult(
                        folder_name=folder_name,
                        status="FAIL",
                        error=last_error,
                    )
                repair_instruction = _generic_script_retry_instruction(last_error)
                _notify_llm_retry(on_retry, last_error)
                continue

            link_errors = detect_chapter_link_violations(
                partial.narration_full,
                language=project.language,
                allow_from_previous=allow_from,
                allow_to_next=allow_to,
                allow_callback=allow_callback,
            )
            link_errors.extend(
                _validate_chapter_link_usage_audit(
                    payload_dict,
                    narration_full=partial.narration_full,
                    allow_from_previous=allow_from,
                    allow_to_next=allow_to,
                    allow_callback=allow_callback,
                )
            )
            cta_errors: list[str] = []
            if series_bridge_active:
                cta_errors = detect_series_bridge_cta_violations(
                    partial.narration_full
                )
            style_errors: list[str] = []
            if style_is_raw:
                style_errors = detect_raw_chapter_style_violations(
                    partial.narration_full,
                    structure=raw_structure,
                    folder_name=folder_name,
                    segments=partial.segments,
                )

            if link_errors or style_errors or cta_errors:
                last_error = " ".join(link_errors + style_errors + cta_errors)
                if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                    return FolderScriptBuildResult(
                        folder_name=folder_name,
                        status="FAIL",
                        error=(
                            "Kapitel-Narration nach Repair weiterhin ungültig: "
                            + last_error
                        ),
                    )
                pause_errs = [
                    err
                    for err in style_errors
                    if "zeitlich markierte Pausen" in err
                    or "author_pause_after_seconds" in err
                ]
                other_style = [
                    err for err in style_errors if err not in pause_errs
                ]
                if pause_errs and not other_style:
                    style_repair = RAW_STYLE_PAUSE_REPAIR_INSTRUCTION
                elif pause_errs and other_style:
                    style_repair = (
                        RAW_STYLE_REPAIR_INSTRUCTION
                        + "\n\n"
                        + RAW_STYLE_PAUSE_REPAIR_INSTRUCTION
                    )
                else:
                    style_repair = RAW_STYLE_REPAIR_INSTRUCTION
                repair_instruction = _compose_script_repair_instruction(
                    link_errors=link_errors,
                    style_errors=style_errors,
                    cta_errors=cta_errors,
                    series_bridge_active=series_bridge_active,
                    style_repair=style_repair if style_errors else "",
                )
                _notify_llm_retry(on_retry, last_error)
                continue

            partial = partial.model_copy(
                update={"source_style_context_hash": style_hash}
            )
            folder_order = [item.folder_name for item in entries]
            merged = merge_folder_script_into_document(
                existing_draft,
                partial,
                folder_name=folder_name,
                folder_order_index=entry.order_index,
                folder_order=folder_order,
            )
            merged = merged.model_copy(
                update={
                    "source_style_context_hash": style_hash,
                    "script_status": "draft",
                }
            )
            save_script_draft(project, merged)
            save_rhetoric_ledger(
                project,
                merge_rhetoric_claims_for_folder(
                    ledger_for_validation,
                    folder_name=folder_name,
                    usage=usage,
                ),
            )
            save_opening_inventory(
                project,
                merge_opening_for_folder(
                    opening_for_validation,
                    folder_name=folder_name,
                    narration_full=partial.narration_full,
                ),
            )
            _invalidate_script_lock(project)
            return FolderScriptBuildResult(
                folder_name=folder_name,
                status="PASS",
                document=merged,
                segment_count=sum(
                    1 for s in merged.segments if s.folder_name == folder_name
                ),
            )
        except PlanLlmCancelledError:
            raise
        except PlanLlmNotConfiguredError as exc:
            return FolderScriptBuildResult(
                folder_name=folder_name,
                status="FAIL",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_llm_cancelled(exc)
            last_error = str(exc)
            if attempt + 1 >= SCRIPT_LLM_ATTEMPTS:
                return FolderScriptBuildResult(
                    folder_name=folder_name,
                    status="FAIL",
                    error=last_error,
                )
            repair_instruction = _generic_script_retry_instruction(last_error)
            _notify_llm_retry(on_retry, last_error)

    return FolderScriptBuildResult(
        folder_name=folder_name,
        status="FAIL",
        error=last_error,
    )


def generate_all_enhanced_scripts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    replace_existing: bool = True,
) -> list[FolderScriptBuildResult]:
    """Erzeugt alle aktiven Dramaturgie-Kapitel sequenziell (Reihenfolge = order_index).

    Mit replace_existing=True (Default) wird der Draft vorher geleert, damit
    entfernte/deaktivierte Kapitel nicht als Orphans liegen bleiben.
    """
    entries = list_enabled_dramaturgy_folders(project)
    if replace_existing and entries:
        save_script_draft(project, EnhancedScriptDocument(script_status="draft"))
        clear_rhetoric_ledger(project)
        clear_opening_inventory(project)
        _invalidate_script_lock(project)
    results: list[FolderScriptBuildResult] = []
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        if progress_callback is not None:
            progress_callback(entry.folder_name, index, total)
        results.append(
            generate_enhanced_script_for_folder(
                project,
                entry.folder_name,
                provider=provider,
                model=model,
                max_output_tokens=max_output_tokens,
                llm_callable=llm_callable,
            )
        )
    return results


def generate_enhanced_script(
    project: Project,
    *,
    llm_callable: Callable[..., Any] | None = None,
    model: str = DEFAULT_ENHANCED_SCRIPT_MODEL,
    provider: str | None = None,
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
) -> EnhancedScriptDocument:
    """Kompatibilitäts-Wrapper: erzeugt alle Kapitel sequenziell und liefert Draft."""
    if provider is None:
        if ":" in model:
            provider, model_name = model.split(":", 1)
        else:
            provider, model_name = "openai", model
    else:
        model_name = model.split(":", 1)[-1] if ":" in model else model

    results = generate_all_enhanced_scripts(
        project,
        provider=provider,
        model=model_name,
        max_output_tokens=max_output_tokens,
        llm_callable=llm_callable,
    )
    if not results:
        raise ValueError("Keine bestätigte Dramaturgie mit aktiven Ordnern.")
    failures = [r for r in results if r.status != "PASS"]
    draft = load_script_draft(project)
    if draft is None or not draft.segments:
        errors = "; ".join(f"{r.folder_name}: {r.error}" for r in failures) or "unbekannt"
        raise RuntimeError(f"Skripterzeugung fehlgeschlagen — {errors}")
    if failures:
        # Teil-Erfolg: Draft behalten, Fehler dem Aufrufer signalisieren
        failed_names = ", ".join(r.folder_name for r in failures)
        draft.forbidden_phrases_found = list(draft.forbidden_phrases_found) + [
            f"PARTIAL_FAIL:{failed_names}"
        ]
    return draft


def looks_like_asset_inventory_script(document: EnhancedScriptDocument) -> bool:
    """Heuristik: Skript wirkt wie Asset-Inventarliste."""
    if document.forbidden_phrases_found:
        # Ignore our own partial-fail markers
        real = [p for p in document.forbidden_phrases_found if not p.startswith("PARTIAL_FAIL:")]
        if real:
            return True
    if not document.segments:
        return True
    if document.visual_intents and len(document.visual_intents) == len(document.segments):
        single = all(len(s.visual_intent_ids) <= 1 for s in document.segments)
        if single and all(
            phrase in document.narration_full.lower()
            for phrase in ("asset", "bild zeigt")
        ):
            return True
    return False
