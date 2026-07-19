"""Path contract for Discovery V2 visual edit artifacts under ``_otio_v2/editing/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2, get_discovery_v2_root

EDITING_ROOT_SEGMENT = "editing"


class EditingPathError(ValueError):
    """Invalid visual edit artifact path."""


def get_editing_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / EDITING_ROOT_SEGMENT


def visual_edit_plan_json_relative_path(plan_id: str) -> str:
    _reject_unsafe_segment(plan_id, "plan_id")
    return f"{EDITING_ROOT_SEGMENT}/plans/{plan_id}.json"


def humanity_review_json_relative_path(review_id: str) -> str:
    _reject_unsafe_segment(review_id, "review_id")
    return f"{EDITING_ROOT_SEGMENT}/humanity_reviews/{review_id}.json"


def feasibility_report_json_relative_path(report_id: str) -> str:
    _reject_unsafe_segment(report_id, "report_id")
    return f"{EDITING_ROOT_SEGMENT}/feasibility/{report_id}.json"


def repair_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITING_ROOT_SEGMENT}/repairs/{run_id}.json"


def repair_proposal_ops_json_relative_path(proposal_id: str) -> str:
    _reject_unsafe_segment(proposal_id, "proposal_id")
    return f"{EDITING_ROOT_SEGMENT}/repairs/repair_proposal_{proposal_id}.ops.json"


def repair_proposal_decisions_json_relative_path(proposal_id: str) -> str:
    _reject_unsafe_segment(proposal_id, "proposal_id")
    return f"{EDITING_ROOT_SEGMENT}/repairs/repair_proposal_{proposal_id}.decisions.json"


def repair_apply_idempotency_json_relative_path(idempotency_key: str) -> str:
    _reject_unsafe_segment(idempotency_key, "idempotency_key")
    return f"{EDITING_ROOT_SEGMENT}/repairs/apply_{idempotency_key}.json"


def visual_edit_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITING_ROOT_SEGMENT}/runs/{run_id}.json"


def visual_edit_report_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITING_ROOT_SEGMENT}/reports/{run_id}.json"


def visual_edit_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_editing_root(project_root) / "temp" / run_id


def latest_visual_edit_plan_relative_path() -> str:
    return f"{EDITING_ROOT_SEGMENT}/latest_visual_edit_plan.json"


def latest_humanity_review_relative_path() -> str:
    return f"{EDITING_ROOT_SEGMENT}/latest_humanity_review.json"


def latest_feasibility_report_relative_path() -> str:
    return f"{EDITING_ROOT_SEGMENT}/latest_feasibility_report.json"


def latest_repair_run_relative_path() -> str:
    return f"{EDITING_ROOT_SEGMENT}/latest_repair_run.json"


def normalize_editing_relative_path(relative_path: str) -> str:
    if relative_path is None:
        raise EditingPathError("Editing path is missing.")
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise EditingPathError("Editing path is empty.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise EditingPathError(f"Absolute editing paths are forbidden: {raw}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise EditingPathError(f"Invalid editing path: {raw}")
    if ".." in parts:
        raise EditingPathError(f"Path escape is forbidden: {raw}")
    if DEFAULT_WORK_SUBDIR in parts:
        raise EditingPathError(f"Editing paths must not contain `{DEFAULT_WORK_SUBDIR}`: {raw}")
    if DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts:
        raise EditingPathError(
            f"Editing relative paths must not contain `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}`: {raw}"
        )
    if parts[0] != EDITING_ROOT_SEGMENT:
        raise EditingPathError(f"Editing path must be under `{EDITING_ROOT_SEGMENT}/`: {raw}")
    return "/".join(parts)


def assert_editing_relative_path(relative_path: str) -> str:
    return normalize_editing_relative_path(relative_path)


def resolve_editing_relative_path(project_root: Path, relative_path: str) -> Path:
    rel = normalize_editing_relative_path(relative_path)
    absolute = get_discovery_v2_root(project_root) / Path(*rel.split("/"))
    assert_path_is_under_discovery_v2(absolute, project_root)
    editing_root = get_editing_root(project_root).resolve()
    try:
        absolute.resolve().relative_to(editing_root)
    except ValueError as exc:
        raise EditingPathError(f"Path is not under editing/: {absolute}") from exc
    return absolute


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise EditingPathError(f"{name} is missing.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise EditingPathError(f"Invalid {name}: {value}")
    if text.startswith("_otio"):
        raise EditingPathError(f"Invalid {name}: {value}")


__all__ = [
    "EDITING_ROOT_SEGMENT",
    "EditingPathError",
    "assert_editing_relative_path",
    "feasibility_report_json_relative_path",
    "get_editing_root",
    "humanity_review_json_relative_path",
    "latest_feasibility_report_relative_path",
    "latest_humanity_review_relative_path",
    "latest_repair_run_relative_path",
    "latest_visual_edit_plan_relative_path",
    "normalize_editing_relative_path",
    "repair_apply_idempotency_json_relative_path",
    "repair_proposal_decisions_json_relative_path",
    "repair_proposal_ops_json_relative_path",
    "repair_run_json_relative_path",
    "resolve_editing_relative_path",
    "visual_edit_plan_json_relative_path",
    "visual_edit_report_relative_path",
    "visual_edit_run_json_relative_path",
    "visual_edit_temp_dir",
]
