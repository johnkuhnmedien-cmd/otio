"""Zeitlogik für Schnittplan-Shots."""

from __future__ import annotations

from dataclasses import dataclass


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
