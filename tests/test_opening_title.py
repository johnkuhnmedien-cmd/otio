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
from otio_app.services.edit_plan_rules import ExportRuleOptions
from otio_app.services.media_utils import MediaTiming
from otio_app.services.opening_title_renderer import (
    build_opening_title_item,
    ensure_opening_titles_rendered,
)
from otio_app.services.otio_exporter import build_otio_timeline, validate_otio_readback
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


def test_opening_title_created_with_title_style(tmp_path: Path) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
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
    (tmp_path / "v.wav").write_bytes(b"w")
    with patch("otio_app.services.timeline_plan_builder.probe_duration_seconds", return_value=10.0), patch(
        "otio_app.services.title_style.resolve_font_with_fallback",
        return_value=(font_file, "Phosphate", False),
    ):
        items, _, _ = build_timeline_items_for_folder(
            shots,
            folder_name="Antelope Canyon",
            voice_file=str(tmp_path / "v.wav"),
            settings=EditPlanSettings(audio_offset_sec=1.0, section_outro_sec=5.0),
            folder_assets=[{"path": str(tmp_path / "a.mp4"), "description": "a"}],
            trim_leading_sec=0.5,
            opening_title_enabled=True,
            opening_title_font="Phosphate",
            opening_title_duration_sec=5.0,
            opening_title_font_size=48.0,
            work_dir=project.work_dir_path,
            project=project,
        )
    titles = [i for i in items if i.type == "opening_title"]
    assert len(titles) == 1
    title = titles[0]
    assert title.title_style is not None
    assert title.title_style.font_size_px == 48.0
    assert title.track == "V2"
    assert "_opening_title_" in title.title_style.output_mov_path


def test_title_on_v2_not_only_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    with patch(
        "otio_app.services.title_style.resolve_font_with_fallback",
        return_value=(font_file, "Phosphate", False),
    ):
        title = build_opening_title_item(
            folder_name="Antelope Canyon",
            voice_file="/v.wav",
            section_id="section_antelope_canyon",
            work_dir=project.work_dir_path,
            project=project,
            font_size_px=50.0,
        )
    fake_media = Path(title.rendered_media_path)
    fake_media.parent.mkdir(parents=True, exist_ok=True)
    fake_media.write_bytes(b"mov")
    title = title.model_copy(
        update={
            "rendered_media_path": str(fake_media),
            "resolved_media_path": str(fake_media),
            "title_style": title.title_style.model_copy(
                update={"output_mov_path": str(fake_media), "render_manifest_path": str(fake_media.with_suffix(".render.json"))}
            )
            if title.title_style
            else None,
        }
    )
    (tmp_path / "a.mp4").write_bytes(b"v")
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
    v2 = timeline.tracks[1]
    assert v2.name == "V2"
    title_clips = [
        c for c in v2 if isinstance(c, otio.schema.Clip) and c.metadata.get("type") == "opening_title"
    ]
    assert len(title_clips) == 1
    assert title_clips[0].metadata["font_size_px"] == 50.0


@patch("otio_app.services.opening_title_renderer.ffmpeg_has_drawtext", return_value=False)
def test_pillow_render_from_plan_style(_no_drawtext: pytest.Mock, tmp_path: Path) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    with patch(
        "otio_app.services.title_style.resolve_font_with_fallback",
        return_value=(font_file, "Phosphate", False),
    ):
        item = build_opening_title_item(
            folder_name="Antelope Canyon",
            voice_file="/v.wav",
            section_id="section_antelope_canyon",
            work_dir=project.work_dir_path,
            project=project,
            font_size_px=44.0,
        )

    with patch(
        "otio_app.services.opening_title_renderer._render_png_pillow",
        return_value=(200, 44),
    ), patch(
        "otio_app.services.opening_title_renderer._encode_png_to_mov",
        return_value=True,
    ):
        rendered_items, notes = ensure_opening_titles_rendered(project, [item], force=True)
    assert rendered_items[0].title_style is not None
    assert rendered_items[0].title_style.font_size_px == 44.0
    assert notes
