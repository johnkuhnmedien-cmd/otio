"""Regressionstests: Validierung muss die projektspezifischen Min./Max.-Shot-
Regeln respektieren, nicht die globalen Default-Konstanten (3.0/8.0s)."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanSettings, TimelineItem, TimelineItemTransform
from otio_app.services.edit_plan_validator import ValidationStatus, validate_timeline_items


def _item(item_id: str, duration_sec: float, timeline_in: float = 0.0) -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="video_shot",
        section_id="section_test",
        folder_name="Test",
        voice_file="/tmp/voice.wav",
        resolved_media_path="/tmp/clip.mp4",
        duration_sec=duration_sec,
        final_duration_sec=duration_sec,
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_in + duration_sec,
        source_in_sec=0.0,
        source_out_sec=duration_sec,
        voice_start_sec=timeline_in,
        voice_end_sec=timeline_in + duration_sec,
        transform=TimelineItemTransform(),
    )


def test_validator_allows_duration_above_hardcoded_default_when_max_sec_increased() -> None:
    """Regression: Vorher wurde IMMER gegen den hardcoded Default (8.0s)
    validiert, selbst wenn der Nutzer z.B. 10s als Max. Shot konfiguriert hat."""
    settings = EditPlanSettings(shot_min_sec=3.0, shot_max_sec=10.0, section_outro_sec=0.0)
    items = [_item("item_001", 9.0)]

    result = validate_timeline_items(items, settings=settings)

    duration_errors = [e for e in result.errors if "final_duration_sec" in e]
    assert not duration_errors, duration_errors


def test_validator_blocks_duration_above_configured_max_sec() -> None:
    settings = EditPlanSettings(shot_min_sec=3.0, shot_max_sec=6.0, section_outro_sec=0.0)
    items = [_item("item_001", 7.0)]

    result = validate_timeline_items(items, settings=settings)

    duration_errors = [e for e in result.errors if "final_duration_sec" in e]
    assert duration_errors
    assert "6.0" in duration_errors[0]
    assert result.status == ValidationStatus.BLOCKED


def test_validator_warns_when_min_greater_than_max() -> None:
    settings = EditPlanSettings(shot_min_sec=9.0, shot_max_sec=8.0, section_outro_sec=0.0)
    items = [_item("item_001", 8.0)]

    result = validate_timeline_items(items, settings=settings)

    assert any("Min. Shot" in w and "Max. Shot" in w for w in result.warnings)
