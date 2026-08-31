"""Fix 2: Gap-/Bridge-Placeholder als ffmpeg-Slate."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.media_hold import (
    ensure_gap_placeholder_slate,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    _attach_resolve_markers,
    _shot_needs_manual_marker,
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import placeholders_dir
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    TimedSlot,
    _placeholder_resolved_shot,
    _short_asset_with_red_placeholder_tail,
)
import opentimelineio as otio
import pytest


def _project(tmp_path: Path) -> Project:
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )


def test_ensure_gap_placeholder_slate_writes_video(tmp_path: Path) -> None:
    project = _project(tmp_path)
    out = ensure_gap_placeholder_slate(
        project,
        shot_id="Yosemite_slot_011",
        gap_id="gap_yosemite_011",
        needed_visual="close-up waterfall mist",
        start_seconds=12.0,
        end_seconds=15.5,
        fps=25.0,
    )
    assert out.is_file()
    assert out.stat().st_size > 0
    assert out.parent == placeholders_dir(project)
    # Cache-Hit
    again = ensure_gap_placeholder_slate(
        project,
        shot_id="Yosemite_slot_011",
        gap_id="gap_yosemite_011",
        needed_visual="close-up waterfall mist",
        start_seconds=12.0,
        end_seconds=15.5,
        fps=25.0,
    )
    assert again == out


def test_placeholder_resolved_shot_sets_path_and_flags(tmp_path: Path) -> None:
    project = _project(tmp_path)
    timed = TimedSlot(
        slot_id="bridge_001",
        start_seconds=10.0,
        end_seconds=12.0,
        start_boundary_id="b0",
        end_boundary_id="b1",
        cut_alignment="sentence_boundary",
        asset_id=None,
        asset_fit="none",
        asset_fit_reason="Kapitelübergang",
        coverage_gap_id="gap_bridge_001",
        narrative_function="chapter_transition",
        source_range_intent="",
        visual_intent="chapter transition",
        needed_visual="chapter transition / hold",
    )
    shot = _placeholder_resolved_shot(project, timed, fps=25.0)
    assert shot.is_placeholder is True
    assert shot.open_gap is True
    assert shot.hold_mode == "placeholder_slate"
    assert shot.coverage_gap_id == "gap_bridge_001"
    assert Path(shot.resolved_media_path).is_file()


def test_short_asset_keeps_media_and_red_placeholder_tail(tmp_path: Path) -> None:
    """Zu kurz: nutzbares Asset + roter Shortfall-Placeholder statt Voll-Slate."""
    project = _project(tmp_path)
    media = tmp_path / "asset07.mp4"
    media.write_bytes(b"fake-mp4")
    timed = TimedSlot(
        slot_id="The_Wave_slot_007",
        start_seconds=10.0,
        end_seconds=18.0,
        start_boundary_id="b0",
        end_boundary_id="b1",
        cut_alignment="sentence_boundary",
        asset_id="asset_the_wave_asset07",
        asset_fit="strong",
        asset_fit_reason="",
        coverage_gap_id="gap_The_Wave_slot_007",
        narrative_function="evidence",
        source_range_intent="",
        visual_intent="wave detail",
        needed_visual="wave detail",
    )
    entry = {
        "path": str(media),
        "duration_seconds": 6.0,
        "usable_in_s": 0.0,
        "media_kind": "video",
        "media_type": "video",
        "available_start_seconds": 0.0,
        "folder": "A",
        "canonical_id": "asset_the_wave_asset07",
    }
    repairs: list[str] = []
    parts = _short_asset_with_red_placeholder_tail(
        project,
        timed,
        entry=entry,
        asset_id="asset_the_wave_asset07",
        fps=25.0,
        head_trim=1.0,
        short_tolerance=1.0,
        repairs=repairs,
    )
    # usable = 6 - 1 = 5s → Asset 5s + Placeholder 3s
    assert len(parts) == 2
    head, tail = parts
    assert head.is_placeholder is False
    assert head.open_gap is False
    # Gap-ID nur am Shortfall-Tail — sonst verlangt Gap-Merge export_ready auch für den Asset-Head.
    assert head.coverage_gap_id is None
    assert head.resolved_media_path == str(media)
    assert head.timeline_end_seconds - head.timeline_start_seconds == pytest.approx(5.0)
    assert tail.is_placeholder is True
    assert tail.shot_id.endswith("__shortfall")
    assert tail.coverage_gap_id == "gap_The_Wave_slot_007"
    assert Path(tail.resolved_media_path).is_file()
    assert "CC0000" in Path(tail.resolved_media_path).name or Path(
        tail.resolved_media_path
    ).is_file()
    assert head.timeline_end_seconds == pytest.approx(tail.timeline_start_seconds)
    assert tail.timeline_end_seconds == pytest.approx(18.0)
    assert any("roter Placeholder" in note for note in repairs)


def test_shortfall_shot_follows_parent_in_chapter_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shortfall muss mit dem Asset-Kopf in der Kapitelhülle mitverschoben werden."""
    from otio_app.services.without_voiceover_enhanced.models import (
        FinalCutPlanDocument,
        FinalShot,
        NarrationAnchor,
        NarrationTimelineDocument,
        NarrationTimelineEntry,
        ResolvedAudioSegment,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _apply_chapter_envelopes,
        _apply_visual_continuity_rules,
    )

    project = _project(tmp_path)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_reapply_hold_for_timeline_span",
        lambda *args, **kwargs: None,
    )

    class _Locked:
        def __init__(self) -> None:
            self.segments = [
                type(
                    "S",
                    (),
                    {
                        "segment_id": "The_Wave_segment_001",
                        "folder_name": "The Wave",
                        "sequence_index": 1,
                    },
                )()
            ]

    shots = [
        ResolvedShot(
            shot_id="The_Wave_slot_007",
            asset_id="asset07",
            timeline_start_seconds=52.72,
            timeline_end_seconds=57.72,
            source_start_seconds=1.0,
            source_end_seconds=6.0,
            resolved_media_path="/tmp/a.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=10.0,
            folder_name="The Wave",
        ),
        ResolvedShot(
            shot_id="The_Wave_slot_007__shortfall",
            asset_id="asset07",
            timeline_start_seconds=57.72,
            timeline_end_seconds=61.16,
            source_start_seconds=0.0,
            source_end_seconds=3.44,
            resolved_media_path="/tmp/p.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=3.44,
            folder_name="The Wave",
            is_placeholder=True,
            open_gap=True,
            hold_mode="placeholder_slate",
        ),
        ResolvedShot(
            shot_id="The_Wave_slot_008",
            asset_id="asset08",
            timeline_start_seconds=61.16,
            timeline_end_seconds=70.16,
            source_start_seconds=0.0,
            source_end_seconds=9.0,
            resolved_media_path="/tmp/b.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=12.0,
            folder_name="The Wave",
        ),
    ]
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="The_Wave_slot_007",
                asset_id="asset07",
                narration_start_anchor=NarrationAnchor(
                    segment_id="The_Wave_segment_001", offset_seconds=52.72
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="The_Wave_segment_001", offset_seconds=61.16
                ),
            ),
            FinalShot(
                shot_id="The_Wave_slot_008",
                asset_id="asset08",
                narration_start_anchor=NarrationAnchor(
                    segment_id="The_Wave_segment_001", offset_seconds=61.16
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="The_Wave_segment_001", offset_seconds=70.16
                ),
            ),
        ],
    )
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=80.0,
        entries=[
            NarrationTimelineEntry(
                segment_id="The_Wave_segment_001",
                start_seconds=0.0,
                end_seconds=80.0,
                audio_duration_seconds=80.0,
            )
        ],
    )
    audios = [
        ResolvedAudioSegment(
            segment_id="The_Wave_segment_001",
            audio_path="/tmp/vo.mp3",
            timeline_start_seconds=0.0,
            timeline_end_seconds=80.0,
        )
    ]
    repairs: list[str] = []
    errors: list[str] = []
    envelopes = _apply_chapter_envelopes(
        project,
        locked=_Locked(),
        final=final,
        ordered=shots,
        audio_segments=audios,
        preroll=4.0,
        postroll=0.0,
        fps=25.0,
        repairs=repairs,
        errors=errors,
        narration_timeline=narration,
    )
    assert envelopes
    assert all(s.chapter_id == "The Wave" for s in shots)
    # Dichte Kette nach Vorlauf-Shift
    ordered = sorted(shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    for prev, curr in zip(ordered, ordered[1:]):
        assert curr.timeline_start_seconds == pytest.approx(prev.timeline_end_seconds)
    cont_errors: list[str] = []
    _apply_visual_continuity_rules(
        ordered, project=project, fps=25.0, repairs=repairs, errors=cont_errors
    )
    assert not cont_errors


def test_resolve_markers_for_shortfall_shots() -> None:
    shot = ResolvedShot(
        shot_id="slot_x__shortfall",
        asset_id="a",
        timeline_start_seconds=5.0,
        timeline_end_seconds=8.0,
        source_start_seconds=0.0,
        source_end_seconds=3.0,
        is_placeholder=True,
        open_gap=True,
        coverage_gap_id="gap_x",
        asset_fit_reason="Asset zu kurz",
    )
    assert _shot_needs_manual_marker(shot)
    track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    clip = otio.schema.Clip(name=shot.shot_id)
    _attach_resolve_markers(
        clip=clip,
        video_track=track,
        shot=shot,
        fps=25.0,
        source_duration=3.0,
    )
    assert len(clip.markers) == 1
    assert len(track.markers) == 1
    assert "SHORTFALL" in clip.markers[0].name


def _dup_shot(shot_id: str, asset_id: str, start: float, end: float) -> ResolvedShot:
    return ResolvedShot(
        shot_id=shot_id,
        asset_id=asset_id,
        timeline_start_seconds=start,
        timeline_end_seconds=end,
        source_start_seconds=0.0,
        source_end_seconds=end - start,
        resolved_media_path=f"/media/{asset_id}.mp4",
    )


def test_consecutive_duplicate_shot_ids_marks_both_neighbors() -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        consecutive_duplicate_shot_ids,
    )

    shots = [
        _dup_shot("still_a", "other", 0.0, 2.0),
        _dup_shot("velika_slot_03", "Velika_Planina_Asset00001", 2.0, 6.0),
        _dup_shot("velika_slot_04", "Velika_Planina_Asset00001", 6.0, 10.0),
        _dup_shot("velika_slot_05", "Velika_Planina_Asset00006", 10.0, 14.0),
    ]
    flagged = consecutive_duplicate_shot_ids(shots)
    assert flagged == {"velika_slot_03", "velika_slot_04"}


def test_consecutive_duplicate_ignores_placeholder_and_separated_reuse() -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        consecutive_duplicate_shot_ids,
    )

    shots = [
        _dup_shot("a", "Asset00001", 0.0, 2.0),
        ResolvedShot(
            shot_id="gap",
            asset_id="",
            timeline_start_seconds=2.0,
            timeline_end_seconds=4.0,
            source_start_seconds=0.0,
            source_end_seconds=2.0,
            is_placeholder=True,
            open_gap=True,
        ),
        _dup_shot("b", "Asset00001", 4.0, 6.0),
        _dup_shot("c", "Asset00002", 6.0, 8.0),
        _dup_shot("d", "Asset00001", 8.0, 10.0),
    ]
    assert consecutive_duplicate_shot_ids(shots) == set()


def test_three_consecutive_same_assets_all_flagged() -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        consecutive_duplicate_shot_ids,
    )

    shots = [
        _dup_shot("s1", "Asset00001", 0.0, 2.0),
        _dup_shot("s2", "Asset00001", 2.0, 4.0),
        _dup_shot("s3", "Asset00001", 4.0, 6.0),
    ]
    assert consecutive_duplicate_shot_ids(shots) == {"s1", "s2", "s3"}


def test_resolve_markers_for_duplicate_assets() -> None:
    shot = _dup_shot("velika_slot_04", "Velika_Planina_Asset00001", 6.0, 10.0)
    track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    clip = otio.schema.Clip(name=shot.shot_id)
    _attach_resolve_markers(
        clip=clip,
        video_track=track,
        shot=shot,
        fps=25.0,
        source_duration=4.0,
        duplicate=True,
    )
    assert len(clip.markers) == 1
    assert clip.markers[0].name.startswith("DUPLICATE ASSET")
    assert clip.markers[0].metadata["duplicate_asset"] is True
    assert clip.name.startswith("DUPLICATE ·")
    assert (clip.metadata.get("Resolve_OTIO") or {}).get("Clip Color") == "Orange"
    assert clip.color is not None
    assert clip.markers[0].marked_range.duration.to_frames() == 1
    assert track.markers[0].marked_range.duration.to_frames() == 1


def test_consecutive_duplicate_matches_same_filename_different_ids() -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        consecutive_duplicate_shot_ids,
    )

    first = _dup_shot("s1", "id_a", 0.0, 2.0)
    second = _dup_shot("s2", "id_b", 2.0, 4.0)
    first.resolved_media_path = "/a/Velika_Planina_Asset00001.mp4"
    second.resolved_media_path = "/b/Velika_Planina_Asset00001.mp4"
    assert consecutive_duplicate_shot_ids([first, second]) == {"s1", "s2"}


def test_duplicate_review_track_lays_red_slate(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        _build_duplicate_review_track,
        consecutive_duplicate_shot_ids,
    )

    project = _project(tmp_path)
    shots = [
        _dup_shot("s1", "Asset00001", 0.0, 2.0),
        _dup_shot("s2", "Asset00001", 2.0, 4.0),
        _dup_shot("s3", "Asset00002", 4.0, 6.0),
    ]
    track = _build_duplicate_review_track(
        project,
        shots,
        consecutive_duplicate_shot_ids(shots),
        fps=25.0,
    )
    assert track is not None
    assert track.name == "Review"
    clips = [child for child in track if isinstance(child, otio.schema.Clip)]
    assert len(clips) == 2
    assert all("DUPLICATE ASSET" in child.name for child in clips)
    assert all(
        (child.metadata.get("Resolve_OTIO") or {}).get("Clip Color") == "Orange"
        for child in clips
    )


def test_production_gate_blocks_placeholder_even_with_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    slate = ensure_gap_placeholder_slate(
        project,
        shot_id="slot_x",
        gap_id="gap_x",
        needed_visual="x",
        start_seconds=0.0,
        end_seconds=2.0,
        fps=25.0,
    )
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="slot_x",
                asset_id="",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                resolved_media_path=str(slate),
                resolved_media_kind="video",
                hold_mode="placeholder_slate",
                open_gap=True,
                is_placeholder=True,
                coverage_gap_id="gap_x",
            )
        ],
    )
    errors = validate_resolved_timeline_for_production(project, resolved)
    assert any("Placeholder" in e or "offener Gap" in e for e in errors)
    assert not any("resolved_media_path fehlt" in e for e in errors)


def test_allow_errors_export_writes_placeholder_instead_of_empty_gap(
    tmp_path: Path,
) -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        export_otio_from_resolved_timeline,
    )

    project = _project(tmp_path)
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="missing_shot",
                asset_id="gone",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                resolved_media_path=str(tmp_path / "does_not_exist.mp4"),
                resolved_media_kind="video",
            )
        ],
        errors=["Asset gone fehlt — Produktions-Export würde blockieren."],
    )
    out = export_otio_from_resolved_timeline(
        project,
        basename="preview_gaps",
        allow_errors=True,
        resolved=resolved,
    )
    timeline = otio.adapters.read_from_file(str(out))
    video = next(track for track in timeline.tracks if track.kind == otio.schema.TrackKind.Video)
    names = [item.name for item in video]
    assert "missing_shot" in names
    missing = next(item for item in video if item.name == "missing_shot")
    assert isinstance(missing, otio.schema.Clip)
    url = str(missing.media_reference.target_url)
    assert "placeholder" in url.lower()
    assert timeline.metadata.get("enhanced_export_mode") == "test_gaps"
