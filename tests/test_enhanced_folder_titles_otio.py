"""Enhanced OTIO: Ordner-Titel (V2) aus CutPlanOptions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.media_utils import MediaTiming
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    build_enhanced_folder_title_items,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import resolved_timeline_path


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="TitleExport",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite", "Caddo"],
        selected_asset_subdirs=["Yosemite", "Caddo"],
        fps=25.0,
        width=1920,
        height=1080,
    )


def _resolved(*, with_intro: bool = True) -> ResolvedTimelineDocument:
    chapters = []
    shots = []
    audios = []
    if with_intro:
        chapters.append(
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=10.0,
                chapter_video_end=16.5,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
                segment_ids=["Intro_segment_001"],
            )
        )
        shots.append(
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="intro_a",
                timeline_start_seconds=0.0,
                timeline_end_seconds=16.5,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
                chapter_id="Intro",
                resolved_media_path="/tmp/intro.mp4",
                resolved_media_kind="video",
                resolved_media_duration_seconds=20.0,
            )
        )
        audios.append(
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=4.0,
                timeline_end_seconds=10.0,
            )
        )
    chapters.append(
        ResolvedChapterEnvelope(
            chapter_id="Yosemite",
            folder_name="Yosemite",
            chapter_video_start=16.5,
            chapter_audio_start=16.5,
            chapter_audio_end=30.0,
            chapter_video_end=35.0,
            first_shot_id="Yosemite_slot_001",
            last_shot_id="Yosemite_slot_001",
            segment_ids=["Yosemite_segment_001"],
        )
    )
    shots.append(
        ResolvedShot(
            shot_id="Yosemite_slot_001",
            asset_id="yo_01",
            timeline_start_seconds=16.5,
            timeline_end_seconds=35.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Yosemite",
            chapter_id="Yosemite",
            resolved_media_path="/tmp/yo.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=40.0,
        )
    )
    audios.append(
        ResolvedAudioSegment(
            segment_id="Yosemite_segment_001",
            audio_path="/tmp/yo.wav",
            timeline_start_seconds=16.5,
            timeline_end_seconds=30.0,
        )
    )
    return ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=35.0,
        audio_segments=audios,
        shots=shots,
        chapters=chapters,
        voiceover_preroll_sec=1.0,
        voiceover_postroll_sec=5.0,
    )


def test_build_folder_title_items_skips_intro_and_uses_chapter_start(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            folder_title_enabled=True,
            folder_title_font="Phosphate",
            folder_title_duration_sec=5.0,
            folder_title_font_size=120.0,
            folder_title_fade_in_sec=0.5,
            folder_title_fade_out_sec=0.75,
        ),
    )
    items = build_enhanced_folder_title_items(project, _resolved(with_intro=True))
    assert len(items) == 1
    assert items[0].folder_name == "Yosemite"
    assert items[0].timeline_in_sec == 16.5  # Opening-Shot / first_shot
    assert items[0].duration_sec == 5.0
    assert items[0].track == "V2"
    assert items[0].title_style is not None
    assert items[0].title_style.fade_in_sec == 0.5
    assert items[0].title_style.fade_out_sec == 0.75


def test_alpha_fade_filter_and_clamp() -> None:
    from otio_app.analysis_models import TitleStyle
    from otio_app.services.opening_title_renderer import alpha_fade_filter
    from otio_app.services.title_style import clamp_title_fades

    assert clamp_title_fades(5.0, 0.5, 0.5) == (0.5, 0.5)
    fi, fo = clamp_title_fades(1.0, 0.8, 0.8)
    assert abs((fi + fo) - 1.0) < 1e-6

    style = TitleStyle(
        text="Yosemite",
        timeline_width=1920,
        timeline_height=1080,
        duration_sec=5.0,
        fps=25.0,
        fade_in_sec=0.5,
        fade_out_sec=0.75,
    )
    filt = alpha_fade_filter(style)
    assert "fade=t=in:st=0:d=0.500:alpha=1" in filt
    assert "fade=t=out:st=4.250:d=0.750:alpha=1" in filt


def test_build_folder_title_items_disabled(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_options(project, CutPlanOptions(folder_title_enabled=False))
    assert build_enhanced_folder_title_items(project, _resolved()) == []


def test_export_otio_includes_v2_title_track(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            folder_title_enabled=True,
            folder_title_duration_sec=5.0,
            folder_title_font_size=120.0,
        ),
    )
    resolved = _resolved(with_intro=True)
    title_mov = tmp_path / "yosemite_title.mov"
    title_mov.write_bytes(b"fake-prores")

    def _fake_render(project, items, *, force: bool = False):
        del force
        out = []
        for item in items:
            if item.type != "opening_title":
                out.append(item)
                continue
            out.append(
                item.model_copy(
                    update={
                        "rendered_media_path": str(title_mov),
                        "resolved_media_path": str(title_mov),
                        "render_required": False,
                    }
                )
            )
        return out, ["title rendered"]

    def _skip_shot(*_a, **_k):
        raise EnhancedOtioExportError("skip shot")

    def _skip_audio(*_a, **_k):
        raise EnhancedOtioExportError("skip audio")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service.ensure_opening_titles_rendered",
            _fake_render,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service._ensure_shot_media_for_export",
            _skip_shot,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service._assert_local_file",
            _skip_audio,
        ),
        patch(
            "otio_app.services.otio_exporter.probe_media_timing",
            return_value=MediaTiming(duration_sec=5.0, rate=25.0),
        ),
    ):
        path = export_otio_from_resolved_timeline(
            project,
            basename="with_titles",
            allow_errors=True,
            resolved=resolved,
        )

    timeline = otio.adapters.read_from_file(str(path))
    track_names = [t.name for t in timeline.tracks]
    assert "V2" in track_names
    assert track_names.index("V2") == track_names.index("Video") + 1
    assert timeline.metadata.get("opening_title_count") == 1
    v2 = next(t for t in timeline.tracks if t.name == "V2")
    clips = [c for c in v2 if isinstance(c, otio.schema.Clip)]
    assert len(clips) == 1
    assert float(clips[0].metadata.get("timeline_in_sec", -1)) == 16.5
    assert not resolved_timeline_path(project).exists()
