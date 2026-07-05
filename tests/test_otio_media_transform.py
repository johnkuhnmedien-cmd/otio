"""Tests für OTIO-Zoom/Trim-Hilfen."""

from __future__ import annotations

import opentimelineio as otio

from otio_app.services.otio_media_transform import (
    build_resolve_zoom_effect,
    compute_fill_zoom_factor,
    ffmpeg_scale_crop_filter,
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
