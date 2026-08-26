"""Medien-Cache für Asset-Analysen (pro Datei unter _otio/cache/inventory/)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import AssetMediaAnalysis, is_supplement_asset
from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
from otio_app.models import Project
from otio_app.project_layout import get_inventory_dir, safe_folder_slug
from otio_app.services.clean_media import resolve_effective_media_path
from otio_app.services.media_utils import (
    MEDIA_EXTENSIONS,
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    list_media_files,
)

_PREFERRED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")
_PREFERRED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff")
_NUMBERED_ASSET_STEM_RE = re.compile(r"^(.+_Asset)(\d+)$", re.IGNORECASE)

#: Analyse-Scopes. Primary = Originale im Medienordner, Supplement = beschafftes
#: Material. Supplements liegen in einem Unterordner, damit die Ordner-Discovery
#: (die aus Cache- und Frame-Namen auf Top-Level-Medien schließt) sie nicht als
#: fehlende Originaldateien missversteht.
CACHE_SCOPE_PRIMARY = "primary"
CACHE_SCOPE_SUPPLEMENT = "supplement"
SUPPLEMENT_SCOPE_SUBDIR = "_supplements"


def scope_subdir(scope: str) -> str:
    return SUPPLEMENT_SCOPE_SUBDIR if scope == CACHE_SCOPE_SUPPLEMENT else ""


def media_cache_path(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> Path:
    """Pfad zur Analyse-JSON — immer slug-basiert (iCloud-/Leerzeichen-tolerant)."""
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / safe_folder_slug(folder_name)
    )
    subdir = scope_subdir(scope)
    if subdir:
        cache_dir = cache_dir / subdir
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
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> list[Path]:
    if scope == CACHE_SCOPE_SUPPLEMENT:
        # Supplements gab es vor dem Scope nicht als eigenen Cache — kein Legacy-Pfad.
        return [media_cache_path(project, folder_name, media_path, scope=scope)]
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


def numbered_asset_discovery_key(media_path: Path) -> str:
    """Gleicher Schlüssel für Asset12.mp4 und Asset00012.mov."""
    slug = media_stem_slug(media_path)
    match = _NUMBERED_ASSET_STEM_RE.match(slug)
    if match is None:
        return slug
    return f"{match.group(1).casefold()}{int(match.group(2))}"


def media_has_icloud_placeholder(media_path: Path) -> bool:
    """True wenn macOS eine iCloud-Platzhalterdatei für dieses Medium zeigt."""
    parent = media_path.parent
    name = media_path.name
    for candidate in (
        parent / f".{name}.icloud",
        parent / f"{name}.icloud",
    ):
        try:
            if candidate.is_file():
                return True
        except OSError:
            continue
    return False


def cache_json_stem_slug(cache_filename: str) -> str:
    """Slug aus einem Cache-Dateinamen (z. B. Florida_Keys_Asset15.mp4.json)."""
    base = cache_filename[:-5] if cache_filename.endswith(".json") else cache_filename
    return safe_folder_slug(Path(base).stem).casefold()


def all_cache_paths_for_asset(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> list[Path]:
    """Alle möglichen Cache-Pfade für ein Medium (exakt + Slug-Match)."""
    seen: set[str] = set()
    ordered: list[Path] = []
    for cache_file in cached_media_paths_for_asset(
        project, folder_name, media_path, scope=scope
    ):
        key = str(cache_file)
        if key not in seen:
            seen.add(key)
            ordered.append(cache_file)

    target_slug = media_stem_slug(media_path)
    for cache_dir in list_cache_dirs_for_folder(project, folder_name, scope=scope):
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
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> Optional[AssetMediaAnalysis]:
    """Lädt Cache-Eintrag aus neuem oder altem Speicherort."""
    for cache_file in all_cache_paths_for_asset(
        project, folder_name, media_path, scope=scope
    ):
        cached = load_cached_media(cache_file)
        if cached is not None:
            return cached
    return None


def has_successful_asset_cache(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    model: Optional[str] = None,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> bool:
    """True, wenn ein *aktueller* v3-Cache vorliegt (Skip bei Analyselauf).

    Legacy-Beschreibungen gelten hier nicht als erfolgreich — sie bleiben über
    ``is_successfully_analyzed`` / ``is_usable_asset_analysis`` anzeigbar.

    Freshness nutzt denselben effektiven Medienpfad wie die Analyse
    (Clean-Media falls vorhanden, sonst Original).
    """
    from otio_app.services.asset_analysis_signature import is_current_asset_analysis
    from otio_app.services.gemini_client import resolve_gemini_model

    effective_path = resolve_media_for_analysis(
        project, folder_name, media_path, scope=scope
    )
    cached = load_cached_media_for_asset(
        project, folder_name, media_path, scope=scope
    )
    if cached is None:
        return False
    return is_current_asset_analysis(
        cached,
        effective_path,
        resolved_model_id=resolve_gemini_model(model),
    )


def list_assets_missing_successful_cache(
    project: Project,
    folder_name: str,
    *,
    model: Optional[str] = None,
) -> list[Path]:
    """Medien ohne *aktuellen* Cache — bei explizitem Analyselauf neu analysieren."""
    return [
        media_path
        for media_path in discover_folder_media_paths(project, folder_name)
        if not has_successful_asset_cache(
            project, folder_name, media_path, model=model
        )
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
    # Beide lokal oder beide fehlend: Adobe-Neudownload (Asset00012.mov)
    # schlägt die alte Kurzform (Asset12.mp4).
    if len(candidate.name) > len(current.name):
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


def list_cache_dirs_for_folder(
    project: Project,
    folder_name: str,
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> list[Path]:
    slug = safe_folder_slug(folder_name)
    cache_root = project.work_dir_path / "cache" / "inventory" / slug
    if scope == CACHE_SCOPE_SUPPLEMENT:
        return [cache_root / SUPPLEMENT_SCOPE_SUBDIR]
    return [
        cache_root,
        get_inventory_dir(project.work_dir_path) / slug,
    ]


def scan_folder_supplement_cache_assets(
    project: Project,
    folder_name: str,
) -> list[AssetMediaAnalysis]:
    """Analysierte Supplements eines Ordners aus dem Supplement-Cache.

    Quelle der Wahrheit für die Wiederherstellung: geht eine Inventarzeile
    verloren, lässt sie sich hieraus ohne erneuten LLM-Aufruf zurückholen.
    """
    indexed: dict[str, AssetMediaAnalysis] = {}
    for cache_dir in list_cache_dirs_for_folder(
        project, folder_name, scope=CACHE_SCOPE_SUPPLEMENT
    ):
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
            indexed[str(Path(cached.path))] = cached
    return list(indexed.values())


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
    """True bei verwendbarer Analyse (Legacy inkl.); nicht gleichbedeutend mit current.

    Explizite Parse-Fehler und dokumentierte Analysefehler gelten nicht als Erfolg.
    Folder-Grün / Inventar-Materialisierung nutzen diese Semantik — ohne automatisch
    kostenpflichtige Reanalyse beim bloßen Laden.
    """
    from otio_app.services.asset_analysis_signature import is_usable_asset_analysis

    return is_usable_asset_analysis(entry)


def _merge_media_path(
    by_slug: dict[str, Path],
    folder_path: Path,
    media_path: Path,
) -> None:
    slug = numbered_asset_discovery_key(media_path)
    if SUPPLEMENTAL_FOLDER_NAME in media_path.parts:
        resolved = media_path
    else:
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
        if frame_dir.name == SUPPLEMENT_SCOPE_SUBDIR:
            # Frames beschaffter Assets — keine fehlenden Ordner-Originale.
            continue
        slug = frame_dir.name.casefold()
        matched = slug_to_path.get(slug)
        if matched is not None:
            _merge_media_path(by_slug, folder_path, matched)
            continue
        candidate = resolve_media_path_for_slug(folder_path, frame_dir.name)
        _merge_media_path(by_slug, folder_path, candidate)


def _media_available_for_discovery(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    listed_names: set[str],
) -> bool:
    """True wenn die Datei im Ordner, als iCloud-Platzhalter oder als Clean-Kopie da ist."""
    if media_path.name.casefold() in listed_names:
        return True
    try:
        if media_path.is_file():
            return True
    except OSError:
        pass
    if media_has_icloud_placeholder(media_path):
        return True
    from otio_app.services.clean_media import clean_file_is_present, find_clean_file_for_media

    return clean_file_is_present(
        find_clean_file_for_media(project, folder_name, media_path)
    )


def discover_folder_media_paths(project: Project, folder_name: str) -> list[Path]:
    """Medien im Ordner: Dateisystem plus Cache und Frame-Arbeit vereinen.

    Cache-/Frame-Geister ohne Datei werden nicht mitgeschleppt — typisch nach
    Ersetzen von Wasserzeichen-Downloads (Asset12.mp4 weg, Asset00012.mov neu).
    iCloud-Platzhalter und Namen aus der Ordnerliste bleiben erhalten.
    """
    folder_path = project.project_root_path / folder_name
    listed = list(list_media_files(folder_path))
    listed_names = {path.name.casefold() for path in listed}
    by_slug: dict[str, Path] = {}

    for media_path in listed:
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
            cached_path = Path(cached.path)
            # Supplement-Caches gehören nicht in die Primary-Discovery
            # (Inventory-Matching vergleicht nur Top-Level-Medien). Sonst
            # entstünde aus dem Dateinamen ein Original, das es nie gab, und
            # der Ordner würde nie wieder grün.
            if SUPPLEMENTAL_FOLDER_NAME in cached_path.parts:
                continue
            if is_supplement_asset(cached):
                continue
            name = cached_path.name
            if not name or Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            _merge_media_path(by_slug, folder_path, folder_path / name)

    _discover_gaps_from_cache_dir(project, folder_name, folder_path, by_slug)
    _discover_media_from_frame_dirs(project, folder_name, by_slug)

    kept = [
        path
        for path in by_slug.values()
        if _media_available_for_discovery(
            project, folder_name, path, listed_names=listed_names
        )
    ]
    return sorted(kept, key=lambda path: path.name.casefold())


def media_file_is_accessible(media_path: Path) -> tuple[bool, str | None]:
    """Prüft, ob eine Mediendatei lokal lesbar ist (iCloud-Check)."""
    try:
        if not media_path.is_file():
            if media_has_icloud_placeholder(media_path):
                return False, (
                    f"Mediendatei nicht lokal verfügbar: `{media_path.name}` "
                    "(iCloud — bitte im Finder laden)"
                )
            return False, (
                f"Mediendatei fehlt: `{media_path.name}` "
                "(nicht mehr im Ordner — vermutlich ersetzt oder entfernt)"
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
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> Path:
    """Ermittelt den echten Mediendatei-Pfad (Slug-Match, ggf. Clean Media)."""
    if scope == CACHE_SCOPE_SUPPLEMENT:
        # Supplements liegen außerhalb des Medienordners; ihr Pfad ist bereits
        # der effektive (Clean-Media läuft beim Ingest, nicht hier).
        return media_path
    folder_path = project.project_root_path / folder_name
    slug = safe_folder_slug(media_path.stem)
    resolved = resolve_media_path_for_slug(folder_path, slug)
    return resolve_effective_media_path(project, folder_name, resolved)


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
