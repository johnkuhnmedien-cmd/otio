"""Inventar aus pro-Ordner-JSON, Legacy-Datei und Cache laden."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDocument,
    is_supplement_asset,
    supplement_asset_paths,
)
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
    scan_folder_supplement_cache_assets,
)


@dataclass(frozen=True)
class FolderInventorySyncStatus:
    folder: str
    state: str
    detail: str
    cache_files: int = 0
    media_files: int = 0


def is_canonical_folder_inventory_path(path: Path) -> bool:
    """True nur für ``{folder}.json`` — nicht Slim/Delta/Nebenartefakte."""
    name = path.name
    if not name.endswith(".json"):
        return False
    if name.endswith(".slim.json"):
        return False
    if name.endswith(".inventory_delta.json"):
        return False
    return True


def load_folder_inventory_file(path: Path) -> AssetFolderAnalysis | None:
    """Lädt eine pro-Ordner-Inventar-JSON oder None bei Fehler."""
    if not path.is_file():
        return None
    # Slim/Delta niemals als kanonisches Inventar parsen (sonst Validierungs-
    # fehler → unlink und die Ableitung ist weg).
    if not is_canonical_folder_inventory_path(path):
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
    # Abgeleitete Slim-Sicht für LLM/externe Nutzung — bricht keine Konsumenten.
    try:
        from otio_app.services.inventory_prompt_view import write_slim_folder_inventory

        write_slim_folder_inventory(path, item, probe_duration=True)
    except Exception:  # noqa: BLE001
        # Slim ist Best-Effort; kanonisches Inventar bleibt geschrieben.
        pass


def folder_inventory_matches_media(
    item: AssetFolderAnalysis,
    media_paths: list[Path],
) -> bool:
    """Vergleicht nur lokale Original-Assets im Top-Level-Ordner.

    Supplement-Assets liegen außerhalb des Medienordners — in
    `<Ordner>/_supplemental/<provider>/`, unter `cut_plan/supplement_assets/`
    oder (Enhanced-Funnel/Inbox) im geteilten Clean-Verzeichnis. Sie werden von
    `discover_folder_media_paths` (bewusst nicht-rekursiver Top-Level-Scan)
    nicht erfasst. Ohne diesen Ausschluss wurde ein gespeichertes Inventar mit
    Supplement-Assets HIER IMMER als "nicht mehr aktuell" erkannt und verworfen
    — mit der Folge, dass build_edit_plan() und die Stale-Hash-Prüfung
    unterschiedliche, inkonsistente Inventar-Stände lasen. Das erzeugte sofort
    nach einem frischen, korrekten Schnittplan-Rebuild einen falschen
    "Inventory changed"-Fehler.

    Maßgeblich ist die Herkunft der Asset-Zeile (`asset_origin`), nicht der
    Pfad: Clean Media legt auch Originale außerhalb des Medienordners ab.
    """
    from otio_app.services.cut_plan_inventory_bridge import is_external_inventory_media_path

    supplement_paths = supplement_asset_paths(item)
    current = sorted(
        str(path) for path in media_paths if not is_external_inventory_media_path(path)
    )
    saved = sorted(
        path
        for path in item.media_files
        if not is_external_inventory_media_path(path)
        and path not in supplement_paths
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
    try:
        from otio_app.services.inventory_prompt_view import slim_inventory_path_for

        slim_inventory_path_for(path).unlink(missing_ok=True)
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
    """Schreibt Inventory-JSON (+ Slim), sobald mindestens ein Asset analysiert ist.

    Unvollständige Ordner bleiben gelb (z. B. Stock-Wasserzeichen), bekommen
    aber trotzdem kanonisches Inventar und Slim aus den erfolgreichen Caches.
    Ohne erfolgreiche Analyse wird vorhandenes Inventar entfernt.
    """
    media_paths = discover_folder_media_paths(project, folder_name)
    if not media_paths:
        delete_folder_inventory(project, folder_name)
        return False

    auto_complete = folder_is_fully_analyzed(project, folder_name)
    item, _error = materialize_folder_inventory_from_cache(
        project,
        folder_name,
        allow_partial=not auto_complete,
    )
    if item is None:
        delete_folder_inventory(project, folder_name)
        return False
    return True


def remove_stale_folder_inventory(
    project: Project,
    folder_name: str,
    media_paths: list[Path] | None = None,
) -> None:
    """Entfernt Inventory-JSON nur wenn kein erfolgreicher Analyse-Cache mehr da ist."""
    paths = (
        media_paths
        if media_paths is not None
        else discover_folder_media_paths(project, folder_name)
    )
    if not paths:
        delete_folder_inventory(project, folder_name)
        return
    for media_path in paths:
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is not None and is_successfully_analyzed(cached):
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


def _supplement_assets_to_preserve(
    project: Project,
    folder_name: str,
    primary_assets: list[AssetMediaAnalysis],
) -> list[AssetMediaAnalysis]:
    """Beschaffte Assets, die ein Rebuild aus dem Cache nicht verlieren darf.

    Zwei Quellen, damit ein einmal beschafftes Asset nicht an einer einzelnen
    Datei hängt: die vorherige Inventar-JSON und der Supplement-Cache. Letzterer
    holt Zeilen zurück, die eine ältere Programmversion bereits entfernt hat.
    """
    from otio_app.services.cut_plan_inventory_bridge import is_external_inventory_media_path

    primary_paths = {asset.path for asset in primary_assets}
    preserved: dict[str, AssetMediaAnalysis] = {}

    previous = load_folder_inventory_file(
        get_folder_inventory_path(project.work_dir_path, folder_name)
    )
    for asset in getattr(previous, "assets", None) or []:
        if asset.path in primary_paths:
            continue
        if is_supplement_asset(asset) or is_external_inventory_media_path(asset.path):
            preserved[asset.path] = asset

    for asset in scan_folder_supplement_cache_assets(project, folder_name):
        if asset.path in primary_paths:
            continue
        # Analysierter Cache schlägt eine ältere Inventarzeile.
        if asset.path in preserved and not is_successfully_analyzed(asset):
            continue
        if not Path(asset.path).is_file():
            continue
        preserved[asset.path] = asset

    return list(preserved.values())


def _with_preserved_supplements(
    project: Project,
    folder_name: str,
    item: AssetFolderAnalysis,
) -> AssetFolderAnalysis:
    """Ergänzt fehlende Supplement-Zeilen aus Inventar-Historie und Cache."""
    extras = _supplement_assets_to_preserve(project, folder_name, item.assets)
    if not extras:
        return item
    merged_assets = list(item.assets) + extras
    merged_media = list(item.media_files)
    for asset in extras:
        if asset.path not in merged_media:
            merged_media.append(asset.path)
    return item.model_copy(
        update={
            "assets": merged_assets,
            "media_files": merged_media,
            "frames_used": [frame for asset in merged_assets for frame in asset.frames_used],
            "description": _folder_summary_from_assets(merged_assets),
        }
    )


def _cache_is_newer_than_inventory(
    project: Project,
    folder_name: str,
    item: AssetFolderAnalysis,
    media_paths: list[Path],
    indexed_cache: dict[str, AssetMediaAnalysis],
) -> bool:
    """True, wenn der Analyse-Cache eine frischere Fassung hält als das Inventar.

    Ohne diese Prüfung bliebe eine Legacy-Zeile im Inventar stehen, obwohl der
    Ordner gerade neu analysiert wurde: ``should_skip_folder_analysis`` akzeptiert
    Legacy-Analysen als „erfolgreich", und die vorhandene JSON würde
    unverändert weiterverwendet. Der Cut-LLM läse dann weiter den alten Stand,
    obwohl die Analyse bezahlt wurde.
    """
    rows = {asset.path: asset for asset in item.assets or []}
    for media_path in media_paths:
        cached = _resolve_cached_asset(project, folder_name, media_path, indexed_cache)
        if cached is None:
            continue
        row = rows.get(cached.path)
        if row is None:
            return True
        if row.analysis_signature != cached.analysis_signature:
            return True
        if row.description != cached.description:
            return True
    return False


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
    if existing is not None and not _cache_is_newer_than_inventory(
        project, folder_name, existing, media_paths, indexed_cache
    ):
        restored = _with_preserved_supplements(project, folder_name, existing)
        if restored is not existing:
            try:
                save_folder_inventory(
                    get_folder_inventory_path(project.work_dir_path, folder_name),
                    restored,
                )
            except OSError as exc:
                return None, f"Schreiben fehlgeschlagen: {exc}"
        return restored, None

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

    item = _with_preserved_supplements(project, folder_name, item)

    try:
        save_folder_inventory(
            get_folder_inventory_path(project.work_dir_path, folder_name),
            item,
        )
    except OSError as exc:
        return None, f"Schreiben fehlgeschlagen: {exc}"
    return item, None


def probe_folder_inventory_statuses(
    project: Project,
    folder_names: list[str] | None = None,
) -> list[FolderInventorySyncStatus]:
    """Read-only Inventar-Status für die UI — ohne Migrate/Sync/Schreiben."""
    from otio_app.services.media_inventory_cache import list_cache_dirs_for_folder
    from otio_app.services.media_utils import list_media_files

    targets = folder_names if folder_names is not None else project.asset_subdir_names
    statuses: list[FolderInventorySyncStatus] = []
    for folder_name in targets:
        out_path = get_folder_inventory_path(project.work_dir_path, folder_name)
        folder_path = project.project_root_path / folder_name
        try:
            media_count = len(list_media_files(folder_path)) if folder_path.is_dir() else 0
        except OSError:
            media_count = 0
        cache_count = 0
        for cache_dir in list_cache_dirs_for_folder(project, folder_name):
            if not cache_dir.is_dir():
                continue
            try:
                cache_count += sum(1 for _ in cache_dir.glob("*.json"))
            except OSError:
                continue
        if out_path.is_file():
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="exists",
                    detail=f"Vorhanden: `{out_path.name}`",
                    cache_files=cache_count,
                    media_files=media_count,
                )
            )
        else:
            statuses.append(
                FolderInventorySyncStatus(
                    folder=folder_name,
                    state="incomplete",
                    detail="Kein Inventar — Button „aus Cache aufbauen“ nutzen",
                    cache_files=cache_count,
                    media_files=media_count,
                )
            )
    return statuses


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
            if not is_canonical_folder_inventory_path(json_path):
                continue
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
    return sorted(
        path
        for path in inventory_dir.glob("*.json")
        if is_canonical_folder_inventory_path(path)
    )


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
    Unvollständige Ordner schreiben trotzdem Inventory aus den erfolgreichen
    Caches; fehlt die Datei, reicht der Analyse-Cache als Fallback — denselben
    nutzt die Dramaturgie-Planung (build_and_save_folder_inventory_summaries).
    Nur diese lockerere Prüfung sollte für Bereit-Zustände der "Projekt ohne
    Voice-Over"-Pipeline verwendet werden, nicht die strikte Datei-Prüfung."""
    analysis = load_folder_inventory(project, folder_name)
    return any(is_successfully_analyzed(asset) for asset in analysis.assets)
