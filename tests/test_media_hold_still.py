"""E2E-2.2a: Still-Hold scale/pad mit geraden Maßen."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.media_hold import still_hold_video_filter


def test_still_hold_filter_letterboxes_to_even_project_size() -> None:
    vf = still_hold_video_filter(width=1920, height=1080)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf
    assert "ceil(iw/2)*2" in vf


def test_still_hold_filter_rounds_odd_project_size_even() -> None:
    vf = still_hold_video_filter(width=1921, height=1081)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf


def test_still_hold_filter_fallback_even_scale_without_target() -> None:
    assert still_hold_video_filter() == "scale=ceil(iw/2)*2:ceil(ih/2)*2"
