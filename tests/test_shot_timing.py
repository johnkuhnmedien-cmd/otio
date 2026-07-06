"""Tests für Shot-Zeitlogik."""

from __future__ import annotations

from otio_app.services.shot_timing import (
    TimedPart,
    allocate_time_by_text,
    shots_from_timed_parts,
)


def test_allocate_time_by_text_splits_proportionally() -> None:
    ranges = allocate_time_by_text(0.0, 10.0, ["aaa", "aaaaaa"])
    assert len(ranges) == 2
    assert ranges[0][0] == 0.0
    assert ranges[1][1] == 10.0
    assert ranges[0][1] <= ranges[1][0]
    assert abs(ranges[0][1] - 3.333) < 0.01


def test_shots_from_timed_parts_splits_long_segments() -> None:
    parts = [
        TimedPart(
            text="langer abschnitt",
            motif="bridge",
            start_sec=0.0,
            end_sec=20.0,
            asset_path="/tmp/a.mp4",
            confidence="high",
        )
    ]
    shots = shots_from_timed_parts(parts, min_sec=3.0, max_sec=8.0)
    assert len(shots) >= 2
    assert all(3.0 <= (shot.end_sec - shot.start_sec) <= 8.0 for shot in shots)
