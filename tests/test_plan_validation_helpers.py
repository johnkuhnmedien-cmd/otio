"""Phase-1-Hilfsfunktionen: Asset-Regeln, allowed_parts, PlanValidationError."""

from __future__ import annotations

import pytest

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.services.asset_usage import AssetUsageRules, get_asset_usage_rules
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES
from otio_app.services.edit_plan_validator import (
    RETRYABLE_ERROR_TYPES,
    PlanValidationError,
    plan_validation_error_to_message,
)
from otio_app.services.shot_timing import AllowedPartsBounds, allowed_parts_for_segment


def _rules_doc(
    *,
    max_count: int = 2,
    min_gap: int = 0,
    enabled: bool = True,
) -> EditPlanRulesDocument:
    return EditPlanRulesDocument(
        project_id="test",
        rules=[
            EditPlanRule(
                id="max-uses",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=enabled,
                params={"max_count": max_count, "min_gap": min_gap},
            ),
        ],
    )


def test_get_asset_usage_rules_reads_max_count_and_min_gap() -> None:
    rules = get_asset_usage_rules(_rules_doc(max_count=1, min_gap=3))
    assert rules == AssetUsageRules(
        max_asset_usage=1,
        min_asset_reuse_distance_shots=3,
        asset_reuse_policy="hard_block",
    )
    assert rules.to_dict() == {
        "max_asset_usage": 1,
        "min_asset_reuse_distance_shots": 3,
        "asset_reuse_policy": "hard_block",
    }


def test_get_asset_usage_rules_disabled_rule_returns_none_max() -> None:
    rules = get_asset_usage_rules(_rules_doc(enabled=False))
    assert rules.max_asset_usage is None
    assert rules.min_asset_reuse_distance_shots == 0


@pytest.mark.parametrize(
    ("duration", "min_sec", "max_sec", "expected"),
    [
        (
            2.4,
            3.0,
            8.0,
            AllowedPartsBounds(min_parts=1, max_parts=1, short_segment_allowed=True),
        ),
        (
            7.5,
            3.0,
            8.0,
            AllowedPartsBounds(min_parts=1, max_parts=2, short_segment_allowed=False),
        ),
        (
            18.0,
            3.0,
            8.0,
            AllowedPartsBounds(min_parts=3, max_parts=6, short_segment_allowed=False),
        ),
        (
            22.0,
            3.0,
            8.0,
            AllowedPartsBounds(min_parts=3, max_parts=7, short_segment_allowed=False),
        ),
    ],
)
def test_allowed_parts_for_segment_chef_examples(
    duration: float,
    min_sec: float,
    max_sec: float,
    expected: AllowedPartsBounds,
) -> None:
    result = allowed_parts_for_segment(duration, min_sec=min_sec, max_sec=max_sec)
    assert result == expected
    assert result.to_dict() == expected.to_dict()


def test_allowed_parts_for_segment_zero_duration_is_short() -> None:
    result = allowed_parts_for_segment(0.0, min_sec=3.0, max_sec=8.0)
    assert result.min_parts == 1
    assert result.max_parts == 1
    assert result.short_segment_allowed is True


def test_plan_validation_error_roundtrip_dict() -> None:
    error = PlanValidationError(
        type="ASSET_USAGE_LIMIT_EXCEEDED",
        asset_id="clip.mp4",
        usage_count=2,
        max_allowed=1,
        timeline_item_ids=["shot_003", "shot_007"],
    )
    restored = PlanValidationError.from_dict(error.to_dict())
    assert restored.type == error.type
    assert restored.asset_id == error.asset_id
    assert restored.usage_count == error.usage_count
    assert restored.timeline_item_ids == error.timeline_item_ids


def test_plan_validation_error_retryable_types() -> None:
    assert PlanValidationError(type="SHOT_TOO_SHORT").is_retryable()
    assert PlanValidationError(type="SHOT_TOO_LONG").is_retryable()
    assert PlanValidationError(type="ASSET_USAGE_LIMIT_EXCEEDED").is_retryable()
    assert PlanValidationError(type="ASSET_REUSE_DISTANCE_TOO_SHORT").is_retryable()
    assert PlanValidationError(type="INSUFFICIENT_PARTS").is_retryable()
    assert not PlanValidationError(type="UNKNOWN_RULE").is_retryable()
    assert "SHOT_TOO_SHORT" in RETRYABLE_ERROR_TYPES


def test_plan_validation_error_to_message_formats_asset_usage() -> None:
    error = PlanValidationError(
        type="ASSET_USAGE_LIMIT_EXCEEDED",
        asset_id="Antelope.mp4",
        usage_count=2,
        max_allowed=1,
    )
    message = plan_validation_error_to_message(error)
    assert "ASSET_USAGE_LIMIT_EXCEEDED" in message
    assert "Antelope.mp4" in message
    assert "2" in message


def test_plan_validation_error_to_message_formats_shot_too_long() -> None:
    error = PlanValidationError(
        type="SHOT_TOO_LONG",
        timeline_item_id="shot_004",
        duration_sec=10.2,
        max_sec=8.0,
        segment_id="beat_002",
        reason="Shot longer than maximum duration",
    )
    message = plan_validation_error_to_message(error)
    assert "SHOT_TOO_LONG" in message
    assert "shot_004" in message
    assert "10.2" in message
