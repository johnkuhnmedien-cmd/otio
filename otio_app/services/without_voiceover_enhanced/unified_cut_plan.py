"""Unified Cut Plan — Parse + Kompat-Ableitungen (Phase 1–2)."""

from __future__ import annotations

from typing import Any

from otio_app.services.gemini_client import _extract_json
from otio_app.services.without_voiceover_enhanced.models import (
    BOUNDARY_POSITIONS,
    CUT_ALIGNMENTS,
    GAP_FIT_VALUES,
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    EditorialAnchor,
    NarrationAnchor,
    PauseDirective,
    RoughCutPlanDocument,
    RoughShot,
    UnifiedCutPlanDocument,
)


class UnifiedCutPlanError(ValueError):
    """Ungültige Unified-Cut-Plan-Antwort."""


_POSITION_FRACTION = {
    "start": 0.0,
    "early": 0.25,
    "middle": 0.5,
    "late": 0.75,
    "end": 1.0,
}


def segment_id_from_sentence_id(sentence_id: str) -> str:
    """``Folder_segment_001__s003`` → ``Folder_segment_001``."""
    text = (sentence_id or "").strip()
    if "__s" in text:
        return text.rsplit("__s", 1)[0]
    return text


def _boundary_position(boundary: CutBoundary) -> str:
    if boundary.position in BOUNDARY_POSITIONS:
        return str(boundary.position)
    if boundary.offset_seconds is None:
        return "start"
    # Ohne Satzdauer nur grob (Resolver nutzt später offset_seconds).
    return "start" if float(boundary.offset_seconds) <= 0.0 else "middle"


def _boundary_to_editorial_anchor(boundary: CutBoundary) -> EditorialAnchor:
    sentence_id = str(boundary.sentence_id or "").strip()
    return EditorialAnchor(
        type="sentence",
        segment_id=segment_id_from_sentence_id(sentence_id),
        sentence_id=sentence_id or None,
        position=_boundary_position(boundary),
    )


def _boundary_to_narration_anchor(boundary: CutBoundary) -> NarrationAnchor:
    """Bridge: offset gewinnt; sonst Positions-Fraction als offset_seconds."""
    sentence_id = str(boundary.sentence_id or "").strip()
    segment_id = segment_id_from_sentence_id(sentence_id)
    if boundary.offset_seconds is not None:
        offset = max(0.0, float(boundary.offset_seconds))
    else:
        offset = float(_POSITION_FRACTION.get(_boundary_position(boundary), 0.0))
    return NarrationAnchor(
        segment_id=segment_id,
        offset_seconds=offset,
        sentence_id=sentence_id or None,
    )


def _default_gap_id(slot_id: str) -> str:
    return f"gap_{slot_id}"


def _covered_sentence_ids(
    start: CutBoundary,
    end: CutBoundary,
    explicit: list[str],
) -> list[str]:
    if explicit:
        return list(dict.fromkeys(str(s) for s in explicit if str(s).strip()))
    ordered: list[str] = []
    for sid in (start.sentence_id, end.sentence_id):
        text = str(sid or "").strip()
        if text and text not in ordered:
            ordered.append(text)
    return ordered


def _nullish(value: Any) -> bool:
    return value in (None, "", "null")


def _optional_float(value: Any) -> float | None:
    if _nullish(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_folder_prefix(raw_id: str, folder_slug: str, kind: str, index: int) -> str:
    text = (raw_id or "").strip() or f"{kind}_{index:03d}"
    slug = (folder_slug or "").strip()
    if not slug:
        return text
    prefix = f"{slug}_"
    if text.startswith(prefix):
        return text
    if text.startswith(slug):
        return text
    return f"{prefix}{text}"


def _parse_boundary(item: dict[str, Any], *, index: int) -> CutBoundary:
    cut_id = str(item.get("cut_id") or f"cut_{index:03d}")
    sentence_id = str(item.get("sentence_id") or "").strip()
    if not sentence_id:
        raise UnifiedCutPlanError(f"{cut_id}: sentence_id fehlt.")
    position_raw = item.get("position")
    position = None if _nullish(position_raw) else str(position_raw).strip().lower()
    if position is not None and position not in BOUNDARY_POSITIONS:
        raise UnifiedCutPlanError(
            f"{cut_id}: ungültige position {position_raw!r}."
        )
    offset = _optional_float(item.get("offset_seconds"))
    alignment = str(item.get("alignment") or "sentence_boundary").strip().lower()
    if alignment not in CUT_ALIGNMENTS:
        alignment = "sentence_boundary"
    try:
        return CutBoundary(
            cut_id=cut_id,
            sentence_id=sentence_id,
            position=position,  # type: ignore[arg-type]
            offset_seconds=offset,
            alignment=alignment,  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001
        raise UnifiedCutPlanError(f"{cut_id}: {exc}") from exc


def _parse_slot(item: dict[str, Any], *, index: int) -> CutSlot:
    slot_id = str(item.get("slot_id") or f"slot_{index:03d}")
    local_raw = item.get("local_asset_id", item.get("asset_id"))
    local_asset_id = None if _nullish(local_raw) else str(local_raw)
    fit = str(item.get("asset_fit") or ("none" if local_asset_id is None else "acceptable"))
    fit = fit.strip().lower()
    gap_raw = item.get("coverage_gap_id")
    coverage_gap_id = None if _nullish(gap_raw) else str(gap_raw)
    concepts = item.get("search_concepts") or []
    if not isinstance(concepts, list):
        concepts = [str(concepts)]
    must_include = item.get("must_include") or []
    must_avoid = item.get("must_avoid") or []
    covered = item.get("covered_sentence_ids") or []
    try:
        return CutSlot(
            slot_id=slot_id,
            local_asset_id=local_asset_id,
            asset_fit=fit,  # type: ignore[arg-type]
            asset_fit_reason=str(
                item.get("asset_fit_reason") or item.get("editorial_reason") or ""
            ),
            visual_intent=str(item.get("visual_intent") or ""),
            narrative_function=str(
                item.get("narrative_function")
                or item.get("editorial_function")
                or "orientation"
            ),
            coverage_gap_id=coverage_gap_id,
            source_range_intent=str(
                item.get("source_range_intent") or "representative_middle_section"
            ),
            needed_visual=str(item.get("needed_visual") or ""),
            search_concepts=[str(c) for c in concepts if not _nullish(c)],
            must_include=[str(c) for c in must_include if not _nullish(c)],
            must_avoid=[str(c) for c in must_avoid if not _nullish(c)],
            desired_motion=str(item.get("desired_motion") or ""),
            desired_framing=str(item.get("desired_framing") or ""),
            preferred_media_type=str(item.get("preferred_media_type") or "video"),
            fact_check_required=bool(item.get("fact_check_required") or False),
            covered_sentence_ids=[str(c) for c in covered if not _nullish(c)],
            target_duration_seconds=_optional_float(item.get("target_duration_seconds")),
        )
    except Exception as exc:  # noqa: BLE001
        raise UnifiedCutPlanError(f"{slot_id}: {exc}") from exc


def parse_unified_cut_response(
    raw: str | dict[str, Any],
    script_version: str,
    *,
    folder_slug: str = "",
) -> UnifiedCutPlanDocument:
    """Parst LLM-JSON → UnifiedCutPlanDocument (inkl. optionaler ID-Prefix)."""
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise UnifiedCutPlanError("Unified Cut Plan ist kein JSON-Objekt.")

    directives: list[PauseDirective] = []
    for item in payload.get("pause_directives") or []:
        if not isinstance(item, dict):
            continue
        after_segment = str(item.get("after_segment_id") or "")
        after_sentence_raw = item.get("after_sentence_id")
        after_sentence = (
            None if _nullish(after_sentence_raw) else str(after_sentence_raw)
        )
        if not after_segment and not after_sentence:
            continue
        directives.append(
            PauseDirective(
                after_segment_id=after_segment,
                after_sentence_id=after_sentence,
                pause_function=str(item.get("pause_function") or "breath"),
                duration_class=str(item.get("duration_class") or "medium"),
                visual_behavior=str(
                    item.get("visual_behavior") or "editorial_choice"
                ),
                editorial_reason=str(item.get("editorial_reason") or ""),
            )
        )

    boundaries_raw = payload.get("boundaries") or []
    slots_raw = payload.get("slots") or []
    if not isinstance(boundaries_raw, list) or not isinstance(slots_raw, list):
        raise UnifiedCutPlanError("boundaries/slots müssen Arrays sein.")

    boundaries = [
        _parse_boundary(item, index=i)
        for i, item in enumerate(boundaries_raw, start=0)
        if isinstance(item, dict)
    ]
    slots = [
        _parse_slot(item, index=i)
        for i, item in enumerate(slots_raw, start=1)
        if isinstance(item, dict)
    ]

    if folder_slug:
        for index, boundary in enumerate(boundaries):
            boundary.cut_id = _with_folder_prefix(
                boundary.cut_id, folder_slug, "cut", index
            )
        for index, slot in enumerate(slots, start=1):
            slot.slot_id = _with_folder_prefix(
                slot.slot_id, folder_slug, "slot", index
            )
            if slot.coverage_gap_id:
                slot.coverage_gap_id = _with_folder_prefix(
                    slot.coverage_gap_id, folder_slug, "gap", index
                )

    # Gap-IDs für weak/none nachziehen, falls Modell sie wegließ.
    for slot in slots:
        fit = str(slot.asset_fit or "none")
        if fit in GAP_FIT_VALUES and not slot.coverage_gap_id:
            slot.coverage_gap_id = _default_gap_id(slot.slot_id)
        if fit in {"strong", "acceptable"}:
            slot.coverage_gap_id = None
        if fit == "none":
            slot.local_asset_id = None

    try:
        return UnifiedCutPlanDocument(
            script_version=script_version,
            pause_directives=directives,
            boundaries=boundaries,
            slots=slots,
            voiceover_preroll_sec=_optional_float(payload.get("voiceover_preroll_sec")),
            voiceover_postroll_sec=_optional_float(
                payload.get("voiceover_postroll_sec")
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise UnifiedCutPlanError(str(exc)) from exc


def unified_to_rough(
    plan: UnifiedCutPlanDocument,
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    """Leitet RoughCut + CoverageGaps ab (Funnel/UI-Kompat, unveränderte Pfade).

    Gaps nur für Slots mit ``asset_fit`` in {weak, none}.
    """
    shots: list[RoughShot] = []
    gaps: list[CoverageGap] = []

    for index, slot in enumerate(plan.slots):
        start_b = plan.boundaries[index]
        end_b = plan.boundaries[index + 1]
        start_anchor = _boundary_to_editorial_anchor(start_b)
        end_anchor = _boundary_to_editorial_anchor(end_b)
        fit = str(slot.asset_fit or "none").strip().lower()
        is_bridge = (
            str(slot.slot_id).startswith("bridge_")
            or str(slot.narrative_function or "").strip().lower()
            == "chapter_transition"
        )
        # Bridges nie in den Funnel (Entscheidung 13 / Fix 3).
        needs_gap = (fit in GAP_FIT_VALUES) and not is_bridge
        gap_id = None
        if needs_gap:
            gap_id = (slot.coverage_gap_id or "").strip() or _default_gap_id(slot.slot_id)

        shots.append(
            RoughShot(
                shot_id=slot.slot_id,
                start_anchor=start_anchor,
                end_anchor=end_anchor,
                narrative_function=slot.narrative_function or "orientation",
                visual_intent=slot.visual_intent or "",
                local_asset_id=slot.local_asset_id,
                asset_fit=fit if fit in {"strong", "acceptable", "weak", "none"} else "none",
                asset_fit_reason=slot.asset_fit_reason or "",
                coverage_gap_id=gap_id,
                start_cut_alignment=start_b.alignment,
                narration_start_anchor=_boundary_to_narration_anchor(start_b),
                narration_end_anchor=_boundary_to_narration_anchor(end_b),
                asset_id=slot.local_asset_id,
                editorial_function=slot.narrative_function or "orientation",
                editorial_reason=slot.asset_fit_reason or "",
            )
        )

        if not needs_gap or not gap_id:
            continue

        needed = (slot.needed_visual or slot.visual_intent or slot.slot_id).strip()
        # needed_visual bleibt Prosa; search_concepts nur Keywords (E2E-2.1).
        # Prosa/leere Listen werden beim Persist via cut_plan_supplement_query gefüllt.
        from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
            filter_keyword_concepts,
        )

        concepts = filter_keyword_concepts(list(slot.search_concepts or []))
        covered = _covered_sentence_ids(
            start_b, end_b, list(slot.covered_sentence_ids or [])
        )
        priority = "high" if fit == "none" else "medium"
        reason = slot.asset_fit_reason or (
            "Kein geeignetes lokales Asset"
            if fit == "none"
            else "Lokales Asset nur schwach geeignet — Upgrade-Gap"
        )
        if slot.target_duration_seconds is not None:
            reason = (
                f"{reason} · Ziel-Dauer ≥ {float(slot.target_duration_seconds):.2f}s"
            ).strip(" ·")

        gaps.append(
            CoverageGap(
                gap_id=gap_id,
                related_shot_ids=[slot.slot_id],
                needed_visual=needed,
                editorial_purpose=slot.narrative_function or "orientation",
                preferred_media_type=slot.preferred_media_type or "video",
                search_concepts=concepts,
                search_queries=list(concepts),
                must_include=list(slot.must_include or []),
                must_avoid=list(slot.must_avoid or []),
                fact_check_required=bool(slot.fact_check_required),
                covered_sentence_ids=covered,
                desired_motion=slot.desired_motion or "",
                desired_framing=slot.desired_framing or "",
                subject=needed,
                editorial_function=slot.narrative_function or "orientation",
                priority=priority,
                reason=reason,
                target_duration_seconds=slot.target_duration_seconds,
            )
        )

    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id,
    )

    rough = RoughCutPlanDocument(
        script_version=plan.script_version,
        pause_directives=list(plan.pause_directives),
        shots=shots,
    )
    coverage = CoverageGapsDocument(
        script_version=plan.script_version,
        cut_plan_run_id=compute_cut_plan_run_id(plan),
        gaps=gaps,
    )
    return rough, coverage
