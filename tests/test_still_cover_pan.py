"""Still Cover-Fill + horizontaler Schwenk (Cut Settings / Hold-Filter)."""

from __future__ import annotations

from pathlib import Path

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
    still_aspect_allows_cover_pan,
    still_hold_cover_pan_filter,
)


def test_pan_defaults_off() -> None:
    opts = default_cut_plan_options()
    assert opts.still_image_pan_mode == STILL_PAN_MODE_OFF
    assert opts.still_image_pan_travel == 0.04
    assert opts.still_image_pan_min_aspect == 1.50
    assert opts.still_image_pan_max_aspect == 2.05
    assert opts.schema_version == "1.8"


def test_normalize_legacy_keeps_pan_off() -> None:
    opts = _normalize_payload({"schema_version": "1.5", "shot_min_sec": 4.0})
    assert opts.still_image_pan_mode == STILL_PAN_MODE_OFF
    assert 0.02 <= opts.still_image_pan_travel <= 0.30
    assert opts.still_image_pan_travel == 0.04
    assert opts.schema_version == "1.8"


def test_normalize_migrates_legacy_pan_travel() -> None:
    opts = _normalize_payload(
        {
            "schema_version": "1.7",
            "still_image_pan_mode": "ltr",
            "still_image_pan_travel": 0.12,
        }
    )
    assert opts.still_image_pan_travel == 0.04
    assert opts.schema_version == "1.8"


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
        still_image_pan_min_aspect=1.6,
        still_image_pan_max_aspect=1.9,
    )
    dumped = opts.model_dump()
    assert dumped["still_image_pan_mode"] == "alternate"
    assert dumped["still_image_pan_travel"] == 0.18
    assert dumped["still_image_pan_min_aspect"] == 1.6


def test_square_image_does_not_allow_cover_pan(tmp_path: Path) -> None:
    from PIL import Image

    square = tmp_path / "square.jpg"
    Image.new("RGB", (1000, 1000), (20, 40, 60)).save(square, quality=90)
    assert still_aspect_allows_cover_pan(square) is False

    wide = tmp_path / "wide.jpg"
    Image.new("RGB", (1920, 1080), (20, 40, 60)).save(wide, quality=90)
    assert still_aspect_allows_cover_pan(wide) is True

    three_two = tmp_path / "32.jpg"
    Image.new("RGB", (1500, 1000), (20, 40, 60)).save(three_two, quality=90)
    assert still_aspect_allows_cover_pan(three_two) is True


def test_export_near_16x9_covers_even_when_pan_mode_off(tmp_path: Path) -> None:
    """Regression: Pan-Mode off darf nahe 16:9 nicht mit Paper-Edge belassen."""
    from unittest.mock import patch

    from PIL import Image

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )
    from otio_app.services.without_voiceover_enhanced.models import ResolvedShot
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        _export_styled_still_hold,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = root / "Dublin"
    folder.mkdir()
    photo = folder / "doors.jpg"
    # Fast 16:9 (wie im Nutzerbeispiel) — muss Cover+Pan bekommen.
    Image.new("RGB", (1600, 900), (30, 80, 40)).save(photo, quality=90)

    project = Project(
        id="still-aspect-cover",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
        width=1920,
        height=1080,
        fps=25.0,
    )
    # Pan-Mode default/off + Style an — früher fälschlich Paper-Edge.
    save_cut_plan_options(
        project,
        CutPlanOptions(
            still_image_style_enabled=True,
            still_image_pan_mode=STILL_PAN_MODE_OFF,
            still_image_background_style="paper_edge",
        ),
    )
    shot = ResolvedShot(
        shot_id="Dublin_slot_001",
        asset_id="doors",
        timeline_start_seconds=0.0,
        timeline_end_seconds=8.0,
        source_start_seconds=0.0,
        source_end_seconds=8.0,
        folder_name="Dublin",
        resolved_media_path=str(photo),
        resolved_media_kind="image",
        hold_mode="still_hold",
    )

    seen: dict[str, object] = {}
    out_hold = work / "hold_cache" / "still_hold_cover.mp4"

    def _fake_hold(project, image_path, **kwargs):
        del project
        seen["image_path"] = Path(image_path)
        seen["pan_direction"] = kwargs.get("pan_direction")
        seen["pan_travel"] = kwargs.get("pan_travel")
        out_hold.parent.mkdir(parents=True, exist_ok=True)
        out_hold.write_bytes(b"cover-hold")
        return out_hold

    with patch(
        "otio_app.services.without_voiceover_enhanced.otio_export_service.ensure_still_hold_video",
        _fake_hold,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.otio_export_service.ensure_styled_still_for_export",
        side_effect=lambda *a, **k: photo if not k.get("enabled") else photo,
    ) as styled:
        out = _export_styled_still_hold(
            project,
            shot,
            image_path=photo,
            fps=25.0,
            label="test",
        )

    assert out == out_hold.resolve()
    assert seen["image_path"] == photo.resolve() or seen["image_path"] == photo
    assert seen["pan_direction"] == STILL_PAN_MODE_LTR
    assert float(seen["pan_travel"] or 0) >= 0.02
    # Style darf für Cover-Stills nicht aktiv sein.
    assert styled.call_args.kwargs.get("enabled") is False


def test_export_square_still_uses_paper_edge_fallback(tmp_path: Path) -> None:
    from unittest.mock import patch

    from PIL import Image

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )
    from otio_app.services.without_voiceover_enhanced.models import ResolvedShot
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        _export_styled_still_hold,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = root / "Dublin"
    folder.mkdir()
    photo = folder / "square.jpg"
    Image.new("RGB", (1000, 1000), (200, 40, 40)).save(photo, quality=90)

    project = Project(
        id="still-aspect-square",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
        width=1920,
        height=1080,
        fps=25.0,
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            still_image_style_enabled=False,
            still_image_pan_mode=STILL_PAN_MODE_OFF,
            still_image_background_style="none",
        ),
    )
    shot = ResolvedShot(
        shot_id="Dublin_slot_002",
        asset_id="sq",
        timeline_start_seconds=0.0,
        timeline_end_seconds=5.0,
        source_start_seconds=0.0,
        source_end_seconds=5.0,
        folder_name="Dublin",
        resolved_media_path=str(photo),
        resolved_media_kind="image",
        hold_mode="still_hold",
    )

    seen: dict[str, object] = {}
    out_hold = work / "hold_cache" / "still_hold_square.mp4"
    styled_out = work / "styled_stills" / "square_styled.jpg"
    styled_out.parent.mkdir(parents=True)
    styled_out.write_bytes(b"fake-jpg")

    def _fake_style(*_a, **kwargs):
        seen["style_enabled"] = kwargs.get("enabled")
        seen["style_zoom"] = kwargs.get("zoom")
        seen["background_style"] = kwargs.get("background_style")
        return styled_out

    def _fake_hold(project, image_path, **kwargs):
        del project
        seen["hold_input"] = Path(image_path)
        seen["pan_direction"] = kwargs.get("pan_direction")
        out_hold.parent.mkdir(parents=True, exist_ok=True)
        out_hold.write_bytes(b"square-hold")
        return out_hold

    with patch(
        "otio_app.services.without_voiceover_enhanced.otio_export_service.ensure_styled_still_for_export",
        _fake_style,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.otio_export_service.ensure_still_hold_video",
        _fake_hold,
    ):
        out = _export_styled_still_hold(
            project,
            shot,
            image_path=photo,
            fps=25.0,
            label="test",
        )

    assert out == out_hold.resolve()
    assert seen["style_enabled"] is True
    assert float(seen["style_zoom"] or 0) == 0.8
    assert seen["background_style"] == "paper_edge"
    assert seen["pan_direction"] is None
    assert seen["hold_input"] == styled_out
