"""Analyse-Status pro Asset-Ordner."""

from __future__ import annotations

from enum import Enum

from otio_app.models import Project
from otio_app.services.manual_folder_completion import is_manually_complete
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    is_successfully_analyzed,
    load_cached_media_for_asset,
)


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
    from otio_app.services.folder_asset_status import folder_is_fully_analyzed

    return is_manually_complete(project, folder_name) and not folder_is_fully_analyzed(
        project, folder_name
    )


def get_folder_analysis_state(
    project: Project,
    folder_name: str,
    *,
    sync_inventory: bool = False,
) -> FolderAnalysisState:
    """Ordner-Status aus Medien + Cache.

    Standardmäßig read-only (keine Inventory-Schreib-/Lösch-Side-Effects).
    Mit ``sync_inventory=True`` wird bei Bedarf das Ordner-Inventar
    synchronisiert (Tests / explizite Sync-Pfade).
    """
    media_paths = discover_folder_media_paths(project, folder_name)
    if not media_paths:
        if sync_inventory:
            from otio_app.services.inventory_loader import sync_folder_inventory_with_status

            sync_folder_inventory_with_status(project, folder_name)
        return FolderAnalysisState.EMPTY

    completed = 0
    for media_path in media_paths:
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is not None and is_successfully_analyzed(cached):
            completed += 1

    if completed == len(media_paths):
        state = FolderAnalysisState.COMPLETE
    elif is_manually_complete(project, folder_name):
        state = FolderAnalysisState.COMPLETE
    elif completed == 0:
        state = FolderAnalysisState.PENDING
    else:
        state = FolderAnalysisState.PARTIAL

    if sync_inventory:
        from otio_app.services.inventory_loader import sync_folder_inventory_with_status

        sync_folder_inventory_with_status(project, folder_name)

    return state


def format_folder_with_status(
    project: Project,
    folder_name: str,
    *,
    state: FolderAnalysisState | None = None,
) -> str:
    resolved = state if state is not None else get_folder_analysis_state(project, folder_name)
    label = FOLDER_STATE_LABELS[resolved]
    # Nur Marker-Datei prüfen — kein erneuter Media/Cache-Scan für Labels.
    if resolved == FolderAnalysisState.COMPLETE and is_manually_complete(
        project, folder_name
    ):
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
    *,
    states: dict[str, FolderAnalysisState] | None = None,
) -> dict[FolderAnalysisState, int]:
    counts = {state: 0 for state in FolderAnalysisState}
    for folder_name in folder_names:
        if states is not None and folder_name in states:
            counts[states[folder_name]] += 1
        else:
            counts[get_folder_analysis_state(project, folder_name)] += 1
    return counts
