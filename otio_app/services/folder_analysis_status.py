"""Analyse-Status pro Asset-Ordner."""

from __future__ import annotations

from enum import Enum

from otio_app.models import Project
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.inventory_loader import sync_folder_inventory_with_status
from otio_app.services.manual_folder_completion import is_manually_complete
from otio_app.services.media_inventory_cache import (
    is_successfully_analyzed,
    load_cached_media_for_asset,
)
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


def is_manual_complete_only(project: Project, folder_name: str) -> bool:
    return is_manually_complete(project, folder_name) and not folder_is_fully_analyzed(
        project, folder_name
    )


def get_folder_analysis_state(project: Project, folder_name: str) -> FolderAnalysisState:
    """Grün bei vollständiger Analyse oder manueller Freigabe; dann Inventory-JSON sync."""
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)
    if not media_paths:
        return FolderAnalysisState.EMPTY

    if folder_is_fully_analyzed(project, folder_name):
        sync_folder_inventory_with_status(project, folder_name)
        return FolderAnalysisState.COMPLETE

    if is_manually_complete(project, folder_name):
        sync_folder_inventory_with_status(project, folder_name)
        return FolderAnalysisState.COMPLETE

    sync_folder_inventory_with_status(project, folder_name)

    completed = 0
    for media_path in media_paths:
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is not None and is_successfully_analyzed(cached):
            completed += 1

    if completed == 0:
        return FolderAnalysisState.PENDING
    return FolderAnalysisState.PARTIAL


def format_folder_with_status(project: Project, folder_name: str) -> str:
    state = get_folder_analysis_state(project, folder_name)
    label = FOLDER_STATE_LABELS[state]
    if state == FolderAnalysisState.COMPLETE and is_manual_complete_only(project, folder_name):
        label = "🟢 Fertig (manuell)"
    return f"{label} · {folder_name}"


def list_open_folder_names(project: Project, folder_names: list[str]) -> list[str]:
    """Ordner, die noch nicht vollständig oder manuell freigegeben sind."""
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
