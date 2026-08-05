"""Still Cover-Fill + horizontaler Schwenk (Cut Settings / Hold-Filter)."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    STILL_PAN_MODE_ALTERNATE,
    STILL_PAN_MODE_LTR,
    STILL_PAN_MODE_OFF,
    STILL_PAN_MODE_RTL,
    CutPlanOptions,
    _normalize_payload,
    default_cut_plan_options,
    resolve_still_pan_direction,
)
from otio_app.services.without_voiceover_enhanced.media_hold import (
    still_hold_cover_pan_filter,
)


def test_pan_defaults_off() -> None:
    opts = default_cut_plan_options()
    assert opts.still_image_pan_mode == STILL_PAN_MODE_OFF
    assert opts.still_image_pan_travel == 0.12
    assert opts.schema_version == "1.6"


def test_normalize_legacy_keeps_pan_off() -> None:
    opts = _normalize_payload({"schema_version": "1.5", "shot_min_sec": 4.0})
    assert opts.still_image_pan_mode == STILL_PAN_MODE_OFF
    assert 0.05 <= opts.still_image_pan_travel <= 0.30


def test_normalize_clamps_pan_travel() -> None:
    opts = _normalize_payload(
        {
            "still_image_pan_mode": "ltr",
            "still_image_pan_travel": 0.9,
        }
    )
    assert opts.still_image_pan_mode == STILL_PAN_MODE_LTR
    assert opts.still_image_pan_travel == 0.30


def test_resolve_still_pan_direction_modes() -> None:
    assert resolve_still_pan_direction("off") is None
    assert resolve_still_pan_direction("ltr") == STILL_PAN_MODE_LTR
    assert resolve_still_pan_direction("rtl") == STILL_PAN_MODE_RTL
    a = resolve_still_pan_direction(STILL_PAN_MODE_ALTERNATE, shot_id="shot_a")
    b = resolve_still_pan_direction(STILL_PAN_MODE_ALTERNATE, shot_id="shot_b")
    assert a in {STILL_PAN_MODE_LTR, STILL_PAN_MODE_RTL}
    assert b in {STILL_PAN_MODE_LTR, STILL_PAN_MODE_RTL}


def test_cover_pan_filter_fills_and_pans() -> None:
    vf = still_hold_cover_pan_filter(
        duration_seconds=5.0,
        fps=25.0,
        width=1920,
        height=1080,
        direction="ltr",
        pan_travel=0.12,
    )
    assert "force_original_aspect_ratio=increase" in vf
    assert "crop=1920:1080" in vf
    assert "zoompan=" in vf
    assert "1920x1080" in vf
    # z = 1/(1-0.12) ≈ 1.1364
    assert "1.1364" in vf
    assert "(iw-iw/zoom)*on/" in vf

    vf_rtl = still_hold_cover_pan_filter(
        duration_seconds=5.0,
        fps=25.0,
        width=1920,
        height=1080,
        direction="rtl",
        pan_travel=0.12,
    )
    assert "(iw-iw/zoom)*(1-on/" in vf_rtl


def test_cut_plan_options_accepts_pan_fields() -> None:
    opts = CutPlanOptions(
        still_image_pan_mode="alternate",
        still_image_pan_travel=0.18,
    )
    dumped = opts.model_dump()
    assert dumped["still_image_pan_mode"] == "alternate"
    assert dumped["still_image_pan_travel"] == 0.18
