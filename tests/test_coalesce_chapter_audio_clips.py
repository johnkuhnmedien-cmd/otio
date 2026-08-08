"""Eine Kapitel-WAV → ein Narrationsclip (Intro + Körper)."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import (
    IntraPauseMarker,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    SegmentTiming,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    _build_resolved_audio_segments,
    coalesce_same_media_audio_segments,
)


def test_coalesce_abutting_intro_slices_into_one_clip() -> None:
    intro = "/proj/_otio_enhanced/FR/audio/chapters/Intro.wav"
    pieces = [
        ResolvedAudioSegment(
            segment_id="Intro_segment_001",
            audio_path=intro,
            timeline_start_seconds=4.0,
            timeline_end_seconds=8.0,
            pause_after_seconds=0.0,
            source_start_seconds=0.0,
            source_end_seconds=4.0,
            split_label="after:Intro_segment_001__s001",
            chapter_id="Intro",
        ),
        ResolvedAudioSegment(
            segment_id="Intro_segment_001",
            audio_path=intro,
            timeline_start_seconds=8.0,
            timeline_end_seconds=12.5,
            pause_after_seconds=0.0,
            source_start_seconds=4.0,
            source_end_seconds=8.5,
            split_label="tail",
            chapter_id="Intro",
        ),
        ResolvedAudioSegment(
            segment_id="Achill_segment_001",
            audio_path="/proj/_otio_enhanced/FR/audio/chapters/Achill_Island.wav",
            timeline_start_seconds=20.0,
            timeline_end_seconds=30.0,
            pause_after_seconds=0.0,
            source_start_seconds=0.0,
            source_end_seconds=10.0,
            chapter_id="Achill Island",
        ),
    ]
    merged = coalesce_same_media_audio_segments(pieces)
    assert len(merged) == 2
    intro_clip = merged[0]
    assert intro_clip.segment_id == "Intro_segment_001"
    assert intro_clip.timeline_start_seconds == 4.0
    assert intro_clip.timeline_end_seconds == 12.5
    assert intro_clip.source_start_seconds == 0.0
    assert intro_clip.source_end_seconds == 8.5
    assert intro_clip.split_label == ""
    assert intro_clip.pause_after_seconds == 0.0
    assert merged[1].segment_id == "Achill_segment_001"


def test_coalesce_keeps_timeline_pause_gap_between_same_file() -> None:
    path = "/proj/audio/chapters/Dublin.wav"
    pieces = [
        ResolvedAudioSegment(
            segment_id="Dublin_segment_001",
            audio_path=path,
            timeline_start_seconds=0.0,
            timeline_end_seconds=5.0,
            pause_after_seconds=1.5,
            source_start_seconds=0.0,
            source_end_seconds=5.0,
        ),
        ResolvedAudioSegment(
            segment_id="Dublin_segment_001",
            audio_path=path,
            timeline_start_seconds=6.5,
            timeline_end_seconds=9.0,
            pause_after_seconds=0.0,
            source_start_seconds=5.0,
            source_end_seconds=7.5,
            split_label="tail",
        ),
    ]
    merged = coalesce_same_media_audio_segments(pieces)
    assert len(merged) == 2
    assert merged[0].pause_after_seconds == 1.5
    assert merged[1].split_label == "tail"


def test_build_resolved_audio_coalesces_multi_segment_chapter_wav() -> None:
    chapter = "/tmp/proj/audio/chapters/Dublin.wav"
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=9.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="Dublin_segment_001",
                start_seconds=0.0,
                end_seconds=3.0,
                pause_after_seconds=0.0,
                audio_duration_seconds=3.0,
                intra_pauses=[],
            ),
            NarrationTimelineEntry(
                segment_id="Dublin_segment_002",
                start_seconds=3.0,
                end_seconds=6.0,
                pause_after_seconds=0.0,
                audio_duration_seconds=3.0,
                intra_pauses=[],
            ),
            NarrationTimelineEntry(
                segment_id="Dublin_segment_003",
                start_seconds=6.0,
                end_seconds=9.0,
                pause_after_seconds=0.0,
                audio_duration_seconds=3.0,
                intra_pauses=[],
            ),
        ],
    )
    timing_map = {
        "Dublin_segment_001": SegmentTiming(
            segment_id="Dublin_segment_001",
            script_version="v1",
            audio_path=chapter,
            duration_seconds=3.0,
            audio_status="valid",
            source_start_seconds=0.0,
            source_end_seconds=3.0,
        ),
        "Dublin_segment_002": SegmentTiming(
            segment_id="Dublin_segment_002",
            script_version="v1",
            audio_path=chapter,
            duration_seconds=3.0,
            audio_status="valid",
            source_start_seconds=3.0,
            source_end_seconds=6.0,
        ),
        "Dublin_segment_003": SegmentTiming(
            segment_id="Dublin_segment_003",
            script_version="v1",
            audio_path=chapter,
            duration_seconds=3.0,
            audio_status="valid",
            source_start_seconds=6.0,
            source_end_seconds=9.0,
        ),
    }
    resolved = _build_resolved_audio_segments(
        timeline=timeline, timing_map=timing_map, fps=25.0
    )
    assert len(resolved) == 1
    clip = resolved[0]
    assert clip.audio_path == chapter
    assert clip.timeline_start_seconds == 0.0
    assert clip.timeline_end_seconds == 9.0
    assert clip.source_start_seconds == 0.0
    assert clip.source_end_seconds == 9.0
    assert clip.segment_id == "Dublin_segment_001"


def test_build_resolved_audio_keeps_intra_pause_split_when_gap_inserted() -> None:
    path = "/tmp/proj/audio/chapters/Intro.wav"
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=11.5,
        entries=[
            NarrationTimelineEntry(
                segment_id="Intro_segment_001",
                start_seconds=0.0,
                end_seconds=11.5,
                pause_after_seconds=0.0,
                audio_duration_seconds=10.0,
                intra_pauses=[
                    IntraPauseMarker(
                        after_sentence_id="Intro_segment_001__s001",
                        source_split_seconds=5.0,
                        pause_seconds=1.5,
                    )
                ],
            )
        ],
    )
    timing_map = {
        "Intro_segment_001": SegmentTiming(
            segment_id="Intro_segment_001",
            script_version="v1",
            audio_path=path,
            duration_seconds=10.0,
            audio_status="valid",
            source_start_seconds=0.0,
            source_end_seconds=10.0,
        )
    }
    resolved = _build_resolved_audio_segments(
        timeline=timeline, timing_map=timing_map, fps=25.0
    )
    # Eingefügte Stille bleibt als zwei Clips (A/V); Keyword Flow setzt keine.
    assert len(resolved) == 2
    assert resolved[0].pause_after_seconds == 1.5
    assert resolved[1].timeline_start_seconds == 6.5
