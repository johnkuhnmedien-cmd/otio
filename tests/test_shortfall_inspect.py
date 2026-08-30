"""Shortfall-Ansicht: Asset, Datei und nutzbare vs. nötige Dauer."""

from __future__ import annotations

import pytest

from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.shortfall_inspect import (
    collect_shortfall_rows_from_resolved,
    format_shortfall_inspect_label,
    production_blocking_placeholder_labels,
)


def _shot(
    shot_id: str,
    *,
    asset_id: str = "cave_walk",
    start: float = 0.0,
    end: float = 5.0,
    placeholder: bool = False,
    path: str = "",
) -> ResolvedShot:
    return ResolvedShot(
        shot_id=shot_id,
        asset_id=asset_id,
        timeline_start_seconds=start,
        timeline_end_seconds=end,
        source_start_seconds=0.0,
        source_end_seconds=max(0.0, end - start),
        resolved_media_path=path,
        is_placeholder=placeholder,
        open_gap=placeholder,
        coverage_gap_id="gap_slot_010" if placeholder else None,
        folder_name="Škocjan Caves",
    )


def test_shortfall_label_includes_asset_and_durations() -> None:
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        shots=[
            _shot(
                "Skocjan_Caves_slot_010",
                path="/media/cave_walk.mp4",
                start=0.0,
                end=4.2,
            ),
            _shot(
                "Skocjan_Caves_slot_010__shortfall",
                path="/tmp/slate.mp4",
                start=4.2,
                end=12.1,
                placeholder=True,
            ),
        ],
    )
    rows = collect_shortfall_rows_from_resolved(
        resolved, folder_name="Škocjan Caves"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.slot_id == "Skocjan_Caves_slot_010"
    assert row.asset_id == "cave_walk"
    assert row.filename == "cave_walk.mp4"
    assert row.usable_seconds == pytest.approx(4.2)
    assert row.need_seconds == pytest.approx(12.1)
    assert row.shortfall_seconds == pytest.approx(7.9)
    label = format_shortfall_inspect_label(row)
    assert "cave_walk" in label
    assert "cave_walk.mp4" in label
    assert "4.2" in label
    assert "12.1" in label
    labels = production_blocking_placeholder_labels(resolved)
    assert any("cave_walk" in item and "slot_010" in item for item in labels)


def test_open_gap_without_parent_still_lists_asset_id() -> None:
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=8.0,
        shots=[
            _shot(
                "Ptuj_slot_003",
                asset_id="short_clip",
                start=0.0,
                end=8.0,
                placeholder=True,
            )
        ],
    )
    labels = production_blocking_placeholder_labels(resolved)
    assert len(labels) == 1
    assert "short_clip" in labels[0]
    assert "Ptuj_slot_003" in labels[0]
