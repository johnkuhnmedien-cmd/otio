"""Inventar aus JSON und Cache laden."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.models import Project
from otio_app.services.asset_analyzer import _load_cached_media, _media_cache_path
from otio_app.services.media_utils import list_media_files


def load_inventory_document(project: Project) -> InventoryDocument | None:
    path = project.inventory_path
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return InventoryDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def load_folder_inventory(project: Project, folder_name: str) -> AssetFolderAnalysis:
    """Lädt Ordner-Inventar aus inventory.json oder dem Medien-Cache."""
    document = load_inventory_document(project)
    if document is not None:
        for item in document.items:
            if item.folder == folder_name:
                return item

    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)
    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        cached = _load_cached_media(_media_cache_path(project, folder_name, media_path))
        if cached is not None:
            assets.append(cached)
        else:
            assets.append(AssetMediaAnalysis(path=str(media_path)))

    return AssetFolderAnalysis(
        folder=folder_name,
        media_files=[asset.path for asset in assets],
        assets=assets,
    )
