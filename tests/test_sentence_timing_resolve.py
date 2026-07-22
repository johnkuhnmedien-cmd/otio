"""Satz-Anker, Intra-Pausen (Silence-Mid) und Cut-Rhythmus-Quoten."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
)
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    PauseDirective,
    ResolvedShot,
    SegmentTiming,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
    mid_silence_split_seconds,
    source_seconds_to_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    DEFAULT_CUT_RHYTHM_TARGETS,
    build_final_cut_prompt,
    build_rough_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    _anchor_to_seconds,
    _build_resolved_audio_segments,
)


def _sentences() -> dict[str, SentenceTiming]:
    return {
        "seg_001__s001": SentenceTiming(
            sentence_id="seg_001__s001",
            segment_id="seg_001",
            text="One.",
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
        ),
        "seg_001__s002": SentenceTiming(
            sentence_id="seg_001__s002",
            segment_id="seg_001",
            text="Two.",
            start_seconds=1.4,
            end_seconds=2.5,
            duration_seconds=1.1,
        ),
    }


def test_mid_silence_split_between_sentences() -> None:
    sentences = _sentences()
    split = mid_silence_split_seconds(
        sentence=sentences["seg_001__s001"],
        next_sentence=sentences["seg_001__s002"],
        audio_duration_seconds=3.0,
    )
    assert split == 1.2


def test_intra_sentence_pause_expands_timeline_without_stretching_audio(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.pause_config."
        "ENHANCED_VOICEOVER_PAUSES_ENABLED",
        True,
    )
    timings = [
        SegmentTiming(
            segment_id="seg_001",
            script_version="script-v1",
            audio_path="a.mp3",
            duration_seconds=3.0,
        ),
        SegmentTiming(
            segment_id="seg_002",
            script_version="script-v1",
            audio_path="b.mp3",
            duration_seconds=2.0,
        ),
    ]
    directives = [
        PauseDirective(
            after_segment_id="seg_001",
            after_sentence_id="seg_001__s001",
            pause_function="breath",
            duration_class="medium",
        )
    ]
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=directives,
        sentence_index=_sentences(),
    )
    entry = timeline.entries[0]
    assert entry.audio_duration_seconds == 3.0
    assert len(entry.intra_pauses) == 1
    assert entry.intra_pauses[0].source_split_seconds == 1.2
    assert entry.intra_pauses[0].pause_seconds == 0.80
    # Wanduhr = Audio + Intra-Pause; Segment 2 startet später.
    assert entry.end_seconds == 3.8
    assert timeline.entries[1].start_seconds == 3.8

    # Source-Zeit nach dem Split liegt hinter der Gap.
    assert source_seconds_to_timeline(entry, 1.0) == 1.0
    assert source_seconds_to_timeline(entry, 1.4) == 2.2


def test_resolved_audio_splits_at_silence_midpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.pause_config."
        "ENHANCED_VOICEOVER_PAUSES_ENABLED",
        True,
    )
    timings = [
        SegmentTiming(
            segment_id="seg_001",
            script_version="script-v1",
            audio_path="/tmp/a.mp3",
            duration_seconds=3.0,
        )
    ]
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=[
            PauseDirective(
                after_segment_id="seg_001",
                after_sentence_id="seg_001__s001",
                pause_function="breath",
                duration_class="short",
            )
        ],
        sentence_index=_sentences(),
    )
    pieces = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map={item.segment_id: item for item in timings},
    )
    assert len(pieces) == 2
    assert pieces[0].source_start_seconds == 0.0
    assert pieces[0].source_end_seconds == 1.2
    assert pieces[0].pause_after_seconds == 0.35
    assert pieces[1].source_start_seconds == 1.2
    assert pieces[1].source_end_seconds == 3.0
    # Kein Time-Stretch: Summe der Source-Längen = Audio-Dauer.
    source_total = sum(
        (p.source_end_seconds or 0) - p.source_start_seconds for p in pieces
    )
    assert source_total == 3.0


def test_sentence_anchor_offset_is_relative_to_sentence() -> None:
    timings = [
        SegmentTiming(
            segment_id="seg_001",
            script_version="script-v1",
            audio_path="a.mp3",
            duration_seconds=3.0,
        )
    ]
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=[],
        sentence_index=_sentences(),
    )
    absolute = _anchor_to_seconds(
        timeline,
        NarrationAnchor(
            segment_id="seg_001",
            sentence_id="seg_001__s002",
            offset_seconds=0.5,
        ),
        sentence_index=_sentences(),
    )
    # Satz startet bei 1.4 → 1.9 absolut (keine Intra-Pause).
    assert absolute == 1.9


def test_cut_rhythm_notes_when_distribution_skewed() -> None:
    shots = [
        FinalShot(
            shot_id=f"s{i}",
            narration_start_anchor=NarrationAnchor(segment_id="a", offset_seconds=0),
            narration_end_anchor=NarrationAnchor(segment_id="a", offset_seconds=1),
            asset_id="x",
            start_cut_alignment="sentence_boundary",
        )
        for i in range(6)
    ]
    final = FinalCutPlanDocument(script_version="script-v1", shots=shots)
    resolved = [
        ResolvedShot(
            shot_id=f"s{i}",
            asset_id="x",
            timeline_start_seconds=float(i * 5),
            timeline_end_seconds=float(i * 5 + 5),
            source_start_seconds=0.0,
            source_end_seconds=5.0,
        )
        for i in range(6)
    ]
    notes = assess_cut_rhythm(final, resolved)
    assert any("mid_sentence" in note for note in notes)
    assert any("außerhalb" in note for note in notes)


def test_prompts_include_sentence_blocks_when_provided() -> None:
    assert "65%" in DEFAULT_CUT_RHYTHM_TARGETS
    rough = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="[]",
        local_assets_json="[]",
        style_profile_text="",
        dramaturgy_text="",
        sentence_timings_json='[{"sentence_id":"a__s001"}]',
        cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
    )
    assert "SENTENCE TIMINGS" in rough
    assert "CUT RHYTHM TARGETS" in rough
    assert "after_sentence_id" in rough
    assert "start_cut_alignment" in rough

    final = build_final_cut_prompt(
        locked_script_json="{}",
        narration_timeline_json="{}",
        pause_directives_json="[]",
        rough_cut_json="{}",
        local_assets_json="[]",
        accepted_supplements_json="{}",
        style_profile_text="",
        sentence_alignment_json='[{"sentence_id":"a__s001"}]',
        cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
    )
    assert "SENTENCE TIMINGS" in final
    assert "RELATIVE TO THAT SENTENCE" in final
    assert "FINAL VALIDATION" in final
