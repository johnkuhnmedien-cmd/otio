"""Inventar aus pro-Ordner-JSON, Legacy-Datei und Cache laden."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, get_inventory_dir, safe_folder_slug
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.manual_folder_completion import is_manually_complete
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    is_successfully_analyzed,
    load_cached_media,
    load_cached_media_for_asset,
    migrate_legacy_per_asset_cache_folder,
    save_cached_media,
    scan_folder_cache_assets,
)


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
    """Vergleicht nur lokale Original-Assets im Top-Level-Ordner.

    Supplement-Assets liegen in `<Ordner>/_supplemental/<provider>/` bzw. unter
    `cut_plan/supplement_assets/` und werden von `discover_folder_media_paths`
    (bewusst nicht-rekursiver Top-Level-Scan) nicht erfasst. Ohne diesen
    Ausschluss wurde ein gespeichertes Inventar mit Supplement-Assets HIER
    IMMER als "nicht mehr aktuell" erkannt und verworfen — mit der Folge,
    dass build_edit_plan() und die Stale-Hash-Prüfung unterschiedliche,
    inkonsistente Inventar-Stände lasen. Das erzeugte sofort nach einem
    frischen, korrekten Schnittplan-Rebuild einen falschen
    "Inventory changed"-Fehler.
    """
    from otio_app.services.cut_plan_inventory_bridge import is_external_inventory_media_path

    current = sorted(
        str(path) for path in media_paths if not is_external_inventory_media_path(path)
    )
    saved = sorted(
        path for path in item.media_files if not is_external_inventory_media_path(path)
    )
    return current == saved


def folder_inventory_is_complete(item: AssetFolderAnalysis) -> bool:
    if not item.assets:
        return False
    return all(is_successfully_analyzed(asset) for asset in item.assets)


def delete_folder_inventory(project: Project, folder_name: str) -> None:
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def folder_is_green(project: Project, folder_name: str) -> bool:
    """Grün = alle Assets erfolgreich analysiert oder manuell freigegeben."""
    media_paths = discover_folder_media_paths(project, folder_name)
    if not media_paths:
        return False
    if folder_is_fully_analyzed(project, folder_name):
        return True
    return is_manually_complete(project, folder_name)


def sync_folder_inventory_with_status(project: Project, folder_name: str) -> bool:
    """Grün → Inventory-JSON erstellen/aktualisieren. Nicht grün → JSON entfernen."""
    media_paths = discover_folder_media_paths(project, folder_name)
    if not media_paths:
        delete_folder_inventory(project, folder_name)
        return False

    auto_complete = folder_is_fully_analyzed(project, folder_name)
    manual_complete = is_manually_complete(project, folder_name)

    if not auto_complete and not manual_complete:
        delete_folder_inventory(project, folder_name)
        return False

    allow_partial = manual_complete and not auto_complete
    item, _error = materialize_folder_inventory_from_cache(
        project,
        folder_name,
        allow_partial=allow_partial,
    )
    return item is not None


def remove_stale_folder_inventory(
    project: Project,
    folder_name: str,
    media_paths: list[Path] | None = None,
) -> None:
    """Entfernt Inventory-JSON, wenn der Ordner nicht grün ist."""
    if folder_is_green(project, folder_name):
        return
    delete_folder_inventory(project, folder_name)


def should_skip_folder_analysis(
    project: Project,
    folder_name: str,
    media_paths: list[Path],
) -> AssetFolderAnalysis | None:
    """Liefert Inventar nur wenn alle Medien erfolgreich analysiert sind."""
    if not folder_is_fully_analyzed(project, folder_name):
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
    media_paths = discover_folder_media_paths(project, folder_name)
    indexed_cache = _cached_assets_by_filename(project, folder_name)

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

    # Bestehende Supplement-/Cut-Plan-Einträge nicht verwerfen.
    from otio_app.services.cut_plan_inventory_bridge import is_external_inventory_media_path

    previous = load_folder_inventory_file(get_folder_inventory_path(project.work_dir_path, folder_name))
    if previous is not None:
        primary_paths = {asset.path for asset in assets}
        extras = [
            asset
            for asset in previous.assets
            if is_external_inventory_media_path(asset.path) and asset.path not in primary_paths
        ]
        if extras:
            merged_assets = list(assets) + extras
            merged_media = list(item.media_files)
            for asset in extras:
                if asset.path not in merged_media:
                    merged_media.append(asset.path)
            item = item.model_copy(
                update={
                    "assets": merged_assets,
                    "media_files": merged_media,
                    "frames_used": [frame for asset in merged_assets for frame in asset.frames_used],
                    "description": _folder_summary_from_assets(merged_assets),
                }
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
        out_path = get_folder_inventory_path(project.work_dir_path, folder_name)
        existed_before = out_path.is_file()
        media_count = len(discover_folder_media_paths(project, folder_name))
        cache_count = len(scan_folder_cache_assets(project, folder_name))

        created_now = sync_folder_inventory_with_status(project, folder_name)
        if out_path.is_file() and not existed_before:
            created.append(folder_name)
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="created",
                    detail=f"Neu erstellt (Ordner grün): `{out_path.name}`",
                    cache_files=cache_count,
                    media_files=media_count,
                )
            )
        elif out_path.is_file():
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="exists",
                    detail=f"Vorhanden (Ordner grün): `{out_path.name}`",
                    cache_files=cache_count,
                    media_files=media_count,
                )
            )
        else:
            detail = "Kein Inventar — Ordner noch nicht grün"
            if folder_is_green(project, folder_name):
                detail = "Ordner grün, aber Inventar konnte nicht erstellt werden"
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="incomplete",
                    detail=detail,
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
    """True, wenn alle ausgewählten Ordner grün sind und je eine Inventory-JSON haben."""
    if not project.selected_asset_subdirs:
        return False
    for folder_name in project.selected_asset_subdirs:
        if not folder_is_green(project, folder_name):
            return False
        sync_folder_inventory_with_status(project, folder_name)
        if not get_folder_inventory_path(project.work_dir_path, folder_name).is_file():
            return False
    return True


def load_folder_inventory(project: Project, folder_name: str) -> AssetFolderAnalysis:
    """Lädt Ordner-Inventar aus pro-Ordner-JSON oder dem Medien-Cache."""
    migrate_legacy_inventory(project)

    folder_path = project.project_root_path / folder_name
    media_paths = discover_folder_media_paths(project, folder_name)

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


def folder_has_usable_inventory_data(project: Project, folder_name: str) -> bool:
    """True, wenn für diesen Ordner mindestens ein erfolgreich analysiertes Asset
    vorliegt — aus der fertigen Inventar-JSON ODER (Fallback) direkt aus dem
    Analyse-Cache, via load_folder_inventory().

    Bewusst NICHT dasselbe wie folder_is_green()/get_folder_inventory_path(...).
    is_file(): Diese Datei wird von sync_folder_inventory_with_status() wieder
    gelöscht, sobald auch nur EIN einzelnes Asset im Ordner nicht als vollständig
    analysiert gilt (z. B. nach Clean-Media-Umbenennungen) — obwohl die
    Dramaturgie-Planung selbst (build_and_save_folder_inventory_summaries) genau
    denselben Cache-Fallback nutzt und daher KEINEN "grünen" Ordner braucht.
    Nur diese lockerere Prüfung sollte für Bereit-Zustände der "Projekt ohne
    Voice-Over"-Pipeline verwendet werden, nicht die strikte Datei-Prüfung."""
    analysis = load_folder_inventory(project, folder_name)
    return any(is_successfully_analyzed(asset) for asset in analysis.assets)
