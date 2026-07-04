"""Medien-Cache für Asset-Analysen (pro Datei unter _otio/cache/inventory/)."""

from __future__ import annotations

import json
import re
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

_PREFERRED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")
_PREFERRED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff")
_NUMBERED_ASSET_STEM_RE = re.compile(r"^(.+_Asset)(\d+)$", re.IGNORECASE)


def media_cache_path(project: Project, folder_name: str, media_path: Path) -> Path:
    """Pfad zur Analyse-JSON — immer slug-basiert (iCloud-/Leerzeichen-tolerant)."""
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / safe_folder_slug(folder_name)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    canonical_name = f"{safe_folder_slug(media_path.stem)}{media_path.suffix.lower()}"
    return cache_dir / f"{safe_folder_slug(canonical_name)}.json"


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


def media_stem_slug(media_path: Path) -> str:
    """Einheitlicher Slug für Medien-Dateien (Cache, Frames, iCloud-Namen)."""
    return safe_folder_slug(media_path.stem).casefold()


def cache_json_stem_slug(cache_filename: str) -> str:
    """Slug aus einem Cache-Dateinamen (z. B. Florida_Keys_Asset15.mp4.json)."""
    base = cache_filename[:-5] if cache_filename.endswith(".json") else cache_filename
    return safe_folder_slug(Path(base).stem).casefold()


def all_cache_paths_for_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> list[Path]:
    """Alle möglichen Cache-Pfade für ein Medium (exakt + Slug-Match)."""
    seen: set[str] = set()
    ordered: list[Path] = []
    for cache_file in cached_media_paths_for_asset(project, folder_name, media_path):
        key = str(cache_file)
        if key not in seen:
            seen.add(key)
            ordered.append(cache_file)

    target_slug = media_stem_slug(media_path)
    for cache_dir in list_cache_dirs_for_folder(project, folder_name):
        if not cache_dir.is_dir():
            continue
        try:
            cache_files = sorted(cache_dir.glob("*.json"))
        except OSError:
            continue
        for cache_file in cache_files:
            if cache_json_stem_slug(cache_file.name) != target_slug:
                continue
            key = str(cache_file)
            if key not in seen:
                seen.add(key)
                ordered.append(cache_file)
    return ordered


def load_cached_media_for_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Optional[AssetMediaAnalysis]:
    """Lädt Cache-Eintrag aus neuem oder altem Speicherort."""
    for cache_file in all_cache_paths_for_asset(project, folder_name, media_path):
        cached = load_cached_media(cache_file)
        if cached is not None:
            return cached
    return None


def has_successful_asset_cache(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> bool:
    """True, wenn für dieses Medium eine erfolgreiche Analyse-JSON existiert."""
    cached = load_cached_media_for_asset(project, folder_name, media_path)
    return cached is not None and is_successfully_analyzed(cached)


def list_assets_missing_successful_cache(
    project: Project,
    folder_name: str,
) -> list[Path]:
    """Medien ohne gültige Analyse-JSON — diese müssen (neu) analysiert werden."""
    return [
        media_path
        for media_path in discover_folder_media_paths(project, folder_name)
        if not has_successful_asset_cache(project, folder_name, media_path)
    ]


def _prefer_discovered_media_path(current: Path, candidate: Path) -> Path:
    try:
        candidate_is_file = candidate.is_file()
        current_is_file = current.is_file()
    except OSError:
        return current
    if candidate_is_file and not current_is_file:
        return candidate
    if current_is_file and not candidate_is_file:
        return current
    if len(candidate.name) < len(current.name):
        return candidate
    return current


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


def _merge_media_path(
    by_slug: dict[str, Path],
    folder_path: Path,
    media_path: Path,
) -> None:
    slug = media_stem_slug(media_path)
    resolved = folder_path / media_path.name if media_path.parent != folder_path else media_path
    existing = by_slug.get(slug)
    if existing is None:
        by_slug[slug] = resolved
        return
    by_slug[slug] = _prefer_discovered_media_path(existing, resolved)


def _discover_gaps_from_cache_dir(
    project: Project,
    folder_name: str,
    folder_path: Path,
    by_slug: dict[str, Path],
) -> None:
    """Findet Lücken wie Asset15, wenn Asset14+Asset16-JSONs existieren."""
    prefixes: dict[str, set[int]] = {}
    for cache_dir in list_cache_dirs_for_folder(project, folder_name):
        if not cache_dir.is_dir():
            continue
        try:
            cache_files = sorted(cache_dir.glob("*.json"))
        except OSError:
            continue
        for cache_file in cache_files:
            media_stem = Path(cache_file.name[:-5]).stem
            match = _NUMBERED_ASSET_STEM_RE.match(safe_folder_slug(media_stem))
            if match is None:
                continue
            prefixes.setdefault(match.group(1), set()).add(int(match.group(2)))

    for prefix, numbers in prefixes.items():
        if len(numbers) < 2:
            continue
        for number in range(min(numbers), max(numbers) + 1):
            if number in numbers:
                continue
            stem = f"{prefix}{number}"
            candidate = resolve_media_path_for_slug(folder_path, stem)
            _merge_media_path(by_slug, folder_path, candidate)


def _discover_media_from_frame_dirs(
    project: Project,
    folder_name: str,
    by_slug: dict[str, Path],
) -> None:
    """Ergänzt Medien, für die bereits Frame-Ordner existieren (z. B. nach Teilanalyse)."""
    folder_path = project.project_root_path / folder_name
    frames_root = project.work_dir_path / "frames" / safe_folder_slug(folder_name)
    if not frames_root.is_dir():
        return

    slug_to_path = {media_stem_slug(path): path for path in list_media_files(folder_path)}
    slug_to_path.update({media_stem_slug(path): path for path in by_slug.values()})

    try:
        frame_dirs = sorted(frames_root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return

    for frame_dir in frame_dirs:
        if not frame_dir.is_dir():
            continue
        slug = frame_dir.name.casefold()
        matched = slug_to_path.get(slug)
        if matched is not None:
            _merge_media_path(by_slug, folder_path, matched)
            continue
        candidate = resolve_media_path_for_slug(folder_path, frame_dir.name)
        _merge_media_path(by_slug, folder_path, candidate)


def discover_folder_media_paths(project: Project, folder_name: str) -> list[Path]:
    """Medien im Ordner: Dateisystem plus Cache und Frame-Arbeit vereinen."""
    folder_path = project.project_root_path / folder_name
    by_slug: dict[str, Path] = {}

    for media_path in list_media_files(folder_path):
        _merge_media_path(by_slug, folder_path, media_path)

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
            _merge_media_path(by_slug, folder_path, folder_path / name)

    _discover_gaps_from_cache_dir(project, folder_name, folder_path, by_slug)
    _discover_media_from_frame_dirs(project, folder_name, by_slug)

    return sorted(by_slug.values(), key=lambda path: path.name.casefold())


def media_file_is_accessible(media_path: Path) -> tuple[bool, str | None]:
    """Prüft, ob eine Mediendatei lokal lesbar ist (iCloud-Check)."""
    try:
        if not media_path.is_file():
            return False, (
                f"Mediendatei nicht lokal verfügbar: `{media_path.name}` "
                "(iCloud — bitte im Finder laden)"
            )
        with media_path.open("rb") as handle:
            handle.read(1)
        return True, None
    except OSError as exc:
        return False, f"Mediendatei nicht lesbar: `{media_path.name}` — {exc}"


def resolve_media_path_for_slug(folder_path: Path, slug: str) -> Path:
    """Findet eine Mediendatei zu einem Frame-/Cache-Slug im Ordner."""
    slug_cf = slug.casefold()
    for media_path in list_media_files(folder_path):
        if media_stem_slug(media_path) == slug_cf:
            return media_path
    for ext in _PREFERRED_VIDEO_EXTENSIONS + _PREFERRED_IMAGE_EXTENSIONS:
        if ext not in MEDIA_EXTENSIONS:
            continue
        candidate = folder_path / f"{slug}{ext}"
        if media_stem_slug(candidate) == slug_cf:
            return candidate
    return folder_path / f"{slug}.mp4"


def resolve_media_for_analysis(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Path:
    """Ermittelt den echten Mediendatei-Pfad (Slug-Match im Ordner)."""
    folder_path = project.project_root_path / folder_name
    slug = safe_folder_slug(media_path.stem)
    return resolve_media_path_for_slug(folder_path, slug)


def save_cached_media(cache_file: Path, entry: AssetMediaAnalysis) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(entry.model_dump_json(indent=2), encoding="utf-8")


def _save_cached_media_safe(
    cache_file: Path,
    entry: AssetMediaAnalysis,
) -> AssetMediaAnalysis:
    """Speichert Cache-JSON; bei Schreibfehler bleibt der Fehler im Eintrag."""
    try:
        save_cached_media(cache_file, entry)
        return entry
    except OSError as exc:
        entry.error = f"Cache konnte nicht geschrieben werden: {exc}"
        entry.description = ""
        return entry
