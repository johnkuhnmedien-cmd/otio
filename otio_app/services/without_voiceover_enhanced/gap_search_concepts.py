"""Fix E2E-2.1: search_concepts = Stock-Keywords, nie Prosa."""

from __future__ import annotations

import re
from typing import Callable

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
)

__all__ = [
    "MAX_CONCEPT_WORDS",
    "enrich_coverage_search_concepts",
    "filter_keyword_concepts",
    "heuristic_stock_concepts",
    "is_prose_search_concept",
    "search_concepts_need_regen",
]

MAX_CONCEPT_WORDS = 6
_SENTENCE_PUNCT = re.compile(r"[.!?;:—–]")


def is_prose_search_concept(text: str) -> bool:
    """True wenn Phrase zu lang ist oder Satzzeichen enthält (kein Stock-Keyword)."""
    raw = (text or "").strip()
    if not raw:
        return True
    if _SENTENCE_PUNCT.search(raw):
        return True
    words = [w for w in re.split(r"\s+", raw) if w]
    return len(words) > MAX_CONCEPT_WORDS


def filter_keyword_concepts(concepts: list[str] | None) -> list[str]:
    """Behält nur kurze, stichwortartige Phrasen."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in concepts or []:
        text = str(raw or "").strip()
        if not text or is_prose_search_concept(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def search_concepts_need_regen(concepts: list[str] | None) -> bool:
    """Leer oder ausschließlich/teils prosahaft → neu generieren."""
    cleaned = filter_keyword_concepts(concepts)
    if not cleaned:
        return True
    # Wenn Originale Prosaphrasen enthielten und nach Filter zu wenig bleibt.
    raw = [str(c).strip() for c in (concepts or []) if str(c).strip()]
    if any(is_prose_search_concept(c) for c in raw) and len(cleaned) < 2:
        return True
    return False


def heuristic_stock_concepts(
    *,
    needed_visual: str,
    folder_name: str = "",
    max_concepts: int = 3,
) -> list[str]:
    """Deterministischer Fallback ohne LLM: kurze EN-ähnliche Stichworte."""
    raw = (needed_visual or "").strip()
    cleaned = _SENTENCE_PUNCT.sub(" ", raw)
    cleaned = re.sub(r"[^\w\s\-]", " ", cleaned, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", cleaned) if w and len(w) > 1][:5]
    if not words:
        words = ["landscape", "detail"]
    location = (folder_name or "").replace("_", " ").strip()
    phrases: list[str] = []
    base = " ".join(words[:4])
    if location:
        phrases.append(f"{location} {base}".strip())
        phrases.append(f"{location} {' '.join(words[:3])}".strip())
    else:
        phrases.append(base)
    if len(words) >= 3:
        phrases.append(" ".join(words[:3]))
    return filter_keyword_concepts(phrases)[:max_concepts] or [base[:40]]


def _folder_for_gap(
    gap: CoverageGap,
    locked: EnhancedScriptDocument | None,
) -> str:
    if locked is None:
        return ""
    related = set(gap.related_shot_ids or [])
    covered = set(gap.covered_sentence_ids or [])
    for segment in locked.segments:
        folder = (segment.folder_name or "").strip()
        if not folder:
            continue
        if any(sid.startswith(segment.segment_id) for sid in covered):
            return folder
        if any(folder.replace(" ", "_") in sid for sid in related):
            return folder
    # Fallback: erstes Segment mit Ordner.
    for segment in locked.segments:
        if (segment.folder_name or "").strip():
            return str(segment.folder_name).strip()
    return ""


def _passage_for_gap(
    gap: CoverageGap,
    locked: EnhancedScriptDocument | None,
) -> str:
    if locked is None:
        return ""
    covered = set(gap.covered_sentence_ids or [])
    parts: list[str] = []
    for segment in locked.segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        if any(sid.startswith(segment.segment_id) for sid in covered):
            parts.append(text)
        if len(parts) >= 2:
            break
    return " ".join(parts)[:500]


def _sync_concepts_to_plan(
    plan: UnifiedCutPlanDocument | None,
    *,
    gap_id: str,
    concepts: list[str],
) -> None:
    if plan is None or not gap_id:
        return
    for slot in plan.slots:
        if (slot.coverage_gap_id or "").strip() == gap_id:
            slot.search_concepts = list(concepts)


def enrich_coverage_search_concepts(
    project: Project,
    coverage: CoverageGapsDocument,
    *,
    plan: UnifiedCutPlanDocument | None = None,
    query_llm: Callable[..., list[str]] | None = None,
) -> CoverageGapsDocument:
    """Ersetzt leere/prosa-hafte search_concepts via cut_plan_supplement_query.

    ``query_llm(gap, folder_name, text) -> list[str]`` ist injizierbar (Tests).
    Bei LLM-Fehler: heuristischer Keyword-Fallback.
    Optional ``plan``: Keywords zurück in die Slots schreiben (kein Re-LLM
    beim nächsten Timing-Persist).
    """
    if coverage is None or not coverage.gaps:
        return coverage

    locked = load_locked_script(project)
    for gap in coverage.gaps:
        if not search_concepts_need_regen(gap.search_concepts):
            keywords = filter_keyword_concepts(gap.search_concepts)
            if keywords:
                gap.search_concepts = keywords
                gap.search_queries = list(keywords)
                _sync_concepts_to_plan(plan, gap_id=gap.gap_id, concepts=keywords)
            continue

        folder = _folder_for_gap(gap, locked)
        passage = _passage_for_gap(gap, locked)
        visual = (gap.needed_visual or gap.subject or "").strip()
        reason = (gap.reason or "").strip()
        queries: list[str] = []

        if query_llm is not None:
            try:
                queries = filter_keyword_concepts(
                    query_llm(gap, folder_name=folder, text=passage)
                )
            except Exception:  # noqa: BLE001
                queries = []
        else:
            queries = _generate_via_supplement_query_role(
                project,
                gap=gap,
                folder_name=folder,
                text=passage,
                visual_intent=visual,
                reason=reason,
            )

        if not queries:
            queries = heuristic_stock_concepts(
                needed_visual=visual or gap.gap_id,
                folder_name=folder,
            )
        gap.search_concepts = queries
        gap.search_queries = list(queries)
        _sync_concepts_to_plan(plan, gap_id=gap.gap_id, concepts=queries)

    return coverage


def _generate_via_supplement_query_role(
    project: Project,
    *,
    gap: CoverageGap,
    folder_name: str,
    text: str,
    visual_intent: str,
    reason: str,
) -> list[str]:
    from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
        CutPlanSupplementRequest,
    )
    from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
        generate_cut_plan_supplement_queries,
    )
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
    )

    settings = load_model_settings(project)
    role = settings.cut_plan_supplement_query
    request = CutPlanSupplementRequest(
        request_id=f"enh_gap_{gap.gap_id}",
        cut_item_id=gap.gap_id,
        folder_name=folder_name or "location",
        text=text or visual_intent,
        visual_intent=visual_intent,
        reason=reason or "coverage gap",
        needed_duration_sec=float(gap.target_duration_seconds or 0.0),
    )
    result = generate_cut_plan_supplement_queries(
        project,
        request,
        provider=role.provider,
        model=role.model,
    )
    return filter_keyword_concepts(list(result.queries or []))
