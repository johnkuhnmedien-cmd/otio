"""Tests für Shot-Timing und Outro-Aufteilung."""

from __future__ import annotations

import pytest

from otio_app.services.duration_rules import split_total_duration
from otio_app.services.shot_timing import (
    TimedPart,
    allocate_time_by_text,
    allocate_time_with_constraints,
    coalesce_gemini_parts_for_min_shot,
    max_parts_for_segment,
    merge_short_voice_windows,
    normalize_gemini_parts_for_segment,
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


def test_coalesce_gemini_parts_merges_micro_fragments_for_min_shot() -> None:
    parts = [
        {"text": "Das spanische Moos hängt", "motif": "Moos", "asset_path": "/a.mp4", "match_quality": "gut"},
        {"text": "Bäume.", "motif": "Detail", "asset_path": "/b.mp4", "match_quality": "gut"},
        {"text": "Jahrhundert.", "motif": "Detail", "asset_path": "/c.mp4", "match_quality": "mittel"},
    ]
    merged = coalesce_gemini_parts_for_min_shot(
        parts,
        segment_duration=7.5,
        min_sec=5.0,
        max_sec=10.0,
    )
    assert len(merged) == 1
    assert "Bäume" in merged[0]["text"]


def test_max_parts_for_segment_respects_min_shot() -> None:
    assert max_parts_for_segment(7.5, min_sec=5.0) == 1
    assert max_parts_for_segment(12.0, min_sec=5.0) == 2
    assert max_parts_for_segment(2.8, min_sec=5.0) == 1


def test_merge_short_voice_windows_combines_tail_slices() -> None:
    texts = ["a", "b", "c", "d"]
    ranges = allocate_time_with_constraints(
        88.0,
        94.88,
        texts,
        min_sec=3.0,
        max_sec=8.0,
    )
    timed = [
        TimedPart(t, "m", start, end, "/tmp/clip.mp4", None)
        for t, (start, end) in zip(texts, ranges)
    ]
    merged = merge_short_voice_windows(timed, min_sec=3.0)
    assert len(merged) < len(timed)
    assert merged[-1].end_sec == pytest.approx(94.88, abs=0.01)
    assert all(part.end_sec - part.start_sec + 0.01 >= 3.0 for part in merged[:-1])


def test_allocate_time_with_constraints_respects_min_and_max() -> None:
    """Regression: textgewichtete Aufteilung darf keine Shots < min oder > max erzeugen."""
    texts = ["kurz", "sehr langer Textanteil mit vielen Wörtern"]
    ranges = allocate_time_with_constraints(
        0.0,
        10.0,
        texts,
        min_sec=3.0,
        max_sec=8.0,
    )
    durations = [end - start for start, end in ranges]
    assert len(durations) == 2
    assert sum(durations) == pytest.approx(10.0, abs=0.05)
    assert all(3.0 - 0.05 <= duration <= 8.0 + 0.05 for duration in durations)


def test_allocate_time_with_constraints_short_segment_single_range() -> None:
    ranges = allocate_time_with_constraints(
        5.0,
        7.4,
        ["kurzer Text"],
        min_sec=3.0,
        max_sec=8.0,
    )
    assert ranges == [(5.0, 7.4)]


def test_normalize_gemini_parts_merges_when_too_many() -> None:
    parts = [
        {"text": f"Teil {index}", "motif": "m", "asset_path": f"/{index}.mp4"}
        for index in range(6)
    ]
    result = normalize_gemini_parts_for_segment(
        parts,
        segment_duration=18.0,
        min_sec=3.0,
        max_sec=8.0,
    )
    assert result.part_count_ok is True
    assert len(result.parts) <= 6
    assert len(result.parts) >= 3


def test_normalize_gemini_parts_flags_insufficient_parts_for_long_segment() -> None:
    parts = [{"text": "Ein langer Block", "motif": "m", "asset_path": "/a.mp4"}]
    result = normalize_gemini_parts_for_segment(
        parts,
        segment_duration=18.0,
        min_sec=3.0,
        max_sec=8.0,
    )
    assert result.part_count_ok is False
    assert result.part_count_error_type == "INSUFFICIENT_PARTS"
    assert result.allowed_parts_min == 3
    assert result.actual_parts == 1


def test_normalize_gemini_parts_allows_single_part_for_short_segment() -> None:
    parts = [{"text": "Kurz", "motif": "m", "asset_path": "/a.mp4"}]
    result = normalize_gemini_parts_for_segment(
        parts,
        segment_duration=2.4,
        min_sec=3.0,
        max_sec=8.0,
    )
    assert result.part_count_ok is True
    assert result.short_segment_allowed is True
    assert len(result.parts) == 1
