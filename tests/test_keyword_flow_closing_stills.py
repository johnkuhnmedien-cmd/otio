"""Closing darf Standbilder per Hold tragen — keine Source-Dauer-Sperre."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
    KeywordFlowClosingError,
    assess_closing_asset_technical,
    choose_closing_asset_for_resolve,
)
from otio_app.services.without_voiceover_enhanced.models import UnifiedCutPlanDocument
from otio_app.services.without_voiceover_enhanced.timeline_resolver import AssetCatalog


def _jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimales gültiges JPEG (1×1).
    path.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
            "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
            "1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d"
            "0d1832211c2132323232323232323232323232323232323232323232323232323232"
            "323232323232323232323232323232323232323232ffc00011080001000103011100"
            "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
            "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
        )
    )
    return path


def _catalog_with_still(tmp_path: Path, *, asset_id: str = "still_close") -> AssetCatalog:
    media = _jpeg(tmp_path / "cave_walkway.jpeg")
    catalog = AssetCatalog()
    catalog.by_id[asset_id] = {
        "path": str(media),
        "duration_seconds": 0.04,
        "folder": "Marble Arch Caves & Fermanagh Lakelands",
        "media_type": "photo",
        "media_kind": "image",
        "canonical_id": asset_id,
    }
    return catalog


def _catalog_short_video(tmp_path: Path, *, asset_id: str = "short_vid") -> AssetCatalog:
    media = tmp_path / "short.mp4"
    media.write_bytes(b"not-a-real-mp4-but-path-exists")
    catalog = AssetCatalog()
    catalog.by_id[asset_id] = {
        "path": str(media),
        "duration_seconds": 0.04,
        "folder": "Marble Arch Caves & Fermanagh Lakelands",
        "media_type": "video",
        "media_kind": "video",
        "canonical_id": asset_id,
    }
    return catalog


def test_still_closing_passes_despite_short_probe_duration(tmp_path: Path) -> None:
    catalog = _catalog_with_still(tmp_path)
    with patch(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_closing."
        "validate_local_media_path",
        return_value=("export_ready", None),
    ):
        ok, reason, entry = assess_closing_asset_technical(
            catalog,
            "still_close",
            min_duration_seconds=8.8,
            expected_folder="Marble Arch Caves & Fermanagh Lakelands",
        )
    assert ok is True
    assert "still" in reason
    assert entry is not None


def test_still_closing_detected_from_jpeg_suffix_when_media_type_empty(
    tmp_path: Path,
) -> None:
    media = _jpeg(tmp_path / "Asset00015.jpeg")
    catalog = AssetCatalog()
    catalog.by_id["asset15"] = {
        "path": str(media),
        "duration_seconds": 0.04,
        "folder": "Marble Arch Caves & Fermanagh Lakelands",
        "media_type": "",
        "media_kind": "",
        "canonical_id": "asset15",
    }
    with patch(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_closing."
        "validate_local_media_path",
        return_value=("export_ready", None),
    ) as validate_mock:
        ok, reason, _ = assess_closing_asset_technical(
            catalog, "asset15", min_duration_seconds=8.8
        )
    assert ok is True
    assert validate_mock.call_args.kwargs["media_type"] == "photo"
    assert "still" in reason


def test_short_video_closing_still_rejected(tmp_path: Path) -> None:
    catalog = _catalog_short_video(tmp_path)
    with patch(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_closing."
        "validate_local_media_path",
        return_value=("export_ready", None),
    ):
        ok, reason, _ = assess_closing_asset_technical(
            catalog, "short_vid", min_duration_seconds=8.8
        )
    assert ok is False
    assert "Source-Dauer zu kurz" in reason


def test_choose_closing_prefers_still_primary_over_short_video_fallback(
    tmp_path: Path,
) -> None:
    still = _jpeg(tmp_path / "primary.jpeg")
    video = tmp_path / "fallback.mp4"
    video.write_bytes(b"fake")
    catalog = AssetCatalog()
    catalog.by_id["primary_still"] = {
        "path": str(still),
        "duration_seconds": 0.04,
        "folder": "Cave",
        "media_type": "photo",
        "media_kind": "image",
        "canonical_id": "primary_still",
    }
    catalog.by_id["fallback_video"] = {
        "path": str(video),
        "duration_seconds": 0.04,
        "folder": "Cave",
        "media_type": "video",
        "media_kind": "video",
        "canonical_id": "fallback_video",
    }
    plan = UnifiedCutPlanDocument(
        schema_version="x",
        script_version="s",
        closing_fallback_asset_id="fallback_video",
        closing_fallback_asset_fit="strong",
        closing_fallback_asset_fit_reason="other closer",
        closing_fallback_visual_intent="close",
    )
    with patch(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_closing."
        "validate_local_media_path",
        return_value=("export_ready", None),
    ):
        chosen, entry, note = choose_closing_asset_for_resolve(
            primary_id="primary_still",
            fallback_id="fallback_video",
            catalog=catalog,
            min_duration_seconds=8.8,
            expected_folder="Cave",
            plan=plan,
        )
    assert chosen == "primary_still"
    assert note == "primary"
    assert entry["canonical_id"] == "primary_still"


def test_both_short_videos_still_raise(tmp_path: Path) -> None:
    catalog = AssetCatalog()
    for aid in ("p", "f"):
        path = tmp_path / f"{aid}.mp4"
        path.write_bytes(b"fake")
        catalog.by_id[aid] = {
            "path": str(path),
            "duration_seconds": 0.04,
            "folder": "Cave",
            "media_type": "video",
            "media_kind": "video",
            "canonical_id": aid,
        }
    plan = UnifiedCutPlanDocument(
        schema_version="x",
        script_version="s",
        closing_fallback_asset_id="f",
        closing_fallback_asset_fit="strong",
        closing_fallback_asset_fit_reason="reason",
        closing_fallback_visual_intent="intent",
    )
    with patch(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_closing."
        "validate_local_media_path",
        return_value=("export_ready", None),
    ):
        try:
            choose_closing_asset_for_resolve(
                primary_id="p",
                fallback_id="f",
                catalog=catalog,
                min_duration_seconds=8.8,
                plan=plan,
            )
            raised = False
        except KeywordFlowClosingError as exc:
            raised = True
            assert "beide ungültig" in str(exc)
    assert raised
