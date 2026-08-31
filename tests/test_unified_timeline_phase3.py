"""Phase 3: Unified timing resolver (boundaries → absolute seconds)."""

from __future__ import annotations

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    CutPlanOptions,
)
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
    assert_timed_slots_contiguous,
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


def test_boundary_to_absolute_ignores_disabled_pause_directives() -> None:
    timeline = _timeline()
    sentence_index = _sentences()
    # Pause-Directives sind abgeschaltet — s002 start = Source 4.4 absolut.
    boundary = CutBoundary(
        cut_id="b1",
        sentence_id="seg_001__s002",
        position="start",
        alignment="sentence_boundary",
    )
    absolute = boundary_to_absolute_seconds(
        boundary, timeline, sentence_index=sentence_index, fps=25.0
    )
    assert absolute == pytest.approx(4.4)


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
    # Ohne Intra-Pause: Slot 1 endet an s002 start (4.4); Slot 2 bis s003 end (12.0).
    assert timed[0].start_seconds == pytest.approx(0.0)
    assert timed[0].end_seconds == pytest.approx(4.4)
    assert timed[1].end_seconds == pytest.approx(12.0)


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


def test_keyword_sync_applies_shot_min_max_clamp() -> None:
    """Keyword-Sync: shot_min/max gelten wie im Rhythmus-Modus."""
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
        "seg_001__s002": _sentence("seg_001__s002", start=1.2, end=2.2),
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
            CutSlot(slot_id="kw", local_asset_id="waterfall", asset_fit="strong"),
            CutSlot(slot_id="rest", local_asset_id="valley", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_SYNC,
        shot_min_sec=5.0,
        shot_max_sec=8.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
    )
    # Kurzer Keyword-Cut wird auf shot_min angehoben.
    assert timed[0].duration_seconds == pytest.approx(5.0, abs=0.05)
    # Langer Rest wird auf shot_max gekürzt (ggf. mit Folge-Slots).
    assert all(slot.duration_seconds <= 8.0 + 0.05 for slot in timed)
    assert any("shot_min" in note or "shot_max" in note for note in repairs)


def test_usable_tolerance_pulls_shared_end_boundary_not_local_cut() -> None:
    """Fix 1: shortfall ≤ Toleranz → Endgrenze nach vorne; Kette dicht."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=10.0, end=11.0),
        "seg_001__s003": _sentence("seg_001__s003", start=20.0, end=21.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    # Roh: Slot A = 10s, Slot B = 10s. A usable 9.0, Toleranz 1.0 → clamp A auf 9s.
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=120.0,
        short_asset_tolerance_sec=1.0,
        video_head_trim_sec=0.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        slot_usable_max=[9.0, None],
    )
    assert timed[0].duration_seconds == pytest.approx(9.0)
    assert timed[0].end_seconds == timed[1].start_seconds
    assert timed[1].duration_seconds == pytest.approx(11.0)
    assert_timed_slots_contiguous(timed, fps=25.0)
    total = sum(s.duration_seconds for s in timed)
    span = timed[-1].end_seconds - timed[0].start_seconds
    assert total == pytest.approx(span)
    assert any("nutzbare Dauer knapp" in note for note in repairs)


def test_frame_snap_usable_floor_avoids_post_round_overshoot(tmp_path) -> None:
    """Fix 1b: usable nicht framerein — Klemme mit floor, kein Round-Overshoot.

    usable=10.983s, span=11.5s, tol=1s @25fps → Span == 10.96s (274 Frames);
    ``_resolve_shot_media`` wirft keinen Grenzen-Klemme-Fehler; Summen-Assert grün.
    """
    import math

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _resolve_shot_media,
    )

    fps = 25.0
    usable = 10.983
    expected_span = math.floor(usable * fps) / fps  # 274/25 = 10.96
    assert expected_span == pytest.approx(10.96)

    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=11.5, end=12.5),
        "seg_001__s003": _sentence("seg_001__s003", start=22.0, end=23.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=120.0,
        short_asset_tolerance_sec=1.0,
        video_head_trim_sec=0.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=fps,
        repairs=repairs,
        slot_usable_max=[usable, None],
    )
    assert timed[0].duration_seconds == pytest.approx(expected_span)
    assert timed[0].end_seconds == timed[1].start_seconds
    assert_timed_slots_contiguous(timed, fps=fps)
    total = sum(s.duration_seconds for s in timed)
    span = timed[-1].end_seconds - timed[0].start_seconds
    assert total == pytest.approx(span)
    assert any("nutzbare Dauer knapp" in note for note in repairs)

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    # duration = head_trim(0) + usable → media_duration = usable
    entry = {
        "path": str(media),
        "duration_seconds": usable,
        "usable_in_s": 0.0,
        "media_kind": "video",
        "media_type": "video",
        "available_start_seconds": 0.0,
        "folder": "A",
        "canonical_id": "asset_a",
    }
    media_repairs: list[str] = []
    resolved = _resolve_shot_media(
        project,
        shot_id="slot_a",
        asset_id="asset_a",
        entry=entry,
        timeline_start=timed[0].start_seconds,
        timeline_end=timed[0].end_seconds,
        fps=fps,
        head_trim=0.0,
        short_tolerance=1.0,
        editorial_function="evidence",
        may_overlap_pause=False,
        repairs=media_repairs,
    )
    assert resolved.timeline_end_seconds == pytest.approx(timed[0].end_seconds)
    need = resolved.timeline_end_seconds - resolved.timeline_start_seconds
    assert need <= usable + 1e-6
    assert need == pytest.approx(expected_span)


def test_usable_floor_below_shot_min_skips_editorial_raise() -> None:
    """Fix 1b.5: floor(usable) < shot_min → kein editorial-Hochschieben (kein Pingpong)."""
    import math

    fps = 25.0
    usable = 4.5
    floor_span = math.floor(usable * fps) / fps  # 4.48
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=5.2, end=6.0),
        "seg_001__s003": _sentence("seg_001__s003", start=20.0, end=21.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=5.0,
        shot_max_sec=120.0,
        short_asset_tolerance_sec=1.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=fps,
        repairs=repairs,
        slot_usable_max=[usable, None],
    )
    assert timed[0].duration_seconds == pytest.approx(floor_span)
    assert timed[0].duration_seconds < 5.0
    assert not any("unter shot_min" in note and "slot[0]" in note for note in repairs)
    assert_timed_slots_contiguous(timed, fps=fps)


def test_intro_slots_bypass_cut_plan_shot_min() -> None:
    """Intro: Settings shot_min (5s) gilt nicht — LLM-Kurzschnitte bleiben erhalten."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=20.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="Intro_segment_001",
                start_seconds=0.0,
                end_seconds=20.0,
                audio_duration_seconds=20.0,
            )
        ],
    )
    sentences = {
        "Intro_segment_001__s001": _sentence(
            "Intro_segment_001__s001",
            start=0.0,
            end=1.0,
            segment_id="Intro_segment_001",
        ),
        "Intro_segment_001__s002": _sentence(
            "Intro_segment_001__s002",
            start=1.2,
            end=2.0,
            segment_id="Intro_segment_001",
        ),
        "Intro_segment_001__s003": _sentence(
            "Intro_segment_001__s003",
            start=2.5,
            end=3.5,
            segment_id="Intro_segment_001",
        ),
        "Intro_segment_001__s004": _sentence(
            "Intro_segment_001__s004",
            start=10.0,
            end=12.0,
            segment_id="Intro_segment_001",
        ),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="Intro_cut_000",
                sentence_id="Intro_segment_001__s001",
                position="start",
            ),
            CutBoundary(
                cut_id="Intro_cut_001",
                sentence_id="Intro_segment_001__s002",
                position="start",
            ),
            CutBoundary(
                cut_id="Intro_cut_002",
                sentence_id="Intro_segment_001__s003",
                position="start",
            ),
            CutBoundary(
                cut_id="Intro_cut_003",
                sentence_id="Intro_segment_001__s004",
                position="end",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Intro_slot_001",
                local_asset_id="a",
                asset_fit="strong",
            ),
            CutSlot(
                slot_id="Intro_slot_002",
                local_asset_id="b",
                asset_fit="strong",
            ),
            CutSlot(
                slot_id="Intro_slot_003",
                local_asset_id="c",
                asset_fit="strong",
            ),
        ],
    )
    options = CutPlanOptions(shot_min_sec=5.0, shot_max_sec=120.0)
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
    )
    # Ohne Intro-Ausnahme würde Cascade jede Lücke auf ≥5s ziehen.
    assert timed[0].duration_seconds < 5.0
    assert timed[1].duration_seconds < 5.0
    assert timed[0].duration_seconds == pytest.approx(1.2, abs=0.05)
    assert timed[1].duration_seconds == pytest.approx(1.3, abs=0.05)
    assert not any("unter shot_min" in note for note in repairs)
    assert_timed_slots_contiguous(timed, fps=25.0)


def test_intro_slots_bypass_shot_max_and_cover_vo_end() -> None:
    """Intro: shot_max darf Keyword-Onsets und VO-Ende nicht kürzen."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=42.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="Intro_segment_001",
                start_seconds=0.0,
                end_seconds=42.0,
                audio_duration_seconds=42.0,
            )
        ],
    )
    sentences = {
        "Intro_segment_001__s001": _sentence(
            "Intro_segment_001__s001",
            start=0.0,
            end=8.0,
            segment_id="Intro_segment_001",
        ),
        "Intro_segment_001__s002": _sentence(
            "Intro_segment_001__s002",
            start=8.0,
            end=20.0,
            segment_id="Intro_segment_001",
        ),
        "Intro_segment_001__s003": _sentence(
            "Intro_segment_001__s003",
            start=20.0,
            end=42.0,
            segment_id="Intro_segment_001",
        ),
    }
    # Keyword-Onset bei 10.0s (offset 2.0 in s002); Closing hält Rest-VO (>8s).
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="Intro_cut_000",
                sentence_id="Intro_segment_001__s001",
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="Intro_cut_001",
                sentence_id="Intro_segment_001__s002",
                position="middle",
                offset_seconds=2.0,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="Intro_cut_002",
                sentence_id="Intro_segment_001__s003",
                position="end",
                offset_seconds=None,
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Intro_slot_001",
                local_asset_id="a",
                asset_fit="strong",
            ),
            CutSlot(
                slot_id="Intro_slot_002",
                local_asset_id="b",
                asset_fit="strong",
            ),
        ],
    )
    options = CutPlanOptions(shot_min_sec=5.0, shot_max_sec=8.0)
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        segment_to_chapter={"Intro_segment_001": "Intro"},
    )
    # Keyword-Onset bleibt bei 10.0s — nicht durch shot_max nach vorne gezogen.
    assert timed[1].start_seconds == pytest.approx(10.0, abs=0.05)
    # Letztes Bild deckt VO-Ende (42s) ab — kein schwarzes Bild bei laufendem Audio.
    assert timed[-1].end_seconds == pytest.approx(42.0, abs=0.05)
    assert timed[-1].duration_seconds > 8.0
    assert not any("über shot_max" in note for note in repairs)
    assert_timed_slots_contiguous(timed, fps=25.0)


def test_usable_tolerance_allows_neighbor_past_shot_max() -> None:
    """Shortfall ≤ Toleranz: Folge-Slot darf shot_max überschreiten (kein Placeholder)."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    # Roh: A=8s, B=8s → A usable 7.0, tol 1.0 → A=7s, B=9s > shot_max 8.
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=8.0, end=9.0),
        "seg_001__s003": _sentence("seg_001__s003", start=16.0, end=17.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=8.0,
        short_asset_tolerance_sec=1.0,
        video_head_trim_sec=0.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        slot_usable_max=[7.0, None],
    )
    assert timed[0].duration_seconds == pytest.approx(7.0)
    assert timed[1].duration_seconds == pytest.approx(9.0)
    assert timed[0].end_seconds == timed[1].start_seconds
    assert any("shot_max-Überschreitung erlaubt" in note for note in repairs)
    assert not any(
        "über shot_max" in note and "Endgrenze nach vorne verschoben" in note
        for note in repairs
        if "slot[1]" in note
    )


def test_usable_tolerance_last_slot_extends_previous() -> None:
    """Letzter Slot knapp → Vorgänger verlängern (Timeline-Ende bleibt)."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=8.0, end=9.0),
        "seg_001__s003": _sentence("seg_001__s003", start=16.0, end=17.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=8.0,
        short_asset_tolerance_sec=1.0,
        video_head_trim_sec=0.0,
    )
    repairs: list[str] = []
    end_before = 16.0
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        slot_usable_max=[None, 7.0],
    )
    assert timed[1].duration_seconds == pytest.approx(7.0)
    assert timed[0].duration_seconds == pytest.approx(9.0)
    assert timed[-1].end_seconds == pytest.approx(end_before)
    assert any("Vorgänger-Slot länger" in note for note in repairs)


def test_over_tolerance_extends_neighbor_when_it_has_spare() -> None:
    """2s zu kurz, Folgeslot hat Reserve → Nachbar länger, kein Gap auf Slot A."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=10.0, end=11.0),
        "seg_001__s003": _sentence("seg_001__s003", start=20.0, end=21.0),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=120.0,
        short_asset_tolerance_sec=1.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        slot_usable_max=[8.0, None],  # shortfall 2.0 > tol 1.0, B unbegrenzt
    )
    assert timed[0].duration_seconds == pytest.approx(8.0, abs=0.04)
    assert timed[1].duration_seconds == pytest.approx(12.0, abs=0.04)
    assert timed[0].end_seconds == timed[1].start_seconds
    assert any("Folge-Slot länger" in note for note in repairs)


def test_over_tolerance_without_neighbor_spare_leaves_gap() -> None:
    """Über Toleranz und Nachbarn ohne Reserve → Slot bleibt lang (roter Rest)."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.4, 24.4],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[8.0, 6.0, 8.0],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(3)]
    # Mitte 8.4s, usable 6.0, fehlend 2.4s > 1s; Nachbarn schon am Limit.
    assert spans[1] == pytest.approx(8.4, abs=0.04)
    assert spans[0] == pytest.approx(8.0, abs=0.04)
    assert spans[2] == pytest.approx(8.0, abs=0.04)


def test_savica_style_over_tolerance_splits_to_both_neighbors() -> None:
    """Clip 5.7s / Sprecher 8.1s: 2.4s auf Nachbarn mit Reserve, hälftig."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.12, 24.12],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[12.0, 5.7, 12.0],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(3)]
    assert spans[1] == pytest.approx(5.7, abs=0.08)
    assert spans[0] == pytest.approx(9.2, abs=0.12)
    assert spans[2] == pytest.approx(9.2, abs=0.12)
    assert sum(spans) == pytest.approx(24.12, abs=0.04)
    assert any("Vorgänger-Slot länger" in note for note in repairs)
    assert any("Folge-Slot länger" in note for note in repairs)


def test_allocate_mini_gap_splits_half_when_both_neighbors_have_spare() -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        allocate_mini_gap_to_neighbors,
    )

    to_prev, to_next = allocate_mini_gap_to_neighbors(
        1.0, spare_prev=5.0, spare_next=5.0, fps=25.0
    )
    assert to_prev + to_next == pytest.approx(1.0)
    assert to_prev == pytest.approx(0.48)  # 12 frames
    assert to_next == pytest.approx(0.52)  # 13 frames (extra frame → next)


def test_allocate_mini_gap_uses_direct_neighbors_before_extended() -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        allocate_mini_gap_across_neighbor_rings,
    )

    take_prev, take_next = allocate_mini_gap_across_neighbor_rings(
        0.4,
        spare_prev_by_ring=[5.0, 5.0],
        spare_next_by_ring=[5.0, 5.0],
        fps=25.0,
    )
    assert take_prev[0] + take_next[0] == pytest.approx(0.4)
    assert take_prev[1] == 0.0
    assert take_next[1] == 0.0


def test_allocate_mini_gap_falls_through_to_extended_neighbors() -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        allocate_mini_gap_across_neighbor_rings,
    )

    take_prev, take_next = allocate_mini_gap_across_neighbor_rings(
        0.4,
        spare_prev_by_ring=[0.0, 5.0],
        spare_next_by_ring=[0.0, 5.0],
        fps=25.0,
    )
    assert take_prev[0] == 0.0
    assert take_next[0] == 0.0
    assert take_prev[1] + take_next[1] == pytest.approx(0.4)
    assert take_prev[1] == pytest.approx(0.2)
    assert take_next[1] == pytest.approx(0.2)


def test_mini_gap_pushes_through_tight_direct_neighbors() -> None:
    """Direkte Nachbarn am Limit → Mini-Zeit auf Nachbar-des-Nachbarn."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.0, 24.0, 32.0, 40.0],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[None, 8.0, 7.6, 8.0, None],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(5)]
    assert spans[2] == pytest.approx(7.6, abs=0.04)
    assert spans[1] == pytest.approx(8.0, abs=0.04)
    assert spans[3] == pytest.approx(8.0, abs=0.04)
    assert spans[0] > 8.0
    assert spans[4] > 8.0
    assert sum(spans) == pytest.approx(40.0, abs=0.04)
    assert times[-1] == pytest.approx(40.0, abs=0.04)
    assert any("erweiterte Nachbarn" in note for note in repairs)


def test_over_tolerance_pushes_through_tight_direct_neighbors() -> None:
    """Kropa-Fall: 1.8s zu kurz, direkte Nachbarn voll → Clip zwei weiter."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    # Slots: 8, 8, 8.8, 8, 8 — Mitte braucht 8.8s, hat 7.0s (fehlend 1.8s > 1s).
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.0, 24.8, 32.8, 40.8],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[8.0, 8.0, 7.0, 8.0, 12.0],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(5)]
    assert spans[2] == pytest.approx(7.0, abs=0.08)
    assert spans[1] == pytest.approx(8.0, abs=0.04)
    assert spans[3] == pytest.approx(8.0, abs=0.04)
    assert spans[4] > 8.0
    assert sum(spans) == pytest.approx(40.8, abs=0.04)
    assert times[-1] == pytest.approx(40.8, abs=0.04)
    assert any("erweiterte Nachbarn" in note for note in repairs)


def test_kropa_style_consecutive_shorts_use_extended_next() -> None:
    """Zwei knappe Slots hintereinander: Rest geht durch den zweiten hindurch."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 10.0, 20.0, 30.0, 40.0],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[10.0, 9.7, 9.8, None],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(4)]
    assert spans[1] == pytest.approx(9.7, abs=0.04)
    assert spans[2] == pytest.approx(9.8, abs=0.04)
    assert spans[0] == pytest.approx(10.0, abs=0.04)
    assert spans[3] == pytest.approx(10.5, abs=0.08)
    assert times[-1] == pytest.approx(40.0)
    assert any("erweiterte Nachbarn" in note for note in repairs)
    assert not any("nicht stabil" in note for note in repairs)


def test_extended_prev_does_not_move_timeline_end() -> None:
    """Letzter Slot knapp, direkter Vorgänger voll → weiter links, Ende bleibt."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 10.0, 20.0, 30.0],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[None, 10.0, 9.6],
        short_tolerance=1.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(3)]
    assert spans[2] == pytest.approx(9.6, abs=0.04)
    assert spans[1] == pytest.approx(10.0, abs=0.04)
    assert spans[0] == pytest.approx(10.4, abs=0.08)
    assert times[-1] == pytest.approx(30.0)
    assert any("erweiterte Nachbarn" in note for note in repairs)


def test_leftover_mini_gap_becomes_placeholder_repair_not_error() -> None:
    """Nachbarn ohne Reserve: Mini-Rest bleibt, Timing bricht nicht ab."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.5, 24.5],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[8.0, 8.0, 8.0],
        short_tolerance=2.0,
        fps=25.0,
    )
    spans = [times[i + 1] - times[i] for i in range(3)]
    assert spans[1] == pytest.approx(8.5, abs=0.04)
    assert times[-1] == pytest.approx(24.5, abs=0.04)
    assert any("roter Placeholder" in note for note in repairs)
    assert not any("nicht stabil" in note for note in repairs)


def test_subframe_leftover_is_ignored() -> None:
    """Sub-Frame (~0s Anzeige) ist kein Timing-Abbruch und kein Repair."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        _clamp_boundary_times,
    )

    repairs: list[str] = []
    times = _clamp_boundary_times(
        [0.0, 8.0, 16.03, 24.03],
        editorial_min=0.4,
        editorial_max=120.0,
        repairs=repairs,
        slot_usable_max=[8.0, 8.0, 8.0],
        short_tolerance=2.0,
        fps=25.0,
    )
    assert times[-1] == pytest.approx(24.03, abs=0.04)
    assert not any("roter Placeholder" in note for note in repairs)
    assert not any("nicht stabil" in note for note in repairs)


def test_mini_gap_splits_between_both_neighbors() -> None:
    """0,1s Mini-Lücke: Vorgänger und Folgeslot teilen sich die fehlende Zeit."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=40.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=40.0,
                audio_duration_seconds=40.0,
            )
        ],
    )
    sentences = {
        "seg_001__s001": _sentence("seg_001__s001", start=0.0, end=1.0),
        "seg_001__s002": _sentence("seg_001__s002", start=10.0, end=11.0),
        "seg_001__s003": _sentence("seg_001__s003", start=21.3, end=22.3),
        "seg_001__s004": _sentence("seg_001__s004", start=31.3, end=32.3),
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg_001__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg_001__s002", position="start"),
            CutBoundary(cut_id="b2", sentence_id="seg_001__s003", position="start"),
            CutBoundary(cut_id="b3", sentence_id="seg_001__s004", position="start"),
        ],
        slots=[
            CutSlot(slot_id="slot_a", local_asset_id="asset_a", asset_fit="strong"),
            CutSlot(slot_id="slot_b", local_asset_id="asset_b", asset_fit="strong"),
            CutSlot(slot_id="slot_c", local_asset_id="asset_c", asset_fit="strong"),
        ],
    )
    options = CutPlanOptions(
        shot_min_sec=0.4,
        shot_max_sec=120.0,
        short_asset_tolerance_sec=1.0,
        video_head_trim_sec=0.0,
    )
    repairs: list[str] = []
    timed = resolve_timed_slots(
        plan,
        timeline,
        sentence_index=sentences,
        options=options,
        fps=25.0,
        repairs=repairs,
        slot_usable_max=[None, 11.2, None],
    )
    assert timed[1].duration_seconds == pytest.approx(11.2, abs=0.05)
    assert timed[0].duration_seconds == pytest.approx(10.04, abs=0.08)
    assert timed[2].duration_seconds == pytest.approx(10.06, abs=0.08)
    assert timed[0].end_seconds == timed[1].start_seconds
    assert timed[1].end_seconds == timed[2].start_seconds
    assert_timed_slots_contiguous(timed, fps=25.0)
    total = sum(s.duration_seconds for s in timed)
    span = timed[-1].end_seconds - timed[0].start_seconds
    assert total == pytest.approx(span)
    assert timed[0].duration_seconds > 10.0
    assert timed[2].duration_seconds > 10.0
    assert any("Vorgänger-Slot länger" in note for note in repairs)
    assert any("Folge-Slot länger" in note for note in repairs)


def test_resolve_shot_media_never_shortens_timeline_end(tmp_path) -> None:
    """Fix 1.2: Media-Auflösung ändert timeline_end nicht (auch innerhalb Toleranz)."""
    from otio_app.models import Project, ProjectMode
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        TimelineResolveError,
        _resolve_shot_media,
    )

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    entry = {
        "path": str(media),
        "duration_seconds": 5.0,
        "usable_in_s": 0.0,
        "media_kind": "video",
        "media_type": "video",
        "available_start_seconds": 0.0,
        "folder": "A",
        "canonical_id": "asset_a",
    }
    repairs: list[str] = []
    # need 5.5, usable 5.0, tol 1.0 → früher lokal gekürzt; jetzt Fehler.
    with pytest.raises(TimelineResolveError, match="Grenzen-Klemme|zu kurz|knapp über"):
        _resolve_shot_media(
            project,
            shot_id="slot_x",
            asset_id="asset_a",
            entry=entry,
            timeline_start=0.0,
            timeline_end=5.5,
            fps=25.0,
            head_trim=0.0,
            short_tolerance=1.0,
            editorial_function="evidence",
            may_overlap_pause=False,
            repairs=repairs,
        )
    assert not any("Shot gekürzt" in note for note in repairs)


def test_resolve_shot_media_probes_mp4_when_inventory_duration_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression Győr_slot_012: MP4 mit Dauer 0 darf nicht in Still-Hold."""
    from otio_app.models import Project, ProjectMode
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _resolve_shot_media,
    )

    media = tmp_path / "Győr_Asset00014_3840x2160.mp4"
    media.write_bytes(b"x")
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Győr"],
        selected_asset_subdirs=["Győr"],
    )
    entry = {
        "path": str(media),
        "duration_seconds": 0,
        "usable_in_s": 0.0,
        "media_kind": "image",
        "media_type": "image",
        "available_start_seconds": 0.0,
        "folder": "Győr",
        "canonical_id": "asset_gy_r_asset00014",
    }
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver.probe_duration_seconds",
        lambda path: 12.0,
    )

    def _forbid_still_hold(*_args, **_kwargs):
        raise AssertionError("MP4 darf nicht über Still-Hold / -loop laufen")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver.ensure_still_hold_video",
        _forbid_still_hold,
    )
    repairs: list[str] = []
    shot = _resolve_shot_media(
        project,
        shot_id="Győr_slot_012",
        asset_id="asset_gy_r_asset00014",
        entry=entry,
        timeline_start=78.760,
        timeline_end=83.280,
        fps=25.0,
        head_trim=0.0,
        short_tolerance=1.0,
        editorial_function="evidence",
        may_overlap_pause=False,
        repairs=repairs,
    )
    assert shot.hold_mode == ""
    assert shot.resolved_media_kind == "video"
    assert shot.resolved_media_duration_seconds == pytest.approx(12.0)
    assert shot.timeline_end_seconds - shot.timeline_start_seconds == pytest.approx(4.52)
    assert any("ffprobe 12.00s" in note for note in repairs)


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
