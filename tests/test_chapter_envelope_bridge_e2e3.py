"""E2E-3/E2E-4: Nachlauf am Inhalts-Shot; kein Bridge; Audio-Split-Guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    FinalShot,
    IntraPauseMarker,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    PauseDirective,
    ResolvedAudioSegment,
    ResolvedShot,
    SegmentTiming,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    MIN_AUDIO_CLIP_SECONDS,
    _apply_chapter_envelopes,
    _build_resolved_audio_segments,
)


class _Locked:
    def __init__(self, segments: list) -> None:
        self.segments = segments


class _Seg:
    def __init__(self, segment_id: str, folder_name: str, sequence_index: int) -> None:
        self.segment_id = segment_id
        self.folder_name = folder_name
        self.sequence_index = sequence_index


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Yosemite_Caddo"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="e2e3",
        name="e2e3",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yosemite", "Caddo"],
        selected_asset_subdirs=["Yosemite", "Caddo"],
        fps=25.0,
        width=1920,
        height=1080,
    )


def test_postroll_on_content_shot_no_bridge_chapter_abut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-4: slot_014 = VO_ende+5; chapter_video_start(N+1)=chapter_video_end(N)."""
    project = _project(tmp_path)
    preroll = 5.0
    postroll = 5.0
    yos_vo = 40.0
    cad_vo = 20.0

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_reapply_hold_for_timeline_span",
        lambda *args, **kwargs: None,
    )

    # Roh-Timeline (vor Hülle): Yosemite 0–40, Caddo 40–60 (kein Bridge-Slot)
    shots = [
        ResolvedShot(
            shot_id="Yosemite_slot_001",
            asset_id="a1",
            timeline_start_seconds=0.0,
            timeline_end_seconds=20.0,
            source_start_seconds=0.0,
            source_end_seconds=20.0,
            resolved_media_path="/tmp/a.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=30.0,
            editorial_function="chapter_open",
            folder_name="Yosemite",
        ),
        ResolvedShot(
            shot_id="Yosemite_slot_014",
            asset_id="a2",
            timeline_start_seconds=20.0,
            timeline_end_seconds=yos_vo,
            source_start_seconds=0.0,
            source_end_seconds=20.0,
            resolved_media_path="/tmp/b.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=40.0,
            editorial_function="chapter_close",
            folder_name="Yosemite",
        ),
        ResolvedShot(
            shot_id="Caddo_slot_001",
            asset_id="c1",
            timeline_start_seconds=yos_vo,
            timeline_end_seconds=yos_vo + cad_vo,
            source_start_seconds=0.0,
            source_end_seconds=20.0,
            resolved_media_path="/tmp/c.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=30.0,
            editorial_function="chapter_open",
            folder_name="Caddo",
        ),
    ]
    audio = [
        ResolvedAudioSegment(
            segment_id="Yosemite_segment_001",
            audio_path="/tmp/y.mp3",
            timeline_start_seconds=0.0,
            timeline_end_seconds=yos_vo,
            source_start_seconds=0.0,
            source_end_seconds=yos_vo,
            pause_after_seconds=0.0,
        ),
        ResolvedAudioSegment(
            segment_id="Caddo_segment_001",
            audio_path="/tmp/c.mp3",
            timeline_start_seconds=yos_vo,
            timeline_end_seconds=yos_vo + cad_vo,
            source_start_seconds=0.0,
            source_end_seconds=cad_vo,
            pause_after_seconds=0.0,
        ),
    ]
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=yos_vo + cad_vo,
        entries=[
            NarrationTimelineEntry(
                segment_id="Yosemite_segment_001",
                start_seconds=0.0,
                end_seconds=yos_vo,
                pause_after_seconds=0.0,
                audio_duration_seconds=yos_vo,
            ),
            NarrationTimelineEntry(
                segment_id="Caddo_segment_001",
                start_seconds=yos_vo,
                end_seconds=yos_vo + cad_vo,
                pause_after_seconds=0.0,
                audio_duration_seconds=cad_vo,
            ),
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Yosemite_slot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Yosemite_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Yosemite_segment_001", offset_seconds=20.0
                ),
                asset_id="a1",
            ),
            FinalShot(
                shot_id="Yosemite_slot_014",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Yosemite_segment_001", offset_seconds=20.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Yosemite_segment_001", offset_seconds=yos_vo
                ),
                asset_id="a2",
            ),
            FinalShot(
                shot_id="Caddo_slot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=cad_vo
                ),
                asset_id="c1",
            ),
        ],
    )
    locked = _Locked(
        [
            _Seg("Yosemite_segment_001", "Yosemite", 1),
            _Seg("Caddo_segment_001", "Caddo", 2),
        ]
    )
    repairs: list[str] = []
    errors: list[str] = []
    envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=shots,
        audio_segments=audio,
        preroll=preroll,
        postroll=postroll,
        fps=25.0,
        repairs=repairs,
        errors=errors,
        narration_timeline=narration,
    )
    assert not errors, errors
    assert len(envelopes) == 2
    yos, cad = envelopes

    assert yos.preroll_seconds == pytest.approx(5.0)
    assert yos.postroll_seconds == pytest.approx(5.0)
    assert cad.preroll_seconds == pytest.approx(5.0)
    assert yos.last_shot_id == "Yosemite_slot_014"
    assert yos.postroll_hold_shot_id == "Yosemite_slot_014"
    assert not str(yos.last_shot_id).startswith("bridge_")
    assert not any(s.shot_id.startswith("bridge_") for s in shots)

    slot014 = next(s for s in shots if s.shot_id == "Yosemite_slot_014")
    assert slot014.timeline_end_seconds == pytest.approx(
        yos.chapter_audio_end + 5.0, abs=1e-3
    )
    assert slot014.timeline_end_seconds == pytest.approx(yos.chapter_video_end, abs=1e-3)

    # Kapitelwechsel = 10.00s ohne Narration (5 Nachlauf + 5 Vorlauf)
    assert cad.chapter_video_start == pytest.approx(yos.chapter_video_end, abs=1e-3)
    gap = cad.chapter_audio_start - yos.chapter_audio_end
    assert gap == pytest.approx(postroll + preroll, abs=1e-3)
    assert cad.chapter_audio_start - cad.chapter_video_start == pytest.approx(5.0)


def test_chapter_transition_ignored_no_narration_pause() -> None:
    """E2E-4: chapter_transition-Direktive wird ignoriert (Hülle deckt ab)."""
    sentences = {
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
            end_seconds=2.9,
            duration_seconds=1.5,
        ),
    }
    timeline = build_narration_timeline(
        script_version="v1",
        segment_timings=[
            SegmentTiming(
                segment_id="seg_001",
                script_version="v1",
                audio_path="/tmp/a.mp3",
                duration_seconds=3.0,
            ),
            SegmentTiming(
                segment_id="seg_002",
                script_version="v1",
                audio_path="/tmp/b.mp3",
                duration_seconds=2.0,
            ),
        ],
        pause_directives=[
            PauseDirective(
                after_segment_id="seg_001",
                after_sentence_id="seg_001__s002",
                pause_function="chapter_transition",
                duration_class="long",
            )
        ],
        sentence_index=sentences,
    )
    entry = timeline.entries[0]
    assert entry.intra_pauses == []
    assert entry.pause_after_seconds == pytest.approx(0.0)
    # Segmente liegen direkt aneinander (kein Bridge-Gap in der Narration).
    assert timeline.entries[1].start_seconds == pytest.approx(entry.end_seconds)


def test_tiny_remainder_split_is_merged_away() -> None:
    """0.04s-Waisenclip nach Split muss unmöglich sein."""
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=5.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=5.0,
                pause_after_seconds=0.0,
                audio_duration_seconds=3.0,
                intra_pauses=[
                    IntraPauseMarker(
                        after_sentence_id="seg_001__s001",
                        source_split_seconds=2.96,
                        pause_seconds=2.0,
                    )
                ],
            )
        ],
    )
    pieces = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map={
            "seg_001": SegmentTiming(
                segment_id="seg_001",
                script_version="v1",
                audio_path="/tmp/a.mp3",
                duration_seconds=3.0,
            )
        },
        fps=25.0,
    )
    assert pieces
    for piece in pieces:
        dur = piece.timeline_end_seconds - piece.timeline_start_seconds
        assert dur + 1e-9 >= MIN_AUDIO_CLIP_SECONDS
        src = (piece.source_end_seconds or 0) - piece.source_start_seconds
        assert src + 1e-9 >= MIN_AUDIO_CLIP_SECONDS or src == pytest.approx(3.0)
    assert not any(
        abs((p.source_end_seconds or 0) - p.source_start_seconds - 0.04) < 1e-6
        for p in pieces
    )
