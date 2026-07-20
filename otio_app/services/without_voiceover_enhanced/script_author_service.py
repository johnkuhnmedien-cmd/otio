"""LLM-Lauf 1: Skripterzeugung für without_voiceover_enhanced.

Standard: ein Call pro Dramaturgie-Kapitel (wie klassische Folder-VOs),
Ergebnisse werden in ein EnhancedScriptDocument gemerged.
"""

from __future__ import annotations

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
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_reference_service import (
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
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
)

DEFAULT_ENHANCED_SCRIPT_MODEL = "openai:gpt-5.4-mini"
DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS = 32_768


@dataclass
class FolderScriptBuildResult:
    folder_name: str
    status: str  # PASS | FAIL
    document: EnhancedScriptDocument | None = None
    error: str | None = None
    segment_count: int = 0


def _brief_text(project: Project) -> str:
    brief = load_project_brief(project)
    if brief is None:
        return "(kein Project Brief)"
    return brief.model_dump_json(indent=2)


def _style_text(project: Project) -> str:
    return style_context_text_for_prompts(project, detailed=True)


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


def _asset_inventory_summary_for_folder(project: Project, folder_name: str) -> str:
    inventory = load_folder_inventory(project, folder_name)
    if inventory is None:
        return f"- {folder_name}: (kein Inventory — visual resource only)"
    asset_count = len(getattr(inventory, "assets", []) or [])
    return f"- {folder_name}: {asset_count} assets (visual resource only)"


def _asset_inventory_summary(project: Project) -> str:
    lines = [
        _asset_inventory_summary_for_folder(project, folder)
        for folder in project.selected_asset_subdirs
    ]
    return "\n".join(lines) if lines else "(keine lokalen Assets inventarisiert)"


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
        f"narrative_arc: {plan.narrative_arc or '-'}\n"
        f"global_transition_strategy: {plan.global_transition_strategy or '-'}"
    )


def _chapter_dramaturgy_text(entry: DramaturgyFolderEntry) -> str:
    return (
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
        narration = " ".join(s.text for s in segments)

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

    return EnhancedScriptDocument(
        narration_full=narration,
        segments=segments,
        visual_beats=beats,
        visual_intents=intents,
        coverage_needs=needs,
        fact_check_hints=hints,
        forbidden_phrases_found=detect_forbidden_phrases(narration),
        script_status="draft",
    )


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
    document.narration_full = " ".join(seg.text for seg in segments)
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
    )
    return _resequence_document(merged, folder_order)


def folders_present_in_script(document: EnhancedScriptDocument | None) -> set[str]:
    if document is None:
        return set()
    return {seg.folder_name for seg in document.segments if seg.folder_name}


def generate_enhanced_script_for_folder(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    max_output_tokens: int | None = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    llm_callable: Callable[..., Any] | None = None,
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

    prompt = build_enhanced_folder_script_prompt(
        project_brief_text=_brief_text(project),
        film_context_text=_film_context_text(plan),
        chapter_dramaturgy_text=_chapter_dramaturgy_text(entry),
        style_profile_text=_style_text(project),
        verified_facts_text=_verified_facts_text(project),
        asset_inventory_summary=_asset_inventory_summary_for_folder(project, folder_name),
        folder_name=folder_name,
        folder_slug=folder_slug,
        dramaturgy_role=entry.dramaturgy_role,
        target_words=target,
        min_words=min_words,
        max_words=max_words,
        previous_folder_name=previous_name,
        next_folder_name=next_name,
        language=project.language,
    )

    model_id = resolve_llm_model_id(provider, model)
    try:
        if llm_callable is not None:
            raw = llm_callable(
                prompt=prompt,
                model=model_id,
                max_output_tokens=max_output_tokens,
            )
            raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
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
            return FolderScriptBuildResult(
                folder_name=folder_name,
                status="FAIL",
                error="LLM-Antwort enthielt keine Segmente.",
            )
        folder_order = [item.folder_name for item in entries]
        existing = load_script_draft(project)
        merged = merge_folder_script_into_document(
            existing,
            partial,
            folder_name=folder_name,
            folder_order_index=entry.order_index,
            folder_order=folder_order,
        )
        save_script_draft(project, merged)
        _invalidate_script_lock(project)
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="PASS",
            document=merged,
            segment_count=sum(1 for s in merged.segments if s.folder_name == folder_name),
        )
    except Exception as exc:  # noqa: BLE001
        return FolderScriptBuildResult(
            folder_name=folder_name,
            status="FAIL",
            error=str(exc),
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
