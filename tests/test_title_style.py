"""Tests für TitleStyle, Render-Hash und Schriftgröße."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import TimelineItem, TimelineItemTransform, TitleStyle
from otio_app.models import Project
from otio_app.services.opening_title_renderer import (
    build_opening_title_item,
    ensure_opening_titles_rendered,
    render_opening_title_from_style,
)
from otio_app.services.title_style import (
    TITLE_FONT_SIZE_NOT_APPLIED,
    attach_output_paths,
    build_title_style_for_plan,
    compute_render_hash,
    extract_title_style,
    measure_text_bbox,
    render_cache_valid,
    validate_font_size_applied,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / "_otio"
    work.mkdir(parents=True)
    return Project(
        id="style-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
        width=1920,
        height=1080,
        fps=25,
    )


def test_build_opening_title_item_stores_font_size_px_in_plan(tmp_path: Path) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    with patch(
        "otio_app.services.title_style.resolve_font_with_fallback",
        return_value=(font_file, "Arial Bold", False),
    ):
        item = build_opening_title_item(
            folder_name="Antelope_Canyon",
            voice_file="/v.wav",
            section_id="section_antelope_canyon",
            work_dir=project.work_dir_path,
            project=project,
            requested_font_family="Arial Bold",
            font_size_px=60.0,
        )
    assert item.title_style is not None
    assert item.title_style.font_size_px == 60.0
    assert item.font_size_px == 60.0
    assert "opening_title_16" in item.title_style.render_hash or item.title_style.render_hash


def test_font_size_change_changes_render_hash(tmp_path: Path) -> None:
    project = _project(tmp_path)
    style60 = build_title_style_for_plan(
        text="Test",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=60.0,
    )
    style90 = style60.model_copy(update={"font_size_px": 90.0})
    assert compute_render_hash(style60) != compute_render_hash(style90)


def test_pillow_truetype_uses_font_size_px(tmp_path: Path) -> None:
    from otio_app.services.opening_title_renderer import _render_png_pillow

    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    style = build_title_style_for_plan(
        text="Hi",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=72.0,
    )
    captured: dict[str, int] = {}

    class FakeFont:
        def getbbox(self, text: str, *args, **kwargs) -> tuple[int, int, int, int]:
            return (0, 0, 50, 72)

        def getmask(self, *args, **kwargs):
            from PIL import Image

            return Image.new("L", (50, 72), 255).im

    with patch("PIL.ImageFont.truetype", side_effect=lambda _p, size: captured.update(size=size) or FakeFont()):
        bbox = _render_png_pillow(style, font_file, tmp_path / "out.png")
    assert captured["size"] == 72
    assert bbox[1] > 0


def test_renderer_passes_plan_font_size_px_to_pillow(tmp_path: Path) -> None:
    project = _project(tmp_path)
    font_file = tmp_path / "font.ttf"
    font_file.write_bytes(b"font")
    style = build_title_style_for_plan(
        text="Antelope Canyon",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=60.0,
    )
    style = attach_output_paths(style, work_dir=project.work_dir_path, section_id="section_test")
    style = style.model_copy(update={"resolved_font_file_path": str(font_file)})

    item = TimelineItem(
        timeline_item_id="title_x",
        type="opening_title",
        section_id="section_test",
        folder_name="Antelope Canyon",
        title_style=style,
        transform=TimelineItemTransform(),
    )

    with patch(
        "otio_app.services.opening_title_renderer._render_png_pillow",
        return_value=(120, 60),
    ) as mock_png, patch(
        "otio_app.services.opening_title_renderer.ffmpeg_has_drawtext",
        return_value=False,
    ), patch(
        "otio_app.services.opening_title_renderer._encode_png_to_mov",
        return_value=True,
    ), patch(
        "otio_app.services.opening_title_renderer.path_is_readable_file",
        return_value=True,
    ):
        render_opening_title_from_style(item, style)
    passed_style = mock_png.call_args[0][0]
    assert passed_style.font_size_px == 60.0


def test_render_cache_not_used_when_font_size_changes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = build_opening_title_item(
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        section_id="section_antelope_canyon",
        work_dir=project.work_dir_path,
        project=project,
        font_size_px=30.0,
    )
    assert item.title_style is not None
    updated = item.model_copy(
        update={
            "title_style": item.title_style.model_copy(update={"font_size_px": 90.0}),
            "font_size_px": 90.0,
        }
    )
    style = extract_title_style(updated, project)
    style = attach_output_paths(style, work_dir=project.work_dir_path, section_id=item.section_id)
    assert not render_cache_valid(style, Path(style.render_manifest_path))


def test_video_shot_has_no_title_style_fields(tmp_path: Path) -> None:
    shot = TimelineItem(
        timeline_item_id="v1",
        type="video_shot",
        section_id="s1",
        folder_name="Folder",
        resolved_media_path="/a.mp4",
        duration_sec=3.0,
        final_duration_sec=3.0,
        transform=TimelineItemTransform(),
    )
    assert shot.title_style is None
    assert shot.font_size_px == 0.0
    assert shot.shadow_enabled is False


def test_jpg_not_used_as_overlay_path(tmp_path: Path) -> None:
    from otio_app.services.otio_exporter import _append_opening_title_clip
    import opentimelineio as otio

    jpg = tmp_path / "title.jpg"
    jpg.write_bytes(b"j")
    item = TimelineItem(
        timeline_item_id="t1",
        type="opening_title",
        section_id="s1",
        folder_name="F",
        text="T",
        rendered_media_path=str(jpg),
        resolved_media_path=str(jpg),
        transform=TimelineItemTransform(),
    )
    track = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
    with pytest.raises(ValueError, match="JPG"):
        _append_opening_title_clip(track, item, rate=25.0)


def test_otio_metadata_contains_font_size_px(tmp_path: Path) -> None:
    from otio_app.services.otio_exporter import _append_opening_title_clip
    import opentimelineio as otio

    mov = tmp_path / "title.mov"
    mov.write_bytes(b"mov")
    style = TitleStyle(
        text="Test",
        font_size_px=60.0,
        shadow_offset_x=3.0,
        shadow_offset_y=4.0,
        render_hash="abc123",
        output_mov_path=str(mov),
        render_manifest_path=str(tmp_path / "m.json"),
    )
    item = TimelineItem(
        timeline_item_id="t1",
        type="opening_title",
        section_id="s1",
        folder_name="F",
        track="V2",
        duration_sec=5.0,
        title_style=style,
        rendered_media_path=str(mov),
        transform=TimelineItemTransform(),
    )
    track = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
    with patch("otio_app.services.otio_exporter.probe_media_timing") as mock_timing:
        from otio_app.services.media_utils import MediaTiming

        mock_timing.return_value = MediaTiming(0.0, 5.0, 25.0)
        _append_opening_title_clip(track, item, rate=25.0)
    clip = track[0]
    assert clip.metadata["font_size_px"] == 60.0
    assert clip.metadata["shadow_offset_x"] == 3.0
    assert clip.metadata["shadow_offset_y"] == 4.0


def test_bbox_validation_detects_missing_font_size_application() -> None:
    ok, err = validate_font_size_applied(font_size_px=60.0, bbox_height=5)
    assert not ok
    assert err == TITLE_FONT_SIZE_NOT_APPLIED


def test_measure_text_bbox_on_png(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    png = tmp_path / "t.png"
    img = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    if font is not None:
        draw.text((10, 150), "Hi", font=font, fill=(255, 255, 255, 255))
    img.save(png)
    w, h = measure_text_bbox(png)
    assert w >= 0
    assert h >= 0


def test_font_size_change_produces_different_output_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    style60 = build_title_style_for_plan(
        text="Test",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=60.0,
    )
    style90 = build_title_style_for_plan(
        text="Test",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=90.0,
    )
    path60 = attach_output_paths(style60, work_dir=project.work_dir_path, section_id="section_test")
    path90 = attach_output_paths(style90, work_dir=project.work_dir_path, section_id="section_test")
    assert path60.output_mov_path != path90.output_mov_path
    assert path60.output_png_path != path90.output_png_path


def test_font_fallback_is_documented(tmp_path: Path) -> None:
    project = _project(tmp_path)
    fallback_font = tmp_path / "fallback.ttf"
    fallback_font.write_bytes(b"font")
    with patch(
        "otio_app.services.title_style.resolve_font_with_fallback",
        return_value=(fallback_font, "Helvetica Neue", True),
    ):
        style = build_title_style_for_plan(
            text="Test",
            project=project,
            requested_font_family="Phosphate",
            duration_sec=5.0,
            font_size_px=60.0,
        )
    assert style.font_fallback_used is True
    assert "Phosphate" in style.font_resolution_warning


def test_renderer_raises_instead_of_load_default(tmp_path: Path) -> None:
    from otio_app.services.opening_title_renderer import _render_png_pillow

    project = _project(tmp_path)
    style = build_title_style_for_plan(
        text="Hi",
        project=project,
        requested_font_family="Arial Bold",
        duration_sec=5.0,
        font_size_px=60.0,
    )
    bad_font = tmp_path / "missing.ttf"
    with patch("PIL.ImageFont.truetype", side_effect=OSError("bad font")), patch(
        "PIL.ImageFont.load_default"
    ) as mock_default:
        with pytest.raises(RuntimeError, match="Schriftdatei nicht lesbar"):
            _render_png_pillow(style, bad_font, tmp_path / "out.png")
    mock_default.assert_not_called()


def test_otio_clip_references_rendered_media_path(tmp_path: Path) -> None:
    from otio_app.services.otio_exporter import _append_opening_title_clip
    import opentimelineio as otio

    mov = tmp_path / "title.mov"
    mov.write_bytes(b"mov")
    style = TitleStyle(
        text="Test",
        font_size_px=60.0,
        output_mov_path=str(mov),
        render_manifest_path=str(tmp_path / "m.json"),
    )
    item = TimelineItem(
        timeline_item_id="t1",
        type="opening_title",
        section_id="s1",
        folder_name="F",
        track="V2",
        duration_sec=5.0,
        title_style=style,
        rendered_media_path=str(mov),
        transform=TimelineItemTransform(),
    )
    track = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
    with patch("otio_app.services.otio_exporter.probe_media_timing") as mock_timing:
        from otio_app.services.media_utils import MediaTiming

        mock_timing.return_value = MediaTiming(0.0, 5.0, 25.0)
        _append_opening_title_clip(track, item, rate=25.0)
    clip = track[0]
    assert clip.metadata["rendered_media_path"] == str(mov)
    assert clip.media_reference.target_url == str(mov)
