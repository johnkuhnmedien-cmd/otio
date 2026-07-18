"""Path contract for Discovery V2 editorial artifacts under ``_otio_v2/editorial/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)

EDITORIAL_ROOT_SEGMENT = "editorial"
WORKING_MEDIA_SEGMENT = "media/working"


class EditorialPathError(ValueError):
    """Invalid editorial artifact path."""


def get_editorial_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / EDITORIAL_ROOT_SEGMENT


def editorial_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_editorial_root(project_root) / "temp" / run_id


def editorial_brief_json_relative_path(project_brief_id: str) -> str:
    _reject_unsafe_segment(project_brief_id, "project_brief_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/briefs/{project_brief_id}.json"


def editorial_narrative_json_relative_path(narrative_plan_id: str) -> str:
    _reject_unsafe_segment(narrative_plan_id, "narrative_plan_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/narrative_plans/{narrative_plan_id}.json"


def editorial_hook_json_relative_path(narrative_plan_id: str, hook_id: str) -> str:
    _reject_unsafe_segment(narrative_plan_id, "narrative_plan_id")
    _reject_unsafe_segment(hook_id, "hook_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/hooks/{narrative_plan_id}/{hook_id}.json"


def editorial_script_json_relative_path(script_id: str) -> str:
    _reject_unsafe_segment(script_id, "script_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/scripts/{script_id}.json"


def editorial_script_diff_relative_path(script_id: str) -> str:
    _reject_unsafe_segment(script_id, "script_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/scripts/{script_id}.diff.md"


def editorial_coverage_json_relative_path(coverage_audit_id: str) -> str:
    _reject_unsafe_segment(coverage_audit_id, "coverage_audit_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/coverage/{coverage_audit_id}.json"


def editorial_coverage_run_dedup_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/coverage_dedup/{run_id}.json"


def editorial_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/runs/{run_id}.json"


def editorial_attempt_json_relative_path(attempt_id: str) -> str:
    _reject_unsafe_segment(attempt_id, "attempt_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/attempts/{attempt_id}.json"


def editorial_latest_brief_relative_path() -> str:
    return f"{EDITORIAL_ROOT_SEGMENT}/latest_brief.json"


def editorial_latest_narrative_relative_path() -> str:
    return f"{EDITORIAL_ROOT_SEGMENT}/latest_narrative_plan.json"


def editorial_latest_script_relative_path() -> str:
    return f"{EDITORIAL_ROOT_SEGMENT}/latest_script.json"


def editorial_latest_coverage_relative_path() -> str:
    return f"{EDITORIAL_ROOT_SEGMENT}/latest_coverage.json"


def editorial_temp_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EDITORIAL_ROOT_SEGMENT}/temp/{run_id}"


def normalize_editorial_relative_path(relative_path: str) -> str:
    if relative_path is None:
        raise EditorialPathError("Editorial path is missing.")
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise EditorialPathError("Editorial path is empty.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise EditorialPathError(f"Absolute editorial paths are forbidden: {raw}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise EditorialPathError(f"Invalid editorial path: {raw}")
    if ".." in parts:
        raise EditorialPathError(f"Path escape is forbidden: {raw}")
    if DEFAULT_WORK_SUBDIR in parts:
        raise EditorialPathError(
            f"Editorial paths must not contain `{DEFAULT_WORK_SUBDIR}`: {raw}"
        )
    if parts.count(DEFAULT_DISCOVERY_V2_WORK_SUBDIR) > 0:
        raise EditorialPathError(
            f"Editorial relative paths must not contain "
            f"`{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}`: {raw}"
        )
    if parts[0] != EDITORIAL_ROOT_SEGMENT:
        raise EditorialPathError(
            f"Editorial path must be under `{EDITORIAL_ROOT_SEGMENT}/`: {raw}"
        )
    joined = "/".join(parts)
    if joined.startswith(f"{EDITORIAL_ROOT_SEGMENT}/media/working") or (
        WORKING_MEDIA_SEGMENT in joined
    ):
        raise EditorialPathError(f"Editorial path must not be working media: {raw}")
    return joined


def assert_editorial_relative_path(relative_path: str) -> str:
    return normalize_editorial_relative_path(relative_path)


def resolve_editorial_relative_path(project_root: Path, relative_path: str) -> Path:
    rel = normalize_editorial_relative_path(relative_path)
    absolute = get_discovery_v2_root(project_root) / Path(*rel.split("/"))
    assert_path_is_under_discovery_v2(absolute, project_root)
    editorial_root = get_editorial_root(project_root).resolve()
    try:
        absolute.resolve().relative_to(editorial_root)
    except ValueError as exc:
        raise EditorialPathError(f"Path is not under editorial/: {absolute}") from exc
    return absolute


def is_editorial_relative_path(relative_path: str) -> bool:
    try:
        normalize_editorial_relative_path(relative_path)
    except EditorialPathError:
        return False
    return True


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise EditorialPathError(f"{name} is missing.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise EditorialPathError(f"Invalid {name}: {value}")
    if text.startswith("_otio"):
        raise EditorialPathError(f"Invalid {name}: {value}")


__all__ = [
    "EDITORIAL_ROOT_SEGMENT",
    "EditorialPathError",
    "assert_editorial_relative_path",
    "editorial_attempt_json_relative_path",
    "editorial_brief_json_relative_path",
    "editorial_coverage_json_relative_path",
    "editorial_coverage_run_dedup_relative_path",
    "editorial_hook_json_relative_path",
    "editorial_latest_brief_relative_path",
    "editorial_latest_coverage_relative_path",
    "editorial_latest_narrative_relative_path",
    "editorial_latest_script_relative_path",
    "editorial_narrative_json_relative_path",
    "editorial_run_json_relative_path",
    "editorial_script_diff_relative_path",
    "editorial_script_json_relative_path",
    "editorial_temp_dir",
    "editorial_temp_relative_path",
    "get_editorial_root",
    "is_editorial_relative_path",
    "normalize_editorial_relative_path",
    "resolve_editorial_relative_path",
]
