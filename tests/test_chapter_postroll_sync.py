"""Nachlauf muss auf der Videospur bleiben, sonst verschiebt sich das VO."""

from __future__ import annotations

import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.media_hold import (
    ensure_last_frame_hold,
)
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    _apply_chapter_envelopes,
)


class _Locked:
    def __init__(self, segments: list) -> None:
        self.segments = segments


class _Seg:
    def __init__(self, segment_id: str, folder_name: str, sequence_index: int) -> None:
        self.segment_id = segment_id
        self.folder_name = folder_name
        self.sequence_index = sequence_index


def _ffmpeg_color_video(path: Path, *, duration: float, color: str, fps: int = 25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x240:d={duration:.3f}:r={fps}",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _write_silent_wav(path: Path, duration: float, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(duration * sample_rate))
    import wave

    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    names = folders or ["Goriška Brda", "Vintgar-Klamm"]
    root = tmp_path / "Slowenien"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
    return Project(
        id="postroll-sync",
        name="postroll-sync",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=names,
        selected_asset_subdirs=names,
        fps=25.0,
        width=1920,
        height=1080,
    )


def test_last_frame_hold_has_requested_duration(tmp_path: Path) -> None:
    project = _project(tmp_path, ["A"])
    video = Path(project.project_root) / "A" / "clip.mp4"
    _ffmpeg_color_video(video, duration=2.0, color="blue")
    hold = ensure_last_frame_hold(project, video, duration_seconds=5.0, fps=25.0)
    assert hold.is_file()
    duration = probe_duration_seconds(hold)
    assert duration is not None
    assert duration == pytest.approx(5.0, abs=0.12)


def test_short_closing_clip_gets_last_frame_postroll(tmp_path: Path) -> None:
    """Goriška-Fall: letzter Shot reicht fürs VO, nicht für +5s Nachlauf."""
    project = _project(tmp_path)
    vo = 8.0
    postroll = 5.0
    close = Path(project.project_root) / "Goriška Brda" / "close.mp4"
    nxt = Path(project.project_root) / "Vintgar-Klamm" / "open.mp4"
    _ffmpeg_color_video(close, duration=vo, color="green")
    _ffmpeg_color_video(nxt, duration=12.0, color="red")

    shots = [
        ResolvedShot(
            shot_id="Goriška_Brda_slot_012",
            asset_id="close",
            timeline_start_seconds=0.0,
            timeline_end_seconds=vo,
            source_start_seconds=0.0,
            source_end_seconds=vo,
            resolved_media_path=str(close),
            resolved_media_kind="video",
            resolved_media_duration_seconds=vo,
            resolved_available_start_seconds=0.0,
            editorial_function="chapter_close",
            folder_name="Goriška Brda",
            chapter_id="Goriška Brda",
        ),
        ResolvedShot(
            shot_id="Vintgar-Klamm_slot_001",
            asset_id="open",
            timeline_start_seconds=vo,
            timeline_end_seconds=vo + 6.0,
            source_start_seconds=0.0,
            source_end_seconds=6.0,
            resolved_media_path=str(nxt),
            resolved_media_kind="video",
            resolved_media_duration_seconds=12.0,
            resolved_available_start_seconds=0.0,
            editorial_function="chapter_open",
            folder_name="Vintgar-Klamm",
            chapter_id="Vintgar-Klamm",
        ),
    ]
    audio = [
        ResolvedAudioSegment(
            segment_id="Goriška_Brda_segment_001",
            audio_path="/tmp/g.wav",
            timeline_start_seconds=0.0,
            timeline_end_seconds=vo,
        ),
        ResolvedAudioSegment(
            segment_id="Vintgar-Klamm_segment_001",
            audio_path="/tmp/v.wav",
            timeline_start_seconds=vo,
            timeline_end_seconds=vo + 6.0,
        ),
    ]
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=vo + 6.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="Goriška_Brda_segment_001",
                start_seconds=0.0,
                end_seconds=vo,
                audio_duration_seconds=vo,
            ),
            NarrationTimelineEntry(
                segment_id="Vintgar-Klamm_segment_001",
                start_seconds=vo,
                end_seconds=vo + 6.0,
                audio_duration_seconds=6.0,
            ),
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Goriška_Brda_slot_012",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Goriška_Brda_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Goriška_Brda_segment_001", offset_seconds=vo
                ),
                asset_id="close",
            ),
            FinalShot(
                shot_id="Vintgar-Klamm_slot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Vintgar-Klamm_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Vintgar-Klamm_segment_001", offset_seconds=6.0
                ),
                asset_id="open",
            ),
        ],
    )
    locked = _Locked(
        [
            _Seg("Goriška_Brda_segment_001", "Goriška Brda", 1),
            _Seg("Vintgar-Klamm_segment_001", "Vintgar-Klamm", 2),
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
        preroll=0.0,
        postroll=postroll,
        fps=25.0,
        repairs=repairs,
        errors=errors,
        narration_timeline=narration,
    )
    assert not errors, errors
    assert len(envelopes) == 2
    first, second = envelopes
    post = next(s for s in shots if s.shot_id == "Goriška_Brda_postroll")
    close_shot = next(s for s in shots if s.shot_id == "Goriška_Brda_slot_012")
    assert close_shot.timeline_end_seconds == pytest.approx(first.chapter_audio_end, abs=1e-3)
    assert post.editorial_function == "technical_chapter_postroll"
    assert post.hold_mode == "freeze_video"
    assert post.timeline_start_seconds == pytest.approx(first.chapter_audio_end, abs=1e-3)
    assert post.timeline_end_seconds == pytest.approx(first.chapter_video_end, abs=1e-3)
    assert post.timeline_end_seconds - post.timeline_start_seconds == pytest.approx(
        postroll, abs=1e-3
    )
    assert first.postroll_hold_shot_id == post.shot_id
    assert second.chapter_video_start == pytest.approx(first.chapter_video_end, abs=1e-3)
    assert second.chapter_audio_start == pytest.approx(
        second.chapter_video_start, abs=1e-3
    )


def test_export_pads_short_source_so_next_clip_stays_aligned(tmp_path: Path) -> None:
    """Test-OTIO darf einen zu kurzen Source-Clip nicht stillschweigend kürzen."""
    project = _project(tmp_path, ["A"])
    short = Path(project.project_root) / "A" / "short.mp4"
    nxt = Path(project.project_root) / "A" / "next.mp4"
    _ffmpeg_color_video(short, duration=5.0, color="blue")
    _ffmpeg_color_video(nxt, duration=3.0, color="red")
    wav = Path(project.work_dir) / "vo.wav"
    _write_silent_wav(wav, 3.0)
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=13.0,
        voiceover_postroll_sec=5.0,
        shots=[
            ResolvedShot(
                shot_id="A_slot_001",
                asset_id="short",
                timeline_start_seconds=0.0,
                timeline_end_seconds=10.0,
                source_start_seconds=0.0,
                source_end_seconds=5.0,
                resolved_media_path=str(short),
                resolved_media_kind="video",
                resolved_media_duration_seconds=5.0,
                folder_name="A",
                chapter_id="A",
            ),
            ResolvedShot(
                shot_id="A_slot_002",
                asset_id="next",
                timeline_start_seconds=10.0,
                timeline_end_seconds=13.0,
                source_start_seconds=0.0,
                source_end_seconds=3.0,
                resolved_media_path=str(nxt),
                resolved_media_kind="video",
                resolved_media_duration_seconds=3.0,
                folder_name="A",
                chapter_id="A",
            ),
        ],
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="A_segment_001",
                audio_path=str(wav),
                timeline_start_seconds=10.0,
                timeline_end_seconds=13.0,
            )
        ],
    )
    out = export_otio_from_resolved_timeline(
        project,
        basename="postroll_pad",
        allow_errors=True,
        resolved=resolved,
    )
    timeline = otio.adapters.read_from_file(str(out))
    video = next(t for t in timeline.tracks if t.name == "Video")
    narr = next(t for t in timeline.tracks if t.name == "Narration")
    kinds = [type(item).__name__ for item in video]
    assert "Gap" in kinds
    video_dur = sum(item.duration().to_seconds() for item in video)
    narr_dur = sum(item.duration().to_seconds() for item in narr)
    assert video_dur == pytest.approx(13.0, abs=0.08)
    vo = next(item for item in narr if isinstance(item, otio.schema.Clip))
    vo_start = sum(
        item.duration().to_seconds()
        for item in narr
        if narr.index(item) < narr.index(vo)
    )
    second = next(item for item in video if getattr(item, "name", "") == "A_slot_002")
    second_start = sum(
        item.duration().to_seconds()
        for item in video
        if video.index(item) < video.index(second)
    )
    assert second_start == pytest.approx(10.0, abs=0.08)
    assert vo_start == pytest.approx(10.0, abs=0.08)
    assert narr_dur == pytest.approx(13.0, abs=0.08)
