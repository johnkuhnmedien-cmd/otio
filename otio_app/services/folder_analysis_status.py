"""Analyse-Status pro Asset-Ordner (Cache-Auswertung)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from otio_app.models import Project
from otio_app.services.asset_analyzer import _is_completed_analysis, _load_cached_media, _media_cache_path
from otio_app.services.media_utils import list_media_files


class FolderAnalysisState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    PENDING = "pending"
    EMPTY = "empty"


FOLDER_STATE_LABELS = {
    FolderAnalysisState.COMPLETE: "🟢 Fertig",
    FolderAnalysisState.PARTIAL: "🟡 Teilweise",
    FolderAnalysisState.PENDING: "⚪ Offen",
    FolderAnalysisState.EMPTY: "➖ Keine Medien",
}


def get_folder_analysis_state(project: Project, folder_name: str) -> FolderAnalysisState:
    """Ermittelt den Analyse-Status anhand des Medien-Caches."""
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)
    if not media_paths:
        return FolderAnalysisState.EMPTY

    completed = 0
    for media_path in media_paths:
        cache_file = _media_cache_path(project, folder_name, media_path)
        cached = _load_cached_media(cache_file)
        if cached is not None and _is_completed_analysis(cached):
            completed += 1

    if completed == 0:
        return FolderAnalysisState.PENDING
    if completed >= len(media_paths):
        return FolderAnalysisState.COMPLETE
    return FolderAnalysisState.PARTIAL


def format_folder_with_status(project: Project, folder_name: str) -> str:
    state = get_folder_analysis_state(project, folder_name)
    return f"{FOLDER_STATE_LABELS[state]} · {folder_name}"


def list_open_folder_names(project: Project, folder_names: list[str]) -> list[str]:
    """Ordner, die noch nicht vollständig analysiert sind (ohne leere)."""
    open_names: list[str] = []
    for folder_name in folder_names:
        state = get_folder_analysis_state(project, folder_name)
        if state in {FolderAnalysisState.PENDING, FolderAnalysisState.PARTIAL}:
            open_names.append(folder_name)
    return open_names


def count_folder_states(
    project: Project,
    folder_names: list[str],
) -> dict[FolderAnalysisState, int]:
    counts = {state: 0 for state in FolderAnalysisState}
    for folder_name in folder_names:
        counts[get_folder_analysis_state(project, folder_name)] += 1
    return counts
