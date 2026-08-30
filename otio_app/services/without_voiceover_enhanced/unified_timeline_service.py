"""Phase 3: Unified Cut Plan → deterministische Timeline (ohne LLM 3)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    probe_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    is_keyword_flow_free_unified_style,
    load_cut_plan_options,
    resolve_timing_seconds,
    uses_keyword_onset_timing_rules,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
    assess_unified_cut_quality,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    BOUNDARY_POSITIONS,
    CoverageGapsDocument,
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
    _resolved_shot_sort_key,
    _seconds_to_frame,
    _segment_to_chapter_map,
    build_asset_catalog,
    lookup_catalog_entry,
    still_image_path_from_catalog_entry,
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


def _seconds_floor_to_frame(seconds: float, fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return round(math.floor(float(seconds) * rate + 1e-9) / rate, 6)


def _seconds_ceil_to_frame(seconds: float, fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return round(math.ceil(float(seconds) * rate - 1e-9) / rate, 6)


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


def _snap_chapter_edge_boundary_times(
    raw_times: list[float],
    plan: UnifiedCutPlanDocument,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming],
    segment_to_chapter: dict[str, str],
    fps: float,
    repairs: list[str],
) -> list[float]:
    """E2E-4: Kapitel-Erste Grenze → Audio-Start (floor); letzte → Audio-Ende (ceil)."""
    if len(raw_times) != len(plan.boundaries):
        return raw_times
    entry_map = {entry.segment_id: entry for entry in timeline.entries}
    out = list(raw_times)
    chapters: list[str] = []
    for boundary in plan.boundaries:
        sid = str(boundary.sentence_id or "").strip()
        seg = segment_id_from_sentence_id(sid)
        sentence = sentence_index.get(sid)
        if sentence is not None and not seg:
            seg = sentence.segment_id
        chapters.append(segment_to_chapter.get(seg, seg or ""))

    for index, boundary in enumerate(plan.boundaries):
        sid = str(boundary.sentence_id or "").strip()
        seg = segment_id_from_sentence_id(sid)
        sentence = sentence_index.get(sid)
        if sentence is not None and not seg:
            seg = sentence.segment_id
        entry = entry_map.get(seg)
        if entry is None:
            continue
        is_first = index == 0 or (
            chapters[index] and chapters[index] != chapters[index - 1]
        )
        is_last = index == len(plan.boundaries) - 1 or (
            chapters[index] and chapters[index] != chapters[index + 1]
        )
        if is_first:
            snapped = _seconds_floor_to_frame(float(entry.start_seconds), fps)
            if abs(snapped - out[index]) > 1e-9:
                repairs.append(
                    f"{boundary.cut_id}: Kapitel-Start an Audio-Start "
                    f"{out[index]:.3f}s → {snapped:.3f}s (floor)."
                )
            out[index] = snapped
        if is_last:
            snapped = _seconds_ceil_to_frame(float(entry.end_seconds), fps)
            if abs(snapped - out[index]) > 1e-9:
                repairs.append(
                    f"{boundary.cut_id}: Kapitel-Ende an Audio-Ende "
                    f"{out[index]:.3f}s → {snapped:.3f}s (ceil)."
                )
            out[index] = snapped
    for index in range(1, len(out)):
        if out[index] + 1e-9 < out[index - 1]:
            out[index] = out[index - 1]
    return out


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
    original = str(entry.get("original_image_path") or "").strip()
    if original:
        orig = Path(original)
        if is_image_media(orig) and not is_video_media(orig):
            return None
    if still_image_path_from_catalog_entry(entry) is not None:
        return None
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
        usable = usable_media_duration_seconds(entry, head_trim=head_trim)
        if usable is None and still_image_path_from_catalog_entry(entry) is None:
            media_kind = str(entry.get("media_kind") or entry.get("media_type") or "").lower()
            if media_kind not in {"image", "photo"}:
                path = Path(str(entry.get("path") or ""))
                probed: float | None = None
                try:
                    if path.is_file():
                        probed = probe_duration_seconds(path)
                except Exception:  # noqa: BLE001
                    probed = None
                if probed is not None and float(probed) > 0:
                    filled = dict(entry)
                    filled["duration_seconds"] = float(probed)
                    usable = usable_media_duration_seconds(
                        filled, head_trim=head_trim
                    )
        out.append(usable)
    return out


def _is_intro_plan_slot(
    plan: UnifiedCutPlanDocument,
    index: int,
    segment_to_chapter: dict[str, str] | None,
) -> bool:
    """Intro-Slots: Cut-Plan shot_min/max gelten nicht (nur technische Limits)."""
    if index < 0 or index >= len(plan.slots):
        return False
    slot = plan.slots[index]
    if _is_intro_folder(str(slot.slot_id or "")):
        return True
    if index < len(plan.boundaries):
        start_sid = str(plan.boundaries[index].sentence_id or "").strip()
        seg_id = segment_id_from_sentence_id(start_sid)
        if _is_intro_folder(seg_id):
            return True
        if segment_to_chapter:
            chapter = str(segment_to_chapter.get(seg_id) or "")
            if _is_intro_folder(chapter):
                return True
    return False


def _plan_has_intro_slots(
    plan: UnifiedCutPlanDocument,
    segment_to_chapter: dict[str, str] | None,
) -> bool:
    return any(
        _is_intro_plan_slot(plan, index, segment_to_chapter)
        for index in range(len(plan.slots))
    )


def allocate_mini_gap_to_neighbors(
    need_sec: float,
    *,
    spare_prev: float,
    spare_next: float,
    fps: float = 25.0,
) -> tuple[float, float]:
    """Teilt eine Mini-Lücke auf Vorgänger/Nachfolger (bevorzugt hälftig).

    Letzter/erster Slot: ``spare_*`` vorher auf 0 setzen. Extra-Frame geht
    an den Nachfolger, damit links-nach-rechts Resolve den Vorgänger seltener
    neu auflösen muss.
    """
    rate = float(fps) if float(fps) > 0 else 25.0

    def _to_frames(seconds: float) -> int:
        return int(round(max(0.0, float(seconds)) * rate))

    def _from_frames(frames: int) -> float:
        return round(max(0, int(frames)) / rate, 6)

    need_f = _to_frames(need_sec)
    prev_f = _to_frames(spare_prev)
    next_f = _to_frames(spare_next)
    if need_f <= 0 or (prev_f <= 0 and next_f <= 0):
        return 0.0, 0.0
    if prev_f <= 0:
        return 0.0, _from_frames(min(need_f, next_f))
    if next_f <= 0:
        return _from_frames(min(need_f, prev_f)), 0.0
    to_next = min((need_f + 1) // 2, next_f)
    to_prev = min(need_f - to_next, prev_f)
    leftover = need_f - to_prev - to_next
    extra = min(leftover, next_f - to_next)
    to_next += extra
    leftover -= extra
    extra = min(leftover, prev_f - to_prev)
    to_prev += extra
    return _from_frames(to_prev), _from_frames(to_next)


def absorb_timed_slot_mini_shortfall(
    timed_slots: list[TimedSlot],
    index: int,
    *,
    usable: float,
    neighbor_usables: list[float | None],
    fps: float,
    short_tolerance: float,
    repairs: list[str],
) -> tuple[bool, bool]:
    """Kürzt ``timed_slots[index]`` auf usable und gibt den Rest an Nachbarn.

    Rückgabe: ``(changed, prev_changed)``. ``prev_changed`` heißt: der
    Vorgänger wurde verlängert und muss neu aufgelöst werden.
    """
    if index < 0 or index >= len(timed_slots):
        return False, False
    rate = float(fps) if float(fps) > 0 else 25.0
    slot = timed_slots[index]
    need = float(slot.duration_seconds)
    target = _seconds_to_frame(float(usable), rate)
    cut_need = need - target
    if cut_need <= 1e-9:
        return False, False
    if cut_need > float(short_tolerance) + 1e-9:
        return False, False

    def _spare(neighbor: int) -> float:
        if neighbor < 0 or neighbor >= len(timed_slots):
            return 0.0
        n_usable = (
            neighbor_usables[neighbor] if neighbor < len(neighbor_usables) else None
        )
        n_span = float(timed_slots[neighbor].duration_seconds)
        if n_usable is None:
            return 10_000.0
        return max(0.0, float(n_usable) + float(short_tolerance) - n_span)

    spare_prev = 0.0 if index == 0 else _spare(index - 1)
    spare_next = 0.0 if index == len(timed_slots) - 1 else _spare(index + 1)
    to_prev, to_next = allocate_mini_gap_to_neighbors(
        cut_need,
        spare_prev=spare_prev,
        spare_next=spare_next,
        fps=rate,
    )
    if to_prev + to_next <= 1e-9:
        if index == len(timed_slots) - 1:
            return False, False
        to_next = cut_need
    prev_changed = False
    if to_prev > 1e-9 and index > 0:
        new_start = _seconds_to_frame(slot.start_seconds + to_prev, rate)
        prev = timed_slots[index - 1]
        if new_start + 1e-9 >= prev.start_seconds:
            prev.end_seconds = new_start
            slot.start_seconds = new_start
            prev_changed = True
    if to_next > 1e-9 and index < len(timed_slots) - 1:
        new_end = _seconds_to_frame(slot.end_seconds - to_next, rate)
        nxt = timed_slots[index + 1]
        if new_end + 1e-9 >= slot.start_seconds:
            slot.end_seconds = new_end
            nxt.start_seconds = new_end
    if not prev_changed and abs(float(slot.duration_seconds) - need) <= 1e-9:
        return False, False
    repairs.append(
        f"{slot.slot_id}: Mini-Lücke {cut_need:.2f}s an Nachbarn "
        f"(Vorgänger +{to_prev:.2f}s, Folgeslot +{to_next:.2f}s)."
    )
    return True, prev_changed


def _clamp_boundary_times(
    times: list[float],
    *,
    editorial_min: float,
    editorial_max: float,
    repairs: list[str],
    slot_usable_max: list[float | None] | None = None,
    slot_editorial_mins: list[float] | None = None,
    slot_editorial_maxes: list[float] | None = None,
    short_tolerance: float = 0.0,
    max_media_iterations: int = 2,
    fps: float = 25.0,
) -> list[float]:
    """Shot min/max + nutzbare Asset-Dauer; gemeinsame Grenzen, Kette bleibt dicht.

    Fix 1b: gesamte Klemme arbeitet nur auf Framegrenzen (Input vorher snappen).
    Media-Regel (pro Slot mit usable):
    - span > usable + tolerance → nicht klemmen (später is_short/Gap)
    - usable < span <= usable + tolerance → knappen Shortfall an Nachbar
      abgeben (Folge-Slot länger und/oder Vorgänger länger), auch wenn
      dadurch ``shot_max`` überschritten wird. Short-Slot selbst wird auf
      ``floor(usable * fps) / fps`` geklemmt.
    Wenn ``floor(usable) < shot_min``: kein editorial-Hochschieben
    (sonst Pingpong mit usable-Klemme) → Gap-Pfad.
    Max. ``max_media_iterations`` Links-nach-rechts-Pässe; danach noch
    innerhalb-Toleranz verletzt → Fehler.

    ``slot_editorial_mins`` / ``slot_editorial_maxes``: optional pro Slot
    (Intro nutzt TECH_MIN/TECH_MAX statt Cut-Plan shot_min/max — sonst
    verschiebt shot_max Keyword-Onsets und lässt das letzte Intro-Bild vor
    dem VO-Ende enden).
    """
    if len(times) < 2:
        return times
    rate = float(fps) if float(fps) > 0 else 25.0

    def _to_frames(seconds: float) -> int:
        return int(round(float(seconds) * rate))

    def _floor_frames(seconds: float) -> int:
        return int(math.floor(float(seconds) * rate + 1e-9))

    def _ceil_frames(seconds: float) -> int:
        return int(math.ceil(float(seconds) * rate - 1e-9))

    def _from_frames(frames: int) -> float:
        return round(int(frames) / rate, 6)

    # 1) raw_times VOR der Klemme frame-snappen; danach nur Framegrenzen.
    out = [_seconds_to_frame(t, rate) for t in times]
    for index in range(1, len(out)):
        if out[index] + 1e-9 < out[index - 1]:
            out[index] = out[index - 1]

    n_slots = len(out) - 1
    usables = list(slot_usable_max or [None] * n_slots)
    if len(usables) < n_slots:
        usables.extend([None] * (n_slots - len(usables)))

    slot_mins = list(slot_editorial_mins or [])
    while len(slot_mins) < n_slots:
        slot_mins.append(editorial_min)
    min_frames_by_slot = [
        max(1, _ceil_frames(float(slot_mins[index]))) for index in range(n_slots)
    ]
    slot_maxes = list(slot_editorial_maxes or [])
    while len(slot_maxes) < n_slots:
        slot_maxes.append(editorial_max)
    max_frames_by_slot = [
        max(
            min_frames_by_slot[index],
            _floor_frames(float(slot_maxes[index])),
        )
        for index in range(n_slots)
    ]
    # Fallback-Frames für globale Labels in Repair-Texten.
    min_frames = max(1, _ceil_frames(editorial_min))
    max_frames = max(min_frames, _floor_frames(editorial_max))

    usable_frames: list[int | None] = []
    for usable in usables:
        if usable is None:
            usable_frames.append(None)
        else:
            usable_frames.append(max(0, _floor_frames(float(usable))))

    # Slots, die Shortfall aus Toleranz-Absorb übernommen haben — dürfen
    # shot_max überschreiten (sonst wird die Absorb-Korrektur wieder kassiert).
    shot_max_exempt: set[int] = set()

    def _skip_editorial_min(index: int) -> bool:
        """Fix 1b.5: usable (floor) < shot_min → nicht hochschieben (Gap-Pfad)."""
        uf = usable_frames[index]
        return uf is not None and uf < min_frames_by_slot[index]

    def _neighbor_spare_sec(neighbor: int) -> float:
        """Wie viel Extra der Nachbar aufnehmen kann (usable + Toleranz)."""
        if neighbor < 0 or neighbor >= n_slots:
            return 0.0
        neighbor_usable = usables[neighbor]
        neighbor_span = out[neighbor + 1] - out[neighbor]
        if neighbor_usable is None:
            return 10_000.0
        return max(0.0, float(neighbor_usable) + tol - neighbor_span)

    def _editorial_pass(*, enforce_shot_max: bool = True) -> None:
        # Zu lang: Ende nach vorne (spätere Slots werden länger).
        if enforce_shot_max:
            for index in range(n_slots):
                if index in shot_max_exempt:
                    continue
                slot_max_frames = max_frames_by_slot[index]
                duration_frames = _to_frames(out[index + 1] - out[index])
                if duration_frames <= slot_max_frames:
                    continue
                slot_max_sec = _from_frames(slot_max_frames)
                out[index + 1] = _seconds_to_frame(out[index] + slot_max_sec, rate)
                repairs.append(
                    f"slot[{index}]: über shot_max ({slot_max_sec:.2f}s) — "
                    "Endgrenze nach vorne verschoben."
                )

        # Zu kurz: Ende nach hinten + Cascade (spätere Dauern bleiben gleich).
        for index in range(n_slots):
            if _skip_editorial_min(index):
                continue
            slot_min_frames = min_frames_by_slot[index]
            duration_frames = _to_frames(out[index + 1] - out[index])
            if duration_frames >= slot_min_frames:
                continue
            need_frames = slot_min_frames - duration_frames
            need = _from_frames(need_frames)
            slot_min_sec = _from_frames(slot_min_frames)
            out[index + 1] = _seconds_to_frame(out[index + 1] + need, rate)
            for later in range(index + 2, len(out)):
                out[later] = _seconds_to_frame(out[later] + need, rate)
            repairs.append(
                f"slot[{index}]: unter shot_min ({slot_min_sec:.2f}s) — "
                f"Endgrenze +{need:.2f}s (Cascade)."
            )

    tol = max(0.0, float(short_tolerance))
    _editorial_pass(enforce_shot_max=True)

    for _iteration in range(max(1, int(max_media_iterations))):
        changed = False
        for index in range(n_slots):
            usable = usables[index]
            uf = usable_frames[index]
            if usable is None or uf is None:
                continue
            duration = out[index + 1] - out[index]
            if duration <= float(usable) + 1e-9:
                continue
            shortfall = duration - float(usable)
            if shortfall > tol + 1e-9:
                # Über Toleranz: Grenzen unverändert → is_short/Gap-Pfad.
                continue
            # Innerhalb Toleranz: Mini-Lücke auf Nachbarn verteilen (shot_max ok).
            clamped = _from_frames(uf)
            cut_need = duration - clamped
            if cut_need <= 1e-9:
                continue
            spare_prev = 0.0 if index == 0 else _neighbor_spare_sec(index - 1)
            spare_next = 0.0 if index == n_slots - 1 else _neighbor_spare_sec(index + 1)
            to_prev, to_next = allocate_mini_gap_to_neighbors(
                cut_need,
                spare_prev=spare_prev,
                spare_next=spare_next,
                fps=rate,
            )
            if to_prev + to_next <= 1e-9:
                if index == n_slots - 1:
                    continue
                # Kein nachweisbarer Spare: Folge-Slot nimmt alles (wie bisher).
                to_next = cut_need
            applied_prev = False
            applied_next = False
            if to_prev > 1e-9:
                new_start = _seconds_to_frame(out[index] + to_prev, rate)
                if new_start + 1e-9 >= out[index - 1]:
                    out[index] = new_start
                    shot_max_exempt.add(index - 1)
                    applied_prev = True
            if to_next > 1e-9:
                new_end = _seconds_to_frame(out[index + 1] - to_next, rate)
                if new_end + 1e-9 >= out[index]:
                    out[index + 1] = new_end
                    if index < n_slots - 1:
                        shot_max_exempt.add(index + 1)
                    applied_next = True
            if not applied_prev and not applied_next:
                continue
            parts: list[str] = []
            if applied_prev:
                parts.append("Vorgänger-Slot länger")
            if applied_next:
                parts.append("Folge-Slot länger")
            repairs.append(
                f"slot[{index}]: nutzbare Dauer knapp "
                f"(span {duration:.2f}s → usable {float(usable):.2f}s / "
                f"frame {clamped:.2f}s, "
                f"shortfall {shortfall:.2f}s ≤ Toleranz {tol:.1f}s) — "
                + " und ".join(parts)
                + " (shot_max-Überschreitung erlaubt)."
            )
            changed = True
        if changed:
            # Nur shot_min nachziehen — shot_max für Absorb-Nachbarn bewusst aus.
            _editorial_pass(enforce_shot_max=False)
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
    segment_to_chapter: dict[str, str] | None = None,
    keyword_flow: bool = False,
    sentence_rows_by_id: dict[str, dict] | None = None,
    segment_words_by_id: dict[str, list[dict]] | None = None,
) -> list[TimedSlot]:
    """Grenzen-Kette → TimedSlots (VO-absolut, vor Kapitel-Hülle)."""
    notes = repairs if repairs is not None else []
    if not plan.slots:
        return []

    if keyword_flow and sentence_rows_by_id is not None:
        from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
            KeywordFlowTimingError,
            validate_keyword_flow_mid_sentence_onsets,
        )

        try:
            validate_keyword_flow_mid_sentence_onsets(
                plan, sentence_rows_by_id=sentence_rows_by_id
            )
        except KeywordFlowTimingError as exc:
            raise UnifiedTimelineError(str(exc)) from exc

    words_by_segment = segment_words_by_id or {}
    raw_times: list[float] = []
    for boundary in plan.boundaries:
        align = str(getattr(boundary, "alignment", "") or "").strip().lower()
        if keyword_flow and align == "in_pause":
            from otio_app.services.without_voiceover_enhanced.pause_resolver import (
                PauseResolveError,
                clamp_in_pause_cut_to_natural_window,
                source_seconds_to_timeline,
            )

            sentence_id = str(boundary.sentence_id or "").strip()
            sentence = sentence_index.get(sentence_id)
            if sentence is None:
                raise UnifiedTimelineError(
                    f"{boundary.cut_id}: unbekannte Sentence-ID {sentence_id}."
                )
            segment_id = segment_id_from_sentence_id(sentence_id) or sentence.segment_id
            entry_map = {entry.segment_id: entry for entry in timeline.entries}
            entry = entry_map.get(segment_id)
            if entry is None:
                raise UnifiedTimelineError(
                    f"{boundary.cut_id}: Segment {segment_id} fehlt in Narration-Timeline."
                )
            requested = float(sentence.start_seconds) + boundary_source_offset_seconds(
                boundary, sentence, alignment=align
            )
            try:
                clamped_source = clamp_in_pause_cut_to_natural_window(
                    requested_source_seconds=requested,
                    segment_words=list(words_by_segment.get(segment_id) or []),
                    fps=fps,
                )
            except PauseResolveError as exc:
                raise UnifiedTimelineError(str(exc)) from exc
            if abs(clamped_source - requested) > 1e-6:
                notes.append(
                    f"{boundary.cut_id}: in_pause in natürliches Fenster "
                    f"{requested:.3f}s → {clamped_source:.3f}s."
                )
            absolute = source_seconds_to_timeline(entry, clamped_source)
            raw_times.append(_seconds_to_frame(absolute, fps))
            continue
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

    if segment_to_chapter:
        raw_times = _snap_chapter_edge_boundary_times(
            raw_times,
            plan,
            timeline,
            sentence_index=sentence_index,
            segment_to_chapter=segment_to_chapter,
            fps=fps,
            repairs=notes,
        )

    editorial_min = max(TECH_MIN_SHOT_SECONDS, float(options.shot_min_sec))
    editorial_max = min(
        TECH_MAX_SHOT_SECONDS,
        max(editorial_min, float(options.shot_max_sec)),
    )
    # Intro only: Cut-Plan shot_min/max aushebeln — LLM darf präzise
    # Keyword-Kurzschnitte (~1s) und lange Closing-Holds über Rest-VO behalten.
    # Technischer Floor/Ceiling bleiben TECH_MIN/TECH_MAX.
    # Keyword-Sync/Flow (Körper): shot_min/max gelten weiter (Settings → LLM + Clamp).
    intro_editorial_min = TECH_MIN_SHOT_SECONDS
    intro_editorial_max = TECH_MAX_SHOT_SECONDS
    slot_editorial_mins = [
        intro_editorial_min
        if _is_intro_plan_slot(plan, index, segment_to_chapter)
        else editorial_min
        for index in range(len(plan.slots))
    ]
    slot_editorial_maxes = [
        intro_editorial_max
        if _is_intro_plan_slot(plan, index, segment_to_chapter)
        else editorial_max
        for index in range(len(plan.slots))
    ]
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    usables = (
        list(slot_usable_max)
        if slot_usable_max is not None
        else _slot_usable_max_from_catalog(plan, catalog, head_trim=head_trim)
    )
    onset_anchor_times = list(raw_times)
    times = _clamp_boundary_times(
        raw_times,
        editorial_min=editorial_min,
        editorial_max=editorial_max,
        repairs=notes,
        slot_usable_max=usables,
        slot_editorial_mins=slot_editorial_mins,
        slot_editorial_maxes=slot_editorial_maxes,
        short_tolerance=short_tolerance,
        max_media_iterations=2,
        fps=fps,
    )
    if keyword_flow:
        from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
            KeywordFlowTimingError,
            apply_keyword_flow_onset_tolerance,
        )

        try:
            times = apply_keyword_flow_onset_tolerance(
                plan=plan,
                raw_times=onset_anchor_times,
                clamped_times=times,
                repairs=notes,
                allow_overflow=bool(
                    getattr(options, "keyword_flow_allow_onset_overflow", False)
                ),
            )
        except KeywordFlowTimingError as exc:
            raise UnifiedTimelineError(str(exc)) from exc
    # Nach Clamp: Intro-VO-Teppich erneut an Audio-Start/Ende pinnen.
    # Sonst kann usable-Toleranz-Klemme die letzte Grenze vor das VO-Ende ziehen
    # → schwarzes Bild bei laufendem Intro-Audio.
    if segment_to_chapter and _plan_has_intro_slots(plan, segment_to_chapter):
        times = _snap_chapter_edge_boundary_times(
            times,
            plan,
            timeline,
            sentence_index=sentence_index,
            segment_to_chapter=segment_to_chapter,
            fps=fps,
            repairs=notes,
        )

    slots: list[TimedSlot] = []
    for index, slot in enumerate(plan.slots):
        start_b = plan.boundaries[index]
        end_b = plan.boundaries[index + 1]
        # E2E-4: Kapitel-Join ohne Bridge — Start-Sentence vom Slot überschreiben.
        override_start = str(getattr(slot, "start_sentence_id", None) or "").strip()
        start_sid = override_start or str(start_b.sentence_id or "").strip()
        end_sid = str(end_b.sentence_id or "").strip()
        fit = str(slot.asset_fit or "none").strip().lower()
        asset_id = slot.local_asset_id
        if fit == "none" or (keyword_flow and fit == "weak"):
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
        override_start = str(getattr(slot, "start_sentence_id", None) or "").strip()
        if override_start:
            # Kapitel-Join: gemeinsame Grenze trägt End-Position von Kap. N;
            # Start von Kap. N+1 ist Satzanfang der überschriebenen Sentence.
            start_anchor = boundary_to_narration_anchor(
                start_b.model_copy(
                    update={
                        "sentence_id": override_start,
                        "position": "start",
                        "offset_seconds": None,
                    }
                ),
                sentence_index=sentence_index,
            )
        else:
            start_anchor = boundary_to_narration_anchor(
                start_b, sentence_index=sentence_index
            )
        shots.append(
            FinalShot(
                shot_id=slot.slot_id,
                narration_start_anchor=start_anchor,
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
    from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
        load_elevenlabs_settings,
    )
    from otio_app.services.without_voiceover_enhanced.pause_resolver import (
        author_pause_after_map_from_script,
    )

    return build_narration_timeline(
        script_version=locked.script_version,
        segment_timings=list(timings.segments),
        pause_directives=[],
        sentence_index=sentence_index,
        enable_keyword_flow_pauses=False,
        author_pause_after_by_segment=author_pause_after_map_from_script(
            locked,
            model_id=load_elevenlabs_settings(project).model_id,
        ),
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
    shot_id: str | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    color: str = "0x2b1d1d",
    title: str = "PLACEHOLDER / OPEN GAP",
) -> ResolvedShot:
    """Open-Gap-/Bridge-Shot mit ffmpeg-Slate (Preview); Produktion sperrt via Flag."""
    from otio_app.services.without_voiceover_enhanced.media_hold import (
        MediaHoldError,
        ensure_gap_placeholder_slate,
    )

    t0 = float(timed.start_seconds if start_seconds is None else start_seconds)
    t1 = float(timed.end_seconds if end_seconds is None else end_seconds)
    duration = max(TECH_MIN_SHOT_SECONDS, t1 - t0)
    resolved_shot_id = str(shot_id or timed.slot_id)
    if coverage_gap_id is not None:
        gap_meta = str(coverage_gap_id).strip() or None
    else:
        gap_meta = (timed.coverage_gap_id or "").strip() or f"gap_{timed.slot_id}"
    gap_slate = gap_meta or f"bridge_{timed.slot_id}"
    needed = (timed.needed_visual or timed.visual_intent or "").strip()
    try:
        slate = ensure_gap_placeholder_slate(
            project,
            shot_id=resolved_shot_id,
            gap_id=str(gap_slate),
            needed_visual=needed,
            start_seconds=t0,
            end_seconds=t1,
            fps=float(fps),
            color=color,
            title=title,
        )
        media_path = str(slate)
    except MediaHoldError as exc:
        raise UnifiedTimelineError(
            f"{resolved_shot_id}: Placeholder-Slate fehlgeschlagen: {exc}"
        ) from exc

    return ResolvedShot(
        shot_id=resolved_shot_id,
        asset_id=str(asset_id or timed.asset_id or ""),
        timeline_start_seconds=t0,
        timeline_end_seconds=t1,
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


def _short_asset_with_red_placeholder_tail(
    project: Project,
    timed: TimedSlot,
    *,
    entry: dict,
    asset_id: str,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    repairs: list[str],
) -> list[ResolvedShot]:
    """Zu kurzes Asset behalten + Restlücke als roter Placeholder.

    Timeline: [Asset nutzbar][roter Shortfall-Placeholder].
    """
    usable = usable_media_duration_seconds(entry, head_trim=head_trim)
    need = float(timed.duration_seconds)
    rate = float(fps) if float(fps) > 0 else 25.0
    gap_id = (timed.coverage_gap_id or "").strip() or f"gap_{timed.slot_id}"
    reason = "Asset zu kurz für berechnete Narrationsdauer"

    if usable is None or usable < TECH_MIN_SHOT_SECONDS:
        return [
            _placeholder_resolved_shot(
                project,
                timed,
                fps=fps,
                asset_id=asset_id,
                coverage_gap_id=gap_id,
                asset_fit="weak",
                asset_fit_reason=reason,
                color="0xCC0000",
                title="SHORT ASSET / MANUAL FIX",
            )
        ]

    usable_span = math.floor(float(usable) * rate + 1e-9) / rate
    if usable_span < TECH_MIN_SHOT_SECONDS:
        return [
            _placeholder_resolved_shot(
                project,
                timed,
                fps=fps,
                asset_id=asset_id,
                coverage_gap_id=gap_id,
                asset_fit="weak",
                asset_fit_reason=reason,
                color="0xCC0000",
                title="SHORT ASSET / MANUAL FIX",
            )
        ]

    # Praktisch voll — kein sichtbarer Shortfall-Tail.
    if usable_span + 1e-9 >= need:
        head = _resolve_shot_media(
            project,
            shot_id=timed.slot_id,
            asset_id=str(entry.get("canonical_id") or asset_id),
            entry=entry,
            timeline_start=timed.start_seconds,
            timeline_end=timed.end_seconds,
            fps=fps,
            head_trim=head_trim,
            short_tolerance=max(short_tolerance, need),
            editorial_function=timed.narrative_function,
            may_overlap_pause=False,
            repairs=repairs,
        )
        head.asset_fit = timed.asset_fit
        head.asset_fit_reason = timed.asset_fit_reason
        head.cut_alignment = timed.cut_alignment
        head.coverage_gap_id = timed.coverage_gap_id
        head.open_gap = False
        return [head]

    asset_end = _seconds_to_frame(timed.start_seconds + usable_span, rate)
    if asset_end <= timed.start_seconds + 1e-9:
        return [
            _placeholder_resolved_shot(
                project,
                timed,
                fps=fps,
                asset_id=asset_id,
                coverage_gap_id=gap_id,
                asset_fit="weak",
                asset_fit_reason=reason,
                color="0xCC0000",
                title="SHORT ASSET / MANUAL FIX",
            )
        ]

    head = _resolve_shot_media(
        project,
        shot_id=timed.slot_id,
        asset_id=str(entry.get("canonical_id") or asset_id),
        entry=entry,
        timeline_start=timed.start_seconds,
        timeline_end=asset_end,
        fps=fps,
        head_trim=head_trim,
        short_tolerance=short_tolerance,
        editorial_function=timed.narrative_function,
        may_overlap_pause=False,
        repairs=repairs,
    )
    head.asset_fit = timed.asset_fit or "weak"
    head.asset_fit_reason = (
        f"{reason} — nutzbar {usable_span:.2f}s von {need:.2f}s; "
        "Rest als roter Placeholder."
    )
    head.cut_alignment = timed.cut_alignment
    # Gap nur am Shortfall-Tail — sonst meldet Gap-Merge am Asset-Kopf fälschlich
    # „Kein geeigneter export_ready-Kandidat“ (Funnel bleibt über Plan/Tail).
    head.coverage_gap_id = None
    head.open_gap = False

    shortfall = timed.end_seconds - asset_end
    repairs.append(
        f"{timed.slot_id}: Asset zu kurz — {usable_span:.2f}s Asset + "
        f"{shortfall:.2f}s roter Placeholder (manuell verfeinern)."
    )
    tail = _placeholder_resolved_shot(
        project,
        timed,
        fps=fps,
        asset_id=asset_id,
        coverage_gap_id=gap_id,
        asset_fit="weak",
        asset_fit_reason=reason,
        shot_id=f"{timed.slot_id}__shortfall",
        start_seconds=asset_end,
        end_seconds=timed.end_seconds,
        color="0xCC0000",
        title="SHORTFALL / MANUAL FIX",
    )
    return [head, tail]


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
    include_chapter: Callable[[str], bool] | None = None,
    preroll_override: float | None = None,
    postroll_override: float | None = None,
    catalog_folders: list[str] | None = None,
    apply_keyword_flow_rules: bool | None = None,
) -> ResolvedTimelineDocument:
    """UnifiedCutPlan → ResolvedTimelineDocument (+ Kompat-Schatten).

    ``allow_open_gaps=True`` (Phase 3→4): none-Slots ohne Asset bleiben als
    Platzhalter erhalten. ``False``: offene none-Slots sind Fehler (Produktion).

    ``include_chapter`` / ``preroll_override`` / ``postroll_override``:
    Intro-only Resolve ohne Gesamt-Timeline zu schreiben.

    ``catalog_folders``: Asset-Katalog nur für diese Ordner bauen (Kapitel-Timing).

    ``apply_keyword_flow_rules``: ``None`` = aus Cut-Plan-Stil; Intro-Resolve
    setzt ``False`` — Intro hat eigenen Prompt (kein KF closing_fallback-Fit).
    """
    locked = require_locked_script(project)
    if plan is None:
        plan = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if plan is None:
        raise UnifiedTimelineError("Unified Cut Plan fehlt.")

    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        ingest_coverage_gap_inbox,
    )

    ingest_coverage_gap_inbox(project)

    timings = load_segment_timings(project)
    if timings is None:
        raise UnifiedTimelineError("Segment-Timings fehlen.")

    errors: list[str] = []
    repairs: list[str] = []
    fps = float(project.fps)
    options = load_cut_plan_options(project)
    catalog = build_asset_catalog(
        project,
        fps=fps,
        folder_names=catalog_folders,
    )
    errors.extend(catalog.collisions)

    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    segment_to_chapter = _segment_to_chapter_map(locked)
    # Nur Segmente aus dem gesperrten Skript — keine verwaisten Alt-Timings
    # (z. B. frühere Intro_segment_00x), sonst zerfällt Intro.wav in viele Clips.
    live_segment_ids = {seg.segment_id for seg in locked.segments}
    timing_segments = [
        item for item in timings.segments if item.segment_id in live_segment_ids
    ]
    if include_chapter is not None:
        timing_segments = [
            item
            for item in timing_segments
            if include_chapter(segment_to_chapter.get(item.segment_id, ""))
            or include_chapter(item.segment_id)
        ]
        if not timing_segments:
            raise UnifiedTimelineError(
                "Keine Segment-Timings für den gewählten Kapitel-Filter."
            )
    if apply_keyword_flow_rules is None:
        # Keyword Flow Free shares the existing onset/timing pipeline additively.
        # is_keyword_flow_unified_style remains exact for style==keyword_flow.
        keyword_flow = uses_keyword_onset_timing_rules(options)
    else:
        keyword_flow = bool(apply_keyword_flow_rules)
    if keyword_flow:
        from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
            KEYWORD_FLOW_UNSUPPORTED_PAUSE_EXTENSIONS_MESSAGE,
            plan_has_unsupported_keyword_flow_pause_directives,
        )
        from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
            validate_keyword_flow_closing,
        )
        from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
            build_asset_reuse_key_index,
        )
        from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
            enforce_asset_reuse_as_coverage_gaps,
        )

        if plan_has_unsupported_keyword_flow_pause_directives(plan):
            raise UnifiedTimelineError(
                KEYWORD_FLOW_UNSUPPORTED_PAUSE_EXTENSIONS_MESSAGE
            )
        intro_asset_ids = {
            str(aid)
            for aid, entry in (catalog.by_id or {}).items()
            if _is_intro_folder(str((entry or {}).get("folder") or ""))
        }
        plan, reuse_notes = enforce_asset_reuse_as_coverage_gaps(
            plan,
            max_asset_usage=int(options.max_asset_usage),
            min_asset_reuse_distance_shots=int(
                options.min_asset_reuse_distance_shots
            ),
            intro_asset_ids=intro_asset_ids,
            prefer_closing_fallback=True,
            reuse_key_index=build_asset_reuse_key_index(project),
        )
        for note in reuse_notes:
            repairs.append(f"reuse→gap: {note}")
        closing_errors = validate_keyword_flow_closing(plan, catalog=catalog)
        if closing_errors:
            # Closing als ehrliche Gap nach Reuse-Demote: soft bei open gaps.
            if allow_open_gaps and plan.slots:
                last = plan.slots[-1]
                last_is_gap = (
                    str(last.asset_fit or "") == "none"
                    or not str(last.local_asset_id or "").strip()
                )
                if last_is_gap:
                    soft_closing = [
                        e
                        for e in closing_errors
                        if "Primary Closing" in e or "Fallback Closing" in e
                    ]
                    hard_closing = [
                        e for e in closing_errors if e not in soft_closing
                    ]
                    repairs.extend(f"open-gap soft: {m}" for m in soft_closing)
                    errors.extend(hard_closing)
                else:
                    errors.extend(closing_errors)
            else:
                errors.extend(closing_errors)
    words_by_segment: dict[str, list[dict]] = {}
    sentence_rows_by_id: dict[str, dict] | None = None
    if keyword_flow:
        from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
            sentence_rows_from_alignments,
        )
        from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
            load_elevenlabs_alignment_for_segment,
            words_from_elevenlabs_alignment,
        )

        for timing in timing_segments:
            raw = load_elevenlabs_alignment_for_segment(project, timing.segment_id)
            seg_words = words_from_elevenlabs_alignment(raw)
            words_by_segment[timing.segment_id] = [
                {**word, "original_word_index": index}
                for index, word in enumerate(seg_words)
            ]
        sentence_rows_by_id = sentence_rows_from_alignments(
            sentence_index=sentence_index,
            words_by_segment=words_by_segment,
        )
    try:
        from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
            load_elevenlabs_settings,
        )
        from otio_app.services.without_voiceover_enhanced.pause_resolver import (
            author_pause_after_map_from_script,
        )

        # Keyword Flow: keine pause_directives, keine eingefügte Stille,
        # keine Verschiebung von Narration/Wortzeiten.
        timeline = build_narration_timeline(
            script_version=locked.script_version,
            segment_timings=timing_segments,
            pause_directives=[],
            sentence_index=sentence_index,
            enable_keyword_flow_pauses=False,
            segment_words_by_id=words_by_segment,
            fps=fps,
            repairs=repairs,
            author_pause_after_by_segment=author_pause_after_map_from_script(
                locked,
                model_id=load_elevenlabs_settings(project).model_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        from otio_app.services.without_voiceover_enhanced.pause_resolver import (
            PauseResolveError,
        )

        if isinstance(exc, PauseResolveError):
            raise UnifiedTimelineError(str(exc)) from exc
        raise

    # Ziel-Dauer in Slots nachziehen (Funnel-Dauerfilter, Phase 4).
    timed_slots = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentence_index,
        options=options,
        fps=fps,
        repairs=repairs,
        catalog=catalog,
        segment_to_chapter=segment_to_chapter,
        keyword_flow=keyword_flow,
        sentence_rows_by_id=sentence_rows_by_id,
        segment_words_by_id=words_by_segment,
    )
    assert_timed_slots_contiguous(timed_slots, fps=fps)
    for slot, timed in zip(plan.slots, timed_slots):
        slot.target_duration_seconds = round(timed.duration_seconds, 6)

    final_shadow = unified_plan_to_final_shadow(
        plan, sentence_index=sentence_index, timed_slots=timed_slots
    )
    rough_shadow, coverage_shadow = unified_to_rough(plan)

    if preroll_override is not None:
        preroll = max(0.0, float(preroll_override))
    else:
        preroll = resolve_timing_seconds(
            mode=options.voiceover_preroll_mode,
            setting_max=options.voiceover_preroll_sec,
            llm_value=plan.voiceover_preroll_sec,
        )
    if postroll_override is not None:
        postroll = max(0.0, float(postroll_override))
    else:
        postroll = resolve_timing_seconds(
            mode=options.voiceover_postroll_mode,
            setting_max=options.voiceover_postroll_sec,
            llm_value=plan.voiceover_postroll_sec,
        )

    timing_map = {item.segment_id: item for item in timing_segments}
    audio_segments = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map=timing_map,
        fps=fps,
    )
    known_segments = live_segment_ids
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))

    content_timed_slots = [
        timed
        for timed in timed_slots
        if not (
            str(timed.slot_id).startswith("bridge_")
            or str(timed.narrative_function or "") == "chapter_transition"
        )
    ]
    last_content_slot_id = (
        content_timed_slots[-1].slot_id if content_timed_slots else None
    )

    resolved_shots: list[ResolvedShot] = []
    running_usage: dict[str, int] = {}
    timed_usables = _slot_usable_max_from_catalog(
        plan, catalog, head_trim=head_trim
    )
    while len(timed_usables) < len(timed_slots):
        timed_usables.append(None)
    for index, timed in enumerate(timed_slots):
        # E2E-4: Legacy-Bridge-Slots aus alten Plänen überspringen.
        if (
            str(timed.slot_id).startswith("bridge_")
            or str(timed.narrative_function or "") == "chapter_transition"
        ):
            repairs.append(
                f"{timed.slot_id}: Bridge-Slot ignoriert (E2E-4: kein Bridge)."
            )
            continue
        if timed.start_segment_id not in known_segments:
            errors.append(f"{timed.slot_id}: unbekannte Start-Segment-ID.")
            continue
        if timed.end_segment_id not in known_segments:
            errors.append(f"{timed.slot_id}: unbekannte End-Segment-ID.")
            continue
        start_chapter = segment_to_chapter.get(timed.start_segment_id, "")
        end_chapter = segment_to_chapter.get(timed.end_segment_id, "")
        if start_chapter and end_chapter and start_chapter != end_chapter:
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
        is_keyword_flow_closing = bool(
            keyword_flow
            and last_content_slot_id is not None
            and timed.slot_id == last_content_slot_id
        )
        closing_need = max(
            0.0, float(timed.end_seconds) - float(timed.start_seconds)
        )
        if is_keyword_flow_closing:
            from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
                KeywordFlowClosingError,
                choose_closing_asset_for_resolve,
            )

            try:
                asset_id, entry, choice_note = choose_closing_asset_for_resolve(
                    primary_id=asset_id,
                    fallback_id=str(plan.closing_fallback_asset_id or ""),
                    catalog=catalog,
                    min_duration_seconds=closing_need,
                    expected_folder=start_chapter or None,
                    usage_counts=running_usage,
                    max_asset_usage=int(options.max_asset_usage),
                    plan=plan,
                )
            except KeywordFlowClosingError as exc:
                errors.append(f"{timed.slot_id}: {exc}")
                continue
            if "fallback" in choice_note:
                repairs.append(
                    f"{timed.slot_id}: Closing Primary unbrauchbar — "
                    f"Fallback {asset_id} verwendet ({choice_note})."
                )
        else:
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
            mini_resolved = None
            if not is_keyword_flow_closing:
                usable_now = usable_media_duration_seconds(
                    entry, head_trim=head_trim
                )
                if (
                    usable_now is not None
                    and timed.duration_seconds > float(usable_now) + 1e-6
                    and (timed.duration_seconds - float(usable_now))
                    <= short_tolerance + 1e-6
                ):
                    changed, prev_changed = absorb_timed_slot_mini_shortfall(
                        timed_slots,
                        index,
                        usable=float(usable_now),
                        neighbor_usables=timed_usables,
                        fps=fps,
                        short_tolerance=short_tolerance,
                        repairs=repairs,
                    )
                    if changed:
                        prev_ok = True
                        if prev_changed and index > 0:
                            prev_timed = timed_slots[index - 1]
                            prev_entry, _prev_err = lookup_catalog_entry(
                                catalog, str(prev_timed.asset_id or "")
                            )
                            prev_shot_i = next(
                                (
                                    i
                                    for i, shot in enumerate(resolved_shots)
                                    if shot.shot_id == prev_timed.slot_id
                                ),
                                None,
                            )
                            if prev_entry is not None and prev_shot_i is not None:
                                try:
                                    new_prev = _resolve_shot_media(
                                        project,
                                        shot_id=prev_timed.slot_id,
                                        asset_id=str(
                                            prev_entry.get("canonical_id")
                                            or prev_timed.asset_id
                                        ),
                                        entry=prev_entry,
                                        timeline_start=prev_timed.start_seconds,
                                        timeline_end=prev_timed.end_seconds,
                                        fps=fps,
                                        head_trim=head_trim,
                                        short_tolerance=short_tolerance,
                                        editorial_function=prev_timed.narrative_function,
                                        may_overlap_pause=False,
                                        repairs=repairs,
                                    )
                                    keep = resolved_shots[prev_shot_i]
                                    new_prev.asset_fit = keep.asset_fit
                                    new_prev.asset_fit_reason = keep.asset_fit_reason
                                    new_prev.cut_alignment = keep.cut_alignment
                                    new_prev.coverage_gap_id = keep.coverage_gap_id
                                    new_prev.open_gap = False
                                    if not new_prev.folder_name:
                                        new_prev.folder_name = keep.folder_name
                                    resolved_shots[prev_shot_i] = new_prev
                                except TimelineResolveError:
                                    prev_ok = False
                        if prev_ok:
                            try:
                                mini_resolved = _resolve_shot_media(
                                    project,
                                    shot_id=timed.slot_id,
                                    asset_id=str(
                                        entry.get("canonical_id") or asset_id
                                    ),
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
                            except TimelineResolveError:
                                mini_resolved = None
            if mini_resolved is not None:
                resolved = mini_resolved
            elif is_keyword_flow_closing:
                from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
                    KeywordFlowClosingError,
                    choose_closing_asset_for_resolve,
                )

                try:
                    fb_id, fb_entry, choice_note = choose_closing_asset_for_resolve(
                        primary_id=str(timed.asset_id or ""),
                        fallback_id=str(plan.closing_fallback_asset_id or ""),
                        catalog=catalog,
                        primary_failure=msg,
                        min_duration_seconds=closing_need,
                        expected_folder=start_chapter or None,
                        usage_counts=running_usage,
                        max_asset_usage=int(options.max_asset_usage),
                        plan=plan,
                    )
                    resolved = _resolve_shot_media(
                        project,
                        shot_id=timed.slot_id,
                        asset_id=str(fb_entry.get("canonical_id") or fb_id),
                        entry=fb_entry,
                        timeline_start=timed.start_seconds,
                        timeline_end=timed.end_seconds,
                        fps=fps,
                        head_trim=head_trim,
                        short_tolerance=short_tolerance,
                        editorial_function=timed.narrative_function,
                        may_overlap_pause=False,
                        repairs=repairs,
                    )
                    asset_id = fb_id
                    entry = fb_entry
                    repairs.append(
                        f"{timed.slot_id}: Closing Primary Resolve fehlgeschlagen — "
                        f"Fallback {fb_id} verwendet ({choice_note})."
                    )
                except (KeywordFlowClosingError, TimelineResolveError) as fb_exc:
                    errors.append(f"{timed.slot_id}: {fb_exc}")
                    continue
            else:
                is_short = (
                    "zu kurz" in msg.lower() or "knapp über usable" in msg.lower()
                )
                # Zu kurz: Asset behalten + roter Shortfall-Placeholder (statt
                # komplettem Slate). Andere open-gap-Fälle: voller Placeholder.
                if allow_open_gaps and (
                    timed.asset_fit in {"weak", "none"} or is_short
                ):
                    if is_short:
                        _mark_slot_as_duration_gap(
                            plan,
                            timed.slot_id,
                            reason="Asset zu kurz für berechnete Narrationsdauer",
                        )
                        short_parts = _short_asset_with_red_placeholder_tail(
                            project,
                            timed,
                            entry=entry,
                            asset_id=asset_id,
                            fps=fps,
                            head_trim=head_trim,
                            short_tolerance=short_tolerance,
                            repairs=repairs,
                        )
                        for part in short_parts:
                            if not part.folder_name:
                                part.folder_name = start_chapter
                        resolved_shots.extend(short_parts)
                    else:
                        resolved_shots.append(
                            _placeholder_resolved_shot(
                                project,
                                timed,
                                fps=fps,
                                asset_id=asset_id,
                                coverage_gap_id=timed.coverage_gap_id
                                or f"gap_{timed.slot_id}",
                                asset_fit=None,
                                asset_fit_reason=None,
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
        chosen = str(resolved.asset_id or asset_id or "").strip()
        if chosen and not resolved.open_gap:
            running_usage[chosen] = int(running_usage.get(chosen, 0)) + 1

    ordered = sorted(resolved_shots, key=_resolved_shot_sort_key)

    map_decisions: dict[str, dict] = {}
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
        narration_timeline=timeline,
        include_chapter=include_chapter,
        catalog=catalog,
        closing_fallback_asset_id=plan.closing_fallback_asset_id,
        closing_fallback_by_chapter=dict(plan.closing_fallback_by_chapter or {}),
        head_trim=head_trim,
        short_tolerance=short_tolerance,
        enable_map_opener=True,
        map_decisions=map_decisions,
        intro_opener_asset_id=plan.intro_opener_asset_id,
        intro_closing_asset_id=plan.intro_closing_asset_id,
    )
    if map_decisions:
        repairs.append(
            "map_opener_decisions: "
            + ", ".join(
                f"{cid}={info.get('status')}" for cid, info in map_decisions.items()
            )
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

    ordered = sorted(ordered, key=_resolved_shot_sort_key)
    _apply_visual_continuity_rules(
        ordered,
        project=project,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    ordered = sorted(ordered, key=_resolved_shot_sort_key)
    _count_chapter_continuity(chapter_envelopes, ordered, fps=fps)

    # Abstand: alle redaktionellen Shots inkl. open-gap Trenner; Usage nur
    # belegte Assets. Nach Keyword-Flow-Demote sollten Verletzungen leer sein.
    editorial_shots = [
        shot
        for shot in ordered
        if not str(shot.editorial_function or "").startswith("technical_chapter_")
    ]
    filled_shots = [
        shot for shot in editorial_shots if not shot.open_gap and shot.asset_id
    ]
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        build_asset_reuse_key_index,
        reuse_identity_key,
    )

    reuse_index = build_asset_reuse_key_index(project)

    def _reuse_key(asset_id: str | None) -> str:
        return reuse_identity_key(asset_id, index=reuse_index)

    for prev, curr in zip(filled_shots, filled_shots[1:]):
        if not prev.asset_id or not curr.asset_id:
            continue
        if _reuse_key(prev.asset_id) != _reuse_key(curr.asset_id):
            continue
        # Nur direkt benachbart auf der redaktionellen Spur (Gaps dazwischen OK).
        prev_i = next(
            (i for i, s in enumerate(editorial_shots) if s.shot_id == prev.shot_id),
            None,
        )
        curr_i = next(
            (i for i, s in enumerate(editorial_shots) if s.shot_id == curr.shot_id),
            None,
        )
        if prev_i is None or curr_i is None or curr_i - prev_i != 1:
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


    # Provider-kanonisch: pexels_video_123 und supplement_pexels_123 = dieselbe Nutzung.
    usage_counts: Counter[str] = Counter()
    usage_examples: dict[str, str] = {}
    for shot in filled_shots:
        key = _reuse_key(shot.asset_id)
        usage_counts[key] += 1
        usage_examples.setdefault(key, str(shot.asset_id))
    for key, count in sorted(usage_counts.items()):
        example_id = usage_examples.get(key, key)
        folder = str((catalog.by_id.get(example_id) or {}).get("folder") or "")
        if _is_intro_folder(folder):
            continue
        if count > int(options.max_asset_usage):
            errors.append(
                f"Asset {example_id} ({key}) wird {count}× genutzt "
                f"(max_asset_usage={options.max_asset_usage})."
            )

    # min_gap wie Classic: mind. 1 (kein Direkt-Reuse); Setting erhöht.
    min_gap = max(1, int(options.min_asset_reuse_distance_shots or 0))
    last_index: dict[str, int] = {}
    for index, shot in enumerate(editorial_shots):
        if shot.open_gap or not shot.asset_id:
            continue
        folder = str(
            shot.folder_name
            or (catalog.by_id.get(shot.asset_id) or {}).get("folder")
            or ""
        )
        if _is_intro_folder(folder):
            continue
        key = _reuse_key(shot.asset_id)
        prev_index = last_index.get(key)
        if prev_index is not None:
            gap_shots = index - prev_index - 1
            if gap_shots < min_gap:
                message = (
                    f"{shot.shot_id}: Asset {shot.asset_id} erneut nach "
                    f"{gap_shots} Shots (min Abstand {min_gap})."
                )
                # Keyword Flow: nach Pre-Resolve-Demote Rest hart failen.
                # Andere Stile: Direkt-Reuse hart, sonst soft (wie zuvor).
                if keyword_flow or gap_shots == 0:
                    errors.append(message)
                else:
                    repairs.append(message)
        last_index[key] = index

    # Keyword Flow Free: Settings shot_min/max are the only shot-length band.
    # Keep legacy 10–17s soft band for all other unified styles (unchanged).
    include_legacy_shot_length_band = not is_keyword_flow_free_unified_style(options)
    repairs.extend(
        assess_cut_rhythm(
            final_shadow,
            ordered,
            include_legacy_shot_length_band=include_legacy_shot_length_band,
        )
    )

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
        plan=plan,
        resolved=document,
        options=options,
        include_legacy_shot_length_band=include_legacy_shot_length_band,
    )
    for note in quality.all_notes():
        if note not in document.repairs:
            document.repairs.append(note)
    repairs = document.repairs

    if persist:
        from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
            enrich_coverage_search_concepts,
        )

        coverage_shadow = enrich_coverage_search_concepts(
            project, coverage_shadow, plan=plan
        )
        from otio_app.services.without_voiceover_enhanced.gap_status_service import (
            carry_over_user_confirmed_weak,
        )

        previous_coverage = load_model(
            coverage_gaps_path(project), CoverageGapsDocument
        )
        coverage_shadow = carry_over_user_confirmed_weak(
            coverage_shadow, previous_coverage
        )
        write_json(unified_cut_plan_path(project), plan)
        write_json(narration_timeline_path(project), timeline)
        write_json(final_cut_plan_path(project), final_shadow)
        write_json(rough_cut_plan_path(project), rough_shadow)
        from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
            persist_coverage_gaps,
        )

        persist_coverage_gaps(project, coverage_shadow)
        write_json(resolved_timeline_path(project), document)
        write_json(repair_log_path(project), {"repairs": repairs, "errors": errors})

    if errors:
        raise UnifiedTimelineError("\n".join(errors), errors=errors)
    return document
