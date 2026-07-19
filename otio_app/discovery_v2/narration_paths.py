"""Path contract for Discovery V2 narration artifacts under ``_otio_v2/narration/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)

NARRATION_ROOT_SEGMENT = "narration"


class NarrationPathError(ValueError):
    """Invalid narration artifact path."""


def get_narration_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / NARRATION_ROOT_SEGMENT


def narration_voice_profile_json_relative_path(voice_profile_id: str) -> str:
    _reject_unsafe_segment(voice_profile_id, "voice_profile_id")
    return f"{NARRATION_ROOT_SEGMENT}/voice_profiles/{voice_profile_id}.json"


def narration_audio_relative_path(run_id: str, segment_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    _reject_unsafe_segment(segment_id, "segment_id")
    return f"{NARRATION_ROOT_SEGMENT}/audio/{run_id}/{segment_id}.wav"


def narration_pause_plan_json_relative_path(pause_plan_id: str) -> str:
    _reject_unsafe_segment(pause_plan_id, "pause_plan_id")
    return f"{NARRATION_ROOT_SEGMENT}/pause_plans/{pause_plan_id}.json"


def narration_timeline_json_relative_path(timeline_id: str) -> str:
    _reject_unsafe_segment(timeline_id, "timeline_id")
    return f"{NARRATION_ROOT_SEGMENT}/timelines/{timeline_id}.json"


def narration_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{NARRATION_ROOT_SEGMENT}/runs/{run_id}.json"


def narration_attempt_json_relative_path(attempt_id: str) -> str:
    _reject_unsafe_segment(attempt_id, "attempt_id")
    return f"{NARRATION_ROOT_SEGMENT}/attempts/{attempt_id}.json"


def narration_report_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{NARRATION_ROOT_SEGMENT}/reports/{run_id}.json"


def narration_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_narration_root(project_root) / "temp" / run_id


def narration_latest_voice_run_relative_path() -> str:
    return f"{NARRATION_ROOT_SEGMENT}/latest_voice_run.json"


def narration_latest_pause_plan_relative_path() -> str:
    return f"{NARRATION_ROOT_SEGMENT}/latest_pause_plan.json"


def narration_latest_timeline_relative_path() -> str:
    return f"{NARRATION_ROOT_SEGMENT}/latest_timeline.json"


def normalize_narration_relative_path(relative_path: str) -> str:
    if relative_path is None:
        raise NarrationPathError("Narration path is missing.")
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise NarrationPathError("Narration path is empty.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise NarrationPathError(f"Absolute narration paths are forbidden: {raw}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise NarrationPathError(f"Invalid narration path: {raw}")
    if ".." in parts:
        raise NarrationPathError(f"Path escape is forbidden: {raw}")
    if DEFAULT_WORK_SUBDIR in parts:
        raise NarrationPathError(
            f"Narration paths must not contain `{DEFAULT_WORK_SUBDIR}`: {raw}"
        )
    if DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts:
        raise NarrationPathError(
            f"Narration relative paths must not contain "
            f"`{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}`: {raw}"
        )
    if parts[0] != NARRATION_ROOT_SEGMENT:
        raise NarrationPathError(
            f"Narration path must be under `{NARRATION_ROOT_SEGMENT}/`: {raw}"
        )
    return "/".join(parts)


def assert_narration_relative_path(relative_path: str) -> str:
    return normalize_narration_relative_path(relative_path)


def resolve_narration_relative_path(project_root: Path, relative_path: str) -> Path:
    rel = normalize_narration_relative_path(relative_path)
    absolute = get_discovery_v2_root(project_root) / Path(*rel.split("/"))
    assert_path_is_under_discovery_v2(absolute, project_root)
    narration_root = get_narration_root(project_root).resolve()
    try:
        absolute.resolve().relative_to(narration_root)
    except ValueError as exc:
        raise NarrationPathError(f"Path is not under narration/: {absolute}") from exc
    return absolute


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise NarrationPathError(f"{name} is missing.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise NarrationPathError(f"Invalid {name}: {value}")
    if text.startswith("_otio"):
        raise NarrationPathError(f"Invalid {name}: {value}")


__all__ = [
    "NARRATION_ROOT_SEGMENT",
    "NarrationPathError",
    "assert_narration_relative_path",
    "get_narration_root",
    "narration_attempt_json_relative_path",
    "narration_audio_relative_path",
    "narration_latest_pause_plan_relative_path",
    "narration_latest_timeline_relative_path",
    "narration_latest_voice_run_relative_path",
    "narration_pause_plan_json_relative_path",
    "narration_report_relative_path",
    "narration_run_json_relative_path",
    "narration_temp_dir",
    "narration_timeline_json_relative_path",
    "narration_voice_profile_json_relative_path",
    "normalize_narration_relative_path",
    "resolve_narration_relative_path",
]
