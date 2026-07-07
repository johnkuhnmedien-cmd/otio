"""Tests für Shot-Timing und Outro-Aufteilung."""

from __future__ import annotations

import pytest

from otio_app.services.duration_rules import split_total_duration
from otio_app.services.shot_timing import (
    TimedPart,
    allocate_time_by_text,
    merge_short_voice_windows,
    shots_from_timed_parts,
)


def test_split_total_duration_14_seconds() -> None:
    assert split_total_duration(14.0) == [7.0, 7.0]


def test_split_total_duration_5_seconds() -> None:
    assert split_total_duration(5.0) == [5.0]


def test_split_total_duration_respects_custom_max_sec() -> None:
    """Regression: Outro/Filler-Splitting muss die projektspezifische
    Max.-Shot-Regel respektieren, nicht den globalen Default (8s)."""
    durations = split_total_duration(10.0, min_sec=2.0, max_sec=4.0)
    assert durations
    assert all(d <= 4.0 + 1e-6 for d in durations)


def test_shots_from_timed_parts_never_exceeds_max_sec_when_min_within_range() -> None:
    parts = [
        TimedPart(
            text="kurzer Text",
            motif="Motiv",
            start_sec=0.0,
            end_sec=5.0,
            asset_path="/tmp/clip.mp4",
            confidence=None,
        )
    ]
    result = shots_from_timed_parts(parts, min_sec=3.0, max_sec=8.0)
    assert all(part.end_sec - part.start_sec <= 8.0 + 1e-6 for part in result)


def test_shots_from_timed_parts_caps_at_max_sec_when_min_sec_misconfigured_above_max() -> None:
    """Regression: Wenn Min. Shot versehentlich > Max. Shot gesetzt wird, darf
    trotzdem kein Shot entstehen, der Max. Shot verletzt (vorher: min_sec
    überschrieb max_sec via `max(min_sec, duration)` ohne Obergrenze,
    z.B. min=9s, max=8s -> Shot wurde fälschlich auf 9.0s gesetzt)."""
    parts = [
        TimedPart(
            text="kurzer Text",
            motif="Motiv",
            start_sec=0.0,
            end_sec=5.0,
            asset_path="/tmp/clip.mp4",
            confidence=None,
        )
    ]
    result = shots_from_timed_parts(parts, min_sec=9.0, max_sec=8.0)
    assert len(result) == 1
    duration = result[0].end_sec - result[0].start_sec
    assert duration <= 8.0 + 1e-6, f"duration {duration}s verletzt max_sec=8.0s"


def test_shots_from_timed_parts_splitting_respects_misconfigured_min_max() -> None:
    """Selbe Regression, aber im Splitting-Zweig (Abschnitt länger als max_sec)."""
    parts = [
        TimedPart(
            text="langer Text",
            motif="Motiv",
            start_sec=0.0,
            end_sec=20.0,
            asset_path="/tmp/clip.mp4",
            confidence=None,
        )
    ]
    result = shots_from_timed_parts(parts, min_sec=9.0, max_sec=8.0)
    for part in result:
        duration = part.end_sec - part.start_sec
        assert duration <= 8.0 + 1e-6, f"duration {duration}s verletzt max_sec=8.0s"


def test_shots_from_timed_parts_never_extends_past_voice_end() -> None:
    """Regression: Min.-Shot darf voice_end nicht über part.end_sec (Dateiende) schieben."""
    parts = [TimedPart("Ende", "Motiv", 94.31, 94.88, "/tmp/clip.mp4", None)]
    result = shots_from_timed_parts(parts, min_sec=3.0, max_sec=8.0)
    assert len(result) == 1
    assert result[0].end_sec <= 94.88 + 1e-6
    assert result[0].end_sec - result[0].start_sec == pytest.approx(0.57, abs=0.01)


def test_merge_short_voice_windows_combines_tail_slices() -> None:
    texts = ["a", "b", "c", "d"]
    ranges = allocate_time_by_text(88.0, 94.88, texts)
    timed = [
        TimedPart(t, "m", start, end, "/tmp/clip.mp4", None)
        for t, (start, end) in zip(texts, ranges)
    ]
    merged = merge_short_voice_windows(timed, min_sec=3.0)
    assert len(merged) < len(timed)
    assert merged[-1].end_sec == pytest.approx(94.88, abs=0.01)
    assert all(part.end_sec - part.start_sec + 0.01 >= 3.0 for part in merged[:-1])
