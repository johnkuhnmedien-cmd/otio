"""Medien-Dateien finden und technisch prüfen."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

NO_ANALYZABLE_MEDIA_DESCRIPTION = "Keine analysierbaren Medien gefunden."


def _is_media_filename(name: str) -> bool:
    return Path(name).suffix.lower() in MEDIA_EXTENSIONS


def _list_media_names_iterdir(directory: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in directory.iterdir()], None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_os_listdir(directory: Path) -> tuple[list[str], str | None]:
    try:
        return list(os.listdir(directory)), None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_glob(directory: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in directory.glob("*")], None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_subprocess(directory: Path) -> tuple[list[str], str | None]:
    """Fallback für macOS/iCloud: Dateinamen mit /bin/ls lesen."""
    try:
        result = subprocess.run(
            ["/bin/ls", "-1p", str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [], str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return [], message or f"ls exit {result.returncode}"

    names: list[str] = []
    for line in result.stdout.splitlines():
        entry = line.strip().rstrip("/")
        if entry and not entry.startswith("."):
            names.append(entry)
    return names, None


def _collect_media_filenames(directory: Path) -> tuple[list[str], bool]:
    """Sammelt Mediendateinamen mit mehreren Lese-Strategien (iCloud-tolerant)."""
    names: set[str] = set()
    used_subprocess = False

    for lister in (
        _list_media_names_iterdir,
        _list_media_names_os_listdir,
        _list_media_names_glob,
    ):
        found, _error = lister(directory)
        if found:
            names.update(found)
            break

    if not names:
        ls_names, _ls_error = _list_media_names_subprocess(directory)
        if ls_names:
            names.update(ls_names)
            used_subprocess = True

    media_names = sorted(
        (name for name in names if _is_media_filename(name)),
        key=str.casefold,
    )
    return media_names, used_subprocess


def list_media_files(directory: Path) -> list[Path]:
    """Listet Mediendateien in einem Ordner (nur lesen, iCloud-Fallbacks)."""
    if not directory.is_dir():
        return []

    media_names, used_subprocess = _collect_media_filenames(directory)
    files: list[Path] = []
    for name in media_names:
        child = directory / name
        try:
            if child.is_file():
                files.append(child)
                continue
        except OSError:
            pass
        if used_subprocess:
            files.append(child)
    return files


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration_seconds(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        duration = payload.get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
