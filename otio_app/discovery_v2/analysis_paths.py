"""Pfadvertrag für Discovery-V2-Assetanalyse — nur unter ``_otio_v2/analysis/``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


ANALYSIS_ROOT_SEGMENT = "analysis"
WORKING_MEDIA_SEGMENT = "media/working"


class AnalysisPathError(ValueError):
    """Ungültiger Analysis-Pfad."""


def get_analysis_root(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / ANALYSIS_ROOT_SEGMENT


def analysis_runs_dir(project_root: Path) -> Path:
    return get_analysis_root(project_root) / "runs"


def analysis_manifests_dir(project_root: Path) -> Path:
    return get_analysis_root(project_root) / "manifests"


def analysis_temp_dir(project_root: Path, run_id: str) -> Path:
    _reject_unsafe_segment(run_id, "run_id")
    return get_analysis_root(project_root) / "temp" / run_id


def analysis_frames_dir(project_root: Path) -> Path:
    return get_analysis_root(project_root) / "frames"


def analysis_observations_dir(project_root: Path) -> Path:
    return get_analysis_root(project_root) / "observations"


def analysis_run_json_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{ANALYSIS_ROOT_SEGMENT}/runs/{run_id}.json"


def analysis_manifest_json_relative_path(analysis_identity_id: str) -> str:
    _reject_unsafe_segment(analysis_identity_id, "analysis_identity_id")
    return f"{ANALYSIS_ROOT_SEGMENT}/manifests/{analysis_identity_id}.json"


def analysis_temp_relative_path(run_id: str) -> str:
    _reject_unsafe_segment(run_id, "run_id")
    return f"{ANALYSIS_ROOT_SEGMENT}/temp/{run_id}"


def analysis_frames_relative_prefix() -> str:
    return f"{ANALYSIS_ROOT_SEGMENT}/frames"


def analysis_observations_relative_prefix() -> str:
    return f"{ANALYSIS_ROOT_SEGMENT}/observations"


def normalize_analysis_relative_path(relative_path: str) -> str:
    """Normalisiert und validiert einen relativen Analysis-Pfad (POSIX)."""
    if relative_path is None:
        raise AnalysisPathError("Analysis-Pfad fehlt.")
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise AnalysisPathError("Analysis-Pfad ist leer.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise AnalysisPathError(
            f"Absolute Pfade sind in Analysis-Artefakten verboten: {raw}"
        )
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise AnalysisPathError(f"Ungültiger Analysis-Pfad: {raw}")
    if ".." in parts:
        raise AnalysisPathError(f"Pfadausbruch verboten: {raw}")
    if DEFAULT_WORK_SUBDIR in parts:
        raise AnalysisPathError(
            f"Analysis-Pfade dürfen `{DEFAULT_WORK_SUBDIR}` nicht enthalten: {raw}"
        )
    # Kein doppeltes _otio_v2 und keine Working-Media-Vermischung.
    if parts.count(DEFAULT_DISCOVERY_V2_WORK_SUBDIR) > 0:
        raise AnalysisPathError(
            f"Analysis-Relative-Pfade dürfen `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}` "
            f"nicht enthalten: {raw}"
        )
    if parts[0] != ANALYSIS_ROOT_SEGMENT:
        raise AnalysisPathError(
            f"Analysis-Pfad muss unter `{ANALYSIS_ROOT_SEGMENT}/` liegen: {raw}"
        )
    joined = "/".join(parts)
    if joined.startswith(f"{ANALYSIS_ROOT_SEGMENT}/media/working") or (
        WORKING_MEDIA_SEGMENT in joined
    ):
        raise AnalysisPathError(
            f"Analysis-Pfad darf kein Working-Media-Pfad sein: {raw}"
        )
    return joined


def assert_analysis_relative_path(relative_path: str) -> str:
    return normalize_analysis_relative_path(relative_path)


def resolve_analysis_relative_path(project_root: Path, relative_path: str) -> Path:
    rel = normalize_analysis_relative_path(relative_path)
    # Relative Pfade sind unter _otio_v2 zu verstehen.
    absolute = get_discovery_v2_root(project_root) / Path(*rel.split("/"))
    assert_path_is_under_discovery_v2(absolute, project_root)
    analysis_root = get_analysis_root(project_root).resolve()
    try:
        absolute.resolve().relative_to(analysis_root)
    except ValueError as exc:
        raise AnalysisPathError(
            f"Pfad liegt nicht unter analysis/: {absolute}"
        ) from exc
    return absolute


def is_analysis_relative_path(relative_path: str) -> bool:
    try:
        normalize_analysis_relative_path(relative_path)
    except AnalysisPathError:
        return False
    return True


def is_valid_otio_media_relative_path(relative_path: str) -> bool:
    """Analysis-Artefakte sind niemals gültige OTIO-Medienpfade."""
    try:
        normalize_analysis_relative_path(relative_path)
    except AnalysisPathError:
        return True  # nicht-Analysis → hier nicht beurteilt
    return False


def assert_not_otio_media_path(relative_path: str) -> None:
    if is_analysis_relative_path(relative_path):
        raise AnalysisPathError(
            f"Analysis-Pfad darf nicht als OTIO-Medienpfad verwendet werden: "
            f"{relative_path}"
        )


def _reject_unsafe_segment(value: str, name: str) -> None:
    text = (value or "").strip()
    if not text:
        raise AnalysisPathError(f"{name} fehlt.")
    if "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        raise AnalysisPathError(f"Ungültiges {name}: {value}")
    if text.startswith("_otio"):
        raise AnalysisPathError(f"Ungültiges {name}: {value}")
