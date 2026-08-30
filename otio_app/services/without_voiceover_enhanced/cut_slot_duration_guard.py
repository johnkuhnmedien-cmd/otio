"""LLM-Cut: Motion-Videos dürfen nicht kürzer sein als die geplante Slot-Spanne.

Der Prompt sagt das bereits; ohne diese Python-Prüfung bleibt ``asset_fit=strong``
stehen und Timing erzeugt später ``__shortfall``. Stills dürfen halten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from otio_app.services.media_utils import is_image_media, is_video_media
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    LLM_ASSET_DURATION_SAFETY_SEC,
    intro_hold_timings,
    resolve_timing_seconds,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    _demote_slot_to_coverage_gap,
    segment_id_from_sentence_id,
)

TOO_SHORT_ERROR_PREFIX = "Motion video too short for slot span"


@dataclass(frozen=True)
class TooShortMotionAssignment:
    slot_id: str
    asset_id: str
    planning_usable: float
    need_seconds: float
    span_seconds: float
    extra_seconds: float
    reason: str


def catalog_from_prompt_assets(assets: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """``local_asset_id`` / ``asset_id`` → Slim-Zeile aus dem Cut-Prompt."""
    out: dict[str, dict[str, Any]] = {}
    for row in assets or []:
        if not isinstance(row, dict):
            continue
        asset_id = str(row.get("local_asset_id") or row.get("asset_id") or "").strip()
        if asset_id:
            out[asset_id] = row
    return out


def is_still_asset(entry: Mapping[str, Any] | None) -> bool:
    """Foto/Still darf die Slot-Spanne halten; Motion-Video nicht."""
    if not entry:
        return False
    media = str(entry.get("media_type") or entry.get("media_kind") or "").strip().lower()
    if media in {"photo", "image", "still"}:
        return True
    if media in {"video", "movie"}:
        return False
    path_text = str(entry.get("path") or entry.get("file") or "").strip()
    if not path_text:
        return False
    path = Path(path_text)
    if is_image_media(path) and not is_video_media(path):
        return True
    return False


def planning_usable_seconds(
    entry: Mapping[str, Any] | None,
    *,
    head_trim_sec: float = 0.0,
    safety_sec: float = LLM_ASSET_DURATION_SAFETY_SEC,
) -> float | None:
    """Nutzdauer wie im LLM-Prompt: ``duration - max(head_trim, usable_in) - safety``.

    ``None`` = Still, unbekannte Dauer oder kein Motion-Constraint.
    """
    if not entry or is_still_asset(entry):
        return None
    media_duration = entry.get("duration_seconds")
    if media_duration is None or float(media_duration or 0.0) <= 0.0:
        return None
    trim = max(0.0, float(head_trim_sec))
    usable_in = entry.get("usable_in_s")
    if usable_in is not None:
        trim = max(trim, max(0.0, float(usable_in)))
    return max(0.0, float(media_duration) - trim - max(0.0, float(safety_sec)))


def sentence_index_from_timing_rows(
    rows: list[Any] | None,
) -> dict[str, SentenceTiming]:
    """SentenceTiming-Index aus Slim-JSON (Cut-Prompt) oder Alignment-Rows."""
    out: dict[str, SentenceTiming] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        sentence_id = str(row.get("sentence_id") or "").strip()
        if not sentence_id:
            continue
        try:
            start = float(row.get("start_seconds") or 0.0)
            end = float(row.get("end_seconds") or 0.0)
        except (TypeError, ValueError):
            continue
        segment_id = str(row.get("segment_id") or "").strip() or (
            segment_id_from_sentence_id(sentence_id)
        )
        out[sentence_id] = SentenceTiming(
            sentence_id=sentence_id,
            segment_id=segment_id,
            text=str(row.get("text") or ""),
            start_seconds=start,
            end_seconds=end,
            duration_seconds=max(0.0, end - start),
        )
    return out


def chapter_segment_offsets(segments: list[Any] | None) -> dict[str, float]:
    """Kapitel-relative Starts aus Segment-Reihenfolge + ``duration_seconds``."""
    offset = 0.0
    out: dict[str, float] = {}
    for item in segments or []:
        segment_id = str(getattr(item, "segment_id", "") or "").strip()
        if not segment_id and isinstance(item, Mapping):
            segment_id = str(item.get("segment_id") or "").strip()
        duration = getattr(item, "duration_seconds", None)
        if duration is None and isinstance(item, Mapping):
            duration = item.get("duration_seconds")
        if segment_id:
            out[segment_id] = offset
        offset += max(0.0, float(duration or 0.0))
    return out


def _boundary_chapter_seconds(
    boundary: CutBoundary,
    sentence_index: Mapping[str, SentenceTiming],
    segment_offsets: Mapping[str, float],
) -> float | None:
    sentence_id = str(boundary.sentence_id or "").strip()
    if not sentence_id:
        return None
    sentence = sentence_index.get(sentence_id)
    if sentence is None:
        return None
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        boundary_source_offset_seconds,
    )

    source = float(sentence.start_seconds) + boundary_source_offset_seconds(
        boundary, sentence
    )
    segment_id = segment_id_from_sentence_id(sentence_id) or str(
        sentence.segment_id or ""
    )
    base = float(segment_offsets.get(segment_id, 0.0))
    return base + source


def estimate_slot_need_seconds(
    plan: UnifiedCutPlanDocument,
    slot_index: int,
    *,
    sentence_index: Mapping[str, SentenceTiming],
    segment_offsets: Mapping[str, float] | None = None,
    preroll_sec: float = 0.0,
    postroll_sec: float = 0.0,
) -> tuple[float, float] | None:
    """``(need, narration_span)`` inkl. Vorlauf am ersten / Nachlauf am letzten Slot.

    ``None`` wenn Grenzen nicht in Sekunden auflösbar sind.
    """
    if slot_index < 0 or slot_index >= len(plan.slots):
        return None
    if slot_index + 1 >= len(plan.boundaries):
        return None
    offsets = dict(segment_offsets or {})
    start = _boundary_chapter_seconds(
        plan.boundaries[slot_index], sentence_index, offsets
    )
    end = _boundary_chapter_seconds(
        plan.boundaries[slot_index + 1], sentence_index, offsets
    )
    if start is None or end is None:
        return None
    span = max(0.0, float(end) - float(start))
    slot = plan.slots[slot_index]
    if slot.target_duration_seconds is not None:
        span = max(span, float(slot.target_duration_seconds))
    extra = 0.0
    if slot_index == 0:
        extra += max(0.0, float(preroll_sec))
    if slot_index == len(plan.slots) - 1:
        extra += max(0.0, float(postroll_sec))
    return span + extra, span


def too_short_reason(
    assignment: TooShortMotionAssignment,
) -> str:
    extra = ""
    if assignment.extra_seconds > 1e-9:
        extra = (
            f", davon {assignment.extra_seconds:.2f}s Vor-/Nachlauf"
        )
    return (
        f"{TOO_SHORT_ERROR_PREFIX}: {assignment.slot_id} asset "
        f"{assignment.asset_id} planning_usable={assignment.planning_usable:.2f}s "
        f"< need={assignment.need_seconds:.2f}s "
        f"(Narrationsspanne {assignment.span_seconds:.2f}s{extra}). "
        "Kein Video-Hold — anderes (längeres) Asset, kürzerer Slot oder "
        "asset_fit none + coverage_gap."
    )


def collect_too_short_motion_assignments(
    plan: UnifiedCutPlanDocument,
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    sentence_index: Mapping[str, SentenceTiming],
    segment_offsets: Mapping[str, float] | None = None,
    head_trim_sec: float = 0.0,
    short_tolerance_sec: float = 0.0,
    preroll_sec: float = 0.0,
    postroll_sec: float = 0.0,
    safety_sec: float = LLM_ASSET_DURATION_SAFETY_SEC,
) -> list[TooShortMotionAssignment]:
    """Filled motion slots whose planning_usable cannot cover the estimated span."""
    found: list[TooShortMotionAssignment] = []
    last_index = len(plan.slots) - 1
    for index, slot in enumerate(plan.slots):
        asset_id = str(slot.local_asset_id or "").strip()
        fit = str(slot.asset_fit or "").strip().lower()
        if not asset_id or fit in {"none", "weak"}:
            continue
        entry = catalog.get(asset_id)
        if not entry or is_still_asset(entry):
            continue
        usable = planning_usable_seconds(
            entry, head_trim_sec=head_trim_sec, safety_sec=safety_sec
        )
        if usable is None:
            continue
        estimated = estimate_slot_need_seconds(
            plan,
            index,
            sentence_index=sentence_index,
            segment_offsets=segment_offsets,
            preroll_sec=preroll_sec,
            postroll_sec=postroll_sec,
        )
        if estimated is None:
            continue
        need, span = estimated
        extra = 0.0
        if index == 0:
            extra += max(0.0, float(preroll_sec))
        if index == last_index:
            extra += max(0.0, float(postroll_sec))
        allowed = float(usable) + max(0.0, float(short_tolerance_sec))
        if allowed + 1e-9 >= float(need):
            continue
        assignment = TooShortMotionAssignment(
            slot_id=str(slot.slot_id),
            asset_id=asset_id,
            planning_usable=float(usable),
            need_seconds=float(need),
            span_seconds=float(span),
            extra_seconds=float(extra),
            reason="",
        )
        found.append(
            TooShortMotionAssignment(
                slot_id=assignment.slot_id,
                asset_id=assignment.asset_id,
                planning_usable=assignment.planning_usable,
                need_seconds=assignment.need_seconds,
                span_seconds=assignment.span_seconds,
                extra_seconds=assignment.extra_seconds,
                reason=too_short_reason(assignment),
            )
        )
    return found


def collect_too_short_intro_envelope_assignments(
    plan: UnifiedCutPlanDocument,
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    preroll_sec: float,
    postroll_sec: float,
    head_trim_sec: float = 0.0,
    short_tolerance_sec: float = 0.0,
    safety_sec: float = LLM_ASSET_DURATION_SAFETY_SEC,
) -> list[TooShortMotionAssignment]:
    """Intro-Opener/Closing-Hold: Motion muss Vorlauf bzw. Nachlauf tragen."""
    found: list[TooShortMotionAssignment] = []
    checks = (
        ("intro_opener_asset_id", str(plan.intro_opener_asset_id or "").strip(), preroll_sec),
        ("intro_closing_asset_id", str(plan.intro_closing_asset_id or "").strip(), postroll_sec),
    )
    for slot_id, asset_id, need in checks:
        if not asset_id or float(need) <= 1e-9:
            continue
        entry = catalog.get(asset_id)
        if not entry or is_still_asset(entry):
            continue
        usable = planning_usable_seconds(
            entry, head_trim_sec=head_trim_sec, safety_sec=safety_sec
        )
        if usable is None:
            continue
        allowed = float(usable) + max(0.0, float(short_tolerance_sec))
        if allowed + 1e-9 >= float(need):
            continue
        assignment = TooShortMotionAssignment(
            slot_id=slot_id,
            asset_id=asset_id,
            planning_usable=float(usable),
            need_seconds=float(need),
            span_seconds=0.0,
            extra_seconds=float(need),
            reason="",
        )
        found.append(
            TooShortMotionAssignment(
                slot_id=assignment.slot_id,
                asset_id=assignment.asset_id,
                planning_usable=assignment.planning_usable,
                need_seconds=assignment.need_seconds,
                span_seconds=assignment.span_seconds,
                extra_seconds=assignment.extra_seconds,
                reason=too_short_reason(assignment),
            )
        )
    return found


def format_too_short_error(assignments: list[TooShortMotionAssignment]) -> str:
    if not assignments:
        return ""
    return "; ".join(item.reason for item in assignments)


def chapter_edge_rolls(
    plan: UnifiedCutPlanDocument,
    options: CutPlanOptions,
    *,
    is_intro: bool,
) -> tuple[float, float]:
    """Vor-/Nachlauf, der auf First/Last-Content-Slots gelegt wird.

    Intro hat eigene Envelope-Shots — Content-Slots bekommen 0.
    """
    if is_intro:
        return 0.0, 0.0
    preroll = resolve_timing_seconds(
        mode=options.voiceover_preroll_mode,
        setting_max=options.voiceover_preroll_sec,
        llm_value=plan.voiceover_preroll_sec,
    )
    postroll = resolve_timing_seconds(
        mode=options.voiceover_postroll_mode,
        setting_max=options.voiceover_postroll_sec,
        llm_value=plan.voiceover_postroll_sec,
    )
    return float(preroll), float(postroll)


def collect_too_short_for_chapter_cut(
    plan: UnifiedCutPlanDocument,
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    options: CutPlanOptions,
    sentence_index: Mapping[str, SentenceTiming],
    segment_offsets: Mapping[str, float] | None = None,
    is_intro: bool = False,
) -> list[TooShortMotionAssignment]:
    """Alle zu kurzen Motion-Zuweisungen eines Kapitel-LLM-Cuts."""
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tol = max(0.0, float(options.short_asset_tolerance_sec))
    preroll, postroll = chapter_edge_rolls(plan, options, is_intro=is_intro)
    found = collect_too_short_motion_assignments(
        plan,
        catalog,
        sentence_index=sentence_index,
        segment_offsets=segment_offsets,
        head_trim_sec=head_trim,
        short_tolerance_sec=short_tol,
        preroll_sec=preroll,
        postroll_sec=postroll,
    )
    if is_intro:
        intro_preroll, intro_post, _post_min, _post_max = intro_hold_timings(options)
        found.extend(
            collect_too_short_intro_envelope_assignments(
                plan,
                catalog,
                preroll_sec=intro_preroll,
                postroll_sec=intro_post,
                head_trim_sec=head_trim,
                short_tolerance_sec=short_tol,
            )
        )
    return found


def stamp_slot_target_durations(
    plan: UnifiedCutPlanDocument,
    *,
    sentence_index: Mapping[str, SentenceTiming],
    segment_offsets: Mapping[str, float] | None = None,
    preroll_sec: float = 0.0,
    postroll_sec: float = 0.0,
) -> UnifiedCutPlanDocument:
    """Schreibt geschätzte Slot-Dauer (inkl. Kanten-Vor-/Nachlauf) für den Funnel."""
    if not plan.slots:
        return plan
    updated = []
    changed = False
    for index, slot in enumerate(plan.slots):
        estimated = estimate_slot_need_seconds(
            plan,
            index,
            sentence_index=sentence_index,
            segment_offsets=segment_offsets,
            preroll_sec=preroll_sec,
            postroll_sec=postroll_sec,
        )
        if estimated is None:
            updated.append(slot)
            continue
        need, _span = estimated
        current = slot.target_duration_seconds
        if current is not None and abs(float(current) - float(need)) <= 1e-6:
            updated.append(slot)
            continue
        updated.append(
            slot.model_copy(update={"target_duration_seconds": round(float(need), 6)})
        )
        changed = True
    if not changed:
        return plan
    return plan.model_copy(update={"slots": updated})


def demote_too_short_motion_slots(
    plan: UnifiedCutPlanDocument,
    assignments: list[TooShortMotionAssignment],
) -> tuple[UnifiedCutPlanDocument, list[str]]:
    """Zu kurze Motion-Zuweisungen → ehrliche Coverage-Gaps (kein strong mehr)."""
    by_slot = {
        item.slot_id: item
        for item in assignments
        if item.slot_id not in {"intro_opener_asset_id", "intro_closing_asset_id"}
    }
    if not by_slot:
        return plan, []
    updated = []
    notes: list[str] = []
    for slot in plan.slots:
        assignment = by_slot.get(str(slot.slot_id))
        if assignment is None:
            updated.append(slot)
            continue
        demoted = _demote_slot_to_coverage_gap(slot, reason=assignment.reason)
        demoted = demoted.model_copy(
            update={"target_duration_seconds": round(float(assignment.need_seconds), 6)}
        )
        updated.append(demoted)
        notes.append(str(slot.slot_id))
    extra: dict[str, Any] = {"slots": updated}
    return plan.model_copy(update=extra), notes
