"""Tests für Opening-Title-Workflow (V2, edit_plan, OTIO-Export)."""

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
from otio_app.models import Project
from otio_app.services.edit_plan_rules import ExportRuleOptions, RULE_FOLDER_TITLE
from otio_app.services.media_utils import MediaTiming
from otio_app.services.opening_title_renderer import (
    build_opening_title_item,
    ensure_opening_titles_rendered,
    opening_title_media_path,
    render_opening_title_media,
)
from otio_app.services.otio_exporter import build_otio_timeline, validate_otio_readback
from otio_app.services.otio_media_transform import format_folder_display_name
from otio_app.services.timeline_plan_builder import build_timeline_items_for_folder


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    work = root / "_otio"
    work.mkdir()
    return Project(
        id="title-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
        width=1920,
        height=1080,
        fps=25,
    )


def test_sync_opening_titles_updates_font_from_rules(tmp_path: Path) -> None:
    from otio_app.services.edit_plan_rules import ExportRuleOptions
    from otio_app.services.opening_title_renderer import sync_opening_titles_from_rules

    project = _project(tmp_path)
    existing = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
        requested_font_family="Phosphate",
        font_size=96.0,
    )
    video = TimelineItem(
        timeline_item_id="v1",
        type="video_shot",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        resolved_media_path="/a.mp4",
        duration_sec=5.0,
        final_duration_sec=5.0,
        timeline_in_sec=0.0,
        timeline_out_sec=5.0,
        source_in_sec=0.5,
        source_out_sec=5.5,
        transform=TimelineItemTransform(),
    )
    opts = ExportRuleOptions(
        folder_title_enabled=True,
        folder_title_font="Helvetica Neue",
        folder_title_duration_sec=4.0,
        folder_title_font_size=40.0,
    )
    items, changed = sync_opening_titles_from_rules(
        project,
        [existing, video],
        folder_name="Antelope Canyon",
        export_opts=opts,
    )
    assert changed is True
    title = next(item for item in items if item.type == "opening_title")
    assert title.requested_font_family == "Helvetica Neue"
    assert title.font_size == 40.0
    assert title.duration_sec == 4.0


def test_lower_third_style_defaults(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope_Canyon",
        voice_file="/voice.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
        video_width=1920,
        video_height=1080,
    )
    assert item.position == "lower_third"
    assert item.font_size == 72.0
    assert item.shadow_opacity == 0.5
    assert item.requested_font_family == "Helvetica Neue"


def test_ffmpeg_lower_third_filter_uses_bottom_left(tmp_path: Path) -> None:
    from otio_app.services.opening_title_renderer import _ffmpeg_opening_title_filter

    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/voice.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    vf = _ffmpeg_opening_title_filter(item, tmp_path / "font.ttf", project)
    assert "y=h-th-" in vf
    assert "x=76" in vf
    assert "fontsize=72" in vf


def test_folder_name_becomes_title_text(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope_Canyon",
        voice_file="/voice.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    assert item.text == "Antelope Canyon"


def test_opening_title_created_as_edit_plan_element(tmp_path: Path) -> None:
    project = _project(tmp_path)
    shots = [
        EditPlanShot(
            voice_file=str(tmp_path / "v.wav"),
            folder="Antelope Canyon",
            voice_start_sec=0.0,
            voice_end_sec=5.0,
            duration_sec=5.0,
            asset_path=str(tmp_path / "a.mp4"),
        )
    ]
    (tmp_path / "a.mp4").write_bytes(b"v")
    (tmp_path / "b.mp4").write_bytes(b"v")
    (tmp_path / "v.wav").write_bytes(b"w")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=10.0):
        items, _, _ = build_timeline_items_for_folder(
            shots,
            folder_name="Antelope Canyon",
            voice_file=str(tmp_path / "v.wav"),
            settings=EditPlanSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
            folder_assets=[
                {"path": str(tmp_path / "a.mp4"), "description": "a"},
                {"path": str(tmp_path / "b.mp4"), "description": "ruhige Landschaft wide"},
            ],
            trim_leading_sec=0.5,
            opening_title_enabled=True,
            opening_title_font="Phosphate",
            opening_title_duration_sec=5.0,
            work_dir=project.work_dir_path,
        )
    titles = [i for i in items if i.type == "opening_title"]
    assert len(titles) == 1
    title = titles[0]
    assert title.track == "V2"
    assert title.timeline_in_sec == 0.0
    assert title.timeline_out_sec == 5.0
    assert title.render_required is True
    assert title.rendered_media_path.endswith("_opening_title_v002.mov")


def test_title_on_v2_not_only_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    title = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    fake_media = project.work_dir_path / "generated_titles" / "title.mov"
    fake_media.parent.mkdir(parents=True, exist_ok=True)
    fake_media.write_bytes(b"mov")
    title = title.model_copy(
        update={
            "rendered_media_path": str(fake_media),
            "resolved_media_path": str(fake_media),
        }
    )
    video = TimelineItem(
        timeline_item_id="v1",
        type="video_shot",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        resolved_media_path=str(tmp_path / "a.mp4"),
        duration_sec=8.0,
        final_duration_sec=8.0,
        timeline_in_sec=0.0,
        timeline_out_sec=8.0,
        source_in_sec=0.5,
        source_out_sec=8.5,
        transform=TimelineItemTransform(),
    )
    (tmp_path / "a.mp4").write_bytes(b"v")

    from otio_app.services.otio_exporter import MergedEditPlanResult

    merged = MergedEditPlanResult(
        timeline_items=[title, video],
        shots=[],
        settings=EditPlanSettings(audio_offset_sec=1.0),
        voiceovers=[
            VoiceoverPlan(
                path="/v.wav",
                timeline_start_sec=1.0,
                source_in_sec=0.0,
                source_out_sec=10.0,
                duration_sec=10.0,
                timeline_end_sec=11.0,
                duration_source="ffprobe",
                trim_policy="disabled",
            )
        ],
    )
    timing = MediaTiming(0.0, 5.0, 25.0)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing), patch(
        "otio_app.services.otio_exporter.resolve_effective_media_path",
        side_effect=lambda _p, _f, orig: orig,
    ), patch(
        "otio_app.services.otio_exporter.ensure_export_media_for_export",
        side_effect=lambda _p, _f, orig, **_: orig,
    ):
        timeline = build_otio_timeline(project, merged)

    assert len(timeline.tracks) >= 2
    v1 = timeline.tracks[0]
    v2 = timeline.tracks[1]
    assert v1.name == "V1"
    assert v2.name == "V2"
    assert "folder_title_overlay" not in timeline.metadata
    title_clips = [
        c for c in v2 if isinstance(c, otio.schema.Clip) and c.metadata.get("type") == "opening_title"
    ]
    assert len(title_clips) == 1
    assert title_clips[0].metadata["text"] == "Antelope Canyon"


def test_voiceover_still_starts_at_audio_offset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    title = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file=str(tmp_path / "v.wav"),
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    fake_media = project.work_dir_path / "generated_titles" / "title.mov"
    fake_media.parent.mkdir(parents=True, exist_ok=True)
    fake_media.write_bytes(b"mov")
    title = title.model_copy(
        update={"rendered_media_path": str(fake_media), "resolved_media_path": str(fake_media)}
    )
    (tmp_path / "v.wav").write_bytes(b"w")
    (tmp_path / "a.mp4").write_bytes(b"v")

    from otio_app.services.otio_exporter import MergedEditPlanResult

    merged = MergedEditPlanResult(
        timeline_items=[
            title,
            TimelineItem(
                timeline_item_id="v1",
                type="video_shot",
                section_id="section_antelope_canyon",
                folder_name="Antelope Canyon",
                voice_file=str(tmp_path / "v.wav"),
                resolved_media_path=str(tmp_path / "a.mp4"),
                duration_sec=8.0,
                final_duration_sec=8.0,
                timeline_in_sec=0.0,
                timeline_out_sec=8.0,
                source_in_sec=0.5,
                source_out_sec=8.5,
                transform=TimelineItemTransform(),
            ),
        ],
        shots=[],
        settings=EditPlanSettings(audio_offset_sec=1.0),
        voiceovers=[
            VoiceoverPlan(
                path=str(tmp_path / "v.wav"),
                timeline_start_sec=1.0,
                source_in_sec=0.0,
                source_out_sec=8.0,
                duration_sec=8.0,
                timeline_end_sec=9.0,
                duration_source="ffprobe",
                trim_policy="disabled",
            )
        ],
    )
    timing = MediaTiming(0.0, 5.0, 25.0)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing), patch(
        "otio_app.services.otio_exporter.resolve_effective_media_path",
        side_effect=lambda _p, _f, orig: orig,
    ), patch(
        "otio_app.services.otio_exporter.ensure_export_media_for_export",
        side_effect=lambda _p, _f, orig, **_: orig,
    ):
        timeline = build_otio_timeline(project, merged)

    audio_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio]
    assert audio_tracks
    assert audio_tracks[0][0].source_range.duration.to_seconds() == 1.0


@patch("otio_app.services.opening_title_renderer.ffmpeg_has_drawtext", return_value=False)
def test_pillow_fallback_when_drawtext_missing(
    _no_drawtext: pytest.Mock,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )

    def _fake_png(_item, _font, _project, output_png: Path) -> None:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        output_png.write_bytes(b"png")

    def _fake_encode(_png: Path, mov: Path, **_: object) -> bool:
        mov.parent.mkdir(parents=True, exist_ok=True)
        mov.write_bytes(b"mov")
        return True

    with patch(
        "otio_app.services.opening_title_renderer.resolve_font_with_fallback",
        return_value=(font_file, "Phosphate", False),
    ), patch(
        "otio_app.services.opening_title_renderer._render_title_png_pillow",
        side_effect=_fake_png,
    ), patch(
        "otio_app.services.opening_title_renderer._encode_png_to_title_mov",
        side_effect=_fake_encode,
    ):
        rendered = render_opening_title_media(project, item)
    assert rendered.suffix.lower() == ".mov"
    assert rendered.is_file()


def test_ensure_opening_titles_rerenders_when_signature_changes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
        requested_font_family="Phosphate",
        font_size=96.0,
    )
    output = Path(item.rendered_media_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"old-mov")
    from otio_app.services.opening_title_renderer import _write_title_render_meta

    _write_title_render_meta(output, item)

    updated_item = item.model_copy(update={"font_size": 40.0, "requested_font_family": "Helvetica Neue"})
    with patch(
        "otio_app.services.opening_title_renderer.render_opening_title_media",
        return_value=output,
    ) as mock_render:
        rendered_items, notes = ensure_opening_titles_rendered(project, [updated_item])
    mock_render.assert_called_once()
    assert notes
    assert rendered_items[0].font_size == 40.0


def test_ensure_opening_titles_skips_valid_cache(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    output = Path(item.rendered_media_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"mov")
    from otio_app.services.opening_title_renderer import _write_title_render_meta

    _write_title_render_meta(output, item)

    with patch("otio_app.services.opening_title_renderer.render_opening_title_media") as mock_render:
        ensure_opening_titles_rendered(project, [item])
    mock_render.assert_not_called()


@patch("otio_app.services.opening_title_renderer.subprocess.run")
def test_rendered_media_path_created(mock_run: pytest.Mock, tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
    )
    output = Path(item.rendered_media_path)

    def _fake_run(cmd, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mov")

        class R:
            returncode = 0
            stderr = ""

        return R()

    mock_run.side_effect = _fake_run
    with patch("otio_app.services.opening_title_renderer.resolve_font_with_fallback") as mock_font:
        mock_font.return_value = (tmp_path / "font.ttf", "Phosphate", False)
        (tmp_path / "font.ttf").write_bytes(b"font")
        rendered = ensure_opening_titles_rendered(project, [item])
    assert rendered[0][0].rendered_media_path
    assert Path(rendered[0][0].rendered_media_path).is_file()


def test_font_fallback_documented(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with patch(
        "otio_app.services.opening_title_renderer.resolve_font_with_fallback",
        return_value=(tmp_path / "fallback.ttf", "Helvetica Neue Condensed Bold", True),
    ):
        (tmp_path / "fallback.ttf").write_bytes(b"f")
        item = build_opening_title_item(
            folder_name="Antelope Canyon",
            voice_file="/v.wav",
            section_id="section_antelope_canyon",
            work_dir=project.work_dir_path,
            requested_font_family="Phosphate",
        )
    assert item.requested_font_family == "Phosphate"
    assert item.resolved_font_family == "Helvetica Neue Condensed Bold"
    assert item.font_fallback_used is True
    assert any("Phosphate" in w for w in item.warnings)


def test_otio_readback_detects_title_on_v2(tmp_path: Path) -> None:
    from otio_app.services.otio_exporter import TimelineSection

    timeline = otio.schema.Timeline(name="t")
    v2 = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
    clip = otio.schema.Clip(name="Title")
    clip.source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, 25),
        duration=otio.opentime.RationalTime(125, 25),
    )
    clip.metadata["type"] = "opening_title"
    v2.append(clip)
    timeline.tracks.append(v2)

    section = TimelineSection(
        voice_file="/v.wav",
        folder="Antelope Canyon",
        video_start_sec=0.0,
        video_duration_sec=20.0,
        voiceover=VoiceoverPlan(
            path="/v.wav",
            timeline_start_sec=1.0,
            source_in_sec=0.0,
            source_out_sec=10.0,
            duration_sec=10.0,
            timeline_end_sec=11.0,
            duration_source="ffprobe",
            trim_policy="disabled",
        ),
    )
    items = [
        TimelineItem(
            timeline_item_id="title_1",
            type="opening_title",
            section_id="section_antelope_canyon",
            folder_name="Antelope Canyon",
            voice_file="/v.wav",
            text="Antelope Canyon",
            track="V2",
            timeline_in_sec=0.0,
            timeline_out_sec=5.0,
            duration_sec=5.0,
            final_duration_sec=5.0,
            rendered_media_path="/title.mov",
            resolved_media_path="/title.mov",
            transform=TimelineItemTransform(),
        )
    ]
    reports = validate_otio_readback(
        timeline,
        sections=[section],
        items=items,
        audio_offset_sec=1.0,
    )
    assert reports[0].opening_title_on_v2 is True
    assert reports[0].opening_title_timeline_start_sec == 0.0
