"""Tests für Voice-over-Trim-Regeln (nur visuelle Assets, nie Audio)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest

from otio_app.analysis_models import (
    EditPlanSettings,
    EditPlanShot,
    TimelineItem,
    TimelineItemTransform,
    VoiceoverPlan,
)
from otio_app.services.edit_plan_validator import (
    ValidationStatus,
    validate_timeline_items,
    validate_voiceover_plan,
)
from otio_app.services.media_utils import MediaTiming
from otio_app.services.otio_exporter import (
    _append_aligned_voice_track,
    _clip_source_range_for_media,
    _compute_timeline_sections,
    build_otio_timeline,
    validate_otio_readback,
)
from otio_app.services.timeline_plan_builder import (
    build_timeline_items_for_folder,
    build_voiceover_plan,
)


def _settings(**kwargs) -> EditPlanSettings:
    return EditPlanSettings(
        audio_offset_sec=1.0,
        video_head_trim_sec=0.5,
        video_head_trim_policy="fixed_trim",
        voiceover_trim_policy="disabled",
        section_outro_sec=5.0,
        **kwargs,
    )


def test_audio_offset_creates_a1_gap(tmp_path: Path) -> None:
    timeline = otio.schema.Timeline(name="test")
    voice = VoiceoverPlan(
        path=str(tmp_path / "voice.wav"),
        timeline_start_sec=1.0,
        source_in_sec=0.0,
        source_out_sec=10.0,
        duration_sec=10.0,
        timeline_end_sec=11.0,
        duration_source="ffprobe",
        trim_policy="disabled",
    )
    from otio_app.services.otio_exporter import TimelineSection

    section = TimelineSection(
        voice_file=voice.path,
        folder="Canyon",
        video_start_sec=0.0,
        video_duration_sec=20.0,
        voiceover=voice,
    )
    Path(voice.path).write_bytes(b"wav")
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=MediaTiming(0, 10, 25)):
        _append_aligned_voice_track(timeline, section, 25.0, track_index=1)

    track = timeline.tracks[0]
    assert isinstance(track[0], otio.schema.Gap)
    assert track[0].source_range.duration.to_seconds() == 1.0


def test_voiceover_source_in_stays_zero(tmp_path: Path) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"wav")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=12.0):
        plan = build_voiceover_plan(str(wav), _settings())
    assert plan.source_in_sec == 0.0
    assert plan.trim_policy == "disabled"


def test_head_trim_only_on_video_assets(tmp_path: Path) -> None:
    shots = [
        EditPlanShot(
            voice_file="/v.wav",
            folder="Canyon",
            voice_start_sec=0.0,
            voice_end_sec=5.0,
            duration_sec=5.0,
            asset_path=str(tmp_path / "clip.mp4"),
        ),
        EditPlanShot(
            voice_file="/v.wav",
            folder="Canyon",
            voice_start_sec=5.0,
            voice_end_sec=10.0,
            duration_sec=5.0,
            asset_path=str(tmp_path / "photo.jpg"),
        ),
    ]
    (tmp_path / "clip.mp4").write_bytes(b"v")
    (tmp_path / "photo.jpg").write_bytes(b"i")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=20.0):
        items, voiceover, _ = build_timeline_items_for_folder(
            shots,
            folder_name="Canyon",
            voice_file="/v.wav",
            settings=_settings(),
            folder_assets=[
                {"path": str(tmp_path / "clip.mp4"), "description": "video"},
                {"path": str(tmp_path / "photo.jpg"), "description": "image"},
                {"path": str(tmp_path / "filler.mp4"), "description": "establishing wide"},
            ],
            trim_leading_sec=0.5,
        )
    video = next(i for i in items if i.type == "video_shot")
    image = next(i for i in items if i.type == "image_shot")
    assert video.source_in_sec == 0.5
    assert image.source_in_sec == 0.0
    assert voiceover.source_in_sec == 0.0


def test_wav_never_trimmed_by_video_head_trim(tmp_path: Path) -> None:
    media = tmp_path / "voice.wav"
    media.write_bytes(b"wav")
    timing = MediaTiming(start_sec=0.0, duration_sec=30.0, rate=25.0)
    timeline = otio.schema.Timeline(name="t")
    voice = VoiceoverPlan(
        path=str(media),
        timeline_start_sec=1.0,
        source_in_sec=0.0,
        source_out_sec=30.0,
        duration_sec=30.0,
        timeline_end_sec=31.0,
        duration_source="ffprobe",
        trim_policy="disabled",
    )
    from otio_app.services.otio_exporter import TimelineSection

    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        _append_aligned_voice_track(
            timeline,
            TimelineSection(str(media), "Canyon", 0.0, 40.0, voice),
            25.0,
            track_index=1,
        )
    clip = timeline.tracks[0][1]
    assert clip.source_range.start_time.to_seconds() == 0.0
    assert clip.source_range.duration.to_seconds() == 30.0


def test_voiceover_duration_from_ffprobe_not_text_segments(tmp_path: Path) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"wav")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=92.22):
        plan = build_voiceover_plan(str(wav), _settings())
    assert plan.duration_source == "ffprobe"
    assert plan.duration_sec == pytest.approx(92.22)
    assert plan.timeline_end_sec == pytest.approx(93.22)


def test_voiceover_not_clipped_to_last_text_segment() -> None:
    voiceover = VoiceoverPlan(
        path="/voice.wav",
        timeline_start_sec=1.0,
        source_in_sec=0.0,
        source_out_sec=50.0,
        duration_sec=50.0,
        timeline_end_sec=51.0,
        duration_source="ffprobe",
        trim_policy="disabled",
    )
    items = [
        TimelineItem(
            timeline_item_id="n1",
            type="video_shot",
            section_id="s1",
            folder_name="Canyon",
            voice_file="/voice.wav",
            resolved_media_path="/a.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=8.0,
            duration_sec=8.0,
            final_duration_sec=8.0,
            source_in_sec=0.5,
            source_out_sec=8.5,
            voice_start_sec=0.0,
            voice_end_sec=25.0,
        )
    ]
    result = validate_voiceover_plan(voiceover, settings=_settings(), items=items)
    assert not any("Textsegment" in e for e in result.errors)
    assert voiceover.duration_sec > items[0].voice_end_sec


def test_filler_when_visuals_end_before_voice(tmp_path: Path) -> None:
    shots = [
        EditPlanShot(
            voice_file=str(tmp_path / "v.wav"),
            folder="Canyon",
            voice_start_sec=0.0,
            voice_end_sec=5.0,
            duration_sec=5.0,
            asset_path=str(tmp_path / "main.mp4"),
        ),
    ]
    (tmp_path / "main.mp4").write_bytes(b"v")
    (tmp_path / "filler.mp4").write_bytes(b"v")
    (tmp_path / "outro.mp4").write_bytes(b"v")
    (tmp_path / "v.wav").write_bytes(b"w")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=20.0):
        items, voiceover, errors = build_timeline_items_for_folder(
            shots,
            folder_name="Canyon",
            voice_file=str(tmp_path / "v.wav"),
            settings=_settings(),
            folder_assets=[
                {"path": str(tmp_path / "main.mp4"), "description": "main"},
                {"path": str(tmp_path / "filler.mp4"), "description": "establishing wide"},
                {"path": str(tmp_path / "outro.mp4"), "description": "ruhige Landschaft overview"},
            ],
            trim_leading_sec=0.5,
        )
    assert not errors
    assert voiceover.duration_sec == 20.0
    fillers = [i for i in items if i.type == "generic_narration_visual"]
    assert fillers
    assert fillers[0].source_in_sec == 0.5
    assert fillers[0].duration_sec <= 8.0
    outro = next(i for i in items if i.type == "generic_outro_visual")
    assert outro.timeline_in_sec == pytest.approx(voiceover.timeline_end_sec)


def test_generic_outro_starts_after_full_voiceover(tmp_path: Path) -> None:
    shots = [
        EditPlanShot(
            voice_file=str(tmp_path / "v.wav"),
            folder="Canyon",
            voice_start_sec=0.0,
            voice_end_sec=3.0,
            duration_sec=3.0,
            asset_path=str(tmp_path / "a.mp4"),
        ),
    ]
    (tmp_path / "a.mp4").write_bytes(b"v")
    (tmp_path / "filler.mp4").write_bytes(b"v")
    (tmp_path / "outro.mp4").write_bytes(b"v")
    (tmp_path / "v.wav").write_bytes(b"w")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=8.0):
        items, voiceover, errors = build_timeline_items_for_folder(
            shots,
            folder_name="Canyon",
            voice_file=str(tmp_path / "v.wav"),
            settings=_settings(),
            folder_assets=[
                {"path": str(tmp_path / "a.mp4"), "description": "a"},
                {"path": str(tmp_path / "filler.mp4"), "description": "establishing wide"},
                {"path": str(tmp_path / "outro.mp4"), "description": "ruhige Landschaft overview"},
            ],
            trim_leading_sec=0.5,
        )
    assert not errors
    outro = next(i for i in items if i.type == "generic_outro_visual")
    assert outro.timeline_in_sec >= voiceover.timeline_end_sec - 0.01


def test_otio_readback_detects_clipped_voiceover() -> None:
    voice = VoiceoverPlan(
        path="/voice.wav",
        timeline_start_sec=1.0,
        source_in_sec=0.0,
        source_out_sec=10.0,
        duration_sec=10.0,
        timeline_end_sec=11.0,
        duration_source="ffprobe",
        trim_policy="disabled",
    )
    from otio_app.services.otio_exporter import TimelineSection

    section = TimelineSection("/voice.wav", "Canyon", 0.0, 20.0, voice)
    timeline = otio.schema.Timeline(name="t")
    track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    track.append(otio.schema.Gap(name="gap", source_range=otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, 25),
        duration=otio.opentime.RationalTime(25, 25),
    )))
    clip = otio.schema.Clip(name="voice")
    clip.source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(12.5, 25),
        duration=otio.opentime.RationalTime(125, 25),
    )
    track.append(clip)
    timeline.tracks.append(track)
    reports = validate_otio_readback(
        timeline,
        sections=[section],
        items=[],
        audio_offset_sec=1.0,
    )
    assert not reports[0].ok
    assert any("source_in_sec" in e for e in reports[0].errors)


def test_all_video_assets_keep_source_in_half_second(tmp_path: Path) -> None:
    shots = [
        EditPlanShot(
            voice_file="/v.wav",
            folder="Canyon",
            voice_start_sec=0.0,
            voice_end_sec=4.0,
            duration_sec=4.0,
            asset_path=str(tmp_path / "a.mp4"),
        ),
        EditPlanShot(
            voice_file="/v.wav",
            folder="Canyon",
            voice_start_sec=4.0,
            voice_end_sec=8.0,
            duration_sec=4.0,
            asset_path=str(tmp_path / "b.mp4"),
        ),
    ]
    (tmp_path / "a.mp4").write_bytes(b"v")
    (tmp_path / "b.mp4").write_bytes(b"v")
    (tmp_path / "outro.mp4").write_bytes(b"v")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=15.0):
        items, voiceover, _ = build_timeline_items_for_folder(
            shots,
            folder_name="Canyon",
            voice_file="/v.wav",
            settings=_settings(),
            folder_assets=[
                {"path": str(tmp_path / "a.mp4"), "description": "a"},
                {"path": str(tmp_path / "b.mp4"), "description": "b"},
                {"path": str(tmp_path / "outro.mp4"), "description": "ruhige Landschaft wide"},
            ],
            trim_leading_sec=0.5,
        )
    video_items = [
        i for i in items if i.type in {"video_shot", "generic_narration_visual", "generic_outro_visual"}
        and not str(i.resolved_media_path).endswith(".jpg")
    ]
    for item in video_items:
        assert item.source_in_sec == pytest.approx(0.5)
    assert voiceover.source_in_sec == 0.0
