"""Tests für OTIO-Export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    EditPlanShot,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
)
from otio_app.models import Project
from otio_app.services.edit_plan_builder import save_edit_plan
from otio_app.services.media_utils import MediaTiming
from otio_app.services.otio_exporter import (
    _clip_name_for_media,
    _clip_source_range_for_media,
    _compute_timeline_sections,
    _media_reference,
    _media_target_url,
    build_otio_timeline,
    export_otio_timeline,
    merge_confirmed_edit_plans,
)
from otio_app.services.otio_export_settings import OtioExportSettings


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="otio-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys", "Grand Canyon"],
        selected_asset_subdirs=["Florida Keys", "Grand Canyon"],
    )


def _shot(folder: str, voice_file: str, index: int) -> EditPlanShot:
    return EditPlanShot(
        voice_file=voice_file,
        folder=folder,
        voice_start_sec=float(index * 3),
        voice_end_sec=float(index * 3 + 3),
        duration_sec=3.0,
        asset_path=str(Path(f"/media/{folder.replace(' ', '_')}_{index}.mp4")),
        motif=f"motif {index}",
        passage_text=f"text {index}",
    )


def _setup_mapping_and_plans(project: Project, tmp_path: Path) -> None:
    voice_a = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Florida Keys_VO.wav")
    voice_b = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Grand Canyon_VO.wav")
    Path(voice_a).parent.mkdir(parents=True, exist_ok=True)
    Path(voice_a).write_bytes(b"wav")
    Path(voice_b).write_bytes(b"wav")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_a,
                folder="Florida Keys",
                confirmed=True,
            ),
            VoiceFolderMappingEntry(
                voice_file=voice_b,
                folder="Grand Canyon",
                confirmed=True,
            ),
        ],
    )
    project.voice_folder_mapping_path.write_text(
        mapping.model_dump_json(indent=2),
        encoding="utf-8",
    )

    plan_settings = EditPlanSettings(audio_offset_sec=1.0, section_outro_sec=5.0)
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            settings=plan_settings,
            shots=[_shot("Florida Keys", voice_a, 1), _shot("Florida Keys", voice_a, 2)],
        ),
        "Florida Keys",
    )
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Grand Canyon",
            confirmed=True,
            settings=plan_settings,
            shots=[_shot("Grand Canyon", voice_b, 1)],
        ),
        "Grand Canyon",
    )


def test_merge_confirmed_edit_plans_in_mapping_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)

    merged = merge_confirmed_edit_plans(project)
    assert merged.ready is True
    assert merged.included_folders == ["Florida Keys", "Grand Canyon"]
    assert len(merged.shots) == 3


def test_timeline_sections_include_outro_and_per_section_voice_offset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)
    settings = EditPlanSettings(audio_offset_sec=1.0, section_outro_sec=5.0)

    sections = _compute_timeline_sections(merged.shots, settings)
    assert len(sections) == 2
    assert sections[0].video_start_sec == 0.0
    assert sections[0].video_duration_sec == 11.0
    assert sections[0].voice_start_sec == 1.0
    assert sections[1].video_start_sec == 11.0
    assert sections[1].video_duration_sec == 8.0
    assert sections[1].voice_start_sec == 12.0


def test_clip_durations_use_seconds_not_frames(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    timeline = build_otio_timeline(
        project,
        merged,
        export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
    )
    video_track = timeline.tracks[0]
    clips = [item for item in video_track if isinstance(item, otio.schema.Clip)]
    assert clips[0].source_range.duration.to_seconds() == 3.0
    assert clips[1].source_range.duration.to_seconds() == 8.0
    assert clips[2].source_range.duration.to_seconds() == 8.0
    assert clips[0].name == "Florida_Keys_1.mp4"
    assert clips[0].media_reference.target_url.startswith("/")


def test_audio_offset_and_outro_on_export(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    timeline = build_otio_timeline(
        project,
        merged,
        export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
    )
    assert isinstance(timeline.tracks[0][0], otio.schema.Clip)

    florida_audio = timeline.tracks[1]
    assert florida_audio[0].source_range.duration.to_seconds() == 1.0
    assert florida_audio[1].source_range.duration.to_seconds() == 10.0

    canyon_audio = timeline.tracks[2]
    assert canyon_audio[0].source_range.duration.to_seconds() == 12.0
    assert canyon_audio[1].source_range.duration.to_seconds() == 7.0


def test_video_section_padded_when_media_shorter_than_planned(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)
    short_timing = MediaTiming(start_sec=0.0, duration_sec=2.0, rate=25.0)

    with patch(
        "otio_app.services.otio_exporter.probe_media_timing",
        return_value=short_timing,
    ), patch(
        "otio_app.services.otio_exporter.probe_duration_seconds",
        return_value=2.0,
    ):
        timeline = build_otio_timeline(
            project,
            merged,
            export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
        )

    video_track = timeline.tracks[0]
    gaps = [item for item in video_track if isinstance(item, otio.schema.Gap)]
    assert not any("Ausklingen" in gap.name for gap in gaps)
    clips = [item for item in video_track if isinstance(item, otio.schema.Clip)]
    assert clips[1].source_range.duration.to_seconds() > 8.0
    assert clips[2].source_range.duration.to_seconds() == 8.0
    total_video = sum(item.source_range.duration.to_seconds() for item in video_track)
    assert total_video == 19.0


def test_clip_source_range_hold_last_frame_beyond_media(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    timing = MediaTiming(start_sec=0.0, duration_sec=5.0, rate=25.0)

    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        source_range, play_sec, notes = _clip_source_range_for_media(
            media,
            fallback_rate=25.0,
            requested_duration_sec=12.0,
            hold_last_frame=True,
        )

    assert play_sec == 12.0
    assert source_range.duration.to_seconds() == 12.0
    assert any("gehalten" in note for note in notes)


def test_media_reference_aligns_available_range_with_embedded_timecode(tmp_path: Path) -> None:
    media = tmp_path / "Arches_National_Park_Asset03.mp4"
    media.write_bytes(b"x")

    timing = MediaTiming(start_sec=15.04, duration_sec=14.88, rate=25.0)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        ref = _media_reference(str(media), 25.0)

    assert ref.available_range is not None
    assert ref.available_range.start_time.to_seconds() == 15.04
    assert abs(ref.available_range.duration.to_seconds() - 14.88) < 0.01
    assert "Arches_National_Park_Asset03.mp4" in ref.target_url


def test_media_target_url_uses_absolute_posix_path(tmp_path: Path) -> None:
    folder = tmp_path / "Unglaubliche Welt"
    folder.mkdir()
    media = folder / "Apostle_Islands_Asset01.mp4"
    media.write_bytes(b"x")
    url = _media_target_url(media)
    assert url.startswith("/")
    assert "file://" not in url
    assert "Apostle_Islands_Asset01.mp4" in url
    assert "%20" not in url
    assert "Unglaubliche Welt" in url


def test_clip_source_range_starts_at_embedded_timecode(tmp_path: Path) -> None:
    media = tmp_path / "Arches_National_Park_Asset03.mp4"
    media.write_bytes(b"x")
    timing = MediaTiming(start_sec=15.04, duration_sec=14.88, rate=25.0)

    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        source_range, play_sec, notes = _clip_source_range_for_media(
            media,
            fallback_rate=25.0,
            requested_duration_sec=14.88,
        )

    assert source_range.start_time.to_seconds() == 15.04
    assert abs(play_sec - 14.88) < 0.01
    assert notes == []


def test_clip_source_range_applies_trim_leading(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    timing = MediaTiming(start_sec=0.0, duration_sec=10.0, rate=25.0)

    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        source_range, play_sec, notes = _clip_source_range_for_media(
            media,
            fallback_rate=25.0,
            requested_duration_sec=5.0,
            trim_leading_sec=0.5,
        )

    assert source_range.start_time.to_seconds() == 0.5
    assert play_sec == 5.0
    assert any("0.5s" in note for note in notes)


def test_clip_name_for_media_uses_filename() -> None:
    assert _clip_name_for_media(Path("/tmp/Arches_National_Park_Asset03.mp4"), index=3) == (
        "Arches_National_Park_Asset03.mp4"
    )


def test_merge_skips_ffmpeg_decode_on_preview(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    calls: list[Path] = []

    def _fake_validate(path: Path) -> tuple[bool, str | None]:
        calls.append(path)
        return True, None

    monkeypatch.setattr(
        "otio_app.services.otio_exporter.validate_clean_output",
        _fake_validate,
    )
    monkeypatch.setattr(
        "otio_app.services.otio_exporter.path_is_readable_file",
        lambda _path: True,
    )

    merged = merge_confirmed_edit_plans(project)
    assert merged.ready is True
    assert calls == []


def test_export_otio_timeline_writes_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    with patch(
        "otio_app.services.otio_exporter.verify_shot_media_paths",
        return_value=[],
    ):
        export_result = export_otio_timeline(
            project,
            merged,
            export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
        )
    assert export_result.path.is_file()
    assert (project.work_dir_path / "otio_export_settings.json").is_file()

    timeline = otio.adapters.read_from_file(str(export_result.path))
    assert timeline.name == "USA"
    assert len(timeline.tracks) == 3
