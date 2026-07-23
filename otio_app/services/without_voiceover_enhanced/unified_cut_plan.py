"""Unified Cut Plan — Kompat-Ableitungen für Funnel/UI (Phase 1)."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import (
    BOUNDARY_POSITIONS,
    GAP_FIT_VALUES,
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    EditorialAnchor,
    NarrationAnchor,
    RoughCutPlanDocument,
    RoughShot,
    UnifiedCutPlanDocument,
)

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
        needs_gap = fit in GAP_FIT_VALUES
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
        concepts = list(slot.search_concepts) if slot.search_concepts else []
        if not concepts and needed:
            concepts = [needed]
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
            )
        )

    rough = RoughCutPlanDocument(
        script_version=plan.script_version,
        pause_directives=list(plan.pause_directives),
        shots=shots,
    )
    coverage = CoverageGapsDocument(
        script_version=plan.script_version,
        gaps=gaps,
    )
    return rough, coverage
