#!/usr/bin/env python3
"""Resolve-safe Relink für portables Enhanced-OTIO-Paket (Zielrechner).

Läuft standalone im entpackten Paketordner:

    python3 relink_for_resolve.py

Liest transportneutrales ``timeline.otio`` + ``media_manifest.json``,
schreibt ``timeline_resolve.otio`` mit absoluten ``file://``-URIs.
Überschreibt niemals ``timeline.otio``. Keine Medienkopien.

Nur Stdlib (+ optional ffprobe für Videovalidierung).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

SCHEMA_EXTERNAL = "ExternalReference"


class RelinkError(RuntimeError):
    """Fail-closed Relink-Fehler."""


def package_root_from_script(script_file: str | Path | None = None) -> Path:
    """Paketordner = Verzeichnis dieser Script-Datei."""
    base = Path(script_file or __file__).resolve().parent
    return base


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_http_url(value: str) -> bool:
    lower = (value or "").strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _load_manifest(package_root: Path) -> list[dict[str, Any]]:
    path = package_root / "media_manifest.json"
    if not path.is_file():
        raise RelinkError(f"media_manifest.json fehlt: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RelinkError(f"media_manifest.json ungültig: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise RelinkError("media_manifest.json ist leer oder kein Array.")
    return payload


def _index_manifest(
    entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RelinkError("Manifesteintrag ist kein Objekt.")
        name = str(entry.get("packaged_filename") or "").strip()
        if not name or name != Path(name).name or "/" in name or "\\" in name:
            raise RelinkError(
                f"Ungültiger packaged_filename im Manifest: {name!r}"
            )
        if name in by_name:
            raise RelinkError(
                f"Doppelte/mehrdeutige packaged_filename im Manifest: {name!r}"
            )
        by_name[name] = entry
    return by_name


def _media_dir(package_root: Path) -> Path:
    media = (package_root / "media").resolve()
    if not media.is_dir():
        raise RelinkError(f"media/-Ordner fehlt: {media}")
    return media


def _ensure_inside_media(path: Path, media_dir: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(media_dir)
    except ValueError as exc:
        raise RelinkError(
            f"Pfad liegt außerhalb von media/: {resolved}"
        ) from exc
    return resolved


def _packaged_name_from_target_url(target_url: str) -> str:
    raw = str(target_url or "").strip()
    if not raw:
        raise RelinkError("Leere ExternalReference.target_url.")
    if _is_http_url(raw):
        raise RelinkError(f"HTTP(S)-URL unzulässig: {raw}")
    if raw.lower().startswith("file:"):
        parsed = urlparse(raw)
        path = Path(unquote(parsed.path or ""))
        name = path.name
    else:
        # relative media/<name> oder absolut — nur Basename für Manifest-Lookup
        normalized = raw.replace("\\", "/")
        if ".." in Path(normalized).parts:
            raise RelinkError(f"Path Traversal in target_url: {raw}")
        name = Path(normalized).name
    if not name or name in {".", ".."}:
        raise RelinkError(f"Kein Dateiname in target_url: {raw}")
    return name


def _validate_video(path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type,width,height,duration",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RelinkError(
            f"ffprobe nicht gefunden — Videovalidierung unmöglich für {path.name}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RelinkError(f"ffprobe Timeout bei {path.name}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RelinkError(
            f"Video technisch ungültig ({path.name}): {detail or result.returncode}"
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RelinkError(f"ffprobe-JSON ungültig für {path.name}") from exc
    streams = payload.get("streams") or []
    if not isinstance(streams, list) or not streams:
        raise RelinkError(f"Kein Videostream: {path.name}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RelinkError(f"Ungültige Auflösung in {path.name}")
    duration = stream.get("duration")
    if duration is None:
        duration = (payload.get("format") or {}).get("duration")
    try:
        dur = float(duration or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= 0.0:
        # Manche Container ohne Stream-Dauer — Dateigröße als Fallback-Gate.
        if path.stat().st_size <= 0:
            raise RelinkError(f"Video ohne Dauer/leer: {path.name}")


def _validate_audio(path: Path) -> None:
    if path.stat().st_size <= 0:
        raise RelinkError(f"Audiodatei leer: {path.name}")
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError as exc:
        raise RelinkError(f"Audiodatei unlesbar ({path.name}): {exc}") from exc
    if not head:
        raise RelinkError(f"Audiodatei unlesbar/leer: {path.name}")


def _validate_image(path: Path) -> None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        # Ohne Pillow: Datei muss lesbar und nicht leer sein.
        if path.stat().st_size <= 0:
            raise RelinkError(f"Bilddatei leer: {path.name}")
        with path.open("rb") as handle:
            if not handle.read(16):
                raise RelinkError(f"Bilddatei unlesbar: {path.name}")
        return
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise RelinkError(f"Bild technisch ungültig ({path.name}): {exc}") from exc
    if int(width or 0) <= 0 or int(height or 0) <= 0:
        raise RelinkError(f"Bild ohne positive Auflösung: {path.name}")


def _validate_media(path: Path, media_kind: str) -> None:
    kind = (media_kind or "").strip().lower()
    suffix = path.suffix.lower()
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
    video_exts = {".mp4", ".mov", ".mkv", ".m4v", ".avi"}
    audio_exts = {".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff"}
    if kind == "audio" or suffix in audio_exts:
        _validate_audio(path)
        return
    if suffix in image_exts or kind in {"photo", "image"}:
        _validate_image(path)
        return
    if kind in {"video", "still_hold"} or suffix in video_exts:
        # still_hold oft als Hold-MP4; echte Stills oben über Extension.
        _validate_video(path)
        return
    raise RelinkError(f"Unbekannte media_kind {media_kind!r} für {path.name}")


def resolve_media_file(
    *,
    package_root: Path,
    target_url: str,
    manifest_by_name: dict[str, dict[str, Any]],
    media_dir: Path,
    clip_name: str = "",
) -> tuple[Path, dict[str, Any]]:
    """Löst eine OTIO-Referenz über das Manifest auf (fail-closed)."""
    label = clip_name or "(ohne Clipname)"
    name = _packaged_name_from_target_url(target_url)
    entry = manifest_by_name.get(name)
    if entry is None:
        raise RelinkError(
            f"Clip {label!r}: kein Manifesteintrag für Datei {name!r} "
            f"(target_url={target_url!r})."
        )
    candidate = _ensure_inside_media(media_dir / name, media_dir)
    if not candidate.is_file():
        raise RelinkError(
            f"Clip {label!r}: Mediendatei fehlt: {candidate} "
            f"(erwartet media/{name})."
        )
    expected_sha = str(entry.get("sha256") or "").strip().lower()
    if expected_sha:
        actual = _sha256_file(candidate).lower()
        if actual != expected_sha:
            raise RelinkError(
                f"Clip {label!r}: Prüfsumme weicht ab für {name} "
                f"(erwartet {expected_sha}, ist {actual})."
            )
    media_kind = str(entry.get("media_kind") or "").strip()
    _validate_media(candidate, media_kind)
    return candidate, entry


def _walk_external_references(
    node: Any,
    *,
    clip_name: str = "",
) -> list[tuple[dict[str, Any], str]]:
    """Sammelt (ExternalReference-dict, clip_name)."""
    found: list[tuple[dict[str, Any], str]] = []
    if isinstance(node, dict):
        schema = str(node.get("OTIO_SCHEMA") or "")
        name_here = str(node.get("name") or clip_name or "")
        if schema.startswith(SCHEMA_EXTERNAL):
            found.append((node, name_here))
        media_ref = node.get("media_reference")
        if isinstance(media_ref, dict):
            found.extend(
                _walk_external_references(media_ref, clip_name=name_here)
            )
        for key, value in node.items():
            if key == "media_reference":
                continue
            found.extend(_walk_external_references(value, clip_name=name_here))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_external_references(item, clip_name=clip_name))
    return found


def path_to_file_uri(path: Path) -> str:
    """Kanonische file://-URI via Path.as_uri() (korrektes Encoding)."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RelinkError(f"Datei existiert nicht für URI: {resolved}")
    return resolved.as_uri()


def relink_package(
    package_root: Path | None = None,
    *,
    script_file: str | Path | None = None,
) -> Path:
    """Erzeugt timeline_resolve.otio neben timeline.otio. Gibt Zielpfad zurück."""
    root = (
        Path(package_root).expanduser().resolve()
        if package_root is not None
        else package_root_from_script(script_file)
    )
    source = root / "timeline.otio"
    dest = root / "timeline_resolve.otio"
    if not source.is_file():
        raise RelinkError(f"timeline.otio fehlt: {source}")

    original_bytes = source.read_bytes()
    try:
        timeline = json.loads(original_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RelinkError(f"timeline.otio ist kein gültiges JSON-OTIO: {exc}") from exc

    manifest_by_name = _index_manifest(_load_manifest(root))
    media_dir = _media_dir(root)
    refs = _walk_external_references(timeline)
    if not refs:
        raise RelinkError("Keine ExternalReference in timeline.otio gefunden.")

    rewritten = 0
    for ref, clip_name in refs:
        old = str(ref.get("target_url") or "")
        media_path, _entry = resolve_media_file(
            package_root=root,
            target_url=old,
            manifest_by_name=manifest_by_name,
            media_dir=media_dir,
            clip_name=clip_name,
        )
        uri = path_to_file_uri(media_path)
        if not uri.startswith("file://"):
            raise RelinkError(f"URI-Erzeugung fehlgeschlagen: {uri}")
        if _is_http_url(uri):
            raise RelinkError(f"HTTP in Ergebnis-URI: {uri}")
        # Keine relativen media/... mehr.
        if "media/" in uri and not uri.startswith("file://"):
            raise RelinkError(f"Relative Referenz verblieben: {uri}")
        ref["target_url"] = uri
        rewritten += 1

    # Niemals timeline.otio überschreiben — Ziel ist immer timeline_resolve.otio.
    dest.write_text(
        json.dumps(timeline, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # Integrität der Transport-OTIO.
    if source.read_bytes() != original_bytes:
        raise RelinkError("timeline.otio wurde unerwartet verändert — Abbruch.")
    if rewritten <= 0:
        raise RelinkError("Keine Referenzen umgeschrieben.")
    return dest


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    package: Path | None = None
    if args:
        if args[0] in {"-h", "--help"}:
            print(
                "Usage: python3 relink_for_resolve.py [PACKAGE_DIR]\n"
                "Ohne Argument: Paketordner = Verzeichnis dieses Scripts.\n"
                "Schreibt timeline_resolve.otio (timeline.otio bleibt unverändert)."
            )
            return 0
        package = Path(args[0])
    try:
        out = relink_package(package, script_file=__file__)
    except RelinkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: Resolve-OTIO geschrieben: {out}")
    print("Importiere timeline_resolve.otio in DaVinci Resolve (nicht timeline.otio).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
