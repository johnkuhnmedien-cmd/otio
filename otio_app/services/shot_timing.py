"""Zeitlogik für Schnittplan-Shots."""

from __future__ import annotations

import math
from dataclasses import dataclass

from otio_app.defaults import (
    MATCH_QUALITY_GUT,
    MATCH_QUALITY_MITTEL,
    MATCH_QUALITY_SEHR_GUT,
    MATCH_QUALITY_UNPASSEND,
)

_QUALITY_RANK = {
    MATCH_QUALITY_SEHR_GUT: 0,
    MATCH_QUALITY_GUT: 1,
    MATCH_QUALITY_MITTEL: 2,
    MATCH_QUALITY_UNPASSEND: 3,
    "": 4,
}


@dataclass(frozen=True)
class AllowedPartsBounds:
    min_parts: int
    max_parts: int
    short_segment_allowed: bool

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "min_parts": self.min_parts,
            "max_parts": self.max_parts,
            "short_segment_allowed": self.short_segment_allowed,
        }


def allowed_parts_for_segment(
    segment_duration: float,
    *,
    min_sec: float,
    max_sec: float,
) -> AllowedPartsBounds:
    """Erlaubte Gemini-parts pro Voice-Segment unter Min./Max.-Shot-Regeln."""
    if segment_duration <= 0.05 or min_sec <= 0 or max_sec <= 0:
        return AllowedPartsBounds(min_parts=1, max_parts=1, short_segment_allowed=True)

    if segment_duration + 0.01 < min_sec:
        return AllowedPartsBounds(min_parts=1, max_parts=1, short_segment_allowed=True)

    min_parts = max(1, math.ceil(segment_duration / max_sec))
    max_parts = max(1, math.floor(segment_duration / min_sec))
    if min_parts > max_parts:
        max_parts = min_parts

    return AllowedPartsBounds(
        min_parts=min_parts,
        max_parts=max_parts,
        short_segment_allowed=False,
    )


def max_parts_for_segment(segment_duration: float, *, min_sec: float) -> int:
    """Maximale sinnvolle parts-Anzahl, damit jeder Teil mindestens min_sec erhalten kann."""
    if segment_duration <= 0.05 or min_sec <= 0:
        return 1
    if segment_duration + 0.01 < min_sec:
        return 1
    return max(1, int(segment_duration // min_sec))


def _pick_better_match_quality(left: str, right: str) -> str:
    left_rank = _QUALITY_RANK.get(left, 4)
    right_rank = _QUALITY_RANK.get(right, 4)
    return left if left_rank <= right_rank else right


def _merge_gemini_parts(left: dict, right: dict) -> dict:
    left_quality = str(left.get("match_quality", "")).strip()
    right_quality = str(right.get("match_quality", "")).strip()
    asset_path = left.get("asset_path") or right.get("asset_path")
    if left.get("asset_path") and right.get("asset_path"):
        if _QUALITY_RANK.get(right_quality, 4) < _QUALITY_RANK.get(left_quality, 4):
            asset_path = right.get("asset_path")
    return {
        "text": f"{str(left.get('text', '')).strip()} {str(right.get('text', '')).strip()}".strip(),
        "motif": str(left.get("motif", "")).strip() or str(right.get("motif", "")).strip(),
        "asset_path": asset_path,
        "match_quality": _pick_better_match_quality(left_quality, right_quality),
        "confidence": left.get("confidence") or right.get("confidence") or "low",
    }


def coalesce_gemini_parts_for_min_shot(
    parts: list[dict],
    *,
    segment_duration: float,
    min_sec: float,
    max_sec: float,
) -> list[dict]:
    """Führt Gemini-parts zusammen, bevor die Zeit verteilt wird (Min.-Shot erzwingen)."""
    return normalize_gemini_parts_for_segment(
        parts,
        segment_duration=segment_duration,
        min_sec=min_sec,
        max_sec=max_sec,
    ).parts


@dataclass(frozen=True)
class NormalizePartsResult:
    parts: list[dict]
    part_count_ok: bool
    part_count_error_type: str | None = None
    allowed_parts_min: int = 1
    allowed_parts_max: int = 1
    actual_parts: int = 0
    short_segment_allowed: bool = False


def _merge_parts_down_to_count(parts: list[dict], target_count: int) -> list[dict]:
    merged = [dict(part) for part in parts]
    while len(merged) > target_count:
        best_index = 0
        best_weight = len(str(merged[0].get("text", ""))) + len(str(merged[1].get("text", "")))
        for index in range(len(merged) - 1):
            weight = len(str(merged[index].get("text", ""))) + len(
                str(merged[index + 1].get("text", ""))
            )
            if weight < best_weight:
                best_weight = weight
                best_index = index
        merged[best_index] = _merge_gemini_parts(merged[best_index], merged[best_index + 1])
        merged.pop(best_index + 1)
    return merged


def normalize_gemini_parts_for_segment(
    parts: list[dict],
    *,
    segment_duration: float,
    min_sec: float,
    max_sec: float,
) -> NormalizePartsResult:
    """Normalisiert Gemini-parts auf erlaubte Anzahl; meldet zu wenige/zu viele Parts."""
    bounds = allowed_parts_for_segment(segment_duration, min_sec=min_sec, max_sec=max_sec)
    if not parts:
        return NormalizePartsResult(
            parts=[],
            part_count_ok=True,
            allowed_parts_min=bounds.min_parts,
            allowed_parts_max=bounds.max_parts,
            short_segment_allowed=bounds.short_segment_allowed,
        )

    merged = [dict(part) for part in parts]
    if segment_duration <= 0.05:
        return NormalizePartsResult(
            parts=merged,
            part_count_ok=True,
            allowed_parts_min=bounds.min_parts,
            allowed_parts_max=bounds.max_parts,
            actual_parts=len(merged),
            short_segment_allowed=bounds.short_segment_allowed,
        )

    if bounds.short_segment_allowed:
        combined = merged[0]
        for part in merged[1:]:
            combined = _merge_gemini_parts(combined, part)
        return NormalizePartsResult(
            parts=[combined],
            part_count_ok=True,
            allowed_parts_min=bounds.min_parts,
            allowed_parts_max=bounds.max_parts,
            actual_parts=1,
            short_segment_allowed=True,
        )

    merged = _merge_parts_down_to_count(merged, bounds.max_parts)
    actual = len(merged)
    part_count_ok = bounds.min_parts <= actual <= bounds.max_parts
    error_type: str | None = None
    if actual < bounds.min_parts:
        error_type = "INSUFFICIENT_PARTS"
    elif actual > bounds.max_parts:
        error_type = "TOO_MANY_PARTS"

    return NormalizePartsResult(
        parts=merged,
        part_count_ok=part_count_ok,
        part_count_error_type=error_type,
        allowed_parts_min=bounds.min_parts,
        allowed_parts_max=bounds.max_parts,
        actual_parts=actual,
        short_segment_allowed=False,
    )


@dataclass(frozen=True)
class TimedPart:
    text: str
    motif: str
    start_sec: float
    end_sec: float
    asset_path: str | None
    confidence: str | None
    match_quality: str | None = None


def split_duration_evenly(start_sec: float, end_sec: float, parts: int) -> list[tuple[float, float]]:
    if parts <= 0:
        return []
    total = max(0.0, end_sec - start_sec)
    if parts == 1:
        return [(start_sec, end_sec)]
    step = total / parts
    ranges: list[tuple[float, float]] = []
    for index in range(parts):
        part_start = start_sec + step * index
        part_end = start_sec + step * (index + 1)
        ranges.append((part_start, part_end))
    return ranges


def _part_voice_duration(part: TimedPart) -> float:
    return max(0.0, part.end_sec - part.start_sec)


def _merge_timed_parts(left: TimedPart, right: TimedPart) -> TimedPart:
    return TimedPart(
        text=f"{left.text} {right.text}".strip(),
        motif=left.motif or right.motif,
        start_sec=left.start_sec,
        end_sec=right.end_sec,
        asset_path=left.asset_path or right.asset_path,
        confidence=left.confidence or right.confidence,
        match_quality=left.match_quality or right.match_quality,
    )


def merge_short_voice_windows(parts: list[TimedPart], *, min_sec: float) -> list[TimedPart]:
    """Führt Teile zusammen, deren Voice-Fenster kürzer als min_sec ist."""
    if not parts or min_sec <= 0:
        return list(parts)
    merged = list(parts)
    while True:
        merged_short = False
        for index, part in enumerate(merged):
            if _part_voice_duration(part) + 0.01 >= min_sec:
                continue
            if index + 1 < len(merged):
                merged[index + 1] = _merge_timed_parts(part, merged[index + 1])
                merged.pop(index)
                merged_short = True
                break
            if index > 0:
                merged[index - 1] = _merge_timed_parts(merged[index - 1], part)
                merged.pop(index)
                merged_short = True
                break
        if not merged_short:
            break
    return merged


def _clamp_shot_duration(
    duration: float,
    *,
    min_sec: float,
    max_sec: float,
    voice_span: float,
) -> float:
    """Shot-Dauer innerhalb des Voice-Fensters — nie über voice_span hinaus verlängern."""
    if voice_span <= 0:
        return 0.0
    target = min(voice_span, min(max_sec, max(min_sec, duration)))
    return min(target, voice_span)


def allocate_time_by_text(
    start_sec: float,
    end_sec: float,
    texts: list[str],
) -> list[tuple[float, float]]:
    if not texts:
        return []
    weights = [max(1, len(text.strip())) for text in texts]
    total_weight = sum(weights)
    total = max(0.0, end_sec - start_sec)
    ranges: list[tuple[float, float]] = []
    cursor = start_sec
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            ranges.append((cursor, end_sec))
        else:
            part_duration = total * (weight / total_weight)
            ranges.append((cursor, cursor + part_duration))
            cursor += part_duration
    return ranges


def _round_to_frame_boundary(sec: float, fps: float) -> float:
    if fps <= 0:
        return sec
    frame = 1.0 / fps
    return round(sec / frame) * frame


def allocate_time_with_constraints(
    start_sec: float,
    end_sec: float,
    texts: list[str],
    *,
    min_sec: float,
    max_sec: float,
    fps: float = 25.0,
) -> list[tuple[float, float]]:
    """Verteilt Segmentzeit auf Parts — harte Min./Max.-Shot-Grenzen, Text nur als Gewicht."""
    if not texts:
        return []
    part_count = len(texts)
    total = max(0.0, end_sec - start_sec)
    if part_count == 1 or total + 0.01 < min_sec:
        return [(start_sec, end_sec)]

    weights = [max(1.0, float(len(text.strip()))) for text in texts]
    weight_sum = sum(weights)
    durations = [total * weight / weight_sum for weight in weights]

    for _ in range(part_count * 4):
        changed = False
        for index, duration in enumerate(durations):
            if duration > max_sec + 1e-9:
                surplus = duration - max_sec
                durations[index] = max_sec
                recipients = [
                    recipient
                    for recipient in range(part_count)
                    if recipient != index and durations[recipient] < max_sec - 1e-9
                ]
                for recipient in recipients:
                    headroom = max_sec - durations[recipient]
                    give = min(surplus / max(1, len(recipients)), headroom)
                    durations[recipient] += give
                    surplus -= give
                changed = True
        for index, duration in enumerate(durations):
            if duration < min_sec - 1e-9:
                deficit = min_sec - duration
                durations[index] = min_sec
                donors = [
                    donor
                    for donor in range(part_count)
                    if donor != index and durations[donor] > min_sec + 1e-9
                ]
                for donor in donors:
                    available = durations[donor] - min_sec
                    take = min(deficit / max(1, len(donors)), available)
                    durations[donor] -= take
                    deficit -= take
                changed = True
        if not changed:
            break

    drift = total - sum(durations)
    if abs(drift) > 1e-6:
        durations[-1] += drift

    cursor = start_sec
    ranges: list[tuple[float, float]] = []
    for index, duration in enumerate(durations):
        if index == part_count - 1:
            ranges.append((cursor, end_sec))
            continue
        next_cursor = _round_to_frame_boundary(cursor + duration, fps)
        if next_cursor <= cursor + 1e-9:
            next_cursor = cursor + duration
        ranges.append((cursor, next_cursor))
        cursor = next_cursor
    return ranges


def shots_from_timed_parts(
    parts: list[TimedPart],
    *,
    min_sec: float,
    max_sec: float,
) -> list[TimedPart]:
    """Teilt zu lange Motiv-Abschnitte in mehrere Shots (3–8 s)."""
    result: list[TimedPart] = []
    for part in parts:
        duration = max(0.0, part.end_sec - part.start_sec)
        if duration <= 0:
            continue
        if duration <= max_sec:
            # max_sec ist die harte Obergrenze — falls min_sec (Fehlkonfiguration:
            # min > max) größer als max_sec ist, darf min_sec sie trotzdem NICHT
            # überschreiben. Sonst entstehen Shots, die die eigene Max-Regel
            # verletzen (siehe Validierung „final_duration_sec > max“).
            # Voice-Fenster (part.end_sec) ist hart: Min.-Shot darf nicht darüber hinaus
            # verlängern, sonst entsteht Voice-over > Dateilänge.
            clamped_duration = _clamp_shot_duration(
                duration,
                min_sec=min_sec,
                max_sec=max_sec,
                voice_span=duration,
            )
            result.append(
                TimedPart(
                    text=part.text,
                    motif=part.motif,
                    start_sec=part.start_sec,
                    end_sec=part.start_sec + clamped_duration,
                    asset_path=part.asset_path,
                    confidence=part.confidence,
                    match_quality=part.match_quality,
                )
            )
            continue

        parts_needed = max(1, int(duration // max_sec) + (1 if duration % max_sec else 0))
        if duration / parts_needed < min_sec:
            parts_needed = max(1, int(duration // min_sec))
        for sub_start, sub_end in split_duration_evenly(
            part.start_sec, part.end_sec, parts_needed
        ):
            sub_span = max(0.0, sub_end - sub_start)
            sub_duration = _clamp_shot_duration(
                sub_span,
                min_sec=min_sec,
                max_sec=max_sec,
                voice_span=sub_span,
            )
            result.append(
                TimedPart(
                    text=part.text,
                    motif=part.motif,
                    start_sec=sub_start,
                    end_sec=sub_start + sub_duration,
                    asset_path=part.asset_path,
                    confidence=part.confidence,
                    match_quality=part.match_quality,
                )
            )
    return result
