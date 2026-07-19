"""Path contract for Discovery V2 export artifacts under ``_otio_v2/export/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2, get_discovery_v2_root

EXPORT_ROOT_SEGMENT = "export"


class ExportPathError(ValueError):
    """Invalid export artifact path."""


def get_export_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / EXPORT_ROOT_SEGMENT


def editorial_approval_json_relative_path(approval_id: str) -> str:
    _reject_unsafe_segment(approval_id, "approval_id")
    return f"{EXPORT_ROOT_SEGMENT}/approvals/{approval_id}.json"


def export_validation_json_relative_path(report_id: str) -> str:
    _reject_unsafe_segment(report_id, "report_id")
    return f"{EXPORT_ROOT_SEGMENT}/validation/{report_id}.json"


def otio_export_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EXPORT_ROOT_SEGMENT}/otio/{run_id}/timeline.otio"


def export_manifest_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EXPORT_ROOT_SEGMENT}/manifests/{run_id}/export_manifest.json"


def export_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EXPORT_ROOT_SEGMENT}/runs/{run_id}.json"


def export_report_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{EXPORT_ROOT_SEGMENT}/reports/{run_id}.json"


def otio_reparse_json_relative_path(report_id: str) -> str:
    _reject_unsafe_segment(report_id, "report_id")
    return f"{EXPORT_ROOT_SEGMENT}/reparse/{report_id}.json"


def export_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_export_root(project_root) / "temp" / run_id


def latest_approval_relative_path() -> str:
    return f"{EXPORT_ROOT_SEGMENT}/latest_approval.json"


def latest_validation_relative_path() -> str:
    return f"{EXPORT_ROOT_SEGMENT}/latest_validation.json"


def latest_otio_export_relative_path() -> str:
    return f"{EXPORT_ROOT_SEGMENT}/latest_otio_export.json"


def latest_reparse_relative_path() -> str:
    return f"{EXPORT_ROOT_SEGMENT}/latest_reparse.json"


def normalize_export_relative_path(relative_path: str) -> str:
    if relative_path is None:
        raise ExportPathError("Export path is missing.")
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise ExportPathError("Export path is empty.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ExportPathError(f"Absolute export paths are forbidden: {raw}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise ExportPathError(f"Invalid export path: {raw}")
    if ".." in parts:
        raise ExportPathError(f"Path escape is forbidden: {raw}")
    if DEFAULT_WORK_SUBDIR in parts:
        raise ExportPathError(f"Export paths must not contain `{DEFAULT_WORK_SUBDIR}`: {raw}")
    if DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts:
        raise ExportPathError(
            f"Export relative paths must not contain `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}`: {raw}"
        )
    if parts[0] != EXPORT_ROOT_SEGMENT:
        raise ExportPathError(f"Export path must be under `{EXPORT_ROOT_SEGMENT}/`: {raw}")
    return "/".join(parts)


def assert_export_relative_path(relative_path: str) -> str:
    return normalize_export_relative_path(relative_path)


def resolve_export_relative_path(project_root: Path, relative_path: str) -> Path:
    rel = normalize_export_relative_path(relative_path)
    absolute = get_discovery_v2_root(project_root) / Path(*rel.split("/"))
    assert_path_is_under_discovery_v2(absolute, project_root)
    export_root = get_export_root(project_root).resolve()
    try:
        absolute.resolve().relative_to(export_root)
    except ValueError as exc:
        raise ExportPathError(f"Path is not under export/: {absolute}") from exc
    return absolute


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise ExportPathError(f"{name} is missing.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise ExportPathError(f"Invalid {name}: {value}")
    if text.startswith("_otio"):
        raise ExportPathError(f"Invalid {name}: {value}")


__all__ = [
    "EXPORT_ROOT_SEGMENT",
    "ExportPathError",
    "assert_export_relative_path",
    "editorial_approval_json_relative_path",
    "export_manifest_relative_path",
    "export_report_relative_path",
    "export_run_json_relative_path",
    "export_temp_dir",
    "export_validation_json_relative_path",
    "get_export_root",
    "latest_approval_relative_path",
    "latest_otio_export_relative_path",
    "latest_reparse_relative_path",
    "latest_validation_relative_path",
    "normalize_export_relative_path",
    "otio_export_relative_path",
    "otio_reparse_json_relative_path",
    "resolve_export_relative_path",
]
