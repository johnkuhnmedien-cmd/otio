"""Phase 3: Unified Cut Plan → deterministische Timeline (ohne LLM 3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    load_cut_plan_options,
    resolve_timing_seconds,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
    assess_unified_cut_quality,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    BOUNDARY_POSITIONS,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    ResolvedShot,
    ResolvedTimelineDocument,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    final_cut_plan_path,
    narration_timeline_path,
    repair_log_path,
    resolved_timeline_path,
    rough_cut_plan_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
    source_seconds_to_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    sentence_index_by_id,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TECH_MAX_SHOT_SECONDS,
    TECH_MIN_SHOT_SECONDS,
    AssetCatalog,
    TimelineResolveError,
    _apply_chapter_envelopes,
    _apply_visual_continuity_rules,
    _build_resolved_audio_segments,
    _count_chapter_continuity,
    _is_intro_folder,
    _resolve_shot_media,
    _seconds_to_frame,
    _segment_to_chapter_map,
    build_asset_catalog,
    lookup_catalog_entry,
)
from otio_app.services.without_voiceover_enhanced.local_media_service import is_http_url
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    segment_id_from_sentence_id,
    unified_to_rough,
)

from collections import Counter


class UnifiedTimelineError(RuntimeError):
    """Fehler beim Unified-Timing-Resolver."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = list(errors or [])


POSITION_FRACTION = {
    "start": 0.0,
    "early": 0.25,
    "middle": 0.5,
    "late": 0.75,
    "end": 1.0,
}

# Mid-sentence / innere Positionen: Mindestabstand zu Satzrändern.
EDGE_MARGIN_SECONDS = 0.4


@dataclass
class TimedSlot:
    """Slot mit absoluten VO-Zeiten (vor Kapitel-Hülle)."""

    slot_id: str
    start_seconds: float
    end_seconds: float
    start_boundary_id: str
    end_boundary_id: str
    cut_alignment: str
    asset_id: str | None
    asset_fit: str
    asset_fit_reason: str
    coverage_gap_id: str | None
    narrative_function: str
    source_range_intent: str
    visual_intent: str
    needed_visual: str = ""
    start_sentence_id: str = ""
    end_sentence_id: str = ""
    start_segment_id: str = ""
    end_segment_id: str = ""

    @property
    def duration_seconds(self) -> float:
        return max(0.0, float(self.end_seconds) - float(self.start_seconds))

    @property
    def is_open_gap(self) -> bool:
        fit = str(self.asset_fit or "").strip().lower()
        if fit == "none":
            return True
        return not bool(self.asset_id)


def boundary_source_offset_seconds(
    boundary,
    sentence: SentenceTiming,
    *,
    alignment: str | None = None,
) -> float:
    """Satzrelativer Offset: ``offset_seconds`` gewinnt, sonst position×Satzdauer.

    Innere Positionen halten ``EDGE_MARGIN_SECONDS`` Abstand zu den Rändern,
    sofern der Satz lang genug ist.
    """
    span = max(0.0, float(sentence.end_seconds) - float(sentence.start_seconds))
    if boundary.offset_seconds is not None:
        return max(0.0, min(float(boundary.offset_seconds), span))

    position = str(boundary.position or "start").strip().lower()
    if position not in BOUNDARY_POSITIONS:
        position = "start"
    frac = float(POSITION_FRACTION.get(position, 0.0))
    offset = span * frac

    align = (alignment or boundary.alignment or "").strip().lower()
    if (
        align == "mid_sentence" or position in {"early", "middle", "late"}
    ) and span > 2.0 * EDGE_MARGIN_SECONDS:
        offset = max(EDGE_MARGIN_SECONDS, min(offset, span - EDGE_MARGIN_SECONDS))
    return max(0.0, min(offset, span))


def boundary_to_absolute_seconds(
    boundary,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming],
    fps: float = 25.0,
) -> float:
    """CutBoundary → absolute Timeline-Sekunden (Frame-gerundet)."""
    sentence_id = str(boundary.sentence_id or "").strip()
    if not sentence_id:
        raise UnifiedTimelineError(f"{boundary.cut_id}: sentence_id fehlt.")
    sentence = sentence_index.get(sentence_id)
    if sentence is None:
        raise UnifiedTimelineError(f"{boundary.cut_id}: unbekannte Sentence-ID {sentence_id}.")

    segment_id = segment_id_from_sentence_id(sentence_id) or sentence.segment_id
    entry_map = {entry.segment_id: entry for entry in timeline.entries}
    entry = entry_map.get(segment_id)
    if entry is None:
        raise UnifiedTimelineError(
            f"{boundary.cut_id}: Segment {segment_id} fehlt in Narration-Timeline."
        )

    source = float(sentence.start_seconds) + boundary_source_offset_seconds(
        boundary, sentence
    )
    absolute = source_seconds_to_timeline(entry, source)
    return _seconds_to_frame(absolute, fps)


def boundary_to_narration_anchor(
    boundary,
    *,
    sentence_index: dict[str, SentenceTiming],
) -> NarrationAnchor:
    """Echter satzrelativer Offset für Final-Shadow / ``_anchor_to_seconds``."""
    sentence_id = str(boundary.sentence_id or "").strip()
    sentence = sentence_index.get(sentence_id)
    if sentence is None:
        # Fallback: Roh-Offset oder 0 — Caller sollte vorher validieren.
        offset = float(boundary.offset_seconds or 0.0)
        return NarrationAnchor(
            segment_id=segment_id_from_sentence_id(sentence_id),
            offset_seconds=max(0.0, offset),
            sentence_id=sentence_id or None,
        )
    offset = boundary_source_offset_seconds(boundary, sentence)
    return NarrationAnchor(
        segment_id=sentence.segment_id,
        offset_seconds=round(offset, 6),
        sentence_id=sentence_id,
    )


def usable_media_duration_seconds(
    entry: dict,
    *,
    head_trim: float = 0.0,
) -> float | None:
    """Nutzbare Motion-Video-Dauer; ``None`` = kein Media-Constraint (Still/unbekannt)."""
    media_kind = str(entry.get("media_kind") or entry.get("media_type") or "").lower()
    if media_kind in {"image", "photo"}:
        return None
    media_duration = entry.get("duration_seconds")
    if media_duration is None or float(media_duration or 0.0) <= 0.0:
        return None
    trim = max(0.0, float(head_trim))
    usable_in = entry.get("usable_in_s")
    if usable_in is not None:
        trim = max(trim, max(0.0, float(usable_in)))
    return max(0.0, float(media_duration) - trim)


def _slot_usable_max_from_catalog(
    plan: UnifiedCutPlanDocument,
    catalog: AssetCatalog | None,
    *,
    head_trim: float,
) -> list[float | None]:
    if catalog is None:
        return [None] * len(plan.slots)
    out: list[float | None] = []
    for slot in plan.slots:
        fit = str(slot.asset_fit or "").strip().lower()
        asset_id = slot.local_asset_id
        if fit == "none" or not asset_id:
            out.append(None)
            continue
        entry, _err = lookup_catalog_entry(catalog, str(asset_id))
        if entry is None:
            out.append(None)
            continue
        out.append(usable_media_duration_seconds(entry, head_trim=head_trim))
    return out


def _clamp_boundary_times(
    times: list[float],
    *,
    editorial_min: float,
    editorial_max: float,
    repairs: list[str],
    slot_usable_max: list[float | None] | None = None,
    short_tolerance: float = 0.0,
    max_media_iterations: int = 2,
) -> list[float]:
    """Shot min/max + nutzbare Asset-Dauer; gemeinsame Grenzen, Kette bleibt dicht.

    Media-Regel (pro Slot mit usable):
    - span > usable + tolerance → nicht klemmen (später is_short/Gap)
    - usable < span <= usable + tolerance → Endgrenze nach vorne (Folge-Slot länger)
    Max. ``max_media_iterations`` Links-nach-rechts-Pässe; danach noch
    innerhalb-Toleranz verletzt → Fehler.
    """
    if len(times) < 2:
        return times
    out = [float(t) for t in times]
    n_slots = len(out) - 1
    usables = list(slot_usable_max or [None] * n_slots)
    if len(usables) < n_slots:
        usables.extend([None] * (n_slots - len(usables)))

    def _editorial_pass() -> None:
        # Zu lang: Ende nach vorne (spätere Slots werden länger).
        for index in range(n_slots):
            duration = out[index + 1] - out[index]
            if duration > editorial_max + 1e-9:
                out[index + 1] = out[index] + editorial_max
                repairs.append(
                    f"slot[{index}]: über shot_max ({editorial_max:.2f}s) — "
                    "Endgrenze nach vorne verschoben."
                )

        # Zu kurz: Ende nach hinten + Cascade (spätere Dauern bleiben gleich).
        for index in range(n_slots):
            duration = out[index + 1] - out[index]
            if duration + 1e-9 >= editorial_min:
                continue
            need = editorial_min - duration
            out[index + 1] += need
            for later in range(index + 2, len(out)):
                out[later] += need
            repairs.append(
                f"slot[{index}]: unter shot_min ({editorial_min:.2f}s) — "
                f"Endgrenze +{need:.2f}s (Cascade)."
            )

    _editorial_pass()

    tol = max(0.0, float(short_tolerance))
    for _iteration in range(max(1, int(max_media_iterations))):
        changed = False
        for index in range(n_slots):
            usable = usables[index]
            if usable is None:
                continue
            duration = out[index + 1] - out[index]
            if duration <= float(usable) + 1e-9:
                continue
            shortfall = duration - float(usable)
            if shortfall > tol + 1e-9:
                # Über Toleranz: Grenzen unverändert → is_short/Gap-Pfad.
                continue
            # Innerhalb Toleranz: gemeinsame Endgrenze nach vorne.
            out[index + 1] = out[index] + float(usable)
            repairs.append(
                f"slot[{index}]: nutzbare Dauer knapp "
                f"(span {duration:.2f}s → usable {float(usable):.2f}s, "
                f"shortfall {shortfall:.2f}s ≤ Toleranz {tol:.1f}s) — "
                "Endgrenze nach vorne (Folge-Slot länger)."
            )
            changed = True
        if changed:
            # Folge-Slot kann editorial_max überschreiten.
            _editorial_pass()
        if not changed:
            break

    remaining_tol_violations: list[str] = []
    for index in range(n_slots):
        usable = usables[index]
        if usable is None:
            continue
        duration = out[index + 1] - out[index]
        if duration <= float(usable) + 1e-9:
            continue
        shortfall = duration - float(usable)
        if shortfall <= tol + 1e-9:
            remaining_tol_violations.append(
                f"slot[{index}]: span {duration:.2f}s > usable {float(usable):.2f}s "
                f"(shortfall {shortfall:.2f}s innerhalb Toleranz {tol:.1f}s) "
                "nach Grenzen-Klemme nicht stabil."
            )
    if remaining_tol_violations:
        raise UnifiedTimelineError(
            "\n".join(remaining_tol_violations),
            errors=remaining_tol_violations,
        )
    return out


def assert_timed_slots_contiguous(
    timed_slots: list[TimedSlot],
    *,
    fps: float = 25.0,
) -> None:
    """Invariante Fix 1.3: Summe Clip-Dauern == Spanne; keine Lücke > 1 Frame."""
    if len(timed_slots) < 1:
        return
    frame = 1.0 / max(1.0, float(fps))
    total = sum(slot.duration_seconds for slot in timed_slots)
    span = float(timed_slots[-1].end_seconds) - float(timed_slots[0].start_seconds)
    if abs(total - span) > frame + 1e-9:
        raise UnifiedTimelineError(
            f"Grenzen-Kette inkonsistent: Summe Dauern {total:.6f}s ≠ "
            f"Spanne {span:.6f}s (Frame-Toleranz {frame:.4f}s)."
        )
    for prev, curr in zip(timed_slots, timed_slots[1:]):
        gap = float(curr.start_seconds) - float(prev.end_seconds)
        if gap > frame + 1e-9:
            raise UnifiedTimelineError(
                f"Timeline-Lücke {prev.slot_id}→{curr.slot_id}: "
                f"{prev.end_seconds:.3f}s–{curr.start_seconds:.3f}s "
                f"({gap:.3f}s > 1 Frame)."
            )
        if gap < -(frame + 1e-9):
            raise UnifiedTimelineError(
                f"Timeline-Overlap {prev.slot_id}→{curr.slot_id}: "
                f"{gap:.3f}s."
            )


def resolve_timed_slots(
    plan: UnifiedCutPlanDocument,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming],
    options: CutPlanOptions,
    fps: float = 25.0,
    repairs: list[str] | None = None,
    catalog: AssetCatalog | None = None,
    slot_usable_max: list[float | None] | None = None,
) -> list[TimedSlot]:
    """Grenzen-Kette → TimedSlots (VO-absolut, vor Kapitel-Hülle)."""
    notes = repairs if repairs is not None else []
    if not plan.slots:
        return []

    raw_times: list[float] = []
    for boundary in plan.boundaries:
        raw_times.append(
            boundary_to_absolute_seconds(
                boundary,
                timeline,
                sentence_index=sentence_index,
                fps=fps,
            )
        )

    # Monotonie erzwingen (Fail-soft: nach vorne klemmen).
    for index in range(1, len(raw_times)):
        if raw_times[index] + 1e-9 < raw_times[index - 1]:
            notes.append(
                f"{plan.boundaries[index].cut_id}: Grenze vor Vorgänger — "
                f"{raw_times[index]:.3f}s → {raw_times[index - 1]:.3f}s."
            )
            raw_times[index] = raw_times[index - 1]

    editorial_min = max(TECH_MIN_SHOT_SECONDS, float(options.shot_min_sec))
    editorial_max = min(
        TECH_MAX_SHOT_SECONDS,
        max(editorial_min, float(options.shot_max_sec)),
    )
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    usables = (
        list(slot_usable_max)
        if slot_usable_max is not None
        else _slot_usable_max_from_catalog(plan, catalog, head_trim=head_trim)
    )
    times = _clamp_boundary_times(
        raw_times,
        editorial_min=editorial_min,
        editorial_max=editorial_max,
        repairs=notes,
        slot_usable_max=usables,
        short_tolerance=short_tolerance,
        max_media_iterations=2,
    )
    times = [_seconds_to_frame(t, fps) for t in times]

    slots: list[TimedSlot] = []
    for index, slot in enumerate(plan.slots):
        start_b = plan.boundaries[index]
        end_b = plan.boundaries[index + 1]
        start_sid = str(start_b.sentence_id or "").strip()
        end_sid = str(end_b.sentence_id or "").strip()
        fit = str(slot.asset_fit or "none").strip().lower()
        asset_id = slot.local_asset_id
        if fit == "none":
            asset_id = None
        slots.append(
            TimedSlot(
                slot_id=slot.slot_id,
                start_seconds=times[index],
                end_seconds=times[index + 1],
                start_boundary_id=start_b.cut_id,
                end_boundary_id=end_b.cut_id,
                cut_alignment=str(start_b.alignment or ""),
                asset_id=asset_id,
                asset_fit=fit,
                asset_fit_reason=slot.asset_fit_reason or "",
                coverage_gap_id=slot.coverage_gap_id,
                narrative_function=slot.narrative_function or "orientation",
                source_range_intent=slot.source_range_intent
                or "representative_middle_section",
                visual_intent=slot.visual_intent or "",
                needed_visual=slot.needed_visual or "",
                start_sentence_id=start_sid,
                end_sentence_id=end_sid,
                start_segment_id=segment_id_from_sentence_id(start_sid),
                end_segment_id=segment_id_from_sentence_id(end_sid),
            )
        )
    return slots


def unified_plan_to_final_shadow(
    plan: UnifiedCutPlanDocument,
    *,
    sentence_index: dict[str, SentenceTiming],
    timed_slots: list[TimedSlot] | None = None,
) -> FinalCutPlanDocument:
    """Kompat-Schatten ``final_cut_plan.json`` mit echten NarrationAnchors."""
    shots: list[FinalShot] = []
    for index, slot in enumerate(plan.slots):
        start_b = plan.boundaries[index]
        end_b = plan.boundaries[index + 1]
        timed = timed_slots[index] if timed_slots and index < len(timed_slots) else None
        asset_id = ""
        if timed is not None and timed.asset_id:
            asset_id = timed.asset_id
        elif slot.local_asset_id and str(slot.asset_fit) != "none":
            asset_id = str(slot.local_asset_id)
        shots.append(
            FinalShot(
                shot_id=slot.slot_id,
                narration_start_anchor=boundary_to_narration_anchor(
                    start_b, sentence_index=sentence_index
                ),
                narration_end_anchor=boundary_to_narration_anchor(
                    end_b, sentence_index=sentence_index
                ),
                asset_id=asset_id or f"open_gap:{slot.slot_id}",
                editorial_function=slot.narrative_function or "orientation",
                editorial_reason=slot.asset_fit_reason or "",
                source_range_intent=slot.source_range_intent
                or "representative_middle_section",
                start_cut_alignment=str(start_b.alignment or ""),
            )
        )
    return FinalCutPlanDocument(
        script_version=plan.script_version,
        shots=shots,
        voiceover_preroll_sec=plan.voiceover_preroll_sec,
        voiceover_postroll_sec=plan.voiceover_postroll_sec,
    )


def build_narration_timeline_from_unified(
    project: Project,
    plan: UnifiedCutPlanDocument,
) -> NarrationTimelineDocument:
    """Pause-Directives des Unified-Plans → NarrationTimelineDocument."""
    locked = require_locked_script(project)
    timings = load_segment_timings(project)
    if timings is None:
        raise UnifiedTimelineError("Segment-Timings fehlen.")
    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    return build_narration_timeline(
        script_version=locked.script_version,
        segment_timings=list(timings.segments),
        pause_directives=list(plan.pause_directives),
        sentence_index=sentence_index,
    )


def _placeholder_resolved_shot(
    project: Project,
    timed: TimedSlot,
    *,
    fps: float,
    asset_id: str | None = None,
    coverage_gap_id: str | None = None,
    asset_fit: str | None = None,
    asset_fit_reason: str | None = None,
) -> ResolvedShot:
    """Open-Gap-/Bridge-Shot mit ffmpeg-Slate (Preview); Produktion sperrt via Flag."""
    from otio_app.services.without_voiceover_enhanced.media_hold import (
        MediaHoldError,
        ensure_gap_placeholder_slate,
    )

    duration = max(TECH_MIN_SHOT_SECONDS, timed.duration_seconds)
    if coverage_gap_id is not None:
        gap_meta = str(coverage_gap_id).strip() or None
    else:
        gap_meta = (timed.coverage_gap_id or "").strip() or f"gap_{timed.slot_id}"
    gap_slate = gap_meta or f"bridge_{timed.slot_id}"
    needed = (timed.needed_visual or timed.visual_intent or "").strip()
    try:
        slate = ensure_gap_placeholder_slate(
            project,
            shot_id=timed.slot_id,
            gap_id=str(gap_slate),
            needed_visual=needed,
            start_seconds=float(timed.start_seconds),
            end_seconds=float(timed.end_seconds),
            fps=float(fps),
        )
        media_path = str(slate)
    except MediaHoldError as exc:
        raise UnifiedTimelineError(
            f"{timed.slot_id}: Placeholder-Slate fehlgeschlagen: {exc}"
        ) from exc

    return ResolvedShot(
        shot_id=timed.slot_id,
        asset_id=str(asset_id or timed.asset_id or ""),
        timeline_start_seconds=timed.start_seconds,
        timeline_end_seconds=timed.end_seconds,
        source_start_seconds=0.0,
        source_end_seconds=round(duration, 6),
        editorial_function=timed.narrative_function,
        resolved_media_path=media_path,
        resolved_media_kind="video",
        resolved_media_duration_seconds=round(duration, 6),
        folder_name="",
        hold_mode="placeholder_slate",
        asset_fit=str(asset_fit if asset_fit is not None else timed.asset_fit),
        asset_fit_reason=str(
            asset_fit_reason
            if asset_fit_reason is not None
            else timed.asset_fit_reason
        ),
        cut_alignment=timed.cut_alignment,
        coverage_gap_id=gap_meta,
        open_gap=True,
        is_placeholder=True,
    )


def _mark_slot_as_duration_gap(
    plan: UnifiedCutPlanDocument,
    slot_id: str,
    *,
    reason: str,
) -> None:
    """Slot für Funnel als weak/Gap nachziehen (Coverage-Schatten beim Persist)."""
    for slot in plan.slots:
        if slot.slot_id != slot_id:
            continue
        if not slot.coverage_gap_id:
            slot.coverage_gap_id = f"gap_{slot_id}"
        if str(slot.asset_fit or "") in {"", "strong", "acceptable"}:
            slot.asset_fit = "weak"
        note = (reason or "").strip()
        if note and note not in str(slot.asset_fit_reason or ""):
            prev = str(slot.asset_fit_reason or "").strip()
            slot.asset_fit_reason = f"{prev} | {note}".strip(" |")
        return


def resolve_unified_timeline(
    project: Project,
    plan: UnifiedCutPlanDocument | None = None,
    *,
    allow_open_gaps: bool = True,
    persist: bool = True,
) -> ResolvedTimelineDocument:
    """UnifiedCutPlan → ResolvedTimelineDocument (+ Kompat-Schatten).

    ``allow_open_gaps=True`` (Phase 3→4): none-Slots ohne Asset bleiben als
    Platzhalter erhalten. ``False``: offene none-Slots sind Fehler (Produktion).
    """
    locked = require_locked_script(project)
    if plan is None:
        plan = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if plan is None:
        raise UnifiedTimelineError("Unified Cut Plan fehlt.")

    timings = load_segment_timings(project)
    if timings is None:
        raise UnifiedTimelineError("Segment-Timings fehlen.")

    errors: list[str] = []
    repairs: list[str] = []
    fps = float(project.fps)
    options = load_cut_plan_options(project)
    catalog = build_asset_catalog(project, fps=fps)
    errors.extend(catalog.collisions)

    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    timeline = build_narration_timeline(
        script_version=locked.script_version,
        segment_timings=list(timings.segments),
        pause_directives=list(plan.pause_directives),
        sentence_index=sentence_index,
    )

    # Ziel-Dauer in Slots nachziehen (Funnel-Dauerfilter, Phase 4).
    timed_slots = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentence_index,
        options=options,
        fps=fps,
        repairs=repairs,
        catalog=catalog,
    )
    assert_timed_slots_contiguous(timed_slots, fps=fps)
    for slot, timed in zip(plan.slots, timed_slots):
        slot.target_duration_seconds = round(timed.duration_seconds, 6)

    final_shadow = unified_plan_to_final_shadow(
        plan, sentence_index=sentence_index, timed_slots=timed_slots
    )
    rough_shadow, coverage_shadow = unified_to_rough(plan)

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

    timing_map = {item.segment_id: item for item in timings.segments}
    audio_segments = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map=timing_map,
    )
    segment_to_chapter = _segment_to_chapter_map(locked)
    known_segments = {s.segment_id for s in locked.segments}
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))

    resolved_shots: list[ResolvedShot] = []
    for timed in timed_slots:
        if timed.start_segment_id not in known_segments:
            errors.append(f"{timed.slot_id}: unbekannte Start-Segment-ID.")
            continue
        if timed.end_segment_id not in known_segments:
            errors.append(f"{timed.slot_id}: unbekannte End-Segment-ID.")
            continue
        start_chapter = segment_to_chapter.get(timed.start_segment_id, "")
        end_chapter = segment_to_chapter.get(timed.end_segment_id, "")
        is_bridge = (
            str(timed.slot_id).startswith("bridge_")
            or str(timed.narrative_function or "") == "chapter_transition"
        )
        if start_chapter and end_chapter and start_chapter != end_chapter:
            if allow_open_gaps and is_bridge:
                resolved_shots.append(
                    _placeholder_resolved_shot(
                        project,
                        timed,
                        fps=fps,
                        coverage_gap_id="",  # nie Funnel
                    )
                )
                repairs.append(
                    f"{timed.slot_id}: Kapitelübergang "
                    f"({start_chapter} → {end_chapter}) — Platzhalter "
                    "(Bridge-Fill im Gap-Merge, kein Funnel)."
                )
                continue
            errors.append(
                f"{timed.slot_id}: Start-/Endanker in unterschiedlichen Kapiteln "
                f"({start_chapter} vs {end_chapter})."
            )
            continue

        if timed.is_open_gap and not timed.asset_id:
            if allow_open_gaps:
                resolved_shots.append(
                    _placeholder_resolved_shot(project, timed, fps=fps)
                )
                repairs.append(
                    f"{timed.slot_id}: offener Gap ({timed.asset_fit}) — "
                    "Platzhalter bis Funnel/Merge."
                )
            else:
                errors.append(
                    f"{timed.slot_id}: offener none-Gap ohne Asset "
                    f"(coverage_gap_id={timed.coverage_gap_id!r})."
                )
            continue

        asset_id = str(timed.asset_id or "")
        entry, lookup_error = lookup_catalog_entry(catalog, asset_id)
        if entry is None:
            if allow_open_gaps and timed.asset_fit in {"weak", "none"}:
                resolved_shots.append(
                    _placeholder_resolved_shot(
                        project, timed, fps=fps, asset_id=asset_id
                    )
                )
                repairs.append(
                    f"{timed.slot_id}: Asset {asset_id} nicht auflösbar — "
                    f"Platzhalter ({lookup_error})."
                )
                continue
            errors.append(f"{timed.slot_id}: {lookup_error}")
            continue

        media_path = Path(str(entry.get("path") or ""))
        if is_http_url(str(media_path)) or not media_path.is_file():
            msg = (
                f"{timed.slot_id}: lokale Datei fehlt/ungültig für {asset_id}: "
                f"{media_path}"
            )
            if allow_open_gaps and timed.asset_fit in {"weak", "none"}:
                resolved_shots.append(
                    _placeholder_resolved_shot(
                        project, timed, fps=fps, asset_id=asset_id
                    )
                )
                repairs.append(msg)
                continue
            errors.append(msg)
            continue

        try:
            resolved = _resolve_shot_media(
                project,
                shot_id=timed.slot_id,
                asset_id=str(entry.get("canonical_id") or asset_id),
                entry=entry,
                timeline_start=timed.start_seconds,
                timeline_end=timed.end_seconds,
                fps=fps,
                head_trim=head_trim,
                short_tolerance=short_tolerance,
                editorial_function=timed.narrative_function,
                may_overlap_pause=False,
                repairs=repairs,
            )
        except TimelineResolveError as exc:
            msg = str(exc)
            is_short = "zu kurz" in msg.lower()
            # Unified Preview: zu kurze strong/acceptable-Assets nicht hart
            # verwerfen (das erzeugt Folgelücken), sondern als Gap markieren.
            if allow_open_gaps and (
                timed.asset_fit in {"weak", "none"} or is_short
            ):
                if is_short:
                    _mark_slot_as_duration_gap(
                        plan,
                        timed.slot_id,
                        reason="Asset zu kurz für berechnete Narrationsdauer",
                    )
                resolved_shots.append(
                    _placeholder_resolved_shot(
                        project,
                        timed,
                        fps=fps,
                        asset_id=asset_id,
                        coverage_gap_id=timed.coverage_gap_id
                        or f"gap_{timed.slot_id}",
                        asset_fit="weak" if is_short else None,
                        asset_fit_reason=(
                            "Asset zu kurz für berechnete Narrationsdauer"
                            if is_short
                            else None
                        ),
                    )
                )
                repairs.append(
                    f"{timed.slot_id}: als Gap markiert — {msg}"
                )
                continue
            errors.append(msg)
            continue

        resolved.asset_fit = timed.asset_fit
        resolved.asset_fit_reason = timed.asset_fit_reason
        resolved.cut_alignment = timed.cut_alignment
        resolved.coverage_gap_id = timed.coverage_gap_id
        resolved.open_gap = False
        if not resolved.folder_name:
            resolved.folder_name = start_chapter
        resolved_shots.append(resolved)

    ordered = sorted(resolved_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))

    chapter_envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final_shadow,
        ordered=ordered,
        audio_segments=audio_segments,
        preroll=preroll,
        postroll=postroll,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    # Platzhalter ohne Medien: Vor-/Nachlauf-Hold-Fehler sind soft bei open gaps.
    if allow_open_gaps:
        soft: list[str] = []
        hard: list[str] = []
        for message in errors:
            if "Motion-Video zu kurz" in message or "Vorlauf" in message or "Nachlauf" in message:
                # Nur soft, wenn betroffener Shot ein open_gap ist.
                touched = any(
                    shot.open_gap and shot.shot_id in message for shot in ordered
                )
                if touched or "Motion-Video zu kurz" in message and any(
                    s.open_gap for s in ordered
                ):
                    soft.append(message)
                    continue
            hard.append(message)
        if soft:
            repairs.extend(f"open-gap soft: {m}" for m in soft)
        errors = hard

    ordered = sorted(ordered, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    _apply_visual_continuity_rules(
        ordered,
        project=project,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    ordered = sorted(ordered, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    _count_chapter_continuity(chapter_envelopes, ordered, fps=fps)

    editorial_shots = [
        shot
        for shot in ordered
        if not str(shot.editorial_function or "").startswith("technical_chapter_")
        and not shot.open_gap
        and shot.asset_id
    ]
    for prev, curr in zip(editorial_shots, editorial_shots[1:]):
        if not prev.asset_id or prev.asset_id != curr.asset_id:
            continue
        prev_folder = str(
            prev.folder_name
            or (catalog.by_id.get(prev.asset_id) or {}).get("folder")
            or ""
        )
        if _is_intro_folder(prev_folder):
            continue
        errors.append(
            f"Benachbarte Shots nutzen dasselbe Asset {prev.asset_id}: "
            f"{prev.shot_id} → {curr.shot_id}."
        )

    usage_counts = Counter(shot.asset_id for shot in editorial_shots)
    for asset_id, count in sorted(usage_counts.items()):
        folder = str((catalog.by_id.get(asset_id) or {}).get("folder") or "")
        if _is_intro_folder(folder):
            continue
        if count > int(options.max_asset_usage):
            errors.append(
                f"Asset {asset_id} wird {count}× genutzt "
                f"(max_asset_usage={options.max_asset_usage})."
            )

    reuse_distance = int(options.min_asset_reuse_distance_shots)
    if reuse_distance > 0:
        last_index: dict[str, int] = {}
        for index, shot in enumerate(editorial_shots):
            folder = str(
                shot.folder_name
                or (catalog.by_id.get(shot.asset_id) or {}).get("folder")
                or ""
            )
            if _is_intro_folder(folder):
                continue
            prev_index = last_index.get(shot.asset_id)
            if prev_index is not None:
                gap_shots = index - prev_index - 1
                if gap_shots < reuse_distance:
                    message = (
                        f"{shot.shot_id}: Asset {shot.asset_id} erneut nach "
                        f"{gap_shots} Shots (min Abstand {reuse_distance})."
                    )
                    if gap_shots == 0:
                        errors.append(message)
                    else:
                        repairs.append(message)
            last_index[shot.asset_id] = index

    repairs.extend(assess_cut_rhythm(final_shadow, ordered))

    chapter_count = max(1, len(chapter_envelopes))
    total = timeline.total_duration_seconds + (preroll + postroll) * chapter_count
    if ordered:
        total = max(total, ordered[-1].timeline_end_seconds)
    if audio_segments:
        total = max(
            total,
            max(a.timeline_end_seconds + a.pause_after_seconds for a in audio_segments),
        )
    if chapter_envelopes:
        total = max(total, chapter_envelopes[-1].chapter_video_end)

    document = ResolvedTimelineDocument(
        script_version=locked.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=ordered,
        chapters=chapter_envelopes,
        voiceover_preroll_sec=round(preroll, 6),
        voiceover_postroll_sec=round(postroll, 6),
        repairs=repairs,
        errors=errors,
    )
    quality = assess_unified_cut_quality(
        plan=plan, resolved=document, options=options
    )
    for note in quality.all_notes():
        if note not in document.repairs:
            document.repairs.append(note)
    repairs = document.repairs

    if persist:
        write_json(unified_cut_plan_path(project), plan)
        write_json(narration_timeline_path(project), timeline)
        write_json(final_cut_plan_path(project), final_shadow)
        write_json(rough_cut_plan_path(project), rough_shadow)
        write_json(coverage_gaps_path(project), coverage_shadow)
        write_json(resolved_timeline_path(project), document)
        write_json(repair_log_path(project), {"repairs": repairs, "errors": errors})

    if errors:
        raise UnifiedTimelineError("\n".join(errors), errors=errors)
    return document
