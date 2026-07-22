"""Echtzeit-Timeline: Editorial-Anker → Sekunden + HTML-Spuren."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import (
    EditorialAnchor,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    RoughCutPlanDocument,
    RoughShot,
)
from otio_app.ui.without_voiceover_enhanced.timeline_view import (
    build_timeline_html,
    editorial_anchor_to_seconds,
    final_shot_blocks,
    narration_blocks,
    rough_shot_blocks,
)


def _timeline() -> NarrationTimelineDocument:
    return NarrationTimelineDocument(
        script_version="script-v1",
        total_duration_seconds=12.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="segment_001",
                start_seconds=0.0,
                end_seconds=4.0,
                pause_after_seconds=1.0,
                next_segment_start_seconds=5.0,
            ),
            NarrationTimelineEntry(
                segment_id="segment_002",
                start_seconds=5.0,
                end_seconds=11.0,
                pause_after_seconds=1.0,
            ),
        ],
    )


def test_editorial_anchor_segment_positions() -> None:
    timeline = _timeline()
    start = editorial_anchor_to_seconds(
        EditorialAnchor(type="segment", segment_id="segment_001", position="start"),
        timeline,
    )
    middle = editorial_anchor_to_seconds(
        EditorialAnchor(type="segment", segment_id="segment_001", position="middle"),
        timeline,
    )
    end = editorial_anchor_to_seconds(
        EditorialAnchor(type="segment", segment_id="segment_001", position="end"),
        timeline,
    )
    assert start == 0.0
    assert middle == 2.0
    assert end == 4.0


def test_editorial_anchor_pause_positions() -> None:
    timeline = _timeline()
    pause_start = editorial_anchor_to_seconds(
        EditorialAnchor(
            type="pause",
            after_segment_id="segment_001",
            position="start",
        ),
        timeline,
    )
    pause_end = editorial_anchor_to_seconds(
        EditorialAnchor(
            type="pause",
            after_segment_id="segment_001",
            position="end",
        ),
        timeline,
    )
    assert pause_start == 4.0
    assert pause_end == 5.0


def test_narration_and_rough_blocks_and_html() -> None:
    timeline = _timeline()
    rough = RoughCutPlanDocument(
        script_version="script-v1",
        shots=[
            RoughShot(
                shot_id="shot_001",
                start_anchor=EditorialAnchor(
                    type="segment", segment_id="segment_001", position="start"
                ),
                end_anchor=EditorialAnchor(
                    type="segment", segment_id="segment_002", position="middle"
                ),
                local_asset_id="asset_a",
                narrative_function="orientation",
            )
        ],
    )
    narration = narration_blocks(timeline)
    shots = rough_shot_blocks(rough, timeline)
    assert any(b.kind == "pause" for b in narration)
    assert shots[0].start_seconds == 0.0
    assert shots[0].end_seconds == 8.0  # segment_002 start 5 + middle of 6s span
    html = build_timeline_html(
        total_seconds=timeline.total_duration_seconds,
        narration=narration,
        shots=shots,
        shots_label="Video",
    )
    assert "segment_001" in html
    assert "shot_001" in html
    assert "Audio / Narration" in html


def test_final_shot_blocks_use_segment_offsets() -> None:
    timeline = _timeline()
    final = FinalCutPlanDocument(
        script_version="script-v1",
        shots=[
            FinalShot(
                shot_id="shot_f1",
                narration_start_anchor=NarrationAnchor(
                    segment_id="segment_001", offset_seconds=1.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="segment_002", offset_seconds=2.0
                ),
                asset_id="asset_x",
            )
        ],
    )
    shots = final_shot_blocks(final, timeline)
    assert len(shots) == 1
    assert shots[0].start_seconds == 1.0
    assert shots[0].end_seconds == 7.0  # segment_002 starts at 5.0 + 2.0
