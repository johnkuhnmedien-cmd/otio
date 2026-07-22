"""Tests für Still-Image-Styling (Vintage + Zoom) vor OTIO-Export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from PIL import Image

from otio_app.analysis_models import TimelineItem, TimelineItemTransform
from otio_app.models import Project, ProjectMode
from otio_app.services.edit_plan_rules import ExportRuleOptions
from otio_app.services.media_utils import MediaTiming
from otio_app.services.otio_exporter import _append_timeline_item_clip
from otio_app.services.still_image_export_style import (
    STILL_BACKGROUND_VINTAGE,
    VINTAGE_BACKGROUND_RGB,
    ensure_styled_still_for_export,
    render_styled_still_image,
    still_style_needed,
    styled_still_output_path,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    work = root / "_otio"
    work.mkdir()
    return Project(
        id="proj_still_style",
        name="USA",
        mode=ProjectMode.WITH_VOICEOVER,
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Canyon"],
        selected_asset_subdirs=["Canyon"],
        width=1920,
        height=1080,
        fps=25,
    )


def _write_photo(path: Path, *, size: tuple[int, int] = (800, 600), color=(40, 120, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG", quality=90)
    return path


def test_still_style_needed_defaults() -> None:
    assert still_style_needed(enabled=True, zoom=0.8, background_style="vintage") is True
    assert still_style_needed(enabled=False, zoom=0.8, background_style="vintage") is False
    assert still_style_needed(enabled=True, zoom=1.0, background_style="none") is False
    assert still_style_needed(enabled=True, zoom=0.8, background_style="none") is True


def test_render_styled_still_image_vintage_zoom(tmp_path: Path) -> None:
    source = _write_photo(tmp_path / "photo.jpg", size=(1000, 500), color=(10, 20, 200))
    output = tmp_path / "out.jpg"
    render_styled_still_image(
        source,
        output,
        width=1920,
        height=1080,
        zoom=0.8,
        background_style=STILL_BACKGROUND_VINTAGE,
    )
    assert output.is_file()
    with Image.open(output) as img:
        assert img.size == (1920, 1080)
        # Ecken sollten Vintage-Hintergrund sein (nicht das kräftige Blau des Fotos)
        corner = img.getpixel((10, 10))
        assert abs(corner[0] - VINTAGE_BACKGROUND_RGB[0]) < 80
        assert abs(corner[1] - VINTAGE_BACKGROUND_RGB[1]) < 80


def test_ensure_styled_still_caches(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _write_photo(project.project_root_path / "Canyon" / "shot.jpg")
    notes: list[str] = []
    first = ensure_styled_still_for_export(
        project,
        "Canyon",
        source,
        enabled=True,
        zoom=0.8,
        background_style="vintage",
        notes=notes,
    )
    assert first != source
    assert first.is_file()
    assert any("gerendert" in n for n in notes)

    notes2: list[str] = []
    second = ensure_styled_still_for_export(
        project,
        "Canyon",
        source,
        enabled=True,
        zoom=0.8,
        background_style="vintage",
        notes=notes2,
    )
    assert second == first
    assert any("Cache" in n for n in notes2)


def test_append_timeline_item_applies_still_style(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = _write_photo(project.project_root_path / "Antelope Canyon" / "reused.jpeg")
    item = TimelineItem(
        timeline_item_id="edit_img_001",
        type="image_shot",
        section_id="cut_001",
        folder_name="Antelope Canyon",
        resolved_media_path=str(media),
        original_asset_path=str(media),
        duration_sec=9.5,
        final_duration_sec=9.5,
        source_in_sec=0.0,
        source_out_sec=9.5,
        transform=TimelineItemTransform(),
    )
    timing = MediaTiming(start_sec=0.0, duration_sec=0.0, rate=25.0)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    notes: list[str] = []
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        _append_timeline_item_clip(
            track,
            item,
            project=project,
            index=1,
            rate=25.0,
            export_rules=ExportRuleOptions(),
            auto_zoom_fill=False,
            timing_notes=notes,
            still_image_style_enabled=True,
            still_image_zoom=0.8,
            still_image_background_style="vintage",
        )
    clip = track[0]
    assert clip.source_range.duration.to_seconds() == pytest.approx(9.5, abs=0.01)
    assert clip.metadata.get("still_image_hold") is True
    assert clip.metadata.get("still_image_styled") is True
    assert clip.metadata.get("still_image_zoom") == 0.8
    assert clip.metadata.get("still_image_background_style") == "vintage"
    styled_path = Path(str(clip.metadata["resolved_media_path"]))
    assert styled_path.is_file()
    assert styled_path != media
    expected = styled_still_output_path(
        project.work_dir_path,
        "Antelope Canyon",
        media,
        width=1920,
        height=1080,
        zoom=0.8,
        background_style="vintage",
    )
    assert styled_path.resolve() == expected.resolve()


def test_append_timeline_item_can_disable_still_style(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = _write_photo(project.project_root_path / "Arches" / "photo.jpg")
    item = TimelineItem(
        timeline_item_id="edit_img_002",
        type="image_shot",
        section_id="cut_002",
        folder_name="Arches",
        resolved_media_path=str(media),
        original_asset_path=str(media),
        duration_sec=5.0,
        final_duration_sec=5.0,
        transform=TimelineItemTransform(),
    )
    timing = MediaTiming(start_sec=0.0, duration_sec=0.0, rate=25.0)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    with patch("otio_app.services.otio_exporter.probe_media_timing", return_value=timing):
        _append_timeline_item_clip(
            track,
            item,
            project=project,
            index=1,
            rate=25.0,
            export_rules=ExportRuleOptions(),
            auto_zoom_fill=False,
            still_image_style_enabled=False,
        )
    clip = track[0]
    assert clip.metadata.get("still_image_styled") is None
    assert Path(str(clip.metadata["resolved_media_path"])).resolve() == media.resolve()
