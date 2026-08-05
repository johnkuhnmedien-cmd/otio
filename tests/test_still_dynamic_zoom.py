"""Dynamischer Still-Zoom (Ken Burns) in Cut Settings / Hold-Filter."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    _normalize_payload,
    default_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.media_hold import (
    still_hold_dynamic_zoom_filter,
)


def test_dynamic_zoom_defaults_off() -> None:
    opts = default_cut_plan_options()
    assert opts.still_image_dynamic_zoom_enabled is False
    assert opts.still_image_dynamic_zoom_factor == 1.12


def test_normalize_legacy_payload_keeps_dynamic_zoom_default() -> None:
    opts = _normalize_payload({"schema_version": "1.4", "shot_min_sec": 5.0})
    assert opts.still_image_dynamic_zoom_enabled is False
    assert 1.02 <= opts.still_image_dynamic_zoom_factor <= 1.35


def test_normalize_clamps_dynamic_zoom_factor() -> None:
    opts = _normalize_payload(
        {
            "still_image_dynamic_zoom_enabled": True,
            "still_image_dynamic_zoom_factor": 9.0,
        }
    )
    assert opts.still_image_dynamic_zoom_enabled is True
    assert opts.still_image_dynamic_zoom_factor == 1.35


def test_dynamic_zoom_filter_contains_zoompan() -> None:
    vf = still_hold_dynamic_zoom_filter(
        duration_seconds=4.0,
        fps=25.0,
        zoom_factor=1.12,
        width=1920,
        height=1080,
    )
    assert "zoompan=" in vf
    assert "1920x1080" in vf
    assert "1.1200" in vf or "1.12" in vf


def test_cut_plan_options_model_accepts_dynamic_fields() -> None:
    opts = CutPlanOptions(
        still_image_dynamic_zoom_enabled=True,
        still_image_dynamic_zoom_factor=1.2,
    )
    assert opts.schema_version == "1.7"
    dumped = opts.model_dump()
    assert dumped["still_image_dynamic_zoom_enabled"] is True
    assert dumped["still_image_dynamic_zoom_factor"] == 1.2
