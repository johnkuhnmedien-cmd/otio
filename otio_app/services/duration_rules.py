"""Dauerregeln für Timeline-Elemente (3–8 s)."""

from __future__ import annotations

import math

from otio_app.defaults import DEFAULT_SHOT_MAX_SEC, DEFAULT_SHOT_MIN_SEC

MIN_DURATION_SEC = DEFAULT_SHOT_MIN_SEC
MAX_DURATION_SEC = DEFAULT_SHOT_MAX_SEC


def clamp_duration_sec(
    duration_sec: float,
    *,
    min_sec: float = MIN_DURATION_SEC,
    max_sec: float = MAX_DURATION_SEC,
) -> float:
    return max(min_sec, min(max_sec, duration_sec))


def split_total_duration(
    total_sec: float,
    *,
    min_sec: float = MIN_DURATION_SEC,
    max_sec: float = MAX_DURATION_SEC,
) -> list[float]:
    """Teilt eine Gesamtdauer in mehrere Elemente à 3–8 s auf."""
    if total_sec <= 0:
        return []
    if total_sec <= max_sec:
        return [total_sec]

    n_min = max(1, math.ceil(total_sec / max_sec))
    n_max = max(n_min, math.floor(total_sec / min_sec))
    for count in range(n_min, n_max + 1):
        part = total_sec / count
        if min_sec <= part <= max_sec:
            rounded = [round(part, 4) for _ in range(count - 1)]
            rounded.append(round(total_sec - sum(rounded), 4))
            return rounded

    chunks: list[float] = []
    remaining = total_sec
    slots = n_min
    for index in range(slots - 1):
        chunk = min(max_sec, max(min_sec, remaining - min_sec * (slots - index - 1)))
        chunks.append(round(chunk, 4))
        remaining = round(remaining - chunk, 4)
    chunks.append(round(remaining, 4))
    return chunks
