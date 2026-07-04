"""Inventar aus pro-Ordner-JSON, Legacy-Datei und Cache laden."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, get_inventory_dir
from otio_app.services.media_inventory_cache import (
    is_completed_analysis,
    load_cached_media,
    media_cache_path,
)
from otio_app.services.media_utils import list_media_files


def load_folder_inventory_file(path: Path) -> AssetFolderAnalysis | None:
    """Lädt eine pro-Ordner-Inventar-JSON oder None bei Fehler."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AssetFolderAnalysis.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def save_folder_inventory(path: Path, item: AssetFolderAnalysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(item.model_dump_json(indent=2), encoding="utf-8")


def folder_inventory_matches_media(
    item: AssetFolderAnalysis,
    media_paths: list[Path],
) -> bool:
    current = sorted(str(path) for path in media_paths)
    saved = sorted(item.media_files)
    return current == saved


def folder_inventory_is_complete(item: AssetFolderAnalysis) -> bool:
    if not item.assets:
        return False
    return all(is_completed_analysis(asset) for asset in item.assets)


def should_skip_folder_analysis(
    project: Project,
    folder_name: str,
    media_paths: list[Path],
) -> AssetFolderAnalysis | None:
    """Liefert vorhandenes Inventar, wenn der Ordner bereits vollständig analysiert ist."""
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    item = load_folder_inventory_file(path)
    if item is None:
        return None
    if item.folder != folder_name:
        return None
    if not folder_inventory_matches_media(item, media_paths):
        return None
    if not folder_inventory_is_complete(item):
        return None
    return item


def _folder_summary_from_assets(assets: list[AssetMediaAnalysis]) -> str:
    parts: list[str] = []
    for asset in assets:
        if asset.description:
            parts.append(f"{Path(asset.path).name}: {asset.description}")
    return "\n\n".join(parts)


def materialize_folder_inventory_from_cache(
    project: Project,
    folder_name: str,
) -> AssetFolderAnalysis | None:
    """Erstellt eine Ordner-JSON aus vollständigem Medien-Cache (ohne Gemini)."""
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)
    if not media_paths:
        return None

    existing = should_skip_folder_analysis(project, folder_name, media_paths)
    if existing is not None:
        return existing

    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        cached = load_cached_media(media_cache_path(project, folder_name, media_path))
        if cached is None or not is_completed_analysis(cached):
            return None
        assets.append(cached)

    item = AssetFolderAnalysis(
        folder=folder_name,
        media_files=[str(media_path) for media_path in media_paths],
        assets=assets,
        frames_used=[frame for asset in assets for frame in asset.frames_used],
        description=_folder_summary_from_assets(assets),
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder_name),
        item,
    )
    return item


def sync_folder_inventories_from_cache(
    project: Project,
    folder_names: list[str] | None = None,
) -> list[str]:
    """Baut fehlende Ordner-JSONs aus vollständigem Cache auf. Liefert neu erzeugte Ordner."""
    targets = folder_names if folder_names is not None else project.asset_subdir_names
    created: list[str] = []
    for folder_name in targets:
        out_path = get_folder_inventory_path(project.work_dir_path, folder_name)
        existed_before = out_path.is_file()
        if materialize_folder_inventory_from_cache(project, folder_name) is not None:
            if not existed_before:
                created.append(folder_name)
    return created


def migrate_legacy_inventory(project: Project) -> None:
    """Teilt eine alte zentrale inventory.json in pro-Ordner-Dateien auf."""
    legacy_path = project.inventory_path
    if not legacy_path.is_file():
        return

    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        document = InventoryDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return

    inventory_dir = get_inventory_dir(project.work_dir_path)
    inventory_dir.mkdir(parents=True, exist_ok=True)

    for item in document.items:
        out_path = get_folder_inventory_path(project.work_dir_path, item.folder)
        if not out_path.is_file():
            save_folder_inventory(out_path, item)


def load_inventory_document(project: Project) -> InventoryDocument | None:
    migrate_legacy_inventory(project)

    items: list[AssetFolderAnalysis] = []
    inventory_dir = get_inventory_dir(project.work_dir_path)
    if inventory_dir.is_dir():
        for json_path in sorted(inventory_dir.glob("*.json")):
            item = load_folder_inventory_file(json_path)
            if item is not None:
                items.append(item)

    if items:
        return InventoryDocument(project_id=project.id, items=items)

    legacy_path = project.inventory_path
    if not legacy_path.is_file():
        return None
    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        return InventoryDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def list_folder_inventory_paths(project: Project) -> list[Path]:
    migrate_legacy_inventory(project)
    inventory_dir = get_inventory_dir(project.work_dir_path)
    if not inventory_dir.is_dir():
        return []
    return sorted(inventory_dir.glob("*.json"))


def selected_folders_have_inventory(project: Project) -> bool:
    """True, wenn alle ausgewählten Asset-Ordner eine vollständige Inventar-JSON haben."""
    if not project.selected_asset_subdirs:
        return False
    for folder_name in project.selected_asset_subdirs:
        folder_path = project.project_root_path / folder_name
        media_paths = list_media_files(folder_path)
        if not media_paths:
            continue
        if should_skip_folder_analysis(project, folder_name, media_paths) is None:
            return False
    return True


def load_folder_inventory(project: Project, folder_name: str) -> AssetFolderAnalysis:
    """Lädt Ordner-Inventar aus pro-Ordner-JSON oder dem Medien-Cache."""
    migrate_legacy_inventory(project)

    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)

    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    item = load_folder_inventory_file(path)
    if item is not None and item.folder == folder_name:
        if not media_paths or folder_inventory_matches_media(item, media_paths):
            return item

    document = load_inventory_document(project)
    if document is not None:
        for folder_item in document.items:
            if folder_item.folder == folder_name:
                return folder_item

    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        cached = load_cached_media(media_cache_path(project, folder_name, media_path))
        if cached is not None:
            assets.append(cached)
        else:
            assets.append(AssetMediaAnalysis(path=str(media_path)))

    return AssetFolderAnalysis(
        folder=folder_name,
        media_files=[asset.path for asset in assets],
        assets=assets,
    )
