"""Tests für finale Plan-Validierung und Retry-Loop (Phase 4)."""

from __future__ import annotations

from otio_app.analysis_models import (
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanSettings,
    TimelineItem,
)
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES
from otio_app.services.edit_plan_validator import (
    FinalPlanValidationResult,
    PlanValidationError,
    plan_validation_error_to_message,
    should_retry_gemini_for_validation,
    validate_final_edit_plan,
    validate_asset_usage_rules,
    validate_shot_duration_rules,
)


def _item(
    item_id: str,
    *,
    asset_id: str = "asset_a.mp4",
    duration: float = 5.0,
    timeline_in: float = 0.0,
    beat_id: str = "beat_001",
    item_type: str = "video_shot",
) -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type=item_type,
        section_id="sec",
        folder_name="Folder",
        asset_id=asset_id,
        duration_sec=duration,
        final_duration_sec=duration,
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_in + duration,
        voice_start_sec=0.0,
        voice_end_sec=18.0,
        beat_id=beat_id,
        resolved_media_path=f"/tmp/{asset_id}",
    )


def _rules(max_count: int = 1, min_gap: int = 0) -> EditPlanRulesDocument:
    return EditPlanRulesDocument(
        project_id="test",
        rules=[
            EditPlanRule(
                id="max",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": max_count, "min_gap": min_gap},
            )
        ],
    )


def test_max_asset_usage_one_duplicate_fails_globally() -> None:
    items = [
        _item("shot_001", asset_id="dup.mp4", timeline_in=0.0),
        _item("shot_002", asset_id="other.mp4", timeline_in=5.0),
        _item("shot_003", asset_id="dup.mp4", timeline_in=10.0),
    ]
    errors = validate_asset_usage_rules(items, rules_doc=_rules(max_count=1))
    assert len(errors) == 1
    assert errors[0].type == "ASSET_USAGE_LIMIT_EXCEEDED"
    assert errors[0].asset_id == "dup.mp4"
    assert errors[0].usage_count == 2
    assert set(errors[0].timeline_item_ids or []) == {"shot_001", "shot_003"}


def test_asset_reuse_distance_too_short() -> None:
    items = [
        _item("shot_001", asset_id="dup.mp4", timeline_in=0.0),
        _item("shot_002", asset_id="x.mp4", timeline_in=5.0),
        _item("shot_003", asset_id="dup.mp4", timeline_in=10.0),
    ]
    errors = validate_asset_usage_rules(items, rules_doc=_rules(max_count=5, min_gap=2))
    assert any(error.type == "ASSET_REUSE_DISTANCE_TOO_SHORT" for error in errors)


def test_validate_shot_duration_rules_flags_too_long() -> None:
    items = [_item("shot_001", duration=10.2, beat_id="beat_002")]
    settings = EditPlanSettings(shot_min_sec=3.0, shot_max_sec=8.0)
    errors = validate_shot_duration_rules(items, settings=settings)
    assert len(errors) == 1
    assert errors[0].type == "SHOT_TOO_LONG"
    assert errors[0].timeline_item_id == "shot_001"


def test_validate_shot_duration_allows_short_when_segment_short() -> None:
    items = [
        TimelineItem(
            timeline_item_id="shot_001",
            type="video_shot",
            section_id="sec",
            folder_name="Folder",
            asset_id="a.mp4",
            duration_sec=2.4,
            final_duration_sec=2.4,
            timeline_in_sec=0.0,
            timeline_out_sec=2.4,
            voice_start_sec=0.0,
            voice_end_sec=2.4,
            beat_id="beat_001",
            resolved_media_path="/tmp/a.mp4",
        )
    ]
    settings = EditPlanSettings(shot_min_sec=3.0, shot_max_sec=8.0)
    errors = validate_shot_duration_rules(items, settings=settings)
    assert errors == []


def test_should_retry_gemini_for_validation_on_asset_and_shot_errors() -> None:
    errors = [
        PlanValidationError(type="ASSET_USAGE_LIMIT_EXCEEDED"),
        PlanValidationError(type="SHOT_TOO_LONG"),
        PlanValidationError(type="INSUFFICIENT_PARTS"),
        PlanValidationError(type="TIMELINE_VALIDATION", message="rights"),
    ]
    assert should_retry_gemini_for_validation(errors) is True
    assert should_retry_gemini_for_validation(
        [PlanValidationError(type="TIMELINE_VALIDATION", message="rights")]
    ) is False


def test_validate_final_edit_plan_merges_structured_errors() -> None:
    items = [
        _item("shot_001", asset_id="dup.mp4", duration=10.0, timeline_in=0.0),
        _item("shot_002", asset_id="dup.mp4", duration=5.0, timeline_in=10.0),
    ]
    settings = EditPlanSettings(shot_min_sec=3.0, shot_max_sec=8.0)
    result = validate_final_edit_plan(
        items,
        settings=settings,
        voiceover=None,
        rules_doc=_rules(max_count=1),
    )
    assert isinstance(result, FinalPlanValidationResult)
    assert result.ok is False
    assert result.has_retryable_errors is True
    types = {error.type for error in result.errors}
    assert "ASSET_USAGE_LIMIT_EXCEEDED" in types
    assert "SHOT_TOO_LONG" in types
