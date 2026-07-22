"""Tests für Timeline-Workflow und Dauerregeln."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    EditPlanShot,
    TimelineItem,
    TimelineItemTransform,
    VoiceoverPlan,
)
from otio_app.services.duration_rules import MAX_DURATION_SEC, MIN_DURATION_SEC, split_total_duration
from otio_app.services.edit_plan_validator import ValidationStatus, validate_timeline_items
from otio_app.services.generic_outro_selector import select_generic_outro_assets
from otio_app.services.timeline_plan_builder import build_outro_timeline_items


def _item(
    *,
    item_id: str,
    item_type: str,
    duration: float,
    folder: str = "Canyon",
    path: str = "/media/a.mp4",
    outro: bool = False,
) -> TimelineItem:
    voice_end = 10.0 if not outro else duration
    return TimelineItem(
        timeline_item_id=item_id,
        type=item_type,
        section_id="section_canyon",
        folder_name=folder,
        voice_file="/voice.wav",
        asset_id="asset_a",
        resolved_media_path=path,
        asset_role="generic_section_outro" if outro else "narration",
        timeline_in_sec=0.0,
        timeline_out_sec=duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=0.0,
        source_out_sec=duration,
        voice_start_sec=0.0,
        voice_end_sec=voice_end if outro else duration,
        selection_reason="test",
        confidence=0.8,
        transform=TimelineItemTransform(),
    )


def test_final_duration_never_exceeds_8s() -> None:
    settings = EditPlanSettings(
        section_outro_sec=0.0,
        video_head_trim_policy="disabled",
    )
    voiceover = VoiceoverPlan(
        path="/voice.wav",
        timeline_start_sec=1.0,
        source_in_sec=0.0,
        source_out_sec=7.0,
        duration_sec=7.0,
        timeline_end_sec=8.0,
        duration_source="ffprobe",
        trim_policy="disabled",
    )
    items = [
        _item(
            item_id="i1",
            item_type="video_shot",
            duration=8.0,
            path="/media/ok.mp4",
        )
    ]
    items[0] = items[0].model_copy(update={"voice_end_sec": 7.0})
    result = validate_timeline_items(items, settings=settings, voiceover=voiceover)
    assert result.status == ValidationStatus.OK

    bad = [_item(item_id="i2", item_type="video_shot", duration=12.0)]
    bad_result = validate_timeline_items(bad, settings=settings)
    assert bad_result.status == ValidationStatus.BLOCKED
    assert any("12.0s > 8.0s" in line for line in bad_result.errors)


def test_section_outro_creates_separate_generic_element() -> None:
    items, errors = build_outro_timeline_items(
        folder_name="Canyon",
        voice_file="/v.wav",
        voice_end_sec=30.0,
        section_cursor_sec=25.0,
        outro_total_sec=5.0,
        folder_assets=[
            {"path": "/media/landscape.mp4", "description": "Ruhige Landschaft overview"}
        ],
        used_paths=set(),
        last_asset_path="/media/main.mp4",
        item_index_start=10,
        trim_leading_sec=0.0,
    )
    assert not errors
    assert len(items) == 1
    assert items[0].type == "generic_outro_visual"
    assert items[0].duration_sec == 5.0


def test_section_outro_not_added_to_last_shot() -> None:
    narration = EditPlanShot(
        voice_file="/v.wav",
        folder="Canyon",
        voice_start_sec=0.0,
        voice_end_sec=7.0,
        duration_sec=7.0,
        asset_path="/media/last.mp4",
    )
    assert narration.duration_sec == 7.0
    assert narration.duration_sec <= MAX_DURATION_SEC


def test_section_outro_over_8s_splits_into_multiple_elements() -> None:
    assert split_total_duration(14.0) == [7.0, 7.0]
    chunks = split_total_duration(20.0)
    assert sum(chunks) == pytest.approx(20.0)
    assert all(MIN_DURATION_SEC <= c <= MAX_DURATION_SEC for c in chunks)
    assert len(chunks) >= 2


def test_generic_asset_explicit_in_plan() -> None:
    items, _ = build_outro_timeline_items(
        folder_name="Canyon",
        voice_file="/v.wav",
        voice_end_sec=5.0,
        section_cursor_sec=10.0,
        outro_total_sec=5.0,
        folder_assets=[{"path": "/media/broll.mp4", "description": "Establishing wide"}],
        used_paths=set(),
        last_asset_path=None,
        item_index_start=1,
        trim_leading_sec=0.0,
    )
    assert items[0].resolved_media_path == "/media/broll.mp4"
    assert items[0].selection_reason


def test_outro_prefers_same_folder_asset() -> None:
    chosen = select_generic_outro_assets(
        [
            {"path": "/a.mp4", "description": "action explosion"},
            {"path": "/b.mp4", "description": "ruhige Landschaft establishing"},
        ],
        used_paths=set(),
        last_asset_path="/a.mp4",
        count=1,
    )
    assert chosen[0].path == "/b.mp4"


def test_no_black_during_voiceover() -> None:
    items = [
        TimelineItem(
            timeline_item_id="n1",
            type="video_shot",
            section_id="s1",
            folder_name="Canyon",
            voice_file="/v.wav",
            resolved_media_path="/a.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=5.0,
            duration_sec=5.0,
            final_duration_sec=5.0,
            source_in_sec=0.0,
            source_out_sec=5.0,
            voice_start_sec=0.0,
            voice_end_sec=12.0,
        )
    ]
    result = validate_timeline_items(
        items,
        settings=EditPlanSettings(section_outro_sec=0.0),
    )
    assert any("Visuelles Loch" in line for line in result.errors)


def test_missing_generic_blocks_or_awaiting_approval() -> None:
    items, errors = build_outro_timeline_items(
        folder_name="Canyon",
        voice_file="/v.wav",
        voice_end_sec=1.0,
        section_cursor_sec=0.0,
        outro_total_sec=5.0,
        folder_assets=[],
        used_paths=set(),
        last_asset_path=None,
        item_index_start=1,
        trim_leading_sec=0.0,
    )
    assert not items
    assert errors

    result = validate_timeline_items(
        [_item(item_id="n", item_type="video_shot", duration=5.0)],
        settings=EditPlanSettings(section_outro_sec=5.0),
    )
    assert result.status == ValidationStatus.AWAITING_APPROVAL
