"""Phase 3: Unified timing resolver (boundaries → absolute seconds)."""

from __future__ import annotations

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import CutPlanOptions
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    PauseDirective,
    SegmentTiming,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    EDGE_MARGIN_SECONDS,
    POSITION_FRACTION,
    boundary_source_offset_seconds,
    boundary_to_absolute_seconds,
    boundary_to_narration_anchor,
    resolve_timed_slots,
    unified_plan_to_final_shadow,
)


def _sentence(
    sentence_id: str,
    *,
    start: float,
    end: float,
    segment_id: str = "seg_001",
) -> SentenceTiming:
    return SentenceTiming(
        sentence_id=sentence_id,
        segment_id=segment_id,
        text=sentence_id,
        start_seconds=start,
        end_seconds=end,
        duration_seconds=end - start,
    )


def _sentences() -> dict[str, SentenceTiming]:
    return {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=4.0),
        "seg_001__s002": _sentence("seg_001__s002", start=4.4, end=8.0),
        "seg_001__s003": _sentence("seg_001__s003", start=8.4, end=12.0),
    }


def _timeline() -> NarrationTimelineDocument:
    return build_narration_timeline(
        script_version="script-v1",
        segment_timings=[
            SegmentTiming(
                segment_id="seg_001",
                script_version="script-v1",
                audio_path="a.mp3",
                duration_seconds=12.0,
            )
        ],
        pause_directives=[
            PauseDirective(
                after_segment_id="seg_001",
                after_sentence_id="seg_001__s001",
                pause_function="breath",
                duration_class="medium",
            )
        ],
        sentence_index=_sentences(),
    )


def test_position_fraction_mapping_and_edge_margin() -> None:
    sentence = _sentences()["seg_001__s001"]  # 4s span
    mid = CutBoundary(
        cut_id="c1",
        sentence_id=sentence.sentence_id,
        position="middle",
        alignment="mid_sentence",
    )
    offset = boundary_source_offset_seconds(mid, sentence)
    assert offset == pytest.approx(2.0)

    # Kurzer Satz: 25% liegt innerhalb der Rand-Marge → hoch auf 0.4s.
    short = _sentence("seg_001__s001", start=0.0, end=1.2)
    early = CutBoundary(
        cut_id="c2",
        sentence_id=short.sentence_id,
        position="early",
        alignment="mid_sentence",
    )
    assert boundary_source_offset_seconds(early, short) == pytest.approx(
        EDGE_MARGIN_SECONDS
    )

    # offset_seconds gewinnt
    override = CutBoundary(
        cut_id="c3",
        sentence_id=sentence.sentence_id,
        position="end",
        offset_seconds=1.25,
        alignment="mid_sentence",
    )
    assert boundary_source_offset_seconds(override, sentence) == pytest.approx(1.25)
    assert POSITION_FRACTION["late"] == 0.75


def test_boundary_to_absolute_accounts_for_intra_pause() -> None:
    timeline = _timeline()
    sentence_index = _sentences()
    # s002 startet bei Source 4.4; nach Intra-Pause (mid silence 4.2, +2.5s)
    # absolute = 4.4 + 2.5 = 6.9
    boundary = CutBoundary(
        cut_id="b1",
        sentence_id="seg_001__s002",
        position="start",
        alignment="sentence_boundary",
    )
    absolute = boundary_to_absolute_seconds(
        boundary, timeline, sentence_index=sentence_index, fps=25.0
    )
    # 6.9s → Frame-Rundung bei 25fps: 6.88s
    assert absolute == pytest.approx(6.88)


def test_resolve_timed_slots_chain_and_gap_fields() -> None:
    timeline = _timeline()
    sentence_index = _sentences()
    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="seg_001__s001",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="seg_001__s002",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b2",
                sentence_id="seg_001__s003",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="slot_001",
                local_asset_id="loc_a",
                asset_fit="strong",
                narrative_function="chapter_open",
            ),
            CutSlot(
                slot_id="slot_002",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_002",
                needed_visual="detail",
                narrative_function="evidence",
            ),
        ],
    )
    options = CutPlanOptions(shot_min_sec=0.4, shot_max_sec=120.0)
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentence_index,
        options=options,
        fps=25.0,
        repairs=repairs,
    )
    assert len(timed) == 2
    assert timed[0].end_seconds == timed[1].start_seconds
    assert timed[0].asset_fit == "strong"
    assert timed[0].is_open_gap is False
    assert timed[1].is_open_gap is True
    assert timed[1].coverage_gap_id == "gap_002"
    assert timed[1].duration_seconds > 0
    # Slot 1 endet an s002 start (= 6.9); Slot 2 bis s003 end (+intra) = 12+2.5=14.5
    assert timed[0].start_seconds == pytest.approx(0.0)
    assert timed[0].end_seconds == pytest.approx(6.88)
    assert timed[1].end_seconds == pytest.approx(14.48)


def test_clamp_shortens_overlong_slot_by_nudging_shared_boundary() -> None:
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=30.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=30.0,
                audio_duration_seconds=30.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=20.0, end=21.0),
        "seg_001__s003": _sentence("seg_001__s003", start=29.0, end=30.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="seg_001__s001",
                position="start",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="seg_001__s002",
                position="start",
            ),
            CutBoundary(
                cut_id="b2",
                sentence_id="seg_001__s003",
                position="end",
            ),
        ],
        slots=[
            CutSlot(slot_id="a", local_asset_id="x", asset_fit="strong"),
            CutSlot(slot_id="b", local_asset_id="y", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(shot_min_sec=2.0, shot_max_sec=8.0)
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
    )
    assert timed[0].duration_seconds == pytest.approx(8.0)
    # Kette bleibt dicht
    assert timed[0].end_seconds == timed[1].start_seconds
    assert any("shot_max" in note for note in repairs)


def test_final_shadow_uses_real_sentence_offsets() -> None:
    sentence_index = _sentences()
    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="seg_001__s001",
                position="middle",
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="seg_001__s002",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="slot_001",
                local_asset_id="loc_a",
                asset_fit="acceptable",
            )
        ],
        voiceover_preroll_sec=1.5,
    )
    shadow = unified_plan_to_final_shadow(plan, sentence_index=sentence_index)
    assert len(shadow.shots) == 1
    # middle von 4s-Satz = 2.0s satzrelativ (nicht Fraction 0.5)
    assert shadow.shots[0].narration_start_anchor.offset_seconds == pytest.approx(2.0)
    assert shadow.shots[0].narration_end_anchor.offset_seconds == pytest.approx(3.6)
    assert shadow.voiceover_preroll_sec == pytest.approx(1.5)

    anchor = boundary_to_narration_anchor(
        plan.boundaries[0], sentence_index=sentence_index
    )
    assert anchor.sentence_id == "seg_001__s001"
    assert anchor.offset_seconds == pytest.approx(2.0)
