"""Inventar aus pro-Ordner-JSON, Legacy-Datei und Cache laden."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, get_inventory_dir, safe_folder_slug
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.manual_folder_completion import is_manually_complete
from otio_app.services.media_inventory_cache import (
    is_successfully_analyzed,
    load_cached_media,
    load_cached_media_for_asset,
    migrate_legacy_per_asset_cache_folder,
    save_cached_media,
    scan_folder_cache_assets,
)
from otio_app.services.media_utils import list_media_files


@dataclass(frozen=True)
class FolderInventorySyncStatus:
    folder: str
    state: str
    detail: str
    cache_files: int = 0
    media_files: int = 0


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
    return all(is_successfully_analyzed(asset) for asset in item.assets)


def remove_stale_folder_inventory(
    project: Project,
    folder_name: str,
    media_paths: list[Path] | None = None,
) -> None:
    """Entfernt gebündelte JSON, wenn nicht alle Medien erfolgreich analysiert sind."""
    if is_manually_complete(project, folder_name):
        return
    if media_paths is None:
        media_paths = list_media_files(project.project_root_path / folder_name)
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    if not path.is_file():
        return
    item = load_folder_inventory_file(path)
    if item is None:
        return
    if not media_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    if not folder_inventory_matches_media(item, media_paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    if not folder_inventory_is_complete(item):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    if not folder_is_fully_analyzed(project, folder_name):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def should_skip_folder_analysis(
    project: Project,
    folder_name: str,
    media_paths: list[Path],
) -> AssetFolderAnalysis | None:
    """Liefert Inventar nur wenn alle Medien erfolgreich analysiert sind."""
    if not folder_is_fully_analyzed(project, folder_name):
        remove_stale_folder_inventory(project, folder_name, media_paths)
        return None

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


def _cached_assets_by_filename(
    project: Project,
    folder_name: str,
) -> dict[str, AssetMediaAnalysis]:
    indexed: dict[str, AssetMediaAnalysis] = {}
    for asset in scan_folder_cache_assets(project, folder_name):
        indexed[Path(asset.path).name.casefold()] = asset
    return indexed


def _resolve_cached_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
    indexed_cache: dict[str, AssetMediaAnalysis],
) -> AssetMediaAnalysis | None:
    cached = load_cached_media_for_asset(project, folder_name, media_path)
    if cached is not None:
        return cached
    return indexed_cache.get(media_path.name.casefold())


def materialize_folder_inventory_from_cache(
    project: Project,
    folder_name: str,
    *,
    allow_partial: bool = False,
) -> tuple[AssetFolderAnalysis | None, str | None]:
    """Erstellt eine Ordner-JSON aus dem Medien-Cache (optional auch unvollständig)."""
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)
    indexed_cache = _cached_assets_by_filename(project, folder_name)

    if not media_paths and indexed_cache:
        media_paths = sorted(
            {Path(asset.path) for asset in indexed_cache.values()},
            key=lambda path: path.name.casefold(),
        )

    if not media_paths:
        if indexed_cache:
            return None, "Medienordner leer/unlesbar, Cache vorhanden — Ordner im Finder lokal laden."
        return None, "Keine Medien und kein Cache gefunden."

    existing = should_skip_folder_analysis(project, folder_name, media_paths)
    if existing is not None:
        return existing, None

    migrate_legacy_per_asset_cache_folder(project, folder_name)
    indexed_cache = _cached_assets_by_filename(project, folder_name)

    assets: list[AssetMediaAnalysis] = []
    missing: list[str] = []
    for media_path in media_paths:
        cached = _resolve_cached_asset(project, folder_name, media_path, indexed_cache)
        if cached is None or not is_successfully_analyzed(cached):
            missing.append(media_path.name)
            continue
        assets.append(cached)

    if missing and not allow_partial:
        return (
            None,
            f"{len(assets)}/{len(media_paths)} Assets im Cache — fehlt: {', '.join(missing[:5])}"
            + (" …" if len(missing) > 5 else ""),
        )

    if not assets:
        return None, "Kein analysierter Cache vorhanden."

    saved_media_paths = (
        media_paths
        if not missing
        else [path for path in media_paths if path.name not in missing]
    )
    item = AssetFolderAnalysis(
        folder=folder_name,
        media_files=[str(media_path) for media_path in saved_media_paths],
        assets=assets,
        frames_used=[frame for asset in assets for frame in asset.frames_used],
        description=_folder_summary_from_assets(assets),
    )
    try:
        save_folder_inventory(
            get_folder_inventory_path(project.work_dir_path, folder_name),
            item,
        )
    except OSError as exc:
        return None, f"Schreiben fehlgeschlagen: {exc}"
    return item, None


def sync_folder_inventories_from_cache(
    project: Project,
    folder_names: list[str] | None = None,
) -> tuple[list[str], list[FolderInventorySyncStatus]]:
    """Baut fehlende Ordner-JSONs auf. Liefert neu erzeugte Ordner und Status je Ordner."""
    migrate_legacy_inventory(project)

    targets = folder_names if folder_names is not None else project.asset_subdir_names
    created: list[str] = []
    statuses: list[FolderInventorySyncStatus] = []

    for folder_name in targets:
        migrate_legacy_per_asset_cache_folder(project, folder_name)
        media_paths = list_media_files(project.project_root_path / folder_name)
        remove_stale_folder_inventory(project, folder_name, media_paths)
        out_path = get_folder_inventory_path(project.work_dir_path, folder_name)
        existed_before = out_path.is_file()
        media_count = len(list_media_files(project.project_root_path / folder_name))
        cache_count = len(scan_folder_cache_assets(project, folder_name))

        item, error = materialize_folder_inventory_from_cache(project, folder_name)
        if item is not None and not existed_before and out_path.is_file():
            created.append(folder_name)
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="created",
                    detail=f"Neu erstellt: `{out_path.name}`",
                    cache_files=cache_count,
                    media_files=media_count or len(item.assets),
                )
            )
        elif item is not None and existed_before:
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="exists",
                    detail=f"Vorhanden: `{out_path.name}`",
                    cache_files=cache_count,
                    media_files=media_count or len(item.assets),
                )
            )
        else:
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="incomplete",
                    detail=error or "Unvollständig",
                    cache_files=cache_count,
                    media_files=media_count,
                )
            )

    return created, statuses


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
        if not folder_inventory_is_complete(item):
            continue
        for asset in item.assets:
            if not asset.path or not is_successfully_analyzed(asset):
                continue
            media_path = Path(asset.path)
            cache_path = (
                project.work_dir_path
                / "cache"
                / "inventory"
                / safe_folder_slug(item.folder)
                / f"{safe_folder_slug(media_path.name)}.json"
            )
            try:
                save_cached_media(cache_path, asset)
            except OSError:
                continue
        out_path = get_folder_inventory_path(project.work_dir_path, item.folder)
        if not out_path.is_file():
            try:
                save_folder_inventory(out_path, item)
            except OSError:
                continue


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
    """True, wenn alle ausgewählten Asset-Ordner fertig oder manuell freigegeben sind."""
    if not project.selected_asset_subdirs:
        return False
    for folder_name in project.selected_asset_subdirs:
        if folder_is_fully_analyzed(project, folder_name):
            continue
        if is_manually_complete(project, folder_name):
            continue
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

    indexed_cache = _cached_assets_by_filename(project, folder_name)
    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        cached = _resolve_cached_asset(project, folder_name, media_path, indexed_cache)
        if cached is not None:
            assets.append(cached)
        else:
            assets.append(AssetMediaAnalysis(path=str(media_path)))

    return AssetFolderAnalysis(
        folder=folder_name,
        media_files=[asset.path for asset in assets],
        assets=assets,
    )
