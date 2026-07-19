"""Path contract for Phase 10 supplementation artifacts."""

from __future__ import annotations

from pathlib import Path

from otio_app.discovery_v2.editorial_paths import (
    EDITORIAL_ROOT_SEGMENT,
    EditorialPathError,
    assert_editorial_relative_path,
    get_editorial_root,
    resolve_editorial_relative_path,
)


def supplementation_gap_json_relative_path(gap_id: str) -> str:
    _reject_unsafe_segment(gap_id, "gap_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/gaps/{gap_id}.json"


def supplementation_request_json_relative_path(request_id: str) -> str:
    _reject_unsafe_segment(request_id, "request_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/supplementation/requests/{request_id}.json"


def supplementation_search_json_relative_path(attempt_id: str) -> str:
    _reject_unsafe_segment(attempt_id, "attempt_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/supplementation/searches/{attempt_id}.json"


def supplementation_candidate_json_relative_path(candidate_id: str) -> str:
    _reject_unsafe_segment(candidate_id, "candidate_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/supplementation/candidates/{candidate_id}.json"


def supplementation_preview_relative_path(attempt_id: str, preview_id: str) -> str:
    _reject_unsafe_segment(attempt_id, "attempt_id")
    _reject_unsafe_segment(preview_id, "preview_id")
    return (
        f"{EDITORIAL_ROOT_SEGMENT}/supplementation/previews/"
        f"{attempt_id}/{preview_id}.preview"
    )


def supplementation_graphic_plan_json_relative_path(graphic_plan_id: str) -> str:
    _reject_unsafe_segment(graphic_plan_id, "graphic_plan_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/graphics/{graphic_plan_id}.json"


def supplementation_claim_decision_json_relative_path(decision_id: str) -> str:
    _reject_unsafe_segment(decision_id, "decision_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/claim_decisions/{decision_id}.json"


def supplementation_script_lock_json_relative_path(lock_id: str) -> str:
    _reject_unsafe_segment(lock_id, "lock_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/script_locks/{lock_id}.json"


def supplementation_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/runs/supplementation/{run_id}.json"


def supplementation_attempt_json_relative_path(attempt_id: str) -> str:
    _reject_unsafe_segment(attempt_id, "attempt_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/attempts/supplementation/{attempt_id}.json"


def supplementation_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_editorial_root(project_root) / "temp" / run_id


def supplementation_latest_script_lock_relative_path() -> str:
    return f"{EDITORIAL_ROOT_SEGMENT}/latest_script_lock.json"


def assert_supplementation_relative_path(relative_path: str) -> str:
    return assert_editorial_relative_path(relative_path)


def resolve_supplementation_relative_path(project_root: Path, relative_path: str) -> Path:
    return resolve_editorial_relative_path(project_root, relative_path)


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise EditorialPathError(f"{name} is missing.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise EditorialPathError(f"Invalid {name}: {value}")
    if text.startswith("_otio"):
        raise EditorialPathError(f"Invalid {name}: {value}")


__all__ = [
    "assert_supplementation_relative_path",
    "resolve_supplementation_relative_path",
    "supplementation_attempt_json_relative_path",
    "supplementation_candidate_json_relative_path",
    "supplementation_claim_decision_json_relative_path",
    "supplementation_gap_json_relative_path",
    "supplementation_graphic_plan_json_relative_path",
    "supplementation_latest_script_lock_relative_path",
    "supplementation_preview_relative_path",
    "supplementation_request_json_relative_path",
    "supplementation_run_json_relative_path",
    "supplementation_script_lock_json_relative_path",
    "supplementation_search_json_relative_path",
    "supplementation_temp_dir",
]
