"""Medien-Cache für Asset-Analysen (pro Datei unter _otio/cache/inventory/)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.project_layout import get_inventory_dir, safe_folder_slug
from otio_app.services.media_utils import (
    MEDIA_EXTENSIONS,
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    list_media_files,
)


def media_cache_path(project: Project, folder_name: str, media_path: Path) -> Path:
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / safe_folder_slug(folder_name)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{safe_folder_slug(media_path.name)}.json"


def legacy_per_asset_cache_path(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Path:
    """Alter Pfad: _otio/inventory/<Ordner>/<Datei>.json (vor cache/inventory)."""
    cache_dir = get_inventory_dir(project.work_dir_path) / safe_folder_slug(folder_name)
    return cache_dir / f"{safe_folder_slug(media_path.name)}.json"


def cached_media_paths_for_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> list[Path]:
    return [
        media_cache_path(project, folder_name, media_path),
        legacy_per_asset_cache_path(project, folder_name, media_path),
    ]


def load_cached_media(cache_file: Path) -> Optional[AssetMediaAnalysis]:
    """Lädt einen gültigen Cache-Eintrag oder None bei Fehler/kaputtem Cache."""
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        return AssetMediaAnalysis.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def load_cached_media_for_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Optional[AssetMediaAnalysis]:
    """Lädt Cache-Eintrag aus neuem oder altem Speicherort."""
    for cache_file in cached_media_paths_for_asset(project, folder_name, media_path):
        cached = load_cached_media(cache_file)
        if cached is not None:
            return cached
    return None


def migrate_legacy_per_asset_cache_file(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> None:
    """Verschiebt einen alten Cache-Eintrag nach _otio/cache/inventory/."""
    legacy_path = legacy_per_asset_cache_path(project, folder_name, media_path)
    if not legacy_path.is_file():
        return
    target_path = media_cache_path(project, folder_name, media_path)
    if target_path.is_file():
        try:
            legacy_path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    cached = load_cached_media(legacy_path)
    if cached is None:
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(legacy_path), str(target_path))
    except OSError:
        save_cached_media(target_path, cached)
        try:
            legacy_path.unlink(missing_ok=True)
        except OSError:
            pass


def list_cache_dirs_for_folder(project: Project, folder_name: str) -> list[Path]:
    slug = safe_folder_slug(folder_name)
    return [
        project.work_dir_path / "cache" / "inventory" / slug,
        get_inventory_dir(project.work_dir_path) / slug,
    ]


def scan_folder_cache_assets(
    project: Project,
    folder_name: str,
) -> list[AssetMediaAnalysis]:
    """Liest alle gültigen Cache-Einträge für einen Ordner (beide Layouts)."""
    indexed: dict[str, AssetMediaAnalysis] = {}
    for cache_dir in list_cache_dirs_for_folder(project, folder_name):
        if not cache_dir.is_dir():
            continue
        try:
            cache_files = sorted(cache_dir.glob("*.json"))
        except OSError:
            continue
        for cache_file in cache_files:
            cached = load_cached_media(cache_file)
            if cached is None or not is_completed_analysis(cached):
                continue
            key = Path(cached.path).name.casefold()
            indexed[key] = cached
    return list(indexed.values())


def migrate_legacy_per_asset_cache_folder(
    project: Project,
    folder_name: str,
) -> None:
    """Räumt alten Ordner _otio/inventory/<Ordner>/ auf (Dateien -> cache/inventory)."""
    legacy_dir = get_inventory_dir(project.work_dir_path) / safe_folder_slug(folder_name)
    if not legacy_dir.is_dir():
        return

    media_paths = discover_folder_media_paths(project, folder_name)
    if media_paths:
        for media_path in media_paths:
            migrate_legacy_per_asset_cache_file(project, folder_name, media_path)
    else:
        try:
            for cache_file in legacy_dir.glob("*.json"):
                cached = load_cached_media(cache_file)
                if cached is None:
                    continue
                media_path = Path(cached.path)
                if media_path.name:
                    migrate_legacy_per_asset_cache_file(project, folder_name, media_path)
        except OSError:
            return

    try:
        if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
            legacy_dir.rmdir()
    except OSError:
        pass


def is_completed_analysis(entry: AssetMediaAnalysis) -> bool:
    """True, wenn dieses Asset bereits analysiert wurde (Erfolg oder dokumentierter Fehler)."""
    if entry.description.strip():
        return True
    return bool(entry.error)


def is_successfully_analyzed(entry: AssetMediaAnalysis) -> bool:
    """True nur bei erfolgreicher Beschreibung (kein reiner Fehler-Eintrag)."""
    description = entry.description.strip()
    if not description:
        return False
    if description == NO_ANALYZABLE_MEDIA_DESCRIPTION:
        return False
    return True


def discover_folder_media_paths(project: Project, folder_name: str) -> list[Path]:
    """Medien im Ordner: Dateisystem plus Pfade aus dem Analyse-Cache vereinen."""
    folder_path = project.project_root_path / folder_name
    by_name: dict[str, Path] = {}

    for media_path in list_media_files(folder_path):
        by_name[media_path.name.casefold()] = media_path

    for cache_dir in list_cache_dirs_for_folder(project, folder_name):
        if not cache_dir.is_dir():
            continue
        try:
            cache_files = sorted(cache_dir.glob("*.json"))
        except OSError:
            continue
        for cache_file in cache_files:
            cached = load_cached_media(cache_file)
            if cached is None or not cached.path:
                continue
            name = Path(cached.path).name
            if not name or Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            key = name.casefold()
            if key not in by_name:
                by_name[key] = folder_path / name

    return sorted(by_name.values(), key=lambda path: path.name.casefold())


def save_cached_media(cache_file: Path, entry: AssetMediaAnalysis) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
