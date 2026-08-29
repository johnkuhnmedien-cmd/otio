"""Unified Cut Plan — Parse + Kompat-Ableitungen (Phase 1–2)."""

from __future__ import annotations

from typing import Any, Mapping

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


def _demote_slot_to_coverage_gap(
    slot: CutSlot,
    *,
    reason: str,
) -> CutSlot:
    """Asset entfernen und Slot als ehrliche Coverage-Gap markieren."""
    gap_id = (slot.coverage_gap_id or "").strip() or _default_gap_id(slot.slot_id)
    needed = (
        (slot.needed_visual or "").strip()
        or (slot.visual_intent or "").strip()
        or reason
    )
    concepts = [str(c).strip() for c in (slot.search_concepts or []) if str(c).strip()]
    if not concepts:
        seed = (slot.visual_intent or needed or "missing visual").strip()
        concepts = [seed[:40] or "missing visual"]
    prev_reason = (slot.asset_fit_reason or "").strip()
    combined = f"{prev_reason} | {reason}".strip(" |") if prev_reason else reason
    return slot.model_copy(
        update={
            "local_asset_id": None,
            "asset_fit": "none",
            "asset_fit_reason": combined,
            "coverage_gap_id": gap_id,
            "needed_visual": needed,
            "search_concepts": concepts,
        }
    )


def demote_slots_with_unknown_local_assets(
    plan: UnifiedCutPlanDocument,
    known_asset_ids: set[str] | Mapping[str, object],
    *,
    only_asset_ids: set[str] | Mapping[str, object] | None = None,
    reason: str = "Lokale Datei fehlt — Asset aus dem Inventar entfernt.",
) -> tuple[UnifiedCutPlanDocument, list[str]]:
    """Slots mit tot Inventar-IDs werden ehrliche Coverage-Gaps.

    ``only_asset_ids``: wenn gesetzt, nur diese IDs anfassen (Stock-Geister),
    nicht beliebige Test-/Lokal-IDs die der Katalog noch per Dateiname kennt.
    """
    known = {str(key).strip() for key in known_asset_ids if str(key).strip()}
    only: set[str] | None = None
    if only_asset_ids is not None:
        only = {str(key).strip() for key in only_asset_ids if str(key).strip()}
    updated: list[CutSlot] = []
    notes: list[str] = []
    for slot in plan.slots:
        aid = str(slot.local_asset_id or "").strip()
        fit = str(slot.asset_fit or "").strip().lower()
        if not aid or fit in {"weak", "none"}:
            updated.append(slot)
            continue
        if only is not None and aid not in only:
            updated.append(slot)
            continue
        if aid in known:
            updated.append(slot)
            continue
        demoted = _demote_slot_to_coverage_gap(slot, reason=reason)
        updated.append(demoted)
        notes.append(slot.slot_id)

    extra: dict[str, Any] = {}
    if notes:
        extra["slots"] = updated
    closing = str(plan.closing_fallback_asset_id or "").strip()
    if closing and closing not in known and (only is None or closing in only):
        extra["closing_fallback_asset_id"] = None
        notes.append(f"closing_fallback:{closing}")
    if not extra:
        return plan, []
    return plan.model_copy(update=extra), notes


def _reuse_violation_reason(
    asset_id: str,
    *,
    shot_index: int,
    usage: Mapping[str, int],
    last_index: Mapping[str, int],
    max_usage: int,
    min_gap: int,
    reuse_key: str | None = None,
) -> str | None:
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        reuse_identity_key,
    )

    key = reuse_key or reuse_identity_key(asset_id)
    if int(usage.get(key, 0)) >= max_usage:
        return (
            f"Asset {asset_id} überschreitet max_asset_usage={max_usage} "
            "— Coverage-Gap statt Overuse."
        )
    prev = last_index.get(key)
    if prev is None:
        return None
    gap_shots = shot_index - int(prev) - 1
    if gap_shots < min_gap:
        return (
            f"Asset {asset_id} erneut nach {gap_shots} Shots "
            f"(min Abstand {min_gap}) — Coverage-Gap statt Früh-Reuse."
        )
    return None


def enforce_asset_reuse_as_coverage_gaps(
    plan: UnifiedCutPlanDocument,
    *,
    max_asset_usage: int,
    min_asset_reuse_distance_shots: int,
    prior_usage_counts: Mapping[str, int] | None = None,
    prior_editorial_asset_ids: list[str] | None = None,
    intro_asset_ids: set[str] | None = None,
    prefer_closing_fallback: bool = True,
    reuse_key_index: Mapping[str, str] | None = None,
) -> tuple[UnifiedCutPlanDocument, list[str]]:
    """Früh-Reuse / Max-Usage → Coverage-Gap statt stiller Regelverletzung.

    Walkt Slots in Plan-Reihenfolge. Offene Gaps (ohne Asset) zählen als
    Abstand-Trenner, erhöhen aber weder Usage noch last-use. Intro-Assets
    (optional per ID) sind ausgenommen — wie im Timeline-Resolver.

    Am letzten Slot: bei Verletzung zuerst ``closing_fallback_asset_id``
    versuchen (Keyword Flow), sonst Gap.
    """
    max_usage = max(1, int(max_asset_usage))
    # Wie Classic-Selector: Direkt-Reuse ist immer verboten; Setting erhöht.
    min_gap = max(1, int(min_asset_reuse_distance_shots or 0))
    intro_ids = {str(a).strip() for a in (intro_asset_ids or set()) if str(a).strip()}
    closing_fallback = str(plan.closing_fallback_asset_id or "").strip()

    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        reuse_identity_key,
    )

    def _key(raw: str) -> str:
        return reuse_identity_key(raw, index=reuse_key_index)

    usage: dict[str, int] = {}
    for raw_id, count in dict(prior_usage_counts or {}).items():
        key = _key(str(raw_id))
        if key and int(count) > 0:
            usage[key] = usage.get(key, 0) + int(count)
    last_index: dict[str, int] = {}
    shot_index = 0
    notes: list[str] = []

    # Filmweite Vorgänger-Shots seed'en den Abstand (Kapitel vorher).
    for asset_id in prior_editorial_asset_ids or []:
        key = str(asset_id or "").strip()
        if not key or key in intro_ids:
            shot_index += 1
            continue
        last_index[_key(key)] = shot_index
        shot_index += 1

    updated: list[CutSlot] = []
    slot_count = len(plan.slots)
    for slot_pos, slot in enumerate(plan.slots):
        asset_id = str(slot.local_asset_id or "").strip()
        fit = str(slot.asset_fit or "none").strip().lower()
        # Bereits offene Gaps / null: nur Index vorwärts (Trenner).
        if not asset_id or fit == "none":
            updated.append(
                slot
                if not asset_id
                else slot.model_copy(update={"local_asset_id": None})
            )
            shot_index += 1
            continue
        if asset_id in intro_ids:
            updated.append(slot)
            shot_index += 1
            continue

        asset_key = _key(asset_id)
        reason = _reuse_violation_reason(
            asset_id,
            shot_index=shot_index,
            usage=usage,
            last_index=last_index,
            max_usage=max_usage,
            min_gap=min_gap,
            reuse_key=asset_key,
        )
        chosen = slot
        if (
            reason
            and prefer_closing_fallback
            and slot_pos == slot_count - 1
            and closing_fallback
            and closing_fallback != asset_id
            and closing_fallback not in intro_ids
        ):
            fb_key = _key(closing_fallback)
            fb_reason = _reuse_violation_reason(
                closing_fallback,
                shot_index=shot_index,
                usage=usage,
                last_index=last_index,
                max_usage=max_usage,
                min_gap=min_gap,
                reuse_key=fb_key,
            )
            if fb_reason is None:
                fb_fit = str(plan.closing_fallback_asset_fit or "acceptable").strip()
                if fb_fit not in {"strong", "acceptable"}:
                    fb_fit = "acceptable"
                chosen = slot.model_copy(
                    update={
                        "local_asset_id": closing_fallback,
                        "asset_fit": fb_fit,
                        "asset_fit_reason": (
                            f"{(slot.asset_fit_reason or '').strip()} | "
                            f"Closing Primary reuse-illegal — Fallback "
                            f"{closing_fallback}."
                        ).strip(" |"),
                        "coverage_gap_id": None,
                    }
                )
                notes.append(
                    f"{slot.slot_id}: Primary {asset_id} reuse-illegal — "
                    f"Fallback {closing_fallback}."
                )
                reason = None
                asset_id = closing_fallback
                asset_key = fb_key

        if reason:
            demoted = _demote_slot_to_coverage_gap(slot, reason=reason)
            updated.append(demoted)
            notes.append(f"{slot.slot_id}: {reason}")
            shot_index += 1
            continue

        usage[asset_key] = usage.get(asset_key, 0) + 1
        last_index[asset_key] = shot_index
        updated.append(chosen)
        shot_index += 1

    if not notes:
        return plan, []
    return plan.model_copy(update={"slots": updated}), notes


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


def _coerce_boundary_position_alignment(
    *,
    position: str | None,
    alignment: str,
    offset: float | None,
) -> tuple[str | None, str]:
    """Repariert LLM-Verwechslung: alignment-Werte in ``position``.

    Modelle setzen oft ``position: "mid_sentence"`` statt
    ``alignment: "mid_sentence"`` + ``offset_seconds`` / ``position: middle``.
    """
    align = alignment if alignment in CUT_ALIGNMENTS else "sentence_boundary"
    pos = position
    if pos in CUT_ALIGNMENTS:
        # alignment aus dem falschen Feld übernehmen, wenn sinnvoll
        if align == "sentence_boundary" or align not in CUT_ALIGNMENTS:
            align = str(pos)
        # position ist dann kein start|early|… mehr
        pos = None if offset is not None else "middle"
    if pos is not None and pos not in BOUNDARY_POSITIONS:
        raise UnifiedCutPlanError(f"ungültige position {pos!r}.")
    if align not in CUT_ALIGNMENTS:
        align = "sentence_boundary"
    return pos, align


def _parse_boundary(item: dict[str, Any], *, index: int) -> CutBoundary:
    cut_id = str(item.get("cut_id") or f"cut_{index:03d}")
    sentence_id = str(item.get("sentence_id") or "").strip()
    if not sentence_id:
        raise UnifiedCutPlanError(f"{cut_id}: sentence_id fehlt.")
    position_raw = item.get("position")
    position = None if _nullish(position_raw) else str(position_raw).strip().lower()
    offset = _optional_float(item.get("offset_seconds"))
    alignment = str(item.get("alignment") or "sentence_boundary").strip().lower()
    try:
        position, alignment = _coerce_boundary_position_alignment(
            position=position,
            alignment=alignment,
            offset=offset,
        )
    except UnifiedCutPlanError as exc:
        raise UnifiedCutPlanError(f"{cut_id}: {exc}") from exc
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


def _parse_pause_directive(item: dict[str, Any]) -> PauseDirective | None:
    function = str(item.get("pause_function") or "").strip().lower()
    if not function or function == "no_pause":
        return None
    duration = str(item.get("duration_class") or "").strip().lower()
    if not duration:
        raise UnifiedCutPlanError("pause_directive ohne duration_class.")
    try:
        return PauseDirective(
            after_segment_id=str(item.get("after_segment_id") or ""),
            after_sentence_id=(
                None
                if _nullish(item.get("after_sentence_id"))
                else str(item.get("after_sentence_id"))
            ),
            pause_function=function,
            duration_class=duration,
            visual_behavior=str(
                item.get("visual_behavior") or "editorial_choice"
            ),
            editorial_reason=str(item.get("editorial_reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        raise UnifiedCutPlanError(f"pause_directive ungültig: {exc}") from exc


def parse_unified_cut_response(
    raw: str | dict[str, Any],
    script_version: str,
    *,
    folder_slug: str = "",
    allow_pause_directives: bool = False,
    reject_nonempty_pause_directives: bool = False,
    nullify_weak_assets: bool = False,
) -> UnifiedCutPlanDocument:
    """Parst LLM-JSON → UnifiedCutPlanDocument (inkl. optionaler ID-Prefix)."""
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        KEYWORD_FLOW_UNSUPPORTED_PAUSE_EXTENSIONS_MESSAGE,
    )

    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise UnifiedCutPlanError("Unified Cut Plan ist kein JSON-Objekt.")

    # Default: Pause-Directives abgeschaltet (Rhythm / Keyword-Sync / Intro / KF).
    # Keyword Flow: nicht-leere Directives fail-closed (keine stillen Strips).
    directives: list[PauseDirective] = []
    raw_directives = payload.get("pause_directives") or []
    if raw_directives and not isinstance(raw_directives, list):
        raise UnifiedCutPlanError("pause_directives muss ein Array sein.")
    if not isinstance(raw_directives, list):
        raw_directives = []
    parsed_directives: list[PauseDirective] = []
    for item in raw_directives:
        if not isinstance(item, dict):
            continue
        parsed = _parse_pause_directive(item)
        if parsed is not None:
            parsed_directives.append(parsed)
    if parsed_directives and reject_nonempty_pause_directives:
        raise UnifiedCutPlanError(
            KEYWORD_FLOW_UNSUPPORTED_PAUSE_EXTENSIONS_MESSAGE
        )
    if allow_pause_directives:
        directives = parsed_directives

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
        # Keyword Flow: weak darf kein Asset behalten.
        if nullify_weak_assets and fit == "weak":
            slot.local_asset_id = None

    raw_fallback = payload.get("closing_fallback_asset_id")
    closing_fallback = (
        None
        if _nullish(raw_fallback)
        else str(raw_fallback).strip() or None
    )
    raw_fb_fit = payload.get("closing_fallback_asset_fit")
    closing_fallback_fit = (
        None
        if _nullish(raw_fb_fit)
        else str(raw_fb_fit).strip().lower() or None
    )
    closing_fallback_fit_reason = str(
        payload.get("closing_fallback_asset_fit_reason") or ""
    ).strip()
    closing_fallback_visual_intent = str(
        payload.get("closing_fallback_visual_intent") or ""
    ).strip()
    raw_by_chapter = payload.get("closing_fallback_by_chapter") or {}
    fallback_by_chapter: dict[str, str] = {}
    if isinstance(raw_by_chapter, dict):
        for key, value in raw_by_chapter.items():
            chapter = str(key or "").strip()
            asset = None if _nullish(value) else str(value).strip()
            if chapter and asset:
                fallback_by_chapter[chapter] = asset
    raw_opener = payload.get("intro_opener_asset_id")
    intro_opener = (
        None if _nullish(raw_opener) else str(raw_opener).strip() or None
    )
    raw_intro_closing = payload.get("intro_closing_asset_id")
    intro_closing = (
        None
        if _nullish(raw_intro_closing)
        else str(raw_intro_closing).strip() or None
    )

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
            closing_fallback_asset_id=closing_fallback,
            closing_fallback_asset_fit=closing_fallback_fit,
            closing_fallback_asset_fit_reason=closing_fallback_fit_reason,
            closing_fallback_visual_intent=closing_fallback_visual_intent,
            closing_fallback_by_chapter=fallback_by_chapter,
            intro_opener_asset_id=intro_opener,
            intro_closing_asset_id=intro_closing,
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
