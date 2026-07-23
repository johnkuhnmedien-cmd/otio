"""Rough-Cut muss Kapitel ohne visuelle Löcher abdecken (Soft-Anker-Prüfung)."""

from __future__ import annotations

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    _validate_rough_continuous_coverage,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EditorialAnchor,
    RoughCutPlanDocument,
    RoughShot,
    SegmentTiming,
    SegmentTimingsDocument,
)


def _shot(shot_id: str, start_pos: str, end_pos: str, segment_id: str = "A_segment_001") -> RoughShot:
    return RoughShot(
        shot_id=shot_id,
        start_anchor=EditorialAnchor(
            type="segment", segment_id=segment_id, position=start_pos
        ),
        end_anchor=EditorialAnchor(
            type="segment", segment_id=segment_id, position=end_pos
        ),
    )


def test_rough_coverage_accepts_full_carpet() -> None:
    rough = RoughCutPlanDocument(
        script_version="v1",
        shots=[
            _shot("s1", "start", "middle"),
            _shot("s2", "middle", "end"),
        ],
    )
    timings = SegmentTimingsDocument(
        script_version="v1",
        segments=[
            SegmentTiming(
                segment_id="A_segment_001",
                script_version="v1",
                audio_path="/tmp/a.wav",
                duration_seconds=10.0,
            )
        ],
    )
    _validate_rough_continuous_coverage(
        rough,
        timings=timings,
        ordered_segment_ids=["A_segment_001"],
        folder_name="A",
    )


def test_rough_coverage_rejects_mid_chapter_hole() -> None:
    rough = RoughCutPlanDocument(
        script_version="v1",
        shots=[
            _shot("s1", "start", "early"),
            _shot("s2", "late", "end"),
        ],
    )
    timings = SegmentTimingsDocument(
        script_version="v1",
        segments=[
            SegmentTiming(
                segment_id="A_segment_001",
                script_version="v1",
                audio_path="/tmp/a.wav",
                duration_seconds=10.0,
            )
        ],
    )
    with pytest.raises(CutPlanError, match="visuelle Lücke"):
        _validate_rough_continuous_coverage(
            rough,
            timings=timings,
            ordered_segment_ids=["A_segment_001"],
            folder_name="A",
        )
