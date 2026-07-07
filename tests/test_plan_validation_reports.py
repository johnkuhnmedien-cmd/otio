"""Tests für plan_validation_reports-Hilfsfunktionen."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings
from otio_app.services.edit_plan_builder import (
    edit_plan_candidate_failed_path,
    edit_plan_validation_report_path,
    gemini_retry_report_path,
)
from otio_app.services.plan_validation_reports import (
    format_used_rules_summary,
    gemini_attempts_label,
    latest_retry_attempt_summary,
    load_edit_plan_validation_report,
    plan_is_confirmable,
    validation_status_label,
)


def test_plan_is_confirmable_rejects_blocked_and_fail() -> None:
    blocked = EditPlanDocument(
        project_id="p",
        candidate_status="BLOCKED",
        validation_status="FAIL",
    )
    assert plan_is_confirmable(blocked) is False

    fail_only = EditPlanDocument(project_id="p", validation_status="FAIL")
    assert plan_is_confirmable(fail_only) is False

    accepted = EditPlanDocument(
        project_id="p",
        candidate_status="ACCEPTED",
        validation_status="PASS",
    )
    assert plan_is_confirmable(accepted) is True

    legacy = EditPlanDocument(project_id="p")
    assert plan_is_confirmable(legacy) is True


def test_format_used_rules_summary() -> None:
    lines = format_used_rules_summary(
        {
            "shot_min_sec": 3.0,
            "shot_max_sec": 8.0,
            "max_asset_usage": 1,
            "min_asset_reuse_distance_shots": 2,
        }
    )
    assert any("Min/Max Shot" in line for line in lines)
    assert any("Max. Asset-Nutzung" in line for line in lines)
    assert any("Wiederverwendungsabstand" in line for line in lines)


def test_format_used_rules_summary_holistic_v1() -> None:
    lines = format_used_rules_summary({"prompt_mode": "holistic_v1"})
    assert any("Holistic v1" in line for line in lines)
    assert validation_status_label(ok=True, blocked=False) == "PASS"
    assert validation_status_label(ok=False, blocked=True) == "BLOCKED"
    assert validation_status_label(ok=False, blocked=False, retrying=True) == "RETRYING"


def test_gemini_attempts_label() -> None:
    assert gemini_attempts_label(2) == "2/3"
    assert gemini_attempts_label(0) == "0/3"


def test_validate_document_for_confirm_allows_asset_rule_override() -> None:
    from unittest.mock import patch

    from otio_app.analysis_models import EditPlanDocument, EditPlanSettings
    from otio_app.services.edit_plan_rules import EditPlanRulesDocument
    from otio_app.services.edit_plan_validator import (
        FinalPlanValidationResult,
        PlanValidationError,
        ValidationStatus,
    )
    from otio_app.services.plan_validation_reports import validate_document_for_confirm

    document = EditPlanDocument(project_id="p", settings=EditPlanSettings())
    rules = EditPlanRulesDocument(project_id="p", rules=[])
    asset_error = PlanValidationError(
        type="ASSET_USAGE_LIMIT_EXCEEDED",
        asset_id="dup.mp4",
        usage_count=2,
        max_allowed=1,
    )
    timeline_error = PlanValidationError(
        type="TIMELINE_VALIDATION",
        message="Visuelles Loch während aktivem Voice-over",
    )
    validation_result = FinalPlanValidationResult(
        ok=False,
        status=ValidationStatus.BLOCKED,
        errors=[asset_error],
    )

    with patch(
        "otio_app.services.plan_validation_reports.validate_final_edit_plan",
        return_value=validation_result,
    ):
        strict = validate_document_for_confirm(
            document,
            rules_doc=rules,
            allow_asset_rule_overrides=False,
        )
        relaxed = validate_document_for_confirm(
            document,
            rules_doc=rules,
            allow_asset_rule_overrides=True,
        )

    assert strict.ok is False
    assert relaxed.ok is True
    assert len(relaxed.errors) == 1
    assert relaxed.errors[0].type == "ASSET_USAGE_LIMIT_EXCEEDED"

    mixed_result = FinalPlanValidationResult(
        ok=False,
        status=ValidationStatus.BLOCKED,
        errors=[asset_error, timeline_error],
    )
    with patch(
        "otio_app.services.plan_validation_reports.validate_final_edit_plan",
        return_value=mixed_result,
    ):
        mixed_relaxed = validate_document_for_confirm(
            document,
            rules_doc=rules,
            allow_asset_rule_overrides=True,
        )
    assert mixed_relaxed.ok is False


def test_load_reports_and_latest_retry_summary(tmp_path: Path) -> None:
    work_dir = tmp_path / "_otio"
    work_dir.mkdir()
    validation_path = edit_plan_validation_report_path(work_dir)
    retry_path = gemini_retry_report_path(work_dir)
    failed_path = edit_plan_candidate_failed_path(work_dir)

    validation_path.write_text(
        json.dumps(
            {
                "used_rules": {"shot_min_sec": 3.0, "shot_max_sec": 8.0},
                "final_status": "BLOCKED",
                "retry_attempts": 3,
            }
        ),
        encoding="utf-8",
    )
    retry_path.write_text(
        json.dumps(
            {
                "attempts": [
                    {"attempt_number": 1, "accepted": False},
                    {"attempt_number": 3, "accepted": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    failed_path.write_text(
        json.dumps({"candidate_status": "BLOCKED"}),
        encoding="utf-8",
    )

    report = load_edit_plan_validation_report(work_dir)
    assert report is not None
    assert report["final_status"] == "BLOCKED"
    assert latest_retry_attempt_summary(work_dir) == "Versuch 3/3 — BLOCKED"
