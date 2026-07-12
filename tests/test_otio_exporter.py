"""Tests für OTIO-Export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanSettings,
    EditPlanShot,
    TimelineItem,
    TimelineItemTransform,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceoverPlan,
)
from otio_app.models import Project
from otio_app.services.edit_plan_builder import save_edit_plan
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES, save_edit_plan_rules
from otio_app.services.media_utils import MediaTiming
from otio_app.services.otio_exporter import (
    _clip_name_for_media,
    _clip_source_range_for_media,
    _compute_timeline_sections,
    _media_reference,
    _media_target_url,
    _plan_section_items,
    build_otio_timeline,
    export_otio_timeline,
    merge_confirmed_edit_plans,
)
from otio_app.services.otio_export_settings import OtioExportSettings


@pytest.fixture(autouse=True)
def _readable_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.otio_exporter.path_is_readable_file",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "otio_app.services.otio_exporter.validate_clean_output",
        lambda _path: (True, None),
    )


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


def _section_id(folder: str) -> str:
    return f"section_{folder.replace(' ', '_').lower()}"


def _timeline_narration(
    folder: str,
    voice_file: str,
    index: int,
    *,
    timeline_in: float,
    source_in: float = 0.5,
) -> TimelineItem:
    duration = 3.0
    path = str(Path(f"/media/{folder.replace(' ', '_')}_{index}.mp4"))
    return TimelineItem(
        timeline_item_id=f"item_{folder}_{index}",
        type="video_shot",
        section_id=_section_id(folder),
        folder_name=folder,
        voice_file=voice_file,
        asset_id=f"asset_{index}",
        shot_id=f"shot_{index:03d}",
        resolved_media_path=path,
        original_asset_path=path,
        asset_role="narration",
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_in + duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=source_in,
        source_out_sec=source_in + duration,
        voice_start_sec=float((index - 1) * 3),
        voice_end_sec=float(min((index - 1) * 3 + 3, 5.0)),
        selection_reason="test",
        confidence=0.8,
        transform=TimelineItemTransform(),
        motif=f"motif {index}",
        passage_text=f"text {index}",
    )


def _voiceover_plan(voice_file: str, *, duration: float, offset: float = 1.0) -> VoiceoverPlan:
    return VoiceoverPlan(
        path=voice_file,
        timeline_start_sec=offset,
        source_in_sec=0.0,
        source_out_sec=duration,
        duration_sec=duration,
        timeline_end_sec=offset + duration,
        duration_source="ffprobe",
        trim_policy="disabled",
    )


def _timeline_filler(
    folder: str,
    voice_file: str,
    *,
    timeline_in: float,
    duration: float = 1.0,
) -> TimelineItem:
    path = str(Path(f"/media/{folder.replace(' ', '_')}_filler.mp4"))
    return TimelineItem(
        timeline_item_id=f"filler_{folder.replace(' ', '_')}",
        type="generic_narration_visual",
        section_id=_section_id(folder),
        folder_name=folder,
        voice_file=voice_file,
        asset_id="asset_filler",
        shot_id="filler_001",
        resolved_media_path=path,
        original_asset_path=path,
        asset_role="generic_narration_visual",
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_in + duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=0.5,
        source_out_sec=0.5 + duration,
        voice_start_sec=timeline_in,
        voice_end_sec=timeline_in + duration,
        selection_reason="Filler bis Voice-Ende",
        confidence=0.5,
        transform=TimelineItemTransform(),
        motif="Filler",
    )


def _timeline_outro(
    folder: str,
    voice_file: str,
    *,
    timeline_in: float,
    after_index: int,
    source_in: float = 0.5,
) -> TimelineItem:
    duration = 5.0
    path = str(Path(f"/media/{folder.replace(' ', '_')}_outro.mp4"))
    end_voice = float(after_index * 3 + 3)
    return TimelineItem(
        timeline_item_id=f"outro_{folder.replace(' ', '_')}",
        type="generic_outro_visual",
        section_id=_section_id(folder),
        folder_name=folder,
        voice_file=voice_file,
        asset_id="asset_outro",
        shot_id="outro_001",
        resolved_media_path=path,
        original_asset_path=path,
        asset_role="generic_section_outro",
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_in + duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=source_in,
        source_out_sec=source_in + duration,
        voice_start_sec=end_voice,
        voice_end_sec=end_voice,
        selection_reason="Neutraler Shot aus derselben Sektion",
        confidence=0.8,
        transform=TimelineItemTransform(),
        motif="Ausklingen",
    )


def _shots_from_items(items: list[TimelineItem]) -> list[EditPlanShot]:
    from otio_app.services.timeline_plan_builder import shots_from_timeline_items

    return shots_from_timeline_items(items)


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

    plan_settings = EditPlanSettings(
        audio_offset_sec=1.0,
        section_outro_sec=5.0,
        video_head_trim_sec=0.5,
        video_head_trim_policy="fixed_trim",
        voiceover_trim_policy="disabled",
    )
    florida_voice_end = 6.0
    florida_items = [
        _timeline_narration("Florida Keys", voice_a, 1, timeline_in=0.0),
        _timeline_narration("Florida Keys", voice_a, 2, timeline_in=3.0),
        _timeline_outro(
            "Florida Keys",
            voice_a,
            timeline_in=florida_voice_end,
            after_index=2,
        ),
    ]
    canyon_voice_end = 4.0
    canyon_items = [
        _timeline_narration("Grand Canyon", voice_b, 1, timeline_in=0.0),
        _timeline_filler("Grand Canyon", voice_b, timeline_in=3.0, duration=1.0),
        _timeline_outro(
            "Grand Canyon",
            voice_b,
            timeline_in=canyon_voice_end,
            after_index=1,
        ),
    ]
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            settings=plan_settings,
            voiceover=_voiceover_plan(voice_a, duration=5.0),
            shots=_shots_from_items(florida_items),
            timeline_items=florida_items,
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
            voiceover=_voiceover_plan(voice_b, duration=3.0),
            shots=_shots_from_items(canyon_items),
            timeline_items=canyon_items,
        ),
        "Grand Canyon",
    )


def test_merge_does_not_false_block_second_section_global_coords(tmp_path: Path) -> None:
    """Outro-Position global darf nicht mit lokalem voiceover.timeline_end verglichen werden."""
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)

    merged = merge_confirmed_edit_plans(project)
    assert merged.validation_status == "OK"
    assert merged.ready is True
    assert not any(
        "generic_outro startet bei" in warning and "Grand Canyon" in warning
        for warning in merged.warnings
    )


def test_plan_section_items_accepts_empty_voice_file_from_cut_plan_staging(tmp_path: Path) -> None:
    """Cut-Plan-Promote schreibt VisualItems oft ohne voice_file — Merge darf
    sie nicht verwerfen, sonst: „kein timeline_items im Schnittplan“."""
    voice = "/audio/arches_v003.mp3"
    items = [
        TimelineItem(
            timeline_item_id="edit_seg_1",
            type="video_shot",
            section_id="cut_001",
            folder_name="Arches National Park",
            voice_file="",
            resolved_media_path="/media/clip.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=3.0,
            duration_sec=3.0,
            final_duration_sec=3.0,
            track="V1",
        )
    ]
    plan = EditPlanDocument(
        project_id="p",
        folder_name="Arches National Park",
        confirmed=True,
        voiceover=VoiceoverPlan(path=voice, duration_sec=3.0, timeline_end_sec=3.0),
        timeline_items=items,
        shots=[],
    )
    matched = _plan_section_items(plan, "Arches National Park", voice)
    assert len(matched) == 1
    assert matched[0].voice_file == voice


def test_merge_cut_plan_promoted_plans_with_empty_item_voice_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = project.work_dir_path
    work.mkdir(parents=True, exist_ok=True)
    voice_a = str(tmp_path / "florida_v003.mp3")
    Path(voice_a).write_bytes(b"x")
    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[VoiceFolderMappingEntry(voice_file=voice_a, folder="Florida Keys", confirmed=True)],
    )
    project.voice_folder_mapping_path.parent.mkdir(parents=True, exist_ok=True)
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    items = [
        TimelineItem(
            timeline_item_id="edit_seg_1",
            type="video_shot",
            section_id="cut_001",
            folder_name="Florida Keys",
            voice_file="",
            resolved_media_path=str(tmp_path / "clip.mp4"),
            timeline_in_sec=0.0,
            timeline_out_sec=4.0,
            duration_sec=4.0,
            final_duration_sec=4.0,
            track="V1",
            asset_type="video",
        )
    ]
    (tmp_path / "clip.mp4").write_bytes(b"x")
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            settings=EditPlanSettings(audio_offset_sec=0.0, section_outro_sec=0.0),
            voiceover=VoiceoverPlan(path=voice_a, duration_sec=4.0, timeline_end_sec=4.0),
            shots=[],
            timeline_items=items,
        ),
        "Florida Keys",
    )

    with patch("otio_app.services.otio_exporter.validate_timeline_items") as mock_validate:
        from otio_app.services.edit_plan_validator import TimelineValidationResult, ValidationStatus

        mock_validate.return_value = TimelineValidationResult(
            status=ValidationStatus.OK, errors=[], warnings=[]
        )
        merged = merge_confirmed_edit_plans(project, folder_names=["Florida Keys"])

    assert not any("kein timeline_items" in warning for warning in merged.warnings)
    assert len(merged.timeline_items) == 1
    assert merged.timeline_items[0].voice_file == voice_a
    assert merged.ready is True


def test_export_ignores_stale_global_export_settings_override(tmp_path: Path) -> None:
    """Regression: otio_export_settings.json wurde bisher benutzt, um
    section_outro_sec/audio_offset_sec beim Export GLOBAL zu überschreiben —
    unabhängig davon, mit welchen Werten der jeweilige Schnittplan tatsächlich
    gebaut/bestätigt wurde. Wenn sich die globale Timing-Konfiguration NACH
    dem Bestätigen änderte (z. B. andere Regeln für einen anderen Ort),
    entstanden Geisterfehler wie 'section_outro_sec (8.5s) nicht vollständig
    als Outro-Elemente geplant (4.0s)' oder 'Visuelles Loch', obwohl der
    Schnittplan selbst vollkommen konsistent war. Jetzt zählt ausschließlich
    das, was im jeweiligen Schnittplan (plan.settings / plan.voiceover) fest
    eingebaut ist."""
    from otio_app.services.otio_export_settings import OtioExportSettings, save_otio_export_settings

    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)  # baked-in: audio_offset=1.0, section_outro=5.0

    # Simuliert eine geänderte globale Timing-Konfiguration NACH dem Bestätigen
    # (z. B. weil der Nutzer für einen anderen Ort andere Werte eingestellt hat).
    save_otio_export_settings(
        project,
        OtioExportSettings(audio_offset_sec=2.0, section_outro_sec=8.5),
    )

    merged = merge_confirmed_edit_plans(project)
    assert merged.validation_status == "OK", merged.warnings
    assert merged.ready is True

    timeline = build_otio_timeline(
        project,
        merged,
        export_settings=OtioExportSettings(audio_offset_sec=2.0, section_outro_sec=8.5),
    )
    florida_audio = timeline.tracks[1]
    # Audio-Gap muss dem im Schnittplan verankerten Offset (1.0s) folgen,
    # NICHT der stale globalen Konfiguration (2.0s).
    assert florida_audio[0].source_range.duration.to_seconds() == 1.0


def test_merge_confirmed_edit_plans_in_mapping_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)

    merged = merge_confirmed_edit_plans(project)
    assert merged.ready is True
    assert merged.included_folders == ["Florida Keys", "Grand Canyon"]
    assert len(merged.timeline_items) == 6


def test_timeline_sections_include_outro_and_per_section_voice_offset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)
    settings = EditPlanSettings(audio_offset_sec=1.0, section_outro_sec=5.0)

    sections = _compute_timeline_sections(merged.timeline_items, settings, merged.voiceovers)
    assert len(sections) == 2
    assert sections[0].video_start_sec == 0.0
    assert sections[0].video_duration_sec == 11.0
    assert sections[0].voiceover.timeline_start_sec == 1.0
    assert sections[0].voiceover.duration_sec == 5.0
    assert sections[1].video_start_sec == 11.0
    assert sections[1].video_duration_sec == 9.0
    assert sections[1].voiceover.timeline_start_sec == 1.0
    assert sections[1].voiceover.duration_sec == 3.0


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
    assert clips[1].source_range.duration.to_seconds() == 3.0
    assert clips[2].name == "Florida_Keys_outro.mp4"
    assert clips[2].source_range.duration.to_seconds() == 5.0
    assert clips[3].source_range.duration.to_seconds() == 3.0
    assert clips[4].source_range.duration.to_seconds() == 1.0
    assert clips[5].name == "Grand_Canyon_outro.mp4"
    assert clips[5].source_range.duration.to_seconds() == 5.0
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
    assert florida_audio[1].source_range.duration.to_seconds() == 5.0
    assert florida_audio[1].source_range.start_time.to_seconds() == 0.0

    canyon_audio = timeline.tracks[2]
    assert canyon_audio[0].source_range.duration.to_seconds() == 12.0
    assert canyon_audio[1].source_range.duration.to_seconds() == 3.0
    assert canyon_audio[1].source_range.start_time.to_seconds() == 0.0


def test_video_section_keeps_planned_durations(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)
    timeline = build_otio_timeline(
        project,
        merged,
        export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
    )
    video_track = timeline.tracks[0]
    gaps = [item for item in video_track if isinstance(item, otio.schema.Gap)]
    assert not gaps
    clips = [item for item in video_track if isinstance(item, otio.schema.Clip)]
    assert clips[1].source_range.duration.to_seconds() == 3.0
    assert clips[2].name == "Florida_Keys_outro.mp4"
    assert clips[2].source_range.duration.to_seconds() == 5.0
    total_video = sum(item.source_range.duration.to_seconds() for item in video_track)
    assert total_video == 20.0


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


def test_timeline_item_clip_source_range_includes_embedded_timecode(tmp_path: Path) -> None:
    """Regression: _append_timeline_item_clip() (der moderne TimelineItem-
    basierte Export-Pfad) hat item.source_in_sec/source_out_sec bisher 1:1
    aus dem Schnittplan übernommen — diese wurden beim Schnittplan-Bau IMMER
    relativ zu einem bei Null beginnenden Timecode berechnet. Für Dateien
    mit einem von Null abweichenden eingebetteten SMPTE-Timecode (typisch
    bei professionellem Kamera-Footage) entstand dadurch ein Mismatch
    zwischen source_range und available_range: DaVinci Resolve meldete beim
    Import/Reconnect 'No overlap between specified target timecodes and
    located file timecodes', obwohl die richtige Datei referenziert wurde.
    source_range muss denselben eingebetteten Timecode-Offset wie
    available_range enthalten."""
    from otio_app.services.edit_plan_rules import ExportRuleOptions
    from otio_app.services.otio_exporter import _append_timeline_item_clip

    project = _project(tmp_path)
    media = project.project_root_path / "Grand Canyon" / "Asset16.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"x")

    item = TimelineItem(
        timeline_item_id="item_016",
        type="video_shot",
        section_id="section_grand_canyon",
        folder_name="Grand Canyon",
        voice_file=str(tmp_path / "voice.wav"),
        resolved_media_path=str(media),
        original_asset_path=str(media),
        duration_sec=5.0,
        final_duration_sec=5.0,
        source_in_sec=2.0,
        source_out_sec=0.0,
        transform=TimelineItemTransform(),
    )

    embedded_tc_sec = 2 * 3600 + 23 * 60 + 45.0  # ~02:23:45, wie im Bugreport
    timing = MediaTiming(start_sec=embedded_tc_sec, duration_sec=600.0, rate=29.97)

    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        _append_timeline_item_clip(
            track,
            item,
            project=project,
            index=1,
            rate=29.97,
            export_rules=ExportRuleOptions(),
            auto_zoom_fill=False,
        )

    clip = track[0]
    assert clip.source_range.start_time.to_seconds() == pytest.approx(embedded_tc_sec + 2.0, abs=0.01)

    available_range = clip.media_reference.available_range
    avail_start = available_range.start_time.to_seconds()
    avail_end = avail_start + available_range.duration.to_seconds()
    src_start = clip.source_range.start_time.to_seconds()
    src_end = src_start + clip.source_range.duration.to_seconds()
    assert avail_start <= src_start + 0.01, "source_range beginnt vor available_range"
    assert src_end <= avail_end + 0.01, "source_range endet nach available_range"


def test_timeline_item_clip_source_range_clamped_to_available_media_duration(
    tmp_path: Path,
) -> None:
    """Regression: Wenn die geplante Shot-Dauer länger ist als die
    tatsächlich verfügbare Restlänge der Quelldatei (z. B. ein kurzer
    Clip), muss source_range auf available_range gekürzt werden — sonst
    meldet Resolve ebenfalls einen Timecode-/Media-Offline-Mismatch. Der
    ältere, Shot-basierte Export-Pfad (_clip_source_range_for_media) hatte
    diese Begrenzung bereits; im moderneren TimelineItem-Pfad
    (_append_timeline_item_clip) fehlte sie."""
    from otio_app.services.edit_plan_rules import ExportRuleOptions
    from otio_app.services.otio_exporter import _append_timeline_item_clip

    project = _project(tmp_path)
    media = project.project_root_path / "Grand Canyon" / "Asset06.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"x")

    item = TimelineItem(
        timeline_item_id="item_006",
        type="video_shot",
        section_id="section_grand_canyon",
        folder_name="Grand Canyon",
        voice_file=str(tmp_path / "voice.wav"),
        resolved_media_path=str(media),
        original_asset_path=str(media),
        duration_sec=7.0,
        final_duration_sec=7.0,
        source_in_sec=2.0,
        source_out_sec=9.0,
        transform=TimelineItemTransform(),
    )

    # Datei ist nur 8.18s lang (kürzer als die angeforderten 7s ab Sekunde 2 = bis 9s).
    timing = MediaTiming(start_sec=0.0, duration_sec=8.18, rate=29.97)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    notes: list[str] = []
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        _append_timeline_item_clip(
            track,
            item,
            project=project,
            index=1,
            rate=29.97,
            export_rules=ExportRuleOptions(),
            auto_zoom_fill=False,
            timing_notes=notes,
        )

    clip = track[0]
    available_range = clip.media_reference.available_range
    avail_end = available_range.start_time.to_seconds() + available_range.duration.to_seconds()
    src_start = clip.source_range.start_time.to_seconds()
    src_end = src_start + clip.source_range.duration.to_seconds()
    assert src_end <= avail_end + 0.01, "source_range endet nach available_range"
    assert any("gekürzt" in note for note in notes)


def test_timeline_item_clip_source_range_unaffected_when_no_embedded_timecode(
    tmp_path: Path,
) -> None:
    """Ohne eingebetteten Timecode (start_sec=0.0, der Normalfall) darf sich
    das Verhalten nicht ändern."""
    from otio_app.services.edit_plan_rules import ExportRuleOptions
    from otio_app.services.otio_exporter import _append_timeline_item_clip

    project = _project(tmp_path)
    media = project.project_root_path / "Grand Canyon" / "Asset01.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"x")

    item = TimelineItem(
        timeline_item_id="item_001",
        type="video_shot",
        section_id="section_grand_canyon",
        folder_name="Grand Canyon",
        voice_file=str(tmp_path / "voice.wav"),
        resolved_media_path=str(media),
        original_asset_path=str(media),
        duration_sec=5.0,
        final_duration_sec=5.0,
        source_in_sec=2.0,
        source_out_sec=7.0,
        transform=TimelineItemTransform(),
    )

    timing = MediaTiming(start_sec=0.0, duration_sec=600.0, rate=29.97)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
            _append_timeline_item_clip(
                track, item, project=project, index=1, rate=29.97, export_rules=ExportRuleOptions(), auto_zoom_fill=False
            )

    clip = track[0]
    assert clip.source_range.start_time.to_seconds() == pytest.approx(2.0, abs=0.01)
    assert clip.source_range.duration.to_seconds() == pytest.approx(5.0, abs=0.01)


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


def test_export_otio_timeline_writes_custom_output_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)
    custom_path = project.work_dir_path / "exports" / "Arches_National_Park.otio"

    with patch(
        "otio_app.services.otio_exporter.verify_timeline_media_paths",
        return_value=[],
    ):
        export_result = export_otio_timeline(
            project,
            merged,
            export_settings=OtioExportSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
            output_path=custom_path,
        )
    assert export_result.path == custom_path
    assert custom_path.is_file()


def test_export_otio_timeline_writes_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    with patch(
        "otio_app.services.otio_exporter.verify_timeline_media_paths",
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


def test_merge_warns_on_duplicate_asset_but_allows_export(tmp_path: Path) -> None:
    """Globale Asset-Doppelung wird gemeldet, blockiert den Export aber nicht mehr."""
    project = _project(tmp_path)
    voice_a = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Florida Keys_VO.wav")
    voice_b = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Grand Canyon_VO.wav")
    Path(voice_a).parent.mkdir(parents=True, exist_ok=True)
    Path(voice_a).write_bytes(b"wav")
    Path(voice_b).write_bytes(b"wav")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_a, folder="Florida Keys", confirmed=True),
            VoiceFolderMappingEntry(voice_file=voice_b, folder="Grand Canyon", confirmed=True),
        ],
    )
    project.voice_folder_mapping_path.write_text(
        mapping.model_dump_json(indent=2),
        encoding="utf-8",
    )

    shared_asset = "shared_asset.mp4"
    plan_settings = EditPlanSettings(
        audio_offset_sec=1.0,
        section_outro_sec=5.0,
        video_head_trim_sec=0.5,
        video_head_trim_policy="fixed_trim",
        voiceover_trim_policy="disabled",
    )

    florida_voice_end = 6.0
    florida_items = [
        _timeline_narration("Florida Keys", voice_a, 1, timeline_in=0.0),
        _timeline_narration("Florida Keys", voice_a, 2, timeline_in=3.0),
        _timeline_outro(
            "Florida Keys",
            voice_a,
            timeline_in=florida_voice_end,
            after_index=2,
        ),
    ]
    canyon_voice_end = 4.0
    canyon_items = [
        _timeline_narration("Grand Canyon", voice_b, 1, timeline_in=0.0),
        _timeline_filler("Grand Canyon", voice_b, timeline_in=3.0, duration=1.0),
        _timeline_outro(
            "Grand Canyon",
            voice_b,
            timeline_in=canyon_voice_end,
            after_index=1,
        ),
    ]
    for item in florida_items + canyon_items:
        if item.type == "video_shot":
            item.asset_id = shared_asset

    for folder_name, items, voice_file, duration in (
        ("Florida Keys", florida_items, voice_a, 5.0),
        ("Grand Canyon", canyon_items, voice_b, 3.0),
    ):
        save_edit_plan(
            project,
            EditPlanDocument(
                project_id=project.id,
                folder_name=folder_name,
                confirmed=True,
                candidate_status="ACCEPTED",
                validation_status="PASS",
                settings=plan_settings,
                voiceover=_voiceover_plan(voice_file, duration=duration),
                shots=_shots_from_items(items),
                timeline_items=items,
            ),
            folder_name,
        )

    save_edit_plan_rules(
        project,
        EditPlanRulesDocument(
            project_id=project.id,
            rules=[
                EditPlanRule(
                    id="max",
                    rule_type=RULE_MAX_ASSET_USES,
                    enabled=True,
                    params={"max_count": 1, "min_gap": 0},
                )
            ],
        ),
    )

    merged = merge_confirmed_edit_plans(project)
    assert merged.validation_status == "OK"
    assert merged.ready is True
    assert any(
        warning.startswith("Regel-Hinweis (Export trotzdem möglich):")
        for warning in merged.warnings
    )


def test_merge_skips_blocked_candidate_plan(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)

    blocked_plan = EditPlanDocument(
        project_id=project.id,
        folder_name="Grand Canyon",
        confirmed=True,
        candidate_status="BLOCKED",
        validation_status="FAIL",
        settings=EditPlanSettings(),
        timeline_items=[],
    )
    save_edit_plan(project, blocked_plan, "Grand Canyon")

    merged = merge_confirmed_edit_plans(project)
    assert merged.validation_status == "BLOCKED"
    assert "Grand Canyon" in merged.skipped_folders
