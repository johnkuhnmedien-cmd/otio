"""Tests für OTIO-Zoom/Trim/Titel-Hilfen."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.otio_media_transform import (
    build_export_video_filter,
    build_resolve_zoom_effect,
    compute_fill_zoom_factor,
    escape_drawtext_value,
    ffmpeg_folder_title_filter,
    ffmpeg_scale_crop_filter,
    ffmpeg_video_filter_for_target_resolution,
    format_folder_display_name,
)


def test_compute_fill_zoom_for_4096x2160_on_16_9_timeline() -> None:
    zoom = compute_fill_zoom_factor(4096, 2160, 3840, 2160)
    assert zoom is not None
    assert abs(zoom - 1.0667) < 0.01


def test_compute_fill_zoom_returns_none_for_exact_16_9() -> None:
    assert compute_fill_zoom_factor(1920, 1080, 3840, 2160) is None


def test_ffmpeg_scale_crop_filter_for_non_16_9() -> None:
    vf = ffmpeg_scale_crop_filter(4096, 2160, 3840, 2160)
    assert vf is not None
    assert "scale=" in vf
    assert "crop=" in vf


def test_build_resolve_zoom_effect() -> None:
    effect = build_resolve_zoom_effect(1.07)
    assert isinstance(effect, otio.schema.Effect)
    assert effect.metadata["otio_app"]["zoom_factor"] == 1.07


def test_format_folder_display_name_replaces_underscores() -> None:
    assert format_folder_display_name("Arches_National_Park") == "Arches National Park"


def test_escape_drawtext_value() -> None:
    assert escape_drawtext_value("A:B") == "A\\:B"


def test_build_export_video_filter_zoom_only() -> None:
    project = Project(
        id="t",
        name="USA",
        project_root="/tmp",
        work_dir="/tmp/_otio",
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
        width=3840,
        height=2160,
    )
    vf, expected_w, expected_h, error = build_export_video_filter(
        source_width=4096,
        source_height=2160,
        project=project,
        auto_zoom_fill=True,
    )
    assert error is None
    assert vf is not None
    assert "drawtext=" not in vf
    assert "scale=3840:2160" in vf
    assert expected_w == 3840
    assert expected_h == 2160


def test_build_export_video_filter_off_when_auto_zoom_disabled() -> None:
    project = Project(
        id="t",
        name="USA",
        project_root="/tmp",
        work_dir="/tmp/_otio",
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
        width=3840,
        height=2160,
    )
    vf, expected_w, expected_h, error = build_export_video_filter(
        source_width=3840,
        source_height=2160,
        project=project,
        auto_zoom_fill=False,
    )
    assert error is None
    assert vf is None
    assert expected_w is None
    assert expected_h is None


def test_build_export_video_filter_scales_same_aspect_different_resolution() -> None:
    project = Project(
        id="t",
        name="USA",
        project_root="/tmp",
        work_dir="/tmp/_otio",
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
        width=3840,
        height=2160,
    )
    vf, expected_w, expected_h, error = build_export_video_filter(
        source_width=1920,
        source_height=1080,
        project=project,
        auto_zoom_fill=True,
    )
    assert error is None
    assert vf == "scale=3840:2160"
    assert expected_w == 3840
    assert expected_h == 2160


def test_ffmpeg_video_filter_for_target_resolution_uses_fill_for_dci() -> None:
    vf = ffmpeg_video_filter_for_target_resolution(4096, 2160, 3840, 2160)
    assert vf is not None
    assert "crop=3840:2160" in vf


def test_ffmpeg_folder_title_filter_includes_duration_and_position() -> None:
    vf = ffmpeg_folder_title_filter(
        text="Florida Keys",
        font_path=Path("/fonts/Phosphate.ttf"),
        duration_sec=5.0,
        target_width=3840,
        target_height=2160,
    )
    assert "drawtext=" in vf
    assert "Florida Keys" in vf
    assert "shadowcolor=black" in vf
    assert "lte(t\\,5.000)" in vf
    assert "y=h-th-" in vf
