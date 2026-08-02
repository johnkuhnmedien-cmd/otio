"""Portables Enhanced-OTIO-Medienpaket mit eindeutigen Paketdateinamen.

Resolve verknüpft beim Relink oft nur über den Dateinamen. Gleichnamige
Quellen (z. B. Castle Combe/Asset00011.mov und Rocamadour/Asset00011.mov)
müssen daher im Paket physisch unterschiedliche Namen tragen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.asset_identity import (
    enhanced_asset_id_for_path,
    stable_path_hash,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    exports_dir,
)


class PortableExportError(RuntimeError):
    pass


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class PackagedMediaEntry:
    asset_id: str
    original_path: str
    packaged_filename: str
    packaged_path: str
    media_kind: str
    file_size: int
    sha256: str
    copy_mode: str

    def as_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "original_path": self.original_path,
            "packaged_filename": self.packaged_filename,
            "packaged_path": self.packaged_path,
            "media_kind": self.media_kind,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "copy_mode": self.copy_mode,
        }


def package_dir_for_export(project: Project, basename: str) -> Path:
    """``_otio_enhanced/{LANG}/exports/<basename>_package``."""
    assert_enhanced_work_root(project)
    safe = _sanitize_export_basename(basename)
    return exports_dir(project) / f"{safe}_package"


def _sanitize_export_basename(basename: str) -> str:
    text = (basename or "enhanced").strip() or "enhanced"
    text = _SAFE_NAME_RE.sub("_", text).strip("._") or "enhanced"
    return text[:120]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hardlink_or_copy(source: Path, dest: Path) -> str:
    """Hardlink wenn möglich, sonst Kopie. Keine Symlinks."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(source, dest)
        if dest.is_file() and dest.stat().st_size == source.stat().st_size:
            return "hardlink"
    except OSError:
        pass
    try:
        shutil.copy2(source, dest)
    except OSError as exc:
        raise PortableExportError(
            f"Kopieren fehlgeschlagen: {source} → {dest} ({exc})"
        ) from exc
    if not dest.is_file():
        raise PortableExportError(f"Kopie fehlt nach Schreibversuch: {dest}")
    if dest.stat().st_size != source.stat().st_size:
        raise PortableExportError(
            f"Unvollständige Kopie: {source} ({source.stat().st_size} bytes) → "
            f"{dest} ({dest.stat().st_size} bytes)"
        )
    return "copy"


def packaged_filename_for_media(
    project: Project,
    source: Path,
    *,
    asset_id: str = "",
    media_kind: str = "video",
) -> str:
    """Eindeutiger Paketdateiname — nie nur der Original-Basename."""
    src = Path(source).expanduser().resolve()
    suffix = src.suffix.lower() or ".bin"
    kind = (media_kind or "video").strip().lower()
    aid = (asset_id or "").strip()

    if kind == "video" and aid.startswith("asset__"):
        return f"{aid}{suffix}"
    if kind == "video" and aid:
        safe_id = _SAFE_NAME_RE.sub("_", aid).strip("._") or "asset"
        return f"{safe_id}{suffix}"
    if kind in {"still_hold", "hold", "image"}:
        key = stable_path_hash(str(src), length=16)
        return f"still_hold_{key}{suffix if suffix else '.mp4'}"
    if kind == "audio":
        key = stable_path_hash(str(src), length=16)
        return f"narration_{key}{suffix if suffix else '.wav'}"

    # Fallback: kanonische Asset-ID aus Pfad ableiten.
    canonical = enhanced_asset_id_for_path(project, src)
    return f"{canonical}{suffix}"


def stage_media_into_package(
    project: Project,
    package_root: Path,
    items: list[tuple[Path, str, str]],
) -> list[PackagedMediaEntry]:
    """Staged Medien in ``package_root/media/``.

    ``items``: Liste von ``(source_path, asset_id, media_kind)``.
    Identische Quellen werden nur einmal gestaged.
    """
    media_dir = package_root / "media"
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PortableExportError(
            f"Paketordner nicht beschreibbar: {media_dir} ({exc})"
        ) from exc

    # source resolve → entry
    by_source: dict[Path, PackagedMediaEntry] = {}
    filename_owners: dict[str, PackagedMediaEntry] = {}
    path_owners: dict[str, PackagedMediaEntry] = {}

    for raw_source, asset_id, media_kind in items:
        source = Path(raw_source).expanduser()
        if not source.is_file():
            raise PortableExportError(
                f"Quelldatei fehlt für Asset {asset_id!r}: {source}"
            )
        try:
            source = source.resolve()
        except OSError as exc:
            raise PortableExportError(
                f"Quelldatei nicht auflösbar für Asset {asset_id!r}: {source} ({exc})"
            ) from exc

        if source in by_source:
            existing = by_source[source]
            # Gleiche Datei mit anderer Asset-ID: nur erlauben wenn ID gleich.
            if existing.asset_id != (asset_id or existing.asset_id):
                # Same file referenced under multiple IDs — keep first, OK for holds.
                pass
            continue

        filename = packaged_filename_for_media(
            project, source, asset_id=asset_id, media_kind=media_kind
        )
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise PortableExportError(
                f"Ungültiger Paketdateiname für Asset {asset_id!r}: {filename!r}"
            )
        # Basename-Kollision: zwei unterschiedliche Assets → blockieren.
        if filename in filename_owners:
            other = filename_owners[filename]
            if other.original_path != str(source):
                raise PortableExportError(
                    "Paketdateiname kollidiert für unterschiedliche Quellen: "
                    f"'{filename}'. "
                    f"Asset A {other.asset_id!r} · {other.original_path} · "
                    f"Asset B {asset_id!r} · {source}. "
                    "Keine automatische Auswahl."
                )

        dest = (media_dir / filename).resolve()
        dest_key = str(dest)
        if dest_key in path_owners and path_owners[dest_key].original_path != str(source):
            other = path_owners[dest_key]
            raise PortableExportError(
                "Paketpfad kollidiert für unterschiedliche Quellen: "
                f"{dest}. Asset A {other.asset_id!r} · {other.original_path} · "
                f"Asset B {asset_id!r} · {source}."
            )

        copy_mode = _hardlink_or_copy(source, dest)
        file_size = int(dest.stat().st_size)
        if file_size <= 0:
            raise PortableExportError(f"Paketdatei leer oder ungültig: {dest}")
        digest = _sha256_file(dest)
        entry = PackagedMediaEntry(
            asset_id=(asset_id or "").strip() or enhanced_asset_id_for_path(project, source),
            original_path=str(source),
            packaged_filename=filename,
            packaged_path=str(dest),
            media_kind=(media_kind or "video").strip().lower(),
            file_size=file_size,
            sha256=digest,
            copy_mode=copy_mode,
        )
        by_source[source] = entry
        filename_owners[filename] = entry
        path_owners[dest_key] = entry

    return list(by_source.values())


def relative_media_target_url(packaged_filename: str) -> str:
    """Relative OTIO-Referenz vom timeline.otio-Standort aus."""
    name = Path(packaged_filename).name
    return f"media/{name}"


def assert_portable_target_urls(urls: list[str], *, package_root: Path) -> None:
    """Fail-closed: keine Host-Pfade, kein HTTP, alles unter package/media."""
    media_dir = (package_root / "media").resolve()
    forbidden_markers = (
        "/opt/cursor",
        "/workspace",
        "/workspace-worktrees",
        "http://",
        "https://",
    )
    seen: dict[str, str] = {}
    for raw in urls:
        url = str(raw or "").strip()
        if not url:
            raise PortableExportError("Leere OTIO-Medienreferenz.")
        lower = url.lower()
        for marker in forbidden_markers:
            if marker in lower or lower.startswith(marker):
                raise PortableExportError(
                    f"Portable OTIO enthält verbotene Referenz ({marker}): {url}"
                )
        # Relativ media/... oder absolut innerhalb des Pakets erlauben.
        path = Path(url)
        if path.is_absolute():
            resolved = path.resolve()
            try:
                resolved.relative_to(media_dir)
            except ValueError as exc:
                raise PortableExportError(
                    f"OTIO-Referenz liegt außerhalb des Pakets: {url}"
                ) from exc
            key = str(resolved)
        else:
            # relative to package root (timeline.otio sibling)
            resolved = (package_root / url).resolve()
            try:
                resolved.relative_to(media_dir)
            except ValueError as exc:
                raise PortableExportError(
                    f"OTIO-Referenz liegt außerhalb des Pakets: {url}"
                ) from exc
            if not resolved.is_file():
                raise PortableExportError(
                    f"OTIO-Referenz zeigt auf fehlende Paketdatei: {url} → {resolved}"
                )
            key = url.replace("\\", "/")
        if key in seen and seen[key] != url:
            pass
        seen[key] = url


def write_media_manifest(path: Path, entries: list[PackagedMediaEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [entry.as_dict() for entry in sorted(entries, key=lambda e: e.asset_id)]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_package_readme(path: Path, *, basename: str) -> None:
    path.write_text(
        f"""# {basename} — portables Enhanced-OTIO-Paket

## Import in DaVinci Resolve (macOS / Windows / Linux)

Resolve Studio löst relative `media/...`-Pfade in `timeline.otio` **nicht**
zuverlässig relativ zur OTIO-Datei auf. Deshalb:

1. ZIP **vollständig** entpacken.
2. Ordnerstruktur **nicht** verändern (`timeline.otio`, `media/`, Manifest,
   Relink-Script müssen Geschwister bleiben).
3. Terminal **im Paketordner** öffnen.
4. Relink auf dem Zielrechner ausführen:

   ```bash
   python3 relink_for_resolve.py
   ```

5. Anschließend **`timeline_resolve.otio`** in DaVinci Resolve importieren.
6. **Nicht** die transportneutrale `timeline.otio` direkt in Resolve importieren
   (führt typischerweise zu Media Offline).

Das Relink-Script schreibt nur `timeline_resolve.otio` mit absoluten
`file://`-URIs des Zielrechners. Es kopiert oder verändert keine Medien und
überschreibt niemals `timeline.otio`.

Voraussetzungen: Python 3 und `ffprobe` (FFmpeg) im PATH für die
Videovalidierung.

## Inhalt

- `timeline.otio` — transportneutrale Produktions-Timeline (`media/...`)
- `timeline_resolve.otio` — nach Relink: absolute `file://`-URIs (Zielrechner)
- `relink_for_resolve.py` — einmal auf dem Zielrechner ausführen
- `media/` — eindeutig benannte Medien (Hardlink oder Kopie)
- `media_manifest.json` — verbindliche Zuordnung + Prüfsummen
- `README.md` — diese Anleitung

Originalquelldateien wurden nicht umbenannt.
""",
        encoding="utf-8",
    )


def install_resolve_relink_script(package_root: Path) -> Path:
    """Kopiert das standalone Relink-Script in den Paketordner."""
    source = Path(__file__).resolve().parent / "relink_for_resolve.py"
    if not source.is_file():
        raise PortableExportError(f"Relink-Script fehlt im Repo: {source}")
    dest = Path(package_root) / "relink_for_resolve.py"
    shutil.copy2(source, dest)
    try:
        dest.chmod(dest.stat().st_mode | 0o111)
    except OSError:
        pass
    return dest


def lookup_packaged_path(
    entries: list[PackagedMediaEntry],
    original: Path,
) -> PackagedMediaEntry:
    key = Path(original).expanduser().resolve()
    for entry in entries:
        if Path(entry.original_path).resolve() == key:
            return entry
    raise PortableExportError(f"Kein Paketeintrag für Originalmedium: {original}")
