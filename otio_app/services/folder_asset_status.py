"""Status einzelner Medien-Dateien innerhalb eines Asset-Ordners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_inventory_cache import (
    is_completed_analysis,
    load_cached_media_for_asset,
)
from otio_app.services.media_utils import list_media_files


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
    folder_path = project.project_root_path / folder_name
    statuses: list[AssetAnalysisStatus] = []
    for media_path in list_media_files(folder_path):
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is None:
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.MISSING,
                )
            )
            continue
        if is_completed_analysis(cached) and cached.description.strip():
            statuses.append(
                AssetAnalysisStatus(
                    path=media_path,
                    state=AssetAnalysisState.COMPLETE,
                    description=cached.description,
                )
            )
            continue
        if is_completed_analysis(cached) and cached.error:
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
