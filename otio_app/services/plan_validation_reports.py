"""Laden und Anzeige von Schnittplan-Validierungsreports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings
from otio_app.services.edit_plan_builder import (
    edit_plan_candidate_failed_path,
    edit_plan_validation_report_path,
    gemini_retry_report_path,
)
from otio_app.services.edit_plan_validator import (
    ASSET_RULE_ERROR_TYPES,
    FinalPlanValidationResult,
    PlanValidationError,
    ValidationStatus,
    plan_validation_error_to_message,
    validate_asset_usage_rules,
    validate_final_edit_plan,
    validate_shot_duration_rules,
)
from otio_app.services.edit_plan_rules import EditPlanRulesDocument


def load_json_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_edit_plan_validation_report(work_dir: Path) -> dict[str, Any] | None:
    return load_json_report(edit_plan_validation_report_path(work_dir))


def load_gemini_retry_report(work_dir: Path) -> dict[str, Any] | None:
    return load_json_report(gemini_retry_report_path(work_dir))


def load_failed_plan_candidate(work_dir: Path) -> dict[str, Any] | None:
    return load_json_report(edit_plan_candidate_failed_path(work_dir))


def format_validation_error_entries(errors: list[dict[str, Any] | PlanValidationError | str]) -> list[str]:
    lines: list[str] = []
    for entry in errors:
        if isinstance(entry, str):
            lines.append(entry)
        elif isinstance(entry, PlanValidationError):
            lines.append(plan_validation_error_to_message(entry))
        elif isinstance(entry, dict):
            lines.append(plan_validation_error_to_message(PlanValidationError.from_dict(entry)))
        else:
            lines.append(str(entry))
    return lines


def validation_status_label(*, ok: bool, blocked: bool, retrying: bool = False) -> str:
    if ok:
        return "PASS"
    if retrying:
        return "RETRYING"
    if blocked:
        return "BLOCKED"
    return "FAIL"


def plan_is_confirmable(document: EditPlanDocument) -> bool:
    if document.candidate_status == "BLOCKED":
        return False
    if document.validation_status == "FAIL":
        return False
    return True


def validate_document_for_confirm(
    document: EditPlanDocument,
    *,
    rules_doc: EditPlanRulesDocument,
    allow_asset_rule_overrides: bool = True,
) -> FinalPlanValidationResult:
    result = validate_final_edit_plan(
        document.timeline_items,
        settings=document.settings,
        voiceover=document.voiceover,
        rules_doc=rules_doc,
    )
    if not allow_asset_rule_overrides:
        return result
    blocking_errors = [
        error for error in result.errors if error.type not in ASSET_RULE_ERROR_TYPES
    ]
    if blocking_errors:
        return FinalPlanValidationResult(
            ok=False,
            status=ValidationStatus.BLOCKED,
            errors=blocking_errors,
            warnings=result.warnings,
        )
    return FinalPlanValidationResult(
        ok=True,
        status=result.status if result.status != ValidationStatus.BLOCKED else ValidationStatus.AWAITING_APPROVAL,
        errors=[error for error in result.errors if error.type in ASSET_RULE_ERROR_TYPES],
        warnings=result.warnings,
    )


def global_validation_blocked(
    timeline_items,
    *,
    settings: EditPlanSettings,
    rules_doc: EditPlanRulesDocument,
) -> FinalPlanValidationResult:
    """Globale Validierung über alle Ordner (Asset-Nutzung, Shot-Min/Max)."""
    errors: list[PlanValidationError] = []
    errors.extend(validate_shot_duration_rules(timeline_items, settings=settings))
    errors.extend(validate_asset_usage_rules(timeline_items, rules_doc=rules_doc))
    ok = not errors
    return FinalPlanValidationResult(
        ok=ok,
        status=ValidationStatus.BLOCKED if errors else ValidationStatus.OK,
        errors=errors,
    )


def format_used_rules_summary(used_rules: dict[str, Any] | None) -> list[str]:
    if not used_rules:
        return []
    lines: list[str] = []
    shot_min = used_rules.get("shot_min_sec")
    shot_max = used_rules.get("shot_max_sec")
    if shot_min is not None and shot_max is not None:
        lines.append(f"Min/Max Shot: {float(shot_min):.1f}s / {float(shot_max):.1f}s")
    max_usage = used_rules.get("max_asset_usage")
    if max_usage is not None:
        lines.append(f"Max. Asset-Nutzung: {max_usage}× global")
    min_gap = used_rules.get("min_asset_reuse_distance_shots")
    if min_gap is not None:
        lines.append(f"Min. Wiederverwendungsabstand: {min_gap} Shots")
    return lines


def gemini_attempts_label(attempts: int, *, max_attempts: int = 3) -> str:
    if attempts <= 0:
        return f"0/{max_attempts}"
    return f"{attempts}/{max_attempts}"


def latest_retry_attempt_summary(work_dir: Path) -> str | None:
    report = load_gemini_retry_report(work_dir)
    if not report:
        return None
    attempts = report.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    last = attempts[-1]
    if not isinstance(last, dict):
        return None
    number = last.get("attempt_number")
    accepted = last.get("accepted")
    if number is None:
        return None
    status = "PASS" if accepted else "BLOCKED"
    return f"Versuch {number}/3 — {status}"
