"""Status einzelner Medien-Dateien innerhalb eines Asset-Ordners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    is_successfully_analyzed,
    load_cached_media_for_asset,
)
from otio_app.services.media_utils import NO_ANALYZABLE_MEDIA_DESCRIPTION


class AssetAnalysisState(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class AssetAnalysisStatus:
    path: Path
    state: AssetAnalysisState
    description: str = ""
    error: str | None = None


def get_folder_asset_statuses(
    project: Project,
    folder_name: str,
) -> list[AssetAnalysisStatus]:
    statuses: list[AssetAnalysisStatus] = []
    for media_path in discover_folder_media_paths(project, folder_name):
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is None:
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.MISSING,
                )
            )
            continue
        if is_successfully_analyzed(cached):
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.COMPLETE,
                    description=cached.description,
                )
            )
            continue
        if cached.description.strip() == NO_ANALYZABLE_MEDIA_DESCRIPTION:
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.FAILED,
                    error=NO_ANALYZABLE_MEDIA_DESCRIPTION,
                )
            )
            continue
        if cached.error:
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.FAILED,
                    error=cached.error,
                )
            )
            continue
        statuses.append(
            AssetAnalysisStatus(
                path=media_path,
                state=AssetAnalysisState.MISSING,
                error=cached.error,
            )
        )
    return statuses


def list_missing_or_failed_assets(
    project: Project,
    folder_name: str,
) -> list[AssetAnalysisStatus]:
    return [
        status
        for status in get_folder_asset_statuses(project, folder_name)
        if status.state != AssetAnalysisState.COMPLETE
    ]


def folder_is_fully_analyzed(project: Project, folder_name: str) -> bool:
    """True, wenn jedes Medium im Ordner eine erfolgreiche Beschreibung hat."""
    media_paths = discover_folder_media_paths(project, folder_name)
    if not media_paths:
        return False
    for media_path in media_paths:
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is None or not is_successfully_analyzed(cached):
            return False
    return True
